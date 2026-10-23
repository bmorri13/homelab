#!/usr/bin/env bash
# Create the Cribl user the MCP gateway logs in as (mcp-gateway), or reset its
# password if it exists, and print {"username": ..., "password": ...} on stdout so
# it can be piped straight into Vault:
#   ./scripts/cribl-mcp-user.sh | <vault kv put kv/apiary/shared/cribl ->
# The free license only has the admin role, so this user is an admin too; it exists
# so its actions show up under their own name and its password can be rotated alone.
# Run on security-tooling-01 as root, after deploy.sh.
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "missing .env" >&2; exit 1; }
set -a; . ./.env; set +a
: "${CRIBL_ADMIN_PASSWORD:?set CRIBL_ADMIN_PASSWORD in .env}"
. scripts/cribl-lib.sh
MCP_USER=mcp-gateway

session=$(cribl_login admin CRIBL_ADMIN_PASSWORD)
[[ -n "$session" ]] || { echo "cribl: admin login failed" >&2; exit 1; }
auth=(-H "Authorization: Bearer $session" -H 'Content-Type: application/json')

# 32 random characters plus one of each class Cribl's password rules count.
MCP_PASSWORD=$(python3 -c 'import secrets, string; a = string.ascii_letters + string.digits; print("".join(secrets.choice(a) for _ in range(32)) + "aZ9-")')
export MCP_PASSWORD

user_json() {
    python3 -c 'import json, os, sys; print(json.dumps({"id": sys.argv[1], "username": sys.argv[1], "first": "MCP", "last": "gateway", "email": "", "roles": ["admin"], "password": os.environ["MCP_PASSWORD"]}))' "$MCP_USER"
}

status=$(ccurl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$API/system/users/$MCP_USER")
if [[ "$status" == 200 ]]; then
    method=PATCH; url="$API/system/users/$MCP_USER"; action="password reset"
else
    method=POST; url="$API/system/users"; action="created"
fi
resp=$(user_json | ccurl -s "${auth[@]}" -X "$method" "$url" --data @-)
if [[ "$resp" == *'"status":"error"'* || "$resp" != *"$MCP_USER"* ]]; then
    # Only Cribl's message: the raw response could echo the request back.
    msg=$(printf '%s' "$resp" | python3 -c 'import json, sys
try: print(json.load(sys.stdin).get("message", "no message"))
except ValueError: print("unparseable response")')
    echo "cribl: $action for $MCP_USER failed: $msg" >&2
    exit 1
fi

# Prove the new password works before handing it out.
check=$(cribl_login "$MCP_USER" MCP_PASSWORD)
[[ -n "$check" ]] || { echo "cribl: login as $MCP_USER failed after $action" >&2; exit 1; }
python3 -c 'import json, os, sys; print(json.dumps({"username": sys.argv[1], "password": os.environ["MCP_PASSWORD"]}))' "$MCP_USER"
echo "cribl: $MCP_USER $action" >&2
