"""Build demo.db from the aggregated EU DSA VLOP transparency dataset.

Default source: ../krMaynard.github.io/data/vlop-dsa.json
Override with --source <path> or the SEED_SOURCE_JSON env var.
Override output with --db <path> or the DB_PATH env var.

`vlop-dsa.json` is a compact interned format: shared lookup arrays (services,
service_platforms, categories, category_labels, sections, indicators, scopes,
surfaces) plus one fact array per DSA report table (t3–t11). Each fact row is a
list whose leading values are indices into the lookup arrays (= the row id in
the corresponding dimension table) and whose remaining values are the reported
measures. We expand it into a star schema: dimension tables + one fact table per
report table, queried independently via the API's `table` selector.
"""
import argparse
import json
import os
import sqlite3
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))

_DEFAULT_SOURCE = os.getenv(
    "SEED_SOURCE_JSON",
    os.path.normpath(os.path.join(HERE, "..", "krMaynard.github.io", "data", "vlop-dsa.json")),
)
_DEFAULT_GR_SOURCE = os.getenv(
    "SEED_GR_SOURCE_JSON",
    os.path.normpath(
        os.path.join(HERE, "..", "krMaynard.github.io", "data", "google-government-removals.json")
    ),
)
_DEFAULT_DB = os.getenv("DB_PATH", os.path.join(HERE, "demo.db"))

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

-- Shared dimension tables. id = position in the source lookup array.
CREATE TABLE services   (id INTEGER PRIMARY KEY, name TEXT NOT NULL, platform TEXT NOT NULL);
CREATE TABLE categories (id INTEGER PRIMARY KEY, code TEXT NOT NULL, label TEXT NOT NULL);
CREATE TABLE sections   (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE indicators (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE scopes     (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE surfaces   (id INTEGER PRIMARY KEY, name TEXT NOT NULL);

-- Report dimension: one row per submitted transparency report (one dataset = one report).
-- Supports multi-period ingestion when non-VLOP annual reports are added.
-- tier: vlop | vlose | vlop-vlose | online-platform | hosting | intermediary
CREATE TABLE reports (
    id           INTEGER PRIMARY KEY,
    period       TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    tier         TEXT NOT NULL DEFAULT 'vlop',
    generated    TEXT
);
CREATE INDEX idx_reports_period ON reports(period_start, period_end);

-- Table 3 — Member-State orders (Art. 9 & 10), by category × scope.
CREATE TABLE t3_member_state_orders (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL, scope_id INTEGER NOT NULL,
    orders_to_act INTEGER, items INTEGER, orders_to_provide_info INTEGER
);

-- Table 4 — Notices (Art. 16), by category, with Trusted-Flagger breakdowns.
CREATE TABLE t4_notices (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL, category_id INTEGER NOT NULL,
    notices INTEGER, tf_notices INTEGER, items INTEGER, tf_items INTEGER,
    median_time INTEGER, tf_median_time INTEGER,
    actions_law INTEGER, tf_actions_law INTEGER, actions_tos INTEGER, tf_actions_tos INTEGER
);

-- Table 5 — Own-initiative actions on illegal content, by category × restriction type.
CREATE TABLE t5_own_initiative_illegal (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL, category_id INTEGER NOT NULL,
    measures INTEGER, automated INTEGER,
    vis_removal INTEGER, vis_disable INTEGER, vis_demoted INTEGER, vis_age_restricted INTEGER,
    vis_interaction_restricted INTEGER, vis_labelled INTEGER, vis_other INTEGER,
    monetary_suspension INTEGER, monetary_termination INTEGER, monetary_other INTEGER,
    service_suspension INTEGER, service_termination INTEGER,
    account_suspension INTEGER, account_termination INTEGER
);

-- Table 6 — Own-initiative actions on ToS violations (same shape as t5, + surface).
CREATE TABLE t6_own_initiative_tos (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL, category_id INTEGER NOT NULL,
    measures INTEGER, automated INTEGER,
    vis_removal INTEGER, vis_disable INTEGER, vis_demoted INTEGER, vis_age_restricted INTEGER,
    vis_interaction_restricted INTEGER, vis_labelled INTEGER, vis_other INTEGER,
    monetary_suspension INTEGER, monetary_termination INTEGER, monetary_other INTEGER,
    service_suspension INTEGER, service_termination INTEGER,
    account_suspension INTEGER, account_termination INTEGER,
    surface_id INTEGER NOT NULL
);

-- Table 7 — Appeals & recidivism, by section × indicator × scope × surface.
CREATE TABLE t7_appeals_recidivism (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL, indicator_id INTEGER NOT NULL,
    scope_id INTEGER NOT NULL, value INTEGER, surface_id INTEGER NOT NULL
);

-- Table 8 — Use of automated means, by section × indicator × scope × surface.
CREATE TABLE t8_automated_means (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL, indicator_id INTEGER NOT NULL,
    scope_id INTEGER NOT NULL, value INTEGER, surface_id INTEGER NOT NULL
);

-- Table 9 — Human resources for content moderation, by section × indicator × scope.
CREATE TABLE t9_human_resources (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL, indicator_id INTEGER NOT NULL,
    scope_id INTEGER NOT NULL, value INTEGER
);

-- Table 10 — Average Monthly Active Recipients (AMAR), by scope.
CREATE TABLE t10_amar (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
    scope_id INTEGER NOT NULL, value INTEGER
);

-- Table 11 — Qualitative description (free text), by indicator.
CREATE TABLE t11_qualitative (
    report_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
    indicator_id INTEGER NOT NULL, value_text TEXT
);

CREATE INDEX idx_t3_service  ON t3_member_state_orders(service_id);
CREATE INDEX idx_t4_service  ON t4_notices(service_id);
CREATE INDEX idx_t5_service  ON t5_own_initiative_illegal(service_id);
CREATE INDEX idx_t6_service  ON t6_own_initiative_tos(service_id);
CREATE INDEX idx_t7_service  ON t7_appeals_recidivism(service_id);
CREATE INDEX idx_t8_service  ON t8_automated_means(service_id);
CREATE INDEX idx_t9_service  ON t9_human_resources(service_id);
CREATE INDEX idx_t10_service ON t10_amar(service_id);
CREATE INDEX idx_t11_service ON t11_qualitative(service_id);
CREATE INDEX idx_t3_report   ON t3_member_state_orders(report_id);
CREATE INDEX idx_t4_report   ON t4_notices(report_id);
CREATE INDEX idx_t5_report   ON t5_own_initiative_illegal(report_id);
CREATE INDEX idx_t6_report   ON t6_own_initiative_tos(report_id);
CREATE INDEX idx_t7_report   ON t7_appeals_recidivism(report_id);
CREATE INDEX idx_t8_report   ON t8_automated_means(report_id);
CREATE INDEX idx_t9_report   ON t9_human_resources(report_id);
CREATE INDEX idx_t10_report  ON t10_amar(report_id);
CREATE INDEX idx_t11_report  ON t11_qualitative(report_id);

-- Google Government Removal Requests (2019–2025)
CREATE TABLE gr_periods    (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE gr_countries  (id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE gr_requestors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE gr_products   (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE gr_reasons    (id INTEGER PRIMARY KEY, name TEXT NOT NULL);

CREATE TABLE gr_removals (
    period_id       INTEGER NOT NULL,
    country_id      INTEGER NOT NULL,
    requestor_id    INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    reason_id       INTEGER NOT NULL,
    num_requests    INTEGER,
    items_requested INTEGER,
    removed_legal   INTEGER,
    removed_policy  INTEGER,
    not_found       INTEGER,
    not_enough_info INTEGER,
    no_action       INTEGER,
    already_removed INTEGER
);

CREATE INDEX idx_gr_period  ON gr_removals(period_id);
CREATE INDEX idx_gr_country ON gr_removals(country_id);

-- SoR Comparison: self-reported DSA report figures vs EUDSATDB Statement of Reasons aggregates
CREATE TABLE sor_comparison (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    service_name    TEXT NOT NULL,
    category_code   TEXT NOT NULL,
    category_label  TEXT NOT NULL,
    rep_notices     INTEGER,
    rep_tf_notices  INTEGER,
    rep_own_illegal INTEGER,
    rep_own_tos     INTEGER,
    sor_notices     INTEGER,
    sor_tf_notices  INTEGER,
    sor_own_illegal INTEGER,
    sor_own_tos     INTEGER,
    delta_notices     REAL,
    delta_tf_notices  REAL,
    delta_own_illegal REAL,
    delta_own_tos     REAL,
    flag_notices     TEXT,
    flag_tf_notices  TEXT,
    flag_own_illegal TEXT,
    flag_own_tos     TEXT,
    worst_flag       TEXT,
    is_synthetic     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_sor_service  ON sor_comparison(service_name);
CREATE INDEX idx_sor_worst    ON sor_comparison(worst_flag);

-- VLOP/VLOSE platform registry (catalogue of designated platforms + transparency report links)
CREATE TABLE vlop_registry (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name          TEXT NOT NULL,
    platform              TEXT NOT NULL,
    tier                  TEXT NOT NULL,
    period_start          TEXT,
    period_end            TEXT,
    transparency_page_url TEXT,
    report_url            TEXT,
    notes                 TEXT
);
"""

# fact table name → (number of columns, source JSON key)
_FACT_TABLES = {
    "t3_member_state_orders": (6, "t3"),
    "t4_notices": (12, "t4"),
    "t5_own_initiative_illegal": (18, "t5"),
    "t6_own_initiative_tos": (19, "t6"),
    "t7_appeals_recidivism": (6, "t7"),
    "t8_automated_means": (6, "t8"),
    "t9_human_resources": (5, "t9"),
    "t10_amar": (3, "t10"),
    "t11_qualitative": (3, "t11"),
}


def build_db(data: dict[str, Any], db_path: str) -> dict[str, int]:
    """Build the VLOP star schema at db_path from a parsed vlop-dsa.json dict.

    Returns a {table: row_count} summary. Rows are inserted positionally, so the
    leading lookup indices in each fact row land directly in the *_id columns.
    """
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)

        services = data["services"]
        platforms = data["service_platforms"]
        conn.executemany(
            "INSERT INTO services (id, name, platform) VALUES (?, ?, ?)",
            [(i, services[i], platforms[i]) for i in range(len(services))],
        )

        categories = data["categories"]
        labels = data.get("category_labels", {})
        conn.executemany(
            "INSERT INTO categories (id, code, label) VALUES (?, ?, ?)",
            [(i, code, labels.get(code, code)) for i, code in enumerate(categories)],
        )

        for table, key in (("sections", "sections"), ("indicators", "indicators"),
                           ("scopes", "scopes"), ("surfaces", "surfaces")):
            conn.executemany(
                f"INSERT INTO {table} (id, name) VALUES (?, ?)",
                [(i, name) for i, name in enumerate(data[key])],
            )

        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in data.get("meta", {}).items()],
        )

        meta = data.get("meta", {})
        period = meta.get("period", "/")
        period_start, _, period_end = period.partition("/")
        tier = meta.get("tier", "vlop")
        generated = meta.get("generated")
        conn.execute(
            "INSERT INTO reports (id, period, period_start, period_end, tier, generated) VALUES (0,?,?,?,?,?)",
            (period, period_start.strip(), period_end.strip(), tier, generated),
        )

        summary: dict[str, int] = {}
        for table, (ncols, key) in _FACT_TABLES.items():
            rows = data.get(key, [])
            # Prepend report_id=0 to each row (ncols describes the source JSON width).
            placeholders = ", ".join(["?"] * (ncols + 1))
            conn.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",
                [[0] + list(row) for row in rows],
            )
            summary[table] = len(rows)

        conn.commit()
        return summary
    finally:
        conn.close()


def build_gr_db(data: dict[str, Any], db_path: str) -> int:
    """Populate Google Government Removal tables in an existing DB at db_path.

    The DB must already contain the gr_* tables (created by SCHEMA above, i.e.
    build_db() must have been called first). Returns the number of fact rows inserted.
    """
    countries = data["countries"]
    country_names = data["country_names"]
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO gr_periods (id, name) VALUES (?, ?)",
                list(enumerate(data["periods"])),
            )
            conn.executemany(
                "INSERT INTO gr_countries (id, code, name) VALUES (?, ?, ?)",
                [(i, code, name) for i, (code, name) in enumerate(zip(countries, country_names))],
            )
            conn.executemany(
                "INSERT INTO gr_requestors (id, name) VALUES (?, ?)",
                list(enumerate(data["requestors"])),
            )
            conn.executemany(
                "INSERT INTO gr_products (id, name) VALUES (?, ?)",
                list(enumerate(data["products"])),
            )
            conn.executemany(
                "INSERT INTO gr_reasons (id, name) VALUES (?, ?)",
                list(enumerate(data["reasons"])),
            )
            rows = data["rows"]
            conn.executemany(
                "INSERT INTO gr_removals ("
                "period_id, country_id, requestor_id, product_id, reason_id, "
                "num_requests, items_requested, removed_legal, removed_policy, "
                "not_found, not_enough_info, no_action, already_removed"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)
    finally:
        conn.close()


_DEFAULT_SOR_SOURCE = os.getenv(
    "SEED_SOR_SOURCE_JSON",
    os.path.normpath(os.path.join(HERE, "data", "sor-comparison.json")),
)
_DEFAULT_REGISTRY_SOURCE = os.getenv(
    "SEED_REGISTRY_SOURCE_JSON",
    os.path.normpath(os.path.join(HERE, "data", "registry.json")),
)


def build_sor_db(data: dict[str, Any], db_path: str) -> int:
    """Populate sor_comparison in an existing DB at db_path.

    build_db() must have been called first (SCHEMA creates the table).
    Returns the number of rows inserted.
    """
    period = data.get("period", "")
    is_synthetic = 1 if data.get("_fixture") else 0
    rows = data.get("rows", [])
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO sor_comparison ("
                "period, service_name, category_code, category_label, "
                "rep_notices, rep_tf_notices, rep_own_illegal, rep_own_tos, "
                "sor_notices, sor_tf_notices, sor_own_illegal, sor_own_tos, "
                "delta_notices, delta_tf_notices, delta_own_illegal, delta_own_tos, "
                "flag_notices, flag_tf_notices, flag_own_illegal, flag_own_tos, "
                "worst_flag, is_synthetic"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        period,
                        r["service_name"], r["category_code"], r["category_label"],
                        r.get("rep_notices"), r.get("rep_tf_notices"),
                        r.get("rep_own_illegal"), r.get("rep_own_tos"),
                        r.get("sor_notices"), r.get("sor_tf_notices"),
                        r.get("sor_own_illegal"), r.get("sor_own_tos"),
                        r.get("delta_notices"), r.get("delta_tf_notices"),
                        r.get("delta_own_illegal"), r.get("delta_own_tos"),
                        r.get("flag_notices"), r.get("flag_tf_notices"),
                        r.get("flag_own_illegal"), r.get("flag_own_tos"),
                        r.get("worst_flag"), is_synthetic,
                    )
                    for r in rows
                ],
            )
        return len(rows)
    finally:
        conn.close()


def build_registry_db(data: list[dict[str, Any]], db_path: str) -> int:
    """Populate vlop_registry in an existing DB at db_path.

    build_db() must have been called first. Returns the number of rows inserted.
    """
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO vlop_registry ("
                "service_name, platform, tier, period_start, period_end, "
                "transparency_page_url, report_url, notes"
                ") VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        r["service_name"], r["platform"], r["tier"],
                        r.get("period_start"), r.get("period_end"),
                        r.get("transparency_page_url"), r.get("report_url"),
                        r.get("notes"),
                    )
                    for r in data
                ],
            )
        return len(data)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo.db from the VLOP DSA dataset.")
    parser.add_argument("--source", default=_DEFAULT_SOURCE, help="Path to vlop-dsa.json")
    parser.add_argument("--gr-source", default=_DEFAULT_GR_SOURCE,
                        help="Path to google-government-removals.json")
    parser.add_argument("--sor-source", default=_DEFAULT_SOR_SOURCE,
                        help="Path to sor-comparison.json")
    parser.add_argument("--registry-source", default=_DEFAULT_REGISTRY_SOURCE,
                        help="Path to registry.json")
    parser.add_argument("--db", default=_DEFAULT_DB, help="Output SQLite database path")
    args = parser.parse_args()

    with open(args.source, "r", encoding="utf-8") as f:
        data = json.load(f)

    summary = build_db(data, args.db)
    period = data.get("meta", {}).get("period", "?")
    total = sum(summary.values())
    print(
        f"Seeded {args.db}: {total} fact rows across {len(_FACT_TABLES)} report tables "
        f"for {len(data['services'])} services (period {period})."
    )
    for table, n in summary.items():
        print(f"  {table}: {n}")

    if os.path.isfile(args.gr_source):
        with open(args.gr_source, "r", encoding="utf-8") as f:
            gr_data = json.load(f)
        gr_rows = build_gr_db(gr_data, args.db)
        print(
            f"  gr_removals: {gr_rows} rows across "
            f"{len(gr_data['periods'])} periods, "
            f"{len(gr_data['countries'])} countries"
        )
    else:
        print(f"  (skipping Google removals — not found: {args.gr_source})")

    if os.path.isfile(args.sor_source):
        with open(args.sor_source, "r", encoding="utf-8") as f:
            sor_data = json.load(f)
        sor_rows = build_sor_db(sor_data, args.db)
        synthetic = " (synthetic fixture)" if sor_data.get("_fixture") else ""
        print(f"  sor_comparison: {sor_rows} rows{synthetic}")
    else:
        print(f"  (skipping SoR comparison — not found: {args.sor_source})")

    if os.path.isfile(args.registry_source):
        with open(args.registry_source, "r", encoding="utf-8") as f:
            reg_data = json.load(f)
        reg_rows = build_registry_db(reg_data, args.db)
        print(f"  vlop_registry: {reg_rows} platform entries")
    else:
        print(f"  (skipping VLOP registry — not found: {args.registry_source})")


if __name__ == "__main__":
    main()
