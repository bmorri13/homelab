"""Fixed settings. Secrets are never here: the runner's ServiceAccount token comes from
a systemd credential, everything else from Vault at run time."""
import os

K8S_API = "https://k3s-01:6443"
K8S_CA = "/etc/homelab-agents/k3s-ca.crt"
CLIENT_NAMESPACE = "toolhive-clients"
RUNNER_SA = "agent-runner"

GATEWAY_URL = "https://mcp.bmosan.com/mcp"
GATEWAY_AUDIENCE = "https://mcp.bmosan.com"
GATEWAY_TOKEN_SECONDS = 3600

VAULT_ADDR = "https://vault.bmosan.com"
VAULT_ROLE = "homelab-agents"
# kv/slack-alerts: webhook_url. kv/cribl-hec: token. kv/homelab-agents: claude_oauth_token.
VAULT_SLACK = "slack-alerts"
VAULT_HEC = "cribl-hec"
VAULT_AGENTS = "homelab-agents"

HEC_URL = "http://192.168.4.11:8088/services/collector/event"
HEC_INDEX = "ai_agents"
HEC_SOURCETYPE = "homelab:agent"

STATE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/homelab-agents")
APP_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TURNS = 40            # per item
MAX_ITEMS_PER_RUN = 10    # the rest are posted untriaged, as a list
WARN_TOKEN_DAYS = 14      # warn in Slack when the runner token expires sooner than this

AGENTS = {
    "sec": {"service_account": "agent-sec", "title": "Security agent", "renotify_hours": 24},
    "ops": {"service_account": "agent-ops", "title": "Ops agent", "renotify_hours": 12},
}
