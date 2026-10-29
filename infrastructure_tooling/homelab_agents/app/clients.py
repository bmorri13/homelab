"""Clients for everything the runner talks to: the k3s API (token minting), Vault,
the MCP gateway, Slack and Cribl HEC. Standard library only.

Every URL here is a fixed endpoint from config.py, never data from Splunk or the model.
"""
import base64
import json
import ssl
import urllib.error
import urllib.request

import config


class ClientError(Exception):
    pass


def _http(method, url, body=None, headers=None, context=None, timeout=60, raw_body=None):
    if not url.startswith(("https://", "http://192.168.4.11:")):
        raise ClientError(f"refusing URL scheme/host: {url[:40]}")
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")


# ---- Kubernetes: mint short-lived ServiceAccount tokens ---------------------------------

def mint_token(runner_token, service_account, audience, seconds):
    """TokenRequest for a ServiceAccount in the client namespace. The runner's own
    ServiceAccount may only do this for the agents and itself (Role agent-runner-mint)."""
    ctx = ssl.create_default_context(cafile=config.K8S_CA)
    url = f"{config.K8S_API}/api/v1/namespaces/{config.CLIENT_NAMESPACE}/serviceaccounts/{service_account}/token"
    body = {"apiVersion": "authentication.k8s.io/v1", "kind": "TokenRequest",
            "spec": {"audiences": [audience], "expirationSeconds": seconds}}
    status, _, text = _http("POST", url, body, {"Authorization": f"Bearer {runner_token}"}, ctx)
    if status != 201:
        raise ClientError(f"token request for {service_account} failed: HTTP {status} {text[:200]}")
    return json.loads(text)["status"]["token"]


def jwt_expiry(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["exp"]


# ---- Vault --------------------------------------------------------------------------------

class Vault:
    """Kubernetes-auth login as the runner (audience "vault"), read-only KV access."""

    def __init__(self, runner_token):
        jwt = mint_token(runner_token, config.RUNNER_SA, "vault", 600)
        status, _, text = _http("POST", f"{config.VAULT_ADDR}/v1/auth/kubernetes/login",
                                {"role": config.VAULT_ROLE, "jwt": jwt})
        if status != 200:
            raise ClientError(f"vault login failed: HTTP {status} {text[:200]}")
        self.token = json.loads(text)["auth"]["client_token"]

    def kv(self, path):
        """Data of a KV v2 entry under kv/, or {} if it doesn't exist yet."""
        status, _, text = _http("GET", f"{config.VAULT_ADDR}/v1/kv/data/{path}",
                                headers={"X-Vault-Token": self.token})
        if status == 404:
            return {}
        if status != 200:
            raise ClientError(f"vault read kv/{path} failed: HTTP {status}")
        return json.loads(text)["data"]["data"]

    def close(self):
        _http("POST", f"{config.VAULT_ADDR}/v1/auth/token/revoke-self", headers={"X-Vault-Token": self.token})


# ---- MCP gateway (used by the runner itself for the deterministic queue queries) --------

class Gateway:
    def __init__(self, token):
        self.token = token
        self.session = None
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "homelab-agents-runner", "version": "1"}})
        self._rpc("notifications/initialized", notify=True)

    def _rpc(self, method, params=None, notify=False):
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self._id
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        status, resp_headers, text = _http("POST", config.GATEWAY_URL, body, headers, timeout=180)
        self.session = resp_headers.get("Mcp-Session-Id") or resp_headers.get("mcp-session-id") or self.session
        if status >= 400:
            raise ClientError(f"gateway {method}: HTTP {status} {text[:200]}")
        if notify or not text.strip():
            return None
        if not text.lstrip().startswith("{"):
            text = next((l[5:] for l in text.splitlines() if l.startswith("data:")), "{}")
        msg = json.loads(text)
        if "error" in msg:
            raise ClientError(f"gateway {method}: {msg['error']}")
        return msg["result"]

    def call(self, tool, args):
        result = self._rpc("tools/call", {"name": tool, "arguments": args})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            raise ClientError(f"{tool}: {text[:300]}")
        return text

    def splunk(self, spl, earliest, latest="now", limit=500):
        text = self.call("splunk-agent_splunk_run_query",
                         {"query": spl, "earliest_time": earliest, "latest_time": latest, "row_limit": limit})
        return json.loads(text).get("results", [])


# ---- Slack and HEC --------------------------------------------------------------------------

def slack_escape(text):
    """Slack mrkdwn control characters. Escaping < and > also stops <!channel> mentions
    and disguised links in anything taken from events or written by the model."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_post(webhook, payload):
    if not webhook.startswith("https://hooks.slack.com/"):
        raise ClientError("slack webhook in Vault is not a hooks.slack.com URL")
    status, _, text = _http("POST", webhook, payload, timeout=30)
    if status != 200:
        raise ClientError(f"slack post failed: HTTP {status} {text[:100]}")


def hec_send(token, events):
    if not token or not events:
        return
    raw = "\n".join(json.dumps(e) for e in events).encode()
    status, _, text = _http("POST", config.HEC_URL, raw_body=raw,
                            headers={"Authorization": f"Splunk {token}"}, timeout=30)
    if status != 200:
        raise ClientError(f"HEC send failed: HTTP {status} {text[:200]}")
