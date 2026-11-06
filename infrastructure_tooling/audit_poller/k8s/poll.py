#!/usr/bin/env python3
"""Poll free audit APIs and send new events to Cribl HEC (see ../README.md).

Runs as a CronJob. State is one JSON file on a small PVC. Each source is read from
its last position minus an overlap, because events can appear late, and events
already sent are skipped by ID. A source whose credentials aren't set is skipped.
Each run writes one status event per source. The job fails if any source failed.
Standard library only.
"""
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HEC_URL = os.environ["HEC_URL"]
HEC_TOKEN = os.environ["HEC_TOKEN"]
HEC_CA = os.environ.get("HEC_CA") or None
STATE_FILE = os.environ.get("STATE_FILE", "/state/state.json")
GITHUB_REPOS = os.environ.get("GITHUB_REPOS", "").split()
INDEX = "audit"
OVERLAP = timedelta(minutes=30)
FIRST_RUN_LOOKBACK = timedelta(hours=24)
USER_AGENT = "homelab-audit-poller"
TIMEOUT = 30


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def request(url, headers=None, data=None, context=None):
    """Return (parsed JSON, response headers). Errors never include the request headers."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=context) as resp:
        return json.load(resp), resp.headers


def event_id(event):
    return hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()[:32]


def cloudflare(since, until):
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_TOKEN")
    if not account or not token:
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{urllib.parse.quote(account)}/logs/audit"
    params = {"since": iso(since), "before": iso(until), "limit": 1000, "direction": "asc"}
    events = []
    while True:
        body, _ = request(f"{url}?{urllib.parse.urlencode(params)}", {"Authorization": f"Bearer {token}"})
        for event in body.get("result") or []:
            events.append((event.get("id") or event_id(event), parse_time(event["action"]["time"]), event))
        cursor = (body.get("result_info") or {}).get("cursor")
        if not cursor:
            return "cloudflare:audit", events
        params["cursor"] = cursor


def tailscale(since, until):
    client_id = os.environ.get("TAILSCALE_CLIENT_ID")
    client_secret = os.environ.get("TAILSCALE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    form = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret}).encode()
    token, _ = request("https://api.tailscale.com/api/v2/oauth/token", data=form)
    params = urllib.parse.urlencode({"start": iso(since), "end": iso(until)})
    body, _ = request(f"https://api.tailscale.com/api/v2/tailnet/-/logging/configuration?{params}",
                      {"Authorization": f"Bearer {token['access_token']}"})
    events = [(event_id(e), parse_time(e["eventTime"]), e) for e in body.get("logs") or []]
    return "tailscale:audit", events


def github(since, _until):
    """Public repositories need no token: 4 polls an hour is well inside the anonymous limit."""
    if not GITHUB_REPOS:
        return None
    events = []
    for repo in GITHUB_REPOS:
        url = f"https://api.github.com/repos/{repo}/activity?per_page=100"
        while url:
            body, headers = request(url, {"Accept": "application/vnd.github+json"})
            for event in body:
                when = parse_time(event["timestamp"])
                if when >= since:
                    events.append((f"{repo}/{event['id']}", when, {"repo": repo, **event}))
            oldest = min((parse_time(e["timestamp"]) for e in body), default=since)
            url = next_link(headers.get("Link", "")) if oldest >= since else None
    return "github:activity", events


def next_link(link_header):
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip(" <>")
    return None


def send(items):
    """items: HEC event envelopes. Sent in batches, so a failure can resend at most one run."""
    context = ssl.create_default_context(cafile=HEC_CA)
    for i in range(0, len(items), 100):
        data = "".join(json.dumps(item) for item in items[i:i + 100]).encode()
        request(HEC_URL, {"Authorization": f"Splunk {HEC_TOKEN}"}, data=data, context=context)


def envelope(source, sourcetype, when, event):
    return {"time": when.timestamp(), "host": "audit-poller", "source": source,
            "sourcetype": sourcetype, "index": INDEX, "event": event}


def poll(name, fetch, state, now):
    """Fetch and send one source. Returns its status event."""
    entry = state.setdefault(name, {"since": iso(now - FIRST_RUN_LOOKBACK), "seen": {}})
    since = parse_time(entry["since"])
    result = fetch(since - OVERLAP, now)
    if result is None:
        return {"poll_source": name, "status": "skipped", "new_events": 0}
    sourcetype, events = result
    seen = entry["seen"]
    new = [e for e in events if e[0] not in seen]
    send([envelope(name, sourcetype, when, event) for _, when, event in new])
    seen.update({eid: when.timestamp() for eid, when, _ in new})
    cutoff = (since - 2 * OVERLAP).timestamp()
    entry["seen"] = {eid: t for eid, t in seen.items() if t >= cutoff}
    entry["since"] = iso(now)
    return {"poll_source": name, "status": "ok", "new_events": len(new)}


def describe(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


def main():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    now = datetime.now(timezone.utc)
    statuses = []
    for name, fetch in (("cloudflare", cloudflare), ("tailscale", tailscale), ("github", github)):
        try:
            status = poll(name, fetch, state, now)
        except Exception as exc:  # one source failing must not stop the others
            status = {"poll_source": name, "status": "error", "new_events": 0, "error": describe(exc)}
        statuses.append(status)
        print(json.dumps(status), flush=True)
    tmp = f"{STATE_FILE}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)
    send([{"time": time.time(), "host": "audit-poller", "source": "audit-poller",
           "sourcetype": "audit_poller:status", "index": INDEX, "event": s} for s in statuses])
    return 1 if any(s["status"] == "error" for s in statuses) else 0


if __name__ == "__main__":
    sys.exit(main())
