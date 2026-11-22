#!/usr/bin/env python3
"""Check the prerequisites and validate the POC configuration. Read-only.

    ./scripts/check.py            prerequisites + POC config
    ./scripts/check.py --prereq   prerequisites only (before the first setup)

Prints one PASS/FAIL line per check; exits 1 if any failed.
"""
import argparse
import json
import subprocess
import sys
import time

import sflib
from sflib import CONF, GROUP, SF_DIR

S, C = CONF["splunk"], CONF["cribl"]
failed = 0


def check(name, fn):
    global failed
    try:
        ok, detail = fn()
    except SystemExit as e:
        ok, detail = False, f"exited: {e}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {sflib.scrub(str(e))}"
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {sflib.scrub(str(detail))}", flush=True)


def worker(*cmd):
    p = subprocess.run(["docker", "exec", "cribl-worker", *cmd], capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prereq", action="store_true")
    args = ap.parse_args()
    env = sflib.read_env()
    sp = sflib.admin_splunk(env)
    cr = sflib.admin_cribl(env)

    def splunk_api():
        _, b = sp.call("GET", "/services/server/info")
        c = b["entry"][0]["content"]
        return True, f"{S['api']} Splunk {c['version']} ({c['serverName']}), cert verified against the homelab CA"
    check("Splunk management API (8089)", splunk_api)

    def cribl_api():
        _, b = cr.call("GET", "/system/info")
        return True, f"Leader {b['items'][0]['BUILD']['VERSION']}, cert verified"
    check("Cribl Leader API", cribl_api)

    def cribl_worker():
        _, b = cr.call("GET", "/master/workers")
        ws = [w for w in b["items"] if w.get("group") == GROUP]
        group = cr.get_item(f"/master/groups/{GROUP}")
        ok = ws and all(w["status"] == "healthy" and w["info"]["cribl"]["config"]["version"] == group["configVersion"] for w in ws)
        return ok, "; ".join(f"{w['info']['hostname']} {w['status']} config {w['info']['cribl']['config']['version'][:8]}" for w in ws) + \
            f" (group at {group['configVersion'][:8]})"
    check("Cribl Worker health and config", cribl_worker)

    def worker_ca():
        rc, out = worker("sh", "-c", 'echo "$NODE_EXTRA_CA_CERTS"; test -s "$NODE_EXTRA_CA_CERTS" && echo present')
        return rc == 0 and out.endswith("present"), f"NODE_EXTRA_CA_CERTS={out.splitlines()[0] if out else ''}"
    check("Worker trusts the homelab CA", worker_ca)

    def worker_to_mgmt():
        rc, out = worker("curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{ssl_verify_result}",
                         "--cacert", "/opt/cribl/tls/ca.crt", CONF["splunk"]["worker_search_head"] + "/services/server/info")
        return out == "401 0", f"from the Worker: HTTP/verify {out} (401 = reachable, TLS verified, auth required)"
    check("Worker -> Splunk search head (8089)", worker_to_mgmt)

    def worker_to_hec():
        url = CONF["splunk"]["worker_hec"].rsplit("/services/", 1)[0] + "/services/collector/health"
        rc, out = worker("curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{ssl_verify_result}", "--cacert", "/opt/cribl/tls/ca.crt", url)
        return out == "200 0", f"{url} from the Worker: HTTP/verify {out}"
    check("Worker -> Splunk HEC (8088, internal only)", worker_to_hec)

    def mount():
        mounts = json.loads(subprocess.run(["docker", "inspect", "-f", "{{json .Mounts}}", "cribl-worker"],
                                           capture_output=True, text=True, check=True).stdout)
        m = next((x for x in mounts if x["Destination"] == CONF["paths"]["worker_data"]), None)
        return bool(m) and m["Type"] == "bind" and m["RW"], f"{m and m['Source']} -> {CONF['paths']['worker_data']} ({m and m['Type']}, rw={m and m['RW']})"
    check("Sample directory mounted in the Worker", mount)

    def source_populated():
        src = CONF["source"]
        n = int(sp.export(f"| tstats count where index={src['index']} sourcetype={sflib.spl_quote(src['sourcetype'])}",
                          src["earliest"], src["latest"])[0]["count"])
        return n > 0, f"index={src['index']} sourcetype={src['sourcetype']}: {n} events in {src['earliest']}..{src['latest']}"
    check("Source index populated", source_populated)

    if args.prereq:
        sys.exit(1 if failed else 0)

    def config_in_place():
        p = subprocess.run([sys.executable, str(SF_DIR / "scripts/setup.py"), "--plan"], capture_output=True, text=True)
        changes = json.loads(p.stdout.strip().splitlines()[-1])["changes"] if p.returncode == 0 else ["setup.py failed: " + p.stderr[-200:]]
        return not changes, "setup.py --plan: no changes" if not changes else f"would change: {changes}"
    check("POC resources match the repo definitions", config_in_place)

    def collector():
        job = cr.get_item(f"/m/{GROUP}/lib/jobs/{CONF['collector_id']}")
        want = sflib.collector_checks(job)
        bad = [k for k, ok in want.items() if not ok]
        return not bad, f"{len(want) - len(bad)}/{len(want)} settings ok" + (f"; wrong: {bad}" if bad else "")
    check(f"Collector {CONF['collector_id']} configuration", collector)

    def tokens():
        out, ok = [], True
        renew = time.time() + S["token_renew_days"] * 86400
        for user in (S["collector_user"], S["validator_user"]):
            _, b = sp.call("GET", "/services/authorization/tokens", {"username": user, "count": "0"})
            exps = [int(e["content"]["claims"]["exp"]) for e in b.get("entry", []) if e["content"].get("status") == "enabled"]
            ok &= bool(exps) and max(exps) > renew
            out.append(f"{user}: {len(exps)} token(s), expires {sflib.iso_from_epoch(max(exps))[:10] if exps else '-'}")
        return ok, "; ".join(out) + " (run `make rotate` to renew)"
    check("Splunk tokens valid", tokens)

    def secrets():
        have = [s for s in (C["search_token_secret"], C["hec_token_secret"]) if cr.get_item(f"/m/{GROUP}/system/secrets/{s}")]
        return len(have) == 2, f"Cribl secrets present: {have}"
    check("Cribl secrets", secrets)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
