#!/usr/bin/env python3
"""Run the poc-splunk-search collector once and save the result as a dataset.

    ./scripts/collect.py                  head 100 (max_events_default)
    ./scripts/collect.py --max 1000       up to max_events_limit (1000)

Each run gets a run id, passed to Cribl as job metadata (sf_run_id) and used as
the dataset id. Cribl writes the events to /srv/siem-factory/incoming/<run_id>/;
this script then validates them and moves them into the library as
samples/<category>/<dataset_id>/{events.jsonl,manifest.json,SHA256SUMS}.
Prints the manifest. Exits 1 if the job failed or produced no valid events.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import dataset
import sflib
from sflib import CONF, log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max", type=int, default=CONF["max_events_default"], help="maximum events (head N)")
    ap.add_argument("--run-id", help="use this run id instead of a generated one")
    args = ap.parse_args()
    if not 1 <= args.max <= CONF["max_events_limit"]:
        sys.exit(f"--max must be 1..{CONF['max_events_limit']}")
    src = CONF["source"]
    run_id = sflib.check_id(args.run_id or sflib.new_run_id(src["category"]), "run id")
    host_data = Path(CONF["paths"]["host_data"])
    incoming = host_data / "incoming" / run_id
    if incoming.exists() or dataset.dataset_dir(src["category"], run_id).exists():
        sys.exit(f"run id {run_id} already used")

    search_used = {}

    def bound(job):
        conf = job["collector"]["conf"]
        search, n = re.subn(r"\|\s*head\s+\d+", f"| head {args.max}", conf["search"])
        # Never let a run go unbounded, whatever the saved search says.
        if n != 1 or "earliest=" not in search or conf.get("earliest") in (None, "", "0"):
            sys.exit("collector search must have exactly one `| head N`, an inline earliest= and an earliest setting")
        conf["search"] = search
        search_used.update(search=search, earliest=conf["earliest"], latest=conf["latest"])

    cr = sflib.admin_cribl()
    log(f"collect: run {run_id}, head {args.max}")
    info = sflib.run_saved_job(cr, CONF["collector_id"], mutate=bound,
                               metadata={"sf_run_id": run_id, "sf_dataset_id": run_id})
    job = sflib.job_summary(info)
    log(f"collect: job {job['state']}, tasks failed {job['tasks_failed']}")
    if job["state"] != "finished" or job["tasks_failed"]:
        print(json.dumps({"run_id": run_id, "job": job}, indent=2))
        sys.exit(1)
    files = sflib.wait_files(incoming, CONF["timeouts"]["file_settle_seconds"])
    if not files:
        print(json.dumps({"run_id": run_id, "job": job, "error": "no events written"}, indent=2))
        sys.exit(1)
    manifest = dataset.build(run_id, src["category"], files, job=job, search=search_used, max_events=args.max)
    shutil.rmtree(incoming)
    print(json.dumps(manifest, indent=2))
    if manifest["event_count"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (sflib.HTTPError, RuntimeError) as e:
        sys.exit(f"error: {sflib.scrub(str(e))}")
