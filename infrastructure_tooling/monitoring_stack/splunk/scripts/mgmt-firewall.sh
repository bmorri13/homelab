#!/usr/bin/env bash
# Allow only k3s-01 to reach Splunk's management port (8089, the MCP gateway) and the
# Cribl Leader (9000: the MCP gateway, and the UI at cribl.bmosan.com behind Authentik),
# only the UniFi gateways to send syslog (Cribl, 5514), and only the server LAN to reach
# Cribl's HEC input (8443). Syslog has no authentication, so the source
# address is the only check; HEC also needs its token.
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
HEC_ALLOWED="${HEC_ALLOWED:-192.168.4.0/24}"                           # VMs, Proxmox nodes, k3s-01
CRIBL_UI_ALLOWED="${CRIBL_UI_ALLOWED:-192.168.4.98/32}"                # k3s-01

iptables -w -N DOCKER-USER 2>/dev/null || true

# allow_only <chain> <cidrs> <ports> <proto...>: traffic to <ports> (comma-separated)
# from anything but <cidrs> and this host's Docker networks is dropped.
allow_only() {
    local chain=$1 allowed=$2 ports=$3 port proto; shift 3
    iptables -w -N "$chain" 2>/dev/null || true
    iptables -w -F "$chain"
    for src in ${allowed//,/ }; do
        iptables -w -A "$chain" -s "$src" -j RETURN
    done
    iptables -w -A "$chain" -i docker0 -j RETURN
    iptables -w -A "$chain" -i br-+ -j RETURN
    iptables -w -A "$chain" -j DROP
    for port in ${ports//,/ }; do
        for proto in "$@"; do
            local jump=(-p "$proto" -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL -j "$chain")
            iptables -w -C DOCKER-USER "${jump[@]}" 2>/dev/null || iptables -w -I DOCKER-USER 1 "${jump[@]}"
        done
    done
}

allow_only SPLUNK-MGMT "$MGMT_ALLOWED" 8089 tcp
allow_only SYSLOG-NETWORK "$SYSLOG_NETWORK_ALLOWED" 5514 udp tcp
allow_only SYSLOG-INFRA "$SYSLOG_INFRA_ALLOWED" 514 udp tcp
allow_only CRIBL-HEC "$HEC_ALLOWED" 8443 tcp
allow_only CRIBL-UI "$CRIBL_UI_ALLOWED" 9000 tcp
# Plain-HTTP HEC (8088) was retired on 2026-11-03; drop its old rules.
iptables -w -D DOCKER-USER -p tcp -m conntrack --ctorigdstport 8088 --ctdir ORIGINAL -j CRIBL-HEC 2>/dev/null || true
for proto in udp tcp; do ip6tables -w -D INPUT -p "$proto" --dport 8088 -j DROP 2>/dev/null || true; done

# Docker also publishes these ports on [::]. IPv6 connections go through
# Docker's userland proxy (a host process), so INPUT applies, not DOCKER-USER. The
# only IPv6 address here is Tailscale's, and nothing sends over IPv6: drop them.
for port in 514 5514 8443 9000; do
    for proto in udp tcp; do
        rule=(-p "$proto" --dport "$port" -j DROP)
        ip6tables -w -C INPUT "${rule[@]}" 2>/dev/null || ip6tables -w -I INPUT 1 "${rule[@]}"
    done
done
