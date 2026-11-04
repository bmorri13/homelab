#!/usr/bin/env bash
# Install the host part of the detection validation on this machine (ubuntu-desktop-01
# only: the tests are meant for one low-stakes host). Idempotent. Run as root from
# this directory.
set -euo pipefail
cd "$(dirname "$0")"
[[ "$(hostname)" == ubuntu-desktop-01 ]] || { echo "only for ubuntu-desktop-01" >&2; exit 1; }
install -d -m 755 /usr/local/lib/art-validation
install -m 755 art-validation /usr/local/lib/art-validation/art-validation
install -m 644 art-validation.service art-validation.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now art-validation.timer
systemctl list-timers art-validation.timer --no-pager
