"""Build the tool list the voice model is allowed to call."""

from __future__ import annotations

import json
import logging

from jarvis.config import AppConfig
from jarvis.tools.apple import AddReminderTool, CalendarTodayTool
from jarvis.tools.base import Tool
from jarvis.tools.datetime_tool import DateTimeTool
from jarvis.tools.mcp_client import MCPTool, StdioMCPClient
from jarvis.tools.web_search import FetchUrlTool, WebSearchTool

log = logging.getLogger(__name__)

_FACTORIES = {
    "get_current_datetime": lambda config: DateTimeTool(config.home_timezone),
    "web_search": lambda config: WebSearchTool(),
    "fetch_url": lambda config: FetchUrlTool(),
    "calendar_today": lambda config: CalendarTodayTool(),
    "add_reminder": lambda config: AddReminderTool(),
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._closers: list = []

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def call(self, name: str, arguments: dict | str | None) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool {name}"})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return json.dumps({"error": f"invalid arguments JSON: {exc}"})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return json.dumps({"error": "tool arguments must be an object"})
        try:
            result = tool.run(arguments)
        except Exception as exc:
            log.exception("tool %s failed", name)
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        if not isinstance(result, str):
            result = json.dumps(result)
        return result

    def close(self) -> None:
        for closer in self._closers:
            try:
                closer()
            except Exception:
                log.exception("tool cleanup failed")
        self._closers.clear()


def build_registry(config: AppConfig) -> ToolRegistry:
    registry = ToolRegistry()
    for name in config.tools_enabled:
        factory = _FACTORIES.get(name)
        if factory is None:
            log.warning("unknown tool %r in tools.enabled; skipping", name)
            continue
        registry.add(factory(config))
    _attach_mcp(registry, config)
    return registry


def _attach_mcp(registry: ToolRegistry, config: AppConfig) -> None:
    if not config.mcp.enabled:
        return
    if not config.mcp.command:
        log.warning("mcp.enabled is true but mcp.command is empty; skipping MCP")
        return
    client = StdioMCPClient(config.mcp.command, config.mcp.env)
    try:
        client.start()
        tools = client.list_tools()
    except Exception:
        log.exception("MCP server failed to start; continuing without those tools")
        client.close()
        return
    prefix = config.mcp.prefix
    for spec in tools:
        remote = str(spec.get("name") or "").strip()
        if not remote:
            continue
        public = f"{prefix}{remote}" if prefix else remote
        if public in registry.names():
            public = f"mcp_{remote}"
        registry.add(
            MCPTool(
                client,
                remote,
                str(spec.get("description") or ""),
                spec.get("inputSchema") or {"type": "object", "properties": {}},
                public,
            )
        )
        log.info("MCP tool available: %s", public)
    registry._closers.append(client.close)
