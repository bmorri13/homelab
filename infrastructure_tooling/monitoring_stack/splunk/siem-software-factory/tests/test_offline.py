"""Offline tests: no Splunk, Cribl or host needed (CI-safe).

    python3 -m unittest discover -s tests -v

Cover the sample-record contract (dataset.problems), dataset building (dedupe,
ordering, manifest, checksums), and that the Cribl definitions under cribl/
agree with config/poc.json and stay bounded and isolated. The records here are
SYNTHETIC: they exercise the format only and are not captured events.
"""
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

SF = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SF / "scripts"))
import dataset  # noqa: E402
import sflib  # noqa: E402

CONF = sflib.CONF


def record(i, raw=None, **over):
    raw = raw if raw is not None else json.dumps({"synthetic": True, "n": i})
    rec = {"_raw": raw, "_time": 1700000000.0 + i, "host": "h1", "source": "s1", "sourcetype": "falco:ocsf",
           "index": "security", "sf_schema": dataset.EVENT_SCHEMA, "sf_orig_index": "security",
           "sf_orig_host": "h1", "sf_orig_source": "s1", "sf_orig_sourcetype": "falco:ocsf",
           "sf_splunk_event_id": f"srv/security~1~X/1:{i}", "sf_splunk_indextime": 1700000005 + i,
           "sf_raw_sha256": hashlib.sha256(raw.encode()).hexdigest(), "sf_collector_id": "poc-splunk-search",
           "sf_run_id": "synthetic-1", "sf_collected_at": "2023-11-14T22:20:00.000Z", "sf_result_offset": i}
    rec.update(over)
    return rec


class RecordContract(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(dataset.problems(record(0)), [])

    def test_hash_mismatch(self):
        self.assertIn("sf_raw_sha256 does not match _raw", dataset.problems(record(0, sf_raw_sha256="0" * 64)))

    def test_missing_field(self):
        r = record(0)
        del r["sf_orig_sourcetype"]
        self.assertEqual(dataset.problems(r), ["missing sf_orig_sourcetype"])

    def test_metadata_rewritten(self):
        self.assertIn("host differs from sf_orig_host", dataset.problems(record(0, host="cribl-worker")))

    def test_loop_guard(self):
        self.assertIn("captured from the dev index", dataset.problems(record(0, sf_orig_index=CONF["dev_index"], index=CONF["dev_index"])))

    def test_unicode_hash_is_utf8(self):
        raw = '{"msg":"café ✓"}'
        self.assertEqual(dataset.problems(record(0, raw=raw)), [])


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = dict(CONF["paths"])
        CONF["paths"]["host_data"] = self.tmp.name

    def tearDown(self):
        CONF["paths"].update(self.old)
        self.tmp.cleanup()

    def test_build(self):
        src = Path(self.tmp.name) / "in.jsonl"
        recs = [record(2), record(0), record(1), record(1), record(3, sf_raw_sha256="bad")]
        src.write_text("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
        m = dataset.build("falco-synthetic", "falco", [src], job={"job_id": "j", "state": "finished"},
                          search={"search": "search index=security | head 5", "earliest": "-48h", "latest": "now"}, max_events=5)
        self.assertEqual(m["event_count"], 3)
        self.assertEqual(m["duplicates_dropped"], 1)
        self.assertEqual(m["invalid_record_count"], 2)
        d = dataset.dataset_dir("falco", "falco-synthetic")
        lines = (d / "events.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(x)["sf_seq"] for x in lines], [0, 1, 2])
        self.assertEqual([json.loads(x)["_time"] for x in lines], sorted(json.loads(x)["_time"] for x in lines))
        self.assertEqual(m["files"]["events.jsonl"]["sha256"], hashlib.sha256((d / "events.jsonl").read_bytes()).hexdigest())
        sums = dict(reversed(line.split("  ")) for line in (d / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(sums["manifest.json"], hashlib.sha256((d / "manifest.json").read_bytes()).hexdigest())
        self.assertEqual(dataset.latest(), "falco-synthetic")


class Definitions(unittest.TestCase):
    def load(self, kind, name):
        return json.loads((SF / "cribl" / kind / f"{name}.json").read_text())

    def test_capture_collector_bounded_and_isolated(self):
        job = self.load("collectors", CONF["collector_id"])
        self.assertTrue(all(sflib.collector_checks(job).values()), sflib.collector_checks(job))
        head = int(re.search(r"\|\s*head\s+(\d+)", job["collector"]["conf"]["search"]).group(1))
        self.assertEqual(head, CONF["max_events_default"])

    def test_replay_collector_isolated(self):
        job = self.load("collectors", CONF["replay_collector_id"])
        self.assertFalse(job["schedule"]["enabled"])
        self.assertFalse(job["input"]["sendToRoutes"])
        self.assertEqual(job["input"]["output"], CONF["cribl"]["replay_output"])
        self.assertIn("_set-by-replay", job["collector"]["conf"]["path"])   # a bare UI run replays nothing

    def test_outputs_verify_tls_and_use_secrets(self):
        hec = self.load("outputs", CONF["cribl"]["replay_output"])
        self.assertTrue(hec["rejectUnauthorized"])
        self.assertEqual(hec["authType"], "secret")
        self.assertNotIn("token", hec)
        self.assertEqual(hec["url"], CONF["splunk"]["worker_hec"])

    def test_replay_pipeline_forces_dev_index(self):
        code = next(f for f in self.load("pipelines", CONF["cribl"]["replay_pipeline"])["conf"]["functions"] if f["id"] == "code")
        self.assertIn(f"__e.index = '{CONF['dev_index']}'", code["conf"]["code"])

    def test_no_secrets_in_definitions(self):
        for f in (SF / "cribl").rglob("*.json"):
            text = f.read_text()
            self.assertNotRegex(text, r'"(token|password|authTokens|secretValue)"\s*:\s*"?[^"\s]{8,}', f.name)


if __name__ == "__main__":
    unittest.main()
