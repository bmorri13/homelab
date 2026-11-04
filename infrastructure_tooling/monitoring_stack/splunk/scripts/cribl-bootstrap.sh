#!/usr/bin/env bash
# Setup for the Cribl Leader. Safe to re-run; each step only acts when needed:
#  1. Turn on HTTPS for the Leader API (cert from tls-certs.sh) and restart it.
#  2. After deploy.sh has seeded the Worker Group config (marker file .group-seeded),
#     set the HEC token from .env, enable the HEC input, and commit and deploy the
#     group config to the Worker. A group that still has the old plain-HTTP input
#     (hec, port 8088) gets hec_tls created from it, and hec removed.
#  3. On a brand-new Leader, still on its default admin/admin login, set the admin
#     password from .env. This runs last so a failed run can still log in.
# Once the password has changed, later token or password changes made in the UI are
# kept. Secrets are passed to curl on stdin so they never appear in the process list.
set -euo pipefail

cd "$(dirname "$0")/.."
: "${CRIBL_ADMIN_PASSWORD:?set CRIBL_ADMIN_PASSWORD in .env}"
: "${CRIBL_HEC_TOKEN:?set CRIBL_HEC_TOKEN in .env}"
. scripts/cribl-lib.sh
SEEDED="$CRIBL_DIR/.group-seeded"

# Until step 1 has run, the API is plain HTTP.
https_api="$API"
for _ in $(seq 1 60); do
    ccurl -sf "$https_api/health" >/dev/null && break
    if curl -sf "${https_api/https:/http:}/health" >/dev/null; then API="${https_api/https:/http:}"; break; fi
    sleep 5
done

login() {
    session=$(CRIBL_DEFAULT_PASSWORD=admin cribl_login admin CRIBL_DEFAULT_PASSWORD)
    default_login=false
    if [[ -n "$session" ]]; then
        default_login=true
    else
        session=$(cribl_login admin CRIBL_ADMIN_PASSWORD)
    fi
    [[ -n "$session" ]] || { echo "cribl: login failed with both the default and the .env password" >&2; exit 1; }
    auth=(-H "Authorization: Bearer $session")
}
login

# 1. HTTPS for the Leader API.
if [[ "$API" == http:* ]]; then
    ccurl -sf "${auth[@]}" "$API/system/settings/conf" |
        python3 -c '
import json, sys
s = json.load(sys.stdin)["items"][0]
# Absolute path: the Leader process that serves Workers on port 4200 does not
# expand $CRIBL_VOLUME_DIR and fails to boot with it.
tls = "/opt/cribl/config-volume/local/cribl/auth/homelab-tls"
s["api"]["ssl"] = {"disabled": False, "privKeyPath": tls + "/cribl.key",
                   "certPath": tls + "/cribl.crt", "minVersion": "TLSv1.2"}
print(json.dumps(s))' |
        ccurl -sf "${auth[@]}" -H 'Content-Type: application/json' -X PATCH "$API/system/settings/conf" --data @- >/dev/null
    docker compose restart cribl >/dev/null
    API="$https_api"
    cribl_wait_api
    login
    echo "cribl: Leader API switched to HTTPS"
fi

# The config API is not ready for a few seconds after /health turns healthy.
for _ in $(seq 1 30); do
    ccurl -sf "${auth[@]}" "$API/m/$GROUP/system/inputs/hec" >/dev/null && break
    sleep 2
done

# hec_input <id> <port> <tls>: print the JSON for HEC input <id>, enabled, with the
# token from .env. Starts from the input's current config, or from the plain hec
# input's when <id> doesn't exist yet. <tls> true: HTTPS with the Worker's cert.
hec_input() {
    local cur
    cur=$(ccurl -sf "${auth[@]}" "$API/m/$GROUP/system/inputs/$1" ||
          ccurl -sf "${auth[@]}" "$API/m/$GROUP/system/inputs/hec")
    python3 -c '
import json, os, sys
i = json.loads(sys.argv[1])["items"][0]
for k in ("status", "notifications"):
    i.pop(k, None)
i["id"], i["port"], i["disabled"] = sys.argv[2], int(sys.argv[3]), False
i["authTokens"] = [{"token": os.environ["CRIBL_HEC_TOKEN"], "enabled": True, "description": "OTel collectors"}]
if sys.argv[4] == "true":
    i["tls"] = {"disabled": False, "privKeyPath": "/opt/cribl/tls/hec.key",
                "certPath": "/opt/cribl/tls/hec.crt", "minVersion": "TLSv1.2"}
    i["description"] = "HEC over HTTPS for the OTel collectors and agents. Token set by scripts/cribl-bootstrap.sh"
print(json.dumps(i))' "$cur" "$1" "$2" "$3"
}

# 2. Freshly seeded Worker Group config: its HEC token is either empty (repo seed)
# or encrypted with the old single-instance key, which the group cannot decrypt.
if [[ -f "$SEEDED" ]]; then
    if ccurl -sf "${auth[@]}" "$API/m/$GROUP/system/inputs/hec_tls" >/dev/null; then
        hec_input hec_tls 8443 true |
            ccurl -sf "${auth[@]}" -H 'Content-Type: application/json' -X PATCH "$API/m/$GROUP/system/inputs/hec_tls" --data @- >/dev/null
        echo "cribl: HEC input enabled"
    fi
    cribl_commit_deploy "Seed Worker Group config (deploy.sh)"
    rm -f "$SEEDED"
fi
if ! ccurl -sf "${auth[@]}" "$API/m/$GROUP/system/inputs/hec_tls" >/dev/null; then
    hec_input hec_tls 8443 true |
        ccurl -sf "${auth[@]}" -H 'Content-Type: application/json' -X POST "$API/m/$GROUP/system/inputs" --data @- >/dev/null
    echo "cribl: HEC input hec_tls (HTTPS, port 8443) created"
    cribl_commit_deploy "Add HEC input hec_tls (deploy.sh)"
fi
# NetFlow/IPFIX from the UniFi gateway (2026-11-04): created from the repo seed on a
# group seeded before it existed.
if ! ccurl -sf "${auth[@]}" "$API/m/$GROUP/system/inputs/netflow" >/dev/null; then
    python3 -c 'import json, sys, yaml; c = yaml.safe_load(open(sys.argv[1]))["inputs"]["netflow"]; c["id"] = "netflow"; print(json.dumps(c))' \
        cribl/local/cribl/inputs.yml |
        ccurl -sf "${auth[@]}" -H 'Content-Type: application/json' -X POST "$API/m/$GROUP/system/inputs" --data @- >/dev/null
    echo "cribl: NetFlow input netflow (UDP 2055) created"
    cribl_commit_deploy "Add NetFlow input netflow (deploy.sh)"
fi
# Plain-HTTP HEC is retired: every sender uses hec_tls (2026-11-03).
if ccurl -sf "${auth[@]}" "$API/m/$GROUP/system/inputs/hec" >/dev/null; then
    ccurl -sf "${auth[@]}" -X DELETE "$API/m/$GROUP/system/inputs/hec" >/dev/null
    echo "cribl: plain-HTTP HEC input hec removed"
    cribl_commit_deploy "Remove plain-HTTP HEC input hec (deploy.sh)"
fi

# 3. Admin password, only while the default login still works.
if [[ "$default_login" == true ]]; then
    resp=$(python3 -c 'import json, os; print(json.dumps({"id": "admin", "username": "admin", "first": "admin", "last": "admin", "email": "admin", "roles": ["admin"], "password": os.environ["CRIBL_ADMIN_PASSWORD"]}))' |
        ccurl -s "${auth[@]}" -H 'Content-Type: application/json' -X PATCH "$API/system/users/admin" --data @-)
    if [[ "$resp" == *'"status":"error"'* ]]; then
        echo "cribl: setting the admin password failed: $resp" >&2
        exit 1
    fi
    echo "cribl: admin password set"
fi
