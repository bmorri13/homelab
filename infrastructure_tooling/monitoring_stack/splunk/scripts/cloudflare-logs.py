#!/usr/bin/env python3
"""Pull Cloudflare account logs and send them to Splunk through Cribl's HEC input.

Streams (sourcetype, index):
  audit     cloudflare:audit (audit). Account audit log v2 (/accounts/{id}/logs/audit):
            every dashboard and API action on the account (API tokens, members, DNS,
            tunnels, Zero Trust, zone settings, logins), with the request and response
            bodies. Views are logged too, including this job's own reads of the log.
  access    cloudflare:access (audit). Zero Trust Access authentication log
            (/accounts/{id}/access/logs/access_requests).
  tunnel    cloudflare:tunnel (network). A snapshot of every Cloudflare Tunnel each
            run: status, connection count, colos, origin IPs, connector versions.
  firewall  cloudflare:firewall (network). Security events per zone (GraphQL
            firewallEventsAdaptive): action, source, client IP and country, host,
            path, rule, user agent.
  traffic   cloudflare:traffic (network). Hourly request, threat and byte totals per
            zone (GraphQL httpRequests1hGroups), once each hour is complete.

firewall and traffic need "Zone > Analytics > Read" on the token. Without it they log
status=skip (not fail) and pick up from their start once the permission is added.

audit, access and firewall keep a checkpoint in the state directory: the newest event
time sent, and the ids of the events sent in the overlap window. Each run asks again
from OVERLAP before the checkpoint, so late events are picked up, and skips ids it
already sent, so nothing is sent twice. A checkpoint only moves after Cribl accepted
the events, so a failed send is retried on the next run. The first run of a stream
goes back 24 hours (audit: 29 days, so the "first seen" detections have a baseline
from the start; a few hundred events). traffic keeps the last hour it asked for per
zone, whether or not that hour had requests, and never asks for more than 71 hours.

Event time is Cloudflare's timestamp (the snapshot time for tunnel). host is
"cloudflare"; source is cloudflare-logs:<stream>.

The API token, account ID and HEC token are read from the env file
(CLOUDFLARE_LOGS_API_TOKEN=, CLOUDFLARE_ACCOUNT_ID=, CRIBL_HEC_TOKEN=, only those
lines), sent only as request headers or in the API path, and never logged. Request
and response bodies in audit events lose any string under a key that names a secret
(secret, password, private key, credentials, a bare token or tunnel token) before
they are sent; API token values are not in the log (only a prefix and suffix).

Logs one line per stream to stdout (the journal under systemd, so index=infra
syslog.identifier=hl-cloudflare-logs):
  cloudflare logs: stream=<name> status=ok|skip|fail events=<n> error="<reason>"
"Cloudflare logs - not collected" (85_homelab_ops) alerts when audit, access or tunnel
has had no status=ok for an hour. Exits 1 if any stream failed.

Runs as hl-cloudflare-logs.service (systemd/), every 10 minutes from its timer.
Python 3 standard library only. By hand, a dry run that sends nothing, keeps no
state, and prints two redacted events per stream:
  cloudflare-logs.py --dry-run --state-dir /tmp/cf-state
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.cloudflare.com/client/v4"
HEC_URL = "https://127.0.0.1:8443/services/collector/event"
HEC_CA = "/srv/cribl-worker-tls/ca.crt"   # the homelab CA (scripts/tls-certs.sh)
TIMEOUT = 60
MAX_BYTES = 64 << 20
USER_AGENT = "homelab-cloudflare-logs/1 (+https://github.com/bmorri13/homelab)"

OVERLAP = dt.timedelta(minutes=30)   # asked again each run, for late events
BACKFILL = dt.timedelta(hours=24)    # how far back a stream's first run goes
AUDIT_BACKFILL = dt.timedelta(days=29)   # the audit log's first run: a baseline for the detections
FIREWALL_MAX_AGE = dt.timedelta(hours=23)   # GraphQL caps a query's range; stay under a day
TRAFFIC_MAX_AGE = dt.timedelta(hours=71)    # httpRequests1hGroups refuses ranges over 3 days
MAX_PAGES = 50                       # per stream per run; the rest waits for the next run
HEC_BATCH = 200                      # events per HEC request

STREAMS = {
    # name: (sourcetype, index)
    "audit": ("cloudflare:audit", "audit"),
    "access": ("cloudflare:access", "audit"),
    "tunnel": ("cloudflare:tunnel", "network"),
    "firewall": ("cloudflare:firewall", "network"),
    "traffic": ("cloudflare:traffic", "network"),
}

# Keys in audit request/response bodies whose string values are replaced.
SECRET_KEY = re.compile(r"(?i)(secret|passw|private_?key|credential|^token$|tunnel_?token)")

FIREWALL_FIELDS = ("datetime action source clientIP clientCountryName clientAsn clientASNDescription "
                   "clientRequestHTTPHost clientRequestHTTPMethodName clientRequestPath clientRequestQuery "
                   "ruleId rulesetId description rayName userAgent edgeColoName edgeResponseStatus kind")
FIREWALL_QUERY = ("query($zone: String!, $since: Time!, $until: Time!) { viewer { zones(filter: {zoneTag: $zone}) { "
                  "firewallEventsAdaptive(limit: 10000, filter: {datetime_geq: $since, datetime_lt: $until}, "
                  "orderBy: [datetime_ASC]) { " + FIREWALL_FIELDS + " } } } }")
TRAFFIC_QUERY = ("query($zone: String!, $since: Time!, $until: Time!) { viewer { zones(filter: {zoneTag: $zone}) { "
                 "httpRequests1hGroups(limit: 100, filter: {datetime_geq: $since, datetime_lt: $until}, "
                 "orderBy: [datetime_ASC]) { dimensions { datetime } "
                 "sum { requests cachedRequests encryptedRequests bytes cachedBytes threats pageViews } "
                 "uniq { uniques } } } } }")


class Skip(Exception):
    """The stream can't run yet for an expected reason (a missing permission)."""


def log(stream, status, events=0, error=""):
    error = re.sub(r"\b[0-9a-f]{32}\b", "<id>", str(error)) if error else ""   # account or zone IDs
    error = re.sub(r"[\"\r\n]+", " ", error)[:300] if error else "none"
    print(f'cloudflare logs: stream={stream} status={status} events={events} error="{error}"', flush=True)


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value):
    """A Cloudflare timestamp ('2026-11-15T22:41:38.807Z', with or without fraction or
    zone) as an aware datetime, or None."""
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def read_env(env_file, names):
    """The named KEY= lines from the env file, without sourcing it."""
    found = {}
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
                if m and m.group(1) in names:
                    found[m.group(1)] = m.group(2).strip("'\"") or None
    except OSError:
        pass
    return found


# ---- HTTP -------------------------------------------------------------------------------

def request(url, headers, body=None, context=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip", **headers}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=context) as resp:
            status, raw, enc = resp.status, resp.read(MAX_BYTES + 1), resp.headers.get("Content-Encoding")
    except urllib.error.HTTPError as e:
        status, raw, enc = e.code, e.read(MAX_BYTES + 1), e.headers.get("Content-Encoding")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {req.host}: {e.reason}") from None
    if len(raw) > MAX_BYTES:
        raise RuntimeError(f"response from {req.host} larger than {MAX_BYTES} bytes")
    if enc == "gzip":
        raw = gzip.decompress(raw)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        raise RuntimeError(f"HTTP {status} from {req.host}, not JSON") from None


class Cloudflare:
    def __init__(self, token, account):
        self.headers = {"Authorization": f"Bearer {token}"}
        self.account = account

    def get(self, path, params=None):
        """GET a v4 REST path ({acct} is the account ID); the whole JSON response."""
        url = API + path.replace("{acct}", urllib.parse.quote(self.account, safe=""))
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        status, data = request(url, self.headers)
        if status != 200 or not data.get("success", False):
            raise RuntimeError(f"HTTP {status} on {path.split('?')[0].replace('{acct}', '<account>')}: {errors(data)}")
        return data

    def graphql(self, query, variables):
        status, data = request(API + "/graphql", self.headers, {"query": query, "variables": variables})
        errs = data.get("errors") or []
        if any((e.get("extensions") or {}).get("code") == "authz" or "does not have permission" in str(e.get("message"))
               for e in errs):
            raise Skip("token lacks Zone > Analytics > Read")
        if status != 200 or errs or not data.get("data"):
            msg = "; ".join(str(e.get("message", e)) for e in errs) or f"HTTP {status}"
            raise RuntimeError(f"GraphQL: {re.sub(r'[0-9a-f]{32}', '<id>', msg)}")
        return data["data"]


def errors(data):
    errs = data.get("errors") or []
    return "; ".join(f"{e.get('code')} {e.get('message')}" for e in errs if isinstance(e, dict)) or "no detail"


# ---- state ----------------------------------------------------------------------------

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Checkpoint:
    """Newest event time sent and the ids sent since (checkpoint - OVERLAP), for one
    stream or one zone of a stream."""

    def __init__(self, data):
        data = data or {}
        self.since = parse_time(data.get("since"))
        self.seen = {k: v for k, v in (data.get("seen") or {}).items() if isinstance(v, str)}

    def start(self, max_age=BACKFILL + OVERLAP):
        """Where the next query starts: OVERLAP before the checkpoint, but never more
        than max_age ago (the first run, or after a long outage)."""
        floor = now() - max_age
        return max(self.since - OVERLAP, floor) if self.since else floor

    def new(self, events):
        """events: (id, time, payload); those not sent before, oldest first."""
        fresh = [e for e in events if e[0] not in self.seen]
        return sorted(fresh, key=lambda e: e[1])

    def advance(self, events):
        for key, t, _ in events:
            self.seen[key] = iso(t)
            if not self.since or t > self.since:
                self.since = t
        if self.since:
            oldest = iso(self.since - OVERLAP - dt.timedelta(minutes=5))
            self.seen = {k: v for k, v in self.seen.items() if v >= oldest}

    def dump(self):
        return {"since": iso(self.since) if self.since else None, "seen": self.seen}


# ---- redaction --------------------------------------------------------------------------

def scrub(value, key=""):
    """Replace strings under secret-looking keys in a request or response body."""
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if isinstance(value, str) and key and SECRET_KEY.search(key) and value:
        return "[REDACTED]"
    return value


# ---- streams: each returns [(id, time, event)] ------------------------------------------

def audit(cf, cp, until):
    out, cursor, start = [], None, cp.start(AUDIT_BACKFILL)
    for _ in range(MAX_PAGES):
        data = cf.get("/accounts/{acct}/logs/audit",
                      {"since": iso(start), "before": iso(until), "limit": 1000, "cursor": cursor})
        for r in data.get("result") or []:
            t = parse_time((r.get("action") or {}).get("time"))
            if not r.get("id") or not t:
                continue
            res = r.get("resource")
            if isinstance(res, dict):
                r = dict(r, resource={k: (scrub(v, k) if k in ("request", "response", "value") else v)
                                      for k, v in res.items()})
                # A rolled token's new value is the whole response body.
                if res.get("product") == "tokens" and isinstance(res.get("response"), str):
                    r["resource"]["response"] = "[REDACTED]"
            r.pop("account", None)   # the account ID and name: the same on every event
            out.append((r["id"], t, r))
        cursor = (data.get("result_info") or {}).get("cursor")
        if not cursor:
            return out
    raise RuntimeError(f"more than {MAX_PAGES} pages; the rest waits for the next run")


def access(cf, cp, until):
    out, since = [], cp.start()
    for _ in range(MAX_PAGES):
        data = cf.get("/accounts/{acct}/access/logs/access_requests",
                      {"since": iso(since), "until": iso(until), "limit": 1000, "direction": "asc"})
        rows = data.get("result") or []
        for r in rows:
            t = parse_time(r.get("created_at"))
            if not t:
                continue
            key = r.get("ray_id") or "|".join(str(r.get(k, "")) for k in ("created_at", "user_email", "app_uid", "ip_address"))
            out.append((f"{key}|{r.get('created_at')}", t, r))
        if len(rows) < 1000:
            return out
        newest = max((parse_time(r.get("created_at")) for r in rows), default=None)
        if not newest or newest <= since:
            raise RuntimeError("1000 events in one second; cannot page further")
        since = newest
    raise RuntimeError(f"more than {MAX_PAGES} pages; the rest waits for the next run")


def tunnels(cf):
    data = cf.get("/accounts/{acct}/cfd_tunnel", {"is_deleted": "false", "per_page": 1000})
    t, out = now(), []
    for r in data.get("result") or []:
        conns = r.get("connections") or []
        event = {
            "tunnel_id": r.get("id"), "tunnel_name": r.get("name"), "status": r.get("status"),
            "connections": len(conns),
            "pending_reconnect": sum(1 for c in conns if c.get("is_pending_reconnect")),
            "connectors": len({c.get("client_id") for c in conns if c.get("client_id")}),
            "colos": sorted({c.get("colo_name") for c in conns if c.get("colo_name")}),
            "origin_ips": sorted({c.get("origin_ip") for c in conns if c.get("origin_ip")}),
            "client_versions": sorted({c.get("client_version") for c in conns if c.get("client_version")}),
            "conns_active_at": r.get("conns_active_at"), "conns_inactive_at": r.get("conns_inactive_at"),
            "created_at": r.get("created_at"), "remote_config": r.get("remote_config"),
            "config_src": r.get("config_src"), "tun_type": r.get("tun_type"),
        }
        out.append((r.get("id"), t, event))
    return out


def zones(cf):
    data = cf.get("/zones", {"per_page": 50, "account.id": cf.account})
    return [(z["id"], z["name"]) for z in data.get("result") or [] if z.get("id") and z.get("name")]


def firewall(cf, zone_id, zone_name, cp, until):
    since = cp.start(FIREWALL_MAX_AGE)
    data = cf.graphql(FIREWALL_QUERY, {"zone": zone_id, "since": iso(since), "until": iso(until)})
    rows = ((data.get("viewer") or {}).get("zones") or [{}])[0].get("firewallEventsAdaptive") or []
    out = []
    for r in rows:
        t = parse_time(r.get("datetime"))
        if not t:
            continue
        r = dict(r, zone=zone_name)
        key = "|".join(str(r.get(k, "")) for k in ("rayName", "datetime", "ruleId", "action", "source"))
        out.append((key, t, r))
    if len(rows) >= 10000:
        raise RuntimeError(f"zone {zone_name}: 10000 events in one query, some may be missing")
    return out


def traffic(cf, zone_id, zone_name, last_hour, until):
    """Complete hours after last_hour (or the last BACKFILL) for one zone, and the last
    hour asked for. A zone with no requests returns no rows, so the caller keeps that
    hour rather than the newest row: otherwise a quiet zone's range grows each run until
    GraphQL refuses it (bmosanlabs.com, 2026-11-20). The range is capped at
    TRAFFIC_MAX_AGE for the same reason, so a long outage loses its oldest hours instead
    of failing for good."""
    # The start of the current hour, or of the last one while its totals may still be
    # arriving (the first 15 minutes after it ends).
    hour = (until - dt.timedelta(minutes=15)).replace(minute=0, second=0, microsecond=0)
    start = last_hour + dt.timedelta(hours=1) if last_hour else hour - BACKFILL
    start = max(start, hour - TRAFFIC_MAX_AGE)
    if start >= hour:
        return [], last_hour
    data = cf.graphql(TRAFFIC_QUERY, {"zone": zone_id, "since": iso(start), "until": iso(hour)})
    rows = ((data.get("viewer") or {}).get("zones") or [{}])[0].get("httpRequests1hGroups") or []
    out = []
    for r in rows:
        t = parse_time((r.get("dimensions") or {}).get("datetime"))
        if not t:
            continue
        event = {"zone": zone_name, "hour": iso(t), **(r.get("sum") or {}), **(r.get("uniq") or {})}
        out.append((f"{zone_name}|{iso(t)}", t, event))
    return out, hour - dt.timedelta(hours=1)


# ---- output -----------------------------------------------------------------------------

def hec_events(stream, events):
    sourcetype, index = STREAMS[stream]
    return [{"time": round(t.timestamp(), 3), "host": "cloudflare", "source": f"cloudflare-logs:{stream}",
             "sourcetype": sourcetype, "index": index, "event": e} for _, t, e in events]


class Sender:
    def __init__(self, token, dry_run, samples, account):
        self.token, self.dry_run, self.samples, self.account = token, dry_run, samples, account
        self.context = None if dry_run else ssl.create_default_context(cafile=HEC_CA)

    def send(self, stream, events):
        payload = hec_events(stream, events)
        if self.dry_run:
            for e in payload[:self.samples]:
                print(f"[dry-run {stream}] {self.redact(json.dumps(e, sort_keys=True))}", flush=True)
            return
        for i in range(0, len(payload), HEC_BATCH):
            body = "\n".join(json.dumps(e, separators=(",", ":")) for e in payload[i:i + HEC_BATCH]).encode()
            status, data = request(HEC_URL, {"Authorization": f"Splunk {self.token}"}, body, self.context)
            if status != 200 or data.get("code", 0) != 0:
                raise RuntimeError(f"HEC: HTTP {status} {str(data.get('text', ''))[:100]}")

    def redact(self, text):
        """For dry-run output only: no account ID, emails or IP addresses."""
        if self.account:
            text = text.replace(self.account, "<account>")
        text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<user>", text)
        return re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", text)


# ---- main -------------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--state-dir", default=os.environ.get("STATE_DIRECTORY", "/var/lib/hl-cloudflare-logs"))
    p.add_argument("--env-file", default="/opt/splunk-stack/.env",
                   help="file with the CLOUDFLARE_LOGS_API_TOKEN=, CLOUDFLARE_ACCOUNT_ID= and CRIBL_HEC_TOKEN= lines")
    p.add_argument("--streams", default=",".join(STREAMS), help="comma-separated subset of " + ",".join(STREAMS))
    p.add_argument("--dry-run", action="store_true", help="send nothing, save no state, print redacted samples")
    p.add_argument("--samples", type=int, default=2, help="events printed per stream in a dry run")
    args = p.parse_args()

    wanted = [s.strip() for s in args.streams.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in STREAMS]
    if unknown:
        p.error(f"unknown stream(s): {', '.join(unknown)}")
    env = read_env(args.env_file, {"CLOUDFLARE_LOGS_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CRIBL_HEC_TOKEN"})
    missing = [k for k in ("CLOUDFLARE_LOGS_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID") + (() if args.dry_run else ("CRIBL_HEC_TOKEN",))
               if not env.get(k)]
    if missing:
        for s in wanted:
            log(s, "fail", error=f"no {', '.join(missing)} in {args.env_file}")
        return 1

    cf = Cloudflare(env["CLOUDFLARE_LOGS_API_TOKEN"], env["CLOUDFLARE_ACCOUNT_ID"])
    sender = Sender(env.get("CRIBL_HEC_TOKEN"), args.dry_run, args.samples, env["CLOUDFLARE_ACCOUNT_ID"])
    state_path = os.path.join(args.state_dir, "state.json")
    if not args.dry_run:
        os.makedirs(args.state_dir, mode=0o700, exist_ok=True)
    state = load_state(state_path)
    until = now() - dt.timedelta(seconds=30)
    failed = False

    def commit():
        if not args.dry_run:
            save_state(state_path, state)

    zone_list = None
    for stream in wanted:
        try:
            if stream in ("audit", "access"):
                cp = Checkpoint(state.get(stream))
                events = cp.new((audit if stream == "audit" else access)(cf, cp, until))
                sender.send(stream, events)
                cp.advance(events)
                state[stream] = cp.dump()
                commit()
                log(stream, "ok", len(events))
            elif stream == "tunnel":
                events = tunnels(cf)
                sender.send(stream, events)
                log(stream, "ok", len(events))
            else:
                if zone_list is None:
                    zone_list = zones(cf)
                total, errs, skipped, skip_reason = 0, [], 0, ""
                zstate = state.setdefault(stream, {})
                for zone_id, zone_name in zone_list:
                    try:
                        if stream == "firewall":
                            cp = Checkpoint(zstate.get(zone_name))
                            events = cp.new(firewall(cf, zone_id, zone_name, cp, until))
                            sender.send(stream, events)
                            cp.advance(events)
                            zstate[zone_name] = cp.dump()
                        else:
                            last = parse_time(zstate.get(zone_name))
                            events, asked = traffic(cf, zone_id, zone_name, last, until)
                            sender.send(stream, events)
                            if asked:
                                zstate[zone_name] = iso(asked)
                        commit()
                        total += len(events)
                    except Skip as e:
                        skipped += 1
                        skip_reason = str(e)
                    except Exception as e:  # one zone's failure never stops the others
                        errs.append(f"{zone_name}: {e}")
                if errs:
                    log(stream, "fail", total, "; ".join(errs))
                    failed = True
                elif zone_list and skipped == len(zone_list):
                    log(stream, "skip", 0, skip_reason)
                else:
                    log(stream, "ok", total, f"{skipped} zone(s) skipped: {skip_reason}" if skipped else "")
        except Skip as e:
            log(stream, "skip", 0, e)
        except Exception as e:  # one stream's failure never stops the others
            log(stream, "fail", error=e)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
