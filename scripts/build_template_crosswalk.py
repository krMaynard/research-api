#!/usr/bin/env python3
"""Learn a deterministic {original-language label -> canonical English label}
crosswalk for the DSA harmonised template's *fixed* dimensions (sections,
indicators, and the outcome scopes of tables 7-9).

Filers fill the Commission's harmonised template verbatim in their own official
EU language, and every filer's data sheet follows the template's fixed row order.
So for any report whose sheet has the same row count as the canonical English
structure, row N is the *same template position* as row N of an English report —
which lets us read each non-English label's official English equivalent straight
off the aligned English row (no machine translation, no guessing).

We anchor on English reports (detected by their section labels), take the modal
row count per sheet as the canonical structure, and align every same-structure
report to it. Conflicts (one source label -> two English labels) are reported and
dropped. Output: data/template-crosswalk.json, vendored and consumed by
seed_harmonised to stamp a language-neutral key on each dimension row.

Run from the api repo with the sibling dsa-transparency-data checked out:
    python scripts/build_template_crosswalk.py
"""
from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRACTED = os.path.normpath(os.path.join(
    HERE, "..", "..", "dsa-transparency-data", "harmonised-reports", "extracted"))
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "template-crosswalk.json"))

# English section names that mark a report as English (any one is enough).
_EN_SECTIONS = {
    "Internal complaints mechanism", "Out-of-court dispute settlement bodies",
    "Suspensions imposed on repeated offenders",
    "Use of automated means for content moderation",
    "Human resources dedicated to content moderation",
}
# (sheet file -> {dimension: column index}) for the fixed-structure sheets.
# Only dimensions that are template-fixed *and* semantically the same across
# sheets are learned: sheet 09's "scope" is a language code (de/fr/sv…), not an
# outcome, so it is deliberately excluded from the scope crosswalk.
_SHEETS = {
    "07_appeals_and_recidivism": {"section": 3, "indicator": 4, "scope": 5},
    "08_automated_means": {"section": 3, "indicator": 4, "scope": 5},
    "09_human_resources": {"section": 3, "indicator": 4},
    "11_qualitative": {"indicator": 3},
}

def _rows(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8-sig") as f:   # -sig strips a leading BOM
        return [r for r in csv.reader(f)]


def _cell(row: list[str], i: int) -> str:
    return row[i].strip() if len(row) > i and row[i] else ""


def _is_greek(rows: list[list[str]]) -> bool:
    # Greek extracts have a column shift in the source data, so their rows can't
    # be aligned by position — a consistent shift produces *unanimous but wrong*
    # votes that slip past the conflict check. Skip them entirely (EL labels stay
    # in their original language rather than risk a mis-mapping).
    return any(any(0x370 <= ord(c) <= 0x3FF for c in cell) for row in rows for cell in row)


def main() -> None:
    reports = sorted(p for p in os.listdir(EXTRACTED)
                     if os.path.isdir(os.path.join(EXTRACTED, p)))

    # English reports, detected once from the section-bearing sheets (sheet 11 has
    # no section column, so per-sheet detection would silently skip it).
    english: set[str] = set()
    for rep in reports:
        for s in ("07_appeals_and_recidivism", "08_automated_means", "09_human_resources"):
            fp = os.path.join(EXTRACTED, rep, s + ".csv")
            if os.path.exists(fp) and any(_cell(r, 3) in _EN_SECTIONS for r in _rows(fp)):
                english.add(rep)
                break

    # crosswalk[dim][raw_label] -> Counter of english_label votes
    votes: dict[str, dict[str, Counter]] = {d: defaultdict(Counter)
                                             for d in ("section", "indicator", "scope")}

    for sheet, cols in _SHEETS.items():
        # Read each report's sheet once; drop Greek up front.
        sheet_rows: dict[str, list[list[str]]] = {}
        for rep in reports:
            fp = os.path.join(EXTRACTED, rep, sheet + ".csv")
            if os.path.exists(fp):
                rows = _rows(fp)
                if not _is_greek(rows):
                    sheet_rows[rep] = rows

        # 1. Canonical English structure = modal row count among English reports.
        en_counts = Counter(len(rows) for rep, rows in sheet_rows.items() if rep in english)
        if not en_counts:
            continue
        canon_n = en_counts.most_common(1)[0][0]
        canon = next(rows for rep, rows in sheet_rows.items()
                     if rep in english and len(rows) == canon_n)

        # 2. Align every same-structure report (any language) to the canonical.
        for rep, rows in sheet_rows.items():
            if len(rows) != canon_n:
                continue
            # Skip the header row (row 0: "Section"/"Indicator"/"Scope").
            for cr, rr in list(zip(canon, rows))[1:]:
                for dim, ci in cols.items():
                    if dim not in votes:
                        continue
                    raw, eng = _cell(rr, ci), _cell(cr, ci)
                    if raw and eng:
                        votes[dim][raw][eng] += 1

    # 3. Resolve votes. Correct-by-construction: a raw label is emitted only when
    # ALL evidence agrees on one English target. Any disagreement (a column-shifted
    # extract, a numeric stray, a mis-scanned cell) -> drop, leaving that label in
    # its original language rather than risk a wrong mapping.
    crosswalk: dict[str, dict[str, str]] = {}
    dropped = 0
    for dim, mapping in votes.items():
        out: dict[str, str] = {}
        for raw, counter in mapping.items():
            if not any(ch.isalpha() for ch in raw):   # numeric stray, not a label
                continue
            if len(counter) > 1:
                dropped += 1
                print(f"  drop [{dim}] {raw!r} -> ambiguous {dict(counter)}")
                continue
            top = next(iter(counter))
            if raw != top:                       # skip identity (already English)
                out[raw] = top
        crosswalk[dim] = dict(sorted(out.items()))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(crosswalk, f, ensure_ascii=False, indent=1, sort_keys=True)
    n = sum(len(v) for v in crosswalk.values())
    print(f"wrote {OUT}: {n} non-English mappings "
          f"({', '.join(f'{k}={len(v)}' for k, v in crosswalk.items())}), "
          f"{dropped} ambiguous labels dropped")


if __name__ == "__main__":
    main()
