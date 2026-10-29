# Kubernetes audit detections (exec, RBAC, secrets, tokens, hand-made workloads, denied requests)

Source: `index=audit` k3s API audit. Platform controllers' reads aren't audited, so an audited secret read is a person or an application.

Who is who:
- `system:admin` from 127.0.0.1 with a kubectl user agent: the owner (or an agent they run) on k3s-01 over SSH. Check `index=infra eventtype=homelab_tailscale_ssh_command host=k3s-01` around the same time for the session and commands.
- `system:serviceaccount:argocd:*`: Argo CD syncing; correlate with Argo CD application events.
- `system:serviceaccount:toolhive-clients:agent-runner`: the agent runner. Its own token mints are excluded from the detection; anything else it does is unexpected.
- `system:serviceaccount:toolhive-mcp:homelab-vmcp`: the MCP gateway; about ten 403s a week listing configmaps is a known harmless RBAC gap.
- 401s carry no username (anonymous auth is off); group them by sourceIPs{}.

Check:
- `index=audit user.username="<user>" earliest=-1h@h latest=+1h | stats count values(verb) values(objectRef.resource) by objectRef.namespace` to see the whole session.
- For exec: which pod, what command (requestURI has `command=`), and whether that matches maintenance the owner was doing.
- For RBAC changes and hand-made workloads: was it later applied by Argo CD (same object, argocd user), or left behind? A privileged pod, hostPath mount or new cluster-admin binding by anything but system:admin is serious.
- For token mints: which service account; a token for a gateway client (toolhive-clients) by system:admin is the owner minting a client token.
