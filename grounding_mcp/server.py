"""FastMCP server exposing the 5 grounding tools over Streamable HTTP (stateless).

Run: ``uv run python -m grounding_mcp.server`` (binds ``GROUNDING_HOST:GROUNDING_PORT``). The tutor
connects as an MCP client using its OWN service identity (``MCP_SERVICE_TOKEN``) — never the learner's
JWT (confused-deputy fix; MCP prohibits token passthrough). When ``MCP_SERVICE_TOKEN`` is unset the
server is open (local dev). The corpus is built lazily on the first tool call.

Tool docstrings ARE the descriptions the model reads — they encode the anti-spoiler contract
(``get_problem`` is always solution-free; ``get_lesson`` reveals the solution only with
``include_solution=true`` at the implement/test steps).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from mcp.server.fastmcp import FastMCP

from grounding_mcp.config import get_settings
from grounding_mcp.tools import Grounding

mcp = FastMCP("cortex-grounding", stateless_http=True)

_grounding: Grounding | None = None


def _g() -> Grounding:
    global _grounding
    if _grounding is None:
        _grounding = Grounding()
    return _grounding


@mcp.tool()
def search_corpus(query: str, book: str | None = None, limit: int | None = None) -> dict:
    """Lexically search the cortex corpus (BM25). Returns ranked chapters, each with a snippet and a
    citationUrl. Use this to FIND relevant lessons/problems by keywords; prefer get_problem/get_lesson
    when you already know the problemId."""
    return _g().search_corpus(query, book=book, limit=limit)


@mcp.tool()
def get_problem(problem_id: str) -> dict:
    """Return a problem's statement + examples to ground the coach. problemId is
    '<book>/<hierarchical-slug>' (e.g. 'data-structures-and-algorithms/.../problems/two-sum'). The
    SOLUTION IS ALWAYS WITHHELD here — use this for the clarify→plan steps."""
    return _g().get_problem(problem_id)


@mcp.tool()
def get_lesson(problem_id: str, include_solution: bool = False) -> dict:
    """Return a lesson/chapter's content. Set include_solution=true ONLY at the implement/test steps —
    it returns the worked solution + complexity in a separate `solution` field. Leave it false
    (default) for earlier steps so the coach cannot spoil the answer."""
    return _g().get_lesson(problem_id, include_solution=include_solution)


@mcp.tool()
def list_related(problem_id: str, limit: int | None = None) -> dict:
    """List chapters related to a problem (BM25 neighbours) — useful for prerequisite/transfer hints."""
    return _g().list_related(problem_id, limit=limit)


@mcp.tool()
def get_corpus_outline(book: str | None = None) -> dict:
    """Return the book→chapters outline (optionally a single book) — the map of available content."""
    return _g().get_corpus_outline(book=book)


Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class _ServiceTokenMiddleware:
    """ASGI gate: require ``Authorization: Bearer <token>`` when a token is configured; ``/healthz`` is
    always open (liveness). No token configured → open (local dev)."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if scope.get("path") == "/healthz":
                await _json(send, 200, {"status": "ok"})
                return
            if self.token:
                headers = dict(scope.get("headers") or [])
                if headers.get(b"authorization", b"").decode() != f"Bearer {self.token}":
                    await _json(send, 401, {"error": "unauthorized"})
                    return
        await self.app(scope, receive, send)


async def _json(send: Send, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": data})


def build_app() -> _ServiceTokenMiddleware:
    """The wrapped ASGI app: the service-token gate in front of the MCP Streamable-HTTP app."""
    return _ServiceTokenMiddleware(mcp.streamable_http_app(), get_settings().mcp_service_token)


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(build_app(), host=s.grounding_host, port=s.grounding_port)


if __name__ == "__main__":
    main()
