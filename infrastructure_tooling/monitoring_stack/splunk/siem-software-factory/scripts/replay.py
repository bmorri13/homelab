#!/usr/bin/env python3
"""Replay one saved dataset into Splunk index=devlogs through Cribl.

    ./scripts/replay.py                    the newest dataset
    ./scripts/replay.py <dataset_id>

Runs collector poc-dataset-replay (filesystem) over the dataset's events.jsonl
only, through poc-devlogs-replay to the poc_devlogs_hec destination. Every run
gets a replay id (indexed field sf_replay_id), so repeated replays of the same
dataset stay apart; search one with
    index=devlogs sf_replay_id::<replay_id>
Writes reports/replays/<replay_id>.json and prints it.
"""
import argparse
import json
import sys
from pathlib import Path

import dataset
import sflib
from sflib import CONF, SF_DIR, log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset_id", nargs="?")
    ap.add_argument("--replay-id")
    args = ap.parse_args()
    dataset_id = args.dataset_id or dataset.latest()
    category, d = dataset.find(dataset_id)
    manifest = json.loads((d / "manifest.json").read_text())
    replay_id = sflib.check_id(args.replay_id or sflib.new_run_id("replay"), "replay id")
    worker_dir = f"{CONF['paths']['worker_data']}/samples/{category}/{dataset_id}"

    def target(job):
        job["collector"]["conf"].update(path=worker_dir, recurse=False)

    cr = sflib.admin_cribl()
    log(f"replay: {dataset_id} ({manifest['event_count']} events) as {replay_id}")
    info = sflib.run_saved_job(cr, CONF["replay_collector_id"], mutate=target,
                               metadata={"sf_replay_id": replay_id})
    job = sflib.job_summary(info)
    report = {"replay_id": replay_id, "dataset_id": dataset_id, "category": category,
              "events_in_dataset": manifest["event_count"], "started_at": info.get("stats", {}).get("state", {}).get("initializing"),
              "finished_at": sflib.now_iso(), "dev_index": CONF["dev_index"], "job": job}
    out = SF_DIR / "reports" / "replays"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{replay_id}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if job["state"] != "finished" or job["tasks_failed"]:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (sflib.HTTPError, RuntimeError) as e:
        sys.exit(f"error: {sflib.scrub(str(e))}")
