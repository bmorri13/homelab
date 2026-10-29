# Architecture

## Infrastructure
Proxmox nodes and their VMs, the k3s-01 cluster, the logging and security box, the network and outside services.

![Homelab infrastructure](images/infrastructure.drawio.svg)

## How it's deployed
CI (GitHub-hosted, ARC and VM runners), Argo CD, and secrets from Vault through External Secrets. Splunk and Cribl on security-tooling-01 are deployed by hand, not by Argo CD.

![How it's deployed](images/deployment.drawio.svg)

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
- **Infrastructure and deployment** are draw.io diagrams saved as `images/infrastructure.drawio.svg` and `images/deployment.drawio.svg`: the SVG GitHub shows also contains the editable diagram. Open it in [draw.io Desktop](https://www.drawio.com/) (`brew install --cask drawio`) or the VS Code *Draw.io Integration* extension, edit, and save it under the same name. When exporting from the desktop app, use the light theme (`drawio -x -f svg --embed-diagram --theme light --embed-svg-fonts false --border 12 -o docs/images/<name>.drawio.svg <file>`); the default adaptive theme turns the background dark in GitHub's dark mode. Icons are from [homarr-labs/dashboard-icons](https://github.com/homarr-labs/dashboard-icons), embedded in the file.
- **Data flow and the agents** are Mermaid, written in the Markdown. GitHub draws them; edit the text in the same PR as the change they describe.
