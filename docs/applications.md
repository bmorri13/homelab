# Applications
## Alex Printer tracker
- This is a sample app that I have created to track how long it is taking a buddy to build his printer with a nextjs app and as well test out Cloudflare Tunnels
- The repo for this is: [cloudflare_tunnels_helm](https://github.com/bmorri13/cloudflare_tunnels_helm)

### Setup
1. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: alexprinter
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/cloudflare_tunnels_helm
        - Revision: HEAD
        - Path: cloudflare-tunnels-helm
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: alexprinter

- You now have deployed a helm chart that is calling the secret that is stored in Vault successfully

## Uptime Kuma
- Uptime Kuma is an easy-to-use self-hosted monitoring tool that provides status pages, monitoring for HTTP(s), TCP, DNS, and more
- The helm chart deploys Uptime Kuma in the `uptime-kuma` namespace with a LoadBalancer service type that uses MetalLB for IP assignment

### Setup
1. Create the `uptime-kuma` namespace:
```bash
kubectl create ns uptime-kuma
```

2. Within the ArgoCD UI, create a new app with the below parameters:
    - General
        - Application Name: uptime-kuma
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/homelab
        - Revision: HEAD
        - Path: infrastructure_tooling/uptime_kuma
        - Helm parameter:
            - Release Name: uptime-kuma (make sure to use all lowercase with hyphens, not spaces)
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: uptime-kuma

3. Once deployed, you can access Uptime Kuma using the dynamically assigned IP address provided by MetalLB:
```bash
kubectl get svc -n uptime-kuma
```

4. Navigate to the assigned IP address on port 3001 in your browser:
```
http://<EXTERNAL-IP>:3001
```

5. Follow the initial setup wizard to create your admin user and start monitoring your services.

## Stirling PDF
- Stirling PDF is a locally hosted web application that allows you to perform various operations on PDF files, such as merging, splitting, converting, and more.
- The helm chart deploys Stirling PDF in the `stirling-pdf` namespace.

### Setup
1. Create the `stirling-pdf` namespace:
```bash
kubectl create ns stirling-pdf
```

2. Within the ArgoCD UI, create a new app with the below parameters:
    - General
        - Application Name: `stirling-pdf`
        - Project Name: `default`
    - Source
        - Leave the `Git` dropdown
        - Repository URL: `https://github.com/bmorri13/homelab`
        - Revision: `HEAD`
        - Path: `infrastructure_tooling/stirling_pdf`
        - Cluster URL: `https://kubernetes.default.svc`
        - Namespace: `stirling-pdf`

3. The Ingress is part of the chart ([templates/ingress.yaml](../infrastructure_tooling/stirling_pdf/templates/ingress.yaml)), so ArgoCD deploys it with the app — no separate manifest to apply by hand.
    - You should be able to access it at `https://stirling-pdf.bmosan.com`
    - The Cloudflare record is an A record pointing at the k3s-01 node IP (DNS-only, not proxied), so this resolves from the home network only. The TLS cert is issued over DNS-01, which does not require public reachability.
