# Homelab agents

Two triage agents on coding-box, each run every 30 minutes by systemd:

| Agent | Timer | Looks at | Gateway identity |
|---|---|---|---|
| Security (`sec`) | :00 and :30 | HL - detection findings (`index=security sourcetype=homelab:detection`) and the Falco, honeypot and secret-leak alerts that fired | `agent-sec`, 15 read-only tools |
| Operations (`ops`) | :15 and :45 | Capacity (filesystems, Proxmox storage, CPU, memory), Kubernetes workloads, pods and restarts, Docker restarts, Splunk scheduler skips/failures, Argo CD app health and sync | `agent-ops`, 23 read-only tools |

Results go to Slack as one message per run, and every run and item is logged to Splunk `index=ai_agents sourcetype=homelab:agent`.

## How a run works

`app/runner.py` is deterministic; the model only triages.

1. **Credentials.** The runner holds one secret: a 90-day token for the `agent-runner` ServiceAccount (systemd credential from `/etc/homelab-agents/runner.token`). That ServiceAccount can do exactly one thing: create tokens for `agent-sec`, `agent-ops` and itself (`toolhive_mcp/templates/security/agent-runner.yaml`).
   - It mints itself a 10-minute `audience=vault` token, logs in to Vault (role `homelab-agents`), reads the Slack webhook, the Cribl HEC token and the Claude subscription token, and revokes the Vault token.
   - It mints the agent a fresh **1-hour** gateway token (`audience=https://mcp.bmosan.com`). The Splunk detection *HL - Kubernetes service account token minted* excludes exactly these mints.
2. **Queue.** `app/queues.py` runs fixed Splunk searches (and the Argo CD application list) through the gateway as the agent's own identity, over the last 2 hours. Each row becomes an item with a stable key: the detection plus host/user/source/object, or an alert's own suppression fields (alerts are read back with `| loadjob` from `_audit action=alert_fired`).
3. **Dedupe.** `app/state.py` (SQLite in `/var/lib/homelab-agents/state.db`) skips items already posted inside the renotify window (24 h security, 12 h ops). An item triaged but not posted (Slack down or unset) is posted on the next run without re-triaging.
4. **Triage.** For each new item, up to 10 per run, the Claude Agent SDK runs `claude-opus-5-5` (effort medium, 40 turns max) with:
   - no built-in tools (`tools=[]`: no shell, files or web), only the gateway as MCP server `homelab` (`allowed_tools=mcp__homelab__*`, `permission_mode=dontAsk`, `strict_mcp_config`), and no local Claude settings, hooks or plugins (`setting_sources=[]`);
   - the agent's system prompt (`app/prompts/<agent>.md`) and the item's runbook (`app/runbooks/`), with the item's data marked as untrusted;
   - a JSON schema for the result: verdict, confidence, severity, summary, evidence, next steps.
   What the agent can call is enforced by the gateway's Cedar allowlist and the Splunk `mcp_agent` role, not by the prompt.
5. **Report.** Every new item is posted, whatever the verdict, as Slack Block Kit cards:
   - one **alert card** per item the agent doesn't call routine (or couldn't triage), most severe first: header with severity, title and the agent's one-line headline, a field grid (severity, verdict and confidence, host, and up to three facts such as source, user or object), *Recommended Action*, and **View Dashboard** / **View Analysis** links (plain links, not Block Kit buttons, which need an interactive Slack app; the analysis link opens that item's full triage in Splunk);
   - one **digest card** per run for everything routine (one line each, repeats collapsed) and anything over the per-run limit.
   Severity uses one scheme everywhere: red critical, orange high, yellow warning (medium), blue info (low). Text from logs or the model is escaped, so it can't add mentions or links. If a run fails, a card says so.

## Install and update

From a workstation with root SSH to coding-box and k3s-01:

```bash
./deploy.sh --mint-token   # first install, or token rotation (the runner warns in Slack 14 days before expiry)
./deploy.sh                # code changes
```

`deploy.sh` copies `app/`, the units and `install.sh` to coding-box, installs the k3s CA, optionally pipes a new runner token from k3s-01 straight to coding-box, and runs `install.sh` (system user `homelab-agents`, venv in `/opt/homelab-agents/venv` with the pinned `claude-agent-sdk`, units, timers). The SDK bundles its own Claude Code binary; nothing is installed into anyone's Claude Code.

### Secrets in Vault (once)

| Path | Key | What |
|---|---|---|
| `kv/homelab-agents` | `claude_oauth_token` | Subscription token from `claude setup-token` (valid one year) |
| `kv/slack-alerts` | `webhook_url` | Slack incoming webhook |
| `kv/cribl-hec` | `token` | Already there (OTel collector on k3s) |

Store them without the value touching shell history or a terminal echo, for example:

```bash
claude setup-token   # on any machine with Claude Code; copy the whole token (108 chars; it may wrap)
ssh -t root@k3s-01 'read -rsp "Claude token: " T; echo
  case "$T" in sk-ant-oat01-*) ;; *) echo "wrong prefix, not saved"; exit 1;; esac
  [ ${#T} -ge 100 ] || { echo "only ${#T} chars, looks truncated, not saved"; exit 1; }
  { kubectl -n default get secret vault-token -o jsonpath={.data.token} | base64 -d; echo; printf %s "$T"; } |
  kubectl -n vault exec -i vault-0 -- sh -c "read -r VAULT_TOKEN; export VAULT_TOKEN; vault kv put kv/homelab-agents claude_oauth_token=-"'

ssh -t root@k3s-01 'read -rsp "Slack webhook URL: " T; echo
  { kubectl -n default get secret vault-token -o jsonpath={.data.token} | base64 -d; echo; printf %s "$T"; } |
  kubectl -n vault exec -i vault-0 -- sh -c "read -r VAULT_TOKEN; export VAULT_TOKEN; vault kv put kv/slack-alerts webhook_url=-"'
```

(The Vault UI works too: `http://k3s-01:31080/ui`, KV v2 engine `kv`.) A new value is picked up on the next run.

The Vault side (runtime, not GitOps; already applied):

```bash
vault policy write homelab-agents-read - <<'EOF'
path "kv/data/slack-alerts"   { capabilities = ["read"] }
path "kv/data/cribl-hec"      { capabilities = ["read"] }
path "kv/data/homelab-agents" { capabilities = ["read"] }
EOF
vault write auth/kubernetes/role/homelab-agents \
  bound_service_account_names=agent-runner bound_service_account_namespaces=toolhive-clients \
  audience=vault token_policies=homelab-agents-read token_ttl=10m token_max_ttl=15m
```

Vault is reached at `http://k3s-01.tail25c6fe.ts.net:31080` (the NodePort; the MagicDNS name keeps the traffic inside Tailscale).

## Operating it

```bash
systemctl list-timers 'homelab-agent-*'
journalctl -u homelab-agent@sec -n 50
systemctl start homelab-agent@ops          # run now

# Look without triaging or posting (no model, no state changes):
systemd-run --wait --pipe -q -p User=homelab-agents -p StateDirectory=homelab-agents \
  -p LoadCredential=runner.token:/etc/homelab-agents/runner.token -p Environment=HOME=/var/lib/homelab-agents \
  /opt/homelab-agents/venv/bin/python /opt/homelab-agents/app/runner.py sec --collect-only
```

`--dry-run` triages and prints the Slack message instead of posting. In Splunk: `index=ai_agents sourcetype=homelab:agent kind=run | table _time source status collected new triaged cost_usd error` (cost is the API-equivalent figure the SDK reports; on the subscription it counts against usage limits, not a bill).

To add an ops check, add a row to `OPS_CHECKS` in `app/queues.py`; to change what the security agent reads, edit `security()` there. Runbooks are plain Markdown, matched by detection or alert name.
