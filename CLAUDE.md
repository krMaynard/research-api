# transparency-report-api — Claude context

## What this is

A FastAPI service that accepts **structured query parameters** (not SQL) via
HTTP, runs the resulting query asynchronously on background worker threads, and
returns results as JSON or CSV. Backed by a read-only SQLite database seeded from
transparency-reporting datasets: the aggregated **EU Digital Services Act (DSA)
VLOP transparency reports** (content-moderation statistics for 33 designated Very
Large Online Platforms / Search Engines, H2 2025, tables 3–11 of the DSA
Implementing Regulation template) and **Google Government content-removal
requests**.

Built to demonstrate two things:

1. The **async-job / poll pattern**: `POST /query` returns `202 + job_id`
   immediately; the client polls `/jobs/{id}` until `status=done`, then
   fetches `/jobs/{id}/result`.
2. A **safe, no-SQL query interface** modelled on the TikTok Research API: a
   query names a `table` (one of the 9 DSA report tables), then a boolean
   `and`/`or`/`not` clause of `{operation, field_name, field_values}`, plus
   `group_by`, `aggregates`, `sort`, and `max_count`. The server validates
   everything against that table's fixed field registry (`TABLES`/`TableSpec`)
   and compiles it into a single parameterised SELECT (`compile_query` in
   `main.py`). Arbitrary SQL is never accepted or executed.

## Repo layout

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app — all endpoints, job runner, in-memory job registry |
| `seed.py` | Build `demo.db` from a `vlop-dsa.json` (`--source`/`SEED_SOURCE_JSON`; default = sibling repo) — `build_db()` is reused by `conftest.py`. Also loads gr removals, `report_locations`, and the non-VLOP harmonised reports |
| `seed_harmonised.py` | Append the **non-VLOP harmonised-template reports** into the same `t3`–`t11` star schema (`build_harmonised_facts()`): one new `reports` row (tier ≠ `vlop`) + `services` row per platform, dimensions interned/extended. Reads the vendored `data/harmonised-reports.json` snapshot (or the sibling repo's extracted CSVs in dev); `write_snapshot()` rebuilds the snapshot |
| `data/vlop-dsa.json` | Vendored dataset snapshot — what the Docker image is seeded from (refresh via `scripts/refresh-dataset.sh`) |
| `data/harmonised-reports.json` | Vendored snapshot of the 49 extracted non-VLOP harmonised-template reports (sibling `dsa-transparency-data/harmonised-reports/extracted/`) — seeded into `t3`–`t11` by `seed_harmonised.py` |
| `data/report-locations.csv` | Vendored snapshot of the non-VLOP DSA report-locations catalogue (sibling `dsa-transparency-data/dsa_reports.csv`) — seeded into the read-only `report_locations` table by `seed.py` |
| `data/template-crosswalk.json` | Vendored `{original-language label → canonical English}` map for the template's `sections`/`indicators`/`scopes`, applied by `seed.normalize_dimensions` to stamp each dim row's language-neutral `key`. Regenerate with `scripts/build_template_crosswalk.py` |
| `scripts/build_template_crosswalk.py` | Learns `data/template-crosswalk.json` by aligning same-structure non-VLOP report sheets to an English reference (drops ambiguous labels) — reads the sibling repo's extracted CSVs |
| `demo.py` | Narrated walkthrough script (run after starting the server) |
| `static/index.html` | Public VLOP dashboard (served at `/reports`) — Chart.js overview + interactive query builder + "Compare tables" composite panel + NL "Ask" box (`GET /api/overview`, `POST /api/explore`, `POST /api/ask`) |
| `static/catalog.html` | Public report-locations catalogue page (served at `/catalog`) — the "Where platforms publish their reports" filterable table over `GET /api/report-locations` |
| `static/mcp.html` | Public MCP-server info page (served at `/mcp`) — documents `mcp_server.py`, its 8 tools, and host config; static, no page JS |
| `static/methodology.html` | Public methodology page (served at `/methodology`) — how the dataset is sourced, processed (double-count handling, cross-language keys), queried, and cited, plus known limitations; static, no page JS |
| `static/vendor/chart.umd.js` | Vendored Chart.js 4.4.4 (self-hosted, not a CDN) — served by the `/static/vendor/{filename}` route so the dashboard CSP stays `script-src 'self'` |
| `static/api-key.html` | API-key sign-in page (served at `/api-key`; formerly the "researcher portal") — Google sign-in + demo fallback. `/portal` 308-redirects here |
| `static/schema.html` | Public dataset-schema browser (served at `/schema`) — report tables + dimensions/measures, no sign-in (reads `/api/tables` + `/api/schema/{table}`) |
| `static/{es,fr,de,ja,zh,ko}/*.html` | Localized copies of the nine pages, served under a locale prefix (`/es`, `/es/reports`, …). **Generated** — never hand-edit; see `scripts/localize_static.py` |
| `scripts/localize_static.py` | Generates the localized pages from the English originals + per-locale translation tables (the single source of UI translations). Re-run after any English page change |
| `Dockerfile` | Self-contained image: installs deps, seeds `demo.db` at build time, runs uvicorn on `$PORT` as non-root |
| `service.yaml` | Cloud Run (Knative) manifest — prod env + startup/liveness probes |
| `scripts/refresh-dataset.sh` | Re-vendor `data/vlop-dsa.json` from the canonical sibling-repo dataset |
| `scripts/revendor_data.py` | Re-vendor the **non-VLOP** snapshots (`data/harmonised-reports.json` + `data/report-locations.csv`) from the sibling `dsa-transparency-data` repo and report any extracted platform still missing a `seed_harmonised.SLUG_META` entry. Run by the `revendor-data.yml` workflow (nightly / on dispatch); also runnable locally (`--check` for a dry run) |
| `scripts/_demo_server.py` | Shared helper: seed DB + run a temp server (used by the GIF generators) |
| `scripts/make_gifs.py` | Headless terminal-demo GIF generator (pyte + Pillow) → `docs/gifs/` |
| `scripts/make_portal_gifs.py` | Portal-workflow GIF generator (Playwright + Pillow) → `docs/gifs/portal-*.gif` |
| `requirements.txt` | `fastapi` + `uvicorn[standard]` + `anthropic` (NL queries) |
| `demo.db` | SQLite DB (git-ignored, produced by `seed.py`) |
| `clients/cli/` | Generated Go CLI + MCP server for this API (CLI Printing Press, from `/openapi.json`) — own module; built on demand, excluded from the Docker/Cloud Build image |
| `mcp_server.py` | Native Python MCP **stdio** server — a thin HTTP front end over the API (8 tools: `list_tables`/`describe_table`/`dataset_overview`/`run_query`/`ask`/`register`/`submit_query`/`poll_job`). Does **not** import `main`; talks to a running server over `httpx`, so its deps (`mcp`+`httpx`) stay out of the app image and clear of the `fastapi`/`starlette` pins. Configured via `TRANSPARENCY_API_URL`/`_API_KEY`/`_API_TIMEOUT`. See [`docs/MCP.md`](docs/MCP.md) |
| `requirements-mcp.txt` | Deps for `mcp_server.py` only (`mcp`, `httpx`) — install into a separate venv (`make mcp`); kept out of `requirements.txt`/the Docker image |
| `mcp-config.example.json` | Example MCP host config (Claude Desktop / Claude Code) for `mcp_server.py` |
| `test_mcp_server.py` | Tests for `mcp_server.py` — drives the tool functions against the app via an in-process `TestClient` (no network, no `mcp` SDK needed; the `build_server()` test self-skips when the SDK is absent) |
| `.github/workflows/ci.yml` | CI: `pyflakes` lint + `pytest` on every PR/push (Python 3.11 & 3.12) |
| `.github/workflows/deploy.yml` | CD: build/push image + deploy to Cloud Run on push to `main` (WIF; skips until configured) |
| `.github/workflows/revendor-data.yml` | Auto-vendoring: regenerate the non-VLOP snapshots from `dsa-transparency-data` and open/update a single `auto/revendor-data` PR when they change. Triggers: nightly schedule, `workflow_dispatch`, or a `data-updated` `repository_dispatch` from the data repo. Validates by reseeding + `pytest` before opening the PR |
| `.gcloudignore` | Trims the Cloud Build upload context (keeps Dockerfile + `data/`) |

## Localization

The nine static pages are localized into **Spanish (`/es`), French (`/fr`),
German (`/de`), Japanese (`/ja`), Chinese (`/zh`), and Korean (`/ko`)** alongside
the English originals (served at the root). English is the source of truth; the
translations are **generated**, not hand-written:

- `scripts/localize_static.py` holds the per-locale translation tables (chrome +
  page strings, including inline-JS UI strings) and emits `static/<locale>/*.html`
  from `static/*.html`. After **any** change to an English page, re-run
  `python scripts/localize_static.py` so all four languages stay in sync, and
  commit the regenerated files. Never edit `static/{es,fr,de}/*.html` by hand.
- Routing: a loop in `main.py` registers `/<locale>`, `/<locale>/reports`,
  `/<locale>/removals`, `/<locale>/catalog`, `/<locale>/mcp`, `/<locale>/methodology`, `/<locale>/schema`,
  `/<locale>/api-key`, `/<locale>/privacy` for each locale (plus a `/<locale>/portal` → `/<locale>/api-key`
  redirect), all through `_serve_page` (so each localized file gets its own recomputed
  per-page CSP hash). The JSON API (`/api/*`), Swagger (`/docs`) and operational
  endpoints stay locale-agnostic; localized pages call the same `/api/*`.
- The globe **language switcher** (formerly a cross-site link to
  kieranmaynard.com) now switches the transparency site's own language —
  English / Español / Français / Deutsch — pointing at the equivalent page in
  each locale. The switcher block is rebuilt by the generator, so it is
  consistent across every page and locale.

## CI

GitHub Actions runs `pyflakes`, `mypy` (config in `mypy.ini`, over
`main.py`/`seed.py`/`demo.py`/`conftest.py`/`mcp_server.py`), and `pytest
test_api.py test_mcp_server.py` on every pull request and push to `main`
(`ci.yml`). Keep all three green — the suite is hermetic (no Redis/server/MCP
SDK/`demo.db` needed; `conftest.py` builds a temp DB and `test_mcp_server.py`
drives the API in-process via `TestClient`). Run them locally before pushing
(`make lint typecheck test`).

`deploy.yml` builds + pushes the image and rolls a Cloud Run revision on push to
`main` via Workload Identity Federation, stamping the commit SHA as `APP_VERSION`.
It deploys with `--no-traffic`, smoke-tests the new revision's `/readyz`, then
promotes it with `update-traffic --to-latest`. Gated on the `GCP_PROJECT_ID` repo
variable, so it **skips** (not fails) until GCP is configured — see README →
"Continuous deployment". `.gcloudignore` keeps the Cloud Build upload lean.

## Data re-vendoring (automated)

The API serves a **frozen snapshot** of the data-collection pipeline that lives
in the sibling `dsa-transparency-data` repo (scrapers, raw archives, the
canonical extracted CSVs, the catalogue). The two vendored artifacts the image is
seeded from — `data/harmonised-reports.json` and `data/report-locations.csv` —
are kept in sync **automatically** rather than by hand:

- **`scripts/revendor_data.py`** does the mechanical half: regenerate the
  snapshot from the sibling repo's `harmonised-reports/extracted/` (via
  `seed_harmonised.write_snapshot`), copy `dsa_reports.csv` →
  `data/report-locations.csv` (header-validated), and print a Markdown summary
  that flags any extracted platform **not yet in `SLUG_META`** (those still seed
  under their raw slug, so the script suggests a paste-ready entry instead of
  guessing the display name/tier). `--check` dry-runs without writing.
- **`.github/workflows/revendor-data.yml`** runs it nightly / on
  `workflow_dispatch` / on a `data-updated` `repository_dispatch`, **validates**
  by reseeding + `pytest`, then opens/updates a single `auto/revendor-data` PR
  (body = the summary) only if something changed. A human still reviews it and
  finishes any `SLUG_META` naming — judgment stays with the human; the toil is
  automated.
- The data repo's **`.github/workflows/notify-revendor.yml`** pokes this workflow
  the moment its `main` changes (instant instead of waiting for nightly).

**Secrets (optional).** Both work with zero config (nightly schedule + anonymous
clone of the public data repo). To enable the instant path and let the auto-PR
trigger `ci.yml`, set a PAT: `REVENDOR_PAT` on this repo (used as the
create-pull-request token + private-repo clone) and `REVENDOR_DISPATCH_TOKEN` on
the data repo (scoped to dispatch this repo). Both jobs self-skip cleanly when
their secret is absent. Note: `schedule`/`workflow_dispatch`/`repository_dispatch`
only fire once the workflow is on `main`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# seed.py reads from the sibling repo — clone both into the same parent dir
python seed.py          # creates demo.db

uvicorn main:app --port 8000
```

Repos are expected as siblings:
```
parent/
  transparency-report-api/  ← this repo
  krMaynard.github.io/ ← source data lives at data/vlop-dsa.json
```

## Running the demo

```bash
python demo.py           # auto-advance
python demo.py --pause   # press Enter between steps (live demo mode)
```

## Auth

Two mechanisms, both presented as `X-API-Key` to the rest of the app:

- **Google sign-in (production).** The frontend uses Google Identity Services
  (FedCM in supporting browsers) to get an ID token and POSTs it to
  `/auth/google`. `_verify_id_token` validates it against `GOOGLE_CLIENT_ID`.
  Any verified Google account is **approved automatically on first sign-in**
  (no admin review); a login mints a first-party **session key** (`gs_…`) into
  `_key_store` (TTL `GOOGLE_SESSION_TTL`). Admins (`ADMIN_EMAILS`,
  comma-separated) keep a kill switch via `/admin/registrations/*` (revoke /
  restore). `_lookup_principal` re-checks the registration on every request, so
  an admin revoke kills live sessions at once. Durable account state lives in
  `_registrations` (Redis-backed when configured, else in-memory — same pattern
  as `_key_store`).
- **Demo keys (dev).** Hard-coded `momo`/`honggildong` + the open `/portal/register`.
  Gated by `ALLOW_DEMO_KEYS` (default on); set `ALLOW_DEMO_KEYS=0` in production.

Jobs are scoped per key — each principal only sees their own jobs (foreign IDs
return 404, not 403). `require_admin` gates the admin endpoints on the principal's
email being in `ADMIN_EMAILS`.

## Database schema

Seeded from `vlop-dsa.json` (compact interned format → star schema). Shared
dimension tables `services` (with `platform` = parent company), `categories`
(code + label), `sections`, `indicators`, `scopes`, `surfaces`, plus a `meta`
key/value table (`period`, `generated`). One **fact table per DSA report table**:

- **`t3_member_state_orders`** — Art. 9 & 10 orders, by category × scope
- **`t4_notices`** — Art. 16 notices, by category (+ Trusted-Flagger `tf_*`)
- **`t5_own_initiative_illegal`** / **`t6_own_initiative_tos`** — own-initiative actions, by category × 16 restriction types (t6 + surface)
- **`t7_appeals_recidivism`** / **`t8_automated_means`** — section × indicator × scope × surface → value
- **`t9_human_resources`** — section × indicator × scope → value
- **`t10_amar`** — Average Monthly Active Recipients, by scope
- **`t11_qualitative`** — free-text descriptions, by indicator (`value_text`)

Fact-row leading values are indices into the lookup arrays (= the dimension row
id), so seeding is positional. The DB is opened `mode=ro` as defence in depth.

**Dimension normalization** (`seed.normalize_dimensions`, run post-load by both
`build_db` and `build_harmonised_facts`, idempotent): the DSA template embeds an
aggregate **total** row next to its breakdown rows (AMAR's EU `TOTAL` beside the
per-member-state rows; the `All the entries` category beside per-category rows;
the `Total number` scope beside upheld/reversed outcomes), so a naive `SUM`
double-counts. The pass sets **`is_total`** on the `scopes`/`categories` rows
whose label is an aggregate (TOTAL/GESAMT/"All the entries"/…) and **deletes fact
rows** that reference mis-parsed junk labels (`[...]`, header cells, blanks,
numeric strays) leaked by some non-VLOP extracts. `compile_query` exposes
`scope_is_total`/`category_is_total` as filterable dimensions so the curated tabs
and the Explore "Rows" selector pick a single grain (totals only / breakdown
only) instead of summing a total together with its own parts.

**Cross-language canonical keys.** Non-VLOP reports are filed in any official EU
language, so the same template row arrives as different text (`Décisions
confirmées` / `Bestätigte Entscheidungen` / `Decisions upheld`). The seeder keeps
the original-language label for display (`name`) but stamps a language-neutral
**`key`** (canonical English) on each `sections`/`indicators`/`scopes` row from
the vendored `data/template-crosswalk.json` (built by
`scripts/build_template_crosswalk.py`, which learns the mapping by aligning
same-structure reports to an English reference and **drops anything ambiguous**).
`compile_query` exposes `section_key`/`indicator_key`/`scope_key` so a query can
group or filter across languages (e.g. the Appeals tab filters on `indicator_key`)
while the plain dimension still shows the filed text. (Greek extracts have a
column-shift in the source data, so most EL indicator/scope labels stay
un-normalized for now — correct-but-unmapped, never mis-mapped; category labels
aren't crosswalked yet.)

**Multi-tier reports.** The `reports` table (one row per submitted report, with a
`tier`) lets the same `t3`–`t11` schema hold more than the VLOP set. After the
VLOP load, `seed_harmonised.build_harmonised_facts()` appends the **non-VLOP
harmonised-template reports** (45 services / 46 reports — the 49 extracted minus
LinkedIn / Pinterest / Wikipedia, which are already VLOP services, with AboutYou's
second period attaching to its existing service): a new `reports` row
(tier `online-platform`/`hosting`/`intermediary`) + `services` row per platform,
with the shared dimensions interned/extended. So `POST /api/query` and
`/api/explore` span **both** VLOP and non-VLOP data, while the VLOP dashboard's
`GET /api/overview` stays scoped to `tier = 'vlop'` (it derives the VLOP service
set from vlop-tier facts) so its headline figures don't silently absorb them.

A standalone **`report_locations`** table (flat, not part of the star schema) is
also seeded — from `data/report-locations.csv` via `build_report_locations()` —
holding the non-VLOP DSA transparency-report catalogue (`platform`, `company`,
`category`, `confidence`, `harmonised_template`, `format_period`, `url_label`,
`url`, `archived`). `archived` is a GitHub URL to the report file(s) mirrored in
the sibling `dsa-transparency-data` repo (set in its catalogue by
`link_archives.py`) — surfaced as the catalogue page's "Archived" column. It
powers the public `GET /api/report-locations` endpoint and the dashboard's
"Where platforms publish their reports" panel.

## Query model

Requests are structured (see `QueryRequest`/`compile_query`/`TableSpec` in
`main.py`). A query **must name a `table`**; that table's `TableSpec` fixes the
FROM/joins and the registry of:

- **Dimensions** (text, `EQ`/`IN`): always `service_name`, `platform`; plus
  per-table `category_code`/`category_label`, `section`, `indicator`, `scope`,
  `surface`, or `qualitative_text` (t11); plus the derived `scope_is_total`/
  `category_is_total` grain flags and the language-neutral `section_key`/
  `indicator_key`/`scope_key` canonical labels.
- **Measures** (numeric, `EQ`/`IN`/`GT`/`GTE`/`LT`/`LTE`): per-table count
  columns (e.g. t4 `notices`/`tf_notices`/…, t7–t10 `value`). t11 has none.
- **Aggregates**: `SUM`/`COUNT`/`AVG`/`MIN`/`MAX` over a measure, with an alias.
- `group_by`, `sort`, `max_count`, optional `callback_url` (webhook). `GET /tables`
  lists the tables; `GET /fields?table=…` and `GET /schema/{table}` document a
  table's fields.

`compile_query` is the single trust boundary — it resolves `req.table` to a
`TableSpec` and validates every field/operation against that table's registry.
Never build SQL by interpolating user values (always bind with `?`).

**Composite (cross-table) queries**: instead of `table`, a request may carry
`legs` (2–4 named single-table sub-queries, each validated against its own
`TableSpec`; ≤2 on public `/api/explore` via `EXPLORE_MAX_LEGS`), `join_on`
(merge keys — must be a dimension of every leg's table; each leg is implicitly
grouped by them), `derived` (four-function arithmetic over `leg.alias` refs,
parsed by `_compile_expr` into SQL with `NULLIF` division — never interpolated),
and `having` (the condition grammar over output columns). `_compile_composite`
emits one statement: leg CTEs + a `spine` CTE (UNION of leg keys → full-outer
semantics, unmatched keys kept with NULLs) + LEFT JOINs + an outer
having/sort/limit. `compile_query` dispatches on the presence of `legs`, so
every endpoint (query/explore/ask) gets composites through the same boundary.

## Key design decisions

- **Structured params, not SQL**: the only way to query is the validated
  parameter model, compiled to one parameterised SELECT — no caller SQL runs.
- **NL→query via LLM, same trust boundary** (`POST /api/ask`): an LLM (Claude;
  `ANTHROPIC_MODEL`, default `claude-opus-4-8`) translates a question into the
  *structured* `QueryRequest` using JSON-schema structured outputs — never SQL —
  which then goes through the exact same `compile_query` validation as everything
  else. The model only proposes; `compile_query` disposes (bad field → `422`).
  `_translate_question` is the single, lazily-imported, monkeypatchable seam (tests
  never call the API); off unless `ANTHROPIC_API_KEY` is set; IP-rate-limited.
  Before changing the LLM call, confirm the current model ID + Messages API schema
  (use the `claude-api` skill) — never hardcode a model ID from memory.
- **Researcher portal** (`/portal` + `POST /portal/register`): a demo onboarding
  UI. Registration mints a key into the **issued-key store** (`_key_store`:
  Redis-backed when configured, else in-memory — shares `_redis` with the job
  store), with an expiry (`ISSUED_KEY_TTL`) and per-IP/email rate limiting
  (`_key_store.incr`). `require_api_key` accepts configured keys *or* issued ones
  (`_lookup_principal`); `DELETE /portal/key` self-revokes. Still no real auth —
  production would front it with SSO.
- **202 + polling** instead of blocking HTTP: lets long queries run without
  tying up connections or timing out at proxies.
- **Signed download URLs**: a done job exposes `download_urls` (json/csv) —
  capability links carrying an HMAC-SHA256 of `job_id:format:expires`.
  `GET /jobs/{id}/download` verifies the signature (before any store lookup, so
  job existence isn't leaked) instead of an API key, so the URL alone authorises
  the download (presigned-URL style). Set `DOWNLOAD_URL_SECRET` in production so
  links survive restarts and span workers.
- **In-memory job registry** (`_jobs` dict + `threading.Lock`): simple for a
  demo; restart clears all jobs. Production would need persistent storage.
- **`sqlite3.interrupt()`** on `DELETE /jobs/{id}` while running: aborts the
  in-flight query without parsing SQL.
- **100k row cap**: queries returning more rows fail with a helpful error
  asking the caller to add a `LIMIT`.
- **Per-key query rate limit**: `POST /query` is throttled per API key
  (`QUERY_RATE_MAX`/`QUERY_RATE_WINDOW`, default 60/60s) via `_key_store.incr` —
  the same counter primitive as portal registration. Over-limit → `429` +
  `Retry-After`, before a job is created.
- **Structured logging**: a dedicated `research_api` logger emits JSON lines
  (`JsonLogFormatter`, `LOG_FORMAT=json` default; `text` for humans). An HTTP
  middleware logs each request (method/path/status/`duration_ms`/`request_id`,
  echoed as `X-Request-ID`); the job runner logs `job_submitted`/`job_started`/
  `job_done`/`job_failed`. Pass fields via `extra={"data": {...}}`; never log keys.
- **Webhook callbacks**: an optional `callback_url` on `POST /query`. When the
  job reaches `done`/`failed`, `_dispatch_callback` POSTs the job object (with
  absolute links if `PUBLIC_BASE_URL` is set) to that URL on a **bounded callback
  thread pool** (`_callback_executor`, `CALLBACK_WORKERS`) — off the query
  workers — HMAC-signed (`X-Webhook-Signature`, same secret as download URLs),
  retried with backoff. SSRF-guarded: `_validate_callback_url` blocks non-http(s)
  and private/loopback/link-local/metadata targets, **unwrapping IPv4-mapped/6to4
  IPv6** so they can't smuggle a private v4; enforced at submit *and* before each
  send (narrows DNS rebinding — full closure needs network egress filtering);
  redirects aren't followed; the target must be **globally routable** (`not
  ip.is_global` is rejected, covering CGNAT and other non-private-but-non-public
  ranges). `CALLBACK_ALLOW_PRIVATE=1` bypasses for local dev.
- **Abuse hardening**: request bodies are capped via `Content-Length`
  (`MAX_BODY_BYTES`, default 1 MiB → `413`); query complexity is bounded in the
  Pydantic models (≤100 values per condition, ≤50 conditions per and/or/not
  clause, ≤50 fields/group_by/aggregates/sort entries) since `/api/explore`
  accepts the same model unauthenticated; CSV exports neutralise spreadsheet
  formula injection (`_csv_safe` prefixes text cells starting `=`/`+`/`-`/`@`
  with `'` — server-side and in the dashboard's `toCSV`); configured API keys
  are compared constant-time (`_configured_principal`).
- **Prometheus metrics** at `GET /metrics` (no auth): the same request middleware
  records `research_api_http_requests_total` + `_http_request_duration_seconds`,
  labelled by the **route template** (`/jobs/{job_id}`) to bound cardinality; the
  job runner tracks `research_api_jobs_in_flight`, `research_api_jobs_total{status}`, and
  `research_api_job_queue_depth` (inc'd on submit, dec'd when the worker picks the job
  up — no reliance on `ThreadPoolExecutor` internals).
- **Swagger UI** at `/docs` works out of the box — click Authorize and paste
  a key.
- **Browser hardening**: every response gets a set of hardening headers from the
  request middleware — `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer` (so the HMAC in a signed download URL never leaks via `Referer`),
  `X-Frame-Options: DENY`, `Permissions-Policy` (geolocation/camera/mic/payment
  off), and `Strict-Transport-Security` (HSTS). Every served HTML page gets a per-page
  **Content-Security-Policy** (`_serve_page`/`_page_csp`) — `script-src 'self'` +
  the page's inline-`<script>` **sha256 hash** (computed from the file, never
  stale); the dashboard needs no third-party script origin because **Chart.js is
  vendored same-origin** (`static/vendor/chart.umd.js`, served by the
  `/static/vendor/{filename}` route with a name allowlist + immutable caching),
  and the api-key page only allows `accounts.google.com` for Google sign-in. No
  `'unsafe-inline'` for scripts, `frame-ancestors 'none'`. DB values are
  HTML-escaped in the dashboard JS (`esc()`). If Chart.js is unavailable, the
  dashboard panels **fall back to data tables** instead of blank canvases
  (`chartReady()`/`miniTable()`).
- **Accessibility**: both HTML pages have a skip-link → `<main id="main">`
  landmark, a labelled `<nav>`, visible keyboard focus rings (`:focus-visible`),
  `role="alert"` live regions for errors, and `aria-busy`/loading states while
  data fetches. The chart `<canvas>` elements are `aria-hidden`; their data is
  exposed to assistive tech via an always-rendered table that is `.sr-only`
  (visually hidden) when the chart draws — so screen-reader users get the
  numbers either way. Honours `prefers-reduced-motion`.

## Code Review Workflow

**After opening or updating a pull request, always self-review the diff** and
post a comment summarising what you checked and any issues found + fixed (run
the tests/linters and note the result). Never leave a PR without a self-review.

Whenever a pull request is created or updated, **always check for Gemini
code-review comments** (`gemini-code-assist[bot]`) using the GitHub MCP tools:

1. Call `pull_request_read` with `method=get_reviews` to find the Gemini review summary.
2. Call `pull_request_read` with `method=get_review_comments` to get inline thread details.
3. Verify each finding against the actual source files before acting.
4. Apply confirmed fixes, commit, and push on the same branch.
5. **Always reply to every Gemini (GCA) comment** with `add_reply_to_pull_request_comment` —
   either describing the fix applied, or explaining why the suggestion isn't
   being taken. Never leave a GCA review comment unacknowledged.

## Endpoints

Combined-site layout: the **dashboard is served at `/`** and the JSON API lives
under **`/api/*`** on the same origin (no CORS). Operational endpoints
(`/healthz`, `/readyz`, `/metrics`, `/version`) and the `/schema` + `/api-key` pages stay at the
root. The API endpoints are registered on an `APIRouter` included with
`prefix=API_PREFIX` (`/api`); link builders (`status_url`/`result_url`/signed
`download_urls`/`Location`) are prefixed via `API_PREFIX`.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/` | — | Public VLOP transparency dashboard (web UI) |
| GET | `/api/overview` | — | Public headline aggregates powering the dashboard |
| GET | `/api/report-locations` | — | Public: non-VLOP DSA report-locations catalogue (filters: `category`/`confidence`/`harmonised_template`/`q`; `format=json\|csv`) — memoised, read-only |
| GET | `/api/explore/options` | — | Public: tables + dimensions/measures for the query builder |
| POST | `/api/explore` | — | Public: run a bounded structured query inline (row-capped, IP-rate-limited, ≤`EXPLORE_MAX_LEGS` composite legs) |
| POST | `/api/ask` | key | NL→query via an LLM (Claude) → structured `QueryRequest` → `compile_query`; requires an API key, IP-rate-limited; off unless `ANTHROPIC_API_KEY` set |
| GET | `/api` | — | API service info |
| GET | `/catalog` | — | Public report-locations catalogue page (web UI over `GET /api/report-locations`) |
| GET | `/mcp` | — | Public MCP-server info page (web UI; documents `mcp_server.py`) |
| GET | `/methodology` | — | Public methodology page (web UI; how the dataset is sourced/processed/cited) |
| GET | `/schema` | — | Public dataset-schema browser (web UI; no sign-in) |
| GET | `/api-key` | — | API-key sign-in page (web UI: sign in → key). `/portal` 308-redirects here |
| POST | `/api/auth/google` | — | Verify a Google ID token → session key (any verified account) |
| POST | `/api/portal/register` | — | Demo: issue a key without auth (`ALLOW_DEMO_KEYS`) |
| DELETE | `/api/portal/key` | key | Revoke your own session / portal-issued key |
| GET | `/api/admin/registrations` | admin | List researcher registrations (`?status=`) |
| POST | `/api/admin/registrations/{email}/approve` | admin | Restore a revoked account |
| POST | `/api/admin/registrations/{email}/revoke` | admin | Revoke an account |
| GET | `/api/tables` | — | Public: list the DSA report tables + dataset period |
| GET | `/api/fields?table=…` | — | Public: fields + operations for a table (no arg → table overview) |
| GET | `/api/schema/{table}` | — | Public: field registry for a report table |
| POST | `/api/query` | key | Submit structured query — single-table or composite (optional `callback_url`) → 202 + job_id |
| GET | `/api/jobs` | key | List your jobs |
| GET | `/api/jobs/{id}` | key | Job status |
| GET | `/api/jobs/{id}/result?format=json\|csv` | key | Result (status=done only) |
| GET | `/api/jobs/{id}/download?format=…&expires=…&sig=…` | signed URL | Secure download, no key needed |
| DELETE | `/api/jobs/{id}` | key | Cancel or remove |
| GET | `/healthz` `/readyz` | — | Liveness / readiness probes (root) |
| GET | `/metrics` | — | Prometheus metrics |
| GET | `/version` | — | Deployed build (commit SHA via `APP_VERSION`); also the `X-Version` header |
