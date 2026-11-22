# Phoenix

[Arize Phoenix](https://github.com/Arize-ai/phoenix) is where the agents' OpenTelemetry traces go to be read: one per pull request the software-factory agent triages (project `software-factory`; traces from before 2026-11-22 stay in `default`), and one per triage, retro or self-test of the homelab sec and ops agents (projects `homelab-agent-sec` and `homelab-agent-ops`; see `../homelab_agents`). Each trace has every model request and tool call under it, and in every project each run is a session. Later it also holds the eval datasets and results.

It is one pod with SQLite on a 5Gi volume, at `https://phoenix.bmosan.com` from the tailnet only.

## Who can reach it

| From | Allowed |
|---|---|
| The tailnet, through Traefik, after signing in to Authentik as a member of `authentik Admins` (with a second factor) | The web UI and API |
| Pods in the namespaces listed under `senders` in `values.yaml` | Sending traces to `http://phoenix.phoenix.svc.cluster.local:6006/v1/traces` |
| Anything else | Nothing |

The login is Authentik's, by forward auth, the same way as the Cribl UI: Traefik shows each request to Authentik's embedded outpost first. The provider, the application and who may pass are in `../authentik/blueprints/cribl.yaml`; the outpost's login paths on this host are in `../authentik/templates/phoenix.yaml`. Phoenix's own login is switched off (`PHOENIX_ENABLE_AUTH=false`; Phoenix turns it on by default), so senders inside the cluster need no API key. The network policy is what keeps other pods from reaching Phoenix without the gate.

The traces hold tool names, file paths, token counts and the triage verdicts; they do not hold prompts or file contents unless a sender turns that on.

The pod can reach DNS and nothing else, and Phoenix's usage pixels and built-in assistant are switched off.

To use Phoenix's API from a script (evals, exports), go around the gate from a machine with cluster access: `kubectl -n phoenix port-forward svc/phoenix 6006`.

## Install

1. Point `phoenix.bmosan.com` at k3s-01's tailnet address, the same as `apiary.bmosan.com`.
2. `kubectl apply -f infrastructure_tooling/phoenix/application.yaml`

## Operate

| To | Do |
|---|---|
| Let another namespace send traces | Add it to `senders` |
| Keep traces longer or shorter | `retentionDays` |
| Upgrade | Bump `image.tag` and `image.digest` together. Phoenix migrates its database on start; the volume is not backed up, so losing it loses the traces, not anything the agents need |
