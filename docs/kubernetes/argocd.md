# ArgoCD
- In order to manage deployments in a GitOps workflow metholdology, I will be using ArgoCD as my CD tool to deploy out my subsequent applications

## Setup (original install)
- Following the [ArgoCD Getting Starting Guide](https://argo-cd.readthedocs.io/en/stable/getting_started/), we will deploy ArgoCD
> NOTE: I am only including the steps that I have run that are a subset of the steps on the guide (e.g. not installing argocd cli, etc.)

1. Install ArgoCD
```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```
> NOTE: If you simply wish to port forward, you can run `kubectl port-forward svc/argocd-server -n argocd 8080:443` and skip steps 2, 3 & 4 and simply go to `localhost:8080`

2. Update `argocd-server` service to NodePort
- This will allow us to access the UI from our network. 
> NOTE: This will be replaces in the future once I setup a reverse proxy
```bash
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "NodePort"}}'
```

3. Get the port for your ArgoCD UI frontend
    - It will be the first port that is connected to port 80
```bash
kubectl get svc argocd-server -n argocd
```
- Should look someting like:
```bash
NAME            TYPE       CLUSTER-IP      EXTERNAL-IP   PORT(S)                      AGE
argocd-server   NodePort   10.43.127.142   <none>        80:30447/TCP,443:31836/TCP   64s
```

4. On your local computer, you can enter in the IP address or local DNS name and then the port
```bash
https://192.168.3.170:30447/
https://bmo-nuc.com:30447/
```
> NOTE: If you are running this in the cloud, this will be the IP of your instance. You may need to update securiy groups to include allowing access to the port

5. Get the default ArgoCD admin password
```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

6. Login to the UI
- Username: admin
- Password: outputted results from Step 5

- You are now ready to install your first apps!

## The install itself

The running install is captured in [`infrastructure_tooling/argocd`](../../infrastructure_tooling/argocd): the upstream manifests pinned to a release, with this cluster's changes. Upgrades are made there and applied by hand; see its README. The steps above are how it was first installed.

## ArgoCD server configuration (`argocd-cm`)

ArgoCD's own settings live in [`infrastructure_tooling/argocd_config`](../../infrastructure_tooling/argocd_config), deployed as the `argocd-config` app. Its Application manifest is committed, because losing it would silently reintroduce the problems it prevents. Bootstrap it once:

```bash
kubectl apply -f infrastructure_tooling/argocd_config/application.yaml
```

That chart is the source of truth for `argocd-cm`. Settings added through the ArgoCD UI that are not in the chart get reverted on the next sync — put them in the chart instead. It currently configures:

| Setting | Why |
|---------|-----|
| `resource.exclusions` for `AutoscalingListener`, `EphemeralRunner`, `EphemeralRunnerSet` | The ARC controller stamps `app.kubernetes.io/instance` onto the runtime objects it creates. ArgoCD reads that as its own tracking label, sees resources absent from Git, and prunes them; the controller recreates them, looping every reconcile. `AutoscalingRunnerSet` is deliberately **not** excluded — that one does come from Git. |
| Ingress health override | Traefik runs hostNetwork with its Service disabled and there is no MetalLB, so `status.loadBalancer` never populates and ArgoCD's built-in check would leave every Ingress-owning app stuck at `Progressing`. |
| Argo CD 3.x default `resource.exclusions` and `ignoreResourceUpdates`, minus `Endpoints` and `EndpointSlice` | Upstream ships these in its own `argocd-cm`, which isn't applied here. `Endpoints` and `EndpointSlice` are left out because the `authentik` app manages two `EndpointSlice`s from Git (for Services without a selector), and an excluded kind can't be synced. |

> **The ARC runner apps must keep `prune: false`.** The exclusions above cover the `actions.github.com` kinds, but the ARC controller also creates a **Role and RoleBinding** per listener, which no `apiGroups`-based exclusion can cover without excluding all RBAC. The controller is therefore run with `flags.excludeLabelPropagationPrefixes: [app.kubernetes.io/instance]` (set on the `arc-controller-k3s` Application, which is not in Git; its settings are recorded in [arc.md](arc.md#on-k3s-01)) so it stops copying ArgoCD's tracking label onto them. Keep prune off anyway: if that flag is ever lost, prune would delete the listener RBAC and loop. With the flag in place, `arc-runner-scaleset-k3s` and `arc-runner-cointhieves-k3s` should both show `Synced / Healthy`.

## Rebuild order

There is no app-of-apps: every Application is a committed `application.yaml`, applied once by hand, except `arc-controller-k3s` (created in the UI; its settings are in [arc.md](arc.md#on-k3s-01)). Apps without automated sync must also be synced in Argo CD after applying. To rebuild k3s-01, in this order:

1. Argo CD itself: [`infrastructure_tooling/argocd`](../../infrastructure_tooling/argocd) (see its README), then `kubectl apply -f infrastructure_tooling/argocd_config/application.yaml`.
2. `kubectl apply -f infrastructure_tooling/cert_manager/application.yaml`, and wait for `cert-manager-k3s` to be Healthy. It installs the cert-manager CRDs and namespace the issuer needs.
3. `kubectl apply -f infrastructure_tooling/vault/application.yaml`, sync, then initialise and unseal Vault and set up its Kubernetes auth ([`eso_cluster_provider_helm/README.md`](../../infrastructure_tooling/eso_cluster_provider_helm/README.md)).
4. `kubectl apply -f infrastructure_tooling/external_secrets_operator/application.yaml`, and sync.
5. `kubectl apply -f infrastructure_tooling/eso_cluster_provider_helm/application.yaml`, and sync. The `vault-backend` store should report `Valid`.
6. `kubectl create ns alexprinter` (the app doesn't create it), then `kubectl apply -f infrastructure_tooling/eso_external_secrets_helm/application.yaml`, and sync. This creates the `cf-api-token` Secret and the `bmosan-cert` ClusterIssuer; check `kubectl get clusterissuer bmosan-cert` is `Ready` before relying on any Ingress certificate.
7. Recreate `arc-controller-k3s` in the UI with the settings in [arc.md](arc.md#on-k3s-01), then `kubectl apply -f` the `application.yaml` in `infrastructure_tooling/arc_runner_scaleset` and `infrastructure_tooling/arc_runner_scaleset_cointhieves`.
8. The rest: `kubectl apply -f` each remaining `infrastructure_tooling/*/application.yaml`.
