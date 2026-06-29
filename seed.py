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
# Vendored in-repo catalogue (one row per non-VLOP report URL).
_DEFAULT_RL_SOURCE = os.getenv(
    "SEED_REPORT_LOCATIONS_CSV", os.path.join(HERE, "data", "report-locations.csv")
)


def _category_label(code: str, labels: dict[str, str] | None) -> str:
    """Human label for a category code.

    Use the dataset's explicit label when present; otherwise normalize the raw
    code (e.g. ``KEYWORD_OTHER_..._OR_DATA_VIOLATION``) into a readable label
    instead of surfacing the SCREAMING_SNAKE_CASE code in the UI — strip the
    namespace prefix, drop underscores, and sentence-case it.
    """
    explicit = labels.get(code) if labels else None
    if explicit:
        return explicit
    s = code
    for prefix in ("STATEMENT_CATEGORY_", "KEYWORD_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.replace("_", " ").strip()
    return s.capitalize() if s else code


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

-- Shared dimension tables. id = position in the source lookup array.
CREATE TABLE services   (id INTEGER PRIMARY KEY, name TEXT NOT NULL, platform TEXT NOT NULL);
CREATE TABLE categories (id INTEGER PRIMARY KEY, code TEXT NOT NULL, label TEXT NOT NULL, is_total INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sections   (id INTEGER PRIMARY KEY, name TEXT NOT NULL, key TEXT NOT NULL DEFAULT '');
CREATE TABLE indicators (id INTEGER PRIMARY KEY, name TEXT NOT NULL, key TEXT NOT NULL DEFAULT '');
CREATE TABLE scopes     (id INTEGER PRIMARY KEY, name TEXT NOT NULL, is_total INTEGER NOT NULL DEFAULT 0, key TEXT NOT NULL DEFAULT '');
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

-- Google Government Removal Requests (2011–2025)
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

-- Non-VLOP DSA report-location catalogue: where other online platforms publish
-- their Art. 15/24 transparency reports. One row per report URL.
CREATE TABLE report_locations (
    id                  INTEGER PRIMARY KEY,
    platform            TEXT NOT NULL,
    company             TEXT,
    category            TEXT NOT NULL,
    confidence          TEXT NOT NULL,
    harmonised_template TEXT,
    format_period       TEXT,
    url_label           TEXT,
    url                 TEXT NOT NULL,
    archived            TEXT
);
CREATE INDEX idx_rl_category   ON report_locations(category);
CREATE INDEX idx_rl_confidence ON report_locations(confidence);
"""

_RL_COLUMNS = ("platform", "company", "category", "confidence",
               "harmonised_template", "format_period", "url_label", "url", "archived")

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


# ── Dimension normalization ──────────────────────────────────────────────────
# The DSA template embeds an aggregate "total" row alongside the breakdown rows
# (AMAR's EU TOTAL next to per-member-state rows; the "All the entries" category
# next to per-category rows; the "Total number" scope next to the upheld/reversed
# outcomes). Summing a measure across those rows double-counts. We flag the
# aggregate rows (is_total) so queries can pick a single grain, and we drop the
# mis-parsed header/placeholder cells that some non-VLOP extracts leak in as data.

# Labels (stripped, casefolded) that denote an aggregate/total row.
_TOTAL_LABELS = {
    "total", "totals", "total number", "all the entries", "all entries",
    # "total" in the official EU languages (reports are filed in any of them).
    "gesamt", "gesamtzahl", "nombre total", "número total", "numero total",
    "totale", "totaal", "ogółem", "totalt", "yhteensä", "celkem", "σύνολο",
    "összesen", "общо", "celkovo", "spolu", "iš viso", "kopā", "kokku",
    "skupaj", "ukupno", "iomlán", "i alt", "nifer cyfan", "iomlán líon",
}
# Labels that are mis-parsed header/placeholder cells, not real dimension values.
_JUNK_LABELS = {
    "", "scope", "category", "champ d’application", "champ d'application",
    "geltungsbereich", "categoría", "catégorie", "kategorie", "[...]", "...",
    "n/a", "na", "-",
}
# Fact tables by the dimension FK columns they carry (for junk-row deletion).
_SCOPE_FACTS = ("t3_member_state_orders", "t7_appeals_recidivism",
                "t8_automated_means", "t9_human_resources", "t10_amar")
_CATEGORY_FACTS = ("t3_member_state_orders", "t4_notices",
                   "t5_own_initiative_illegal", "t6_own_initiative_tos")


_CROSSWALK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "template-crosswalk.json")
# {dimension: {original-language label: canonical English label}} — see
# scripts/build_template_crosswalk.py. Maps DSA harmonised-template rows filed in
# other EU languages onto their canonical English term so the same logical
# section/indicator/scope can be grouped across languages (the `key` column),
# while the original-language label is preserved for display (the `name` column).
_CROSSWALK: dict[str, dict[str, str]] | None = None


def _crosswalk() -> dict[str, dict[str, str]]:
    global _CROSSWALK
    if _CROSSWALK is None:
        try:
            with open(_CROSSWALK_PATH, encoding="utf-8") as f:
                _CROSSWALK = json.load(f)
        except (OSError, ValueError):
            _CROSSWALK = {}
    return _CROSSWALK


def _is_total_label(label: str) -> bool:
    return (label or "").strip().casefold() in _TOTAL_LABELS


def _is_junk_label(label: str) -> bool:
    s = (label or "").strip()
    if not s or s.casefold() in _JUNK_LABELS:
        return True
    if any(ch.isalpha() for ch in s):
        return False
    # No letters: a mis-parsed cell. Drop pure-punctuation placeholders ("[...]",
    # "...", "-") and bare single numbers ("0", "168"), but KEEP meaningful numeric
    # labels that carry a separator — e.g. "9 & 10" (Art. 9 & 10), "1.1", "2024/25".
    if not any(ch.isalnum() for ch in s):
        return True
    return s.isdigit()


def normalize_dimensions(conn: sqlite3.Connection) -> dict[str, int]:
    """Post-load cleanup (idempotent). Flags aggregate 'total' scope/category
    rows via ``is_total`` and deletes fact rows that reference mis-parsed junk
    dimension labels. Safe to run repeatedly (e.g. after appending non-VLOP
    reports). Returns a small {what: count} summary."""
    flagged = 0
    for dim, col in (("scopes", "name"), ("categories", "label")):
        ids = [rid for rid, lab in conn.execute(f"SELECT id, {col} FROM {dim}")
               if _is_total_label(lab)]
        if ids:
            conn.executemany(f"UPDATE {dim} SET is_total = 1 WHERE id = ?",
                             [(i,) for i in ids])
            flagged += len(ids)

    # Stamp the language-neutral canonical `key` on each template dimension row:
    # the crosswalk's English term where the label was filed in another language,
    # else the label itself (already English / unmapped). Lets queries group or
    # filter across languages while `name` keeps the original-language text.
    cw = _crosswalk()
    for dim, table in (("section", "sections"), ("indicator", "indicators"),
                       ("scope", "scopes")):
        m = cw.get(dim, {})
        conn.executemany(
            f"UPDATE {table} SET key = ? WHERE id = ?",
            [(m.get(name, name), rid)
             for rid, name in conn.execute(f"SELECT id, name FROM {table}")])

    deleted = 0
    junk_scopes = [rid for rid, n in conn.execute("SELECT id, name FROM scopes")
                   if _is_junk_label(n)]
    junk_cats = [rid for rid, l in conn.execute("SELECT id, label FROM categories")
                 if _is_junk_label(l)]
    if junk_scopes:
        ph = ",".join("?" * len(junk_scopes))
        for t in _SCOPE_FACTS:
            deleted += conn.execute(
                f"DELETE FROM {t} WHERE scope_id IN ({ph})", junk_scopes).rowcount
        # Drop the now-unreferenced junk dimension rows too, so they can't leak
        # into a distinct-scope listing.
        conn.execute(f"DELETE FROM scopes WHERE id IN ({ph})", junk_scopes)
    if junk_cats:
        ph = ",".join("?" * len(junk_cats))
        for t in _CATEGORY_FACTS:
            deleted += conn.execute(
                f"DELETE FROM {t} WHERE category_id IN ({ph})", junk_cats).rowcount
        conn.execute(f"DELETE FROM categories WHERE id IN ({ph})", junk_cats)
    conn.commit()
    return {"totals_flagged": flagged, "junk_facts_deleted": deleted}


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
            [(i, code, _category_label(code, labels)) for i, code in enumerate(categories)],
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
        normalize_dimensions(conn)
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


def build_report_locations(rows: list[dict[str, str]], db_path: str) -> int:
    """Populate the report_locations table in an existing DB at db_path.

    The DB must already contain the table (created by SCHEMA, i.e. build_db()
    must have run first). `rows` are dicts keyed by `_RL_COLUMNS`. Returns the
    number of rows inserted.
    """
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO report_locations "
                "(platform, company, category, confidence, harmonised_template, "
                "format_period, url_label, url, archived) VALUES (?,?,?,?,?,?,?,?,?)",
                [tuple((r.get(c) or None) for c in _RL_COLUMNS) for r in rows],
            )
        return len(rows)
    finally:
        conn.close()


def _load_report_locations_csv(path: str) -> list[dict[str, str]]:
    import csv as _csv
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(_csv.DictReader(f))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo.db from the VLOP DSA dataset.")
    parser.add_argument("--source", default=_DEFAULT_SOURCE, help="Path to vlop-dsa.json")
    parser.add_argument("--gr-source", default=_DEFAULT_GR_SOURCE,
                        help="Path to google-government-removals.json")
    parser.add_argument("--db", default=_DEFAULT_DB, help="Output SQLite database path")
    parser.add_argument("--report-locations", default=_DEFAULT_RL_SOURCE,
                        help="Path to report-locations.csv (non-VLOP catalogue)")
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

    if os.path.isfile(args.report_locations):
        rl_rows = _load_report_locations_csv(args.report_locations)
        n = build_report_locations(rl_rows, args.db)
        print(f"  report_locations: {n} rows from {os.path.basename(args.report_locations)}")
    else:
        print(f"  (skipping report locations — not found: {args.report_locations})")

    # Append the non-VLOP harmonised-template reports into the same star schema
    # (from the vendored snapshot, or the sibling repo's extracted CSVs in dev).
    import seed_harmonised
    h = seed_harmonised.build_harmonised_facts(args.db)
    if h:
        print(f"  harmonised reports: {h['services']} services, {h['reports']} reports, "
              f"{h['facts']} fact rows")
    else:
        print("  (skipping harmonised reports — no snapshot or extracted dir found)")


if __name__ == "__main__":
    main()
