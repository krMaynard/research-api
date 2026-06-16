#!/usr/bin/env bash
# Refresh the vendored dataset snapshots from the canonical source in the sibling
# krMaynard.github.io repo. The snapshots are what the Docker image is seeded from
# at build time, so run this whenever the upstream datasets change, then commit.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="$here/../krMaynard.github.io/data"

vlop_src="${1:-$data_dir/vlop-dsa.json}"
gr_src="${2:-$data_dir/google-government-removals.json}"

if [[ ! -f "$vlop_src" ]]; then
  echo "Source dataset not found: $vlop_src" >&2
  echo "Pass the path explicitly: scripts/refresh-dataset.sh /path/to/vlop-dsa.json [/path/to/google-government-removals.json]" >&2
  exit 1
fi

python3 -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$vlop_src"
cp "$vlop_src" "$here/data/vlop-dsa.json"
echo "Updated data/vlop-dsa.json from $vlop_src ($(wc -c <"$here/data/vlop-dsa.json") bytes)."

if [[ -f "$gr_src" ]]; then
  python3 -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$gr_src"
  cp "$gr_src" "$here/data/google-government-removals.json"
  echo "Updated data/google-government-removals.json from $gr_src ($(wc -c <"$here/data/google-government-removals.json") bytes)."
else
  echo "(skipping google-government-removals.json — not found: $gr_src)"
fi
