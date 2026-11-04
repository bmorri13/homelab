# AdGuard Home

A DNS resolver for the LAN on k3s-01, there to log every query to Splunk: which device looked up which name. It starts as a pure logging resolver. Every query goes on to the UCG-Max, the same server clients use today, and nothing is blocked, so answers don't change.

| | |
|---|---|
| Address | `192.168.4.98:53` UDP and TCP (k3s-01's LAN address) |
| Upstream | `192.168.4.1` (the UCG-Max), for names and for reverse lookups of LAN addresses |
| Blocking | Off (`filtering_enabled: false`, no filters) |
| Query log | `index=network sourcetype=adguard:querylog` |
| Web UI | `kubectl -n adguard port-forward deploy/adguard 3000`, then `http://localhost:3000` |

## How it runs

- `manifests/adguard.yaml`: one pod (image pinned by digest) under the `restricted` Pod Security level: non-root, all capabilities dropped except `NET_BIND_SERVICE` (the binary carries it as a file capability and won't exec without it; it still listens on 5353, not 53). Query log and statistics on a 2 Gi local-path volume.
- `manifests/AdGuardHome.yaml`: the whole config, kept in Git. Kustomize turns it into a ConfigMap whose name changes with its content, so editing it rolls the pod. The init container copies it over the live file on every start, so changes made in the web UI revert at the next restart.
- The Service `adguard-dns` puts port 53 on `192.168.4.98` (`externalIPs`) and kube-proxy forwards it to the pod. `externalTrafficPolicy: Local` keeps each client's own address, so the log shows which device asked.
- The NetworkPolicy lets only DNS in. The web UI has no users, so it is reached only with `kubectl port-forward`, which doesn't pass through the policy.
- The `querylog` sidecar tails `querylog.json` to stdout (AdGuard writes its query log only to a file). The k3s OTel collector picks it up, and Cribl's `hec_in` pipeline sends it to `index=network` as `adguard:querylog`. `monitoring_stack/splunk/apps/85_adguard` names the fields for the CIM Network_Resolution model: `src` (client), `query`, `record_type`, `dest` (upstream), `duration`, `action`.

## Failover: nothing breaks if AdGuard is down

DHCP hands out two DNS servers: AdGuard first, the gateway second. A client that gets no answer from AdGuard asks the gateway, which is what every client uses today.

- Linux with systemd-resolved (the VMs here): moves to the next server when the current one stops answering, and stays there until that one fails. So after an AdGuard outage those hosts keep using the gateway: DNS works, but their queries are missing from the log until systemd-resolved restarts (`systemctl restart systemd-resolved`, or a reboot).
- How fast the fallback is depends on what broke. Tested on 2026-11-04 by scaling the pod to zero: kube-proxy then rejects port 53 on 192.168.4.98 outright, and the VMs switched to the gateway at once (lookups took 0.01 s). If the whole k3s-01 VM is down nothing answers at all, so a client waits for its resolver timeout (a few seconds) once before switching.
- Windows and macOS: retry the second server after about a second without an answer.
- k3s-01 runs AdGuard and also gets it from DHCP. If k3s or the pod is down, k3s-01 itself falls back to the gateway like any other host.
- *HL - Log source stopped sending* reports the AdGuard query log going quiet for 30 minutes, which is what a fallback looks like.

Keep UniFi's own **Ad Blocking** off on networks that use AdGuard: with it on, the gateway answers DNS itself, including queries sent to other servers, and AdGuard would see almost nothing.

## Rollout

In UniFi Network, edit the network's DHCP settings (or with the UniFi API) and set:

- DNS Server: Manual. `192.168.4.98`, then `192.168.4.1`.

Clients get the new servers at their next DHCP renewal (lease 24 h), or right away with `networkctl renew eth0`. On the VMs that isn't enough to move them: systemd-resolved keeps using the gateway while it still answers, because it is still in the list. Restart it so it starts from the first server: `networkctl renew eth0 && systemctl restart systemd-resolved`. Check a client with `resolvectl status` (Current DNS Server: 192.168.4.98), then its queries in Splunk:

```
index=network sourcetype=adguard:querylog src=<client IP> | head
```

Widen to other networks once it has run cleanly for a while.

**Rollback:** set that network's DNS back to Auto. Clients return to the gateway at their next renewal, or `networkctl renew eth0 && systemctl restart systemd-resolved`.

Only roll it out to networks whose firewall policy lets clients reach the server LAN on port 53: elsewhere AdGuard can never answer, and every lookup waits for the fallback.

## Outside Git

- **Argo CD Application**, applied once: `kubectl apply -f infrastructure_tooling/adguard/application.yaml`.
- **The DHCP change** above, made in UniFi.
