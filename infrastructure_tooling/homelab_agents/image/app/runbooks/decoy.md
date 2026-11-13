# Decoy (honeytoken) touched

A Kubernetes secret and a Vault secret exist only as bait: no application, ExternalSecret or person uses them. Their names are deliberately kept out of the repo and these runbooks (they are in the detection's macro override on the Splunk host), so the finding's `object` is the only place you will see them; don't repeat them anywhere outside the triage.

Any request, allowed or denied, is someone (or something running as someone) looking for credentials. Treat it as a real incident unless the owner was testing the decoy or creating it.

Check: who (`user`; for Vault `auth.display_name` and policies, for Kubernetes `user.username`), from where (`src`), which verbs or operations, and whether it succeeded (`codes`, `errors`). Then what else that identity and source did in the same hour: other secrets read (`HL - Kubernetes secrets read`, `HL - Vault secret read by a new identity`), Tailscale SSH sessions and commands on the host it came from, new pods or tokens. A service account touching the decoy means its pod or token is compromised; name the pod.
