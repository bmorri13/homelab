# Cert Manager
- Deploying Cert Manager to configure valid certs within the enviornment
## Setup
1. Deploy helm chart
```bash
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.21.3 \
  --set crds.enabled=true
```
> NOTE: On k3s-01, cert-manager is now the Argo CD app `cert-manager-k3s`; the version it runs is pinned in [`infrastructure_tooling/cert_manager/application.yaml`](../../infrastructure_tooling/cert_manager/application.yaml).

2. Create the `bmosan-cert` ClusterIssuer. It is a template in the `eso-external-secrets-helm` Argo CD app ([`cluster-issuer.yaml`](../../infrastructure_tooling/eso_external_secrets_helm/templates/cluster-issuer.yaml)), next to the `cf-api-token` ExternalSecret it reads, so it comes back with that app. Sync `eso-external-secrets-helm` in Argo CD after `cert-manager-k3s` is Healthy: cert-manager creates the `cert-manager` namespace and the ClusterIssuer CRD. See the rebuild order in [argocd.md](argocd.md#rebuild-order).
> NOTE: The issuer has `argocd.argoproj.io/sync-options: Prune=false`, so Argo CD never deletes it. cert-manager owns its ACME account key Secret (`bmosan-cert` in `cert-manager`); Argo CD doesn't track it.

3. Verify that the issuer has successfully been created
```bash
kubectl get clusterissuers
```
- You should see someting like
```bash
NAME          READY   AGE
bmosan-cert   True    46h
```

4. You can also validate that the certificate has been created / view all certs in your cluster with the below command:
```bash
kubectl get certificate --all-namespaces
```
