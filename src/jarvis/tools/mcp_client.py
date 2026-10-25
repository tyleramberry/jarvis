"""Minimal stdio MCP client.

This speaks the newline-delimited JSON-RPC used by MCP servers launched as
a local command (for example an agent such as Hermes, if it exposes MCP
over stdio). Remote HTTP MCP servers are not opened here; a voice provider
that supports server-side MCP can be given those URLs in its own session
config later. This client is off unless ``mcp.enabled`` is true.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from queue import Empty, Queue

from jarvis.tools.base import Tool

log = logging.getLogger(__name__)


class MCPError(RuntimeError):
    pass


class StdioMCPClient:
    def __init__(self, command: list[str], env: dict[str, str] | None = None, timeout: float = 20):
        self.command = command
        self.env = env or {}
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._lock = threading.Lock()
        self._messages: Queue = Queue()
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        if not self.command:
            raise MCPError("mcp.command is empty")
        log.info("starting MCP server: %s", " ".join(self.command))
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **self.env},
        )
        self._reader = threading.Thread(target=self._read_stdout, name="mcp-stdio", daemon=True)
        self._reader.start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, name="mcp-stderr", daemon=True)
        self._stderr_thread.start()
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "jarvis", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict]:
        result = self.request("tools/list", {})
        tools = result.get("tools") or []
        if not isinstance(tools, list):
            raise MCPError("tools/list did not return a list")
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return format_tool_result(result)

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    def request(self, method: str, params: dict) -> dict:
        with self._lock:
            self._id += 1
            message_id = self._id
            self._write({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
            deadline_loops = int(self.timeout * 10)
            for _ in range(max(1, deadline_loops)):
                try:
                    message = self._messages.get(timeout=0.1)
                except Empty:
                    if self._proc is not None and self._proc.poll() is not None:
                        raise MCPError(f"MCP server exited while waiting for {method}")
                    continue
                if message is None:
                    raise MCPError("MCP server closed stdout")
                if message.get("id") != message_id:
                    continue
                if "error" in message:
                    raise MCPError(json.dumps(message["error"]))
                result = message.get("result") or {}
                if not isinstance(result, dict):
                    return {"value": result}
                return result
        raise MCPError(f"timed out waiting for MCP {method}")

    def notify(self, method: str, params: dict) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPError("MCP server is not running")
        data = json.dumps(payload).encode("utf-8") + b"\n"
        proc.stdin.write(data)
        proc.stdin.flush()

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._messages.put(None)
            return
        buffer = bytearray()
        while True:
            # read1 returns as soon as the pipe has any bytes. read(n) waits
            # until n bytes or EOF, which stalls a short JSON line forever
            # while the server keeps stdout open.
            chunk = proc.stdout.read1(4096)
            if not chunk:
                self._messages.put(None)
                return
            buffer.extend(chunk)
            while True:
                try:
                    message, buffer = extract_message(buffer)
                except Exception:
                    log.exception("mcp stdout was not valid JSON-RPC")
                    self._messages.put(None)
                    return
                if message is None:
                    break
                self._messages.put(message)

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            chunk = proc.stderr.read1(4096)
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace").strip()
            if text:
                log.info("mcp: %s", text)


class MCPTool(Tool):
    def __init__(self, client: StdioMCPClient, remote_name: str, description: str, parameters: dict, public_name: str):
        self._client = client
        self._remote_name = remote_name
        self.name = public_name
        self.description = description or f"MCP tool {remote_name}"
        self.parameters = parameters or {"type": "object", "properties": {}}

    def run(self, arguments: dict) -> str:
        return self._client.call_tool(self._remote_name, arguments)


def format_tool_result(result: dict) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        text = json.dumps(result)[:4000]
    if result.get("isError"):
        return json.dumps({"error": text})
    if len(text) > 8000:
        text = text[:8000] + "…"
    return text


def extract_message(buffer: bytearray) -> tuple[dict | None, bytearray]:
    """Pull one JSON-RPC message from a stdio buffer.

    Supports newline-delimited JSON and the older Content-Length framing.
    """
    if not buffer:
        return None, buffer
    if buffer[:1] in b" \r\n\t":
        buffer = bytearray(buffer.lstrip())
        if not buffer:
            return None, buffer
    if buffer.startswith(b"Content-Length:"):
        separator = buffer.find(b"\r\n\r\n")
        if separator == -1:
            return None, buffer
        header = buffer[:separator].decode("ascii", errors="replace")
        length = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        if length is None:
            raise MCPError(f"MCP header had no Content-Length: {header!r}")
        start = separator + 4
        if len(buffer) < start + length:
            return None, buffer
        body = bytes(buffer[start : start + length])
        return json.loads(body.decode("utf-8")), buffer[start + length :]
    newline = buffer.find(b"\n")
    if newline == -1:
        return None, buffer
    line = bytes(buffer[:newline]).strip()
    rest = buffer[newline + 1 :]
    if not line:
        return extract_message(rest)
    return json.loads(line.decode("utf-8")), rest
