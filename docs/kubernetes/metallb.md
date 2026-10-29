# Metal LB
- Deploying the MetalLb Configuraiton via ArgoCD
## Setup
1. Create the `metallb-system` namespace
```bash
kubectl create ns metallb-system
```

2. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: metallb
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/homelab
        - Revision: HEAD
        - Path: infrastructure_tooling/metallb
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: metallb-system

3. You can now configure your services to get assigned an EXTERNAL-IP when using the `LoadBalancer` type. An exmaple of this with ArgoCD is below:
```bash
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "LoadBalancer"}}'
```

4. You can now access the ArgoCD front end via the EXTERNAL-IP, get that by running
```
 kubectl get svc argocd-server -n argocd
NAME            TYPE           CLUSTER-IP     EXTERNAL-IP     PORT(S)                      AGE
argocd-server   LoadBalancer   10.43.45.220   192.168.3.101   80:30766/TCP,443:32660/TCP   2d
```
