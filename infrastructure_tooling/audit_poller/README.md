# Audit poller

Collects audit logs from the services that control the homelab from outside it, using only their free APIs. A CronJob on k3s-01 runs `k8s/poll.py` every 15 minutes and sends new events through Cribl's HEC to Splunk `index=audit`.

| Source | Sourcetype | API | Credential | Kept at source |
|---|---|---|---|---|
| Cloudflare account audit log | `cloudflare:audit` | `GET /accounts/{id}/logs/audit` (v2) | API token with Account Settings Read | 18 months |
| Tailscale configuration log | `tailscale:audit` | `GET /tailnet/-/logging/configuration` | OAuth client with the `logs:configuration:read` scope | 90 days |
| GitHub repository activity | `github:activity` | `GET /repos/{repo}/activity` | None (public repository) | Not stated |

Each run also writes one `audit_poller:status` event per source, with `status` (`ok`, `skipped` or `error`), `new_events` and `error`. Paid features aren't used: Logpush, Tailscale log streaming and flow logs, and GitHub audit log streaming.

GitHub's personal security log (logins, new tokens and SSH keys) has no API on a personal account, so it isn't collected. Export it by hand from your account's Security log page when you need it.

Proxmox isn't here because its logs are local: tasks and logins are in journald, and API writes come from the OTel agent's `pve` role (`pve:access`, `../monitoring_stack/splunk/otel-agent`).

## Setup

1. Create the Cloudflare token (My Profile → API Tokens, Account Settings: Read, for your account only) and the Tailscale OAuth client (Admin console → Settings → OAuth clients, read access to configuration logs only).
2. Store them in Vault at `kv/audit-poller`, in the Vault UI, so they stay out of shell history. A source whose keys are missing is skipped, so GitHub works without them, but the path must exist.

   | Key | Value |
   |---|---|
   | `cloudflare_account_id` | Account ID |
   | `cloudflare_token` | API token |
   | `tailscale_client_id` | OAuth client ID |
   | `tailscale_client_secret` | OAuth client secret |

3. Apply the Argo CD Application: `kubectl apply -f infrastructure_tooling/audit_poller/application.yaml`.
4. Run it now: `kubectl -n audit-poller create job --from=cronjob/audit-poller audit-poller-manual-$(date +%s)`.
5. Check: `index=audit sourcetype=audit_poller:status | stats latest(status) latest(error) by poll_source`.

ESO reads changed values within the hour. To apply one sooner, delete the `audit-poller` Secret in the namespace; ESO recreates it.

## Detections

In `85_detections`: *HL - GitHub main branch changed outside a pull request*, *HL - Audit source polling failing*, and *HL - Log source stopped sending* if status events stop for 45 minutes. Cloudflare and Tailscale have no change detections yet, because they should be written against real events.

## State

`/state/state.json` on a small PVC holds each source's position and recently sent event IDs. Each read overlaps the last by 30 minutes and skips IDs already sent. If the PVC is lost, the next run reads the last 24 hours again and repeats those events. If the poller is down longer than a source's retention, that stretch is lost.
