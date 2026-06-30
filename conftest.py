"""Pytest configuration — runs before any test file is imported.

Builds a small VLOP-DSA-shaped SQLite DB (via seed.build_db, the same code path
the real seed uses) and sets DB_PATH/API_KEYS_JSON env vars so main.py picks
them up at module-level import time, which happens after this file is loaded.
"""
import os
import tempfile

import seed

_tmp = tempfile.mkdtemp()
_DB = os.path.join(_tmp, "test.db")

# A tiny but representative slice of the vlop-dsa.json shape: 2 services across
# 2 platforms, a couple of categories/sections/indicators/scopes/surfaces, and a
# few fact rows per report table (chosen so aggregations have known totals).
_FIXTURE = {
    "meta": {"period": "2025-07-01/2025-12-31", "generated": "2026-05-13"},
    "services": ["YouTube", "Facebook"],
    "service_platforms": ["Google", "Meta"],
    "categories": ["TOTAL", "STATEMENT_CATEGORY_ILLEGAL_OR_HARMFUL_SPEECH"],
    "category_labels": {"TOTAL": "All the entries",
                        "STATEMENT_CATEGORY_ILLEGAL_OR_HARMFUL_SPEECH": "Illegal or harmful speech"},
    "sections": ["Internal complaints mechanism"],
    "indicators": ["Number of complaints submitted to the internal-complaints mechanism", "Summary"],
    "scopes": ["Total number", "Decisions upheld"],
    "surfaces": ["All", "Ads"],
    # t3: [svc, cat, scope, orders_to_act, items, orders_to_provide_info]
    "t3": [[0, 0, 0, 11, 22, 3], [1, 0, 0, 5, 6, 1]],
    # t4: [svc, cat, notices, tf_notices, items, tf_items, median, tf_median, act_law, tf_act_law, act_tos, tf_act_tos]
    "t4": [[0, 0, 100, 10, 200, 20, 5, None, 30, 3, 70, 7],
           [0, 1, 40, 4, 80, 8, 6, None, 10, 1, 30, 3],
           [1, 0, 50, 5, 90, 9, 4, None, 20, 2, 30, 3]],
    # t5: 18 cols [svc, cat, measures, automated, 7 vis_*, 3 monetary_*, service x2, account x2]
    "t5": [[0, 0, 9, 4, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]],
    # t6: t5 + surface_id
    "t6": [[0, 0, 9, 4, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]],
    # t7/t8: [svc, section, indicator, scope, value, surface]
    "t7": [[0, 0, 0, 0, 1000, 0], [1, 0, 0, 0, 500, 0]],
    "t8": [[0, 0, 0, 0, 7, 0]],
    # t9: [svc, section, indicator, scope, value]
    "t9": [[0, 0, 0, 0, 12]],
    # t10: [svc, scope, value]
    "t10": [[0, 0, 64767887], [1, 0, 50000000]],
    # t11: [svc, indicator, value_text] — the second row's leading "=" exercises
    # the CSV formula-injection escaping (test_csv_download_escapes_formula_cells).
    "t11": [[0, 1, "YouTube qualitative summary text."],
            [1, 1, '=HYPERLINK("http://evil.example/x")']],
}

seed.build_db(_FIXTURE, _DB)

_GR_FIXTURE = {
    "periods": ["January - June 2019", "July - December 2019"],
    "countries": ["US", "DE"],
    "country_names": ["United States", "Germany"],
    "requestors": ["Government Officials", "Court Order directed at Google"],
    "products": ["Web Search", "YouTube"],
    "reasons": ["Defamation", "National security"],
    "rows": [
        [0, 0, 0, 0, 0, 5, 100, 80, 5, 10, 3, 2, 0],
        [0, 1, 1, 1, 1, 3, 50, 40, 2, 5, 2, 1, 0],
        [1, 0, 0, 0, 0, 7, 120, 90, 8, 12, 5, 5, 0],
    ],
}
seed.build_gr_db(_GR_FIXTURE, _DB)

# A small slice of the Apple Transparency dataset (apple-transparency.json shape).
_APPLE_MEASURES = [
    "requests_received", "items_specified", "requests_data_provided",
    "pct_data_provided", "requests_challenged_rejected", "requests_no_data",
    "content_provided", "noncontent_provided", "accounts_preserved",
    "accounts_restricted", "accounts_deleted", "requests_app_removed",
    "apps_removed", "appeals_received", "appeals_granted", "apps_reinstated",
]
_APPLE_FIXTURE = {
    "measures": _APPLE_MEASURES,
    "periods": ["2024 H1", "2024 H2"],
    "countries": ["Germany", "United States of America"],
    "request_types": ["device", "account"],
    # [period, country, request_type] + 16 measures (order = _APPLE_MEASURES).
    # device populates received/specified/data_provided/pct; account adds
    # content/non-content; all other measures stay NULL.
    "rows": [
        [0, 1, 0, 12043, 42747, 10377, 86.0] + [None] * 12,
        [0, 0, 0, 200, 300, 150, 75.0] + [None] * 12,
        # account US 2024 H2: received, specified, (no data_provided), pct,
        # challenged=100, (no no_data), content=4000, noncontent=1000, rest NULL.
        [1, 1, 1, 5000, 9000, None, 90.0, 100, None, 4000, 1000] + [None] * 8,
    ],
    # [period, country, ns_type, req_low, req_high, acc_low, acc_high]
    "ns_rows": [
        [0, 1, "National Security", 0, 249, 0, 249],
        [1, 1, "FISA Content", 250, 499, 250, 499],
    ],
}
seed.build_apple_db(_APPLE_FIXTURE, _DB)

# A small slice of the GitHub Transparency dataset (github-transparency.json shape).
# [year, period, dataset, government, iso2, category, metric, count_low, count_high]
_GITHUB_FIXTURE = {
    "columns": ["year", "period", "dataset", "government", "iso2", "category",
                "metric", "count_low", "count_high"],
    "rows": [
        [2025, "", "government_takedowns_received", "Brazil", "BR", "", "received", 4, 4],
        [2025, "", "user_info_requests", "", "", "criminal court order", "received", 115, 115],
        [2025, "", "user_info_requests", "", "", "criminal court order", "disclosed", 82, 82],
        # national_security: a banded range (count_low != count_high).
        [2025, "Jul-Dec", "national_security", "", "", "Affected accounts", "count", 1000, 1249],
    ],
}
seed.build_github_db(_GITHUB_FIXTURE, _DB)

# A small slice of the Snap Transparency dataset (snap-transparency.json shape).
# [period, section, category, sub_category_1, sub_category_2, metric, value]
_SNAP_FIXTURE = {
    "columns": ["period", "section", "category", "sub_category_1",
                "sub_category_2", "metric", "value"],
    "rows": [
        ["2024-H1", "Ads Moderation", "Global", "", "", "total_ads_removed", 10711],
        ["2024-H1", "Overview of Our T&S Enforcements", "Country", "Afghanistan",
         "Drugs", "total_enforcements", 27],
        # a median metric — must not be summed
        ["2024-H1", "Overview of Our T&S Enforcements", "Country", "Afghanistan",
         "Child Sexual Exploitation", "median_turnaround_time_minutes", 51.68],
        ["2024-H2", "Governmental Content & Account Removal Requests", "Global",
         "", "", "total_requests", 42],
    ],
}
seed.build_snap_db(_SNAP_FIXTURE, _DB)

# A small slice of the non-VLOP report-locations catalogue (report-locations.csv).
_RL_FIXTURE = [
    # Reddit deliberately omits the optional columns (company / harmonised_template /
    # format_period / url_label) so the suite exercises NULL handling in the API
    # JSON projection and the CSV export.
    {"platform": "Reddit", "category": "Social, messaging, community & video",
     "confidence": "likely", "url": "https://support.reddithelp.com/hc/en-us/articles/dsa"},
    {"platform": "Discord", "company": "Discord Netherlands B.V.", "category": "Social, messaging, community & video",
     "confidence": "verified", "harmonised_template": "yes", "format_period": "ZIP (template); 2024 & 2025",
     "url_label": "Hub", "url": "https://discord.com/safety-transparency",
     "archived": "https://github.com/krMaynard/dsa-transparency-data/tree/main/pdf-reports/discord"},
    {"platform": "Vinted", "company": "Vinted UAB", "category": "E-commerce marketplaces & retail",
     "confidence": "verified", "harmonised_template": "yes", "format_period": "XLSX; 2024 & 2025",
     "url_label": "Safety hub", "url": "https://www.vinted.com/safety"},
]
seed.build_report_locations(_RL_FIXTURE, _DB)

# A tiny slice of the NY Social Media ToS catalogue: one publicly-archived filing
# and one login-gated one, so the access facet + archived-link rendering are both
# exercised.
_NY_TOS_FIXTURE = [
    {"company": "Snap Inc", "platform": "", "period": "2025 Q3", "upload_date": "01-01-2026",
     "access": "public", "source_url": "https://ag.ny.gov/sites/default/files/social-media-policy-report/2025-q3-snap-inc-policy.pdf",
     "filename": "2025-q3-snap-inc.pdf",
     "archived": "https://github.com/krMaynard/dsa-transparency-data/blob/main/ny-tos-reports/pdfs/2025-q3-snap-inc.pdf",
     "sha256": "abc123", "bytes": "11222370"},
    {"company": "TikTok Inc", "platform": "", "period": "2025 Q4", "upload_date": "04-01-2026",
     "access": "auth-required", "source_url": "https://ag.ny.gov/system/files/webform/social_media_terms_of_service_re/106547/2025-q4-tiktok-inc-policy.pdf",
     "filename": "", "archived": "", "sha256": "", "bytes": ""},
]
seed.build_ny_tos_reports(_NY_TOS_FIXTURE, _DB)

os.environ.setdefault("DB_PATH", _DB)
os.environ.setdefault("API_KEYS_JSON", '{"momo":{"name":"momo"},"honggildong":{"name":"honggildong"}}')
# Google sign-in config for the auth tests (token verification is monkeypatched).
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")
# Enables POST /api/ask; the LLM translation call itself is monkeypatched in tests,
# so no real Anthropic request is ever made.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
# Don't let the rate limiters interfere with the HTTP tests (they share one
# TestClient IP / API key). The 429 paths are exercised with isolated stores.
os.environ.setdefault("PORTAL_REGISTER_MAX_PER_WINDOW", "10000")
os.environ.setdefault("QUERY_RATE_MAX_PER_WINDOW", "100000")
os.environ.setdefault("LOG_FORMAT", "text")  # readable pytest output
# Allow webhook callbacks to loopback so the end-to-end test can hit a local
# capture server. The SSRF guard itself is unit-tested with the flag off.
os.environ.setdefault("CALLBACK_ALLOW_PRIVATE", "1")
