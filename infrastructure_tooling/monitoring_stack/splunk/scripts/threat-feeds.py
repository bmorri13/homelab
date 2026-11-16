#!/usr/bin/env python3
"""Download free threat-intel feeds into lookups for the 85_detections app.

Feeds:
  threatfox    abuse.ch ThreatFox, IOCs of the last day: ip:port (kept as the IP) and
               domain, confidence 50 or more. Needs ABUSECH_AUTH_KEY.
  urlhaus      abuse.ch URLhaus, the most recent malware URLs that are still online:
               their host (a domain or an IP). Needs ABUSECH_AUTH_KEY.
  feodo        abuse.ch Feodo Tracker botnet C2 IP blocklist: online, or last online
               within 30 days. No key.
  openphish    OpenPhish community feed (free for non-commercial use): the host of
               each phishing URL. No key.
  circl        CIRCL OSINT feed (MISP feed format): events dated in the last 30 days,
               newest first, their to_ids IP, domain, hostname and URL-host attributes,
               at most 60 events and 20,000 rows a run. No key.
  tor          Tor Project bulk exit list. Low tier. No key.
  blocklistde  blocklist.de all.txt, IPs reported for SSH, mail and web brute force in
               the last 48 hours. Low tier. No key.
  kev          CISA Known Exploited Vulnerabilities catalog. No key.

Writes, into the app's lookups directory:
  hl_ti_feed_indicators.csv  indicator,type,source,context,added,expires: one row per
                             indicator across threatfox, urlhaus, feodo, openphish and
                             circl, the same columns as hl_threat_indicators.csv. The
                             medium-severity feed detections read it.
  hl_ti_feed_lowrisk.csv     indicator,type,category,source,context,added,expires: Tor
                             exits (category tor) and blocklist.de (category attacker).
                             Not malicious by themselves, so only low-severity
                             detections of their own read it.
  hl_ti_feed_kev.csv         cve,vendor,product,name,date_added,due_date,ransomware

Each feed's rows are kept in the state directory and merged with every new download:
an indicator stays until the feed's TTL after it was last seen in its feed (30 days;
3 for the churning tor and blocklistde lists, 7 for short-lived phishing hosts), so a
run that fetches one day of IOCs still yields the last month. A feed's state (and so
its part of the lookup) is only replaced when its download parsed and has at least the
feed's minimum number of rows; otherwise the last good copy stays. Files are written
to a temporary name in the same directory, then renamed over the old one.

Before the indicator lookups are written, indicators that MISP warninglists
(https://github.com/MISP/misp-warninglists, CC0) know as benign are dropped: public DNS
resolvers, CDN and edge ranges, sinkholes, research scanners, the most popular domains
(exact names only), and the like (WARNINGLISTS). The lists are cached in the state
directory and only downloaded again when the repository's main branch moves; if that
fails, the cached copy is used. The canary rows are never dropped.

The abuse.ch Auth-Key is read from the env file (ABUSECH_AUTH_KEY=, only that line) and
sent only as a request header; it is never logged or put on a command line.

Logs one line per feed to stdout (the journal under systemd, so index=infra
syslog.identifier=hl-threat-feeds):
  threat feed: feed=<name> status=ok|fail rows=<n> error="<reason>"
feed=lookup and feed=lookup_lowrisk report writing the two indicator lookups, with
dropped=<n>, the indicators a warninglist removed. feed=warninglists reports the lists
in use: rows=<lists loaded> dropped=<n, both lookups> sha=<commit>. "Threat feeds - not
refreshed" (85_homelab_ops) alerts when a feed has had no status=ok line for 24 hours.
Exits 1 if any feed failed.

Runs as hl-threat-feeds.service (systemd/), every 6 hours from its timer. Python 3
standard library only. By hand, for a test that touches nothing of Splunk's
(--show-dropped also prints each indicator a warninglist removed, and which list):
  threat-feeds.py --lookups-dir /tmp/x --state-dir /tmp/x/state --feeds feodo,tor,kev
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
import urllib.parse
import urllib.request
from collections import namedtuple

TTL_DAYS = 30            # an indicator expires this long after it was last seen (FEEDS
                         # sets a shorter one for some feeds)
MIN_CONFIDENCE = 50      # ThreatFox confidence_level, 0-100
MAX_BYTES = 64 << 20     # largest response read (KEV is about 2 MB)
TIMEOUT = 60
USER_AGENT = "homelab-threat-feeds/1 (+https://github.com/bmorri13/homelab)"

INDICATOR_FIELDS = ["indicator", "type", "source", "context", "added", "expires"]
STATE_FIELDS = ["indicator", "type", "context", "first_seen", "last_seen"]
KEV_FIELDS = ["cve", "vendor", "product", "name", "date_added", "due_date", "ransomware"]
LOWRISK_FIELDS = ["indicator", "type", "category", "source", "context", "added", "expires"]
INDICATOR_LOOKUP = "hl_ti_feed_indicators.csv"
LOWRISK_LOOKUP = "hl_ti_feed_lowrisk.csv"
KEV_LOOKUP = "hl_ti_feed_kev.csv"

CIRCL_FEED = "https://www.circl.lu/doc/misp/feed-osint/"
CIRCL_MAX_EVENTS = 60          # newest first; each is one request
CIRCL_MAX_ATTRIBUTES = 5000    # per event (CIRCL's daily Maltrail events reach it)
CIRCL_MAX_ROWS = 20000         # per run, newest events first: keeps the lookup a few MB
CIRCL_EVENT_BYTES = 16 << 20
CIRCL_TYPES = {"ip-dst", "ip-src", "ip-dst|port", "domain", "hostname", "domain|ip", "url"}

# MISP warninglists used to drop known-benign indicators. Only infrastructure where a
# feed listing is a shared front or a known-good actor, never whole hosting providers
# (AWS, DigitalOcean, Hetzner and the like), where most real C2 runs: so not akamai
# (it includes Linode's compute ranges) or github (it includes Azure ranges for Actions
# runners). Matching follows
# each list's type: cidr (IPv4 networks), hostname (the name or any parent of it),
# string (the exact name). Popular-domain lists are the string-typed top 10k ones, so
# x.vercel.app or x.duckdns.org stays even though vercel.app is popular; tranco10k and
# lots-project are hostname-typed and would drop those, so they are not used, nor are
# dynamic-dns and google (Blogspot, Firebase and other user-content platforms).
# Besides these, every list named *-scanning, *-nt-scanning or *-scanner (Shodan,
# Censys, Shadowserver and other research scanners) is used.
WARNINGLIST_REPO = "MISP/misp-warninglists"
WARNINGLISTS = {
    # resolvers, CDN and edge ranges, proxies, vendor egress
    "public-dns-v4", "public-dns-hostname", "cloudflare", "fastly", "cdn77",
    "cachefly", "bunny-net", "imperva", "sucuri", "zscaler", "stackpath", "googlebot",
    "microsoft-office365-ip", "apple", "apple-ipv4", "telegram-ips", "wikimedia",
    "microsoft", "microsoft-office365", "apple-domains",
    # sinkholes, block pages, certificate revocation, reserved ranges and names
    "sinkholes", "umbrella-blockpage-v4", "umbrella-blockpage-hostname", "crl-ip",
    "crl-hostname", "rfc1918", "rfc5735", "rfc6598", "multicast", "rfc6761",
    "shadowserver",
    # benign names that feeds sometimes list: popular sites (exact), public suffixes,
    # vendor blogs, what's-my-IP services, URL shorteners, IPFS gateways
    "cisco_top10k", "cloudflare-top10k", "second-level-tlds", "security-provider-blogpost",
    "whats-my-ip", "url-shortener", "public-ipfs-gateways",
}
WARNINGLIST_SCANNERS = re.compile(r"-(?:nt-)?scann(?:ing|er)$")
WARNINGLIST_CACHE = "warninglists.json"
MIN_WARNINGLISTS = 30    # fewer usable lists than this is a broken download

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
# Microsoft's and Apple's own domains and goo.gl come from the warninglists (microsoft,
# apple-domains, url-shortener) instead.
IGNORED_DOMAINS = {
    "bmosan.com", "ts.net",
    "github.com", "githubusercontent.com", "github.io", "gitlab.com", "bitbucket.org",
    "google.com", "googleapis.com", "googleusercontent.com", "gstatic.com",
    "1drv.ms", "azureedge.net", "azurewebsites.net", "msedge.net",
    "amazonaws.com", "cloudfront.net", "amazon.com",
    "dropbox.com", "dropboxusercontent.com", "box.com", "mediafire.com", "mega.nz",
    "discord.com", "discordapp.com", "discordapp.net", "telegram.org", "t.me",
    "cloudflare.com", "workers.dev", "pages.dev", "r2.dev", "akamaized.net", "fastly.net",
    "facebook.com", "fbcdn.net", "twitter.com", "x.com", "linkedin.com",
    "archive.org", "sourceforge.net", "wikipedia.org", "yandex.ru", "yandex.net",
}

HOSTNAME_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")


def log(feed, status, rows=0, error="", **extra):
    error = re.sub(r"[\"\r\n]+", " ", str(error))[:300] if error else "none"
    kv = "".join(f" {k}={v}" for k, v in extra.items())
    print(f'threat feed: feed={feed} status={status} rows={rows}{kv} error="{error}"', flush=True)


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


def fetch(url, key=None, body=None, accept="application/json", max_bytes=MAX_BYTES):
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip"}
    if key:
        headers["Auth-Key"] = key
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError(f"response larger than {max_bytes} bytes")
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {req.host}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {req.host}: {e.reason}") from None
    return raw, req.host


def fetch_json(url, key=None, body=None, max_bytes=MAX_BYTES):
    raw, host = fetch(url, key, body, max_bytes=max_bytes)
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError(f"response from {host} is not JSON") from None


def fetch_lines(url):
    """A plain-text list, one entry per line; an HTML page (an error or login page
    served with 200) is refused."""
    raw, host = fetch(url, accept="text/plain")
    text = raw.decode("utf-8", "replace")
    if text.lstrip()[:1] == "<":
        raise RuntimeError(f"response from {host} is HTML, not a list")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


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


def url_host(url):
    """The host of a URL, or None."""
    try:
        return urllib.parse.urlsplit(str(url).strip()).hostname
    except ValueError:
        return None


def state_row(found, context, first=None, last=None):
    last = last or today()
    return {"indicator": found[0], "type": found[1], "context": clean(context),
            "first_seen": first or last, "last_seen": last}


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


def openphish(_key):
    rows = []
    for url in fetch_lines("https://openphish.com/feed.txt"):
        found = indicator(url_host(url)) if url.startswith(("http://", "https://")) else None
        if found:
            rows.append(state_row(found, "phishing URL"))
    return rows


def circl(_key, days=TTL_DAYS):
    manifest = fetch_json(CIRCL_FEED + "manifest.json")
    if not isinstance(manifest, dict):
        raise RuntimeError("manifest is not a JSON object")
    oldest = today() - dt.timedelta(days=days)
    events = []
    for uuid, ev in manifest.items():
        if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", str(uuid)) \
                or not isinstance(ev, dict):
            continue
        date = to_date(ev.get("date"))
        if date and oldest <= date <= today():
            events.append((date, str(ev.get("timestamp") or ""), uuid))
    events.sort(reverse=True)
    rows, errors = [], []
    for date, _, uuid in events[:CIRCL_MAX_EVENTS]:
        if len(rows) >= CIRCL_MAX_ROWS:
            break
        try:
            event = fetch_json(f"{CIRCL_FEED}{uuid}.json", max_bytes=CIRCL_EVENT_BYTES).get("Event")
            if not isinstance(event, dict):
                raise RuntimeError("no Event")
        except Exception as e:  # one bad event never loses the others
            errors.append(str(e))
            continue
        attributes = list(event.get("Attribute") or [])
        for obj in event.get("Object") or []:
            if isinstance(obj, dict):
                attributes += obj.get("Attribute") or []
        context = clean(event.get("info"), 80)
        for a in attributes[:CIRCL_MAX_ATTRIBUTES]:
            if not isinstance(a, dict) or a.get("to_ids") not in (True, 1, "1") or a.get("type") not in CIRCL_TYPES:
                continue
            kind, value = a["type"], str(a.get("value") or "")
            if kind == "url":
                values = [url_host(value)]
            elif kind == "domain|ip":
                values = value.split("|")
            elif kind == "ip-dst|port":
                values = [value.split("|")[0]]
            else:
                values = [value]
            want = {"domain|ip": ("ip", "domain"), "url": ("ip", "domain"),
                    "domain": ("domain",), "hostname": ("domain",)}.get(kind, ("ip",))
            for v in values:
                found = indicator(v)
                if found and found[1] in want:
                    rows.append(state_row(found, context, date, date))
    del rows[CIRCL_MAX_ROWS:]
    if errors and len(errors) == len(events[:CIRCL_MAX_EVENTS]):
        raise RuntimeError(f"every event download failed: {errors[0]}")
    return rows


def tor(_key):
    rows = []
    for line in fetch_lines("https://check.torproject.org/torbulkexitlist"):
        ip = public_ipv4(line)
        if ip:
            rows.append(state_row((ip, "ip"), "exit node"))
    return rows


def blocklistde(_key):
    rows = []
    for line in fetch_lines("https://lists.blocklist.de/lists/all.txt"):
        ip = public_ipv4(line)
        if ip:
            rows.append(state_row((ip, "ip"), "reported attacker (SSH/mail/web brute force)"))
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


# fn: returns the feed's rows; key: needs the abuse.ch key; label: the lookup's source
# column; short: the prefix of its context; min_rows: fewer usable rows than this keeps
# the last good copy; ttl: days a row stays after the feed last listed it; lookup:
# indicators (medium detections), lowrisk (low detections, with category) or kev.
Feed = namedtuple("Feed", "fn key label short min_rows ttl lookup category")
FEEDS = {
    "threatfox": Feed(threatfox, True, "abuse.ch ThreatFox", "ThreatFox", 20, TTL_DAYS, "indicators", ""),
    "urlhaus": Feed(urlhaus, True, "abuse.ch URLhaus", "URLhaus", 20, TTL_DAYS, "indicators", ""),
    "feodo": Feed(feodo, False, "abuse.ch Feodo Tracker", "Feodo", 0, TTL_DAYS, "indicators", ""),  # often near empty now
    "openphish": Feed(openphish, False, "OpenPhish community", "OpenPhish", 20, 7, "indicators", ""),
    # CIRCL publishes in bursts and has gone weeks without a new event, so 0
    "circl": Feed(circl, False, "CIRCL OSINT feed", "CIRCL", 0, TTL_DAYS, "indicators", ""),
    "tor": Feed(tor, False, "Tor Project exit list", "Tor", 500, 3, "lowrisk", "tor"),
    "blocklistde": Feed(blocklistde, False, "blocklist.de", "blocklist.de", 2000, 3, "lowrisk", "attacker"),
    "kev": Feed(kev, False, "CISA KEV", "KEV", 1000, 0, "kev", ""),
}


# ---- files ----------------------------------------------------------------------------

def write_file(path, fill, owner=None, mode=0o644):
    """Write path through a temporary file in the same directory and a rename. The
    temporary name starts like the file (hl_ti_feed_), so deploy.sh's rsync exclude
    covers it too. fill(f) writes the content to the open text file."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path).split(".")[0] + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            fill(f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if owner:
            os.chown(tmp, *owner)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_csv(path, fields, rows, owner=None):
    def fill(f):
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    write_file(path, fill, owner)


def read_state(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def merge(old, new, ttl):
    """Old state plus a new download, one row per indicator and type: earliest first
    seen, latest last seen and its context. Rows not seen for ttl days are dropped."""
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
    oldest = (today() - dt.timedelta(days=ttl)).isoformat()
    return sorted((r for r in merged.values() if r["last_seen"] >= oldest), key=lambda r: (r["type"], r["indicator"]))


# ---- warninglists ---------------------------------------------------------------------

def fetch_warninglists(state_dir):
    """(lists, error): the warninglists to use, {name: {"type", "list"}, "sha": commit},
    and why a fresh copy could not be had (then the cached copy, or {}, is returned)."""
    path = os.path.join(state_dir, WARNINGLIST_CACHE)
    selection = sorted(WARNINGLISTS) + [WARNINGLIST_SCANNERS.pattern]   # a change refetches
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        # Only the lists still selected, if the selection changed since the cache.
        cached["lists"] = {n: v for n, v in cached["lists"].items()
                           if n in WARNINGLISTS or WARNINGLIST_SCANNERS.search(n)}
    except (OSError, ValueError, KeyError, AttributeError):
        cached = {}
    try:
        raw, _ = fetch(f"https://api.github.com/repos/{WARNINGLIST_REPO}/commits/main",
                       accept="application/vnd.github.sha", max_bytes=1024)
        sha = raw.decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError("no commit SHA for the main branch")
        if cached.get("sha") == sha and cached.get("selection") == selection:
            return cached, None
        tree = fetch_json(f"https://api.github.com/repos/{WARNINGLIST_REPO}/git/trees/{sha}?recursive=1")
        if tree.get("truncated") or not isinstance(tree.get("tree"), list):
            raise RuntimeError("incomplete file listing")
        names = sorted(m.group(1) for item in tree["tree"]
                       if (m := re.fullmatch(r"lists/([^/]+)/list\.json", str(item.get("path"))))
                       and (m.group(1) in WARNINGLISTS or WARNINGLIST_SCANNERS.search(m.group(1))))
        lists = {}
        for name in names:
            d = fetch_json(f"https://raw.githubusercontent.com/{WARNINGLIST_REPO}/{sha}/lists/{name}/list.json")
            if isinstance(d, dict) and d.get("type") in ("cidr", "hostname", "string") and isinstance(d.get("list"), list):
                lists[name] = {"type": d["type"], "list": [str(v) for v in d["list"]]}
        if len(lists) < MIN_WARNINGLISTS:
            raise RuntimeError(f"only {len(lists)} usable lists (minimum {MIN_WARNINGLISTS})")
        fresh = {"sha": sha, "selection": selection, "fetched": today().isoformat(), "lists": lists}
        write_file(path, lambda f: json.dump(fresh, f), mode=0o600)
        return fresh, None
    except Exception as e:
        if cached.get("lists"):
            return cached, f"{e}; used the cached lists from {cached.get('fetched')}"
        return {}, f"{e}; no cached lists, nothing dropped"


class Warninglists:
    """Matches an indicator against the warninglists; match() is the list's name."""

    def __init__(self, lists):
        self.nets, self.hosts, self.names = {}, {}, {}   # prefix length -> {network: list}
        for name, wl in sorted(lists.items()):
            for value in wl["list"]:
                value = value.strip().lower().rstrip(".")
                if wl["type"] == "cidr":
                    try:
                        net = ipaddress.ip_network(value, strict=False)
                    except ValueError:
                        continue
                    if net.version == 4 and net.prefixlen >= 8:   # never a whole /0-/7
                        self.nets.setdefault(net.prefixlen, {}).setdefault(int(net.network_address), name)
                elif wl["type"] == "hostname":
                    self.hosts.setdefault(value.lstrip("*."), name)
                else:
                    self.names.setdefault(value, name)
        self.prefixes = sorted(self.nets)

    def match(self, value, kind):
        if kind == "ip":
            n = int(ipaddress.IPv4Address(value))
            for p in self.prefixes:
                hit = self.nets[p].get(n >> (32 - p) << (32 - p))
                if hit:
                    return hit
            return None
        if value in self.names:
            return self.names[value]
        labels = value.split(".")
        for i in range(len(labels)):     # the name, or a parent of it
            hit = self.hosts.get(".".join(labels[i:]))
            if hit:
                return hit
        return None


# ---- lookups ---------------------------------------------------------------------------

def build_lookup(state_dir, lookup, wl, show_dropped=False):
    """(rows, dropped): every state of the feeds for one lookup as lookup rows, one per
    indicator: sources joined with '; ', contexts with ' | ', categories with ';', the
    earliest added and the latest expiry. Rows past their feed's TTL (a feed that has
    stopped refreshing) and rows a warninglist matches are left out."""
    combined, dropped = {}, 0
    for name, feed in FEEDS.items():
        if feed.lookup != lookup:
            continue
        for r in read_state(os.path.join(state_dir, f"{name}.csv")):
            last = to_date(r.get("last_seen"))
            if not last or not r.get("indicator") or last + dt.timedelta(days=feed.ttl) <= today():
                continue
            k = (r["indicator"], r["type"])
            row = combined.get(k)
            if row is None:
                hit = wl.match(*k)
                if hit:
                    combined[k] = None
                    dropped += 1
                    if show_dropped:
                        print(f"warninglist drop: {k[0]} type={k[1]} feed={name} list={hit}", flush=True)
                    continue
                row = combined[k] = {"indicator": r["indicator"], "type": r["type"], "categories": [], "sources": [],
                                     "contexts": [], "added": r["first_seen"], "expires": ""}
            if row is None:
                continue
            if feed.label not in row["sources"]:
                row["sources"].append(feed.label)
            if feed.category and feed.category not in row["categories"]:
                row["categories"].append(feed.category)
            context = f"{feed.short}: {r['context']}" if r.get("context") else ""
            if context and context not in row["contexts"]:
                row["contexts"].append(context)
            row["added"] = min(row["added"], r["first_seen"])
            row["expires"] = max(row["expires"], (last + dt.timedelta(days=feed.ttl)).isoformat())
    rows = [{"indicator": r["indicator"], "type": r["type"], "category": ";".join(sorted(r["categories"])),
             "source": "; ".join(r["sources"]), "context": " | ".join(r["contexts"])[:250],
             "added": r["added"], "expires": r["expires"]}
            for r in combined.values() if r]
    rows.sort(key=lambda r: (r["type"], r["indicator"]))
    return rows, dropped


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lookups-dir", required=True, help="the 85_detections app's lookups directory")
    p.add_argument("--state-dir", default=os.environ.get("STATE_DIRECTORY", "/var/lib/hl-threat-feeds"))
    p.add_argument("--env-file", default="/opt/splunk-stack/.env", help="file with the ABUSECH_AUTH_KEY= line")
    p.add_argument("--feeds", default=",".join(FEEDS), help="comma-separated subset of " + ",".join(FEEDS))
    p.add_argument("--show-dropped", action="store_true", help="print each indicator a warninglist drops")
    args = p.parse_args()

    wanted = [f.strip() for f in args.feeds.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in FEEDS]
    if unknown:
        p.error(f"unknown feed(s): {', '.join(unknown)}")
    os.makedirs(args.state_dir, mode=0o700, exist_ok=True)
    st = os.stat(args.lookups_dir)
    owner = (st.st_uid, st.st_gid) if os.geteuid() == 0 else None   # splunkd must read the files

    key = read_key(args.env_file) if any(FEEDS[f].key for f in wanted) else None
    failed = False
    for name in wanted:
        feed = FEEDS[name]
        if feed.key and not key:
            log(name, "fail", error=f"no ABUSECH_AUTH_KEY in {args.env_file}")
            failed = True
            continue
        try:
            rows = feed.fn(key if feed.key else None)
            if len(rows) < feed.min_rows:
                raise RuntimeError(f"only {len(rows)} usable rows (minimum {feed.min_rows}); kept the last good copy")
            if feed.lookup == "kev":
                write_csv(os.path.join(args.lookups_dir, KEV_LOOKUP), KEV_FIELDS, rows, owner)
            else:
                path = os.path.join(args.state_dir, f"{name}.csv")
                rows = merge(read_state(path), rows, feed.ttl)
                write_csv(path, STATE_FIELDS, rows)
            log(name, "ok", len(rows))
        except Exception as e:  # one feed's failure never stops the others
            log(name, "fail", error=e)
            failed = True

    # Warninglists: a failed download falls back to the cached copy and never stops the
    # lookups being written.
    lists, wl_error = fetch_warninglists(args.state_dir)
    wl = Warninglists(lists.get("lists") or {})
    dropped_total = 0

    # Rebuilt every run from the feeds' states, so a failed feed keeps its last good rows.
    # The canaries are added after the warninglists, so they are never dropped.
    for feed_name, lookup, filename, fields, extra in (
            ("lookup", "indicators", INDICATOR_LOOKUP, INDICATOR_FIELDS, CANARIES),
            ("lookup_lowrisk", "lowrisk", LOWRISK_LOOKUP, LOWRISK_FIELDS, [])):
        try:
            rows, dropped = build_lookup(args.state_dir, lookup, wl, args.show_dropped)
            dropped_total += dropped
            write_csv(os.path.join(args.lookups_dir, filename), fields, rows + extra, owner)
            log(feed_name, "ok", len(rows) + len(extra), dropped=dropped)
        except Exception as e:
            log(feed_name, "fail", error=e)
            failed = True
    log("warninglists", "fail" if wl_error else "ok", len(lists.get("lists") or {}), wl_error,
        dropped=dropped_total, sha=str(lists.get("sha") or "none")[:12])
    failed = failed or bool(wl_error)

    # KEV's lookup must exist even before its first good download, or the searches
    # that use it error.
    kev_path = os.path.join(args.lookups_dir, KEV_LOOKUP)
    if not os.path.exists(kev_path):
        write_csv(kev_path, KEV_FIELDS, [], owner)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
