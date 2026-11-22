"""Shared code for the siem-software-factory POC scripts (stdlib only).

Runs on security-tooling-01 as root, from the stack directory's copy
(/opt/splunk-stack/siem-software-factory). Talks to:
  - Splunk's management API at https://192.168.4.11:8089, cert checked against the
    homelab CA. Setup and cleanup use the admin login from the stack's .env; tests
    use the read-only sscpoc-validator token.
  - The Cribl Leader API at https://127.0.0.1:9000, as admin from .env.
Secrets are never printed: request errors are scrubbed of every secret this
process knows before they are raised.
"""
import base64
import datetime as dt
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SF_DIR = Path(__file__).resolve().parent.parent
STACK_DIR = Path(os.environ.get("STACK_DIR", SF_DIR.parent))
CONF = json.loads((SF_DIR / "config" / "poc.json").read_text())
HOMELAB_CA = os.environ.get("HOMELAB_CA", "/root/homelab-ca/ca.crt")
CRIBL_API = os.environ.get("CRIBL_API", "https://127.0.0.1:9000/api/v1")
CRIBL_CA = os.environ.get("CRIBL_CACERT", "/srv/cribl/local/cribl/auth/homelab-tls/ca.crt")
GROUP = "default"
SECRETS_DIR = Path(CONF["paths"]["secrets_dir"])
VALIDATOR_TOKEN_FILE = SECRETS_DIR / "validator.token"
# Run and dataset ids end up in paths and Splunk searches: keep them to this.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")

_secrets = set()


def remember_secret(value):
    if value:
        _secrets.add(value)
    return value


def scrub(text):
    for s in _secrets:
        text = text.replace(s, "<redacted>")
    return text


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_from_epoch(t):
    return dt.datetime.fromtimestamp(float(t), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_env():
    """The stack's .env as a dict, without exporting it to child processes."""
    env = {}
    for line in (STACK_DIR / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    for k in ("SPLUNK_PASSWORD", "CRIBL_ADMIN_PASSWORD", "CRIBL_HEC_TOKEN", "CRIBL_DIST_TOKEN"):
        remember_secret(env.get(k))
    return env


class HTTPError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(scrub(f"HTTP {status} from {url}: {body[:500]}"))


def _request(method, url, *, headers=None, data=None, cafile, timeout=60):
    ctx = ssl.create_default_context(cafile=cafile)
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class Splunk:
    """Splunk REST, as admin (basic auth) or with a bearer token."""

    def __init__(self, *, password=None, token=None, base=None):
        self.base = base or CONF["splunk"]["api"]
        if token:
            self.auth = "Bearer " + remember_secret(token)
        else:
            self.auth = "Basic " + base64.b64encode(f"admin:{remember_secret(password)}".encode()).decode()

    def call(self, method, path, params=None, ok=(200, 201), timeout=120):
        url = self.base + path
        data = None
        params = dict(params or {})
        params.setdefault("output_mode", "json")
        if method == "GET" or method == "DELETE":
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        else:
            data = urllib.parse.urlencode(params, doseq=True).encode()
        status, body = _request(method, url, headers={"Authorization": self.auth}, data=data,
                                cafile=HOMELAB_CA, timeout=timeout)
        if ok is not None and status not in ok:
            raise HTTPError(status, body, url.split("?")[0])
        try:
            return status, json.loads(body) if body.strip() else {}
        except ValueError:
            return status, {"_text": body}

    def exists(self, path):
        status, _ = self.call("GET", path, ok=None)
        return status == 200

    def export(self, search, earliest, latest, timeout=300):
        """Run a bounded search with the export endpoint; returns the result dicts."""
        if not earliest or earliest in ("0", "") or not latest:
            raise ValueError("refusing an unbounded search: earliest and latest are required")
        params = {"search": search, "earliest_time": earliest, "latest_time": latest,
                  "output_mode": "json", "time_format": "%s.%6N"}
        status, body = _request("POST", self.base + "/services/search/v2/jobs/export",
                                headers={"Authorization": self.auth},
                                data=urllib.parse.urlencode(params).encode(),
                                cafile=HOMELAB_CA, timeout=timeout)
        if status != 200:
            raise HTTPError(status, body, "/services/search/v2/jobs/export")
        results, messages = [], []
        for line in body.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if "result" in obj:
                results.append(obj["result"])
            for m in obj.get("messages", []) or []:
                messages.append(m)
        errors = [m for m in messages if m.get("type") in ("FATAL", "ERROR")]
        if errors:
            raise RuntimeError(f"search failed: {errors}")
        return results


class Cribl:
    def __init__(self, password):
        self.token = None
        for _ in range(5):
            status, body = _request("POST", CRIBL_API + "/auth/login",
                                    headers={"Content-Type": "application/json"},
                                    data=json.dumps({"username": "admin", "password": remember_secret(password)}).encode(),
                                    cafile=CRIBL_CA)
            if status == 429:
                time.sleep(1)
                continue
            if status != 200:
                raise HTTPError(status, body, "/auth/login")
            self.token = remember_secret(json.loads(body)["token"])
            break
        if not self.token:
            raise RuntimeError("cribl: login rate-limited")

    def call(self, method, path, body=None, ok=(200,), timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        status, text = _request(method, CRIBL_API + path, data=data, timeout=timeout, cafile=CRIBL_CA,
                                headers={"Authorization": "Bearer " + self.token,
                                         "Content-Type": "application/json"})
        if ok is not None and status not in ok:
            raise HTTPError(status, text, path)
        try:
            return status, json.loads(text) if text.strip() else {}
        except ValueError:
            return status, {"_text": text}

    def get_item(self, path):
        status, body = self.call("GET", path, ok=None)
        if status == 404 or not body.get("items"):
            return None
        if status != 200:
            raise HTTPError(status, json.dumps(body), path)
        return body["items"][0]

    def pending_changes(self):
        _, body = self.call("GET", f"/version/status?group={GROUP}")
        st = body["items"][0]
        return sorted(set(st.get("modified", []) + st.get("created", []) + st.get("deleted", []) +
                          st.get("not_added", []) + st.get("renamed", []) + st.get("staged", [])))

    def commit_deploy(self, message, files=None):
        _, body = self.call("POST", "/version/commit", {"message": message, "group": GROUP,
                                                        **({"files": files} if files else {})})
        version = body["items"][0]["commit"]
        self.call("PATCH", f"/master/groups/{GROUP}/deploy", {"version": version})
        return version

    def wait_worker_version(self, version, timeout=120):
        """Wait until every Worker in the group runs config <version>."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, body = self.call("GET", "/master/workers")
            workers = [w for w in body.get("items", []) if w.get("group") == GROUP]
            if workers and all(w.get("status") == "healthy" and
                               w.get("info", {}).get("cribl", {}).get("config", {}).get("version") == version
                               for w in workers):
                # Worker processes restart to load the new config just after
                # reporting it; give them time before anything runs a job.
                time.sleep(15)
                return True
            time.sleep(3)
        return False


def load_validator_token():
    try:
        return remember_secret(VALIDATOR_TOKEN_FILE.read_text().strip())
    except FileNotFoundError:
        sys.exit(f"missing {VALIDATOR_TOKEN_FILE}: run `make setup` first")


def validator():
    return Splunk(token=load_validator_token())


def admin_splunk(env=None):
    env = env or read_env()
    return Splunk(password=env["SPLUNK_PASSWORD"])


def admin_cribl(env=None):
    env = env or read_env()
    return Cribl(env["CRIBL_ADMIN_PASSWORD"])


def load_json(path):
    return json.loads(Path(path).read_text())


def spl_quote(value):
    """A value as a quoted SPL string."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_id(value, what):
    if not ID_RE.match(value or ""):
        sys.exit(f"invalid {what}: {value!r}")
    return value


# ---------------------------------------------------------------- Cribl jobs

TERMINAL = ("finished", "failed", "cancelled", "canceled", "orphaned")


def new_run_id(prefix):
    """<prefix>-YYYYmmddTHHMMSSZ-<6 hex>: sortable, unique, safe in paths and SPL."""
    return f"{prefix}-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{os.urandom(3).hex()}"


def run_saved_job(cr, saved_id, *, metadata, mutate=None, timeout=None, retries=1):
    """Run saved collector <saved_id> once, ad hoc, with extra input metadata
    (name -> string value); mutate(job) may change the collector config for this
    run only. The saved job is not modified. Returns the finished job info.
    A run that fails only because Worker processes were restarting (right after a
    config deploy) is retried."""
    job = cr.get_item(f"/m/{GROUP}/lib/jobs/{saved_id}")
    if job is None:
        sys.exit(f"cribl: collector {saved_id} not found - run `make setup`")
    for k in ("status", "notifications", "history", "savedState", "lib"):
        job.pop(k, None)
    if mutate:
        mutate(job)
    meta = [m for m in job["input"].get("metadata", []) if m["name"] not in metadata]
    meta += [{"name": k, "value": json.dumps(v)} for k, v in metadata.items()]
    job["input"]["metadata"] = meta
    job["run"] = dict(job["schedule"]["run"])
    for attempt in range(retries + 1):
        info = _run_once(cr, job, timeout)
        lost = [e for e in info["task_errors"] if "WorkerProcessLostError" in e]
        if not (lost and attempt < retries):
            return info
        log("cribl: Worker processes were restarting; retrying in 20s")
        time.sleep(20)
    return info


def _run_once(cr, job, timeout):
    _, body = cr.call("POST", f"/m/{GROUP}/jobs", job)
    job_id = body["items"][0]
    log(f"cribl: started job {job_id}")
    deadline = time.time() + (timeout or CONF["timeouts"]["job_seconds"])
    info = {}
    while time.time() < deadline:
        info = cr.get_item(f"/m/{GROUP}/jobs/{job_id}") or {}
        if info.get("status", {}).get("state") in TERMINAL:
            break
        time.sleep(2)
    else:
        cr.call("PATCH", f"/m/{GROUP}/jobs/{job_id}/cancel", ok=None)
        raise RuntimeError(f"job {job_id} did not finish within the job timeout; cancelled")
    info["job_id"] = job_id
    info["task_errors"] = task_errors(job_id)
    return info


def task_errors(job_id):
    """A job's task errors: the Leader's task-errors.ndjson, and error lines in the
    Worker's task logs (both read from their host directories)."""
    out = []
    leader = Path(os.environ.get("CRIBL_DATA_DIR", "/srv/cribl")) / "state/jobs" / GROUP / job_id / "task-errors.ndjson"
    if leader.exists():
        for line in leader.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            err = rec.get("error") or {}
            out.append(scrub(f"{rec.get('taskId')}: {err.get('name', '')}: {err.get('message', '')}"[:400]))
    base = Path(os.environ.get("CRIBL_WORKER_DATA_DIR", "/srv/cribl-worker")) / "state/jobs" / GROUP / job_id / "tasks"
    for f in sorted(base.glob("*/log/task.log")):
        for line in f.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("level") == "error":
                out.append(scrub(f"{f.parent.parent.name}: {rec.get('message')}: {rec.get('reason') or rec.get('error') or ''}"[:400]))
    return out


def job_summary(info):
    st = info.get("stats", {})
    tasks = st.get("tasks", {})
    return {"job_id": info.get("job_id"), "state": info.get("status", {}).get("state"),
            "tasks_finished": tasks.get("finished"), "tasks_failed": tasks.get("failed"),
            "collected_events": st.get("collectedEvents"), "collected_bytes": st.get("collectedBytes"),
            "task_errors": info.get("task_errors", [])}


def wait_files(directory, timeout, expect_lines=None):
    """Wait for the filesystem destination to close its files in <directory>:
    lines stop changing and nothing for it is left in staging."""
    directory = Path(directory)
    staging = Path(CONF["paths"]["host_data"]) / ".staging"
    deadline = time.time() + timeout
    last, stable = -1, 0
    while time.time() < deadline:
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        lines = sum(sum(1 for _ in f.open()) for f in files)
        open_parts = [p for p in staging.rglob("*") if p.is_file() and directory.name in str(p)]
        if files and not open_parts and lines == last:
            stable += 1
            if stable >= 2 and (expect_lines is None or lines >= expect_lines):
                return files
        else:
            stable = 0
        last = lines
        time.sleep(3)
    return sorted(directory.glob("*.jsonl")) if directory.exists() else []


def collector_checks(job):
    """The settings the capture collector must have: name -> ok."""
    src, c = CONF["source"], CONF["cribl"]
    cc, inp = job["collector"]["conf"], job["input"]
    m = re.search(r"\|\s*head\s+(\d+)", cc["search"])
    return {
        "collector type splunk": job["collector"]["type"] == "splunk",
        "export endpoint": cc["endpoint"] == "/services/search/v2/jobs/export",
        "output json": cc["outputMode"] == "json",
        "schedule disabled": job["schedule"].get("enabled") is False,
        "TLS verified (rejectUnauthorized)": cc.get("rejectUnauthorized") is True,
        "token from Cribl secret": cc["authentication"] == "tokenSecret" and cc.get("tokenSecret") == c["search_token_secret"],
        "request timeout set": 0 < cc.get("timeout", 0) <= 300,
        "job timeout set": job["schedule"]["run"].get("jobTimeout") not in (None, "", "0"),
        "bounded: earliest -48h": cc["earliest"] == src["earliest"] and f"earliest={src['earliest']}" in cc["search"],
        "bounded: head <= limit": bool(m) and int(m.group(1)) <= CONF["max_events_limit"],
        "only the selected index": f"index={src['index']}" in cc["search"] and CONF["dev_index"] not in cc["search"],
        "dedicated breaker": inp["breakerRulesets"] == ["poc-splunk-search-json"],
        "dedicated pipeline": inp["pipeline"] == c["capture_pipeline"],
        "bypasses routes": inp["sendToRoutes"] is False and inp["output"] == c["capture_output"],
    }
