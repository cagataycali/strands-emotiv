"""Expose the brain tools over MCP so any client (Claude Desktop, Cursor,
Kiro, another Strands agent) can feel the person wearing the headset.

    strands-emotiv mcp            # stdio, for desktop clients
    strands-emotiv mcp --http 8000  # streamable HTTP at /mcp, for agents
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Any

from . import tools as bt

log = logging.getLogger("strands_emotiv.mcp")


def _stream_forever(fake: bool, ready: threading.Event) -> None:
    """Own event loop on a worker thread: the MCP stdio server takes the main one."""

    async def run() -> None:
        source = None
        if fake:
            from .fake import FakeCortex

            source = FakeCortex(realtime=True)
        name = await bt.start_stream(source)
        log.info("stream source: %s", name)
        ready.set()
        while True:
            await asyncio.sleep(3600)

    asyncio.run(run())


def serve(fake: bool = False, http: int | None = None, expose_agent: bool = True,
          model: str | None = None) -> int:
    """Start the stream, build the agent, block on the MCP server."""
    # stdio is the protocol channel; everything we say goes to stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(name)s %(levelname)s %(message)s")
    from strands_mcp_server import mcp_server

    from .agent import build_agent

    ready = threading.Event()
    threading.Thread(target=_stream_forever, args=(fake, ready), daemon=True).start()
    ready.wait(timeout=20)

    # the MCP server is itself a running tool call; nested tool calls need this off
    agent = build_agent(model=model, extra_tools=(mcp_server,), record_direct_tool_call=False)
    kwargs: dict[str, Any] = {
        "action": "start",
        "tools": [t.tool_name for t in bt.BRAIN_TOOLS],
        "expose_agent": expose_agent,
        "agent": agent,
    }
    if http:
        agent.tool.mcp_server(transport="http", port=http, **kwargs)
        log.info("MCP at http://127.0.0.1:%d/mcp (ctrl-c to stop)", http)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    else:
        agent.tool.mcp_server(transport="stdio", **kwargs)
    return 0
