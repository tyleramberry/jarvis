import asyncio
import sys

import numpy as np

from jarvis.demo import run_demo_async
from jarvis.providers.mock import LiveMockProvider
from jarvis.tools.mcp_client import StdioMCPClient


def test_demo_half_duplex_and_barge_in():
    report = asyncio.run(run_demo_async())
    assert report["ok"], report["failures"]
    assert report["half"]["chunks_dropped"] > 0
    assert report["full"]["cleared"] >= 1
    assert report["half"]["tool_results"], "clock tool result was not returned"


def test_live_mock_calls_the_clock_after_speech():
    async def exercise():
        provider = LiveMockProvider()
        await provider.connect()
        await provider.configure(sample_rate=16000)
        loud = (np.ones(8000, dtype=np.int16) * 4000).tobytes()
        quiet = np.zeros(16000, dtype=np.int16).tobytes()
        await provider.send_audio(loud)
        await provider.send_audio(quiet)
        kinds = []
        async for event in provider.events():
            kinds.append(event.kind)
            if event.kind == "response_done":
                break
        await provider.close()
        return kinds

    kinds = asyncio.run(exercise())
    assert "function_call" in kinds


def test_stdio_mcp_round_trip():
    script = r"""
import json, sys
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        body = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}}
        sys.stdout.buffer.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": body}) + "\n").encode())
        sys.stdout.buffer.flush()
    elif method == "tools/list":
        tool = {"name": "ping", "description": "Ping", "inputSchema": {"type": "object", "properties": {}}}
        sys.stdout.buffer.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [tool]}}) + "\n").encode())
        sys.stdout.buffer.flush()
    elif method == "tools/call":
        sys.stdout.buffer.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "pong"}], "isError": False}}) + "\n").encode())
        sys.stdout.buffer.flush()
"""
    client = StdioMCPClient([sys.executable, "-c", script], timeout=5)
    try:
        client.start()
        tools = client.list_tools()
        assert tools[0]["name"] == "ping"
        assert client.call_tool("ping", {}) == "pong"
    finally:
        client.close()
