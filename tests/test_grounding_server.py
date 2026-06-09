"""FastMCP server wiring: the service-token gate + that all 5 tools register (schemas generate)."""

from __future__ import annotations

from grounding_mcp import server
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _inner() -> Starlette:
    return Starlette(routes=[Route("/mcp", lambda _r: PlainTextResponse("ok"))])


def test_token_required_when_configured():
    client = TestClient(server._ServiceTokenMiddleware(_inner(), token="secret"))
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_healthz_always_open():
    client = TestClient(server._ServiceTokenMiddleware(_inner(), token="secret"))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_dev_open_without_token():
    client = TestClient(server._ServiceTokenMiddleware(_inner(), token=None))
    assert client.get("/mcp").status_code == 200


async def test_all_tools_registered():
    names = {t.name for t in await server.mcp.list_tools()}
    assert {
        "search_corpus",
        "get_problem",
        "get_lesson",
        "list_related",
        "get_corpus_outline",
    } <= names
