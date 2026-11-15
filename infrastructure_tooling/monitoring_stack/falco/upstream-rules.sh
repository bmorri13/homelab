#!/usr/bin/env bash
# Vendor Falco's upstream incubating and sandbox rules files into rules.d, pinned to a
# release of github.com/falcosecurity/rules and checked against the SHA-256 below.
# Hosts and k3s-01 load the committed copies, so nothing is fetched when Falco starts.
# Only the rules selected in config.d/homelab-rules.yaml are enabled; the rest of
# each file is loaded disabled.
#
# Run from anywhere, no root needed. It downloads both files over the committed ones
# and verifies them; CI runs it and fails if the result differs from the commit.
# On a version bump (Renovate, Dependency Dashboard approval): run it, review the
# upstream changes with `git diff rules.d`, run validate-rules.sh, then set the new
# SHA-256 it printed. The raw files have no published checksum; the tag is the source.
set -euo pipefail

INCUBATING_VERSION=6.0.1
INCUBATING_SHA256=47bf87041e2f39ca336e7acf1ae4e6183771a92e81031f367d01f2b49cabe1ac
SANDBOX_VERSION=6.2.0
SANDBOX_SHA256=f05ad34f07c43930932ea6dd84c08a37bc8e5ba9f88901ca091ff1b52b3f33d3

cd "$(dirname "$0")"
failed=0
fetch() {  # <tag> <upstream file> <local file> <sha256>
    curl -fsSL -o "rules.d/$3" "https://raw.githubusercontent.com/falcosecurity/rules/$1/rules/$2"
    actual=$(sha256sum "rules.d/$3" | cut -d' ' -f1)
    if [[ "$actual" == "$4" ]]; then
        echo "rules.d/$3: $1 ok"
    else
        echo "rules.d/$3: $1 has SHA-256 $actual, expected $4" >&2
        failed=1
    fi
}
fetch "falco-incubating-rules-$INCUBATING_VERSION" falco-incubating_rules.yaml 10-falco-incubating-rules.yaml "$INCUBATING_SHA256"
fetch "falco-sandbox-rules-$SANDBOX_VERSION" falco-sandbox_rules.yaml 11-falco-sandbox-rules.yaml "$SANDBOX_SHA256"
exit "$failed"
