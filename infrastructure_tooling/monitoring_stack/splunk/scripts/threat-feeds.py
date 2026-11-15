#!/usr/bin/env python3
"""Download free threat-intel feeds into lookups for the 85_detections app.

Feeds:
  threatfox  abuse.ch ThreatFox, IOCs of the last day: ip:port (kept as the IP) and
             domain, confidence 50 or more. Needs ABUSECH_AUTH_KEY.
  urlhaus    abuse.ch URLhaus, the most recent malware URLs that are still online:
             their host (a domain or an IP). Needs ABUSECH_AUTH_KEY.
  feodo      abuse.ch Feodo Tracker botnet C2 IP blocklist: online, or last online
             within 30 days. No key.
  kev        CISA Known Exploited Vulnerabilities catalog. No key.

Writes, into the app's lookups directory:
  hl_ti_feed_indicators.csv  indicator,type,source,context,added,expires: one row per
                             indicator across the three abuse.ch feeds, the same
                             columns as hl_threat_indicators.csv
  hl_ti_feed_kev.csv         cve,vendor,product,name,date_added,due_date,ransomware

Each feed's rows are kept in the state directory and merged with every new download:
an indicator stays until TTL_DAYS after it was last seen in its feed, so a run that
fetches one day of IOCs still yields the last month. A feed's state (and so its part
of the lookup) is only replaced when its download parsed and has at least the feed's
minimum number of rows; otherwise the last good copy stays. Files are written to a
temporary name in the same directory, then renamed over the old one.

The abuse.ch Auth-Key is read from the env file (ABUSECH_AUTH_KEY=, only that line) and
sent only as a request header; it is never logged or put on a command line.

Logs one line per feed to stdout (the journal under systemd, so index=infra
syslog.identifier=hl-threat-feeds):
  threat feed: feed=<name> status=ok|fail rows=<n> error="<reason>"
feed=lookup reports writing hl_ti_feed_indicators.csv. "Threat feeds - not refreshed"
(85_homelab_ops) alerts when a feed has had no status=ok line for 24 hours.
Exits 1 if any feed failed.

Runs as hl-threat-feeds.service (systemd/), every 6 hours from its timer. Python 3
standard library only. By hand, for a test that touches nothing of Splunk's:
  threat-feeds.py --lookups-dir /tmp/x --state-dir /tmp/x/state --feeds feodo,kev
"""
import argparse
import csv
import datetime as dt
import gzip
import ipaddress
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request

TTL_DAYS = 30            # an indicator expires this long after it was last seen
MIN_CONFIDENCE = 50      # ThreatFox confidence_level, 0-100
MAX_BYTES = 64 << 20     # largest response read (KEV is about 2 MB)
TIMEOUT = 60
USER_AGENT = "homelab-threat-feeds/1 (+https://github.com/bmorri13/homelab)"

INDICATOR_FIELDS = ["indicator", "type", "source", "context", "added", "expires"]
STATE_FIELDS = ["indicator", "type", "context", "first_seen", "last_seen"]
KEV_FIELDS = ["cve", "vendor", "product", "name", "date_added", "due_date", "ransomware"]
INDICATOR_LOOKUP = "hl_ti_feed_indicators.csv"
KEV_LOOKUP = "hl_ti_feed_kev.csv"

# Always in hl_ti_feed_indicators.csv, so the feed detections can be tested end to end:
# an RFC 5737 documentation address and a name under the reserved .invalid TLD, neither
# of which is ever a real host. The weekly art_validation host test logs the address.
CANARIES = [
    {"indicator": "198.51.100.74", "type": "ip", "source": "art_validation",
     "context": "Detection test canary for the feed detections; never a real host",
     "added": "2026-11-15", "expires": "2099-12-31"},
    {"indicator": "hl-feed-canary.invalid", "type": "domain", "source": "art_validation",
     "context": "Detection test canary for the feed detections; reserved TLD that never resolves",
     "added": "2026-11-15", "expires": "2099-12-31"},
]

# Domains that are never indicators here: our own, and big shared platforms whose
# hosts show up in URLhaus because someone uploaded malware to them (a file on GitHub,
# Google Drive or Discord's CDN is not the platform's fault, and blocking on the
# platform would flag ordinary traffic). A feed host equal to or under one of these is
# dropped. Specific hosts on dynamic-DNS and tunnel services (x.duckdns.org) are kept.
IGNORED_DOMAINS = {
    "bmosan.com", "ts.net",
    "github.com", "githubusercontent.com", "github.io", "gitlab.com", "bitbucket.org",
    "google.com", "googleapis.com", "googleusercontent.com", "gstatic.com", "goo.gl",
    "microsoft.com", "live.com", "office.com", "sharepoint.com", "1drv.ms", "windows.net",
    "azureedge.net", "azurewebsites.net", "msedge.net",
    "amazonaws.com", "cloudfront.net", "amazon.com",
    "apple.com", "icloud.com",
    "dropbox.com", "dropboxusercontent.com", "box.com", "mediafire.com", "mega.nz",
    "discord.com", "discordapp.com", "discordapp.net", "telegram.org", "t.me",
    "cloudflare.com", "workers.dev", "pages.dev", "r2.dev", "akamaized.net", "fastly.net",
    "facebook.com", "fbcdn.net", "twitter.com", "x.com", "linkedin.com",
    "archive.org", "sourceforge.net", "wikipedia.org", "yandex.ru", "yandex.net",
}

HOSTNAME_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")


def log(feed, status, rows=0, error=""):
    error = re.sub(r"[\"\r\n]+", " ", str(error))[:300] if error else "none"
    print(f'threat feed: feed={feed} status={status} rows={rows} error="{error}"', flush=True)


def today():
    return dt.datetime.now(dt.timezone.utc).date()


def to_date(value):
    """The date part of '2026-11-15 12:00:00 UTC' or '2026-11-15', or None."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def clean(text, limit=120):
    """A lookup field: no commas, quotes or control characters (README, Detections)."""
    text = re.sub(r"[,\"\x00-\x1f]+", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def read_key(env_file):
    """ABUSECH_AUTH_KEY from the env file, without sourcing it. None if missing."""
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?ABUSECH_AUTH_KEY\s*=\s*(.*?)\s*$", line)
                if m:
                    return m.group(1).strip("'\"") or None
    except OSError:
        return None
    return None


def fetch_json(url, key=None, body=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"}
    if key:
        headers["Auth-Key"] = key
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError(f"response larger than {MAX_BYTES} bytes")
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {req.host}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {req.host}: {e.reason}") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError(f"response from {req.host} is not JSON") from None


def public_ipv4(value):
    """The address if it is a public IPv4 address, else None. The detections only
    match IPv4, and private, reserved, CGNAT and documentation ranges are never C2."""
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    return str(ip) if ip.version == 4 and ip.is_global else None


def feed_domain(value):
    """A cleaned hostname worth matching, else None: 2 to 4 labels (what
    hl_dns_name_candidates can match), not ours and not on a shared platform."""
    host = str(value or "").strip().lower().rstrip(".")
    if not HOSTNAME_RE.match(host) or public_ipv4(host):
        return None
    labels = host.split(".")
    if not 2 <= len(labels) <= 4:
        return None
    if any(".".join(labels[i:]) in IGNORED_DOMAINS for i in range(len(labels))):
        return None
    return host


def indicator(value):
    """(indicator, type) for an IP or hostname, or None."""
    ip = public_ipv4(value)
    if ip:
        return ip, "ip"
    host = feed_domain(value)
    return (host, "domain") if host else None


# ---- feeds: each returns a list of state rows, or raises ------------------------------

def threatfox(key):
    data = fetch_json("https://threatfox-api.abuse.ch/api/v1/", key, {"query": "get_iocs", "days": 1})
    if data.get("query_status") != "ok" or not isinstance(data.get("data"), list):
        raise RuntimeError(f"query_status={data.get('query_status')}")
    rows = []
    for ioc in data["data"]:
        kind = ioc.get("ioc_type")
        if kind not in ("ip:port", "domain") or int(ioc.get("confidence_level") or 0) < MIN_CONFIDENCE:
            continue
        value = str(ioc.get("ioc") or "")
        if kind == "ip:port":
            value = value.rsplit(":", 1)[0]
        found = indicator(value)
        if not found or found[1] != ("ip" if kind == "ip:port" else "domain"):
            continue
        seen = to_date(ioc.get("last_seen")) or to_date(ioc.get("first_seen")) or today()
        context = " ".join(filter(None, [ioc.get("malware_printable"), ioc.get("threat_type")]))
        rows.append({"indicator": found[0], "type": found[1], "context": clean(context),
                     "first_seen": to_date(ioc.get("first_seen")) or seen, "last_seen": seen})
    return rows


def urlhaus(key):
    data = fetch_json("https://urlhaus-api.abuse.ch/v1/urls/recent/limit/1000/", key)
    if data.get("query_status") != "ok" or not isinstance(data.get("urls"), list):
        raise RuntimeError(f"query_status={data.get('query_status')}")
    rows = []
    for url in data["urls"]:
        if url.get("url_status") != "online":
            continue
        found = indicator(url.get("host"))
        if not found:
            continue
        seen = to_date(url.get("date_added")) or today()
        tags = [t.strip() for t in (url.get("tags") or []) if isinstance(t, str) and t.strip()][:3]
        context = " ".join(filter(None, [url.get("threat"), "/".join(tags)]))
        rows.append({"indicator": found[0], "type": found[1], "context": clean(context),
                     "first_seen": seen, "last_seen": today()})
    return rows


def feodo(_key):
    data = fetch_json("https://feodotracker.abuse.ch/downloads/ipblocklist.json")
    if not isinstance(data, list):
        raise RuntimeError("not a JSON list")
    oldest = today() - dt.timedelta(days=TTL_DAYS)
    rows = []
    for entry in data:
        ip = public_ipv4(entry.get("ip_address"))
        if not ip:
            continue
        seen = today() if entry.get("status") == "online" else to_date(entry.get("last_online"))
        if not seen or seen < oldest:
            continue
        rows.append({"indicator": ip, "type": "ip", "context": clean(f"{entry.get('malware') or 'botnet'} botnet C2"),
                     "first_seen": to_date(entry.get("first_seen")) or seen, "last_seen": seen})
    return rows


def kev(_key):
    data = fetch_json("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
    vulns = data.get("vulnerabilities") if isinstance(data, dict) else None
    if not isinstance(vulns, list):
        raise RuntimeError("no vulnerabilities list")
    rows = {}
    for v in vulns:
        cve = str(v.get("cveID") or "").strip().upper()
        if not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve):
            continue
        rows[cve] = {"cve": cve, "vendor": clean(v.get("vendorProject"), 60), "product": clean(v.get("product"), 60),
                     "name": clean(v.get("vulnerabilityName"), 160),
                     "date_added": to_date(v.get("dateAdded")) or "", "due_date": to_date(v.get("dueDate")) or "",
                     "ransomware": "Known" if v.get("knownRansomwareCampaignUse") == "Known" else "Unknown"}
    return sorted(rows.values(), key=lambda r: r["cve"])


# name: (function, needs the abuse.ch key, source label, minimum rows to accept)
FEEDS = {
    "threatfox": (threatfox, True, "abuse.ch ThreatFox", 20),
    "urlhaus": (urlhaus, True, "abuse.ch URLhaus", 20),
    "feodo": (feodo, False, "abuse.ch Feodo Tracker", 0),   # often near empty now
    "kev": (kev, False, "CISA KEV", 1000),
}


# ---- files ----------------------------------------------------------------------------

def write_csv(path, fields, rows, owner=None):
    """Write rows to path through a temporary file in the same directory and a rename.
    The temporary name starts like the lookup (hl_ti_feed_), so deploy.sh's rsync
    exclude covers it too."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path).split(".")[0] + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        if owner:
            os.chown(tmp, *owner)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_state(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def merge(old, new):
    """Old state plus a new download, one row per indicator and type: earliest first
    seen, latest last seen and its context. Rows not seen for TTL_DAYS are dropped."""
    merged = {}
    for r in old + new:
        r = {k: str(r.get(k) or "") for k in STATE_FIELDS}
        if not to_date(r["last_seen"]) or not to_date(r["first_seen"]):
            continue
        k = (r["indicator"], r["type"])
        cur = merged.get(k)
        if cur is None:
            merged[k] = r
            continue
        cur["first_seen"] = min(cur["first_seen"], r["first_seen"])
        if r["last_seen"] >= cur["last_seen"]:
            cur["last_seen"], cur["context"] = r["last_seen"], r["context"] or cur["context"]
    oldest = (today() - dt.timedelta(days=TTL_DAYS)).isoformat()
    return sorted((r for r in merged.values() if r["last_seen"] >= oldest), key=lambda r: (r["type"], r["indicator"]))


def build_indicators(state_dir):
    """Every abuse.ch feed's state as lookup rows, one per indicator: sources joined
    with '; ', contexts with ' | ', the earliest added and the latest expiry."""
    combined = {}
    for name, (_, _, label, _) in FEEDS.items():
        if name == "kev":
            continue
        for r in read_state(os.path.join(state_dir, f"{name}.csv")):
            last = to_date(r.get("last_seen"))
            if not last or not r.get("indicator"):
                continue
            k = (r["indicator"], r["type"])
            row = combined.setdefault(k, {"indicator": r["indicator"], "type": r["type"], "sources": [], "contexts": [],
                                          "added": r["first_seen"], "expires": ""})
            if label not in row["sources"]:
                row["sources"].append(label)
            short = label.removeprefix("abuse.ch ").split()[0]   # ThreatFox, URLhaus, Feodo
            context = f"{short}: {r['context']}" if r.get("context") else ""
            if context and context not in row["contexts"]:
                row["contexts"].append(context)
            row["added"] = min(row["added"], r["first_seen"])
            row["expires"] = max(row["expires"], (last + dt.timedelta(days=TTL_DAYS)).isoformat())
    rows = [{"indicator": r["indicator"], "type": r["type"], "source": "; ".join(r["sources"]),
             "context": " | ".join(r["contexts"])[:250], "added": r["added"], "expires": r["expires"]}
            for r in combined.values()]
    rows.sort(key=lambda r: (r["type"], r["indicator"]))
    return rows + CANARIES


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lookups-dir", required=True, help="the 85_detections app's lookups directory")
    p.add_argument("--state-dir", default=os.environ.get("STATE_DIRECTORY", "/var/lib/hl-threat-feeds"))
    p.add_argument("--env-file", default="/opt/splunk-stack/.env", help="file with the ABUSECH_AUTH_KEY= line")
    p.add_argument("--feeds", default=",".join(FEEDS), help="comma-separated subset of " + ",".join(FEEDS))
    args = p.parse_args()

    wanted = [f.strip() for f in args.feeds.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in FEEDS]
    if unknown:
        p.error(f"unknown feed(s): {', '.join(unknown)}")
    os.makedirs(args.state_dir, mode=0o700, exist_ok=True)
    st = os.stat(args.lookups_dir)
    owner = (st.st_uid, st.st_gid) if os.geteuid() == 0 else None   # splunkd must read the files

    key = read_key(args.env_file) if any(FEEDS[f][1] for f in wanted) else None
    failed = False
    for name in wanted:
        fn, needs_key, _, min_rows = FEEDS[name]
        if needs_key and not key:
            log(name, "fail", error=f"no ABUSECH_AUTH_KEY in {args.env_file}")
            failed = True
            continue
        try:
            rows = fn(key if needs_key else None)
            if len(rows) < min_rows:
                raise RuntimeError(f"only {len(rows)} usable rows (minimum {min_rows}); kept the last good copy")
            if name == "kev":
                write_csv(os.path.join(args.lookups_dir, KEV_LOOKUP), KEV_FIELDS, rows, owner)
            else:
                path = os.path.join(args.state_dir, f"{name}.csv")
                rows = merge(read_state(path), rows)
                write_csv(path, STATE_FIELDS, rows)
            log(name, "ok", len(rows))
        except Exception as e:  # one feed's failure never stops the others
            log(name, "fail", error=e)
            failed = True

    # Rebuilt every run from the feeds' states, so a failed feed keeps its last good rows.
    try:
        rows = build_indicators(args.state_dir)
        write_csv(os.path.join(args.lookups_dir, INDICATOR_LOOKUP), INDICATOR_FIELDS, rows, owner)
        log("lookup", "ok", len(rows))
    except Exception as e:
        log("lookup", "fail", error=e)
        failed = True
    # KEV's lookup must exist even before its first good download, or the searches
    # that use it error.
    kev_path = os.path.join(args.lookups_dir, KEV_LOOKUP)
    if not os.path.exists(kev_path):
        write_csv(kev_path, KEV_FIELDS, [], owner)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
