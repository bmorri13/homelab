# Monitoring

## Current stack

- [Splunk logging box on security-tooling-01](../infrastructure_tooling/monitoring_stack/splunk/README.md) (Splunk, Cribl, detections, dashboards)
- [OTel host agent](../infrastructure_tooling/monitoring_stack/splunk/otel-agent/README.md)
- [OpenTelemetry Collector on k3s-01](../infrastructure_tooling/otel_collector_k3s/README.md)
- [k3s API audit log](../infrastructure_tooling/k3s_audit/README.md)
- [Falco](../infrastructure_tooling/monitoring_stack/falco/README.md)
- [OpenCanary honeypot](../infrastructure_tooling/opencanary/README.md)
- [ToolHive MCP gateway](../infrastructure_tooling/toolhive_mcp/README.md)
- [Homelab agents](../infrastructure_tooling/homelab_agents/README.md)

## History

Before Splunk, the logging box was Elastic (Elasticsearch, Kibana, Logstash and Beats) with a standalone Cribl Stream in front of it, both run with Docker Compose on a Proxmox VM. Elastic was picked to avoid needing a license; Splunk on a developer license replaced it. Both stacks were removed from the repo in November 2026; the compose files and the step-by-step write-up are in the git history (`infrastructure_tooling/monitoring_stack/elastic/`, `infrastructure_tooling/monitoring_stack/cribl/` and this page).
