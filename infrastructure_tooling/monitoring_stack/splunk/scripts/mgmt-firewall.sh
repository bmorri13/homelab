#!/usr/bin/env bash
# Allow only the MCP gateway on k3s-01 to reach Splunk's management port (8089), and
# only the UniFi gateways to send syslog (Cribl, 5514). Syslog has no authentication,
# so the source address is the only check.
# Docker publishes the port with its own DNAT rules, which bypass the INPUT chain,
# so the filter goes in DOCKER-USER, which Docker evaluates first and never flushes.
# --ctorigdstport matches the port the client connected to, before Docker's DNAT.
# Containers on this host's Docker networks are still allowed, so a compose service
# can reach splunk:8089 directly.
# Idempotent. Run by splunk-mgmt-firewall.service before Docker starts, and by
# deploy.sh.
set -euo pipefail

# The defaults apply at boot, when the unit runs without .env. Comma-separated CIDRs.
MGMT_ALLOWED="${SPLUNK_MGMT_ALLOWED:-192.168.4.98/32}"                 # k3s-01
SYSLOG_NETWORK_ALLOWED="${SYSLOG_NETWORK_ALLOWED:-192.168.4.1/32,192.168.1.85/32}"  # UCG-Max, UX
SYSLOG_INFRA_ALLOWED="${SYSLOG_INFRA_ALLOWED:-}"                       # 514: nothing sends today

iptables -w -N DOCKER-USER 2>/dev/null || true

# allow_only <chain> <cidrs> <port> <proto...>: traffic to <port> from anything but
# <cidrs> and this host's Docker networks is dropped.
allow_only() {
    local chain=$1 allowed=$2 port=$3; shift 3
    iptables -w -N "$chain" 2>/dev/null || true
    iptables -w -F "$chain"
    for src in ${allowed//,/ }; do
        iptables -w -A "$chain" -s "$src" -j RETURN
    done
    iptables -w -A "$chain" -i docker0 -j RETURN
    iptables -w -A "$chain" -i br-+ -j RETURN
    iptables -w -A "$chain" -j DROP
    for proto in "$@"; do
        local jump=(-p "$proto" -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL -j "$chain")
        iptables -w -C DOCKER-USER "${jump[@]}" 2>/dev/null || iptables -w -I DOCKER-USER 1 "${jump[@]}"
    done
}

allow_only SPLUNK-MGMT "$MGMT_ALLOWED" 8089 tcp
allow_only SYSLOG-NETWORK "$SYSLOG_NETWORK_ALLOWED" 5514 udp tcp
allow_only SYSLOG-INFRA "$SYSLOG_INFRA_ALLOWED" 514 udp tcp

# Docker also publishes the syslog ports on [::]. IPv6 connections go through Docker's
# userland proxy (a host process), so INPUT applies, not DOCKER-USER. The only IPv6
# address here is Tailscale's, and no gateway sends over IPv6: drop them.
for port in 514 5514; do
    for proto in udp tcp; do
        rule=(-p "$proto" --dport "$port" -j DROP)
        ip6tables -w -C INPUT "${rule[@]}" 2>/dev/null || ip6tables -w -I INPUT 1 "${rule[@]}"
    done
done
