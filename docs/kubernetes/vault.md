# Vault
- In order to manage kubernetes secrets, we will deploy [Vault](https://developer.hashicorp.com/vault/docs/platform/k8s/helm)
>NOTE: This is just one example of a password manager, you can use local secrets, or other soultions such as AWS Secrets manager, GCP Secrets Manager, etc.

## Setup
1. Using kubectl, create a new namespace for vault
```bash
kubectl create ns vault
```

2. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: vault
        - Project Name: default
    - Source
        - Change the `Git` dropdown to `Helm`
        - Repository URL: https://helm.releases.hashicorp.com
        - Chart: vault
        - Version: 0.34.1
            - >NOTE: There may be a newer version when you view this
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: vault

3. Get the default vault key's
```bash
kubectl exec vault-0 -n vault -- vault operator init -key-shares=1 -key-threshold=1 -format=json > vault-keys.json
```

> NOTE: If you simply wish to port forward, you can run `kubectl port-forward svc/vault -n vault 8020:8200` and skip steps 2, 3 & 4 and simply go to `localhost:8020`

4. Reach the UI without exposing the Service. It stays a `ClusterIP`: a NodePort would serve Vault over plain HTTP to the whole LAN, around the tailnet-only ingress.
    - Before the ingress exists, port-forward: `kubectl port-forward svc/vault -n vault 8020:8200`, then open `http://localhost:8020`.
    - Afterwards, use `https://vault.bmosan.com` from a tailnet device ([`vault_ingress`](../../infrastructure_tooling/vault_ingress/README.md)).

5. Check the Service type:
```bash
kubectl get svc vault -n vault   # TYPE must be ClusterIP
```

6. If an older install left it as a NodePort, put it back:
```bash
kubectl patch svc vault -n vault --type merge -p '{"spec": {"type": "ClusterIP"}}'
```

7. Unseal your Vault by going to your `vault-keys.json` and copying the valye for `unseal_keys_b64`

8. To login, use the `Token` method and from your `vault-keys.json` copy the vaule from `root_token`

9. Create a new Secret Engine to store your first secret by going to. `Secrets Engine` > `Enable new engine` > `KV` > Click `Enable engine`

10. Create our first secret by going to `Create Secret` and entering someting similar to the values below
    - Path for the secret: cloudflare
    - Secret data:
        - Key: token
        - Value: <enter your secret value here>

- You now have a local secrets store setup on your cluster and are ready to set up a GitOps tie in to leveraging these secrets in your Cluster

## Upgrades

The Vault Application and the upgrade procedure (backup, restart into the new version, unseal) are in [`infrastructure_tooling/vault`](../../infrastructure_tooling/vault/README.md).
