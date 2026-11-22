"""The sample library: dataset layout, manifest, validation, and the storage seam.

A dataset is one directory, samples/<category>/<dataset_id>/:
  events.jsonl   one record per event (schema siem-factory.event/v1, below),
                 sorted by original event time, then Splunk event id
  manifest.json  what was captured, from where, when, how many, and hashes
  SHA256SUMS     sha256sum -c compatible, for events.jsonl and manifest.json

Record fields:
  _raw, _time                  the original event and its original time (epoch s)
  host, source, sourcetype,    the original Splunk metadata, top level so Cribl and
  index                        HEC tooling read the record as-is
  sf_orig_{index,host,source,sourcetype}
                               the same values, kept apart so a tool that rewrites
                               the top-level ones (a reader, a replay) can't lose them
  sf_splunk_event_id           <splunk_server>/<_bkt>/<_cd>: unique per stored event
  sf_splunk_indextime          when the source Splunk indexed it (not event time)
  sf_raw_sha256, sf_raw_bytes  of _raw as UTF-8
  sf_collector_id, sf_run_id, sf_dataset_id, sf_collected_at, sf_seq, sf_schema

Storage: everything goes through LocalStore (a directory). To move the library to
S3 or MinIO, implement the same four methods over a bucket prefix
(put_bytes/get_bytes/exists/list) and select it in config; Cribl's side changes
from the filesystem destination/collector to the S3 ones with the same layout.
"""
import collections
import hashlib
import json
from pathlib import Path

import sflib
from sflib import CONF

EVENT_SCHEMA = "siem-factory.event/v1"
DATASET_SCHEMA = "siem-factory.dataset/v1"
REQUIRED = ("_raw", "_time", "host", "source", "sourcetype", "index", "sf_schema", "sf_orig_index",
            "sf_orig_host", "sf_orig_source", "sf_orig_sourcetype", "sf_splunk_event_id",
            "sf_splunk_indextime", "sf_raw_sha256", "sf_collector_id", "sf_run_id", "sf_collected_at")


class LocalStore:
    """A dataset store on the local filesystem, rooted at <root>."""

    backend = "local"

    def __init__(self, root):
        self.root = Path(root)

    def uri(self, key):
        return f"file://{self.root / key}"

    def put_bytes(self, key, data):
        path = self.root / key
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get_bytes(self, key):
        return (self.root / key).read_bytes()

    def exists(self, key):
        return (self.root / key).exists()

    def list(self, prefix=""):
        base = self.root / prefix
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()) if base.exists() else []


def store():
    return LocalStore(Path(CONF["paths"]["host_data"]) / "samples")


def dataset_key(category, dataset_id):
    return f"{category}/{dataset_id}"


def dataset_dir(category, dataset_id):
    return store().root / dataset_key(category, dataset_id)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def problems(rec):
    """Why a record is not a valid sample record (empty list if it is)."""
    out = [f"missing {k}" for k in REQUIRED if k not in rec]
    if out:
        return out
    if rec["sf_schema"] != EVENT_SCHEMA:
        out.append(f"schema {rec['sf_schema']}")
    if sha256(rec["_raw"].encode()) != rec["sf_raw_sha256"]:
        out.append("sf_raw_sha256 does not match _raw")
    if not isinstance(rec["_time"], (int, float)) or rec["_time"] <= 0:
        out.append("bad _time")
    for k in ("index", "host", "source", "sourcetype"):
        if rec[k] != rec[f"sf_orig_{k}"]:
            out.append(f"{k} differs from sf_orig_{k}")
    if rec["sf_orig_index"] == CONF["dev_index"]:
        out.append("captured from the dev index")
    return out


def build(dataset_id, category, files, *, job, search, max_events):
    """Validate the destination's files and write them into the library."""
    records, invalid, seen, dups = [], [], set(), 0
    for f in files:
        for n, line in enumerate(Path(f).read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                invalid.append({"file": Path(f).name, "line": n, "problems": ["not JSON"]})
                continue
            p = problems(rec)
            if p:
                invalid.append({"file": Path(f).name, "line": n, "problems": p})
                continue
            if rec["sf_splunk_event_id"] in seen:
                dups += 1
                continue
            seen.add(rec["sf_splunk_event_id"])
            records.append(rec)
    records.sort(key=lambda r: (r["_time"], r["sf_splunk_event_id"]))
    lines = []
    for seq, rec in enumerate(records):
        rec["sf_dataset_id"] = dataset_id
        rec["sf_seq"] = seq
        rec["sf_raw_bytes"] = len(rec["_raw"].encode())
        lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    events = ("\n".join(lines) + "\n").encode() if lines else b""

    def counts(field):
        return dict(collections.Counter(r[field] for r in records).most_common())

    times = [r["_time"] for r in records]
    collected = sorted(r["sf_collected_at"] for r in records)
    st = store()
    key = dataset_key(category, dataset_id)
    manifest = {
        "schema": DATASET_SCHEMA,
        "dataset_id": dataset_id,
        "category": category,
        "created_at": sflib.now_iso(),
        "event_count": len(records),
        "duplicates_dropped": dups,
        "invalid_records": invalid[:50],
        "invalid_record_count": len(invalid),
        "capture": {
            "collector_id": CONF["collector_id"],
            "run_id": dataset_id,
            "cribl_job": {k: job.get(k) for k in ("job_id", "state", "tasks_finished", "tasks_failed")},
            "splunk_search": search.get("search"),
            "earliest": search.get("earliest"),
            "latest": search.get("latest"),
            "max_events": max_events,
            "truncated_at_max": len(records) >= max_events,
            "collected_at_first": collected[0] if collected else None,
            "collected_at_last": collected[-1] if collected else None,
        },
        "source": {
            "splunk_api": CONF["splunk"]["api"],
            "indexes": counts("sf_orig_index"),
            "sourcetypes": counts("sf_orig_sourcetype"),
            "hosts": counts("sf_orig_host"),
            "source_count": len(set(r["sf_orig_source"] for r in records)),
        },
        "event_time": {
            "earliest_epoch": min(times) if times else None,
            "latest_epoch": max(times) if times else None,
            "earliest": sflib.iso_from_epoch(min(times)) if times else None,
            "latest": sflib.iso_from_epoch(max(times)) if times else None,
            "note": "_time is the original event time from the source Splunk; sf_collected_at is when Cribl collected it; sf_splunk_indextime is when the source Splunk indexed it.",
        },
        "files": {"events.jsonl": {"sha256": sha256(events), "bytes": len(events), "lines": len(lines),
                                   "record_schema": EVENT_SCHEMA}},
        "storage": {"backend": st.backend, "uri": st.uri(key)},
        "replay": {"collector_id": CONF["replay_collector_id"], "pipeline": CONF["cribl"]["replay_pipeline"],
                   "output": CONF["cribl"]["replay_output"], "dev_index": CONF["dev_index"]},
        "handling": "Real homelab security telemetry, not sanitized. Keep out of git; copy only to private storage.",
    }
    st.put_bytes(f"{key}/events.jsonl", events)
    mbytes = (json.dumps(manifest, indent=2, sort_keys=False) + "\n").encode()
    st.put_bytes(f"{key}/manifest.json", mbytes)
    st.put_bytes(f"{key}/SHA256SUMS", f"{sha256(events)}  events.jsonl\n{sha256(mbytes)}  manifest.json\n".encode())
    return manifest


def find(dataset_id):
    """(category, dir) of a dataset id, searching every category."""
    sflib.check_id(dataset_id, "dataset id")
    for p in store().root.glob(f"*/{dataset_id}/manifest.json"):
        return p.parent.parent.name, p.parent
    raise SystemExit(f"dataset {dataset_id} not found under {store().root}")


def latest():
    manifests = sorted(store().root.glob("*/*/manifest.json"), key=lambda p: json.loads(p.read_text())["created_at"])
    if not manifests:
        raise SystemExit("no datasets yet: run `make collect`")
    return manifests[-1].parent.name


def load(dataset_id):
    category, d = find(dataset_id)
    manifest = json.loads((d / "manifest.json").read_text())
    events = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines() if line.strip()]
    return manifest, events, d
