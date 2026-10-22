#!/usr/bin/env bash
# First-run setup for Cribl. Cribl starts with the login admin/admin and has no
# way to set credentials at startup, so this script, while admin/admin still
# works, sets the admin password and the HEC input token from .env and enables
# the HEC input. Once the password has changed, admin/admin fails and the script
# does nothing, so later token or password changes made in the UI are kept.
# Secrets are passed to curl on stdin so they never appear in the process list.
set -euo pipefail

: "${CRIBL_ADMIN_PASSWORD:?set CRIBL_ADMIN_PASSWORD in .env}"
: "${CRIBL_HEC_TOKEN:?set CRIBL_HEC_TOKEN in .env}"
API="${CRIBL_API:-http://127.0.0.1:9000/api/v1}"

for _ in $(seq 1 60); do
    curl -sf "$API/health" >/dev/null && break
    sleep 5
done

session=$(curl -s -X POST "$API/auth/login" -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin"}' |
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("token", ""))' 2>/dev/null || true)
if [[ -z "$session" ]]; then
    echo "cribl: already bootstrapped (default login rejected), skipping"
    exit 0
fi

auth=(-H "Authorization: Bearer $session" -H 'Content-Type: application/json')

# The config API is not ready for a few seconds after /health turns healthy.
for _ in $(seq 1 30); do
    curl -sf "${auth[@]}" "$API/system/inputs/hec" >/dev/null && break
    sleep 2
done

# Enable the HEC input with the token from .env.
curl -sf "${auth[@]}" "$API/system/inputs/hec" |
    python3 -c '
import json, os, sys
i = json.load(sys.stdin)["items"][0]
for k in ("status", "notifications"):
    i.pop(k, None)
i["disabled"] = False
i["authTokens"] = [{"token": os.environ["CRIBL_HEC_TOKEN"], "enabled": True, "description": "OTel collectors"}]
print(json.dumps(i))' |
    curl -sf "${auth[@]}" -X PATCH "$API/system/inputs/hec" --data @- >/dev/null
echo "cribl: HEC input enabled"

# Change the admin password last: if anything above failed, a re-run can still log in.
# Cribl requires 3 of: lowercase, uppercase, digits, symbols.
resp=$(python3 -c 'import json, os; print(json.dumps({"id": "admin", "username": "admin", "first": "admin", "last": "admin", "email": "admin", "roles": ["admin"], "password": os.environ["CRIBL_ADMIN_PASSWORD"]}))' |
    curl -s "${auth[@]}" -X PATCH "$API/system/users/admin" --data @-)
if [[ "$resp" == *'"status":"error"'* ]]; then
    echo "cribl: setting the admin password failed: $resp" >&2
    exit 1
fi
echo "cribl: admin password set"
