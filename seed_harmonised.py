#!/usr/bin/env python3
"""Load the extracted non-VLOP harmonised-template reports into the star schema.

The DSA harmonised template is the same 11-section layout as the VLOP dataset, so
each platform's extracted per-section CSVs map straight onto the t3–t11 fact
tables. This appends them to an existing demo.db (after build_db has populated
the VLOP data + dimensions): one new `reports` row per platform (tier !=
'vlop'), one new `services` row, and fact rows that reuse/extend the shared
dimension tables.

Columns are addressed by fixed template position (not header text) so localised
reports (DE/FR/EL) load the same way. Dimension values are interned by string —
an existing category/scope/section/indicator/surface is reused, otherwise added.

Source: ../dsa-transparency-data/harmonised-reports/extracted/<slug>/NN_*.csv
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
# Vendored compact snapshot (what the Docker image is seeded from), mirroring
# data/vlop-dsa.json. Built from the sibling repo's extracted CSVs via
# write_snapshot() / scripts and committed.
_DEFAULT_SNAPSHOT = os.getenv(
    "SEED_HARMONISED_JSON", os.path.join(HERE, "data", "harmonised-reports.json"))
_DEFAULT_EXTRACTED = os.getenv(
    "SEED_HARMONISED_DIR",
    os.path.join(HERE, "..", "dsa-transparency-data", "harmonised-reports", "extracted"),
)

# These three extracted platforms are already VLOP services in vlop-dsa.json —
# skip them so the official aggregated figures aren't double-counted.
SKIP_SLUGS = {"linkedin", "pinterest", "wikipedia"}

# slug -> (display service name, tier). Tier is informational (online-platform /
# hosting / intermediary); none of these are VLOPs.
SLUG_META = {
    "aboutyou": ("AboutYou", "online-platform"),
    "dailymotion": ("Dailymotion", "online-platform"),
    "carrefour": ("Carrefour Marketplace", "online-platform"),
    "ceneo": ("Ceneo", "online-platform"),
    "cloudflare": ("Cloudflare", "intermediary"),
    "duckduckgo": ("DuckDuckGo", "online-platform"),
    "expedia": ("Expedia", "online-platform"),
    "hometogo": ("HomeToGo", "online-platform"),
    "hostelworld": ("Hostelworld", "online-platform"),
    "hostinger": ("Hostinger", "hosting"),
    "hotelscom": ("Hotels.com", "online-platform"),
    "imdb": ("IMDb", "online-platform"),
    "konami": ("Konami", "online-platform"),
    "lilo": ("Lilo", "online-platform"),
    "manomano": ("ManoMano", "online-platform"),
    "matchgroup": ("Tinder (Match Group)", "online-platform"),
    "niantic": ("Pokémon GO (Niantic)", "online-platform"),
    "qwant": ("Qwant", "online-platform"),
    "roblox": ("Roblox", "online-platform"),
    "shopify": ("Shopify", "online-platform"),
    "skroutz": ("Skroutz", "online-platform"),
    "veepee": ("Veepee", "online-platform"),
    "vinted": ("Vinted", "online-platform"),
    "vrbo": ("Vrbo", "online-platform"),
    "webde": ("Web.de", "online-platform"),
    "yahoo": ("Yahoo Search", "online-platform"),
}

SECTIONS = [
    "01_report_identification", "02_categories_names", "03_member_states_orders",
    "04_notices", "05_own_initiative_illegal", "06_own_initiative_TC",
    "07_appeals_and_recidivism", "08_automated_means", "09_human_resources",
    "10_AMAR", "11_qualitative",
]


def _num(v: str | None):
    """Parse a template cell as an int, else None (blanks, 'n/a', free text)."""
    if v is None:
        return None
    s = v.strip().replace(",", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _cell(row: list[str], i: int):
    """Numeric value of column `i` (or None if absent / non-numeric)."""
    return _num(row[i] if len(row) > i else "")


def _rows(path: str) -> list[list[str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = list(csv.reader(f))
    return data[1:] if data else []  # drop header


class _Interner:
    """Reuse-or-append rows in a shared dimension table, keyed by a string."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cat_by_key: dict[str, int] = {}
        for cid, code, label in conn.execute("SELECT id, code, label FROM categories"):
            self.cat_by_key.setdefault(code, cid)
            self.cat_by_key.setdefault(label, cid)
        self.next_cat = (conn.execute("SELECT COALESCE(MAX(id), -1) FROM categories").fetchone()[0]) + 1
        self.simple = {}
        self.next = {}
        for table in ("sections", "indicators", "scopes", "surfaces"):
            self.simple[table] = {name: i for i, name in conn.execute(f"SELECT id, name FROM {table}")}
            self.next[table] = conn.execute(f"SELECT COALESCE(MAX(id), -1) FROM {table}").fetchone()[0] + 1

    def category(self, value: str) -> int:
        key = (value or "TOTAL").strip() or "TOTAL"
        if key not in self.cat_by_key:
            cid = self.next_cat
            self.conn.execute("INSERT INTO categories (id, code, label) VALUES (?, ?, ?)",
                              (cid, key, key))
            self.cat_by_key[key] = cid
            self.next_cat += 1
        return self.cat_by_key[key]

    def dim(self, table: str, value: str) -> int:
        key = (value or "").strip()
        m = self.simple[table]
        if key not in m:
            i = self.next[table]
            self.conn.execute(f"INSERT INTO {table} (id, name) VALUES (?, ?)", (i, key))
            m[key] = i
            self.next[table] += 1
        return m[key]


def _ident(rows: list[list[str]]) -> tuple[str, str]:
    """Return (period_start, period_end) from a section-1 table (best effort)."""
    start = end = ""
    keys_start = ("starting date", "beginn des berichtszeitraums", "date de début", "έναρξης")
    keys_end = ("ending date", "ende des berichtszeitraums", "date de fin", "λήξης")
    for r in rows:
        label = " ".join(c.lower() for c in r[:-1])
        val = next((c for c in reversed(r) if c), "")[:10]
        if any(k in label for k in keys_start) and not start:
            start = val
        elif any(k in label for k in keys_end) and not end:
            end = val
    return start, end


def _period_from_sections(sections: list[list[list[str]]]) -> tuple[str, str] | None:
    """Most-common "Reporting period" value (col 2) across the filled data sheets
    (sections 3-11). More reliable than section 1, which some publishers leave as
    a "YYYY-MM-DD/YYYY-MM-DD" placeholder or fill with a wrong year."""
    from collections import Counter
    vals: list[str] = []
    for i in range(2, 11):
        if i < len(sections):
            for r in sections[i]:
                if len(r) > 2:
                    v = r[2].strip()
                    if v and "Y" not in v.upper() and any(c.isdigit() for c in v) and "/" in v:
                        vals.append(v)
    if not vals:
        return None
    best = Counter(vals).most_common(1)[0][0]
    parts = [p.strip()[:10] for p in best.split("/")]
    return (parts[0], parts[1]) if len(parts) == 2 else None


def read_extracted(extracted_dir: str = _DEFAULT_EXTRACTED) -> dict[str, list[list[list[str]]]]:
    """{slug: [section-1 rows, …, section-11 rows]} read from the extracted CSVs
    (headers dropped). Used to build the vendored snapshot."""
    out: dict[str, list[list[list[str]]]] = {}
    if not os.path.isdir(extracted_dir):
        return out
    for slug in sorted(os.listdir(extracted_dir)):
        d = os.path.join(extracted_dir, slug)
        if os.path.isdir(d):
            out[slug] = [_rows(os.path.join(d, s + ".csv")) for s in SECTIONS]
    return out


def write_snapshot(extracted_dir: str = _DEFAULT_EXTRACTED,
                   json_path: str = _DEFAULT_SNAPSHOT) -> int:
    data = read_extracted(extracted_dir)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return len(data)


def _load_snapshot(snapshot_path: str, extracted_dir: str) -> dict[str, list[list[list[str]]]]:
    if snapshot_path and os.path.exists(snapshot_path):
        with open(snapshot_path, encoding="utf-8") as f:
            return json.load(f)
    return read_extracted(extracted_dir)


def build_harmonised_facts(db_path: str, snapshot_path: str = _DEFAULT_SNAPSHOT,
                           extracted_dir: str = _DEFAULT_EXTRACTED) -> dict[str, int]:
    """Append every non-VLOP report to the star schema at db_path, from the
    vendored snapshot (preferred) or the sibling repo's extracted CSVs."""
    data = _load_snapshot(snapshot_path, extracted_dir)
    if not data:
        return {}
    conn = sqlite3.connect(db_path)
    try:
        intern = _Interner(conn)
        surface_all = intern.dim("surfaces", "All")
        next_service = conn.execute("SELECT COALESCE(MAX(id), -1) FROM services").fetchone()[0] + 1
        next_report = conn.execute("SELECT COALESCE(MAX(id), -1) FROM reports").fetchone()[0] + 1

        counts = {"services": 0, "reports": 0, "facts": 0}
        for slug in sorted(data):
            if slug in SKIP_SLUGS:
                continue
            sections = data[slug]

            def sec(i: int) -> list[list[str]]:
                return sections[i] if i < len(sections) else []

            name, tier = SLUG_META.get(slug, (slug, "online-platform"))
            # Prefer the period from the filled data sheets' "Reporting period"
            # column (sections 3-11, col 2) over section 1 — it's more reliable
            # against publisher typos / unfilled template placeholders in sheet 1.
            start, end = _period_from_sections(sections) or _ident(sec(0))
            period = f"{start}/{end}" if start or end else ""
            svc_id, rep_id = next_service, next_report
            next_service += 1
            next_report += 1
            conn.execute("INSERT INTO services (id, name, platform) VALUES (?, ?, ?)",
                         (svc_id, name, name))
            conn.execute(
                "INSERT INTO reports (id, period, period_start, period_end, tier, generated) "
                "VALUES (?, ?, ?, ?, ?, NULL)", (rep_id, period, start, end, tier))
            counts["services"] += 1
            counts["reports"] += 1
            n = _load_facts(conn, intern, rep_id, svc_id, sec, surface_all)
            counts["facts"] += n
        conn.commit()
        return counts
    finally:
        conn.close()


def _load_facts(conn, intern, rep, svc, sec, surface_all) -> int:
    n = 0

    def cat(r, i):
        return intern.category(r[i] if len(r) > i else "")

    # t3 — member-state orders: cat=3, scope=5, act=6, items=7, info=10
    for r in sec(2):
        if len(r) < 7:
            continue
        conn.execute("INSERT INTO t3_member_state_orders VALUES (?,?,?,?,?,?,?)",
                     (rep, svc, cat(r, 3), intern.dim("scopes", r[5] if len(r) > 5 else ""),
                      _num(r[6]), _num(r[7] if len(r) > 7 else ""), _num(r[10] if len(r) > 10 else "")))
        n += 1
    # t4 — notices: cat=3, then 5..14
    for r in sec(3):
        if len(r) < 6:
            continue
        conn.execute("INSERT INTO t4_notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rep, svc, cat(r, 3), _cell(r, 5), _cell(r, 6), _cell(r, 7),
                      _cell(r, 8), _cell(r, 9), _cell(r, 10), _cell(r, 11), _cell(r, 12),
                      _cell(r, 13), _cell(r, 14)))
        n += 1
    # t5 / t6 — own initiative: cat=3, measures=5, automated=6, vis 7-13,
    # monetary 14-16, service 17-18, account 19-20 (t6 + surface).
    for tbl, si, extra in (("t5_own_initiative_illegal", 4, ()),
                           ("t6_own_initiative_tos", 5, (surface_all,))):
        for r in sec(si):
            if len(r) < 7:
                continue
            vals = ([rep, svc, cat(r, 3), _cell(r, 5), _cell(r, 6)]
                    + [_cell(r, i) for i in range(7, 21)] + list(extra))
            conn.execute(f"INSERT INTO {tbl} VALUES ({', '.join(['?'] * len(vals))})", vals)
            n += 1
    # t7 / t8 — section=3, indicator=4, scope=5, value=6 (+ default surface)
    for tbl, si in (("t7_appeals_recidivism", 6), ("t8_automated_means", 7)):
        for r in sec(si):
            if len(r) < 7:
                continue
            conn.execute(f"INSERT INTO {tbl} VALUES (?,?,?,?,?,?,?)",
                         (rep, svc, intern.dim("sections", r[3]), intern.dim("indicators", r[4]),
                          intern.dim("scopes", r[5]), _num(r[6]), surface_all))
            n += 1
    # t9 — section=3, indicator=4, scope=5, value=6
    for r in sec(8):
        if len(r) < 7:
            continue
        conn.execute("INSERT INTO t9_human_resources VALUES (?,?,?,?,?,?)",
                     (rep, svc, intern.dim("sections", r[3]), intern.dim("indicators", r[4]),
                      intern.dim("scopes", r[5]), _num(r[6])))
        n += 1
    # t10 — AMAR: scope=5, value=6
    for r in sec(9):
        if len(r) < 7:
            continue
        conn.execute("INSERT INTO t10_amar VALUES (?,?,?,?)",
                     (rep, svc, intern.dim("scopes", r[5]), _num(r[6])))
        n += 1
    # t11 — qualitative: indicator=3, text=4
    for r in sec(10):
        if len(r) < 5:
            continue
        conn.execute("INSERT INTO t11_qualitative VALUES (?,?,?,?)",
                     (rep, svc, intern.dim("indicators", r[3]), (r[4] or None)))
        n += 1
    return n


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "snapshot":
        n = write_snapshot()
        print(f"wrote {_DEFAULT_SNAPSHOT} ({n} platforms)")
    else:
        db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "demo.db")
        print(build_harmonised_facts(db))
