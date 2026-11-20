#!/usr/bin/env bash
# Check the ToolHive docs MCP image pinned in values.yaml (mcpServers.toolhive-docs)
# before a version bump merges. Starts it with the settings the gateway gives it (not
# root, no refresh, a writable /tmp) and speaks MCP to it:
#   1. it starts and answers initialize
#   2. it still offers the two tools the gateway's policy admits (query_docs, get_chunk)
#   3. query_docs returns results, so the documentation database baked into the image works
# No secrets, and no ToolHive operator, proxy or gateway: this is the server alone.
# Run by .github/workflows/toolhive-docs-image.yml, or by hand on any machine with
# Docker; it removes its container on exit.
set -euo pipefail

cd "$(dirname "$0")"

image=$(awk '$1 == "image:" && $2 ~ /\/toolhive-doc-mcp:/ {print $2}' values.yaml | sort -u)
[[ -n "$image" && "$image" != *$'\n'* ]] || { echo "docs-mcp: expected one toolhive-doc-mcp image in values.yaml, found: ${image:-none}" >&2; exit 1; }
echo "docs-mcp: testing $image"

name="docs-mcp-ci-$$"; port=18080
ok=0
cleanup() {
    [[ $ok -eq 1 ]] || docker logs --tail 40 "$name" 2>&1 || true
    docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$name" --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:size=256m,uid=1000,gid=1000 -e REFRESH_ENABLED=false -e OTEL_ENABLED=false \
    -p "127.0.0.1:$port:8080" "$image" >/dev/null

python3 - "http://127.0.0.1:$port/mcp" <<'PY'
import json, sys, time, urllib.error, urllib.request

url, session = sys.argv[1], None


def call(method, params=None, notify=False):
    global session
    body = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {}),
            **({} if notify else {"id": 1})}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        **({"Mcp-Session-Id": session} if session else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        session = r.headers.get("Mcp-Session-Id") or session
        text = r.read().decode()
    if notify:
        return None
    # a JSON body, or one server-sent event carrying it
    data = next((l[5:].strip() for l in text.splitlines() if l.startswith("data:")), text)
    out = json.loads(data)
    if "error" in out:
        sys.exit(f"docs-mcp: {method} failed: {out['error']}")
    return out["result"]


for _ in range(60):
    try:
        info = call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "ci", "version": "1"}})
        break
    except (urllib.error.URLError, ConnectionError, OSError):
        time.sleep(2)
else:
    sys.exit("docs-mcp: the server did not answer initialize")
print(f"docs-mcp: {info['serverInfo'].get('name')} {info['serverInfo'].get('version')} answers")
call("notifications/initialized", notify=True)

tools = {t["name"]: t for t in call("tools/list")["tools"]}
missing = [t for t in ("query_docs", "get_chunk") if t not in tools]
if missing:
    sys.exit(f"docs-mcp: tools the gateway's policy admits are gone: {missing}. It now offers {sorted(tools)}")
extra = sorted(set(tools) - {"query_docs", "get_chunk"})
if extra:
    print(f"docs-mcp: note, new tools the gateway's policy does not admit: {extra}")

schema = tools["query_docs"].get("inputSchema") or {}
strings = [k for k in schema.get("required") or list(schema.get("properties") or {})
           if (schema.get("properties") or {}).get(k, {}).get("type") == "string"]
if not strings:
    sys.exit(f"docs-mcp: query_docs no longer takes a text argument: {schema}")
got = call("tools/call", {"name": "query_docs", "arguments": {strings[0]: "How do I run an MCP server in Kubernetes?"}})
text = " ".join(c.get("text", "") for c in got.get("content") or [])
if got.get("isError") or len(text) < 200:
    sys.exit(f"docs-mcp: query_docs returned no usable result: {text[:300]!r}")
print(f"docs-mcp: query_docs returned {len(text)} characters")
PY
ok=1
echo "docs-mcp: $image passed"
