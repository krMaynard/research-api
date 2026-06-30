#!/usr/bin/env python3
"""Re-vendor the non-VLOP data snapshots from the sibling dsa-transparency-data repo.

The API serves a *frozen snapshot* of the data-collection pipeline that lives in
the separate `dsa-transparency-data` repo (scrapers, raw archives, the canonical
extracted CSVs, the report-locations catalogue). This script regenerates the two
vendored artifacts the Docker image is seeded from:

  * data/harmonised-reports.json  <- <data-repo>/harmonised-reports/extracted/<slug>/NN_*.csv
  * data/report-locations.csv     <- <data-repo>/dsa_reports.csv

and reports any **new extracted platforms that aren't yet curated in
`seed_harmonised.SLUG_META`** (display name + tier). Those still seed — they fall
back to (slug, "online-platform") — but a human should give them a real name, so
the script surfaces a ready-to-paste snippet instead of guessing silently.

It writes a Markdown summary (for a PR body / job summary) to --summary-out (or
stdout). It is the mechanical half of the "collect upstream, then surface in the
API" flow; the `.github/workflows/revendor-data.yml` workflow runs it on a
schedule / on demand and opens a PR with whatever changed.

Usage:
    python scripts/revendor_data.py                       # re-vendor in place
    python scripts/revendor_data.py --data-repo /path/... # explicit source repo
    python scripts/revendor_data.py --check               # report only, no writes
    python scripts/revendor_data.py --summary-out s.md     # write the summary file
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import seed_harmonised as sh  # noqa: E402  (after sys.path tweak)

DEFAULT_DATA_REPO = os.getenv(
    "DATA_REPO", os.path.normpath(os.path.join(REPO, "..", "dsa-transparency-data"))
)
VENDORED_SNAPSHOT = os.path.join(REPO, "data", "harmonised-reports.json")
VENDORED_RL_CSV = os.path.join(REPO, "data", "report-locations.csv")
VENDORED_APPLE = os.path.join(REPO, "data", "apple-transparency.json")
APPLE_SRC_REL = os.path.join("apple-transparency", "apple-transparency.json")
VENDORED_GITHUB = os.path.join(REPO, "data", "github-transparency.json")
GITHUB_SRC_REL = os.path.join("github-transparency", "github-transparency.json")
VENDORED_SNAP = os.path.join(REPO, "data", "snap-transparency.json")
SNAP_SRC_REL = os.path.join("snap-transparency", "snap-transparency.json")
RL_HEADER = "platform,company,category,confidence,harmonised_template,format_period,url_label,url,archived"


def _suggested_name(slug: str) -> str:
    """A best-effort display name for an un-curated slug (a hint, not the truth)."""
    return slug.replace("-", " ").title()


def _slugs(extracted_dir: str) -> set[str]:
    """The extracted platform slugs — just the subdirectory names, so we don't
    read every section CSV (write_snapshot does that once on its own)."""
    return {d for d in os.listdir(extracted_dir)
            if os.path.isdir(os.path.join(extracted_dir, d))}


def _uncurated(slugs: set[str]) -> list[str]:
    """Extracted slugs with no SLUG_META entry (and not intentionally skipped /
    attached as an extra period) — i.e. would seed under their raw slug name."""
    curated = set(sh.SLUG_META) | set(sh.SKIP_SLUGS) | set(sh.EXTRA_PERIODS)
    return sorted(slugs - curated)


def _stale(slugs: set[str]) -> list[str]:
    """SLUG_META entries whose extracted dir has disappeared upstream."""
    return sorted(k for k in sh.SLUG_META if k not in slugs)


def _unknown_surfaces(extracted_dir: str) -> dict[str, list[str]]:
    """Surface labels in the extracted t6/t7/t8 CSVs that the seeder doesn't
    recognise. A folded section has a trailing 'Surface' header; the seeder maps
    its values via sh.FOLDED_SURFACES and silently falls back to 'All' for
    anything else — which, for a per-surface filer with no real 'All' total,
    would reintroduce a double-count. Return {label: [slug/section, ...]} for any
    value not in FOLDED_SURFACES so the re-vendor surfaces it for a human."""
    known = set(sh.FOLDED_SURFACES)
    found: dict[str, list[str]] = {}
    # Only sections 6/7/8 carry a surface dimension.
    surface_sections = [s for s in sh.SECTIONS if s[:2] in ("06", "07", "08")]
    for slug in sorted(_slugs(extracted_dir)):
        for sec in surface_sections:
            path = os.path.join(extracted_dir, slug, sec + ".csv")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header or header[-1] != "Surface":
                    continue  # not a folded section — single 'All' surface
                for r in reader:
                    label = r[-1] if r else ""
                    if label and label not in known:
                        found.setdefault(label, []).append(f"{slug}/{sec}")
    return {k: sorted(set(v)) for k, v in found.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-repo", default=DEFAULT_DATA_REPO,
                    help="Path to the sibling dsa-transparency-data repo")
    ap.add_argument("--check", action="store_true",
                    help="Report what would change without writing the snapshots")
    ap.add_argument("--summary-out", default="-",
                    help="Write the Markdown summary here ('-' = stdout)")
    args = ap.parse_args()

    extracted_dir = os.path.join(args.data_repo, "harmonised-reports", "extracted")
    rl_src = os.path.join(args.data_repo, "dsa_reports.csv")
    if not os.path.isdir(extracted_dir):
        print(f"error: extracted dir not found: {extracted_dir}", file=sys.stderr)
        return 2
    if not os.path.isfile(rl_src):
        print(f"error: report catalogue not found: {rl_src}", file=sys.stderr)
        return 2

    # Validate the catalogue header before trusting it as a drop-in replacement.
    with open(rl_src, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").rstrip("\r")
    if header != RL_HEADER:
        print(f"error: {rl_src} header changed.\n  expected: {RL_HEADER}\n  found:    {header}",
              file=sys.stderr)
        return 2

    slugs = _slugs(extracted_dir)
    n_platforms = len(slugs)
    with open(rl_src, encoding="utf-8") as f:
        n_rl_rows = sum(1 for _ in f) - 1  # minus header
    uncurated = _uncurated(slugs)
    stale = _stale(slugs)
    unknown_surf = _unknown_surfaces(extracted_dir)
    apple_src = os.path.join(args.data_repo, APPLE_SRC_REL)
    apple_present = os.path.isfile(apple_src)
    github_src = os.path.join(args.data_repo, GITHUB_SRC_REL)
    github_present = os.path.isfile(github_src)
    snap_src = os.path.join(args.data_repo, SNAP_SRC_REL)
    snap_present = os.path.isfile(snap_src)

    if not args.check:
        sh.write_snapshot(extracted_dir=extracted_dir, json_path=VENDORED_SNAPSHOT)
        shutil.copyfile(rl_src, VENDORED_RL_CSV)
        if apple_present:
            shutil.copyfile(apple_src, VENDORED_APPLE)
        if github_present:
            shutil.copyfile(github_src, VENDORED_GITHUB)
        if snap_present:
            shutil.copyfile(snap_src, VENDORED_SNAP)

    verb = "Would re-vendor" if args.check else "Re-vendored"
    lines = [
        "## Re-vendor data snapshots",
        "",
        f"{verb} from `{os.path.relpath(args.data_repo, REPO)}`:",
        "",
        f"- `data/harmonised-reports.json` — **{n_platforms}** extracted report files",
        f"- `data/report-locations.csv` — **{n_rl_rows}** catalogue rows",
        f"- `data/apple-transparency.json` — {'present upstream' if apple_present else '**missing upstream — skipped**'}",
        f"- `data/github-transparency.json` — {'present upstream' if github_present else '**missing upstream — skipped**'}",
        f"- `data/snap-transparency.json` — {'present upstream' if snap_present else '**missing upstream — skipped**'}",
        "",
    ]
    if uncurated:
        lines += [
            f"### ⚠️ {len(uncurated)} new platform(s) need a `SLUG_META` entry",
            "",
            "These seed under their raw slug until given a display name + tier. "
            "Paste into `seed_harmonised.SLUG_META` and adjust the name/tier:",
            "",
            "```python",
            *[f'    "{s}": ("{_suggested_name(s)}", "online-platform"),' for s in uncurated],
            "```",
            "",
        ]
    else:
        lines += ["All extracted platforms are curated in `SLUG_META`. ✅", ""]
    if stale:
        lines += [
            f"### ℹ️ {len(stale)} `SLUG_META` entr(y/ies) no longer in the extract",
            "",
            "Their upstream dir disappeared — drop them if the removal is intended:",
            "",
            *[f"- `{s}`" for s in stale],
            "",
        ]
    if unknown_surf:
        lines += [
            f"### ⚠️ {len(unknown_surf)} unrecognised surface label(s) in t6/t7/t8",
            "",
            f"The extractor folded a `Surface` value the seeder doesn't know "
            f"(it maps only {list(sh.FOLDED_SURFACES)}; anything else silently "
            f"falls back to the `All` total, which for a per-surface filer "
            f"**reintroduces a double-count**). Extend `seed_harmonised.FOLDED_SURFACES` "
            f"+ `_load_facts` (and the `surfaces` dimension) to handle these:",
            "",
            *[f"- `{label}` — in {', '.join(locs)}" for label, locs in sorted(unknown_surf.items())],
            "",
        ]
    summary = "\n".join(lines)

    if args.summary_out == "-":
        print(summary)
    else:
        with open(args.summary_out, "a", encoding="utf-8") as f:
            f.write(summary + "\n")
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
