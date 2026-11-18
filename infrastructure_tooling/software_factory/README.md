# software-factory

The dependency-update triage agent, run as a CronJob. Its code and documentation are in the private repository `bmorri13/software-factory`; this directory only deploys it.

Every run, for each repository in `values.yaml`:

1. Checks the updates it merged earlier: reads the workflow runs on the merge commit, writes the result into the pull request's note, and opens a revert if they failed.
2. Triages the open Renovate and Dependabot pull requests against that repository's `.factory/risk.md` and posts one note on each.
3. Merges only what a rule in that risk file marks `merge`, and only when every check in code passes.

A run that finds nothing new calls no model.

## What it needs

| Vault path | Fields | What |
|---|---|---|
| `kv/software-factory/github-app` | `app_id`, `private_key` | GitHub App `bmorri13-software-factory`, installed on the repositories listed in `values.yaml` only |
| `kv/homelab-agents` | `claude_oauth_token` | The Claude credential the homelab agents use |
| `kv/apiary-ghcr-pull` | `username`, `token` | GHCR `read:packages`, for the private image |

The pod has no Kubernetes API token, a read-only root filesystem, and can reach only DNS and HTTPS on the internet.

## Install

```bash
kubectl apply -f infrastructure_tooling/software_factory/application.yaml
```

## Operate

| To | Do |
|---|---|
| Stop every run | `suspend: true` in `values.yaml` |
| Keep the notes but stop all merging | `merge: false` on the repository in `values.yaml` |
| Stop merging one kind of update | Change `merge` to `comment` on its rule in `.factory/risk.md` |
| Run it now | `kubectl -n software-factory create job --from=cronjob/software-factory-bmorri13-homelab manual-1` |
| See what a run did | `kubectl -n software-factory logs job/<name>`: one JSON line per pull request (`software_factory.triage`, `software_factory.validate`) |
| Add a repository | Install the GitHub App on it, add `.factory/risk.md` to it, add it to `repos` |
