"""Tests for the native MCP stdio server (mcp_server.py).

The server is a thin HTTP front end over the API, so we exercise its tool
functions end-to-end against the real FastAPI app via an httpx ASGI transport —
no network, no running server, no `mcp` SDK required (the tool functions are
plain; only build_server() imports the SDK, which is skipped if unavailable).
The temp DB + env are built by conftest.py before main is imported.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

import main
import mcp_server


def _bind(monkeypatch, api_key: str | None = None) -> None:
    """Point mcp_server at the in-process app, optionally with an API key.

    TestClient is itself a sync httpx.Client, so it drops straight into
    mcp_server's `_session` slot and drives the real ASGI app synchronously."""
    headers = {"X-API-Key": api_key} if api_key else None
    client = TestClient(main.app, headers=headers)
    monkeypatch.setattr(mcp_server, "_session", client)
    monkeypatch.setattr(mcp_server, "API_KEY", api_key)


def test_list_tables(monkeypatch):
    _bind(monkeypatch)
    out = mcp_server.list_tables()
    names = {t["table"] for t in out["tables"]}
    assert "t4_notices" in names
    t4 = next(t for t in out["tables"] if t["table"] == "t4_notices")
    assert "notices" in t4["measures"]
    assert "service_name" in t4["dimensions"]
    assert "SUM" in out["aggregates"]


def test_describe_table_public(monkeypatch):
    _bind(monkeypatch)  # no API key → public discovery path
    out = mcp_server.describe_table("t4_notices")
    assert out["table"] == "t4_notices"
    assert "notices" in out["measures"]


def test_describe_table_keyed(monkeypatch):
    _bind(monkeypatch, api_key="momo")  # keyed → full field registry
    out = mcp_server.describe_table("t4_notices")
    assert out["table"] == "t4_notices"
    assert "notices" in out["measures"]["fields"]
    assert "example" in out


def test_describe_table_unknown(monkeypatch):
    _bind(monkeypatch)
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.describe_table("t99_nope")
    assert "Unknown table" in str(exc.value)


def test_dataset_overview(monkeypatch):
    _bind(monkeypatch)
    out = mcp_server.dataset_overview()
    assert out["services"] == 2
    assert out["platforms"] == 2
    assert any(p["platform"] == "Google" for p in out["top_platforms"])


def test_run_query(monkeypatch):
    _bind(monkeypatch)
    out = mcp_server.run_query(
        {
            "table": "t4_notices",
            "group_by": ["service_name"],
            "aggregates": [
                {"function": "SUM", "field_name": "notices", "alias": "total"}
            ],
            "sort": [{"field_name": "total", "order": "desc"}],
            "max_count": 10,
        }
    )
    assert out["columns"] == ["service_name", "total"]
    # YouTube has 100 + 40 = 140 notices in the fixture; Facebook has 50.
    rows = {r[0]: r[1] for r in out["rows"]}
    assert rows["YouTube"] == 140
    assert rows["Facebook"] == 50
    assert out["row_count"] == 2


def test_run_query_invalid_field_raises(monkeypatch):
    _bind(monkeypatch)
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.run_query(
            {
                "table": "t4_notices",
                "query": {
                    "and": [
                        {"operation": "EQ", "field_name": "secrets", "field_values": ["x"]}
                    ]
                },
            }
        )
    assert "failed (400" in str(exc.value)


def test_ask_without_key_raises(monkeypatch):
    _bind(monkeypatch)  # no API key
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.ask("how many notices did Meta receive?")
    assert "TRANSPARENCY_API_KEY" in str(exc.value)


def test_unreachable_api_raises_friendly_error(monkeypatch):
    # A client pointed at a dead socket surfaces a helpful ApiError, not a raw
    # httpx exception, so MCP hosts show something actionable.
    def _boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(base_url="http://127.0.0.1:9")
    monkeypatch.setattr(client, "request", _boom)
    monkeypatch.setattr(mcp_server, "_session", client)
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.list_tables()
    assert "Could not reach the transparency API" in str(exc.value)


def test_non_json_2xx_raises_friendly_error(monkeypatch):
    # A 2xx with a non-JSON body (e.g. a proxy's HTML error page) surfaces a
    # clean ApiError rather than a raw JSONDecodeError traceback.
    def _html(*a, **k):
        return httpx.Response(200, text="<html>not json</html>")

    client = httpx.Client(base_url="http://test")
    monkeypatch.setattr(client, "request", _html)
    monkeypatch.setattr(mcp_server, "_session", client)
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.list_tables()
    assert "returned invalid JSON" in str(exc.value)


def test_register(monkeypatch):
    _bind(monkeypatch)
    out = mcp_server.register("Test User", "testuser@example.com")
    assert "api_key" in out
    assert out["api_key"].startswith("rk_")


def test_submit_query_without_key_raises(monkeypatch):
    _bind(monkeypatch)
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.submit_query({"table": "t4_notices", "max_count": 5})
    assert "TRANSPARENCY_API_KEY" in str(exc.value)


def test_submit_query_and_poll_job(monkeypatch):
    _bind(monkeypatch, api_key="momo")
    job = mcp_server.submit_query(
        {
            "table": "t4_notices",
            "group_by": ["service_name"],
            "aggregates": [
                {"function": "SUM", "field_name": "notices", "alias": "total"}
            ],
            "sort": [{"field_name": "total", "order": "desc"}],
            "max_count": 10,
        }
    )
    assert "job_id" in job
    result = mcp_server.poll_job(job["job_id"])
    assert result["columns"] == ["service_name", "total"]
    rows = {r[0]: r[1] for r in result["rows"]}
    assert rows["YouTube"] == 140
    assert rows["Facebook"] == 50


def test_poll_job_without_key_raises(monkeypatch):
    _bind(monkeypatch)
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.poll_job("some-job-id")
    assert "TRANSPARENCY_API_KEY" in str(exc.value)


def test_poll_job_missing_job_raises(monkeypatch):
    _bind(monkeypatch, api_key="momo")
    with pytest.raises(mcp_server.ApiError) as exc:
        mcp_server.poll_job("nonexistent-job-id-xyz")
    assert "failed (404" in str(exc.value)


def test_build_server_registers_all_tools():
    pytest.importorskip("mcp")
    import asyncio

    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_tables", "describe_table", "dataset_overview",
        "run_query", "ask", "register", "submit_query", "poll_job",
    }
    assert all(t.description for t in tools)
