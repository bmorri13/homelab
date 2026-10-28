"""Splunk custom alert action: post one finding to Slack.

Splunk runs this with --execute and a JSON payload on stdin (session_key,
server_uri, search_name, result, configuration). The incoming-webhook URL is read
from storage/passwords (realm homelab_slack, user webhook) with the alert's own
session key, so it never appears in config files, logs or the repo.
"""
import http.client
import json
import os
import ssl
import sys
import urllib.parse

APP = "85_detections"
REALM = "homelab_slack"
SEVERITY_ICON = {"critical": ":rotating_light:", "high": ":red_circle:", "medium": ":large_orange_circle:", "low": ":large_blue_circle:"}


def log(msg):
    # stderr lands in index=_internal (sendmodalert), so never print the URL.
    print(f"homelab_slack: {msg}", file=sys.stderr)


def https_request(url, method="GET", body=None, headers=None, context=None):
    """HTTPS only (http.client.HTTPSConnection): no other scheme can be reached."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("only https URLs are allowed")
    conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=15, context=context or ssl.create_default_context())
    path = parts.path + ("?" + parts.query if parts.query else "")
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def webhook_url(server_uri, session_key):
    name = urllib.parse.quote(f"{REALM}:webhook:", safe="")
    url = f"{server_uri}/servicesNS/nobody/{APP}/storage/passwords/{name}?output_mode=json"
    # splunkd's cert is from the homelab CA and names 127.0.0.1 and localhost
    # (scripts/tls-certs.sh); caCertFile from server.conf holds that CA.
    ctx = ssl.create_default_context(cafile=os.path.join(os.environ.get("SPLUNK_HOME", "/opt/splunk"), "etc/auth/homelab/cacert.pem"))
    status, data = https_request(url, headers={"Authorization": f"Splunk {session_key}"}, context=ctx)
    if status != 200:
        raise RuntimeError(f"storage/passwords HTTP {status}")
    return json.loads(data)["entry"][0]["content"]["clear_password"]


def message(payload):
    r = payload.get("result", {})
    sev = (r.get("severity") or "medium").lower()
    title = r.get("detection") or payload.get("search_name", "Splunk alert")
    lines = [f"{SEVERITY_ICON.get(sev, '')} *[{sev.upper()}] {title}*"]
    if r.get("technique"):
        lines.append(f"ATT&CK v20 {r.get('technique')} ({r.get('tactic', '')}) {r.get('attack_strategy', '')}".rstrip())
    keys = ("host", "finding_host", "agent_host", "src_host", "rule", "user", "src", "object", "count")
    facts = [f"{k}: `{r[k]}`" for k in keys if r.get(k)]
    if facts:
        lines.append("  ".join(facts))
    # Alerts from other apps (Falco, OpenCanary, secret leaks) use their own field names.
    detail = r.get("detail") or r.get("details") or r.get("commands") or r.get("signatures") or ""
    if isinstance(detail, list):
        detail = " || ".join(detail)
    if detail:
        lines.append(f"> {detail[:1500]}")
    if r.get("first_seen"):
        lines.append(f"_{r.get('first_seen')} to {r.get('last_seen', '')}_")
    return {"text": "\n".join(lines)}


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "--execute":
        log("unsupported mode")
        return 2
    payload = json.loads(sys.stdin.read())
    try:
        url = webhook_url(payload["server_uri"], payload["session_key"])
    except Exception as exc:  # no webhook stored yet, or no permission to read it
        log(f"cannot read webhook from storage/passwords: {type(exc).__name__}")
        return 3
    if not url.startswith("https://hooks.slack.com/"):
        log("stored webhook is not a hooks.slack.com URL; refusing to send")
        return 4
    body = json.dumps(message(payload)).encode()
    try:
        status, _ = https_request(url, "POST", body, {"Content-Type": "application/json"})
        log(f"sent for {payload.get('search_name')}: HTTP {status}")
        if status != 200:
            return 5
    except Exception as exc:
        log(f"send failed for {payload.get('search_name')}: {type(exc).__name__}")
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
