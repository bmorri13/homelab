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
# Same card layout and severity scheme as the homelab agents (homelab_agents/app/runner.py).
SEVERITY = {"critical": (":red_circle:", "Critical"), "high": (":large_orange_circle:", "High"),
            "medium": (":large_yellow_circle:", "Warning"), "low": (":large_blue_circle:", "Info")}
# Result fields shown in the card's field grid, in this order, with their labels.
FIELDS = [("agent", "Agent"), ("problem", "Problem"), ("last_run", "Last run"), ("runs", "Runs (last hour)"),
          ("host", "Host"), ("finding_host", "Host"), ("agent_host", "Host"), ("src_host", "Source"),
          ("rule", "Rule"), ("user", "User"), ("src", "Source"), ("object", "Object"), ("count", "Count")]


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


def esc(text):
    """Slack mrkdwn control characters: stops <!channel> and disguised links from event data."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def message(payload):
    """A Block Kit card: severity header, title and description, field grid, optional
    link (result field `link`, https only), footer."""
    r = payload.get("result", {})
    sev = (r.get("severity") or "medium").lower()
    emoji, label = SEVERITY.get(sev, SEVERITY["medium"])
    title = r.get("detection") or payload.get("search_name", "Splunk alert")
    detail = r.get("detail") or r.get("details") or r.get("commands") or r.get("signatures") or ""
    if isinstance(detail, list):
        detail = "; ".join(detail)
    if r.get("technique"):
        detail = f"ATT&CK v20 {r['technique']} ({r.get('tactic', '')}). {detail}"

    fields = [{"type": "mrkdwn", "text": f"*Severity*\n{emoji} {label}"}]
    seen = set()
    for key, name in FIELDS:
        value = r.get(key)
        if isinstance(value, list):
            value = ", ".join(map(str, value[:3]))
        if value in (None, "") or name in seen or len(fields) >= 8:
            continue
        seen.add(name)
        fields.append({"type": "mrkdwn", "text": f"*{name}*\n{esc(clip(value, 80))}"})

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "emoji": True, "text": f":rotating_light: Splunk Alert — {label}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{esc(clip(title, 150))}*" + (f"\n{esc(clip(detail, 600))}" if detail else "")}},
        {"type": "divider"},
        {"type": "section", "fields": fields},
    ]
    link = r.get("link") or ""
    if link.startswith("https://"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<{link}|View Dashboard ↗>"}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f":bar_chart: Bmo Homelab Bot • Splunk • {esc(payload.get('search_name', ''))}"}]})
    return {"text": f"{label}: {clip(title, 150)}", "blocks": blocks}


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
