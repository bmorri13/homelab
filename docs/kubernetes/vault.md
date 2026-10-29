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
        - Chart: Vault
        - Version: 0.29.1
            - >NOTE: There may be a newer version when you view this
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: vault

3. Get the default vault key's
```bash
kubectl exec vault-0 -n vault -- vault operator init -key-shares=1 -key-threshold=1 -format=json > vault-keys.json
```

> NOTE: If you simply wish to port forward, you can run `kubectl port-forward svc/vault -n vault 8020:8200` and skip steps 2, 3 & 4 and simply go to `localhost:8020`

4. Update `vault` service to NodePort
- This will allow us to access the UI from our network. 
> NOTE: This will be replaces in the future once I setup a reverse proxy
```bash
kubectl patch svc vault -n vault -p '{"spec": {"type": "NodePort"}}'
```

5. Get the port for your ArgoCD UI frontend
    - It will be the first port that is connected to port 80
```bash
kubectl get svc vault -n vault
```
- Should look someting like:
```bash
NAME    TYPE       CLUSTER-IP     EXTERNAL-IP   PORT(S)                         AGE
vault   NodePort   10.43.123.36   <none>        8200:31080/TCP,8201:31973/TCP   16m
```

6. On your local computer, you can enter in the IP address or local DNS name and then the port
```bash
http://192.168.3.170:31080/
http://bmo-nuc.com:31080/
```
> NOTE: If you are running this in the cloud, this will be the IP of your instance. You may need to update securiy groups to include allowing access to the port

7. Unseal your Vault by going to your `vault-keys.json` and copying the valye for `unseal_keys_b64`

8. To login, use the `Token` method and from your `vault-keys.json` copy the vaule from `root_token`

9. Create a new Secret Engine to store your first secret by going to. `Secrets Engine` > `Enable new engine` > `KV` > Click `Enable engine`

10. Create our first secret by going to `Create Secret` and entering someting similar to the values below
    - Path for the secret: cloudflare
    - Secret data:
        - Key: token
        - Value: <enter your secret value here>

- You now have a local secrets store setup on your cluster and are ready to set up a GitOps tie in to leveraging these secrets in your Cluster
