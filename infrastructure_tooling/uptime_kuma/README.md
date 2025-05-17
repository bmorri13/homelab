# Uptime Kuma Helm Chart

This Helm chart deploys [Uptime Kuma](https://github.com/louislam/uptime-kuma) on a Kubernetes cluster in the `uptime-kuma` namespace.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.2.0+
- PV provisioner support in the underlying infrastructure (if persistence is needed)

## Installing the Chart

To install the chart with the release name `uptime-kuma`:

```bash
helm install uptime-kuma ./uptime_kuma --namespace uptime-kuma --create-namespace
```

Alternatively, the chart will create the namespace for you if it doesn't exist.

## Uninstalling the Chart

To uninstall/delete the `uptime-kuma` deployment:

```bash
helm uninstall uptime-kuma
```

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| image.repository | string | `"louislam/uptime-kuma"` | Docker image repository |
| image.tag | string | `"1"` | Docker image tag |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| replicaCount | int | `1` | Number of replicas to deploy |
| service.type | string | `"ClusterIP"` | Kubernetes Service type |
| service.port | int | `3001` | Service port |
| service.targetPort | int | `3001` | Container port |
| service.nodePort | int | `nil` | NodePort (if service.type is NodePort) |
| persistence.enabled | bool | `true` | Enable persistence for data |
| persistence.storageClassName | string | `""` | Storage Class Name for PVC |
| persistence.accessMode | string | `"ReadWriteOnce"` | PVC Access Mode |
| persistence.size | string | `"1Gi"` | PVC Storage Request |
| resources | object | `{}` | CPU/Memory resource requests/limits |
| ingress.enabled | bool | `false` | Enable ingress resource |
| ingress.className | string | `""` | Ingress class name |
| ingress.annotations | object | `{}` | Additional ingress annotations |
| ingress.hosts | list | `[{"host":"uptime-kuma.local","paths":[{"path":"/","pathType":"Prefix"}]}]` | List of ingress hosts |
| ingress.tls | list | `[]` | Ingress TLS configuration |

## Usage Example

Converting from Docker Compose:

```yaml
# Docker Compose
services:
  uptime-kuma:
    image: louislam/uptime-kuma:1
    volumes:
      - ./data:/app/data
    ports:
      - 3001:3001
    restart: unless-stopped
```

Equivalent Helm values:

```yaml
# values.yaml
image:
  repository: louislam/uptime-kuma
  tag: "1"

service:
  type: NodePort  # or use LoadBalancer
  port: 3001
  targetPort: 3001
  nodePort: 30301  # optional, only needed for NodePort

persistence:
  enabled: true
  size: 1Gi
```

## Note on State

Uptime Kuma stores its data in the `/app/data` directory. This chart configures a persistent volume for that purpose.

For production use, ensure you have proper backup procedures in place for the persistent volume.
