"""Async MCP client to the cortex-grounding server (Streamable HTTP).

Presents the tutor's OWN service identity (``MCP_SERVICE_TOKEN``) — never the learner's JWT (MCP
prohibits token passthrough; confused-deputy fix) — and pins ``MCP-Protocol-Version``. Every call
degrades gracefully: on any transport / timeout / protocol / tool error it logs and returns ``None``
so the turn proceeds with reduced (id-only) grounding instead of failing.

A fresh session is opened per call — cheap against the *stateless* grounding server, and it keeps the
client trivially safe under the tutor's 2+ concurrent replicas. (Pooling is a later optimisation.)
"""

from __future__ import annotations

import asyncio
import json

import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = structlog.get_logger()

MCP_PROTOCOL_VERSION = "2025-03-26"


class GroundingClient:
    def __init__(self, url: str, service_token: str | None = None, *, timeout: float = 8.0) -> None:
        self._url = url
        self._timeout = timeout
        self._headers: dict[str, str] = {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
        if service_token:
            self._headers["Authorization"] = f"Bearer {service_token}"

    async def _call(self, tool: str, arguments: dict) -> dict | None:
        try:
            return await asyncio.wait_for(self._invoke(tool, arguments), timeout=self._timeout)
        except Exception as exc:  # transport / timeout / protocol → degrade, never fail the turn
            log.warning("grounding.unavailable", tool=tool, error=str(exc))
            return None

    async def _invoke(self, tool: str, arguments: dict) -> dict | None:
        async with streamablehttp_client(self._url, headers=self._headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
                data = result.structuredContent
                if data is None and result.content:
                    text = getattr(result.content[0], "text", None)
                    data = json.loads(text) if text else None
                if not isinstance(data, dict):
                    return None
                if data.get("error"):
                    log.info("grounding.tool_error", tool=tool, detail=data.get("error"))
                    return None
                return data

    async def get_lesson(self, problem_id: str, *, include_solution: bool) -> dict | None:
        """Statement + examples (``content``); plus the worked ``solution`` only when requested."""
        return await self._call(
            "get_lesson", {"problem_id": problem_id, "include_solution": include_solution}
        )

    async def get_problem(self, problem_id: str) -> dict | None:
        """Statement + examples only — the solution is never returned by this tool."""
        return await self._call("get_problem", {"problem_id": problem_id})

    async def search(self, query: str, *, book: str | None = None, limit: int | None = None) -> dict | None:
        args: dict = {"query": query}
        if book:
            args["book"] = book
        if limit:
            args["limit"] = limit
        return await self._call("search_corpus", args)
