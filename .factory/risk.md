# Change risk

How risky a change to `bmorri13/homelab` is, and so how much of a person's attention it gets. Written for dependency updates first. A triage agent reads this file; the owner owns it.

## Tiers

| Risk | What it looks like here | Agent does | Person does |
|---|---|---|---|
| Low | Only the Terraform proxmox provider patch/minor bumps in infrastructure_tooling/proxmox/terraform (e.g. #112, #144, #150), which have a validate-and-plan workflow on the PR and have merged without a hand 30+ times, and Renovate-managed actions/upload-artifact patch/minor bumps. | Checks the change against the release notes and this file, and comments a verdict | Merges on the verdict |
| Medium | Anything not matched: cribl/cribl digests in the monitoring_stack compose file (#118, #137, #140, nothing deploys them by merge), docs Dockerfile bumps (#148), the homelab-agents image group (#258, #334), mcp-for-argocd image, GitHub Actions bumps in workflows (#117, #147, #146, #121), and the Terraform/Packer version pins (#431, #430). | Reads the release notes, checks this repository's settings against the new version, and comments what to watch after the merge | Reviews, then merges |
| High | Anything that deploys on merge via Argo CD with auto-sync and has no checks: toolhive operator/CRDs charts (#182, #392, #460), toolhive_mcp values (#172), ARC runner scale sets (#153), otel collector charts (#183, #215), falco (#252), Authentik and its Postgres (#273, #456), plus Vault, External Secrets and cert-manager pins, and host pins such as k3s, Falco and kube-bench. | Writes up what breaks and the steps to roll it out by hand. Never recommends merging unread | Reviews, merges and does the rollout |

## Rules

A change gets the highest tier of every rule that matches it. A change no rule matches is **medium**. The agent may raise a tier when it finds a reason to, never lower one. `Path` is a glob over the files the change touches, `Package` a glob over the package name, `Update` is `*` or a list of `digest`, `patch`, `minor`, `major`.

| Tier | Path | Package | Update | Why |
|---|---|---|---|---|
| high | `infrastructure_tooling/vault/**` | `*` | * | Vault holds the secrets for the whole homelab, so a bad chart bump can seal it or break secret access everywhere. |
| high | `infrastructure_tooling/external_secrets_operator/**,infrastructure_tooling/eso_*/**,infrastructure_tooling/vault_ingress/**` | `*` | * | External Secrets feeds secrets to every component, and a CRD or API version change can stop secret sync cluster-wide. |
| high | `infrastructure_tooling/cert_manager/**` | `*` | * | cert-manager must move one minor at a time, and a jump can break certificate issuance and CRDs. |
| high | `infrastructure_tooling/authentik/**` | `*` | * | Authentik is the login for Splunk and Cribl, and a Postgres major or chart/image mismatch can lock everyone out or make the data unreadable. |
| high | `infrastructure_tooling/toolhive_*/**` | `*` | * | ToolHive operator and CRD charts auto-sync live, CRD changes cannot be rolled back easily, and the two charts must move together. |
| high | `infrastructure_tooling/arc_runner_scaleset*/**` | `*` | * | These are the CI runners that run the Terraform and image workflows with repository credentials, and the chart must match the controller version. |
| high | `infrastructure_tooling/falco_k3s/**,infrastructure_tooling/otel_collector_k3s/**,infrastructure_tooling/trivy_operator/**` | `*` | * | These run live on the cluster and carry security and telemetry, and vendored chart bumps have often needed a hand to commit the new archive. |
| high | `infrastructure_tooling/k3s_node/**,infrastructure_tooling/kube_bench/**,infrastructure_tooling/monitoring_stack/falco/**,infrastructure_tooling/monitoring_stack/splunk/otel-agent/**` | `*` | * | These pins change the cluster or host software and carry a SHA-256 beside the version, so a version-only PR fails closed or leaves the repository ahead of what runs. |
| high | `infrastructure_tooling/homelab_agents/**,infrastructure_tooling/toolhive_mcp/**,infrastructure_tooling/art_validation/**,infrastructure_tooling/apiary/**,infrastructure_tooling/adguard/**,infrastructure_tooling/argocd_config/**` | `registry.k8s.io/kubectl,docker.io/alpine/k8s,pydantic-core,pydantic_core` | * | kubectl must stay within one minor of the cluster, and pydantic-core must move only with pydantic. |
| high | `infrastructure_tooling/argocd_config/**` | `*` | * | This is Argo CD's own configuration, and a bad change can stop every deployment. |
| high | `.github/workflows/terraform-apply.yml` | `hashicorp/terraform,hashicorp/setup-terraform` | * | A Terraform version change must still plan with no changes before merge, or apply can alter Proxmox. |
| high | `infrastructure_tooling/toolhive_mcp/values.yaml` | `ghcr.io/sirkirby/unifi-network-mcp,python` | * | These images auto-deploy live and have led to follow-up fixes by hand. |
| medium | `.github/workflows/**` | `*` | * | Workflows run with the repository's credentials and self-hosted runners, so action bumps need a glance at the release notes. |
| low | `infrastructure_tooling/proxmox/terraform/main.tf` | `proxmox` | patch,minor | The Terraform plan workflow runs on the PR, and apply is manual only, so a bad bump surfaces before anything changes. |
