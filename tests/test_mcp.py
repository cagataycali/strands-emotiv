"""The MCP surface: a real stdio client lists the brain tools and calls one."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]



def test_stdio_lists_and_calls_brain_line():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "strands_emotiv.cli", "mcp", "--fake", "--no-agent"],
            cwd=str(ROOT),
        )
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            await asyncio.sleep(2.0)
            line = (await s.call_tool("brain_line", {})).content[0].text
            return names, line

    names, line = asyncio.run(go())
    assert {"brain_line", "brain_status", "mental_approval", "record_start"} <= names
    assert line.startswith("[brain:") and "CQ" in line
