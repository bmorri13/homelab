#!/usr/bin/env bash
# Mint a Splunk MCP Server token for an MCP user and print only the token on stdout, so it can be piped straight into Vault. Creates the user
# first if it is missing, with a random password that is never stored: the gateway
# only ever uses tokens. Each run mints a new token; earlier ones stay valid until
# they expire, so delete them under Settings > Tokens once the new one is in use.
#   usage: ./scripts/splunk-mcp-token.sh [expires_on]   (default +180d, the app's maximum)
# The user is mcp-gateway (role mcp_reader, the gateway's splunk backend) unless
# MCP_USER/MCP_ROLE say otherwise; the agents' backend (splunk-agent) uses
#   MCP_USER=mcp-agent MCP_ROLE=mcp_agent ./scripts/splunk-mcp-token.sh
# Run on security-tooling-01 as root, after deploy.sh. Talks to the published port
# 8089 and verifies its cert against the homelab CA.
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "missing .env" >&2; exit 1; }
set -a; . ./.env; set +a
: "${SPLUNK_PASSWORD:?set SPLUNK_PASSWORD in .env}"
BASE="https://${SPLUNK_MGMT_BIND:-192.168.4.11}:8089"
CA="${HOMELAB_CA_DIR:-/root/homelab-ca}/ca.crt"
MCP_USER=${MCP_USER:-mcp-gateway}
MCP_ROLE=${MCP_ROLE:-mcp_reader}
EXPIRES="${1:-+180d}"

# splunk_api <curl args...>: authenticated as admin. The admin password (and any
# extra secret config lines on fd 3) reach curl through a config file on stdin.
splunk_api() {
    { printf 'user = "admin:%s"\n' "$SPLUNK_PASSWORD"; cat <&3; } |
        curl -s --cacert "$CA" -K - "$@"
}

status=$(splunk_api -o /dev/null -w '%{http_code}' "$BASE/services/authentication/users/$MCP_USER" 3</dev/null)
if [[ "$status" == 404 ]]; then
    password=$(openssl rand -base64 33)
    resp=$(splunk_api -X POST "$BASE/services/authentication/users" \
        --data-urlencode "name=$MCP_USER" --data-urlencode "roles=$MCP_ROLE" \
        -w '\n%{http_code}' 3< <(printf 'data-urlencode = "password=%s"\n' "$password"))
    unset password
    [[ "${resp##*$'\n'}" == 201 ]] || { echo "creating $MCP_USER failed: ${resp%$'\n'*}" >&2; exit 1; }
    echo "created Splunk user $MCP_USER (role $MCP_ROLE)" >&2
elif [[ "$status" != 200 ]]; then
    echo "looking up $MCP_USER failed: HTTP $status" >&2
    exit 1
fi

resp=$(splunk_api -G "$BASE/services/mcp_token" --data-urlencode "username=$MCP_USER" \
    --data-urlencode "expires_on=$EXPIRES" --data-urlencode "output_mode=json" 3</dev/null)
printf '%s' "$resp" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    token = json.loads(raw).get("token")
except ValueError:
    token = None
if not token:
    sys.exit("minting the MCP token failed: " + raw[:300])
print(token)'
echo "minted an MCP token for $MCP_USER (expires_on $EXPIRES)" >&2
