"""Smoke tests for the structured-query API.

conftest.py sets up a temp SQLite DB and env vars before main is imported.
No Redis required — the in-memory store is used automatically.
"""
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
MOMO = {"X-API-Key": "momo"}
HONG = {"X-API-Key": "honggildong"}

# A trivial valid query: count rows in t4_notices (3 in the conftest fixture).
COUNT_ALL = {"table": "t4_notices", "aggregates": [{"function": "COUNT", "alias": "n"}]}


def _wait_for_job(job_id: str, headers: dict, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        time.sleep(0.05)
    pytest.fail(f"Job {job_id} did not reach a terminal state within {timeout}s")


def _submit_and_wait(query: dict, headers: dict = MOMO) -> dict:
    r = client.post("/api/query", json=query, headers=headers)
    assert r.status_code == 202
    return _wait_for_job(r.json()["job_id"], headers)


# ── Infrastructure ────────────────────────────────────────────────────────────

class TestInfra:
    def test_api_index_no_auth(self):
        r = client.get("/api")
        assert r.status_code == 200
        assert "endpoints" in r.json()

    def test_dashboard_page_served(self):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_healthz(self):
        assert client.get("/healthz").status_code == 200

    def test_readyz(self):
        assert client.get("/readyz").status_code == 200


# ── Researcher portal ─────────────────────────────────────────────────────────

class TestPortal:
    def test_api_key_page_served(self):
        r = client.get("/api-key")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Get an API key" in r.text
        # The unverified demo path is labelled as such, not as real registration.
        assert "demo key" in r.text
        # A self-revoke control + a copy-pasteable curl example are present.
        assert 'id="revoke"' in r.text and 'id="curl-example"' in r.text

    def test_api_key_page_localized_no_english_leak(self):
        for loc, needle in [("es", "clave de demostración"), ("zh", "演示密钥")]:
            r = client.get(f"/{loc}/api-key")
            assert r.status_code == 200
            assert "or get a demo key" not in r.text
            assert "Revoke this key" not in r.text
            assert needle in r.text

    def test_schema_page_served(self):
        r = client.get("/schema")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Dataset schema" in r.text
        # A path from the reference page to actually running a query.
        assert "Open the query builder" in r.text and 'href="/reports"' in r.text
        # The page renders the per-table example query the API returns.
        assert "Example query" in r.text

    def test_schema_localized_cite_as_not_in_english(self):
        for loc, needle in [("es", "vía la Transparency Report API"),
                            ("zh", "经由 Transparency Report API")]:
            r = client.get(f"/{loc}/schema")
            assert r.status_code == 200
            assert ", via the Transparency Report API (${meta.generated} snapshot)" not in r.text
            assert needle in r.text

    def test_old_portal_url_redirects(self):
        r = client.get("/portal", follow_redirects=False)
        assert r.status_code == 308
        assert r.headers["location"] == "/api-key"
        r = client.get("/es/portal", follow_redirects=False)
        assert r.status_code == 308
        assert r.headers["location"] == "/es/api-key"

    def test_register_issues_working_key(self):
        r = client.post("/api/portal/register", json={"name": "Ada Lovelace", "email": "ada@rs.org"})
        assert r.status_code == 201
        body = r.json()
        key = body["api_key"]
        assert key.startswith("rk_")
        assert body["name"] == "Ada Lovelace"
        assert "expires_at" in body
        # The issued key authenticates real API calls.
        assert client.get("/api/fields", headers={"X-API-Key": key}).status_code == 200
        assert client.get("/api/tables", headers={"X-API-Key": key}).status_code == 200

    def test_issued_key_can_be_revoked(self):
        key = client.post(
            "/api/portal/register", json={"name": "Grace", "email": "grace@navy.mil"}
        ).json()["api_key"]
        hdr = {"X-API-Key": key}
        assert client.get("/api/jobs", headers=hdr).status_code == 200
        assert client.delete("/api/portal/key", headers=hdr).json()["revoked"] is True
        # Revoked key no longer authenticates.
        assert client.get("/api/jobs", headers=hdr).status_code == 401

    def test_configured_key_cannot_be_revoked(self):
        assert client.delete("/api/portal/key", headers=MOMO).status_code == 400

    def test_register_bad_email_is_400(self):
        r = client.post("/api/portal/register", json={"name": "Ada", "email": "not-an-email"})
        assert r.status_code == 400

    def test_register_whitespace_name_is_400(self):
        r = client.post("/api/portal/register", json={"name": "   ", "email": "ada@rs.org"})
        assert r.status_code == 400

    def test_register_missing_field_is_422(self):
        assert client.post("/api/portal/register", json={"name": "Ada"}).status_code == 422

    def test_unknown_issued_key_rejected(self):
        assert client.get("/api/jobs", headers={"X-API-Key": "rk_deadbeef"}).status_code == 401

    def test_register_rate_limited(self):
        # Use an isolated store + a low limit so we exercise the 429 path without
        # polluting the shared TestClient IP bucket the other tests rely on.
        import main

        original_store, original_limit = main._key_store, main.REGISTER_MAX_PER_WINDOW
        main._key_store = main.MemoryKeyStore()
        main.REGISTER_MAX_PER_WINDOW = 3
        try:
            statuses = [
                client.post("/api/portal/register", json={"name": f"R{i}", "email": f"r{i}@x.org"}).status_code
                for i in range(5)
            ]
            assert statuses == [201, 201, 201, 429, 429]
        finally:
            main._key_store, main.REGISTER_MAX_PER_WINDOW = original_store, original_limit


class TestKeyStore:
    def test_expiry_and_delete(self):
        from main import MemoryKeyStore

        s = MemoryKeyStore()
        s.put("k1", {"name": "a"}, ttl=100)
        assert s.get("k1") == {"name": "a"}
        s.put("k2", {"name": "b"}, ttl=-1)  # already expired
        assert s.get("k2") is None
        assert s.delete("k1") is True
        assert s.get("k1") is None
        assert s.delete("missing") is False

    def test_incr_counts_within_window(self):
        from main import MemoryKeyStore

        s = MemoryKeyStore()
        assert [s.incr("b", 60) for _ in range(3)] == [1, 2, 3]
        assert s.incr("other", 60) == 1  # buckets are independent

    def test_incr_prunes_stale_buckets(self):
        from main import MemoryKeyStore

        s = MemoryKeyStore()
        s._hits["old"] = [0.0]  # a bucket whose only hit is far outside any window
        s._last_sweep = 0.0
        s.incr("new", 60)  # triggers the lazy sweep
        assert "old" not in s._hits and "new" in s._hits

    def test_client_ip_respects_trust_flag(self):
        import main

        class Req:
            def __init__(self, xff, host):
                self.headers = {"x-forwarded-for": xff} if xff else {}
                self.client = type("C", (), {"host": host})()

        original = main.TRUST_PROXY_HEADERS
        try:
            main.TRUST_PROXY_HEADERS = False  # XFF ignored (spoofable) by default
            assert main._client_ip(Req("1.2.3.4", "10.0.0.1")) == "10.0.0.1"
            main.TRUST_PROXY_HEADERS = True  # trust the proxy's first hop
            assert main._client_ip(Req("1.2.3.4, 5.6.7.8", "10.0.0.1")) == "1.2.3.4"
            assert main._client_ip(Req(None, "10.0.0.1")) == "10.0.0.1"
        finally:
            main.TRUST_PROXY_HEADERS = original


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_no_key_is_401(self):
        assert client.get("/api/jobs").status_code == 401

    def test_bad_key_is_401(self):
        assert client.get("/api/jobs", headers={"X-API-Key": "bogus"}).status_code == 401

    def test_valid_key_ok(self):
        # Use a gated endpoint so this actually exercises key validation
        # (schema endpoints are public now).
        r = client.get("/api/jobs", headers=MOMO)
        assert r.status_code == 200
        assert "jobs" in r.json()


# ── Schema ────────────────────────────────────────────────────────────────────

class TestSchema:
    def test_tables_lists_report_tables(self):
        r = client.get("/api/tables", headers=MOMO)
        assert r.status_code == 200
        body = r.json()
        names = [t["name"] for t in body["tables"]]
        assert "t4_notices" in names and "t11_qualitative" in names
        assert body["period"] == "2025-07-01/2025-12-31"

    def test_known_table_schema(self):
        r = client.get("/api/schema/t4_notices", headers=MOMO)
        assert r.status_code == 200
        body = r.json()
        assert "service_name" in body["dimensions"]["fields"]
        assert "notices" in body["measures"]["fields"]
        # The schema page surfaces these — a runnable example + operation vocab.
        assert body["example"]["table"] == "t4_notices"
        assert body["measures"]["operations"] == ["EQ", "IN", "GT", "GTE", "LT", "LTE"]
        # `items` help is DSA-accurate, not the old vague "Item count."
        assert "Tables 3 & 4" in body["field_help"]["items"]

    def test_missing_table_is_404(self):
        assert client.get("/api/schema/nonexistent", headers=MOMO).status_code == 404


# ── Category label normalization ──────────────────────────────────────────────

class TestCategoryLabels:
    def test_explicit_label_is_used(self):
        import seed
        assert seed._category_label("KEYWORD_DEFAMATION", {"KEYWORD_DEFAMATION": "Defamation"}) == "Defamation"

    def test_unlabelled_code_is_normalized(self):
        import seed
        # No explicit label → readable text, not the raw SCREAMING_SNAKE_CASE code.
        out = seed._category_label(
            "KEYWORD_OTHER_INTELLECTUAL_PROPERTY_INFRINGEMENTS_THIRD_PARTY_VIOLATION_OR_DATA_VIOLATION", {})
        assert out == "Other intellectual property infringements third party violation or data violation"
        assert "_" not in out

    def test_no_category_label_is_a_raw_code(self):
        # Every category served by the API has a human label, never a bare code.
        import re
        r = client.get("/api/overview")
        for row in r.json()["by_category"]:
            assert not re.fullmatch(r"[A-Z0-9_]+", row["category"]), row["category"]


# ── Fields discovery ──────────────────────────────────────────────────────────

class TestFields:
    def test_overview_lists_tables(self):
        r = client.get("/api/fields", headers=MOMO)
        assert r.status_code == 200
        assert "t4_notices" in r.json()["tables"]

    def test_per_table_fields(self):
        r = client.get("/api/fields?table=t4_notices", headers=MOMO)
        assert r.status_code == 200
        body = r.json()
        assert "service_name" in body["dimensions"]["fields"]
        assert "notices" in body["measures"]["fields"]
        assert "SUM" in body["aggregate_functions"]

    def test_unknown_table_is_404(self):
        assert client.get("/api/fields?table=nope", headers=MOMO).status_code == 404

    def test_schema_includes_field_help(self):
        # The schema browser needs per-field descriptions, not bare names.
        d = client.get("/api/schema/t4_notices").json()
        help = d["field_help"]
        assert "category_is_total" in help and "total" in help["category_is_total"].lower()
        assert "notices" in help and help["notices"]
        # Only fields the table actually has are documented.
        assert set(help) <= (set(d["dimensions"]["fields"]) | set(d["measures"]["fields"]))

    def test_schema_endpoints_are_public(self):
        # Schema discovery needs no API key — the same structure is already public
        # via /api/explore/options, /docs and /openapi.json.
        assert client.get("/api/fields").status_code == 200
        assert client.get("/api/fields?table=t4_notices").status_code == 200
        assert client.get("/api/tables").status_code == 200
        assert client.get("/api/schema/t4_notices").status_code == 200


# ── Query lifecycle ───────────────────────────────────────────────────────────

class TestQueryLifecycle:
    def test_submit_returns_202_with_location(self):
        r = client.post("/api/query", json=COUNT_ALL, headers=MOMO)
        assert r.status_code == 202
        assert "job_id" in r.json()
        assert r.headers.get("location", "").startswith("/api/jobs/")

    def test_happy_path_json(self):
        job = _submit_and_wait(COUNT_ALL)
        assert job["status"] == "done"
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=json", headers=MOMO)
        assert r.status_code == 200
        body = r.json()
        assert body["row_count"] == 1
        assert body["columns"] == ["n"]
        assert body["rows"][0][0] == 3  # 3 rows seeded in conftest.py

    def test_warnings_ride_along_to_result(self):
        # A double-count-prone query (no category_is_total grain) must carry its
        # warning all the way to /result and the polled job — not only the 202 —
        # so the exported artifact keeps its guardrail.
        job = _submit_and_wait({
            "table": "t4_notices", "group_by": ["service_name"],
            "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}],
        })
        assert any("double-count" in w for w in job.get("warnings", []))
        r = client.get(f"/api/jobs/{job['job_id']}/result", headers=MOMO)
        assert any("double-count" in w for w in r.json().get("warnings", []))

    def test_offset_pagination(self):
        base = {"table": "t4_notices", "group_by": ["service_name"],
                "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}],
                "query": {"and": [{"operation": "EQ", "field_name": "category_is_total", "field_values": ["1"]}]},
                "max_count": 1}
        p0 = client.post("/api/explore", json={**base, "offset": 0}).json()["rows"]
        p1 = client.post("/api/explore", json={**base, "offset": 1}).json()["rows"]
        assert p0 and p1 and p0[0][0] != p1[0][0]  # distinct rows under a stable order

    def test_paginated_pulls_are_deterministic(self):
        # Under pagination the tie-break kicks in (req.offset), giving a total order so
        # page boundaries are stable and repeated pulls are byte-identical — the property
        # snapshot diffing and offset paging actually depend on.
        q = {"table": "t4_notices", "group_by": ["service_name", "category_label"],
             "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}],
             "max_count": 5, "offset": 0}
        a = client.post("/api/explore", json=q).json()["rows"]
        b = client.post("/api/explore", json=q).json()["rows"]
        assert a == b
        # And page 2 never repeats a page-1 row (stable boundary).
        page2 = client.post("/api/explore", json={**q, "offset": 5}).json()["rows"]
        assert not ({tuple(r) for r in a} & {tuple(r) for r in page2})

    def test_report_id_is_traceable_dimension(self):
        # Each fact row exposes its source report_id, so (dataset version, report_id)
        # pins an exact source for citation. It's groupable and filterable.
        r = client.post("/api/explore", json={
            "table": "t4_notices", "group_by": ["report_id"],
            "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}],
            "sort": [{"field_name": "report_id", "order": "asc"}]}).json()
        assert r["rows"], r
        first_id = str(r["rows"][0][0])
        scoped = client.post("/api/explore", json={
            "table": "t4_notices", "group_by": ["report_id", "service_name"],
            "query": {"and": [{"operation": "EQ", "field_name": "report_id",
                               "field_values": [first_id]}]}}).json()
        assert scoped["rows"] and all(str(row[0]) == first_id for row in scoped["rows"])

    def test_gr_period_ord_sorts_chronologically(self):
        r = client.post("/api/explore", json={
            "table": "gr_removals", "group_by": ["period_ord", "period"],
            "aggregates": [{"function": "COUNT", "field_name": "*", "alias": "c"}],
            "sort": [{"field_name": "period_ord", "order": "asc"}]}).json()
        ords = [row[0] for row in r["rows"]]
        assert ords == sorted(ords)

    def test_happy_path_csv(self):
        job = _submit_and_wait(
            {
                "table": "t4_notices",
                "fields": ["service_name"],
                "sort": [{"field_name": "service_name", "order": "asc"}],
            }
        )
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=csv", headers=MOMO)
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        lines = r.text.strip().splitlines()
        assert lines[0] == "service_name"
        assert "YouTube" in lines
        assert "Facebook" in lines
        # CSV provenance rides on response headers (the body's header row is sacred).
        assert r.headers.get("X-Dataset-Period") and r.headers.get("X-App-Version")
        assert "transparency-" in r.headers.get("content-disposition", "")

    def test_submit_surfaces_double_count_warning(self):
        # The async path advises on submit too, so a scripted caller sees it before
        # polling — aggregating t4 by service without pinning the total grain.
        r = client.post(
            "/api/query",
            json={"table": "t4_notices", "group_by": ["service_name"],
                  "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}]},
            headers=MOMO,
        )
        assert r.status_code == 202
        assert any("category_is_total" in w for w in r.json().get("warnings", []))

    def test_filter_group_and_aggregate(self):
        # Notices per service, YouTube only — exercises filter + group + agg + sort.
        job = _submit_and_wait(
            {
                "table": "t4_notices",
                "query": {
                    "and": [
                        {"operation": "EQ", "field_name": "service_name", "field_values": ["YouTube"]}
                    ]
                },
                "group_by": ["service_name"],
                "aggregates": [
                    {"function": "SUM", "field_name": "notices", "alias": "notices"}
                ],
                "sort": [{"field_name": "notices", "order": "desc"}],
            }
        )
        assert job["status"] == "done"
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=json", headers=MOMO)
        body = r.json()
        assert body["columns"] == ["service_name", "notices"]
        # YouTube t4 rows in conftest: notices 100 + 40 = 140.
        assert body["rows"] == [["YouTube", 140]]

    def test_compiled_sql_is_parameterised(self):
        r = client.post(
            "/api/query",
            json={
                "table": "t4_notices",
                "query": {
                    "and": [
                        {"operation": "EQ", "field_name": "service_name", "field_values": ["YouTube"]}
                    ]
                }
            },
            headers=MOMO,
        )
        job = _wait_for_job(r.json()["job_id"], MOMO)
        # Value is bound, not interpolated — 'YouTube' must not appear in the SQL.
        assert "?" in job["compiled_sql"]
        assert "YouTube" not in job["compiled_sql"]

    def test_result_before_done_is_409(self):
        r = client.post("/api/query", json=COUNT_ALL, headers=MOMO)
        job_id = r.json()["job_id"]
        # Job may already be done by the time we hit the result endpoint,
        # but if it's still in-flight we expect 409.
        r2 = client.get(f"/api/jobs/{job_id}/result", headers=MOMO)
        assert r2.status_code in (200, 409)

    def test_list_jobs(self):
        _submit_and_wait(COUNT_ALL)
        r = client.get("/api/jobs", headers=MOMO)
        assert r.status_code == 200
        assert len(r.json()["jobs"]) >= 1


# ── Secure download URLs ──────────────────────────────────────────────────────

class TestSecureDownload:
    def test_done_job_exposes_signed_download_urls(self):
        job = _submit_and_wait(COUNT_ALL)
        urls = job["download_urls"]
        assert set(urls) == {"json", "csv"}
        for u in urls.values():
            assert "sig=" in u and "expires=" in u

    def test_download_without_api_key_works(self):
        job = _submit_and_wait(COUNT_ALL)
        url = job["download_urls"]["json"]
        # No X-API-Key header — the signed URL is the capability.
        r = client.get(url)
        assert r.status_code == 200
        assert r.json()["rows"][0][0] == 3
        assert "attachment" in r.headers.get("content-disposition", "")

    def test_download_csv_without_api_key_works(self):
        job = _submit_and_wait(COUNT_ALL)
        r = client.get(job["download_urls"]["csv"])
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]

    def test_tampered_signature_is_403(self):
        job = _submit_and_wait(COUNT_ALL)
        url = job["download_urls"]["json"]
        tampered = url[:-1] + ("0" if url[-1] != "0" else "1")
        assert client.get(tampered).status_code == 403

    def test_tampered_expiry_is_403(self):
        job = _submit_and_wait(COUNT_ALL)
        url = job["download_urls"]["json"]
        # Extending the expiry without re-signing breaks the signature.
        bumped = re.sub(r"expires=\d+", "expires=99999999999", url)
        assert client.get(bumped).status_code == 403

    def test_expired_link_is_410(self):
        from main import _download_signature

        job = _submit_and_wait(COUNT_ALL)
        job_id = job["job_id"]
        # Forge a correctly-signed but already-expired link.
        expires = 1
        sig = _download_signature(job_id, "json", expires)
        r = client.get(f"/api/jobs/{job_id}/download?format=json&expires={expires}&sig={sig}")
        assert r.status_code == 410

    def test_signature_bound_to_format(self):
        job = _submit_and_wait(COUNT_ALL)
        # Take the json URL but swap the declared format to csv — signature no longer matches.
        url = job["download_urls"]["json"].replace("format=json", "format=csv")
        assert client.get(url).status_code == 403

    def test_unknown_job_download_is_403(self):
        # Signature is verified before any store lookup, so an unknown job id with
        # an invalid signature returns 403 (not 404) — existence isn't leaked.
        r = client.get("/api/jobs/doesnotexist/download?format=json&expires=99999999999&sig=abc")
        assert r.status_code == 403


# ── Job isolation ─────────────────────────────────────────────────────────────

class TestJobIsolation:
    def test_hong_cannot_see_momos_job(self):
        job = _submit_and_wait(COUNT_ALL)
        assert client.get(f"/api/jobs/{job['job_id']}", headers=HONG).status_code == 404

    def test_hong_cannot_fetch_momos_result(self):
        job = _submit_and_wait(COUNT_ALL)
        r = client.get(f"/api/jobs/{job['job_id']}/result", headers=HONG)
        assert r.status_code == 404


# ── Safety: no arbitrary SQL ──────────────────────────────────────────────────

class TestSafety:
    def test_missing_table_is_400(self):
        r = client.post("/api/query", json={"aggregates": [{"function": "COUNT", "alias": "n"}]}, headers=MOMO)
        assert r.status_code == 400

    def test_unknown_table_is_400(self):
        r = client.post("/api/query", json={"table": "t99_nope", "fields": ["service_name"]}, headers=MOMO)
        assert r.status_code == 400

    def test_field_from_other_table_is_400(self):
        # `notices` belongs to t4, not t3 — it must not be accepted for t3.
        r = client.post(
            "/api/query",
            json={"table": "t3_member_state_orders", "fields": ["notices"]},
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_unknown_field_is_400(self):
        r = client.post(
            "/api/query",
            json={"table": "t4_notices", "query": {"and": [{"operation": "EQ", "field_name": "secrets", "field_values": ["x"]}]}},
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_comparator_on_text_field_is_400(self):
        r = client.post(
            "/api/query",
            json={"table": "t4_notices", "query": {"and": [{"operation": "GT", "field_name": "service_name", "field_values": [5]}]}},
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_bad_alias_is_400(self):
        r = client.post(
            "/api/query",
            json={"table": "t4_notices", "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "x); DROP"}]},
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_injection_value_is_treated_as_data(self):
        # A SQL-looking string in field_values is bound as a parameter, so the
        # query runs successfully and simply matches nothing — it is never code.
        job = _submit_and_wait(
            {
                "table": "t4_notices",
                "query": {
                    "and": [
                        {
                            "operation": "EQ",
                            "field_name": "service_name",
                            "field_values": ["YouTube'; DROP TABLE services;--"],
                        }
                    ]
                },
            }
        )
        assert job["status"] == "done"
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=json", headers=MOMO)
        assert r.json()["row_count"] == 0
        # DB still intact afterwards — a follow-up count still works.
        again = _submit_and_wait(COUNT_ALL)
        r2 = client.get(f"/api/jobs/{again['job_id']}/result?format=json", headers=MOMO)
        assert r2.json()["rows"][0][0] == 3

    def test_dimension_requires_string(self):
        # A numeric value on a TEXT dimension would silently match nothing under
        # SQLite affinity rules, so it is rejected up front.
        r = client.post(
            "/api/query",
            json={"table": "t4_notices", "query": {"and": [{"operation": "EQ", "field_name": "service_name", "field_values": [123]}]}},
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_duplicate_group_by_is_400(self):
        r = client.post(
            "/api/query",
            json={
                "table": "t4_notices",
                "group_by": ["service_name", "service_name"],
                "aggregates": [{"function": "COUNT", "alias": "n"}],
            },
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_alias_clashing_with_group_by_is_400(self):
        r = client.post(
            "/api/query",
            json={
                "table": "t4_notices",
                "group_by": ["service_name"],
                "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "service_name"}],
            },
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_duplicate_field_is_400(self):
        r = client.post(
            "/api/query",
            json={"table": "t4_notices", "fields": ["service_name", "service_name"]},
            headers=MOMO,
        )
        assert r.status_code == 400

    def test_extra_field_rejected(self):
        # The model has no free-form `sql` field, and unknown keys are now rejected
        # outright (extra="forbid") rather than silently ignored — so an injection
        # attempt via an `sql` field is a loud 422 and no query ever runs. (This
        # also guards the class of bug where a misnamed `conditions`/`filters` key
        # would have been dropped, returning unfiltered data.)
        r = client.post("/api/query", json={"table": "t4_notices", "sql": "DROP TABLE services"}, headers=MOMO)
        assert r.status_code == 422

    def test_misnamed_filter_key_rejected(self):
        # Regression: the removals dashboard once sent `conditions` instead of
        # `query`, which was silently ignored → unfiltered results. Now it 422s.
        r = client.post(
            "/api/query",
            json={"table": "t4_notices", "conditions": {"and": []}},
            headers=MOMO,
        )
        assert r.status_code == 422

    def test_unknown_job_is_404(self):
        assert client.get("/api/jobs/doesnotexist", headers=MOMO).status_code == 404


# ── Abuse hardening: complexity caps, body cap, CSV escaping ─────────────────────

class TestAbuseHardening:
    def test_oversized_in_list_is_422(self):
        cond = {"operation": "IN", "field_name": "service_name", "field_values": ["x"] * 101}
        r = client.post("/api/explore", json={"table": "t4_notices", "query": {"and": [cond]}})
        assert r.status_code == 422

    def test_too_many_conditions_is_422(self):
        cond = {"operation": "EQ", "field_name": "service_name", "field_values": ["x"]}
        r = client.post("/api/explore", json={"table": "t4_notices", "query": {"and": [cond] * 51}})
        assert r.status_code == 422

    def test_too_many_aggregates_is_422(self):
        aggs = [{"function": "SUM", "field_name": "notices", "alias": f"a{i}"} for i in range(51)]
        r = client.post("/api/explore", json={"table": "t4_notices", "aggregates": aggs})
        assert r.status_code == 422

    def test_oversized_body_is_413(self):
        body = b'{"table": "' + b"x" * (2 * 1024 * 1024) + b'"}'
        r = client.post(
            "/api/explore", content=body, headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 413

    def test_huge_content_length_header_is_413_not_500(self):
        # A digit string longer than CPython's int-parse limit (~4300 digits) must
        # short-circuit on length, not blow up in int() and surface as a 500.
        r = client.post(
            "/api/explore", content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "9" * 5000},
        )
        assert r.status_code == 413

    def test_jobs_limit_bounds_enforced(self):
        assert client.get("/api/jobs?limit=0", headers=MOMO).status_code == 422
        assert client.get("/api/jobs?limit=501", headers=MOMO).status_code == 422

    def test_csv_safe_neutralises_formula_sigils(self):
        import main

        assert main._csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
        assert main._csv_safe("+1+2") == "'+1+2"
        assert main._csv_safe("-2+3") == "'-2+3"
        assert main._csv_safe("@cmd") == "'@cmd"
        assert main._csv_safe("plain text") == "plain text"
        assert main._csv_safe(-5) == -5  # numbers are never mangled
        assert main._csv_safe(None) is None

    def test_csv_download_escapes_formula_cells(self):
        # The conftest fixture seeds a t11 row whose free text starts with "=" —
        # the rendered CSV must neutralise it so Excel/Sheets won't execute it.
        job = _submit_and_wait({
            "table": "t11_qualitative",
            "query": {"and": [{"operation": "EQ", "field_name": "service_name",
                               "field_values": ["Facebook"]}]},
            "fields": ["qualitative_text"],
        })
        assert job["status"] == "done"
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=csv", headers=MOMO)
        assert r.status_code == 200
        import csv as csv_mod
        import io

        data_rows = list(csv_mod.reader(io.StringIO(r.text)))[1:]
        cells = [cell for row in data_rows for cell in row]
        assert any(cell.startswith("'=") for cell in cells)
        assert not any(cell.startswith("=") for cell in cells)


# ── Cancel / delete ───────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_completed_job(self):
        job = _submit_and_wait(COUNT_ALL)
        job_id = job["job_id"]
        r = client.delete(f"/api/jobs/{job_id}", headers=MOMO)
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        assert client.get(f"/api/jobs/{job_id}", headers=MOMO).status_code == 404


# ── Query rate limiting ─────────────────────────────────────────────────────────

class TestQueryRateLimit:
    def test_over_limit_returns_429_with_retry_after(self):
        import main

        original_store, original_max = main._key_store, main.QUERY_RATE_MAX
        main._key_store = main.MemoryKeyStore()  # isolated, so it doesn't affect other tests
        main.QUERY_RATE_MAX = 2
        try:
            first = client.post("/api/query", json=COUNT_ALL, headers=MOMO)
            assert first.status_code == 202
            # Success responses advertise the budget so a caller can self-pace.
            assert first.headers["X-RateLimit-Limit"] == "2"
            assert first.headers["X-RateLimit-Remaining"] == "1"
            statuses = [client.post("/api/query", json=COUNT_ALL, headers=MOMO).status_code for _ in range(3)]
            assert statuses == [202, 429, 429]
            r = client.post("/api/query", json=COUNT_ALL, headers=MOMO)
            assert r.status_code == 429 and r.headers["Retry-After"] == str(main.QUERY_RATE_WINDOW)
            assert r.headers["X-RateLimit-Remaining"] == "0"
        finally:
            main._key_store, main.QUERY_RATE_MAX = original_store, original_max

    def test_limit_is_per_key(self):
        import main

        original_store, original_max = main._key_store, main.QUERY_RATE_MAX
        main._key_store = main.MemoryKeyStore()
        main.QUERY_RATE_MAX = 1
        try:
            assert client.post("/api/query", json=COUNT_ALL, headers=MOMO).status_code == 202
            assert client.post("/api/query", json=COUNT_ALL, headers=MOMO).status_code == 429
            # honggildong has their own bucket and is unaffected
            assert client.post("/api/query", json=COUNT_ALL, headers=HONG).status_code == 202
        finally:
            main._key_store, main.QUERY_RATE_MAX = original_store, original_max


# ── Structured logging ──────────────────────────────────────────────────────────

class TestLogging:
    def test_json_formatter_emits_event_and_data(self):
        import logging

        from main import JsonLogFormatter

        rec = logging.LogRecord("research_api", logging.INFO, __file__, 1, "job_done", None, None)
        rec.data = {"job_id": "abc", "rows": 5}
        line = json.loads(JsonLogFormatter().format(rec))
        assert line["event"] == "job_done"
        assert line["level"] == "INFO"
        assert line["job_id"] == "abc" and line["rows"] == 5
        assert "ts" in line

    def test_json_formatter_includes_exception(self):
        import logging
        import sys

        from main import JsonLogFormatter

        try:
            raise ValueError("boom")
        except ValueError:
            rec = logging.LogRecord(
                "research_api", logging.ERROR, __file__, 1, "request_error", None, sys.exc_info()
            )
        line = json.loads(JsonLogFormatter().format(rec))
        assert "boom" in line["exc"]

    def test_request_emits_request_id_header(self):
        r = client.get("/healthz")
        assert re.fullmatch(r"[0-9a-f]{16}", r.headers["X-Request-ID"])

    def test_formatter_uses_record_created_timestamp(self):
        import logging

        from main import JsonLogFormatter

        rec = logging.LogRecord("research_api", logging.INFO, __file__, 1, "e", None, None)
        rec.created = 1577836800.0  # 2020-01-01T00:00:00Z
        line = json.loads(JsonLogFormatter().format(rec))
        assert line["ts"].startswith("2020-01-01T00:00:00")

    def test_invalid_log_level_falls_back_to_info(self):
        import logging

        import main

        original = main.LOG_LEVEL
        main.LOG_LEVEL = "DEBUGGING"  # not a real level
        try:
            assert main._configure_logging().level == logging.INFO
        finally:
            main.LOG_LEVEL = original
            main._configure_logging()  # restore real config on the shared logger

    def test_unhandled_endpoint_exception_is_logged(self):
        # Regression guard: confirm our @app.middleware("http") still catches
        # unhandled endpoint exceptions (Starlette propagates them through
        # BaseHTTPMiddleware), so they reach the structured "request_error" log.
        import io
        import logging

        from fastapi import FastAPI
        from starlette.middleware.base import BaseHTTPMiddleware

        import main

        probe = FastAPI()
        probe.add_middleware(BaseHTTPMiddleware, dispatch=main.log_requests)

        @probe.get("/boom")
        def boom():
            raise ValueError("kaboom")

        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        main.logger.addHandler(handler)
        try:
            TestClient(probe, raise_server_exceptions=False).get("/boom")
        finally:
            main.logger.removeHandler(handler)
        assert "request_error" in buf.getvalue()


# ── Prometheus metrics ──────────────────────────────────────────────────────────

class TestMetrics:
    def test_metrics_endpoint_exposes_prometheus_text(self):
        client.get("/healthz")  # generate at least one request sample
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        body = r.text
        # Metric families are declared even before first use.
        for name in (
            "research_api_http_requests_total",
            "research_api_http_request_duration_seconds",
            "research_api_jobs_in_flight",
            "research_api_jobs_total",
            "research_api_job_queue_depth",
        ):
            assert name in body

    def test_request_counter_uses_route_template_not_raw_path(self):
        # A job id in the URL must not leak into label cardinality.
        job = _submit_and_wait(COUNT_ALL)
        client.get(f"/api/jobs/{job['job_id']}", headers=MOMO)
        body = client.get("/metrics").text
        assert 'path="/api/jobs/{job_id}"' in body
        assert job["job_id"] not in body  # the literal id is never a label value

    def test_job_completion_increments_jobs_total(self):
        from prometheus_client import REGISTRY

        before = REGISTRY.get_sample_value("research_api_jobs_total", {"status": "done"}) or 0.0
        _submit_and_wait(COUNT_ALL)
        after = REGISTRY.get_sample_value("research_api_jobs_total", {"status": "done"})
        assert after is not None and after >= before + 1

    def test_queue_depth_balances_to_zero_at_rest(self):
        from prometheus_client import REGISTRY

        # inc on submit / dec on pickup should net to zero once the job is done.
        _submit_and_wait(COUNT_ALL)
        assert REGISTRY.get_sample_value("research_api_job_queue_depth") == 0.0


# ── Webhook callbacks ───────────────────────────────────────────────────────────

class TestCallbacks:
    def test_bad_scheme_rejected_at_submit(self):
        r = client.post(
            "/api/query", json={**COUNT_ALL, "callback_url": "ftp://example.com/hook"}, headers=MOMO
        )
        assert r.status_code == 400

    def test_missing_host_rejected_at_submit(self):
        r = client.post(
            "/api/query", json={**COUNT_ALL, "callback_url": "http:///nohost"}, headers=MOMO
        )
        assert r.status_code == 400

    def test_ssrf_guard_blocks_private_targets(self):
        import main

        original = main.CALLBACK_ALLOW_PRIVATE
        main.CALLBACK_ALLOW_PRIVATE = False  # exercise the guard regardless of env
        try:
            for bad in (
                "http://127.0.0.1/x",           # loopback
                "http://169.254.169.254/x",     # cloud metadata (link-local)
                "http://10.1.2.3/x",            # private
                "http://[::1]/x",               # ipv6 loopback
                "http://[::ffff:127.0.0.1]/x",  # ipv4-mapped ipv6 loopback (bypass attempt)
                "http://100.64.0.1/x",          # CGNAT (not is_private, but not global)
                "http://192.0.0.192/x",         # IETF protocol assignments
                "http://198.18.0.1/x",          # benchmarking range
            ):
                with pytest.raises(main.CallbackUrlError):
                    main._validate_callback_url(bad)
            # A public literal IP (no DNS needed) passes.
            main._validate_callback_url("http://8.8.8.8/ok")
        finally:
            main.CALLBACK_ALLOW_PRIVATE = original

    def test_end_to_end_delivery_is_signed(self):
        import hashlib
        import hmac
        import http.server
        import threading

        import main
        from prometheus_client import REGISTRY

        captured: list[dict] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured.append({"headers": dict(self.headers), "body": self.rfile.read(n)})
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        delivered_before = REGISTRY.get_sample_value(
            "research_api_callbacks_total", {"result": "delivered"}
        ) or 0.0
        try:
            cb = f"http://127.0.0.1:{server.server_port}/hook"
            job = _submit_and_wait({**COUNT_ALL, "callback_url": cb})
            deadline = time.monotonic() + 5
            while not captured and time.monotonic() < deadline:
                time.sleep(0.05)
            assert captured, "callback was never delivered"
        finally:
            server.shutdown()

        rec = captured[0]
        payload = json.loads(rec["body"])
        assert payload["event"] == "job.done"
        assert payload["job"]["job_id"] == job["job_id"]
        assert payload["job"]["download_urls"]["json"]  # present on a done job
        expected = "sha256=" + hmac.new(
            main.DOWNLOAD_URL_SECRET.encode(), rec["body"], hashlib.sha256
        ).hexdigest()
        assert rec["headers"].get("X-Webhook-Signature") == expected

        after = REGISTRY.get_sample_value("research_api_callbacks_total", {"result": "delivered"})
        assert after is not None and after >= delivered_before + 1

    def test_unreachable_target_records_failure(self):
        import main
        from prometheus_client import REGISTRY

        original = main.CALLBACK_MAX_ATTEMPTS
        main.CALLBACK_MAX_ATTEMPTS = 1  # fail fast, no backoff sleeps
        before = REGISTRY.get_sample_value(
            "research_api_callbacks_total", {"result": "failed"}
        ) or 0.0
        try:
            _submit_and_wait({**COUNT_ALL, "callback_url": "http://127.0.0.1:1/closed"})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                now = REGISTRY.get_sample_value("research_api_callbacks_total", {"result": "failed"})
                if now is not None and now >= before + 1:
                    break
                time.sleep(0.05)
            assert (REGISTRY.get_sample_value(
                "research_api_callbacks_total", {"result": "failed"}) or 0.0) >= before + 1
        finally:
            main.CALLBACK_MAX_ATTEMPTS = original


# ── Google sign-in + admin approval ──────────────────────────────────────────


def _fake_verify(credential):
    """Stand-in for Google token verification. `credential` is treated as the
    account email; prefix tricks model the failure modes."""
    if credential == "BAD":
        raise ValueError("invalid token")
    if credential.startswith("unverified:"):
        return {"email": credential.split(":", 1)[1], "email_verified": False, "name": "X"}
    return {"email": credential, "email_verified": True, "name": credential.split("@")[0]}


class TestGoogleAuth:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        # Patch token verification and give each test a fresh registration store
        # so approval state doesn't leak between tests.
        import main
        monkeypatch.setattr(main, "_verify_id_token", _fake_verify)
        monkeypatch.setattr(main, "_registrations", main.MemoryRegistrationStore())
        yield

    def _signin(self, email):
        return client.post("/api/auth/google", json={"credential": email})

    def test_new_user_is_approved_immediately(self):
        r = self._signin("newbie@example.com")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "approved"
        assert client.get("/api/tables", headers={"X-API-Key": body["api_key"]}).status_code == 200

    def test_invalid_credential_is_401(self):
        assert self._signin("BAD").status_code == 401

    def test_unverified_email_is_401(self):
        assert self._signin("unverified:x@example.com").status_code == 401

    def test_legacy_pending_account_is_approved_on_signin(self):
        # Accounts left `pending` from when admin review existed sign straight in.
        import main
        main._registrations.upsert("res@example.com", {
            "email": "res@example.com", "name": "res", "status": "pending",
            "requested_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "approved_by": None,
        })
        r = self._signin("res@example.com")
        assert r.status_code == 200
        assert client.get("/api/tables", headers={"X-API-Key": r.json()["api_key"]}).status_code == 200

    def test_non_admin_cannot_reach_admin_endpoints(self):
        user_key = self._signin("u@example.com").json()["api_key"]
        assert client.get("/api/admin/registrations", headers={"X-API-Key": user_key}).status_code == 403
        assert client.get("/api/admin/registrations").status_code == 401

    def test_revoke_invalidates_live_session(self):
        admin_key = self._signin("admin@example.com").json()["api_key"]
        user_key = self._signin("r3@example.com").json()["api_key"]
        assert client.get("/api/jobs", headers={"X-API-Key": user_key}).status_code == 200
        client.post("/api/admin/registrations/r3@example.com/revoke", headers={"X-API-Key": admin_key})
        # The live session stops working immediately (re-checked on every request).
        assert client.get("/api/jobs", headers={"X-API-Key": user_key}).status_code == 401
        # And a fresh sign-in is rejected as revoked.
        assert self._signin("r3@example.com").status_code == 403

    def test_approve_restores_revoked_account(self):
        admin_key = self._signin("admin@example.com").json()["api_key"]
        self._signin("r4@example.com")
        client.post("/api/admin/registrations/r4@example.com/revoke", headers={"X-API-Key": admin_key})
        assert self._signin("r4@example.com").status_code == 403
        appr = client.post("/api/admin/registrations/r4@example.com/approve",
                           headers={"X-API-Key": admin_key})
        assert appr.status_code == 200 and appr.json()["status"] == "approved"
        r = self._signin("r4@example.com")
        assert r.status_code == 200
        assert client.get("/api/tables", headers={"X-API-Key": r.json()["api_key"]}).status_code == 200

    def test_precreated_account_gets_google_name_on_signin(self):
        # /approve before first sign-in placeholders the name with the email;
        # the first real sign-in must replace it with the Google profile name.
        admin_key = self._signin("admin@example.com").json()["api_key"]
        client.post("/api/admin/registrations/r5@example.com/approve",
                    headers={"X-API-Key": admin_key})
        r = self._signin("r5@example.com")
        assert r.status_code == 200
        assert r.json()["name"] == "r5"

    def test_list_registrations_filters_by_status(self):
        admin_key = self._signin("admin@example.com").json()["api_key"]
        self._signin("p1@example.com")
        self._signin("p2@example.com")
        client.post("/api/admin/registrations/p2@example.com/revoke", headers={"X-API-Key": admin_key})
        body = client.get("/api/admin/registrations?status=revoked",
                          headers={"X-API-Key": admin_key}).json()
        emails = {r["email"] for r in body["registrations"]}
        assert emails == {"p2@example.com"}

    def test_cannot_revoke_admin(self):
        admin_key = self._signin("admin@example.com").json()["api_key"]
        r = client.post("/api/admin/registrations/admin@example.com/revoke",
                        headers={"X-API-Key": admin_key})
        assert r.status_code == 400


# ── CORS (configurable cross-origin access) ──────────────────────────────────

class TestCORS:
    def test_disabled_by_default(self):
        # No ALLOWED_ORIGINS in the test env → no CORS headers emitted.
        r = client.get("/", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}

    def test_origin_parsing(self, monkeypatch):
        import main
        monkeypatch.setenv("ALLOWED_ORIGINS", " https://a.example , https://b.example ,")
        assert main._cors_origins() == ["https://a.example", "https://b.example"]


# ── Version / build identifier ───────────────────────────────────────────────

class TestVersion:
    def test_version_endpoint(self):
        r = client.get("/version")
        assert r.status_code == 200
        body = r.json()
        # Defaults to "dev" in tests (APP_VERSION unset); the CD workflow injects
        # the commit SHA on Cloud Run.
        assert body["version"] == "dev"
        assert "app_version" in body

    def test_x_version_header_on_every_response(self):
        r = client.get("/healthz")
        assert r.headers.get("X-Version") == "dev"

    def test_nosniff_header_on_every_response(self):
        r = client.get("/healthz")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_security_hardening_headers_on_every_response(self):
        r = client.get("/healthz")
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert "geolocation=()" in r.headers.get("Permissions-Policy", "")
        assert "max-age=" in r.headers.get("Strict-Transport-Security", "")

    def test_hardening_headers_present_on_500(self):
        # An unhandled exception must not escape without the hardening headers:
        # the middleware synthesises a 500 that still carries them.
        import main

        @main.app.get("/_boom_test")
        def _boom():  # pragma: no cover - body raises before returning
            raise RuntimeError("boom")

        try:
            local = TestClient(main.app, raise_server_exceptions=False)
            r = local.get("/_boom_test")
            assert r.status_code == 500
            assert r.headers.get("X-Content-Type-Options") == "nosniff"
            assert r.headers.get("Referrer-Policy") == "no-referrer"
            assert r.headers.get("X-Frame-Options") == "DENY"
            assert "max-age=" in r.headers.get("Strict-Transport-Security", "")
        finally:
            main.app.router.routes = [
                rt for rt in main.app.router.routes
                if getattr(rt, "path", None) != "/_boom_test"
            ]


# ── Combined site: dashboard + public overview ───────────────────────────────

class TestDashboard:
    def test_overview_is_public_and_populated(self):
        r = client.get("/api/overview")  # no X-API-Key
        assert r.status_code == 200
        d = r.json()
        assert d["services"] > 0 and d["platforms"] > 0
        assert d["total_notices"] >= 0
        assert isinstance(d["top_platforms"], list) and d["top_platforms"]
        assert {"platform", "notices"} <= set(d["top_platforms"][0])
        assert isinstance(d["by_category"], list)
        assert "period" in d and "generated" in d

    def test_overview_carries_dataset_version_and_etag(self):
        # The snapshot exposes an immutable version token (citable) and an ETag
        # that lets a client revalidate with a conditional GET → 304.
        r = client.get("/api/overview")
        assert r.json().get("version")
        etag = r.headers.get("ETag")
        assert etag and r.headers.get("Cache-Control")
        not_modified = client.get("/api/overview", headers={"If-None-Match": etag})
        assert not_modified.status_code == 304

    def test_dataset_version_is_db_content_fingerprint(self):
        # The version token is a digest of the served DB file, so it changes iff
        # the data changes (not on a code-only redeploy).
        import hashlib, os, main
        h = hashlib.sha256()
        with open(os.environ["DB_PATH"], "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        assert main._dataset_version() == h.hexdigest()[:12]

    def test_removals_overview_carries_version_and_etag(self):
        r = client.get("/api/overview/removals")
        assert r.status_code == 200 and r.json().get("version")
        etag = r.headers.get("ETag")
        assert etag
        assert client.get("/api/overview/removals",
                          headers={"If-None-Match": etag}).status_code == 304

    def test_overview_notices_not_double_counted(self):
        # Regression: t4 carries a reported grand-total row (code 'TOTAL') plus two
        # overlapping taxonomies (STATEMENT_CATEGORY_* and KEYWORD_*). The headline
        # must be the reported TOTAL, and the by-category breakdown must use the
        # statement categories only — never summed together (which double/triple-counts).
        import sqlite3, os
        d = client.get("/api/overview").json()
        con = sqlite3.connect(os.environ["DB_PATH"]); con.row_factory = sqlite3.Row
        reported_total = con.execute(
            "SELECT COALESCE(SUM(t.notices),0) FROM t4_notices t "
            "JOIN categories cat ON cat.id=t.category_id JOIN reports r ON r.id=t.report_id "
            "WHERE r.tier='vlop' AND cat.is_total=1").fetchone()[0]
        sum_all = con.execute(
            "SELECT COALESCE(SUM(t.notices),0) FROM t4_notices t "
            "JOIN reports r ON r.id=t.report_id WHERE r.tier='vlop'").fetchone()[0]
        con.close()
        # Headline equals the reported total, not the inflated sum-of-everything.
        assert d["total_notices"] == reported_total
        assert d["total_notices"] < sum_all
        # No category bar is the "All the entries" grand-total row.
        assert all("All the entries" != c["category"] for c in d["by_category"])
        # The statement-category bars never exceed the reported total.
        assert sum(c["notices"] for c in d["by_category"]) <= reported_total + 1

    def test_home_page_served_at_root(self):
        r = client.get("/")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "Platform transparency data" in r.text

    def test_dashboard_served_at_reports(self):
        r = client.get("/reports")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "/api/overview" in r.text  # dashboard fetches the public overview
        # The curated tabs must pin a single grain so a SUM never mixes incompatible
        # indicators / overlapping taxonomies (regression guard for the data fixes).
        assert "Number of measures solely taken by automated means" in r.text  # t8 Automated
        assert "Number of internal moderators employed by the provider" in r.text  # t9 moderators
        assert "STATEMENT_CATEGORY_ILLEGAL_OR_HARMFUL_SPEECH" in r.text  # t4 by-category breakdown
        # The tab strip is a complete ARIA tab pattern.
        assert 'role="tablist"' in r.text and 'aria-selected' in r.text

    def test_methodology_page_served(self):
        r = client.get("/methodology")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "<h1>Methodology</h1>" in r.text
        assert "script-src 'self'" in r.headers.get("content-security-policy", "")

    def test_methodology_page_localized(self):
        # The page is generated into every locale; the chrome + body translate and
        # the route serves with its own recomputed CSP.
        r = client.get("/es/methodology")
        assert r.status_code == 200
        assert "<h1>Metodología</h1>" in r.text
        assert "Methodology</h1>" not in r.text  # no English heading leaked
        assert "script-src 'self'" in r.headers.get("content-security-policy", "")

    def test_catalog_page_served(self):
        r = client.get("/catalog")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        # The report-locations panel + its data source live here now.
        assert "/api/report-locations" in r.text and 'id="rl-category"' in r.text

    def test_catalog_panel_moved_off_dashboard(self):
        # The catalogue panel was relocated from the dashboard to /catalog.
        assert 'id="rl-category"' not in client.get("/reports").text

    def test_mcp_page_served(self):
        r = client.get("/mcp")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "mcp_server.py" in r.text and "Model Context Protocol" in r.text
        # The page must document every tool the server actually registers. Parse
        # mcp_server.py as text (don't import it — its httpx dep isn't in the app
        # env) so this can't drift from build_server()'s registration list.
        import re
        import pathlib
        src = pathlib.Path(__file__).with_name("mcp_server.py").read_text()
        reg = re.search(r"for fn in \(([^)]*)\):", src, re.S)
        assert reg, "could not find the tool-registration tuple in mcp_server.py"
        tools = re.findall(r"[A-Za-z_]\w+", reg.group(1))
        assert len(tools) >= 8
        for tool in tools:
            assert f"<code>{tool}</code>" in r.text, f"{tool} missing from /mcp"
        # Host-config snippet matches the example file (server name + valid demo key).
        assert '"transparency-report-api"' in r.text and '"momo"' in r.text

    def test_mcp_localized_prose_not_in_english(self):
        for loc, needle in [("es", "El repositorio incluye"), ("zh", "代码库提供了")]:
            r = client.get(f"/{loc}/mcp")
            assert r.status_code == 200
            assert "The repository ships" not in r.text
            assert needle in r.text

    def test_new_pages_in_sidebar_nav(self):
        # Both new pages are linked from every page's sidebar nav.
        for path in ("/reports", "/catalog", "/mcp", "/schema"):
            t = client.get(path).text
            assert 'href="/catalog"' in t and 'href="/mcp"' in t

    def test_localized_catalog_and_mcp(self):
        assert client.get("/es/catalog").status_code == 200
        assert client.get("/ja/mcp").status_code == 200
        # Chrome/heading is localized (catalogue h1 in Spanish; MCP heading in Japanese).
        assert "Dónde publican" in client.get("/es/catalog").text
        assert "概要" in client.get("/ja/mcp").text

    def test_dashboard_uses_vendored_chartjs(self):
        r = client.get("/reports")
        # Chart.js is self-hosted, not loaded from a CDN.
        assert "/static/vendor/chart.umd.js" in r.text
        assert "cdn.jsdelivr.net" not in r.text

    def test_vendored_chartjs_served(self):
        r = client.get("/static/vendor/chart.umd.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert "immutable" in r.headers.get("cache-control", "")
        assert "Chart.js v4.4.4" in r.text  # genuine vendored bundle

    def test_unknown_vendor_asset_404(self):
        # Only allowlisted filenames are served — no path traversal / arbitrary reads.
        assert client.get("/static/vendor/secrets.js").status_code == 404
        assert client.get("/static/vendor/..%2fmain.py").status_code in (404, 400)


# ── Public report-locations catalogue (GET /api/report-locations) ────────────

class TestReportLocations:
    def test_public_and_populated(self):
        r = client.get("/api/report-locations")  # no X-API-Key
        assert r.status_code == 200
        d = r.json()
        # The conftest fixture seeds Reddit / Discord / Vinted.
        assert d["count"] == d["total"] == 3
        assert d["platform_count"] == 3
        names = {row["platform"] for row in d["rows"]}
        assert {"Reddit", "Discord", "Vinted"} <= names
        assert set(d["facets"]) == {"category", "confidence", "harmonised_template"}
        assert "verified" in d["facets"]["confidence"]
        # Sorted by platform name (case-insensitive): Discord, Reddit, Vinted.
        assert [row["platform"] for row in d["rows"]] == ["Discord", "Reddit", "Vinted"]
        # Reddit omits the optional columns — they surface as JSON null, not a crash.
        reddit = next(row for row in d["rows"] if row["platform"] == "Reddit")
        assert reddit["company"] is None and reddit["harmonised_template"] is None
        # The `archived` column links to the file mirrored in the data repo (Discord
        # has one; Reddit doesn't).
        discord = next(row for row in d["rows"] if row["platform"] == "Discord")
        assert discord["archived"].startswith("https://github.com/")
        assert reddit["archived"] is None

    def test_catalogue_carries_provenance(self):
        # The catalogue is its own CSV snapshot, so it exposes a content version +
        # build date (JSON body + X-Catalogue-Version header) and a stamped CSV
        # filename — so an exported slice is citable like every other export.
        r = client.get("/api/report-locations")
        d = r.json()
        assert d.get("version") and "generated" in d
        assert r.headers.get("X-Catalogue-Version") == d["version"]
        cv = client.get("/api/report-locations", params={"format": "csv"})
        assert cv.headers.get("X-Catalogue-Version") == d["version"]
        assert f'report-locations-{d["version"]}.csv' in cv.headers.get("content-disposition", "")

    def test_filter_by_confidence(self):
        r = client.get("/api/report-locations", params={"confidence": "verified"})
        d = r.json()
        assert d["count"] == 2 and d["total"] == 3
        assert all(row["confidence"] == "verified" for row in d["rows"])

    def test_filter_by_harmonised_template(self):
        r = client.get("/api/report-locations", params={"harmonised_template": "yes"})
        d = r.json()
        assert {row["platform"] for row in d["rows"]} == {"Discord", "Vinted"}

    def test_filter_by_category(self):
        r = client.get("/api/report-locations",
                       params={"category": "E-commerce marketplaces & retail"})
        d = r.json()
        assert d["count"] == 1 and d["rows"][0]["platform"] == "Vinted"

    def test_free_text_search(self):
        # Matches platform / company / url, case-insensitively.
        assert client.get("/api/report-locations",
                          params={"q": "reddit"}).json()["count"] == 1
        assert client.get("/api/report-locations",
                          params={"q": "discord.com"}).json()["count"] == 1
        assert client.get("/api/report-locations",
                          params={"q": "nomatch-xyz"}).json()["count"] == 0

    def test_combined_filters(self):
        r = client.get("/api/report-locations",
                       params={"confidence": "verified", "q": "reddit"})
        assert r.json()["count"] == 0  # Reddit is "likely", not "verified"

    def test_csv_export(self):
        r = client.get("/api/report-locations", params={"format": "csv"})
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "attachment" in r.headers.get("content-disposition", "")
        lines = r.text.splitlines()
        assert lines[0] == "platform,company,category,confidence,harmonised_template,format_period,url_label,url,archived"
        assert len(lines) == 4  # header + 3 rows


# ── Public interactive query (POST /api/explore) ─────────────────────────────

class TestExplore:
    def test_options_public(self):
        r = client.get("/api/explore/options")  # no key
        assert r.status_code == 200
        d = r.json()
        assert d["max_rows"] > 0 and "SUM" in d["aggregates"]
        t4 = next(t for t in d["tables"] if t["table"] == "t4_notices")
        assert "platform" in t4["dimensions"] and "notices" in t4["measures"]

    def test_explore_csv_format(self):
        q = {"table": "t4_notices", "group_by": ["platform"],
             "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "value"}],
             "query": {"and": [{"operation": "EQ", "field_name": "category_is_total", "field_values": ["1"]}]}}
        r = client.post("/api/explore?format=csv", json=q)
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert r.text.strip().splitlines()[0] == "platform,value"
        # CSV can't carry a metadata block, so provenance rides on headers.
        assert r.headers.get("X-Dataset-Period") and r.headers.get("X-App-Version")

    def test_explore_warns_on_double_count_grain(self):
        # Aggregating t4 without pinning category_is_total may double-count the
        # reported total with its breakdown — the API should advise (not block).
        q = {"table": "t4_notices", "group_by": ["service_name"],
             "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}]}
        d = client.post("/api/explore", json=q).json()
        assert any("category_is_total" in w for w in d.get("warnings", []))

    def test_explore_warns_on_median_aggregation(self):
        q = {"table": "t4_notices", "group_by": ["service_name"],
             "query": {"and": [{"operation": "EQ", "field_name": "category_is_total", "field_values": ["1"]}]},
             "aggregates": [{"function": "SUM", "field_name": "median_time", "alias": "m"}]}
        d = client.post("/api/explore", json=q).json()
        assert any("median" in w.lower() for w in d.get("warnings", []))

    def test_explore_no_warning_when_grain_pinned(self):
        # Pinning both the category total grain and the report tier → no advisories.
        q = {"table": "t4_notices", "group_by": ["service_name"],
             "query": {"and": [
                 {"operation": "EQ", "field_name": "category_is_total", "field_values": ["1"]},
                 {"operation": "EQ", "field_name": "report_tier", "field_values": ["vlop"]},
             ]},
             "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}]}
        assert "warnings" not in client.post("/api/explore", json=q).json()

    def test_report_tier_help_covers_vlose(self):
        # The 'vlop' tier is the aggregated designated set — it folds in VLOSEs
        # (search engines), so the field help must say so, not just "platform".
        response = client.get("/api/schema/t3_member_state_orders")
        assert response.status_code == 200
        body = response.json()
        assert "VLOSE" in body["field_help"]["report_tier"]

    def test_explore_warns_on_cross_tier_mix(self):
        # Not pinning report_tier mixes VLOP (H2-2025) with non-VLOP (often full-year).
        q = {"table": "t4_notices", "group_by": ["service_name"],
             "query": {"and": [{"operation": "EQ", "field_name": "category_is_total", "field_values": ["1"]}]},
             "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}]}
        d = client.post("/api/explore", json=q).json()
        assert any("report_tier" in w for w in d.get("warnings", []))

    def test_explore_aggregated_query_public(self):
        q = {"table": "t4_notices", "group_by": ["platform"],
             "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "value"}],
             "sort": [{"field_name": "value", "order": "desc"}], "max_count": 5}
        r = client.post("/api/explore", json=q)  # no key
        assert r.status_code == 200
        d = r.json()
        assert d["columns"] == ["platform", "value"]
        assert 0 < len(d["rows"]) <= 5
        vals = [row[1] for row in d["rows"]]
        assert vals == sorted(vals, reverse=True)  # sorted desc

    def test_explore_caps_rows(self):
        r = client.post("/api/explore", json={"table": "t4_notices",
                                              "fields": ["service_name", "notices"],
                                              "max_count": 100000})
        assert r.status_code == 200
        assert r.json()["row_count"] <= 500

    def test_explore_rejects_invalid_table(self):
        assert client.post("/api/explore", json={"table": "nope", "group_by": ["platform"]}).status_code == 400

    def test_explore_ignores_callback_url(self):
        # callback_url is stripped — no SSRF surface, query still runs.
        r = client.post("/api/explore", json={
            "table": "t4_notices", "group_by": ["platform"],
            "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "value"}],
            "max_count": 3, "callback_url": "http://169.254.169.254/latest/meta-data"})
        assert r.status_code == 200


# ── Composite (cross-table) queries ──────────────────────────────────────────

def _ratio_query(**overrides):
    """A canonical actions÷appeals composite over the fixture data."""
    q = {
        "legs": {
            "actions": {"table": "t5_own_initiative_illegal",
                        "aggregates": [{"function": "SUM", "field_name": "measures", "alias": "a"}]},
            "appeals": {"table": "t7_appeals_recidivism",
                        "aggregates": [{"function": "SUM", "field_name": "value", "alias": "p"}]},
        },
        "join_on": ["service_name"],
        "derived": [{"alias": "ratio", "expr": "actions.a / appeals.p"}],
        "sort": [{"field_name": "ratio", "order": "desc"}],
    }
    q.update(overrides)
    return q


class TestCompositeQueries:
    def test_ratio_end_to_end_via_explore(self):
        # Fixture: t5 has YouTube only (measures=9); t7 has YouTube=1000, Facebook=500.
        r = client.post("/api/explore", json=_ratio_query())
        assert r.status_code == 200
        d = r.json()
        assert d["columns"] == ["service_name", "a", "p", "ratio"]
        rows = {row[0]: row for row in d["rows"]}
        assert rows["YouTube"][1:] == [9, 1000, 0.009]
        # Full-outer semantics: Facebook has no t5 rows but is kept, with NULLs.
        assert rows["Facebook"][1:] == [None, 500, None]

    def test_composite_pagination_is_deterministic(self):
        # Composite (cross-table) pulls get the same deterministic tie-break as the
        # single-table path: paginated windows are stable across runs and disjoint
        # at the page boundary (regression — the composite compiler previously
        # appended no tie-break and ignored offset entirely).
        base = _ratio_query(sort=[], max_count=1)
        p0a = client.post("/api/explore", json={**base, "offset": 0}).json()["rows"]
        p0b = client.post("/api/explore", json={**base, "offset": 0}).json()["rows"]
        p1 = client.post("/api/explore", json={**base, "offset": 1}).json()["rows"]
        assert p0a and p0a == p0b                 # repeated pull is byte-identical
        assert p1 and p0a[0][0] != p1[0][0]       # page 2 is a different row

    def test_division_is_real_not_integer(self):
        # SUM of INTEGER columns must not integer-divide (9/1000 → 0).
        r = client.post("/api/explore", json=_ratio_query())
        ratio = {row[0]: row[3] for row in r.json()["rows"]}["YouTube"]
        assert 0 < ratio < 1

    def test_divide_by_zero_yields_null(self):
        q = _ratio_query(derived=[{"alias": "r", "expr": "actions.a / (appeals.p - appeals.p)"}],
                         sort=[])
        r = client.post("/api/explore", json=q)
        assert r.status_code == 200
        assert all(row[3] is None for row in r.json()["rows"])

    def test_having_filters_merged_rows(self):
        q = _ratio_query(having={"and": [{"operation": "GT", "field_name": "p",
                                          "field_values": [600]}]})
        r = client.post("/api/explore", json=q)
        assert [row[0] for row in r.json()["rows"]] == ["YouTube"]

    def test_having_accepts_numeric_strings(self):
        # The NL layer emits string values; numeric having columns coerce them.
        q = _ratio_query(having={"and": [{"operation": "GT", "field_name": "ratio",
                                          "field_values": ["0.001"]}]})
        r = client.post("/api/explore", json=q)
        assert r.status_code == 200
        assert [row[0] for row in r.json()["rows"]] == ["YouTube"]

    def test_leg_filters_apply_per_leg(self):
        q = _ratio_query()
        q["legs"]["appeals"]["query"] = {
            "and": [{"operation": "EQ", "field_name": "service_name", "field_values": ["Facebook"]}]}
        r = client.post("/api/explore", json=q)
        rows = {row[0]: row for row in r.json()["rows"]}
        assert rows["Facebook"][2] == 500
        assert rows["YouTube"][2] is None  # filtered out of the appeals leg only

    def test_leg_can_filter_on_is_total_dimension(self):
        # The Compare panel pins each leg's published total row (category_is_total /
        # scope_is_total) so a SUM is the headline figure, not the aggregate
        # double-counted with its own breakdown. Filtering a leg on those flags must
        # compile cleanly (regression: it once 500'd against a stale schema).
        q = {
            "legs": {
                "n": {"table": "t4_notices",
                      "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "av"}],
                      "query": {"and": [{"operation": "EQ", "field_name": "category_is_total", "field_values": ["1"]}]}},
                "u": {"table": "t10_amar",
                      "aggregates": [{"function": "SUM", "field_name": "value", "alias": "bv"}],
                      "query": {"and": [{"operation": "EQ", "field_name": "scope_is_total", "field_values": ["1"]}]}},
            },
            "join_on": ["service_name"],
            "derived": [{"alias": "result", "expr": "1000 * n.av / u.bv"}],
            "max_count": 5,
        }
        r = client.post("/api/explore", json=q)
        assert r.status_code == 200, r.text
        assert "result" in r.json()["columns"]

    def test_multi_dim_join(self):
        q = _ratio_query(join_on=["service_name", "platform"])
        r = client.post("/api/explore", json=q)
        assert r.status_code == 200
        assert r.json()["columns"][:2] == ["service_name", "platform"]

    def test_submitted_as_async_job(self):
        job = _submit_and_wait(_ratio_query())
        assert job["status"] == "done"
        res = client.get(f"/api/jobs/{job['job_id']}/result?format=json", headers=MOMO)
        assert res.status_code == 200
        assert res.json()["columns"] == ["service_name", "a", "p", "ratio"]

    # ── validation errors ──────────────────────────────────────────────────────

    def test_join_dim_must_be_shared(self):
        # category_code exists on t5 but not on t7 → 400 naming the shared dims.
        r = client.post("/api/explore", json=_ratio_query(join_on=["category_code"]))
        assert r.status_code == 400
        assert "shared" in r.json()["detail"].lower()

    def test_unknown_leg_table_rejected(self):
        q = _ratio_query()
        q["legs"]["actions"]["table"] = "nope"
        assert client.post("/api/explore", json=q).status_code == 400

    def test_duplicate_aggregate_alias_across_legs_rejected(self):
        q = _ratio_query()
        q["legs"]["appeals"]["aggregates"][0]["alias"] = "a"
        r = client.post("/api/explore", json=q)
        assert r.status_code == 400 and "unique" in r.json()["detail"].lower()

    def test_unknown_expr_reference_rejected(self):
        q = _ratio_query(derived=[{"alias": "r", "expr": "actions.a / nosuch.x"}])
        r = client.post("/api/explore", json=q)
        assert r.status_code == 400 and "nosuch.x" in r.json()["detail"]

    def test_expr_tolerates_surrounding_whitespace(self):
        q = _ratio_query(derived=[{"alias": "ratio", "expr": "  actions.a / appeals.p  "}])
        assert client.post("/api/explore", json=q).status_code == 200

    def test_malformed_exprs_rejected(self):
        for expr in ["actions.a /", "(actions.a", "actions.a ; DROP TABLE x",
                     "actions.a + 'x'", "actions"]:
            q = _ratio_query(derived=[{"alias": "r", "expr": expr}], sort=[])
            assert client.post("/api/explore", json=q).status_code == 400, expr

    def test_having_on_unknown_column_rejected(self):
        q = _ratio_query(having={"and": [{"operation": "GT", "field_name": "nope",
                                          "field_values": [1]}]})
        assert client.post("/api/explore", json=q).status_code == 400

    def test_sort_on_unknown_column_rejected(self):
        q = _ratio_query(sort=[{"field_name": "nope", "order": "desc"}])
        assert client.post("/api/explore", json=q).status_code == 400

    def test_legs_and_table_mutually_exclusive(self):
        q = _ratio_query(table="t4_notices")
        assert client.post("/api/explore", json=q).status_code == 400

    def test_composite_fields_require_legs(self):
        q = {"table": "t4_notices", "group_by": ["platform"],
             "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "n"}],
             "derived": [{"alias": "r", "expr": "x.y"}]}
        assert client.post("/api/explore", json=q).status_code == 400

    def test_single_leg_rejected(self):
        q = _ratio_query()
        del q["legs"]["appeals"]
        q["derived"] = []
        q["sort"] = []
        assert client.post("/api/explore", json=q).status_code == 422  # model bound

    def test_explore_leg_cap(self):
        q = _ratio_query()
        for name in ("third", "fourth"):
            q["legs"][name] = {"table": "t10_amar",
                               "aggregates": [{"function": "SUM", "field_name": "value", "alias": f"v_{name}"}]}
        r = client.post("/api/explore", json=q)
        assert r.status_code == 400 and "legs" in r.json()["detail"]
        # The keyed job API accepts the same 4-leg query.
        assert client.post("/api/query", json=q, headers=MOMO).status_code == 202

    def test_options_advertises_composite(self):
        d = client.get("/api/explore/options").json()
        assert d["composite"]["max_legs"] >= 2


# ── Natural-language query (POST /api/ask) ───────────────────────────────────

class TestAsk:
    @pytest.fixture(autouse=True)
    def _mock_llm(self, monkeypatch):
        # Stand in for the Claude call: return a fixed AskQuery for "top platforms
        # by notices". No network, deterministic.
        def fake_translate(question):
            assert isinstance(question, str) and question
            return {
                "table": "t4_notices",
                "filters": [],
                "group_by": ["platform"],
                "aggregates": [{"function": "SUM", "field": "notices", "alias": "notices"}],
                "sort": [{"field": "notices", "order": "desc"}],
                "max_count": 5,
            }
        import main
        main._ask_cache.clear()  # no cross-test cache leakage
        monkeypatch.setattr(main, "_translate_question", fake_translate)
        yield

    def test_ask_runs_generated_query(self):
        r = client.post("/api/ask", json={"question": "top platforms by notices?"}, headers=MOMO)
        assert r.status_code == 200
        d = r.json()
        assert d["question"] == "top platforms by notices?"
        assert d["query"]["table"] == "t4_notices"
        assert d["columns"] == ["platform", "notices"]
        assert 0 < len(d["rows"]) <= 5
        assert [row[1] for row in d["rows"]] == sorted([row[1] for row in d["rows"]], reverse=True)

    def test_ask_composite_generated_query_runs(self, monkeypatch):
        # The NL layer can emit the composite shape: legs + join_on + derived.
        import main
        monkeypatch.setattr(main, "_translate_question", lambda q: {
            "table": "t5_own_initiative_illegal", "filters": [], "group_by": [],
            "aggregates": [], "max_count": 10,
            "legs": [
                {"name": "actions", "table": "t5_own_initiative_illegal", "filters": [],
                 "aggregate": {"function": "SUM", "field": "measures", "alias": "a"}},
                {"name": "appeals", "table": "t7_appeals_recidivism", "filters": [],
                 "aggregate": {"function": "SUM", "field": "value", "alias": "p"}},
            ],
            "join_on": ["service_name"],
            "derived": [{"alias": "ratio", "expr": "actions.a / appeals.p"}],
            "sort": [{"field": "ratio", "order": "desc"}],
        })
        r = client.post("/api/ask", json={"question": "ratio of actions to appeals?"}, headers=MOMO)
        assert r.status_code == 200
        d = r.json()
        assert d["columns"] == ["service_name", "a", "p", "ratio"]
        rows = {row[0]: row for row in d["rows"]}
        assert rows["YouTube"][3] == 0.009

    def test_ask_caps_rows(self, monkeypatch):
        import main
        monkeypatch.setattr(main, "_translate_question", lambda q: {
            "table": "t4_notices", "filters": [], "group_by": [],
            "aggregates": [], "sort": [], "max_count": 100000,
        })
        r = client.post("/api/ask", json={"question": "everything"}, headers=MOMO)
        # raw (non-aggregated) query, capped at EXPLORE_MAX_ROWS
        assert r.status_code == 200 and r.json()["row_count"] <= 500

    def test_ask_invalid_generated_query_is_422_with_generated(self, monkeypatch):
        import main
        bad = {"table": "t4_notices", "filters": [], "group_by": ["not_a_field"],
               "aggregates": [], "sort": [], "max_count": 10}
        monkeypatch.setattr(main, "_translate_question", lambda q: bad)
        r = client.post("/api/ask", json={"question": "huh"}, headers=MOMO)
        assert r.status_code == 422
        assert r.json()["detail"]["generated"] == bad  # surfaces the model's attempt

    def test_ask_llm_failure_is_502(self, monkeypatch):
        import main
        def boom(q):
            raise RuntimeError("model unavailable")
        monkeypatch.setattr(main, "_translate_question", boom)
        assert client.post("/api/ask", json={"question": "x"}, headers=MOMO).status_code == 502

    def test_ask_disabled_is_503(self, monkeypatch):
        import main
        monkeypatch.setattr(main, "NL_QUERY_ENABLED", False)
        assert client.post("/api/ask", json={"question": "x"}, headers=MOMO).status_code == 503

    def test_ask_caches_translation(self, monkeypatch):
        import main
        calls = {"n": 0}
        def counting(q):
            calls["n"] += 1
            return {"table": "t4_notices", "filters": [], "group_by": ["platform"],
                    "aggregates": [{"function": "SUM", "field": "notices", "alias": "v"}],
                    "sort": [{"field": "v", "order": "desc"}], "max_count": 3}
        monkeypatch.setattr(main, "_translate_question", counting)
        r1 = client.post("/api/ask", json={"question": "Top platforms?"}, headers=MOMO)
        r2 = client.post("/api/ask", json={"question": "  top   PLATFORMS? "}, headers=MOMO)  # same, normalized
        assert r1.status_code == 200 and r2.status_code == 200
        assert calls["n"] == 1  # second served from cache
        assert r1.json()["cached"] is False and r2.json()["cached"] is True

    def test_ask_reports_truncated(self):
        r = client.post("/api/ask", json={"question": "top platforms by notices?"}, headers=MOMO)
        assert "truncated" in r.json()  # cap indicator present

    def test_ask_requires_key(self):
        # No key → 401 from require_api_key, before any LLM work (gated like /api/query).
        assert client.post("/api/ask", json={"question": "x"}).status_code == 401


def test_askquery_to_request_maps_count_star():
    import main
    req = main._askquery_to_request({
        "table": "t11_qualitative", "filters": [], "group_by": ["indicator"],
        "aggregates": [{"function": "COUNT", "field": "(rows)", "alias": "n"}],
        "sort": [{"field": "n", "order": "desc"}], "max_count": 3,
    })
    assert req.aggregates[0].field_name == "*"  # COUNT (rows) → COUNT(*)
    assert req.table == "t11_qualitative"


# ── Content-Security-Policy on the served HTML pages ─────────────────────────

class TestCSP:
    def _inline_hash(self, html):
        import re, hashlib, base64
        blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
        assert blocks, "expected an inline <script> block"
        return "'sha256-" + base64.b64encode(hashlib.sha256(blocks[0].encode()).digest()).decode() + "'"

    def test_dashboard_csp(self):
        r = client.get("/")
        csp = r.headers.get("Content-Security-Policy")
        assert csp and "default-src 'self'" in csp
        # Chart.js is vendored same-origin, so no third-party script origin is needed.
        assert "cdn.jsdelivr.net" not in csp
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert "'unsafe-inline'" not in csp.split("style-src")[0]  # no unsafe-inline for scripts
        # The inline-script hash is present, so a strict CSP won't break the page.
        assert self._inline_hash(r.text) in csp

    def test_api_key_csp(self):
        r = client.get("/api-key")
        csp = r.headers.get("Content-Security-Policy")
        assert "script-src 'self'" in csp and "https://accounts.google.com" in csp
        assert "frame-src https://accounts.google.com" in csp  # GSI sign-in iframe
        assert self._inline_hash(r.text) in csp


# ── Accessibility landmarks on the served HTML pages ─────────────────────────

class TestAccessibility:
    def test_home_a11y_landmarks(self):
        html = client.get("/").text
        assert 'href="#main"' in html and 'class="skip-link"' in html  # skip link
        assert 'id="main"' in html                                      # main landmark

    def test_dashboard_a11y_landmarks(self):
        html = client.get("/reports").text
        assert 'href="#main"' in html and 'class="skip-link"' in html  # skip link
        assert 'id="main"' in html                                     # main landmark
        assert 'role="alert"' in html                                  # error live region
        # Canvases carry an accessible table alternative, so hide them from AT.
        assert 'id="tab-chart" height="150" aria-hidden="true"' in html

    def test_api_key_a11y_landmarks(self):
        html = client.get("/api-key").text
        assert 'href="#main"' in html and 'class="skip-link"' in html
        assert 'id="main"' in html
        assert 'role="alert"' in html

    def test_schema_a11y_landmarks(self):
        html = client.get("/schema").text
        assert 'href="#main"' in html and 'class="skip-link"' in html
        assert 'id="main"' in html
        assert 'role="alert"' in html


# ── Localized static pages (es / fr / de) ────────────────────────────────────

class TestLocalization:
    LOCALES = ("es", "fr", "de", "ja", "zh", "ko")
    SUFFIXES = ("", "reports", "removals", "schema", "api-key", "privacy")

    def _path(self, loc, suffix):
        # Home is served with a trailing slash (/es/); sub-pages without.
        return f"/{loc}" + (f"/{suffix}" if suffix else "/")

    def _inline_hashes(self, html):
        # Every inline <script> block must be hashed — pages carry several
        # (theme + page logic + chrome), and only the translated ones differ.
        import re, hashlib, base64
        blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
        assert blocks, "expected an inline <script> block"
        return [
            "'sha256-" + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode() + "'"
            for b in blocks
        ]

    def test_every_localized_page_is_served(self):
        for loc in self.LOCALES:
            for suffix in self.SUFFIXES:
                path = self._path(loc, suffix)
                r = client.get(path)
                assert r.status_code == 200, path
                assert "text/html" in r.headers["content-type"], path
                assert f'<html lang="{loc}">' in r.text, path

    def test_localized_pages_keep_strict_csp(self):
        # The translated inline scripts differ from English, so the per-page CSP
        # hashes must be recomputed from the served bytes — verify *every* inline
        # block's hash is present, not just the first (theme) one.
        for loc in self.LOCALES:
            for suffix in self.SUFFIXES:
                path = self._path(loc, suffix)
                r = client.get(path)
                csp = r.headers.get("Content-Security-Policy")
                assert csp and "script-src 'self'" in csp, path
                assert "'unsafe-inline'" not in csp.split("style-src")[0], path
                for h in self._inline_hashes(r.text):
                    assert h in csp, f"missing hash for {path}: {h}"

    def test_localized_api_key_allows_google_signin(self):
        for loc in self.LOCALES:
            csp = client.get(f"/{loc}/api-key").headers.get("Content-Security-Policy", "")
            assert "https://accounts.google.com" in csp, loc
            assert "frame-src https://accounts.google.com" in csp, loc

    def test_content_is_actually_translated(self):
        # A representative translated string on each locale's home page.
        markers = {
            "es": "Transparencia de plataformas",
            "fr": "Transparence des plateformes",
            "de": "Plattform-Transparenz",
            "ja": "プラットフォームの透明性",
            "zh": "平台透明度",
            "ko": "플랫폼 투명성",
        }
        for loc, marker in markers.items():
            assert marker in client.get(f"/{loc}/").text, loc

    def test_switcher_links_across_locales(self):
        # The in-site switcher on the Spanish dashboard points at the same page
        # in every locale (English unprefixed, others prefixed).
        html = client.get("/es/reports").text
        for href in ('href="/reports"', 'href="/es/reports"',
                     'href="/fr/reports"', 'href="/de/reports"'):
            assert href in html, href
        # Active locale carries aria-current.
        assert 'is-current" href="/es/reports" aria-current="page"' in html

    def test_internal_chrome_links_are_prefixed(self):
        # Sidebar / brand links stay within the locale; Swagger + the JSON API don't.
        html = client.get("/es/reports").text
        assert 'href="/es/removals"' in html and 'href="/es/api-key"' in html
        assert 'href="/es/schema"' in html
        assert 'href="/docs"' in html  # Swagger link is locale-agnostic, never prefixed
        assert "/api/explore" in html and "/es/api/" not in html  # API calls aren't prefixed

    def test_english_home_uses_in_site_switcher(self):
        # The globe now switches the transparency site's own language.
        html = client.get("/").text
        assert 'href="/es/"' in html and 'href="/fr/"' in html and 'href="/de/"' in html


# ── Google Government Removals table ─────────────────────────────────────────

class TestGRTable:
    def test_gr_table_listed(self):
        r = client.get("/api/tables", headers=MOMO)
        assert r.status_code == 200
        names = [t["name"] for t in r.json()["tables"]]
        assert "gr_removals" in names

    def test_gr_fields_endpoint(self):
        r = client.get("/api/fields?table=gr_removals", headers=MOMO)
        assert r.status_code == 200
        body = r.json()
        assert {"period", "country_code", "country_name", "requestor", "product", "reason"} <= set(body["dimensions"]["fields"])
        assert {"num_requests", "items_requested", "removed_legal"} <= set(body["measures"]["fields"])

    def test_gr_count_all(self):
        job = _submit_and_wait({
            "table": "gr_removals",
            "aggregates": [{"function": "COUNT", "alias": "n"}],
        })
        assert job["status"] == "done"
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=json", headers=MOMO)
        body = r.json()
        assert body["columns"] == ["n"]
        assert body["rows"][0][0] == 3  # 3 rows in the fixture

    def test_gr_filter_by_country_code(self):
        job = _submit_and_wait({
            "table": "gr_removals",
            "query": {"and": [{"operation": "EQ", "field_name": "country_code", "field_values": ["US"]}]},
            "aggregates": [{"function": "SUM", "field_name": "num_requests", "alias": "reqs"}],
        })
        assert job["status"] == "done"
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=json", headers=MOMO)
        body = r.json()
        # US rows in fixture: period0/US (num_requests=5) + period1/US (num_requests=7) = 12
        assert body["rows"][0][0] == 12

    def test_gr_group_by_period(self):
        job = _submit_and_wait({
            "table": "gr_removals",
            "aggregates": [{"function": "SUM", "field_name": "num_requests", "alias": "reqs"}],
            "group_by": ["period"],
            "sort": [{"field_name": "period", "order": "asc"}],
        })
        assert job["status"] == "done"
        r = client.get(f"/api/jobs/{job['job_id']}/result?format=json", headers=MOMO)
        body = r.json()
        assert body["columns"] == ["period", "reqs"]
        assert len(body["rows"]) == 2
        assert body["rows"][0][0] == "January - June 2019"
        # period 0 total: rows 0 (5) + 1 (3) = 8
        assert body["rows"][0][1] == 8

    def test_gr_invalid_field_rejected(self):
        r = client.post("/api/query", json={
            "table": "gr_removals",
            "query": {"and": [{"operation": "EQ", "field_name": "nonexistent_field", "field_values": ["x"]}]},
        }, headers=MOMO)
        assert r.status_code == 400

    def test_gr_overview_removals(self):
        r = client.get("/api/overview/removals")
        assert r.status_code == 200
        d = r.json()
        assert "total_requests" in d and "total_items" in d
        assert "country_count" in d and "period_count" in d
        assert isinstance(d["periods"], list) and len(d["periods"]) >= 1
        assert isinstance(d["countries"], list) and isinstance(d["countries"][0], dict)
        assert "code" in d["countries"][0] and "name" in d["countries"][0]
        assert isinstance(d["requestors"], list)
        assert isinstance(d["products"], list)
        assert isinstance(d["reasons"], list)
        # Fixture has 3 rows: spot-check totals are non-negative integers
        assert d["total_requests"] >= 0
        assert d["total_items"] >= 0

    def test_vendored_gr_dataset_spans_2011_to_2025(self):
        # The shipped Google-removals snapshot (what the Docker image seeds from)
        # must cover the full 2011–2025 range, not just the old 2019+ slice.
        import json
        import pathlib
        data = json.loads(pathlib.Path(__file__).with_name("data")
                          .joinpath("google-government-removals.json").read_text())
        periods = data["periods"]
        assert len(periods) == 30
        assert periods[0] == "January - June 2011"
        assert periods[-1] == "July - December 2025"
        # Row width matches the schema the seeder expects (5 dims + 8 measures).
        assert all(len(row) == 13 for row in data["rows"][:50])

    def test_removals_page_served(self):
        r = client.get("/removals")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "/api/overview/removals" in r.text
        assert "Government Requests" in r.text
        # Column legend + honest framing of the "Items removed" column.
        assert "What the columns mean" in r.text
        # "Items removed" must NOT fold in already-removed items (matches the
        # removal-rate chart's numerator: legal + policy only).
        assert "var removed = (r[7]||0) + (r[8]||0);" in r.text
        # The records table exposes column scope + a localizable caption for AT.
        assert 'th scope="col">Period' in r.text

    def test_removals_localized_cite_as_not_in_english(self):
        # The provenance "Cite as" citation is built in JS; it must be translated
        # on the localized pages (regression: it used to leak English).
        for loc, needle in [("es", "vía la Transparency Report API"),
                            ("ja", "Transparency Report API 経由"),
                            ("zh", "经由 Transparency Report API")]:
            r = client.get(f"/{loc}/removals")
            assert r.status_code == 200
            assert "Google government content-removal requests, via" not in r.text
            assert needle in r.text
            # The scope="col" a11y attribute must survive localization (regression:
            # the two-line ja/zh/ko th tuples once stripped it).
            assert 'th scope="col"' in r.text


# ── Non-VLOP harmonised-template reports loaded into the star schema ──────────

class TestHarmonisedFacts:
    """build_harmonised_facts() appends the extracted non-VLOP reports (from the
    vendored snapshot) into the same t3-t11 model, queryable alongside the VLOPs."""

    def _build(self, tmp_path):
        import os
        import sqlite3
        import seed
        import seed_harmonised
        db = str(tmp_path / "h.db")
        # Minimal VLOP base so the dimension tables + a vlop-tier report exist.
        seed.build_db({
            "meta": {"period": "2025-07-01/2025-12-31", "tier": "vlop"},
            "services": ["YouTube"], "service_platforms": ["Google"],
            "categories": ["TOTAL"], "category_labels": {"TOTAL": "All the entries"},
            "sections": ["AMAR"], "indicators": ["x"], "scopes": ["TOTAL"], "surfaces": ["All"],
            "t3": [], "t4": [[0, 0, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0]], "t5": [], "t6": [],
            "t7": [], "t8": [], "t9": [], "t10": [[0, 0, 999]], "t11": [],
        }, db)
        snap = os.path.join(os.path.dirname(seed_harmonised.__file__), "data",
                            "harmonised-reports.json")
        counts = seed_harmonised.build_harmonised_facts(db, snapshot_path=snap)
        return db, counts, sqlite3.connect(db)

    def test_appends_non_vlop_services(self, tmp_path):
        db, counts, conn = self._build(tmp_path)
        # Every non-VLOP slug in the snapshot loads as one service + one report
        # (the snapshot's 3 already-VLOP platforms — LinkedIn/Pinterest/Wikipedia
        # — are skipped). Assert it tracks the snapshot rather than a fixed count.
        import os
        import json as _json
        import seed_harmonised as _sh
        snap_path = os.path.join(os.path.dirname(_sh.__file__), "data", "harmonised-reports.json")
        with open(snap_path, encoding="utf-8") as f:
            snap = _json.load(f)
        # One report per non-VLOP slug; one service per slug, except extra-period
        # slugs (e.g. aboutyou2) attach to an existing service.
        loaded = [s for s in snap if s not in _sh.SKIP_SLUGS]
        assert counts["reports"] == len(loaded)
        assert counts["services"] == len([s for s in loaded if s not in _sh.EXTRA_PERIODS])
        names = {r[0] for r in conn.execute("SELECT name FROM services")}
        assert {"ManoMano", "Roblox", "Web.de", "Skroutz"} <= names
        # The three already-VLOP platforms are skipped (not re-added).
        assert sum(1 for n in names if n == "Wikipedia") == 0

    def test_extra_period_attaches_to_existing_service(self, tmp_path):
        # AboutYou ships two consecutive periods (aboutyou + aboutyou2); the second
        # must be a new report on the same service, not a duplicate service.
        db, counts, conn = self._build(tmp_path)
        svc = conn.execute("SELECT id FROM services WHERE name = 'AboutYou'").fetchall()
        assert len(svc) == 1
        periods = conn.execute(
            "SELECT DISTINCT r.period_start, r.period_end FROM reports r "
            "JOIN t4_notices t ON t.report_id = r.id WHERE t.service_id = ? "
            "ORDER BY r.period_start", (svc[0][0],)).fetchall()
        assert len(periods) == 2 and periods[0] != periods[1]

    def test_non_vlop_facts_queryable(self, tmp_path):
        db, counts, conn = self._build(tmp_path)
        # ManoMano's Article 16 notices landed in t4.
        n = conn.execute(
            "SELECT COALESCE(SUM(t.notices), 0) FROM t4_notices t JOIN services s "
            "ON s.id = t.service_id WHERE s.name = 'ManoMano'").fetchone()[0]
        assert n > 0
        # New reports carry a non-vlop tier.
        tiers = {r[0] for r in conn.execute("SELECT DISTINCT tier FROM reports")}
        assert "vlop" in tiers and tiers - {"vlop"}

    def test_overview_stays_vlop_scoped(self, tmp_path):
        # The vlop-tier base report has one service / 100 notices; the harmonised
        # load must not change a tier-scoped headline count.
        db, counts, conn = self._build(tmp_path)
        vlop_notices = conn.execute(
            "SELECT COALESCE(SUM(t.notices), 0) FROM t4_notices t JOIN reports r "
            "ON r.id = t.report_id WHERE r.tier = 'vlop'").fetchone()[0]
        assert vlop_notices == 100


class TestDimensionNormalization:
    """seed.normalize_dimensions flags aggregate rows and drops junk facts so a
    naive SUM never double-counts a total with its own breakdown."""

    def _build(self, tmp_path):
        import sqlite3
        import seed
        db = str(tmp_path / "norm.db")
        seed.build_db({
            "meta": {"period": "2025-07-01/2025-12-31", "tier": "vlop"},
            "services": ["S"], "service_platforms": ["P"],
            "categories": ["TOTAL", "X"],
            "category_labels": {"TOTAL": "All the entries", "X": "Other"},
            "sections": ["s"], "indicators": ["i"],
            # 0 = EU total (aggregate), 1 = member state (leaf), 2 = junk header,
            # 3 = a legitimate numeric range that must NOT be treated as junk.
            "scopes": ["TOTAL", "DE", "[...]", "9 & 10"], "surfaces": ["All"],
            # t10: [svc, scope, value] — total + member state + junk + numeric-range.
            "t10": [[0, 0, 100], [0, 1, 100], [0, 2, 999], [0, 3, 42]],
        }, db)
        return sqlite3.connect(db)

    def test_total_rows_flagged(self, tmp_path):
        conn = self._build(tmp_path)
        assert conn.execute("SELECT is_total FROM scopes WHERE name = 'TOTAL'").fetchone()[0] == 1
        assert conn.execute("SELECT is_total FROM scopes WHERE name = 'DE'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT is_total FROM categories WHERE label = 'All the entries'").fetchone()[0] == 1

    def test_junk_fact_rows_dropped(self, tmp_path):
        conn = self._build(tmp_path)
        # The '[...]' header cell was mis-parsed as a scope; its fact row AND the
        # now-orphaned dimension row are both gone.
        assert conn.execute(
            "SELECT COUNT(*) FROM t10_amar t JOIN scopes s ON s.id = t.scope_id "
            "WHERE s.name = '[...]'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scopes WHERE name = '[...]'").fetchone()[0] == 0
        # Total + leaf + the legitimate "9 & 10" range remain (3 rows, not 4) —
        # a numeric label with a separator is NOT treated as a stray cell.
        assert conn.execute("SELECT COUNT(*) FROM t10_amar").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM scopes WHERE name = '9 & 10'").fetchone()[0] == 1

    def test_canonical_key_unifies_languages(self, tmp_path):
        import sqlite3
        import seed
        # Inject a crosswalk so the test stays hermetic (no dependence on the
        # vendored file), then restore the lazy loader.
        seed._CROSSWALK = {"scope": {"Décisions confirmées": "Decisions upheld"}}
        try:
            db = str(tmp_path / "cw.db")
            seed.build_db({
                "meta": {"period": "2025-07-01/2025-12-31", "tier": "vlop"},
                "services": ["S"], "service_platforms": ["P"],
                "categories": ["TOTAL"], "category_labels": {"TOTAL": "All the entries"},
                "sections": ["Internal complaints mechanism"], "indicators": ["i"],
                "scopes": ["Decisions upheld", "Décisions confirmées"], "surfaces": ["All"],
                "t7": [[0, 0, 0, 0, 5, 0], [0, 0, 0, 1, 7, 0]],
            }, db)
            conn = sqlite3.connect(db)
            keys = dict(conn.execute("SELECT name, key FROM scopes"))
            # The French label keeps its display text but shares the English key.
            assert keys["Décisions confirmées"] == "Decisions upheld"
            assert keys["Decisions upheld"] == "Decisions upheld"
            # An unmapped/English label keys to itself.
            assert conn.execute("SELECT key FROM sections").fetchone()[0] == \
                "Internal complaints mechanism"
        finally:
            seed._CROSSWALK = None

    def test_total_grain_avoids_double_count(self, tmp_path):
        conn = self._build(tmp_path)
        # Summing the total row alone (is_total=1) gives 100, not 200 (total+leaf).
        only_total = conn.execute(
            "SELECT SUM(t.value) FROM t10_amar t JOIN scopes s ON s.id = t.scope_id "
            "WHERE s.is_total = 1").fetchone()[0]
        all_rows = conn.execute("SELECT SUM(value) FROM t10_amar").fetchone()[0]
        # is_total=1 → 100 (the EU total); all-rows mixes total + leaves (100 + 100
        # + 42) and over-counts.
        assert only_total == 100 and all_rows == 242
