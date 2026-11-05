#!/usr/bin/env bash
# Start the image and speak MCP to it: initialize, then list tools. Fails unless
# the Argo CD read tools are there and the write tools are not (MCP_READ_ONLY).
# No Argo CD is needed: listing tools makes no API call.
set -euo pipefail
image=$1
name=mcp-for-argocd-smoke
docker run -d --name "$name" -p 127.0.0.1:3000:3000 --read-only --tmpfs /tmp \
  -e ARGOCD_BASE_URL=https://argocd.invalid -e ARGOCD_API_TOKEN=dummy -e MCP_READ_ONLY=true \
  "$image" http --bind-address 0.0.0.0 --allow-unauthenticated >/dev/null
trap 'docker logs "$name" | tail -20; docker rm -f "$name" >/dev/null' EXIT

post() {  # post <json> [session-id]
  curl -sS -D /tmp/h -X POST http://127.0.0.1:3000/mcp \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    ${2:+-H "Mcp-Session-Id: $2"} -d "$1"
}
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:3000/ && break; sleep 1; done

init=$(post '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}')
echo "$init" | grep -q '"serverInfo"' || { echo "no serverInfo in: $init"; exit 1; }
sid=$(grep -i '^mcp-session-id:' /tmp/h | tr -d '\r' | awk '{print $2}')
post '{"jsonrpc":"2.0","method":"notifications/initialized"}' "$sid" >/dev/null
tools=$(post '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' "$sid")
for t in list_applications get_application get_application_workload_logs; do
  echo "$tools" | grep -q "\"$t\"" || { echo "missing tool $t"; exit 1; }
done
for t in create_application delete_application sync_application; do
  echo "$tools" | grep -q "\"$t\"" && { echo "write tool $t present despite MCP_READ_ONLY"; exit 1; }
done
echo "smoke test passed: $(echo "$tools" | grep -o '"name":"[a-z_]*"' | wc -l) tools, read-only"
