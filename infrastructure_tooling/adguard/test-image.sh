#!/usr/bin/env bash
# Check the AdGuard Home image pinned in manifests/adguard.yaml before a version bump
# merges. Starts it the way the pod does (same arguments, a copy of
# manifests/AdGuardHome.yaml, an empty work directory, not root) and checks that:
#   1. it starts and reports the version in the tag
#   2. it answers a DNS query over UDP and over TCP on the pod's port
#   3. it still has our settings after rewriting its config (it migrates the file on start)
#   4. the query lands in work/agh/data/querylog.json, the file the sidecar ships to Splunk
# The one change to the config: the upstream. Ours is the LAN gateway, which a CI
# runner cannot reach, so the test points AdGuard at public resolvers instead.
# No secrets. Run by .github/workflows/adguard-image.yml, or by hand on any machine with
# Docker and dig; it removes its container and its temporary directory on exit.
set -euo pipefail

cd "$(dirname "$0")"

image=$(awk '$1 == "image:" && $2 ~ /\/adguardhome:/ {print $2}' manifests/adguard.yaml | sort -u)
[[ -n "$image" && "$image" != *$'\n'* ]] || { echo "adguard: expected one adguardhome image in manifests/adguard.yaml, found: ${image:-none}" >&2; exit 1; }
version=${image##*/adguardhome:}
version=${version%%@*}
echo "adguard: testing $image"

gateway=192.168.4.1
grep -q "$gateway" manifests/AdGuardHome.yaml || { echo "adguard: the config no longer uses $gateway as its upstream: update this test" >&2; exit 1; }

name="adguard-ci-$$"; port=15353
tmp=$(mktemp -d)
mkdir -p "$tmp/conf" "$tmp/work/agh"
chmod 700 "$tmp/work/agh"
sed -e "0,/- $gateway/s//- 1.1.1.1\n    - 8.8.8.8/" -e "s/- $gateway/- 1.1.1.1/" manifests/AdGuardHome.yaml > "$tmp/conf/AdGuardHome.yaml"
ok=0
cleanup() {
    [[ $ok -eq 1 ]] || docker logs --tail 40 "$name" 2>&1 || true
    docker rm -f "$name" >/dev/null 2>&1 || true
    rm -rf "$tmp"
}
trap cleanup EXIT

docker run -d --name "$name" --user "$(id -u):$(id -g)" --cap-drop ALL --cap-add NET_BIND_SERVICE \
    -p "127.0.0.1:$port:5353/udp" -p "127.0.0.1:$port:5353/tcp" \
    -v "$tmp/conf:/opt/adguardhome/conf" -v "$tmp/work:/opt/adguardhome/work" \
    "$image" --no-check-update -c /opt/adguardhome/conf/AdGuardHome.yaml -w /opt/adguardhome/work/agh >/dev/null

lookup() { dig +short +time=3 +tries=1 "$@" @127.0.0.1 -p "$port" example.com A | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1; }
answer=""
for _ in $(seq 1 30); do
    [[ "$(docker inspect -f '{{.State.Running}}' "$name")" == true ]] || { echo "adguard: the container exited" >&2; exit 1; }
    answer=$(lookup || true)
    [[ -n "$answer" ]] && break
    sleep 2
done
[[ -n "$answer" ]] || { echo "adguard: no answer over UDP on port 5353" >&2; exit 1; }
[[ -n "$(lookup +tcp || true)" ]] || { echo "adguard: no answer over TCP on port 5353" >&2; exit 1; }
echo "adguard: answers over UDP and TCP (example.com -> $answer)"

running=$(docker exec "$name" /opt/adguardhome/AdGuardHome --version 2>&1 || true)
grep -qF "$version" <<<"$running" || { echo "adguard: the image reports '$running', expected $version" >&2; exit 1; }

# A clean stop writes the query log buffer and the config.
docker stop -t 30 "$name" >/dev/null
after="$tmp/conf/AdGuardHome.yaml"
python3 - "$after" <<'PY'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
dns, log, flt = c["dns"], c["querylog"], c["filtering"]
want = {
    "dns port": (dns["port"], 5353),
    "upstreams": (dns["upstream_dns"], ["1.1.1.1", "8.8.8.8"]),
    "query log on": (log["enabled"] and log["file_enabled"], True),
    "filtering off": (flt["filtering_enabled"], False),
    "no blocklists": (c.get("filters") or [], []),
    "no users": (c.get("users") or [], []),
}
bad = {k: got for k, (got, expected) in want.items() if got != expected}
if bad:
    sys.exit(f"adguard: settings changed after the new version rewrote the config: {bad}")
print(f"adguard: settings kept, config schema_version {c.get('schema_version')}")
PY
grep -q '"QH":"example.com"' "$tmp/work/agh/data/querylog.json" 2>/dev/null ||
    { echo "adguard: the query is not in work/agh/data/querylog.json (the file shipped to Splunk)" >&2; ls -R "$tmp/work" >&2; exit 1; }
ok=1
echo "adguard: $image passed"
