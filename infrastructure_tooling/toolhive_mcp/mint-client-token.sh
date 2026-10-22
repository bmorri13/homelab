#!/usr/bin/env bash
# Mint a bearer token for one MCP client machine.
#
#   ./mint-client-token.sh <client> [duration]     e.g. macbook-air 2160h
#
# <client> must be listed under auth.clients in values.yaml (that list is both
# the ServiceAccounts that exist and the Cedar allowlist). The token is a
# Kubernetes ServiceAccount JWT with audience https://mcp.bmosan.com; the API
# server itself rejects that audience, so it grants no cluster access.
#
# The token is written to stdout only. Store it in the client machine's secret
# store / environment (TOOLHIVE_MCP_TOKEN) -- never in Git or a synced dotfile.
set -euo pipefail

client="${1:?usage: $0 <client> [duration]}"
duration="${2:-2160h}" # 90 days
host="${K3S_SSH_HOST:-root@k3s-01}"

ssh "$host" kubectl -n toolhive-clients create token "$client" \
  --audience https://mcp.bmosan.com \
  --duration "$duration"
