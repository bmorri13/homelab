# shellcheck shell=bash disable=SC2154  # auth is set by the sourcing script
# Shared helpers for the Cribl scripts. Source it after loading .env.
# The Leader's API is HTTPS with the homelab CA cert written by tls-certs.sh.
# Worker Group config (inputs, routes, packs) is edited under /m/default/... and only
# reaches the Worker after cribl_commit_deploy.

CRIBL_DIR="${CRIBL_DATA_DIR:-/srv/cribl}"
API="${CRIBL_API:-https://127.0.0.1:9000/api/v1}"
CRIBL_CACERT="${CRIBL_CACERT:-$CRIBL_DIR/local/cribl/auth/homelab-tls/ca.crt}"
GROUP=default

ccurl() { curl --cacert "$CRIBL_CACERT" "$@"; }

# cribl_login <user> <name of env var holding the password>: prints a session
# token, or nothing if the login failed. The password goes to curl on stdin.
# Logins are rate-limited (2/second), so a 429 is retried.
cribl_login() {
    local resp
    for _ in 1 2 3 4 5; do
        resp=$(python3 -c 'import json, os, sys; print(json.dumps({"username": sys.argv[1], "password": os.environ[sys.argv[2]]}))' "$1" "$2" |
            ccurl -s -w '\n%{http_code}' -X POST "$API/auth/login" -H 'Content-Type: application/json' --data @- || true)
        [[ "${resp##*$'\n'}" == 429 ]] || break
        sleep 1
    done
    printf '%s' "${resp%$'\n'*}" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("token", ""))' 2>/dev/null || true
}

# cribl_wait_api: wait up to 5 minutes for the Leader API to answer.
cribl_wait_api() {
    for _ in $(seq 1 60); do
        ccurl -sf "$API/health" >/dev/null && return 0
        sleep 5
    done
    echo "cribl: API at $API not answering" >&2
    return 1
}

# cribl_commit_deploy <message>: commit pending Worker Group changes and deploy
# them to the Worker. Needs the auth array (-H "Authorization: Bearer ...").
cribl_commit_deploy() {
    local resp version
    resp=$(python3 -c 'import json, sys; print(json.dumps({"message": sys.argv[1], "group": sys.argv[2]}))' "$1" "$GROUP" |
        ccurl -s "${auth[@]}" -H 'Content-Type: application/json' -X POST "$API/version/commit" --data @-)
    version=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["items"][0]["commit"])' "$resp" 2>/dev/null || true)
    if [[ -z "$version" ]]; then
        echo "cribl: commit failed: $resp" >&2
        return 1
    fi
    ccurl -sf "${auth[@]}" -H 'Content-Type: application/json' -X PATCH "$API/master/groups/$GROUP/deploy" \
        -d "{\"version\":\"$version\"}" >/dev/null
    echo "cribl: deployed ${version:0:8} to group $GROUP ($1)"
}
