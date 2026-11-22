#!/usr/bin/env python3
"""Create or update every POC resource in Splunk and Cribl. Safe to re-run.

    ./scripts/setup.py              create what is missing, fix what drifted
    ./scripts/setup.py --rotate     also mint new Splunk tokens and re-store them
    ./scripts/setup.py --plan       only print what would change

Splunk (all REST, all inside the POC app siem_factory_poc, so nothing in the
repo-managed apps or system/local changes):
  - index devlogs (14 days, 2 GB cap)
  - role sscpoc_search_collector: search only, index security only, devlogs
    explicitly disallowed, 72h time window, 2 concurrent searches, 5 min each
  - role sscpoc_validator: the same, plus devlogs (for the delivery tests)
  - users sscpoc-search-collector and sscpoc-validator: random passwords that are
    never stored; both only ever use tokens
  - HEC token sscpoc_devlogs: index devlogs only
Tokens: the collector's goes into Cribl secret poc_splunk_search_token, the HEC
token into poc_devlogs_hec_token, the validator's into /root/.siem-factory
(0600). A token is replaced when it is missing, disabled, or expires within
token_renew_days; the user's older tokens are then deleted.
Cribl (Leader API, Worker Group default): the two secrets, outputs, pipelines and
collectors under cribl/. Refuses to run while the group has uncommitted changes
it did not make, so it never commits someone else's work.
"""
import argparse
import json
import secrets
import sys
import time

import sflib
from sflib import CONF, GROUP, SF_DIR, log

S = CONF["splunk"]
C = CONF["cribl"]
APP = S["app"]
NS = f"/servicesNS/nobody/{APP}"
DEV = CONF["dev_index"]
SRC = CONF["source"]["index"]

# Capabilities the built-in [default] role stanza grants every role and that can
# write data or schedule work. Switched off for both POC roles.
WRITE_CAPS_OFF = {"run_collect": "disabled", "run_mcollect": "disabled",
                  "edit_own_objects": "disabled", "schedule_rtsearch": "disabled"}
QUOTAS = {"srchJobsQuota": "2", "cumulativeSrchJobsQuota": "2", "rtSrchJobsQuota": "0",
          "cumulativeRTSrchJobsQuota": "0", "srchMaxTime": "300", "srchDiskQuota": "100",
          "srchFederatedProvidersAllowed": "", "srchFederatedProvidersDefault": ""}
ROLES = {
    S["collector_role"]: {
        "search": "enabled", **WRITE_CAPS_OFF, **QUOTAS,
        "srchIndexesAllowed": SRC, "srchIndexesDefault": SRC, "srchIndexesDisallowed": DEV,
        # Never more than 72h back or 72h wide, whatever the search asks for.
        "srchTimeWin": "259200", "srchTimeEarliest": "259200",
    },
    S["validator_role"]: {
        "search": "enabled", **WRITE_CAPS_OFF, **QUOTAS,
        "srchIndexesAllowed": f"{SRC};{DEV}", "srchIndexesDefault": DEV,
        # Datasets can be replayed days after capture; 30 days covers devlogs' retention.
        "srchTimeWin": "2592000", "srchTimeEarliest": "2592000",
    },
}
INDEX = {"homePath": f"volume:local/{DEV}/db", "coldPath": f"volume:local/{DEV}/colddb",
         "thawedPath": f"$SPLUNK_DB/{DEV}/thaweddb", "frozenTimePeriodInSecs": "1209600",
         "maxTotalDataSizeMB": "2048"}


class Plan:
    def __init__(self, dry):
        self.dry, self.changes = dry, []

    def do(self, what, fn=None):
        self.changes.append(what)
        log(("would: " if self.dry else "") + what)
        if not self.dry and fn:
            return fn()


# ---------------------------------------------------------------- Splunk


def setup_app(sp, plan):
    if not sp.exists(f"/services/apps/local/{APP}"):
        plan.do(f"splunk: create app {APP}", lambda: sp.call("POST", "/services/apps/local", {
            "name": APP, "label": "SIEM software factory POC", "visible": "false",
            "description": "Owns the siem-software-factory POC objects (devlogs, sscpoc roles, HEC token). Delete it to remove them."}))


def setup_index(sp, plan):
    status, body = sp.call("GET", f"/services/data/indexes/{DEV}", ok=None)
    if status == 404:
        plan.do(f"splunk: create index {DEV}", lambda: sp.call("POST", f"{NS}/data/indexes", {"name": DEV, **INDEX}))
        return
    cur = body["entry"][0]["content"]
    drift = {k: v for k, v in INDEX.items() if k in ("frozenTimePeriodInSecs", "maxTotalDataSizeMB") and str(cur.get(k)) != v}
    if drift:
        plan.do(f"splunk: update index {DEV} {sorted(drift)}", lambda: sp.call("POST", f"{NS}/data/indexes/{DEV}", drift))


def setup_roles(sp, plan):
    changed = False
    for role, want in ROLES.items():
        stanza = f"role_{role}"
        status, body = sp.call("GET", f"{NS}/configs/conf-authorize/{stanza}", ok=None)
        if status == 404:
            plan.do(f"splunk: create role {role}", lambda: sp.call("POST", f"{NS}/configs/conf-authorize", {"name": stanza, **want}))
            changed = True
            continue
        cur = body["entry"][0]["content"]
        drift = {k: v for k, v in want.items() if str(cur.get(k, "")) != v}
        if drift:
            plan.do(f"splunk: update role {role} {sorted(drift)}", lambda: sp.call("POST", f"{NS}/configs/conf-authorize/{stanza}", drift))
            changed = True
    if changed and not plan.dry:
        # Roles written through conf-authorize only become usable (for users, and
        # under /services/authorization/roles) after the auth providers reload.
        sp.call("POST", "/services/authentication/providers/services/_reload")


def setup_user(sp, plan, user, role):
    status, body = sp.call("GET", f"/services/authentication/users/{user}", ok=None)
    if status == 404:
        def create():
            pw = sflib.remember_secret(secrets.token_urlsafe(32) + "aA1!")
            sp.call("POST", "/services/authentication/users", {"name": user, "password": pw, "roles": role,
                                                               "realname": "siem-software-factory POC (token only)"})
        plan.do(f"splunk: create user {user} (role {role}, random password, not stored)", create)
    elif body["entry"][0]["content"].get("roles") != [role]:
        plan.do(f"splunk: set {user} roles to [{role}]",
                lambda: sp.call("POST", f"/services/authentication/users/{user}", {"roles": role}))


def user_tokens(sp, user):
    _, body = sp.call("GET", "/services/authorization/tokens", {"username": user, "count": "0"})
    out = []
    for e in body.get("entry", []):
        c = e["content"]
        out.append({"id": e["name"], "status": c.get("status"), "exp": int(c.get("claims", {}).get("exp", 0))})
    return out


def token_good(tokens):
    renew = time.time() + S["token_renew_days"] * 86400
    return any(t["status"] == "enabled" and t["exp"] > renew for t in tokens)


def mint_token(sp, user, audience):
    _, body = sp.call("POST", "/services/authorization/tokens",
                      {"name": user, "audience": audience, "expires_on": S["token_lifetime"]})
    c = body["entry"][0]["content"]
    return c["id"], sflib.remember_secret(c["token"])


def drop_other_tokens(sp, user, keep_id):
    for t in user_tokens(sp, user):
        if t["id"] != keep_id:
            sp.call("DELETE", "/services/authorization/tokens/" + user, {"id": t["id"]}, ok=(200, 404))


def setup_hec(sp, plan):
    name = S["hec_input"]
    want = {"index": DEV, "indexes": DEV, "useACK": "0", "disabled": "0"}
    status, body = sp.call("GET", f"{NS}/data/inputs/http/{name}", ok=None)
    if status == 404:
        plan.do(f"splunk: create HEC token {name} (index {DEV} only)", lambda: sp.call(
            "POST", f"{NS}/data/inputs/http", {"name": name, **want,
                                               "description": "siem-software-factory POC: Cribl poc_devlogs_hec -> devlogs only"}))
        return True
    cur = body["entry"][0]["content"]
    idx = cur.get("indexes") or []
    if cur.get("index") != DEV or idx != [DEV] or str(cur.get("disabled")).lower() in ("1", "true"):
        plan.do(f"splunk: restrict HEC token {name} to {DEV}", lambda: sp.call("POST", f"{NS}/data/inputs/http/{name}", want))
    return False


def hec_token_value(sp):
    _, body = sp.call("GET", f"{NS}/data/inputs/http/{S['hec_input']}")
    return sflib.remember_secret(body["entry"][0]["content"]["token"])


# ---------------------------------------------------------------- Cribl


IGNORE = {"status", "notifications", "lib", "history", "savedState"}


def normalize(obj):
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in sorted(obj.items()) if k not in IGNORE}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    return obj


def subset_differs(want, cur):
    """True if any key in want has a different value in cur (cur may carry extra defaults)."""
    if isinstance(want, dict):
        if not isinstance(cur, dict):
            return True
        return any(subset_differs(v, cur.get(k)) for k, v in want.items())
    if isinstance(want, list):
        if not isinstance(cur, list) or len(want) != len(cur):
            return True
        return any(subset_differs(w, c) for w, c in zip(want, cur))
    return want != cur


def upsert(cr, plan, kind, base, obj):
    oid = obj["id"]
    cur = cr.get_item(f"{base}/{oid}")
    if cur is None:
        plan.do(f"cribl: create {kind} {oid}", lambda: cr.call("POST", base, obj))
    elif subset_differs(normalize(obj), normalize(cur)):
        plan.do(f"cribl: update {kind} {oid}", lambda: cr.call("PATCH", f"{base}/{oid}", obj))


def secret_exists(cr, sid):
    return cr.get_item(f"/m/{GROUP}/system/secrets/{sid}") is not None


def put_secret(cr, sid, value, description):
    obj = {"id": sid, "secretType": "text", "value": value, "description": description}
    if secret_exists(cr, sid):
        cr.call("PATCH", f"/m/{GROUP}/system/secrets/{sid}", obj)
    else:
        cr.call("POST", f"/m/{GROUP}/system/secrets", obj)


def our_paths(paths):
    mine = ("poc-", "poc_", "secrets.yml", "jobs.yml", "outputs.yml", "breakers.yml", "pipelines/poc")
    return all(any(m in p for m in mine) for p in paths)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rotate", action="store_true", help="mint new tokens even if the current ones are fine")
    ap.add_argument("--plan", action="store_true", help="print what would change, change nothing")
    args = ap.parse_args()
    plan = Plan(args.plan)
    env = sflib.read_env()
    sp = sflib.admin_splunk(env)
    cr = sflib.admin_cribl(env)

    pending = cr.pending_changes()
    if pending and not our_paths(pending):
        sys.exit(f"cribl: group {GROUP} has uncommitted changes this script did not make: {pending}. Commit or discard them first.")

    # Splunk objects
    setup_app(sp, plan)
    setup_index(sp, plan)
    setup_roles(sp, plan)
    setup_user(sp, plan, S["collector_user"], S["collector_role"])
    setup_user(sp, plan, S["validator_user"], S["validator_role"])
    hec_new = setup_hec(sp, plan)

    # Tokens. Collector token -> Cribl secret.
    if args.rotate or not token_good(user_tokens(sp, S["collector_user"])) or not secret_exists(cr, C["search_token_secret"]):
        def rotate_collector():
            tid, tok = mint_token(sp, S["collector_user"], "cribl poc-splunk-search")
            put_secret(cr, C["search_token_secret"], tok, "Splunk token for sscpoc-search-collector (read-only, index security). Managed by siem-software-factory/scripts/setup.py")
            drop_other_tokens(sp, S["collector_user"], tid)
        plan.do(f"splunk+cribl: mint {S['collector_user']} token into Cribl secret {C['search_token_secret']}", rotate_collector)

    # Validator token -> root-only file.
    have_file = sflib.VALIDATOR_TOKEN_FILE.exists()
    if args.rotate or not have_file or not token_good(user_tokens(sp, S["validator_user"])):
        def rotate_validator():
            tid, tok = mint_token(sp, S["validator_user"], "siem-software-factory tests")
            sflib.SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            sflib.SECRETS_DIR.chmod(0o700)
            tmp = sflib.VALIDATOR_TOKEN_FILE.with_suffix(".tmp")
            tmp.touch(mode=0o600)
            tmp.write_text(tok + "\n")
            tmp.replace(sflib.VALIDATOR_TOKEN_FILE)
            drop_other_tokens(sp, S["validator_user"], tid)
        plan.do(f"splunk: mint {S['validator_user']} token into {sflib.VALIDATOR_TOKEN_FILE}", rotate_validator)

    # HEC token -> Cribl secret (only when new, missing, or rotating; Splunk keeps the value).
    if hec_new or args.rotate or not secret_exists(cr, C["hec_token_secret"]):
        plan.do(f"cribl: store HEC token {S['hec_input']} in secret {C['hec_token_secret']}",
                lambda: put_secret(cr, C["hec_token_secret"], hec_token_value(sp),
                                   "Splunk HEC token sscpoc_devlogs (index devlogs only). Managed by siem-software-factory/scripts/setup.py"))

    # Cribl objects: breakers, outputs and pipelines before the collectors that use them.
    for f in sorted((SF_DIR / "cribl" / "breakers").glob("*.json")):
        upsert(cr, plan, "breaker ruleset", f"/m/{GROUP}/lib/breakers", sflib.load_json(f))
    for f in sorted((SF_DIR / "cribl" / "outputs").glob("*.json")):
        upsert(cr, plan, "output", f"/m/{GROUP}/system/outputs", sflib.load_json(f))
    for f in sorted((SF_DIR / "cribl" / "pipelines").glob("*.json")):
        upsert(cr, plan, "pipeline", f"/m/{GROUP}/pipelines", sflib.load_json(f))
    for f in sorted((SF_DIR / "cribl" / "collectors").glob("*.json")):
        upsert(cr, plan, "collector", f"/m/{GROUP}/lib/jobs", sflib.load_json(f))

    if not args.plan:
        pending = cr.pending_changes()
        if pending:
            if not our_paths(pending):
                sys.exit(f"cribl: unexpected pending changes, not committing: {pending}")
            version = cr.commit_deploy("siem-software-factory POC: setup.py")
            ok = cr.wait_worker_version(version)
            log(f"cribl: committed and deployed {version[:8]} ({len(pending)} files){'' if ok else ' - Worker not confirmed yet'}")
    if not plan.changes:
        log("everything already in place")
    print(json.dumps({"changes": plan.changes, "plan_only": args.plan}))


if __name__ == "__main__":
    try:
        main()
    except sflib.HTTPError as e:
        sys.exit(f"error: {e}")
