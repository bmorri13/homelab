#!/usr/bin/env bash
# Print the agents' retro findings as JSON, most often seen first.
#
#   ./retro-findings.sh [min_runs]     # default 1: every finding
#
# Reads the retro_findings table of the agents' state.db on k3s-01 over Tailscale SSH.
# Read-only: the database is opened with mode=ro and nothing on the host is changed.
# Each row: id, agent, category, target, title, proposal, observation (from the latest
# run that reported it), times_seen, first_seen, last_seen (UTC).
set -euo pipefail
min_runs="${1:-1}"
[[ "$min_runs" =~ ^[0-9]+$ ]] || { echo "min_runs must be a number" >&2; exit 2; }

tailscale ssh root@k3s-01 python3 - "$min_runs" <<'EOF'
import datetime, glob, json, sqlite3, sys
paths = glob.glob("/var/lib/rancher/k3s/storage/pvc-*_toolhive-clients_homelab-agents-state/state.db")
if len(paths) != 1:
    sys.exit(f"expected one state.db, found {len(paths)}")
db = sqlite3.connect(f"file:{paths[0]}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
cols = [r["name"] for r in db.execute("PRAGMA table_info(retro_findings)")]
utc = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
rows = []
for r in db.execute("SELECT * FROM retro_findings WHERE times_seen >= ? ORDER BY times_seen DESC, last_seen DESC",
                    (int(sys.argv[1]),)):
    row = {c: r[c] for c in cols}
    row["first_seen"], row["last_seen"] = utc(r["first_seen"]), utc(r["last_seen"])
    rows.append(row)
print(json.dumps(rows, indent=1))
EOF
