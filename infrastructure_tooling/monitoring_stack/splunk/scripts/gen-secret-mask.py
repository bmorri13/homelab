#!/usr/bin/env python3
"""Generate the Cribl secret_mask pipeline from gitleaks' default rules.

    ./scripts/gen-secret-mask.py [gitleaks.toml]

Without an argument it downloads the config of GITLEAKS_VERSION. It writes
cribl/local/cribl/pipelines/secret_mask/conf.yml, which splunk_out chains to.
Each gitleaks rule becomes one Mask function whose filter is the rule's keyword
list, so most events skip the regex. The rule's entropy threshold, secret group
and allowlist regexes and stopwords are applied in the replace expression.

Differences from gitleaks, all of which mask more rather than less:
  - Cribl regexes have no inline modifiers, so (?i:...) and (?-i:...) groups
    become plain groups and the whole rule is case-insensitive if any part was.
  - The global allowlist, path-only rules, allowlists that test the whole
    line or a file path, and allowlist entries that are themselves literal
    example keys are skipped.
gitleaks is MIT licensed (https://github.com/gitleaks/gitleaks).
"""
import json
import re
import sys
import tomllib
import urllib.request
import warnings
from pathlib import Path

GITLEAKS_VERSION = "8.30.1"
URL = f"https://raw.githubusercontent.com/gitleaks/gitleaks/v{GITLEAKS_VERSION}/config/gitleaks.toml"
OUT = Path(__file__).resolve().parent.parent / "cribl/local/cribl/pipelines/secret_mask/conf.yml"
MAX_GROUPS = 9  # Cribl exposes capture groups as g0..g9

POSIX = {"alnum": "A-Za-z0-9", "alpha": "A-Za-z", "digit": "0-9", "xdigit": "0-9A-Fa-f",
         "upper": "A-Z", "lower": "a-z", "space": r"\s", "word": r"\w",
         "punct": r"!-\/:-@\[-`{-~"}


def to_js(regex):
    """Translate a Go RE2 pattern to (JS pattern, flags)."""
    flags = "g"
    if "(?i" in regex:
        flags += "i"
    if "(?s" in regex:
        flags += "s"
    p = re.sub(r"\(\?[is]+\)", "", regex)            # (?i) anywhere
    p = re.sub(r"\(\?-?[is]+:", "(?:", p)             # (?i:  (?-i:  (?s:
    p = p.replace("(?P<", "(?<").replace(r"\z", "$").replace(r"\A", "^")
    p = re.sub(r"\[:(\w+):\]", lambda m: POSIX[m.group(1)], p)
    # escape bare / so the /.../flags literal stays intact
    p = re.sub(r"(?<!\\)((?:\\\\)*)/", r"\1\\/", p)
    return p, flags


def group_count(p):
    """Capturing groups in a JS pattern (plain and named, not (?: or lookarounds)."""
    p = re.sub(r"\\.", "", p)
    p = re.sub(r"\[(?:[^\]\\]|\\.)*\]", "", p)
    return len(re.findall(r"\((?!\?)|\(\?<(?![=!])", p))


def repeated_group(p):
    """True if a capturing group is quantified, e.g. ([a-z0-9]{4}-){3}."""
    stack, i, in_class = [], 0, False
    while i < len(p):
        c = p[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            stack.append(p[i + 1] != "?" or p[i + 1:i + 3] == "?<" and p[i + 3] not in "=!")
        elif c == ")":
            if stack.pop() and i + 1 < len(p) and p[i + 1] in "{*+?":
                return True
        i += 1
    return False


def js_str(s):
    return json.dumps(s)


def regex_literal(words):
    return "/" + "|".join(re.escape(w).replace("/", r"\/") for w in words) + "/i"


def rule_to_function(rule):
    pattern, flags = to_js(rule["regex"])
    groups = group_count(pattern)
    # Groups that are only alternation labels (jwt-base64) or repeated parts
    # (microsoft-teams-webhook) do not hold the secret: drop them, mask the whole match.
    if not rule.get("secretGroup") and (groups > MAX_GROUPS or repeated_group(pattern)):
        pattern = re.sub(r"\(\?<(?![=!])\w+>", "(?:", pattern)
        pattern = re.sub(r"(?<!\\)\((?!\?)", "(?:", pattern)
        groups = group_count(pattern)
    if groups > MAX_GROUPS:
        raise SystemExit(f"{rule['id']}: {groups} capture groups, Cribl exposes {MAX_GROUPS}")
    if rule.get("secretGroup"):
        secret = f"g{rule['secretGroup']}"
    elif groups:
        secret = "(" + "||".join(f"g{i}" for i in range(1, groups + 1)) + "||g0)"
    else:
        secret = "g0"

    try:
        with warnings.catch_warnings():  # RE2 class syntax Python warns about
            warnings.simplefilter("ignore", FutureWarning)
            own = re.compile(rule["regex"].replace("(?-i:", "(?:").replace(r"\z", r"\Z"))
    except re.error:
        own = re.compile(r"(?!)")
    keep = []  # conditions under which gitleaks would not report the match
    if rule.get("entropy"):
        keep.append(f"C.Text.entropy({secret}) < {rule['entropy']}")
    for allow in rule.get("allowlists", []):
        if allow.get("paths") or allow.get("regexTarget") == "line":
            continue  # needs the file path or whole line, which a log event lacks
        target = "g0" if allow.get("regexTarget") == "match" else secret
        for r in allow.get("regexes", []):
            if own.search(r):
                # A literal example key (gcp-api-key lists Firebase SDK keys): skip it,
                # so the repo holds no key-shaped strings for secret scanning to flag.
                continue
            p, f = to_js(r)
            keep.append(f"/{p}/{f.replace('g', '')}.test({target})")
        if allow.get("stopwords"):
            keep.append(f"{regex_literal(allow['stopwords'])}.test({secret})")

    marker = js_str(f"[REDACTED {rule['id']}]")
    replace = f"{secret} ? g0.replace({secret}, {marker}) : {marker}"
    expr = f"({' || '.join(keep)}) ? g0 : {replace}" if keep else replace
    keywords = rule.get("keywords") or []
    return {
        "id": "mask",
        "filter": f"{regex_literal(keywords)}.test(_raw)" if keywords else "true",
        "conf": {"fields": ["_raw"], "depth": 5,
                 "rules": [{"matchRegex": f"/{pattern}/{flags}", "replaceExpr": expr}]},
        "description": f"gitleaks {rule['id']}: {rule['description']}",
    }


def main():
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1]).read_bytes()
    else:
        # URL is the fixed https constant above, never user input
        with urllib.request.urlopen(URL, timeout=30) as resp:  # nosemgrep: dynamic-urllib-use-detected
            raw = resp.read()
    config = tomllib.loads(raw.decode())
    rules = [r for r in config["rules"] if r.get("regex")]
    pipeline = {"functions": [rule_to_function(r) for r in rules],
                "asyncFuncTimeout": 1000, "output": "default"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# GENERATED by scripts/gen-secret-mask.py from gitleaks v{GITLEAKS_VERSION} "
              f"({len(rules)} rules). Do not edit; rerun the script.\n"
              "# gitleaks rules: MIT License, Copyright (c) 2019 Zachary Rice.\n"
              "# gitleaks:allow (rule patterns, not secrets)\n")
    OUT.write_text(header + json.dumps(pipeline, indent=1) + "\n")
    print(f"wrote {OUT} ({len(rules)} rules)")


if __name__ == "__main__":
    main()
