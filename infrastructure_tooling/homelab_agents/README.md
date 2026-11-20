# Homelab agents

Two triage agents, run as Kubernetes CronJobs on k3s-01 every 30 minutes:

| Agent | CronJob (UTC) | Looks at | Gateway identity |
|---|---|---|---|
| Security (`sec`) | `homelab-agent-sec`, :00 and :30 | HL - detection findings (`index=security sourcetype=homelab:detection`) and the Falco, honeypot and secret-leak alerts that fired | `agent-sec`, 15 read-only tools |
| Operations (`ops`) | `homelab-agent-ops`, :15 and :45 | Capacity (filesystems, Proxmox storage, CPU, memory), Kubernetes workloads, pods and restarts, Docker restarts, Splunk scheduler skips/failures, Argo CD app health and sync | `agent-ops`, 23 read-only tools |

Results go to Slack as Block Kit cards, and every run, item and tool call is logged to Splunk `index=ai_agents sourcetype=homelab:agent`. Each run that triaged something ends with a **retro**: a second model call that reads the run's traces and suggests changes to the agent's prompt, runbooks, skills, tools and queue ([The improvement loop](#the-improvement-loop)).

![Homelab agents](../../docs/images/agents.drawio.svg)

## Layout

| Path | What |
|---|---|
| `image/` | The container: `app/` (runner, queues, state, tracing, retro, prompts, runbooks), `skills/` (our own skills: `retro`), `requirements.txt` (hash-locked, from `requirements.in` with the `uv pip compile` command in its header; Renovate bumps the pin in `requirements.in` and regenerates the lock itself), `select_skills.py`, `Dockerfile`. Layered build on Chainguard images, both pinned by digest: `python:latest-dev` builds the virtualenv (wheels only, hashes checked) and selects the skills; the runtime is `python:latest` (no shell, no package manager, no pip, nonroot UID 65532) with only the venv, the app and the skills, all owned by root |
| `Chart.yaml`, `values.yaml`, `templates/` | Helm chart: the two CronJobs, the state volume, the NetworkPolicy, the GHCR pull secret, the `homelab-agents-ca` ConfigMap (the homelab CA for HEC, `files/homelab-ca.crt`) |
| `application.yaml` | Argo CD Application (applied once by hand) |
| `RETRO_REVIEW.md`, `retro-findings.sh` | The weekly job that turns retro findings into draft pull requests, and the read-only script that lists them |
| `.github/workflows/homelab-agents-image.yml` | Builds the image on GitHub-hosted runners and pushes `ghcr.io/bmorri13/homelab-agents` (`:main` from main, `:<branch>` from branches, `:sha-<commit>` always) |

## How a run works

`image/app/runner.py` is deterministic; the model only triages.

```mermaid
sequenceDiagram
    autonumber
    participant K as CronJob pod<br/>(agent-sec / agent-ops)
    participant V as Vault
    participant G as MCP gateway<br/>(vmcp-homelab)
    participant S as Splunk / Argo CD
    participant D as SQLite state<br/>(PVC)
    participant C as Claude Agent SDK<br/>(Anthropic API)
    participant SL as Slack
    participant H as Splunk HEC<br/>(index=ai_agents)

    Note over K: kubelet projects two tokens:<br/>gateway (1 h), Vault (10 min)
    K->>V: log in (role homelab-agents)
    V-->>K: Claude token, Slack webhook, HEC token
    K->>V: revoke Vault token
    K->>G: fixed queue searches (agent's own identity)
    G->>S: Splunk searches, Argo CD app list
    S-->>K: rows → items with stable keys
    K->>D: drop items posted inside the renotify window
    loop each new item (up to 10)
        K->>C: prompt + runbook + item (untrusted)
        C->>G: read-only MCP tools
        C->>C: Skill, Read (skills only), WebFetch (Splunk docs only)
        C-->>K: JSON verdict, severity, evidence, next steps
        K->>D: record item
    end
    K->>SL: alert card per non-routine item (+ digest if any not triaged)
    opt the run triaged something
        K->>C: retro: prompt, runbooks and traces (no gateway, no web)
        C-->>K: findings: what to change in the agent's environment
        K->>D: count repeats, store new findings
    end
    K->>H: run, item, step and retro logs
```

1. **Credentials.** The pod runs as the agent's own ServiceAccount (`agent-sec` or `agent-ops`, in `toolhive-clients`, created by the `toolhive_mcp` chart) with no Kubernetes API token. The kubelet projects two tokens into it, bound to the pod and rotated automatically:
   - audience `https://mcp.bmosan.com`, 1 hour: the gateway token;
   - audience `vault`, 10 minutes: logs in to Vault (role `homelab-agents`), reads the Slack webhook, the Cribl HEC token and the Claude subscription token, and revokes the Vault token.

   Nothing long-lived is stored anywhere: no token file, and no Kubernetes Secret with the Claude, Slack or HEC values (they stay in Vault and in the run's memory). The only Kubernetes Secret is the GHCR pull credential.
2. **Queue.** `app/queues.py` runs fixed Splunk searches (and the Argo CD application list) through the gateway as the agent's own identity, over the last 2 hours. The first gateway call is retried for up to 20 s if the connection is refused: a new pod's IP joins the `toolhive-mcp` NetworkPolicy allow rules a moment after the pod starts, and until then the connection is rejected. Each row becomes an item with a stable key: the detection plus host/user/source/object, or an alert's own suppression fields (alerts are read back with `| loadjob` from `_audit action=alert_fired`).
3. **Dedupe.** `app/state.py` (SQLite on the `homelab-agents-state` volume) skips items already posted inside the renotify window (24 h security, 12 h ops). An item triaged but not posted (Slack down or unset) is posted on the next run without re-triaging. An item whose triage fails (Claude API error, bad token, timeout) isn't posted or marked: the next run tries it again, and only after 3 failed runs in a row is it posted with the error.
4. **Triage.** For each new item, up to 10 per run, the Claude Agent SDK runs `claude-opus-5-5` (effort medium, 40 turns max, 240 s per item; a timeout counts as a failed triage) with:
   - the gateway as MCP server `homelab` (`allowed_tools=mcp__homelab__*`): what each agent can call is enforced by the gateway's Cedar allowlist and the Splunk `mcp_agent` role, not by the prompt;
   - built-in tools limited to `Skill`, `Read` and `WebFetch` (no shell, no writes) with `permission_mode=dontAsk` (anything not allowed is denied, never asked). `Read` works only inside the agent's working directory, which holds nothing but the skills; `WebFetch` is allowed only for Splunk documentation sites (`config.DOC_DOMAINS`);
   - the **Splunk agent skills** (below), the agent's system prompt (`app/prompts/<agent>.md`) and the item's runbook (`app/runbooks/`), with the item's data marked as untrusted;
   - a JSON schema for the result: verdict, confidence, severity, headline, summary, evidence, next steps.

   No new triage starts after 20 minutes of a run (the Job is killed at 25): items not reached aren't posted or marked, and the next run triages them (`deferred` in the run summary).
5. **Report.** Every new item that isn't routine is posted as Slack Block Kit cards. **Routine** means the agent called it expected activity, benign or transient, with medium or high confidence and low or medium severity (`runner.routine()`). Routine items get no card: they're still triaged, logged to Splunk and listed on the dashboards, and they count as reported for the renotify window.
   - one **alert card** per item the agent doesn't call routine (or couldn't triage), most severe first: header with severity, title and the agent's one-line headline, a field grid (severity, verdict and confidence, host, and up to three facts such as source, user or object), *Recommended Action*, and **View Dashboard** / **View Analysis** links (plain links, not Block Kit buttons, which need an interactive Slack app; the analysis link opens that item's full triage in Splunk);
   - one **digest card** per run only when something wasn't triaged (over the per-run limit), one line each, repeats collapsed.

   Severity uses one scheme everywhere: red critical, orange high, yellow warning (medium), blue info (low). Text from logs or the model is escaped, so it can't add mentions or links. If a run fails, a card says so.
6. **Retro.** After the cards are posted, a run that triaged at least one item runs the retro (next section). It can't fail the run or delay a card.

## The improvement loop

Each agent is built from parts that can be changed one at a time: the system prompt (`app/prompts/`), a runbook per kind of item (`app/runbooks/`), skills, the gateway tools its identity is allowed, and the queue (`app/queues.py` and the detections behind it). The loop that improves them:

```mermaid
flowchart LR
    T["Triage<br/>each item"] --> TR["Trace<br/>every tool call"]
    TR --> R["Retro<br/>end of the run"]
    R --> F["Findings<br/>state.db + Splunk, repeats counted"]
    F --> P["Person or coding agent<br/>checks the finding, opens a PR"]
    P --> T
```

**Trace** (`app/tracing.py`). The runner records every tool call of a triage from the SDK's message stream: tool, input, how long it took, how much came back, whether it failed. A triage that fails or times out keeps the trace it got to. From it:
- each item's log event gains `steps`, `tool_errors`, `repeat_calls` (the same call made twice), `result_chars`, `max_result_chars` and `denied` (tools the sandbox refused);
- each call becomes a `kind=step` event (tool, input clipped to 500 characters, `ms`, `is_error`, `result_chars`, and the error text when it failed). Tool output is never sent, only its size.

**Retro** (`app/retro.py`, `skills/retro/SKILL.md`). One more model call (`claude-sonnet-5-5`, 180 s at most) with the `retro` skill, adapted from [mattpocock/skills](https://github.com/mattpocock/skills/tree/main/skills/engineering/retro) (MIT): that skill reviews a coding session, ours reviews a triage run with the same categories restated. It is given the agent's system prompt, the runbooks the run used, the tool and skill lists, and each item's result and trace (with a 300-character preview of each tool result, in memory only). It returns at most four findings, each with:

| Field | What |
|---|---|
| `category` | `context_docs` (a fact a runbook should have stated), `deterministic_filter` (a fixed rule could have settled the item without a model), `tool_economy`, `tools_and_access`, `skills`, `system_prompt`, `no_op`, `output_quality` |
| `target` | The one thing to change: `prompts/sec.md`, `runbooks/<name>.md`, `skill:<name>`, `tool:<name>`, `detection:<title>`, `queue`, `runner` |
| `observation`, `proposal` | What the trace showed (item and step numbers), and the change itself |

An empty list is normal. The retro has no gateway, no web access and no write tools: it has `Skill` and `Read` (the skills folder) and nothing else, because the traces it reads contain text from logs.

**Findings.** `state.db` keeps one row per distinct finding (`retro_findings`, with the observation of the latest run that reported it). Each retro is shown the findings of the last 14 days and reports a recurrence as a repeat of that row, so a problem is counted instead of restated; a repeat keeps its first wording. Every sighting is a `kind=retro` event (`finding_id`, `times_seen`, `category`, `target`, `title`, `observation`, `proposal`, `item_keys`), and the run event carries `retro_findings`, `retro_new`, `retro_cost_usd`, `retro_summary` and `retro_error`. `cost_usd` on the run is triage plus retro.

**Acting on them.** Nothing is applied automatically, and a finding is a lead, not an instruction: it was written by a model that read log text. *Homelab Operations* has **Retro findings** (most often seen first) and **Tool calls made during triage**. The same list in a search:

```
index=ai_agents sourcetype=homelab:agent kind=retro earliest=-14d
| stats dc(run_id) as runs latest(proposal) as proposal latest(observation) as observation by agent finding_id category target title
| sort - runs
```

Take the findings several runs agree on, check each against the items it cites (`item_keys`, and the item's `kind=step` events), and change the target in a pull request: a runbook line, a prompt edit, a detection exclusion in `85_detections`, a tool added to the agent's Cedar allowlist. A finding that stops being reported drops out of the known list after 14 days and out of `state.db` after 30; one that keeps coming back after its fix shows the fix didn't work.

**The weekly review.** A scheduled coding agent on coding-box (a T3 Code scheduled task, Mondays 09:00 Eastern) follows [`RETRO_REVIEW.md`](RETRO_REVIEW.md): it lists the findings at least three runs reported (`./retro-findings.sh 3`, read-only, from `state.db` over Tailscale SSH), checks each against the repository, and opens at most three **draft** pull requests, one finding each, marked `Retro-Finding: <agent>#<id>`. It never merges, never touches a host, and leaves anything about tools or permissions for you to decide. Closing one of its pull requests unmerged tells it not to raise that finding again.

To turn the retro off without a new image, set `env.RETRO: "0"` in `values.yaml`.

**Tests.** `image/tests/test_offline.py` runs the trace, the findings store and whole runs against fakes (no network, nothing posted): from `image/`, `uv run --with-requirements requirements.in python tests/test_offline.py`.

## Splunk agent skills

The image bakes in a selection of [Splunk's agent skills](https://github.com/splunk/splunk-agent-skills) (Apache-2.0), pinned to one commit (`SKILLS_COMMIT` in the Dockerfile), at `/app/workspace/.claude/skills`. `select_skills.py`:
- copies only the advisory and diagnostic skills that work from read-only evidence and that some agent uses (9: scheduled-search performance and troubleshooting, search performance, ingestion and HEC, Splunk health, indexes and storage, field extraction and CIM, alerting);
- drops each skill's `agents/` folder, `evals.json` and scripts (`*.js`, `*.py`, `*.sh`); everything else in the skill folder is kept;
- strips each skill's `allowed-tools` frontmatter, so no skill can pre-approve a tool.

Skills that need a shell or write files (`splunk-search` via `splunkctl`, the visualization and setup-page builders, Splunk Cloud admin) are left out. Each agent gets its own list (`config.AGENTS[...]["skills"]`).

Each list is short on purpose: every skill's description is in the model's context on every turn, and similar descriptions compete. The security agent has 5 (is the finding itself right: search logic, fields, alert throttling; and why a source stopped sending, for the telemetry detections); the ops agent has 7 (Splunk scheduler, ingestion, HEC, health, storage). The model decides when to invoke one, so the prompts say which skill fits which kind of item and the runbooks name the skill where one applies (`splunk-health.md`, `telemetry.md`, `capacity.md`, `general.md`, `secret-leak.md`, `detection-health.md`).

Whether they are used shows in Splunk: each item's `tools_used` has a `Skill:<name>` count, and the *Skills used by the agents* panel on *Homelab Operations* charts it. To add a skill: add it to `SKILLS` in `select_skills.py` and to the agent's list, say in the prompt when it fits, and name it in the runbooks it belongs to.

To update the skills: change `SKILLS_COMMIT`, read the diff of the skills you ship (they become part of the agents' instructions), and push.

## Deploy

```bash
kubectl apply -f infrastructure_tooling/homelab_agents/application.yaml
```

Image changes deploy themselves: CronJob pods are new every run and pull `:main` (`imagePullPolicy: Always`), so the run after the workflow finishes uses the new image. Chart changes go through Argo CD as usual. To stop both agents, set `suspend: true` in `values.yaml`.

### Secrets in Vault (once)

| Path | Key | What |
|---|---|---|
| `kv/homelab-agents` | `claude_oauth_token` | Subscription token from `claude setup-token` (valid one year) |
| `kv/slack-alerts` | `webhook_url` | Slack incoming webhook |
| `kv/cribl-hec` | `token` | Already there (OTel collector on k3s) |
| `kv/apiary-ghcr-pull` | `username`, `token` | GHCR `read:packages` credential, shared with Apiary |

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

(The Vault UI works too: `https://vault.bmosan.com/ui` from the tailnet, KV v2 engine `kv`.) A new value is picked up on the next run.

The Vault side (runtime, not GitOps; already applied):

```bash
vault policy write homelab-agents-read - <<'EOF'
path "kv/data/slack-alerts"   { capabilities = ["read"] }
path "kv/data/cribl-hec"      { capabilities = ["read"] }
path "kv/data/homelab-agents" { capabilities = ["read"] }
EOF
vault write auth/kubernetes/role/homelab-agents \
  bound_service_account_names=agent-sec,agent-ops bound_service_account_namespaces=toolhive-clients \
  audience=vault token_policies=homelab-agents-read token_ttl=10m token_max_ttl=15m
```

### Network

The pods accept no connections. They can reach:
- DNS;
- the vMCP Service (`vmcp-homelab.toolhive-mcp:4483`), which admits them through the gateway's `allow-agents` NetworkPolicy;
- Vault (`vault.vault:8200`);
- Splunk HEC (`192.168.4.11:8443`, HTTPS; the cert is checked against the homelab CA only, `files/homelab-ca.crt`);
- HTTPS on the public internet (Anthropic API, Slack, Splunk docs), with private ranges excluded.

Claude Code's telemetry and update checks are off (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`).

## Operating it

```bash
kubectl -n toolhive-clients get cronjobs,jobs,pods -l app.kubernetes.io/name=homelab-agents
kubectl -n toolhive-clients logs job/<job>                                        # one run's summary line
kubectl -n toolhive-clients create job --from=cronjob/homelab-agent-ops ops-now   # run now
```

To look at a queue without triaging or posting, run a one-off job with `--collect-only`. Use `--dry-run` instead to triage and print the Slack cards without posting:

```bash
kubectl -n toolhive-clients create job --from=cronjob/homelab-agent-sec sec-look --dry-run=client -o json |
  jq '.spec.template.spec.containers[0].args += ["--collect-only"]' | kubectl create -f -
```

In Splunk: `index=ai_agents sourcetype=homelab:agent kind=run | table _time source status collected new triaged retrying cost_usd retro_findings retro_error error error_at`. One triage's tool calls: `... kind=step key="<item key>" | sort n | table n tool input ms is_error result_chars error`. `error_at` is where a failed run broke, as our own frames outermost first (`runner.py:… run > clients.py:… _http`). Cost is the API-equivalent figure the SDK reports; on the subscription it counts against usage limits, not a bill. Pod logs, full tracebacks included, are in `index=ai_agents sourcetype=kube:container` (Cribl routes the `toolhive-clients` namespace there; Kubernetes events for it stay in `index=k8s`).

**Watchdog.** A run that never starts can't report itself, so Splunk watches instead. *Agents - not running or failing* (app `85_homelab_ops`, at :05 and :35) posts a card to Slack through the `homelab_slack` action when an agent:
- hasn't run for 75 minutes;
- had every run in the last hour fail;
- had every triage in the last hour fail (this is how an expired Claude token shows up);
- got Kubernetes warnings for its CronJob, Jobs or pods (failed job, image pull, scheduling, deadline).

*Agents - spend or turn limit exceeded* (same app, hourly at :44) posts a card when, over the last 24 hours, an agent's runs cost more than 15 USD in total, one run cost more than 4 USD, or one item took 30 or more turns (`MAX_TURNS` is 40). In the week to 2026-11-12 the highest were 1.92 USD a run, 6.66 USD a day and 15 turns an item.

```mermaid
flowchart LR
    R["index=ai_agents<br/>run + item logs"] --> W
    E["index=k8s<br/>Warning events for the CronJobs,<br/>Jobs and pods (not UnexpectedJob)"] --> W
    W{"Agents - not running or failing<br/>security-tooling-01, :05 and :35"}
    W -->|"no run in 75 min"| P
    W -->|"every run in the last hour failed"| P
    W -->|"every triage in the last hour failed"| P
    W -->|"Kubernetes warnings"| P
    P["homelab_slack action<br/>suppressed 6 h per agent + problem type"] --> SL["Slack card"]
```

It's suppressed per agent and problem type for 6 hours. Splunk runs on security-tooling-01, so it still fires if k3s-01 is down. The webhook it uses is a copy of the Vault one in Splunk's credential store; after changing `kv/slack-alerts`, refresh it:

```bash
ssh root@k3s-01 'kubectl -n default get secret vault-token -o jsonpath={.data.token} | base64 -d |
  kubectl -n vault exec -i vault-0 -- sh -c "read -r VAULT_TOKEN; export VAULT_TOKEN; vault kv get -field=webhook_url kv/slack-alerts"' |
  ssh root@security-tooling-01 /opt/splunk-stack/scripts/set-slack-webhook.sh
```

**Dashboards.** *Homelab Security* has a **Security agent** section (runs, failures, items triaged, items not routine, cost, verdicts, the triaged items and the run log). *Homelab Operations* has an **Agents** section with the health of both agents and the ops agent's items.

**Changing what they look at.** To add an ops check, add a row to `OPS_CHECKS` in `image/app/queues.py`; to change what the security agent reads, edit `security()` there. Runbooks are plain Markdown, matched by detection or alert name (`DETECTION_RUNBOOKS` and `ALERTS` in `queues.py`; an unmatched detection gets `general.md`).
