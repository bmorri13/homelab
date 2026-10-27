#!/usr/bin/env python3
"""Test the secret masking in splunk_out (and the secret_mask pipeline it chains to).

    ./scripts/cribl-test-mask.py                 repo pipelines vs cribl/tests/secret-mask
    ./scripts/cribl-test-mask.py --live          the pipelines deployed in Cribl instead
    ./scripts/cribl-test-mask.py --events F.jsonl  count what the rules change in real
                                                 events (one {"_raw": ...} per line);
                                                 prints rule names and counts, no values

Runs Cribl's own preview, so the result is what the Worker would do. The repo
pipelines are loaded as temporary pipelines (zz_test_*) on the Leader and deleted
afterwards; nothing is committed or deployed. Run on security-tooling-01 as root
from the stack directory. Exits 1 if any must-mask line keeps a generated value
or any must-keep line changes.
"""
import base64
import json
import os
import random
import re
import ssl
import string
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

import yaml

STACK = Path(__file__).resolve().parent.parent
PIPELINES = STACK / "cribl/local/cribl/pipelines"
TESTS = STACK / "cribl/tests/secret-mask"
API = os.environ.get("CRIBL_API", "https://127.0.0.1:9000/api/v1")
GROUP = "default"
CHARSETS = {
    "A": string.ascii_letters + string.digits,
    "L": string.ascii_lowercase + string.digits,
    "a": string.ascii_lowercase,
    "U": string.ascii_uppercase + string.digits,
    "H": "0123456789abcdef",
    "N": string.digits,
    "B": string.ascii_letters + string.digits + "+/",
    "W": string.ascii_letters + string.digits + "_",
    "Z": string.ascii_uppercase + "234567",
    "E": "qpzry9x8gf2tvdw0s3jn54khce6mua7l".upper(),
}


def load_env():
    for line in (STACK / ".env").read_text().splitlines():
        m = re.match(r"\s*([A-Za-z_]\w*)=(.*)", line)
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip().strip("'\""))


class Cribl:
    def __init__(self):
        data_dir = os.environ.get("CRIBL_DATA_DIR", "/srv/cribl")
        self.ctx = ssl.create_default_context(
            cafile=os.environ.get("CRIBL_CACERT", f"{data_dir}/local/cribl/auth/homelab-tls/ca.crt"))
        self.token = None
        for _ in range(5):  # logins are rate-limited
            try:
                self.token = self.call("POST", "/auth/login", {
                    "username": "admin", "password": os.environ["CRIBL_ADMIN_PASSWORD"]})["token"]
                break
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                time.sleep(1)
        if not self.token:
            sys.exit("cribl: admin login failed")

    def call(self, method, path, body=None):
        req = urllib.request.Request(API + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        # API is checked to be https in main()
        with urllib.request.urlopen(req, context=self.ctx, timeout=120) as resp:  # nosemgrep: dynamic-urllib-use-detected
            return json.load(resp)

    def preview(self, pipeline, raws):
        out = self.call("POST", f"/m/{GROUP}/preview", {
            "mode": "pipe", "pipelineId": pipeline, "level": 3,
            "events": [{"_raw": r, "_time": 0, "__id": i} for i, r in enumerate(raws)]})
        return [e["_raw"] for e in sorted(out["items"], key=lambda e: e.get("__id", 0))]


def fill(template):
    """Expand a template; return (text, generated values of 8+ characters)."""
    values = []

    def sub(m):
        name, n = m.group(1), m.group(2)
        if name == "uuid":
            v = str(uuid.uuid4())
        elif name == "PRIVATE":
            return "PRIVATE"
        elif name == "PEMB64":  # base64 of an RSA key header, as in kubeconfig client-key-data
            return base64.b64encode(b"-----BEGIN RSA PRIVATE KEY-----\n").decode().rstrip("=")
        else:
            v = "".join(random.choice(CHARSETS[name]) for _ in range(int(n)))
        if len(v) >= 8:
            values.append(v)
        return v

    text = re.sub(r"\{(uuid|PRIVATE|PEMB64|[A-Za-z])(?::(\d+))?\}", sub, template.replace(r"\n", "\n"))
    return text, values


def read_cases(name):
    cases = []
    for line in (TESTS / name).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            label, template = line.split("|", 1)
            cases.append((label.strip(), template.strip()))
    return cases


def main():
    args = sys.argv[1:]
    live = "--live" in args
    events_file = args[args.index("--events") + 1] if "--events" in args else None
    load_env()
    if not API.startswith("https://"):
        sys.exit("CRIBL_API must be an https:// URL")
    cribl = Cribl()

    pipeline, temp = "splunk_out", []
    if not live:
        mask = yaml.safe_load((PIPELINES / "secret_mask/conf.yml").read_text())
        out = yaml.safe_load((PIPELINES / "splunk_out/conf.yml").read_text())
        for f in out["functions"]:
            if f["id"] == "chain" and f["conf"]["processor"] == "secret_mask":
                f["conf"]["processor"] = "zz_test_secret_mask"
        for pid, conf in (("zz_test_secret_mask", mask), ("zz_test_splunk_out", out)):
            cribl.call("POST", f"/m/{GROUP}/pipelines", {"id": pid, "conf": conf})
            temp.append(pid)
        pipeline = "zz_test_splunk_out"
    try:
        if events_file:
            raws = [json.loads(l)["_raw"] for l in Path(events_file).read_text().splitlines() if l.strip()]
            changed, rules = 0, Counter()
            for i in range(0, len(raws), 500):
                batch = raws[i:i + 500]
                for before, after in zip(batch, cribl.preview(pipeline, batch)):
                    if before != after:
                        changed += 1
                        rules.update(re.findall(r"\[REDACTED(?: ([^\]]+))?\]", after))
            print(f"{changed} of {len(raws)} events changed")
            for rule, n in rules.most_common():
                print(f"  {n:6d}  {rule or 'key/value or URL'}")
            return 0

        failures = 0
        must_mask = [(label, *fill(t)) for label, t in read_cases("must-mask.txt")]
        results = cribl.preview(pipeline, [text for _, text, _ in must_mask])
        for (label, _, values), after in zip(must_mask, results):
            left = [v for v in values if v in after]
            if left:
                failures += 1
                print(f"MISSED  {label}: {after[:160]!r}")
        must_keep = [(label, fill(t)[0]) for label, t in read_cases("must-keep.txt")]
        results = cribl.preview(pipeline, [text for _, text in must_keep])
        for (label, before), after in zip(must_keep, results):
            if before != after:
                failures += 1
                print(f"CHANGED {label}: {after[:200]!r}")
        print(f"{len(must_mask)} must-mask, {len(must_keep)} must-keep, {failures} failed")
        return 1 if failures else 0
    finally:
        for pid in reversed(temp):  # splunk_out chains to secret_mask
            cribl.call("DELETE", f"/m/{GROUP}/pipelines/{pid}")


if __name__ == "__main__":
    sys.exit(main())
