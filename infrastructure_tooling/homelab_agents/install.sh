#!/usr/bin/env bash
# Install or upgrade the homelab agents on this host. Run as root from the copy that
# deploy.sh places in /root/homelab-agents. Idempotent.
set -euo pipefail
cd "$(dirname "$0")"

id homelab-agents >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/homelab-agents --shell /usr/bin/nologin homelab-agents
install -d -m 755 /opt/homelab-agents
install -d -m 700 /etc/homelab-agents

rm -rf /opt/homelab-agents/app.new
cp -r app /opt/homelab-agents/app.new
chmod -R go-w /opt/homelab-agents/app.new
rm -rf /opt/homelab-agents/app
mv /opt/homelab-agents/app.new /opt/homelab-agents/app

# /tmp is a small tmpfs on coding-box; the SDK wheel bundles a ~200 MB CLI.
[[ -x /opt/homelab-agents/venv/bin/python ]] || python3 -m venv /opt/homelab-agents/venv
TMPDIR=/var/tmp /opt/homelab-agents/venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

install -m 644 systemd/homelab-agent@.service systemd/homelab-agent-sec.timer systemd/homelab-agent-ops.timer /etc/systemd/system/
systemctl daemon-reload

if [[ -s /etc/homelab-agents/runner.token && -s /etc/homelab-agents/k3s-ca.crt ]]; then
  systemctl enable --now homelab-agent-sec.timer homelab-agent-ops.timer
  systemctl list-timers 'homelab-agent-*' --no-pager
else
  echo "runner token or k3s CA missing in /etc/homelab-agents: run deploy.sh --mint-token; timers not enabled" >&2
fi
