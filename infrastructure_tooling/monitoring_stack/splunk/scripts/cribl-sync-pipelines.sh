#!/usr/bin/env bash
# Push the repo's Cribl pipelines (cribl/local/cribl/pipelines/*/conf.yml) and
# routes (pipelines/route.yml) to Worker Group "default", then commit and deploy.
# deploy.sh only seeds the group once, so this is how later edits reach Cribl.
#   ./scripts/cribl-sync-pipelines.sh           apply changes
#   ./scripts/cribl-sync-pipelines.sh --check   only list what differs
# Pipelines and routes that exist only in Cribl are left alone. Run on
# security-tooling-01 as root from the stack directory, after deploy.sh.
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "missing .env" >&2; exit 1; }
set -a; . ./.env; set +a
: "${CRIBL_ADMIN_PASSWORD:?set CRIBL_ADMIN_PASSWORD in .env}"
. scripts/cribl-lib.sh
check=0; [[ "${1:-}" == --check ]] && check=1

session=$(cribl_login admin CRIBL_ADMIN_PASSWORD)
[[ -n "$session" ]] || { echo "cribl: admin login failed" >&2; exit 1; }
auth=(-H "Authorization: Bearer $session" -H 'Content-Type: application/json')
base="$API/m/$GROUP"

# same <live json> <repo json>: compare the fields the repo defines. JSON goes
# through stdin: secret_mask is larger than the kernel's per-argument limit.
same() {
    printf '%s\n%s\n' "$1" "$2" | python3 -c 'import json, sys
live, repo = (json.loads(l) for l in sys.stdin.read().splitlines())
sys.exit(0 if all(live.get(k) == v for k, v in repo.items()) else 1)'
}
yaml2json() { python3 -c 'import json, sys, yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))' "$1"; }

changed=0
for conf in cribl/local/cribl/pipelines/*/conf.yml; do
    id=$(basename "$(dirname "$conf")")
    repo=$(yaml2json "$conf")
    live=$(ccurl -s "${auth[@]}" "$base/pipelines/$id" | jq -c '.items[0].conf // empty')
    [[ -n "$live" ]] && same "$live" "$repo" && continue
    # new pipelines are created; secret_mask sorts before splunk_out, which chains to it
    if [[ -z "$live" ]]; then echo "pipeline $id: new"; method=POST; url="$base/pipelines"
    else echo "pipeline $id: differs"; method=PATCH; url="$base/pipelines/$id"; fi
    changed=1
    [[ $check -eq 1 ]] && continue
    jq -c --arg id "$id" '{id: $id, conf: .}' <<<"$repo" |
        ccurl -sf "${auth[@]}" -X "$method" "$url" --data @- >/dev/null ||
        { echo "cribl: updating pipeline $id failed" >&2; exit 1; }
done

routes=$(yaml2json cribl/local/cribl/pipelines/route.yml)
live=$(ccurl -s "${auth[@]}" "$base/routes/default" | jq -c '.items[0] // {}')
if ! same "$(jq -c '{routes: .routes}' <<<"$live")" "$(jq -c '{routes: .routes}' <<<"$routes")"; then
    echo "routes: differ"
    changed=1
    if [[ $check -eq 0 ]]; then
        jq -c --argjson repo "$routes" '.routes = $repo.routes' <<<"$live" |
            ccurl -sf "${auth[@]}" -X PATCH "$base/routes/default" --data @- >/dev/null ||
            { echo "cribl: updating routes failed" >&2; exit 1; }
    fi
fi

if [[ $changed -eq 0 ]]; then echo "cribl: pipelines and routes match the repo"; exit 0; fi
[[ $check -eq 1 ]] && exit 0
cribl_commit_deploy "Sync pipelines and routes from the repo"
