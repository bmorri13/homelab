#!/usr/bin/env bash
# Allow only k3s-01 to reach Splunk's management port (8089, the MCP gateway), Splunk
# Web (8000: Traefik serving splunk.bmosan.com, which logs in through Authentik) and the
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

# The allowlists can be overridden in .env. The unit runs this at boot without
# deploy.sh's environment, so read them from .env here too: only these variables,
# sourced the way deploy.sh does it, in a subshell so a missing or broken .env leaves
# the defaults below (never no rules). A variable already in the environment wins.
allow_vars=(SPLUNK_MGMT_ALLOWED SYSLOG_NETWORK_ALLOWED HEC_ALLOWED CRIBL_UI_ALLOWED SPLUNK_WEB_ALLOWED)
env_file="$(dirname "$0")/../.env"
if [[ -r "$env_file" ]]; then
    # shellcheck disable=SC1090,SC2016  # .env is the host's; the printf builds code for eval
    eval "$( (set +eu; . "$env_file" >/dev/null 2>&1
        for v in "${allow_vars[@]}"; do [[ -n "${!v+x}" ]] && printf '[[ -n "${%s+x}" ]] || %s=%q\n' "$v" "$v" "${!v}"; done) )" || true
fi

# Comma-separated CIDRs.
MGMT_ALLOWED="${SPLUNK_MGMT_ALLOWED:-192.168.4.98/32}"                 # k3s-01
SYSLOG_NETWORK_ALLOWED="${SYSLOG_NETWORK_ALLOWED:-192.168.4.1/32,192.168.1.85/32}"  # UCG-Max, UX
HEC_ALLOWED="${HEC_ALLOWED:-192.168.4.0/24}"                           # VMs, Proxmox nodes, k3s-01
CRIBL_UI_ALLOWED="${CRIBL_UI_ALLOWED:-192.168.4.98/32}"                # k3s-01
SPLUNK_WEB_ALLOWED="${SPLUNK_WEB_ALLOWED:-192.168.4.98/32}"            # k3s-01

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
allow_only CRIBL-HEC "$HEC_ALLOWED" 8443 tcp
allow_only CRIBL-UI "$CRIBL_UI_ALLOWED" 9000 tcp
allow_only SPLUNK-WEB "$SPLUNK_WEB_ALLOWED" 8000 tcp
# Syslog on 514 was never used and was removed on 2026-11-10; drop its old rules.
for proto in udp tcp; do
    iptables -w -D DOCKER-USER -p "$proto" -m conntrack --ctorigdstport 514 --ctdir ORIGINAL -j SYSLOG-INFRA 2>/dev/null || true
    iptables -w -t raw -D PREROUTING -i tailscale0 -p "$proto" --dport 514 -j DROP 2>/dev/null || true
    ip6tables -w -D INPUT -p "$proto" --dport 514 -j DROP 2>/dev/null || true
done
iptables -w -F SYSLOG-INFRA 2>/dev/null && iptables -w -X SYSLOG-INFRA 2>/dev/null || true

# Two paths skip DOCKER-USER, so these ports are dropped there as well:
# - Tailscale's ts-forward chain sits ahead of DOCKER-USER in FORWARD and accepts
#   everything arriving on tailscale0. Drop the ports on that interface in the raw
#   table, before Docker's DNAT; Tailscale doesn't touch it. Nothing uses them over the
#   tailnet. (8089 is published on the LAN address only, so it isn't reachable there.)
# - Docker also publishes them on [::]. IPv6 connections go through Docker's userland
#   proxy (a host process), so INPUT applies. The only IPv6 address here is
#   Tailscale's, and nothing sends over IPv6.
for port in 5514 8000 8443 9000; do
    for proto in udp tcp; do
        rule=(-i tailscale0 -p "$proto" --dport "$port" -j DROP)
        iptables -w -t raw -C PREROUTING "${rule[@]}" 2>/dev/null || iptables -w -t raw -I PREROUTING 1 "${rule[@]}"
        rule=(-p "$proto" --dport "$port" -j DROP)
        ip6tables -w -C INPUT "${rule[@]}" 2>/dev/null || ip6tables -w -I INPUT 1 "${rule[@]}"
    done
done
