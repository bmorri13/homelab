# Cert Manager
- Deploying Cert Manager to configure valid certs within the enviornment
## Setup
1. Deploy helm chart
```bash
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.17.1 \
  --set crds.enabled=true
```
2. Deploy out the `prod_cert_issuer.yaml` from the cert_manager directory
```bash
kubectl apply -f prod_cert_issuer.yaml
```

3. Verify that the issuer has successfully been created
```bash
kubectl get clusterissuers
```
- You should see someting like
```bash
NAME          READY   AGE
bmosan-cert   True    46h
```

4. Deploy out a test deployment chart, we can use the `nginx_deployment.yaml`
```bash
kubectl apply -f nginx_deployment.yaml
```

5. Test going to your site (e.g. https://nginx.bmosan.com)
- You should be able to get to the site and then validat that the cert is valid and issued by Let's Encrypt

6. You can also validate that the certificate has been created / view all certs in your cluster with the below command:
```bash
kubectl get certificate --all-namespaces
```
