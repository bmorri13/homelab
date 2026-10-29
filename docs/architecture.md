# Architecture

## Infrastructure
Proxmox nodes and their VMs, the k3s-01 cluster, the logging and security box, the network and outside services.

![Homelab infrastructure](images/infrastructure.drawio.svg)

## How it's deployed
CI (GitHub-hosted, ARC and VM runners), Argo CD, and secrets from Vault through External Secrets. Splunk and Cribl on security-tooling-01 are deployed by hand, not by Argo CD.

```mermaid
flowchart LR
    dev["Developer<br/>gitleaks before push"] -->|PR, merge to main| repo["GitHub<br/>bmorri13/homelab (public)<br/>Renovate PRs"]

    subgraph ci["CI: GitHub Actions"]
        img["homelab-agents-image<br/>GitHub-hosted runner<br/>Chainguard multi-stage build"]
        packer["proxmox-packer-builder<br/>ARC runners on k3s-01<br/>weekly + on change"]
        tf["terraform plan / apply<br/>VM runner (github-action-runner)"]
    end

    repo -->|image/**| img -->|push :main| ghcr[("GHCR (private)<br/>homelab-agents")]
    repo -->|packer/**| packer --> pve["Proxmox<br/>VM templates"]
    repo -->|terraform/**| tf --> vms["Proxmox VMs"]
    tf -.->|state| consul[("Consul on Unraid")]

    subgraph k3s["k3s-01 (GitOps)"]
        argo["Argo CD<br/>~20 apps, auto-sync + prune"]
        apps["Workloads"]
        agents["Agent CronJobs<br/>pull :main every run"]
        vault["Vault"]
        eso["External Secrets"]
        cm["cert-manager"]
    end

    repo -->|Argo CD polls main| argo --> apps
    argo --> agents
    ghcr -.->|pull| agents
    vault --> eso -->|Kubernetes Secrets| apps
    vault -.->|pod-token login| agents
    cm -->|DNS-01| cf["Cloudflare DNS<br/>Let's Encrypt certs"]

    subgraph st["security-tooling-01 (not GitOps)"]
        splunk["Splunk apps<br/>scripts/deploy.sh"]
        cribl["Cribl pipelines<br/>API sync scripts"]
    end
    repo -.->|deployed by hand| splunk
    repo -.->|deployed by hand| cribl
```

## Data flow
Every source sends to Cribl's one HEC input; Cribl masks secrets, normalizes and routes to the Splunk indexes, and Splunk turns the data into detections, alerts and dashboards. The agents triage what the detections and alerts find.

```mermaid
flowchart LR
    subgraph src["Sources"]
        direction TB
        hosts["VM + Proxmox host agents (OTel)<br/>journald, Docker logs, host metrics"]
        pvem["Proxmox OTLP metrics"]
        k8s["k3s OTel DaemonSet<br/>pod logs, events, cluster metrics"]
        audit["k3s API audit log"]
        falco["Falco (10 VMs + k3s, k8saudit)"]
        vaudit["Vault audit"]
        unifi["UniFi syslog + IDS"]
        canary["OpenCanary honeypot"]
        appl["App logs (daily news)"]
        agentlog["Agent run logs"]
        hosts ~~~ pvem ~~~ k8s ~~~ audit ~~~ falco ~~~ vaudit ~~~ unifi ~~~ canary ~~~ appl ~~~ agentlog
    end

    subgraph st["security-tooling-01"]
        direction LR
        cribl["Cribl Stream · hec_in 192.168.4.11:8088<br/>secret masking · Falco → OCSF + MITRE<br/>logins → linux_secure · routing, noise drops"]
        idx[("Splunk indexes<br/>security · audit · infra · k8s<br/>apps · network · metrics · ai_agents")]
        det["18 ATT&CK v20 detections<br/>every 15 min → homelab:detection"]
        alerts["Alerts<br/>Falco, honeypot, secret leaks"]
        dash["Dashboards<br/>Homelab Ops · Homelab Security"]
        wd["Agents watchdog<br/>:05 / :35"]
    end

    src --> cribl --> idx
    idx --> det & alerts & dash & wd
    det -->|findings queue| agents["Homelab agents on k3s-01<br/>sec :00/:30 · ops :15/:45"]
    alerts -->|fired alerts| agents
    agents -->|alert + digest cards| slack["Slack"]
    wd -->|"only direct post"| slack
```

## The agents
The run flow of the two triage agents and the watchdog that checks on them are in the [homelab agents README](../infrastructure_tooling/homelab_agents/README.md#how-a-run-works).

## Editing the diagrams
- **Infrastructure** is a draw.io diagram saved as `images/infrastructure.drawio.svg`: the SVG GitHub shows also contains the editable diagram. Open it in [draw.io Desktop](https://www.drawio.com/) (`brew install --cask drawio`) or the VS Code *Draw.io Integration* extension, edit, and save it under the same name. When exporting from the desktop app, use the light theme (`drawio -x -f svg --embed-diagram --theme light --embed-svg-fonts false --border 12 -o docs/images/infrastructure.drawio.svg <file>`); the default adaptive theme turns the background dark in GitHub's dark mode. Icons are from [homarr-labs/dashboard-icons](https://github.com/homarr-labs/dashboard-icons), embedded in the file.
- **Deployment, data flow and the agents** are Mermaid, written in the Markdown. GitHub draws them; edit the text in the same PR as the change they describe.
