#!/usr/bin/env bash
# End-to-end CLI demo for the Repo Analysis Tool.
#
#   bash scripts/demo.sh [REPO_URL] [NAME]
#   DIR=<path> bash scripts/demo.sh        # directory used in the dir-metrics step
#
# Examples:
#   bash scripts/demo.sh                                        # cJSON (fast)
#   bash scripts/demo.sh https://github.com/redis/redis.git redis
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"

PY="${PY:-python}"
REPO_URL="${1:-https://github.com/DaveGamble/cJSON.git}"
NAME="${2:-}"
SAMPLE_DIR="${DIR:-tests}"

banner() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

# Read a metrics bundle on stdin and print the spec 2.1-2.4 quantities.
summarize() {
  "$PY" -c "$(cat <<'PYEOF'
import json, sys
d = json.load(sys.stdin)
s = d["scope"]
print(f"  scope        {s['kind']} '{s['path'] or '<repository root>'}")
print(f"  commits |H|  {d['n_commits']}")
print(f"  added    l+  {d['added']}")
print(f"  removed  l-  {d['removed']}")
print(f"  growth   d   {d['growth']}")
print(f"  churn    l   {d['churn']}")
print(f"  mods     n   {d['modifications']}  (frequency {d['modification_frequency']:.3f})")
print(f"  churn rate   {d['churn_rate']:.2f} changed lines / commit")
PYEOF
)"
}

# Read a metrics bundle on stdin and print the top authors (spec 2.5).
owners() {
  "$PY" -c "$(cat <<'PYEOF'
import json, sys
rows = json.load(sys.stdin)["contributors"][:8]
print(f"  {'author':<30}{'commits':>9}{'churn':>10}{'ownership':>11}")
for row in rows:
    print(f"  {row['name'][:30]:<30}{row['n_commits']:>9}{row['churn']:>10}{row['ownership'] * 100:>10.1f}%")
PYEOF
)"
}

# Read an ingest result on stdin and print a one-line repository summary.
repo_line() {
  "$PY" -c "$(cat <<'PYEOF'
import json, sys
d = json.load(sys.stdin)["repo"]
print("  repo #{} {}: {} non-merge commits, {} files, {} directories, {} authors (after mailmap/merges)".format(
    d["id"], d["name"], d["n_commits"], d["n_files"], d["n_dirs"], d["n_authors"]))
PYEOF
)"
}

ARGS=()
[[ -n "$NAME" ]] && ARGS+=(--name "$NAME")

banner "1. Ingesting $REPO_URL"
INGEST_JSON="$("$PY" -m rat.cli ingest "$REPO_URL" "${ARGS[@]}" 2>/dev/null | sed -n '/^{/,$p')"
REPO_ID="$(printf '%s' "$INGEST_JSON" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["repo"]["id"])')"
HEAD_SHA="$(printf '%s' "$INGEST_JSON" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["repo"]["head_sha"])')"
printf '%s' "$INGEST_JSON" | repo_line

banner "2. Repository metrics -- whole commit set H-bar reachable from HEAD (spec 2.3)"
"$PY" -m rat.cli metrics "$REPO_ID" | summarize

banner "3. Directory metrics -- recursive roll-up over immediate children (spec 2.2)"
if "$PY" -m rat.cli metrics "$REPO_ID" --path "$SAMPLE_DIR" >/tmp/rat_demo_dir.json 2>/dev/null; then
  summarize </tmp/rat_demo_dir.json
else
  echo "  skipped: '$SAMPLE_DIR' is not a path in this repository (rerun with DIR=<path>)"
fi

banner "4. Commit-set metrics -- date window H(i,j): 2024-01-01 <= ct < 2025-01-01 (spec 2.4)"
"$PY" -m rat.cli metrics "$REPO_ID" --since 2024-01-01 --until 2025-01-01 | summarize

banner "5. Commit-set metrics -- one manually picked commit (merge commits are never in H-bar)"
"$PY" -m rat.cli metrics "$REPO_ID" --commits "$HEAD_SHA" | summarize

banner "6. Author metrics -- churn / modifications / ownership per author (spec 2.5)"
"$PY" -m rat.cli metrics "$REPO_ID" | owners

banner "Done"
cat <<'EOF'
  Web dashboard:  make run      -> http://127.0.0.1:8000
  Docker:         make docker   -> http://127.0.0.1:8000
  Tests:          make test
EOF
