# ArgoCD
- In order to manage deployments in a GitOps workflow metholdology, I will be using ArgoCD as my CD tool to deploy out my subsequent applications

## Setup
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
- Username: amdin
- Password: outputted results from Step 5

- You are now ready to install your first apps!

## ArgoCD server configuration (`argocd-cm`)

ArgoCD's own settings live in [`infrastructure_tooling/argocd_config`](../../infrastructure_tooling/argocd_config), deployed as the `argocd-config` app. Unlike every other app here, its Application manifest **is** committed — losing it would silently reintroduce the problems it prevents. Bootstrap it once:

```bash
kubectl apply -f infrastructure_tooling/argocd_config/application.yaml
```

That chart is the source of truth for `argocd-cm`. Settings added through the ArgoCD UI that are not in the chart get reverted on the next sync — put them in the chart instead. It currently configures two things:

| Setting | Why |
|---------|-----|
| `resource.exclusions` for `AutoscalingListener`, `EphemeralRunner`, `EphemeralRunnerSet` | The ARC controller stamps `app.kubernetes.io/instance` onto the runtime objects it creates. ArgoCD reads that as its own tracking label, sees resources absent from Git, and prunes them; the controller recreates them, looping every reconcile. `AutoscalingRunnerSet` is deliberately **not** excluded — that one does come from Git. |
| Ingress health override | Traefik runs hostNetwork with its Service disabled and there is no MetalLB, so `status.loadBalancer` never populates and ArgoCD's built-in check would leave every Ingress-owning app stuck at `Progressing`. |

> **The ARC runner apps must keep `prune: false`.** The exclusions above cover the `actions.github.com` kinds, but the ARC controller also creates a **Role and RoleBinding** per listener, which no `apiGroups`-based exclusion can cover without excluding all RBAC. The controller is therefore run with `flags.excludeLabelPropagationPrefixes: [app.kubernetes.io/instance]` (set on the `arc-controller-k3s` Application, which is not in Git) so it stops copying ArgoCD's tracking label onto them. Keep prune off anyway: if that flag is ever lost, prune would delete the listener RBAC and loop. With the flag in place, `arc-runner-scaleset-k3s` and `arc-runner-cointhieves-k3s` should both show `Synced / Healthy`.
