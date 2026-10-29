"""Fixed settings. Secrets are never here: the agent's ServiceAccount tokens are files
the kubelet projects into the pod, everything else comes from Vault at run time.
Addresses can be overridden by environment variables (the CronJobs set them)."""
import os

# Projected ServiceAccount tokens (CronJob volumes). The kubelet issues and rotates
# them; they are bound to the pod and expire on their own.
GATEWAY_TOKEN_FILE = os.environ.get("GATEWAY_TOKEN_FILE", "/var/run/secrets/homelab-agents/gateway/token")
VAULT_TOKEN_FILE = os.environ.get("VAULT_TOKEN_FILE", "/var/run/secrets/homelab-agents/vault/token")

# In-cluster addresses: the public names are tailnet-only (Traefik ipAllowList), so a
# pod uses the Services directly.
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://vmcp-homelab.toolhive-mcp.svc.cluster.local:4483/mcp")
VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://vault.vault.svc.cluster.local:8200")
VAULT_ROLE = "homelab-agents"
# kv/slack-alerts: webhook_url. kv/cribl-hec: token. kv/homelab-agents: claude_oauth_token.
VAULT_SLACK = "slack-alerts"
VAULT_HEC = "cribl-hec"
VAULT_AGENTS = "homelab-agents"

HEC_URL = os.environ.get("HEC_URL", "http://192.168.4.11:8088/services/collector/event")
HEC_INDEX = "ai_agents"
HEC_SOURCETYPE = "homelab:agent"
# For links in Slack messages: the full MagicDNS name resolves on any tailnet device (phones too).
SPLUNK_WEB = "https://security-tooling-01.tail25c6fe.ts.net:8000"

STATE_DIR = os.environ.get("STATE_DIRECTORY", "/state")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# The agent's working directory: holds only .claude/skills (the Splunk agent skills,
# selected at image build). Read is limited to this directory.
WORKSPACE = os.environ.get("AGENT_WORKSPACE", os.path.join(APP_DIR, "workspace"))

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TURNS = 40            # per item
MAX_ITEMS_PER_RUN = 10    # the rest are posted untriaged, as a list

# Splunk documentation the skills cite; the only sites WebFetch may reach. All
# Splunk-owned, so a prompt injected through log data can't send anything to a host
# an attacker reads.
DOC_DOMAINS = ["help.splunk.com", "docs.splunk.com", "dev.splunk.com", "lantern.splunk.com",
               "advisory.splunk.com", "www.splunk.com"]

AGENTS = {
    "sec": {"title": "Security agent", "renotify_hours": 24,
            "skills": ["search-and-dashboard-troubleshooter", "field-extraction-and-cim-mapping",
                       "alerting-and-notable-workflows"]},
    "ops": {"title": "Ops agent", "renotify_hours": 12,
            "skills": ["dashboard-report-alert-performance-advisor", "search-and-dashboard-troubleshooter",
                       "search-performance-optimizer", "forwarder-and-data-ingest-doctor",
                       "hec-setup-and-troubleshooting", "splunk-health-monitoring-and-diagnostic-collection",
                       "index-and-storage-management-advisor"]},
}
