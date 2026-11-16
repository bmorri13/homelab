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

## Checks

What can show that an update to each component is safe: what runs before the merge, and what to look at after it to say the component came back. Read from the repository by discovery, with no model. A gap is the reason a component cannot yet earn a lower tier.

| Component | Before merge | After merge | Gaps |
|---|---|---|---|
| `.github/workflows` | None | Nothing in the repository deploys this: a merge changes no running system | nothing runs on a pull request |
| `infrastructure_tooling/adguard` | None | Argo CD app `adguard` is Synced and Healthy<br>Rolled out: `deployment/adguard` in `adguard` | nothing runs on a pull request<br>no readiness probe on 1 of 1 workloads, so Healthy does not prove they work |
| `infrastructure_tooling/apiary` | None | Argo CD app `apiary` is Synced and Healthy<br>Rolled out: `deployment/apiary` in `apiary`<br>Answers: `apiary.bmosan.com`<br>Accepted by their controller: `ExternalSecret`, `Middleware` objects | nothing runs on a pull request<br>no readiness probe on 1 of 1 workloads, so Healthy does not prove they work |
| `infrastructure_tooling/arc_runner_scaleset` | None | Argo CD app `arc-runner-scaleset-k3s` is Synced and Healthy<br>Accepted by their controller: `AutoscalingRunnerSet`, `ExternalSecret` objects | nothing runs on a pull request<br>deploys on merge but defines no workload: Argo CD's Healthy is the only signal |
| `infrastructure_tooling/arc_runner_scaleset_cointhieves` | None | Argo CD app `arc-runner-cointhieves-k3s` is Synced and Healthy<br>Accepted by their controller: `AutoscalingRunnerSet`, `ExternalSecret` objects | nothing runs on a pull request<br>deploys on merge but defines no workload: Argo CD's Healthy is the only signal |
| `infrastructure_tooling/argocd_config` | None | Argo CD app `argocd-config` is Synced and Healthy | nothing runs on a pull request<br>deploys on merge but defines no workload: Argo CD's Healthy is the only signal |
| `infrastructure_tooling/art_validation` | None | Argo CD app `art-validation` is Synced and Healthy<br>Rolled out: `cronjob/art-validation` in `art-validation` | nothing runs on a pull request |
| `infrastructure_tooling/authentik` | None | Argo CD app `authentik` is Synced and Healthy<br>Rolled out: `deployment/authentik-server`, `deployment/authentik-worker`, `statefulset/authentik-postgres`, `cronjob/authentik-postgres-backup` in `authentik`<br>Answers: `auth.bmosan.com`, `cribl.bmosan.com`, `splunk.bmosan.com`<br>Accepted by their controller: `ExternalSecret`, `Middleware`, `ServersTransport` objects | nothing runs on a pull request |
| `infrastructure_tooling/cert_manager` | None | A merge deploys nothing: `cert-manager-k3s` is applied or synced by hand, then must be Synced and Healthy | nothing runs on a pull request |
| `infrastructure_tooling/eso_cluster_provider_docker_compose_03` | None | Nothing in the repository deploys this: a merge changes no running system<br>Accepted by their controller: `ClusterSecretStore` objects | nothing runs on a pull request |
| `infrastructure_tooling/eso_cluster_provider_helm` | None | A merge deploys nothing: `eso-cluster-provider-helm` is applied or synced by hand, then must be Synced and Healthy<br>Accepted by their controller: `ClusterSecretStore` objects | nothing runs on a pull request |
| `infrastructure_tooling/eso_external_secrets_helm` | None | A merge deploys nothing: `eso-external-secrets-helm` is applied or synced by hand, then must be Synced and Healthy<br>Accepted by their controller: `ClusterIssuer`, `ExternalSecret` objects | nothing runs on a pull request |
| `infrastructure_tooling/external_secrets_operator` | None | A merge deploys nothing: `external-secrets-operator` is applied or synced by hand, then must be Synced and Healthy | nothing runs on a pull request |
| `infrastructure_tooling/falco_k3s` | Workflow: Falco rules | Argo CD app `falco-k3s` is Synced and Healthy<br>Rolled out: `daemonset/falco`, `deployment/falco-k8saudit` in `falco` | None |
| `infrastructure_tooling/homelab_agents` | 1 test file(s) | Argo CD app `homelab-agents` is Synced and Healthy<br>Rolled out: `cronjob/homelab-agent-ops`, `cronjob/homelab-agent-sec` in `toolhive-clients`<br>Accepted by their controller: `ExternalSecret` objects | nothing runs on a pull request (homelab-agents image runs only after merge) |
| `infrastructure_tooling/mcp_for_argocd_image` | None | Nothing in the repository deploys this: a merge changes no running system | nothing runs on a pull request (mcp-for-argocd image runs only after merge) |
| `infrastructure_tooling/monitoring_stack` | Workflow: Falco rules<br>2 test file(s) | Nothing in the repository deploys this: a merge changes no running system | None |
| `infrastructure_tooling/opencanary` | None | Nothing in the repository deploys this: a merge changes no running system | nothing runs on a pull request |
| `infrastructure_tooling/otel_collector_k3s` | None | Argo CD app `otel-collector-k3s` is Synced and Healthy<br>Rolled out: `daemonset/otel-collector-opentelemetry-collector-agent` in `otel-collector`<br>Accepted by their controller: `ExternalSecret` objects | nothing runs on a pull request |
| `infrastructure_tooling/proxmox` | Workflow: Build Proxmox Packer Template<br>Workflow: Terraform Validate and Plan | Nothing in the repository deploys this: a merge changes no running system | None |
| `infrastructure_tooling/tailscale_operator` | None | Nothing in the repository deploys this: a merge changes no running system<br>Rolled out: `deployment/operator` in `default`<br>Accepted by their controller: `ExternalSecret` objects<br>8 CRDs: existing objects of those kinds still reconcile | nothing runs on a pull request<br>no readiness probe on 1 of 1 workloads, so Healthy does not prove they work |
| `infrastructure_tooling/toolhive_crds` | None | Argo CD app `toolhive-crds` is Synced and Healthy<br>14 CRDs: existing objects of those kinds still reconcile | nothing runs on a pull request |
| `infrastructure_tooling/toolhive_mcp` | None | Argo CD app `toolhive-mcp` is Synced and Healthy<br>Answers: `mcp.bmosan.com`<br>Accepted by their controller: `ExternalSecret`, `MCPGroup`, `MCPOIDCConfig`, `MCPRemoteProxy`, `MCPServer`, `Middleware`, `VirtualMCPServer` objects | nothing runs on a pull request<br>deploys on merge but defines no workload: Argo CD's Healthy is the only signal |
| `infrastructure_tooling/toolhive_operator` | None | Argo CD app `toolhive-operator` is Synced and Healthy<br>Rolled out: `deployment/toolhive-operator` in `toolhive-system` | nothing runs on a pull request |
| `infrastructure_tooling/trivy_operator` | None | Argo CD app `trivy-operator` is Synced and Healthy<br>Rolled out: `deployment/trivy-operator`, `statefulset/trivy-server` in `trivy-system`<br>Accepted by their controller: `ClusterComplianceReport` objects<br>12 CRDs: existing objects of those kinds still reconcile | nothing runs on a pull request |
| `infrastructure_tooling/vault` | None | A merge deploys nothing: `vault` is applied or synced by hand, then must be Synced and Healthy | nothing runs on a pull request |
| `infrastructure_tooling/vault_ingress` | None | Argo CD app `vault-ingress` is Synced and Healthy<br>Answers: `vault.bmosan.com`<br>Accepted by their controller: `Middleware` objects | nothing runs on a pull request<br>deploys on merge but defines no workload: Argo CD's Healthy is the only signal |
