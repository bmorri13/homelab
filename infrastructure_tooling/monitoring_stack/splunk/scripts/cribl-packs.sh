#!/usr/bin/env bash
# Install or upgrade the repo's Cribl packs (cribl/packs/<id>/) in the default Worker
# Group through the Leader API, then commit and deploy them to the Worker.
# A pack is (re)installed when it is missing or its package.json version differs from
# the installed one, so bump the version to roll out a change. The repo owns packs:
# an upgrade replaces the installed copy, including edits made to it in the UI.
# Copying a pack into Cribl's config directory is not enough; Cribl lists it but routes
# referencing it (pack:<id>) stay unresolved until it is installed through the API.
set -euo pipefail

cd "$(dirname "$0")/.."
: "${CRIBL_ADMIN_PASSWORD:?set CRIBL_ADMIN_PASSWORD in .env}"
. scripts/cribl-lib.sh

shopt -s nullglob
packs=(cribl/packs/*/package.json)
[[ ${#packs[@]} -gt 0 ]] || { echo "cribl: no packs in cribl/packs"; exit 0; }

session=$(cribl_login admin CRIBL_ADMIN_PASSWORD)
[[ -n "$session" ]] || { echo "cribl: login failed" >&2; exit 1; }
auth=(-H "Authorization: Bearer $session")
changed=()

installed=$(ccurl -sf "${auth[@]}" "$API/m/$GROUP/packs" |
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
    source=$(ccurl -sf "${auth[@]}" -H 'Content-Type: application/octet-stream' \
        -X PUT "$API/m/$GROUP/packs?filename=$id.crbl" --data-binary @"$crbl" |
        python3 -c 'import json, sys; print(json.load(sys.stdin)["source"])')
    rm -f "$crbl"

    if [[ -n "$current" ]]; then
        ccurl -sf "${auth[@]}" -X DELETE "$API/m/$GROUP/packs/$id" >/dev/null
    fi
    resp=$(ccurl -s "${auth[@]}" -H 'Content-Type: application/json' -X POST "$API/m/$GROUP/packs" \
        -d "{\"source\":\"$source\"}")
    if [[ "$resp" == *'"status":"error"'* ]]; then
        echo "cribl: installing pack $id failed: $resp" >&2
        exit 1
    fi
    echo "cribl: pack $id ${current:-not installed} -> $version"
    changed+=("$id $version")
done

if [[ ${#changed[@]} -gt 0 ]]; then
    cribl_commit_deploy "Install packs: ${changed[*]} (deploy.sh)"
fi
