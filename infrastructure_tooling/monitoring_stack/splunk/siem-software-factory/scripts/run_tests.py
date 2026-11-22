#!/usr/bin/env python3
"""End-to-end validation of the POC (tests A-D), with a pass/fail report.

    ./scripts/run_tests.py                     capture 100, replay, test everything
    ./scripts/run_tests.py --max 1000          capture up to 1000 instead
    ./scripts/run_tests.py --dataset <id>      test an existing dataset (new replay)

Every run has a test run id, sftest-<UTC time>-<hex>. The capture run id is
<category>-<same suffix> (also the dataset id), and the replay id is the test run
id itself, so one id finds everything:
    index=devlogs sf_replay_id::sftest-...        the replayed events
    reports/sftest-.../report.{json,md}           the results
A: the collector (config, isolation, auth, TLS verification, bounded search,
   events returned, breaking, least privilege)
B: the saved dataset (files, JSONL, metadata, raw reconstruction, persistence)
C: delivery to devlogs over HEC (arrival, metadata, timestamps, HEC token scope)
D: integrity: counts and raw hashes, source vs dataset vs devlogs, duplicates,
   event time vs collection and index time
Exit status 1 if any test failed. Reports never contain secrets.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

import dataset
import sflib
from sflib import CONF, GROUP, SF_DIR, log, spl_quote

S, C, SRC, DEV = CONF["splunk"], CONF["cribl"], CONF["source"], CONF["dev_index"]


class Suite:
    def __init__(self, test_run_id):
        self.id, self.results, self.facts = test_run_id, [], {}

    def check(self, test_id, name, fn):
        t0 = time.time()
        try:
            ok, detail = fn()
        except SystemExit as e:
            ok, detail = False, f"exited: {e}"
        except Exception as e:  # a crash is a failed test, with the reason
            ok, detail = False, f"{type(e).__name__}: {sflib.scrub(str(e))}"
            log(sflib.scrub(traceback.format_exc(limit=3)))
        self.results.append({"id": test_id, "name": name, "result": "PASS" if ok else "FAIL",
                             "detail": sflib.scrub(str(detail)), "seconds": round(time.time() - t0, 2)})
        log(f"{'PASS' if ok else 'FAIL'}  {test_id} {name}: {sflib.scrub(str(detail))[:200]}")
        return ok


def worker_exec(*cmd):
    p = subprocess.run(["docker", "exec", "cribl-worker", *cmd], capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def container_ip(name):
    out = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", name],
                         capture_output=True, text=True, check=True).stdout.split()
    return out[0]


def window(events, pad=1):
    times = [e["_time"] for e in events]
    return str(math.floor(min(times)) - pad), str(math.ceil(max(times)) + pad)


def source_events(v, events):
    """The captured events as the source Splunk has them now, keyed by event id."""
    earliest, latest = window(events)
    rows = v.export(
        f"search index={SRC['index']} sourcetype={spl_quote(SRC['sourcetype'])} "
        "| eval sf_id=splunk_server.\"/\"._bkt.\"/\"._cd, h=sha256(_raw), n=len(_raw) "
        "| fields sf_id h n _time host source sourcetype index", earliest, latest)
    want = {e["sf_splunk_event_id"] for e in events}
    return {r["sf_id"]: r for r in rows if r.get("sf_id") in want}


def devlogs_events(v, replay_id, events):
    earliest, latest = window(events, pad=2)
    return v.export(
        f"search index={DEV} sf_replay_id::{replay_id} "
        "| eval h=sha256(_raw) "
        "| fields h _time _indextime host source sourcetype index sf_splunk_event_id sf_orig_index "
        "sf_dataset_id sf_raw_sha256 sf_seq", earliest, latest)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max", type=int, default=CONF["max_events_default"])
    ap.add_argument("--dataset", help="test this existing dataset instead of capturing a new one")
    args = ap.parse_args()
    test_run_id = sflib.new_run_id("sftest")
    suffix = test_run_id.split("-", 1)[1]
    suite = Suite(test_run_id)
    env = sflib.read_env()
    cr = sflib.admin_cribl(env)
    sp = sflib.admin_splunk(env)
    v = sflib.validator()
    facts = suite.facts
    facts.update(test_run_id=test_run_id, started_at=sflib.now_iso(), max_events=args.max)
    log(f"test run {test_run_id}")

    # ------------------------------------------------------------ A: collector
    job_cfg = cr.get_item(f"/m/{GROUP}/lib/jobs/{CONF['collector_id']}")

    def a1():
        want = sflib.collector_checks(job_cfg)
        bad = [k for k, ok in want.items() if not ok]
        return not bad, f"{len(want) - len(bad)}/{len(want)} settings ok" + (f"; wrong: {bad}" if bad else "")

    suite.check("A1", "collector configuration (bounded, manual, TLS-verified, isolated)", a1)

    def a2():
        routes = cr.get_item(f"/m/{GROUP}/routes/default") or {}
        ours = {C["capture_pipeline"], C["replay_pipeline"], C["capture_output"], C["replay_output"]}
        hits = [r["id"] for r in routes.get("routes", [])
                if r.get("pipeline") in ours or r.get("output") in ours or
                any(cl.get("output") in ours for cl in r.get("clones", []) or [])]
        outputs = cr.call("GET", f"/m/{GROUP}/system/outputs")[1]["items"]
        default = next((o.get("defaultId") for o in outputs if o["id"] == "default"), None)
        ok = not hits and default not in ours
        return ok, f"routes using POC objects: {hits or 'none'}; default output: {default}"

    suite.check("A2", "collector results cannot enter production routes", a2)

    def a3():
        _, body = sp.call("GET", f"/services/authorization/roles/{S['collector_role']}")
        c = body["entry"][0]["content"]
        _, ub = sp.call("GET", f"/services/authentication/users/{S['collector_user']}")
        roles = ub["entry"][0]["content"]["roles"]
        caps = sorted(set(c["capabilities"]) | set(c.get("imported_capabilities", [])))
        ok = (roles == [S["collector_role"]] and caps == ["list_all_objects", "search"] and
              c["srchIndexesAllowed"] == [SRC["index"]] and DEV in c["srchIndexesDisallowed"] and
              0 < int(c["srchTimeWin"]) <= 259200 and int(c["rtSrchJobsQuota"]) == 0)
        return ok, f"user roles {roles}; capabilities {caps}; indexes {c['srchIndexesAllowed']} (disallowed {c['srchIndexesDisallowed']}); srchTimeWin {c['srchTimeWin']}s"

    suite.check("A3", "dedicated least-privilege Splunk account (read-only, one index)", a3)

    # Capture (or load) the dataset.
    capture = None
    if args.dataset:
        dataset_id = args.dataset
    else:
        dataset_id = f"{SRC['category']}-{suffix}"
        p = subprocess.run([sys.executable, str(SF_DIR / "scripts/collect.py"), "--max", str(args.max), "--run-id", dataset_id],
                           capture_output=True, text=True)
        sys.stderr.write(p.stderr)
        capture = {"exit": p.returncode, "out": p.stdout}
    facts["dataset_id"] = dataset_id
    try:
        manifest, events, ddir = dataset.load(dataset_id)
    except SystemExit:
        manifest, events, ddir = None, [], None
    facts["capture_job"] = (manifest or {}).get("capture", {}).get("cribl_job")

    def a4():
        if capture and capture["exit"] != 0:
            return False, f"collect.py exited {capture['exit']}: {capture['out'][-300:]}"
        job = manifest["capture"]["cribl_job"]
        return (job["state"] == "finished" and job["tasks_failed"] == 0,
                f"job {job['job_id']} {job['state']}, {job['tasks_finished']} tasks ok, {job['tasks_failed']} failed "
                "(token auth over verified TLS to the search head)")

    suite.check("A4", "collector authenticates and executes the bounded search", a4)

    def a5():
        earliest, latest = SRC["earliest"], SRC["latest"]
        avail = int(v.export(f"| tstats count where index={SRC['index']} sourcetype={spl_quote(SRC['sourcetype'])}",
                             earliest, latest)[0]["count"])
        expected = min(avail, manifest["capture"]["max_events"])
        n = manifest["event_count"]
        facts.update(available_in_window=avail, captured=n)
        # The window moves between the collector's search and this count, so allow
        # for events that arrived in between when fewer than max were available.
        ok = n > 0 and (n == expected or (avail < manifest["capture"]["max_events"] and abs(n - avail) <= 5))
        return ok, f"{n} events returned; {avail} available in index={SRC['index']} sourcetype={SRC['sourcetype']} {earliest}..{latest}; expected {expected}"

    suite.check("A5", "collector returns actual events (head N of what is available)", a5)

    def a6():
        ids = [e["sf_splunk_event_id"] for e in events]
        offsets = [e["sf_result_offset"] for e in events]
        wrapped = [e["sf_seq"] for e in events if re.match(r'^\{"preview":', e["_raw"]) or '"result":{"_raw"' in e["_raw"]]
        ok = len(set(ids)) == len(events) and sorted(offsets) == list(range(len(events))) and not wrapped and manifest["invalid_record_count"] == 0
        return ok, (f"{len(events)} events, {len(set(ids))} distinct Splunk event ids, export offsets 0..{max(offsets) if offsets else '-'} "
                    f"each exactly once, {len(wrapped)} events still wrapped, {manifest['invalid_record_count']} invalid lines")

    suite.check("A6", "event breaker separates results: one export result per event", a6)

    def run_probe(mutate_conf, label):
        def mutate(job):
            mutate_conf(job["collector"]["conf"])
            job["input"]["output"] = "devnull"      # probes deliver nothing anywhere
            job["input"]["pipeline"] = "passthru"
        info = sflib.run_saved_job(cr, CONF["collector_id"], mutate=mutate, metadata={"sf_run_id": f"{test_run_id}.{label}"}, retries=0)
        return sflib.job_summary(info)

    def a7():
        ip = container_ip("splunk")

        def to_container_ip(conf):
            conf["searchHead"] = f"https://{ip}:8089"     # same Splunk, an address not in its cert
            conf["search"] = re.sub(r"\|\s*head\s+\d+", "| head 1", conf["search"])
        j = run_probe(to_container_ip, "tlsprobe")
        errs = " ".join(j["task_errors"])
        refused = j["tasks_failed"] and re.search(r"altname|certificate|self.signed|ERR_TLS|SSL", errs, re.I)
        return bool(refused), f"search head https://{ip}:8089 (not in cert SAN): tasks failed {j['tasks_failed']}; error: {errs[:200] or 'none'}"

    suite.check("A7", "TLS certificate verification is enforced (wrong-name cert refused)", a7)

    # ------------------------------------------------------------ B: dataset
    def b1():
        p = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=ddir, capture_output=True, text=True)
        names = sorted(x.name for x in ddir.iterdir())
        return p.returncode == 0 and names == ["SHA256SUMS", "events.jsonl", "manifest.json"], f"{names}; sha256sum -c: {p.stdout.strip()}"

    suite.check("B1", "dataset exported: events.jsonl, manifest.json, SHA256SUMS verify", b1)

    def b2():
        bad, n = [], 0
        for i, line in enumerate((ddir / "events.jsonl").read_text().splitlines()):
            n += 1
            try:
                rec = json.loads(line)
            except ValueError:
                bad.append((i, "not JSON"))
                continue
            p = dataset.problems(rec)
            if p:
                bad.append((i, p))
        return not bad and n == manifest["event_count"], f"{n} lines, all valid JSON with schema {dataset.EVENT_SCHEMA}; problems: {bad[:3] or 'none'}"

    suite.check("B2", "JSONL is valid and every record has the required fields", b2)

    src_now = {}

    def b3():
        src_now.update(source_events(v, events))
        mism = []
        for e in events:
            s = src_now.get(e["sf_splunk_event_id"])
            if s is None:
                mism.append((e["sf_seq"], "not found in source"))
                continue
            for k in ("host", "source", "sourcetype", "index"):
                if s[k] != e[k]:
                    mism.append((e["sf_seq"], k))
            if abs(float(s["_time"]) - e["_time"]) > 0.0005:
                mism.append((e["sf_seq"], "_time"))
        facts["source_matched"] = len(events) - len({m[0] for m in mism})
        return not mism, f"{facts['source_matched']}/{len(events)} events match the source on host, source, sourcetype, index and _time; mismatches: {mism[:5] or 'none'}"

    suite.check("B3", "Splunk metadata and original timestamps preserved", b3)

    def b4():
        rebuilt = [e["_raw"] for e in events]
        h_src = sorted(src_now[e["sf_splunk_event_id"]]["h"] for e in events if e["sf_splunk_event_id"] in src_now)
        h_ds = sorted(hashlib.sha256(r.encode()).hexdigest() for r in rebuilt)
        out = SF_DIR / "reports" / test_run_id
        out.mkdir(parents=True, exist_ok=True)
        # Raw events alone, one per line, in original time order: what a replay
        # to a file-based input or a parser test would consume.
        raw_file = Path(CONF["paths"]["host_data"]) / "tmp" / f"{test_run_id}.raw"
        raw_file.parent.mkdir(mode=0o700, exist_ok=True)
        raw_file.write_text("\n".join(rebuilt) + "\n")
        size = raw_file.stat().st_size
        raw_file.unlink()
        return h_src == h_ds and len(h_ds) == len(events), (
            f"{len(rebuilt)} raw events rebuilt from the dataset ({size} bytes); sha256 of every _raw equals sha256(_raw) computed by the source Splunk")

    suite.check("B4", "original raw events can be reconstructed byte-for-byte", b4)

    def b5():
        m = manifest
        times = [e["_time"] for e in events]
        ok = (m["files"]["events.jsonl"]["lines"] == len(events) == m["event_count"] and
              m["event_time"]["earliest_epoch"] == min(times) and m["event_time"]["latest_epoch"] == max(times) and
              m["source"]["indexes"] == {SRC["index"]: len(events)} and m["capture"]["run_id"] == dataset_id and
              m["capture"]["splunk_search"] and m["created_at"])
        return ok, (f"dataset {m['dataset_id']}: {m['event_count']} events, {m['event_time']['earliest']}..{m['event_time']['latest']}, "
                    f"sourcetypes {m['source']['sourcetypes']}, captured {m['capture']['collected_at_first']}")

    suite.check("B5", "manifest describes the dataset (id, counts, source, capture time)", b5)

    def b6():
        mounts = json.loads(subprocess.run(["docker", "inspect", "-f", "{{json .Mounts}}", "cribl-worker"],
                                           capture_output=True, text=True, check=True).stdout)
        m = next((x for x in mounts if x["Destination"] == CONF["paths"]["worker_data"]), None)
        rel = ddir.relative_to(CONF["paths"]["host_data"])
        rc, out, _ = worker_exec("sha256sum", f"{CONF['paths']['worker_data']}/{rel}/events.jsonl")
        ok = bool(m) and m["Type"] == "bind" and m["Source"] == CONF["paths"]["host_data"] and m["RW"] and rc == 0 and \
            out.split()[0] == manifest["files"]["events.jsonl"]["sha256"]
        return ok, (f"Worker {CONF['paths']['worker_data']} is a {m and m['Type']} mount of host {m and m['Source']} "
                    "(survives container recreation); Worker reads the same events.jsonl hash")

    suite.check("B6", "samples persist on the host, outside the container", b6)

    # ------------------------------------------------------------ C: delivery
    replay = {}

    def c1():
        p = subprocess.run([sys.executable, str(SF_DIR / "scripts/replay.py"), dataset_id, "--replay-id", test_run_id],
                           capture_output=True, text=True)
        sys.stderr.write(p.stderr)
        if p.returncode:
            return False, f"replay.py exited {p.returncode}: {p.stdout[-300:]}"
        replay.update(json.loads(p.stdout))
        j = replay["job"]
        return j["collected_events"] == len(events) and not j["tasks_failed"], (
            f"replay job {j['job_id']} read {j['collected_events']} records ({j['collected_bytes']} bytes) into poc_devlogs_hec")

    suite.check("C1", "Cribl replays the dataset through the HEC destination", c1)
    facts["replay_id"] = test_run_id

    dest = []

    def c2():
        deadline = time.time() + CONF["timeouts"]["index_settle_seconds"]
        rows = []
        while time.time() < deadline:
            rows = devlogs_events(v, test_run_id, events)
            if len(rows) >= len(events):
                time.sleep(5)       # catch anything arriving late (duplicates)
                rows = devlogs_events(v, test_run_id, events)
                break
            time.sleep(5)
        dest.extend(rows)
        idx = collections.Counter(r["index"] for r in rows)
        return len(rows) == len(events) and set(idx) == {DEV}, f"{len(rows)} of {len(events)} events found with index=devlogs sf_replay_id::{test_run_id}; indexes {dict(idx)}"

    suite.check("C2", "events appear in index=devlogs", c2)

    by_id = {}
    for r in dest:
        by_id.setdefault(r.get("sf_splunk_event_id"), []).append(r)
    ds_by_id = {e["sf_splunk_event_id"]: e for e in events}

    def c3():
        bad = []
        for eid, e in ds_by_id.items():
            for r in by_id.get(eid, [])[:1]:
                for k in ("host", "source", "sourcetype"):
                    if r[k] != e[f"sf_orig_{k}"]:
                        bad.append((e["sf_seq"], k, r[k]))
                if r.get("sf_orig_index") != e["sf_orig_index"]:
                    bad.append((e["sf_seq"], "sf_orig_index"))
        st = collections.Counter(r["sourcetype"] for r in dest)
        return not bad and bool(dest), f"sourcetypes {dict(st)}; original host/source/sourcetype kept on every event; sf_orig_index kept; mismatches {bad[:5] or 'none'}"

    suite.check("C3", "original sourcetype, source and host preserved in devlogs", c3)

    def c4():
        replay_start = (replay.get("started_at") or 0) / 1000
        bad, lag = [], []
        for eid, e in ds_by_id.items():
            for r in by_id.get(eid, [])[:1]:
                t, it = float(r["_time"]), int(r["_indextime"])
                if abs(t - e["_time"]) > 0.0005:
                    bad.append((e["sf_seq"], "_time", t, e["_time"]))
                if it + 1 < replay_start:
                    bad.append((e["sf_seq"], "_indextime before replay"))
                lag.append(it - t)
        facts["devlogs_index_minus_event_seconds"] = {"min": round(min(lag), 1), "max": round(max(lag), 1)} if lag else None
        return not bad and bool(lag), (f"_time equals the original event time on every event; _indextime is the replay time, "
                                       f"{facts['devlogs_index_minus_event_seconds']} seconds after event time; problems {bad[:3] or 'none'}")

    suite.check("C4", "timestamps valid: original event time kept, not replay time", c4)

    def c5():
        bad = [e["sf_seq"] for eid, e in ds_by_id.items() if not by_id.get(eid) or by_id[eid][0]["h"] != e["sf_raw_sha256"]]
        return not bad, f"{len(ds_by_id) - len(bad)}/{len(ds_by_id)} devlogs events have sha256(_raw) equal to the sample's; different or missing: {bad[:5] or 'none'}"

    suite.check("C5", "events in devlogs match the source sample", c5)

    def c6():
        _, body = sp.call("GET", f"/servicesNS/nobody/{S['app']}/data/inputs/http/{S['hec_input']}")
        c = body["entry"][0]["content"]
        token = sflib.remember_secret(c["token"])
        url = CONF["splunk"]["worker_hec"]
        # Only the token's allowed index may be written: an event for another,
        # existing index (unrouted, the catch-all) must be rejected. Sent from the
        # Worker, cert verified against the homelab CA; the token goes in on stdin.
        payload = json.dumps({"event": f"siem-factory HEC scope probe {test_run_id} - must be rejected",
                              "index": "unrouted", "sourcetype": "sscpoc:probe"})
        p = subprocess.run(["docker", "exec", "-i", "cribl-worker", "curl", "-s", "--cacert", "/opt/cribl/tls/ca.crt",
                            "-o", "-", "-w", "\n%{http_code}", "-H", "@-", url, "-d", payload],
                           input=f"Authorization: Splunk {token}\n", capture_output=True, text=True, timeout=30)
        body_txt, _, code = p.stdout.rpartition("\n")
        rejected = code == "400" and '"code":7' in body_txt.replace(" ", "")
        cfg_ok = c["index"] == DEV and c["indexes"] == [DEV]
        return cfg_ok and rejected, f"token {S['hec_input']}: index={c['index']} indexes={c['indexes']}; write to index=unrouted -> HTTP {code} {sflib.scrub(body_txt)[:80]}"

    suite.check("C6", "HEC token can write only to devlogs", c6)

    # ------------------------------------------------------------ D: integrity
    def d1():
        n_src, n_ds, n_dst = len(src_now), len(events), len({r["sf_splunk_event_id"] for r in dest})
        facts.update(count_source=n_src, count_dataset=n_ds, count_devlogs=len(dest))
        return n_src == n_ds == n_dst == len(dest), f"source {n_src}, dataset {n_ds}, devlogs {len(dest)} ({n_dst} distinct)"

    suite.check("D1", "event counts: source = dataset = devlogs", d1)

    def d2():
        h_src = collections.Counter(s["h"] for s in src_now.values())
        h_ds = collections.Counter(e["sf_raw_sha256"] for e in events)
        h_dst = collections.Counter(r["h"] for r in dest)
        digest = hashlib.sha256("".join(sorted(h_ds.elements())).encode()).hexdigest()
        facts["raw_hash_set_sha256"] = digest
        return h_src == h_ds == h_dst, f"multisets of sha256(_raw) identical in all three; set digest {digest[:16]}"

    suite.check("D2", "raw-event hashes: source = dataset = devlogs", d2)

    def d3():
        dup_ds = len(events) - len(ds_by_id)
        dup_dst = sum(len(v_) - 1 for v_ in by_id.values() if len(v_) > 1)
        earliest, latest = window(events, pad=2)
        rows = v.export(f"search index={DEV} sf_dataset_id::{dataset_id} | stats count by sf_replay_id", earliest, latest)
        copies = {r["sf_replay_id"]: int(r["count"]) for r in rows}
        facts["devlogs_copies_by_replay"] = copies
        return dup_ds == 0 and dup_dst == 0, (f"duplicates: {dup_ds} in dataset, {dup_dst} within this replay "
                                              f"({manifest['duplicates_dropped']} dropped at capture); replays of this dataset in devlogs: {copies}")

    suite.check("D3", "no duplicates within a run; repeated replays kept apart by replay id", d3)

    def d4():
        bad, lag = [], []
        for e in events:
            collected = dt.datetime.fromisoformat(e["sf_collected_at"]).timestamp()
            if not e["_time"] < collected:
                bad.append((e["sf_seq"], "collected before event time"))
            if e["sf_splunk_indextime"] + 1 < e["_time"]:
                bad.append((e["sf_seq"], "indexed before event time"))
            lag.append(collected - e["_time"])
        facts["collection_minus_event_seconds"] = {"min": round(min(lag)), "max": round(max(lag))}
        return not bad, (f"every event: original _time < sf_collected_at, and source _indextime >= _time; "
                         f"collected {facts['collection_minus_event_seconds']} seconds after event time; problems {bad[:3] or 'none'}")

    suite.check("D4", "event time distinguished from collection and index time", d4)

    def a8():
        # After the replay devlogs has data, so this would find some if the collector
        # could read it. The raw export lines go to a probe directory (passthru), so
        # the test sees what Splunk returned, not just Cribl's line count.
        probe_id = f"{test_run_id}.loopprobe"
        probe_dir = Path(CONF["paths"]["host_data"]) / "incoming" / probe_id

        def mutate(job):
            job["collector"]["conf"]["search"] = f"search index={DEV} earliest=-72h latest=now | head 5 | fields _raw"
            job["input"]["pipeline"] = "passthru"
        info = sflib.run_saved_job(cr, CONF["collector_id"], mutate=mutate, metadata={"sf_run_id": probe_id}, retries=0)
        j = sflib.job_summary(info)
        lines = [json.loads(json.loads(line)["_raw"]) for f in sflib.wait_files(probe_dir, 30) for line in f.read_text().splitlines()]
        shutil.rmtree(probe_dir, ignore_errors=True)
        results = [x for x in lines if "result" in x]
        msgs = [m.get("text") for x in lines for m in x.get("messages", [])]
        return not results and not j["tasks_failed"], (
            f"collector account searching index={DEV} (which has {len(dest)}+ events from this run): "
            f"{len(results)} results; {len(lines)} export control lines {msgs[:2]}")

    suite.check("A8", "collector cannot read its own output index (no collection loop)", a8)

    # ------------------------------------------------------------ report
    passed = sum(r["result"] == "PASS" for r in suite.results)
    facts["finished_at"] = sflib.now_iso()
    report = {"test_run_id": test_run_id, "summary": {"passed": passed, "failed": len(suite.results) - passed,
                                                      "total": len(suite.results),
                                                      "result": "PASS" if passed == len(suite.results) else "FAIL"},
              "facts": facts, "results": sorted(suite.results, key=lambda r: r["id"])}
    out = SF_DIR / "reports" / test_run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "report.md").write_text(markdown(report))
    shutil.copy(out / "report.md", SF_DIR / "reports" / "latest.md")
    print(markdown(report))
    sys.exit(0 if report["summary"]["result"] == "PASS" else 1)


def markdown(rep):
    s, f = rep["summary"], rep["facts"]
    lines = [f"# POC test report {rep['test_run_id']}", "",
             f"**{s['result']}**: {s['passed']}/{s['total']} passed, {s['failed']} failed", "",
             f"- dataset: `{f.get('dataset_id')}` (capture job `{(f.get('capture_job') or {}).get('job_id')}`)",
             f"- replay id: `{f.get('replay_id')}` (search `index=devlogs sf_replay_id::{f.get('replay_id')}`)",
             f"- counts: source {f.get('count_source')}, dataset {f.get('count_dataset')}, devlogs {f.get('count_devlogs')}"
             f" (available in window: {f.get('available_in_window')}, max {f.get('max_events')})",
             f"- started {f.get('started_at')}, finished {f.get('finished_at')}", "",
             "| Test | Result | Detail |", "|---|---|---|"]
    for r in rep["results"]:
        detail = r["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r['id']} {r['name']} | {r['result']} | {detail} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
