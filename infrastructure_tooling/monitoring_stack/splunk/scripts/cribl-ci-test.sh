#!/usr/bin/env bash
# Check the Cribl image pinned in docker-compose.yml before a version bump merges.
# Starts it as a throwaway Leader (no Worker, no Splunk, no secrets) and checks that:
#   1. the Leader and the Worker are pinned to the same image
#   2. it starts, its API turns healthy, and it reports the version in the tag
#   3. it accepts every pipeline in cribl/local/cribl/pipelines
#   4. the secret masking still passes cribl/tests/secret-mask (cribl-test-mask.py)
# Run by .github/workflows/cribl-image.yml. It can be run by hand on any machine with
# Docker: it touches nothing outside its own container, which it removes on exit.
set -euo pipefail

cd "$(dirname "$0")/.."

mapfile -t images < <(awk '$1 == "image:" && $2 ~ /^cribl\/cribl:/ {print $2}' docker-compose.yml | sort -u)
if [[ ${#images[@]} -ne 1 ]]; then
    echo "cribl: the Leader and the Worker must be pinned to one image, found: ${images[*]:-none}" >&2
    exit 1
fi
image=${images[0]}
version=${image#cribl/cribl:}
version=${version%%@*}
echo "cribl: testing $image"

name="cribl-ci-$$"
port=19000
# Any value works: no Worker connects to this Leader.
dist_token=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
docker run -d --name "$name" -p "127.0.0.1:$port:9000" \
    -e CRIBL_DIST_MODE=master -e "CRIBL_DIST_MASTER_URL=tcp://$dist_token@0.0.0.0:4200" "$image" >/dev/null
ok=0
cleanup() {
    [[ $ok -eq 1 ]] || docker logs --tail 60 "$name" 2>&1 || true
    docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# A fresh Leader: plain HTTP on loopback, with the default login.
export CRIBL_API="http://127.0.0.1:$port/api/v1" CRIBL_ADMIN_PASSWORD=admin
export CRIBL_CACERT=/etc/ssl/certs/ca-certificates.crt  # not used over HTTP, but curl wants the file to exist
. scripts/cribl-lib.sh
cribl_wait_api

running=$(docker exec "$name" /opt/cribl/bin/cribl version 2>&1 || true)
if ! grep -qF "$version" <<<"$running"; then
    echo "cribl: the image reports '$running', expected version $version" >&2
    exit 1
fi
echo "cribl: healthy, reports $version"

session=$(cribl_login admin CRIBL_ADMIN_PASSWORD)
[[ -n "$session" ]] || { echo "cribl: login to the test Leader failed" >&2; exit 1; }
auth=(-H "Authorization: Bearer $session" -H 'Content-Type: application/json')
base="$API/m/$GROUP"
# The config API is not ready for a few seconds after /health turns healthy.
for _ in $(seq 1 30); do
    curl -sf "${auth[@]}" "$base/pipelines" >/dev/null && break
    sleep 2
done

# Under their own names would clash with nothing on a fresh Leader, but splunk_out
# chains to secret_mask, so create them in name order (as cribl-sync-pipelines.sh does).
for conf in cribl/local/cribl/pipelines/*/conf.yml; do
    id=$(basename "$(dirname "$conf")")
    body=$(python3 -c 'import json, sys, yaml; print(json.dumps({"id": sys.argv[1], "conf": yaml.safe_load(open(sys.argv[2]))}))' "$id" "$conf")
    code=$(curl -s -o /tmp/cribl-ci-resp -w '%{http_code}' "${auth[@]}" -X POST "$base/pipelines" --data @- <<<"$body")
    if [[ "$code" != 200 ]]; then
        echo "cribl: $version refused pipeline $id (HTTP $code): $(head -c 400 /tmp/cribl-ci-resp)" >&2
        exit 1
    fi
    echo "cribl: accepted pipeline $id"
done

python3 scripts/cribl-test-mask.py --live
ok=1
echo "cribl: $image passed"
