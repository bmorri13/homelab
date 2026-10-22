#!/usr/bin/env bash
# Install or upgrade the repo's Cribl packs (cribl/packs/<id>/) through the Cribl API.
# A pack is (re)installed when it is missing or its package.json version differs from
# the installed one, so bump the version to roll out a change. The repo owns packs:
# an upgrade replaces the installed copy, including edits made to it in the UI.
# Copying a pack into Cribl's config directory is not enough; Cribl lists it but routes
# referencing it (pack:<id>) stay unresolved until it is installed through the API.
set -euo pipefail

cd "$(dirname "$0")/.."
: "${CRIBL_ADMIN_PASSWORD:?set CRIBL_ADMIN_PASSWORD in .env}"
API="${CRIBL_API:-http://127.0.0.1:9000/api/v1}"

shopt -s nullglob
packs=(cribl/packs/*/package.json)
[[ ${#packs[@]} -gt 0 ]] || { echo "cribl: no packs in cribl/packs"; exit 0; }

session=$(python3 -c 'import json, os; print(json.dumps({"username": "admin", "password": os.environ["CRIBL_ADMIN_PASSWORD"]}))' |
    curl -sf -X POST "$API/auth/login" -H 'Content-Type: application/json' --data @- |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])')
auth=(-H "Authorization: Bearer $session")

installed=$(curl -sf "${auth[@]}" "$API/packs" |
    python3 -c 'import json, sys; print(json.dumps({p["id"]: p.get("version") for p in json.load(sys.stdin)["items"]}))')

for manifest in "${packs[@]}"; do
    dir=$(dirname "$manifest")
    read -r id version < <(python3 -c 'import json, sys; m = json.load(open(sys.argv[1])); print(m["name"], m["version"])' "$manifest")
    current=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1]).get(sys.argv[2]) or "")' "$installed" "$id")
    if [[ "$current" == "$version" ]]; then
        echo "cribl: pack $id $version already installed"
        continue
    fi

    crbl=$(mktemp --suffix=.crbl)
    tar --exclude 'CLAUDE.md' -czf "$crbl" -C "$dir" package.json default data
    source=$(curl -sf "${auth[@]}" -H 'Content-Type: application/octet-stream' \
        -X PUT "$API/packs?filename=$id.crbl" --data-binary @"$crbl" |
        python3 -c 'import json, sys; print(json.load(sys.stdin)["source"])')
    rm -f "$crbl"

    if [[ -n "$current" ]]; then
        curl -sf "${auth[@]}" -X DELETE "$API/packs/$id" >/dev/null
    fi
    resp=$(curl -s "${auth[@]}" -H 'Content-Type: application/json' -X POST "$API/packs" \
        -d "{\"source\":\"$source\"}")
    if [[ "$resp" == *'"status":"error"'* ]]; then
        echo "cribl: installing pack $id failed: $resp" >&2
        exit 1
    fi
    echo "cribl: pack $id ${current:-not installed} -> $version"
done
