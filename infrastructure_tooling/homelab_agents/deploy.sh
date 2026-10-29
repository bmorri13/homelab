#!/usr/bin/env bash
# Deploy the homelab agents to coding-box from a workstation with root SSH to coding-box
# and k3s-01.
#
#   ./deploy.sh               copy the code, install or upgrade, (re)enable the timers
#   ./deploy.sh --mint-token  also mint a new 90-day token for the agent-runner
#                             ServiceAccount (first install, or rotation; the runner
#                             warns in Slack 14 days before it expires)
#
# The token goes from k3s-01 to coding-box over SSH pipes; it is never printed or
# written on this machine.
set -euo pipefail
HOST="${AGENTS_SSH_HOST:-root@coding-box}"
K3S="${K3S_SSH_HOST:-root@k3s-01}"
cd "$(dirname "$0")"

COPYFILE_DISABLE=1 tar --no-xattrs --exclude __pycache__ -czf - app systemd install.sh requirements.txt |
  ssh "$HOST" 'rm -rf /root/homelab-agents && mkdir -p /root/homelab-agents && tar -xzf - -C /root/homelab-agents'

ssh "$K3S" cat /var/lib/rancher/k3s/server/tls/server-ca.crt |
  ssh "$HOST" 'install -d -m 755 /etc/homelab-agents && cat > /etc/homelab-agents/k3s-ca.crt'

if [[ "${1:-}" == "--mint-token" ]]; then
  ssh "$K3S" kubectl -n toolhive-clients create token agent-runner --duration 2160h |
    ssh "$HOST" 'umask 077; cat > /etc/homelab-agents/runner.token.new && [ -s /etc/homelab-agents/runner.token.new ] && mv /etc/homelab-agents/runner.token.new /etc/homelab-agents/runner.token'
fi

ssh "$HOST" /root/homelab-agents/install.sh
