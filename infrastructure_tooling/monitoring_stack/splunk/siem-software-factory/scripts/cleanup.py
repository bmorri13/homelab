#!/usr/bin/env python3
"""Remove only what this POC created. Prints the plan; changes nothing without --yes.

    ./scripts/cleanup.py                       show what would be removed
    ./scripts/cleanup.py --yes                 remove it (keeps saved datasets and reports)
    ./scripts/cleanup.py --yes --purge-samples also delete /srv/siem-factory/samples
    ./scripts/cleanup.py --yes --keep-index    keep index devlogs and its events

Removes, by exact name only:
  Cribl:  collectors poc-splunk-search, poc-dataset-replay; pipelines
          poc-sample-processing, poc-devlogs-replay; outputs poc_samples_fs,
          poc_devlogs_hec; breaker rulesets poc-splunk-search-json,
          poc-dataset-jsonl; secrets poc_splunk_search_token, poc_devlogs_hec_token;
          then commits and deploys (refuses if the group has other pending changes)
  Splunk: HEC token sscpoc_devlogs; users sscpoc-search-collector and
          sscpoc-validator with their tokens; roles sscpoc_*; index devlogs
          (unless --keep-index); app siem_factory_poc
  Host:   /srv/siem-factory/{incoming,.staging,tmp}, /root/.siem-factory
Leaves alone: everything else, including the Worker's CA trust and sample mount in
docker-compose.yml (harmless without the POC; revert them in git if unwanted).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import sflib
from sflib import CONF, GROUP, SF_DIR, log

S, C = CONF["splunk"], CONF["cribl"]
APP = S["app"]
NS = f"/servicesNS/nobody/{APP}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--purge-samples", action="store_true")
    ap.add_argument("--keep-index", action="store_true")
    args = ap.parse_args()
    env = sflib.read_env()
    sp = sflib.admin_splunk(env)
    cr = sflib.admin_cribl(env)
    done = []

    def act(what, fn):
        done.append(what)
        log(("" if args.yes else "would: ") + what)
        if args.yes:
            fn()

    # Cribl: collectors first, then what they use.
    pending = cr.pending_changes()
    if pending and args.yes:
        sys.exit(f"cribl: group {GROUP} has uncommitted changes: {pending}. Commit or discard them first.")
    cribl = [("collector", "lib/jobs", CONF["collector_id"]), ("collector", "lib/jobs", CONF["replay_collector_id"]),
             ("pipeline", "pipelines", C["capture_pipeline"]), ("pipeline", "pipelines", C["replay_pipeline"]),
             ("output", "system/outputs", C["capture_output"]), ("output", "system/outputs", C["replay_output"])]
    cribl += [("breaker ruleset", "lib/breakers", sflib.load_json(f)["id"]) for f in sorted((SF_DIR / "cribl/breakers").glob("*.json"))]
    cribl += [("secret", "system/secrets", C["search_token_secret"]), ("secret", "system/secrets", C["hec_token_secret"])]
    for kind, base, oid in cribl:
        if cr.get_item(f"/m/{GROUP}/{base}/{oid}") is not None:
            act(f"cribl: delete {kind} {oid}", lambda b=base, o=oid: cr.call("DELETE", f"/m/{GROUP}/{b}/{o}"))
    if args.yes and cr.pending_changes():
        version = cr.commit_deploy("siem-software-factory POC: cleanup.py")
        log(f"cribl: committed and deployed {version[:8]}")

    # Splunk
    if sp.exists(f"{NS}/data/inputs/http/{S['hec_input']}"):
        act(f"splunk: delete HEC token {S['hec_input']}", lambda: sp.call("DELETE", f"{NS}/data/inputs/http/{S['hec_input']}"))
    for user in (S["collector_user"], S["validator_user"]):
        if sp.exists(f"/services/authentication/users/{user}"):
            def drop(u=user):
                _, b = sp.call("GET", "/services/authorization/tokens", {"username": u, "count": "0"})
                for e in b.get("entry", []):
                    sp.call("DELETE", f"/services/authorization/tokens/{u}", {"id": e["name"]}, ok=(200, 404))
                sp.call("DELETE", f"/services/authentication/users/{u}")
            act(f"splunk: delete user {user} and its tokens", drop)
    for role in (S["collector_role"], S["validator_role"]):
        if sp.exists(f"{NS}/configs/conf-authorize/role_{role}"):
            act(f"splunk: delete role {role}", lambda r=role: sp.call("DELETE", f"{NS}/configs/conf-authorize/role_{r}"))
    if not args.keep_index and sp.exists(f"/services/data/indexes/{CONF['dev_index']}"):
        act(f"splunk: delete index {CONF['dev_index']} and its events",
            lambda: sp.call("DELETE", f"{NS}/data/indexes/{CONF['dev_index']}"))
    if args.yes:
        sp.call("POST", "/services/authentication/providers/services/_reload")
    if sp.exists(f"/services/apps/local/{APP}") and not args.keep_index:
        act(f"splunk: delete app {APP}", lambda: sp.call("DELETE", f"/services/apps/local/{APP}"))

    # Host
    host = Path(CONF["paths"]["host_data"])
    for p in [host / "incoming", host / ".staging", host / "tmp", sflib.SECRETS_DIR] + ([host / "samples"] if args.purge_samples else []):
        if p.exists():
            act(f"host: remove {p}", lambda q=p: shutil.rmtree(q))
    if not done:
        log("nothing to remove")
    print(json.dumps({"removed" if args.yes else "would_remove": done}))


if __name__ == "__main__":
    try:
        main()
    except sflib.HTTPError as e:
        sys.exit(f"error: {e}")
