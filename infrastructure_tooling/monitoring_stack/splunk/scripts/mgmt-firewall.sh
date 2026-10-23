#!/usr/bin/env bash
# Allow only the MCP gateway on k3s-01 to reach Splunk's management port (8089).
# Docker publishes the port with its own DNAT rules, which bypass the INPUT chain,
# so the filter goes in DOCKER-USER, which Docker evaluates first and never flushes.
# --ctorigdstport matches the port the client connected to, before Docker's DNAT.
# Containers on this host's Docker networks are still allowed, so a compose service
# can reach splunk:8089 directly.
# Idempotent. Run by splunk-mgmt-firewall.service before Docker starts, and by
# deploy.sh.
set -euo pipefail

ALLOWED="${SPLUNK_MGMT_ALLOWED:-192.168.4.98/32}"   # k3s-01
CHAIN=SPLUNK-MGMT

iptables -w -N DOCKER-USER 2>/dev/null || true
iptables -w -N "$CHAIN" 2>/dev/null || true
iptables -w -F "$CHAIN"
for src in ${ALLOWED//,/ }; do
    iptables -w -A "$CHAIN" -s "$src" -j RETURN
done
iptables -w -A "$CHAIN" -i docker0 -j RETURN
iptables -w -A "$CHAIN" -i br-+ -j RETURN
iptables -w -A "$CHAIN" -j DROP

jump=(-p tcp -m conntrack --ctorigdstport 8089 --ctdir ORIGINAL -j "$CHAIN")
iptables -w -C DOCKER-USER "${jump[@]}" 2>/dev/null || iptables -w -I DOCKER-USER 1 "${jump[@]}"
