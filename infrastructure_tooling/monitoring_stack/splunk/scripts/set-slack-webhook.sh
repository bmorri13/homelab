#!/usr/bin/env bash
# Store (or replace) the Slack incoming-webhook URL used by the homelab_slack alert
# action (apps/85_detections) in Splunk's credential store: storage/passwords, app
# 85_detections, realm homelab_slack, user webhook. Splunk encrypts it with its
# splunk.secret. The URL is read from stdin and is never printed or passed as an
# argument:
#   printf '%s' "$URL" | ssh root@security-tooling-01 /opt/splunk-stack/scripts/set-slack-webhook.sh
# Run on security-tooling-01 as root from the stack directory.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

IFS= read -r url || true
[[ "$url" == https://hooks.slack.com/* ]] || { echo "expected a https://hooks.slack.com/ URL on stdin" >&2; exit 1; }

# Inside the container: python reads the admin password from the environment and the
# URL from stdin, and talks to splunkd on 127.0.0.1 with the homelab CA.
printf '%s' "$url" | docker exec -i -u splunk -e P="$SPLUNK_PASSWORD" splunk /opt/splunk/bin/splunk cmd python3 -c '
import base64, json, os, ssl, sys, urllib.parse, http.client
url = sys.stdin.read().strip()
ctx = ssl.create_default_context(cafile="/opt/splunk/etc/auth/homelab/cacert.pem")
auth = "Basic " + base64.b64encode(("admin:" + os.environ["P"]).encode()).decode()
base = "/servicesNS/nobody/85_detections/storage/passwords"
def call(method, path, body=None):
    c = http.client.HTTPSConnection("127.0.0.1", 8089, context=ctx, timeout=15)
    hdr = {"Authorization": auth, "Content-Type": "application/x-www-form-urlencoded"}
    c.request(method, path + "?output_mode=json", body=urllib.parse.urlencode(body) if body else None, headers=hdr)
    r = c.getresponse(); r.read(); return r.status
name = urllib.parse.quote("homelab_slack:webhook:", safe="")
if call("POST", base + "/" + name, {"password": url}) == 200:
    print("updated"); sys.exit(0)
s = call("POST", base, {"name": "webhook", "realm": "homelab_slack", "password": url})
print("created" if s in (200, 201) else "failed: HTTP %d" % s); sys.exit(0 if s in (200, 201) else 1)
'
