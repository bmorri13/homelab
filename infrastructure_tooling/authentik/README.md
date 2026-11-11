# Authentik

`https://auth.bmosan.com`: the homelab's identity provider, on k3s-01 and reachable only from the tailnet. It gives Splunk Web and the Cribl UI one login with a required second factor:

| App | How | Why |
|---|---|---|
| Splunk Web | SAML (Splunk's own SSO) | The Developer license includes `SAMLAuth`. |
| Cribl UI | Traefik forward auth in front of the Leader (`cribl.bmosan.com`) | Cribl's Free license has no external login (`remote_auth: 0`), so Authentik guards the route and Cribl's own login still follows. |

This chart deploys Authentik and the routes for both apps.

## What's here

- **Upstream chart** `authentik` 2026.8.5, image pinned by digest. One server and one worker, run as non-root under the `restricted` Pod Security level.
- **PostgreSQL** (`templates/postgres.yaml`): the official image, one replica, 5 Gi on local-path. A NetworkPolicy lets only Authentik's pods reach it. The upstream chart's PostgreSQL option is the Bitnami chart, which no longer has free images.
- **Database backup** (`templates/postgres-backup.yaml`, off until `postgres.backup.enabled`): a nightly `pg_dump` CronJob. See [Backup and restore](#backup-and-restore).
- **No Redis.** Authentik dropped it in 2025.10.
- **No Kubernetes API access** (`serviceAccount.create: false`). The Cribl forward auth uses the outpost embedded in the server, so Authentik never deploys outposts of its own.
- **Blueprints** (`blueprints/*.yaml`, mounted from a ConfigMap): Authentik configuration kept in Git. The worker applies each one when it changes and re-applies it every hour, so UI edits to these objects revert.
  - `session.yaml`: a login lasts 5 hours from sign-in (Authentik's default ends it when the browser closes), so one password and second factor cover Splunk Web and the Cribl UI gate for that time. The Cribl gate's token is 5 hours too (`cribl.yaml`). Cribl's own login still follows: its Free license can't take Authentik's identity.
  - `require-mfa.yaml`: the default login flow's MFA stage requires TOTP or WebAuthn, and makes a user without either enrol one before the login completes.
  - `splunk.yaml`: the Splunk SAML provider and application, its `role`, `mail` and `realName` attributes, and the entitlement `splunk-admin` for `authentik Admins` (Splunk's side: `monitoring_stack/splunk/apps/00_homelab_auth`).
  - `cribl.yaml`: a forward-auth proxy provider for `cribl.bmosan.com`, the Cribl application (open to `authentik Admins` only), and the provider bound to the embedded outpost.
- **Ingress**: Traefik, cert-manager DNS-01, `tailnet-only` middleware (403 to anything outside the tailnet ranges).
- **Splunk Web** (`templates/splunk.yaml`): `https://splunk.bmosan.com`, tailnet-only, to Splunk Web at `https://192.168.4.11:8000` (homelab-CA cert, name `splunk-homelab`). No forward auth: Splunk itself sends the login here.
- **Cribl UI** (`templates/cribl.yaml`): `https://cribl.bmosan.com`, tailnet-only. Traefik sends each request to the embedded outpost (`authentik-forward-auth` middleware) and, once Authentik allows it, on to the Leader at `https://192.168.4.11:9000`, a Service without a selector plus its EndpointSlice. Traefik checks the Leader's cert against the homelab CA (`files/homelab-ca.crt`) for the name `cribl-homelab`. `/outpost.goauthentik.io` on the same host goes to Authentik without forward auth, for the login start and callback. Traefik runs on the host network and can't resolve cluster DNS names, so the outpost is reached through the Service `authentik-forward-auth` at a fixed `10.43.200.11`. The Leader's firewall (`monitoring_stack/splunk/scripts/mgmt-firewall.sh`) accepts port 9000 only from k3s-01.

## Outside Git

- **Vault `kv/authentik`**: `secret_key`, `postgres_password`, `bootstrap_password`. Generated inside the Vault pod so no value is printed or leaves Vault:

  ```bash
  kubectl -n vault exec -i vault-0 -- sh -c 'export VAULT_TOKEN="$(cat)"; r() { head -c "$1" /dev/urandom | base64 | tr -d "\n/+="; }; vault kv put kv/authentik secret_key="$(r 60)" postgres_password="$(r 32)" bootstrap_password="$(r 24)" >/dev/null && vault kv metadata get kv/authentik | grep -E "^(current_version|created_time)"' \
    < <(kubectl -n default get secret vault-token -o jsonpath='{.data.token}' | base64 -d)
  ```

  `secret_key` must never change after the first start: it signs sessions and derives IDs. `bootstrap_password` is read only on the very first start, as the password of the built-in `akadmin` user. Read it in the Vault UI to log in the first time.

  `postgres_password` can't simply be changed in Vault either. The database container reads it only when it creates an empty data directory; after that the password lives in the database. Authentik's server and worker read the Secret when they start, so a new value in Vault reaches the Secret within an hour (`refreshInterval`) and the next restart of either pod sends a password the database never adopted: the login for Splunk and Cribl breaks. To rotate it, change the database in the same few minutes as Vault, before any Authentik pod restarts:

  1. Write the new value, again without printing it:

     ```bash
     kubectl -n vault exec -i vault-0 -- sh -c 'export VAULT_TOKEN="$(cat)"; vault kv patch kv/authentik postgres_password="$(head -c 32 /dev/urandom | base64 | tr -d "\n/+=")" >/dev/null && vault kv metadata get kv/authentik | grep -E "^current_version"' \
       < <(kubectl -n default get secret vault-token -o jsonpath='{.data.token}' | base64 -d)
     ```
  2. Sync the Secret now and check it took (`SecretSynced`, a new refresh time):

     ```bash
     kubectl -n authentik annotate externalsecret authentik-credentials force-sync="$(date +%s)" --overwrite
     kubectl -n authentik get externalsecret authentik-credentials
     ```
  3. Set the database password from the Secret. The value goes through stdin and an environment variable, never a command line, and `log_min_error_statement` keeps the statement out of the database log if it fails. Local connections in the official image need no password, so this works whatever the database currently expects:

     ```bash
     kubectl -n authentik get secret authentik-credentials -o jsonpath='{.data.postgres_password}' | base64 -d \
       | kubectl -n authentik exec -i authentik-postgres-0 -- sh -c 'export NEW_PW="$(cat)"; printf "%s\n" "\\getenv pw NEW_PW" "SET log_min_error_statement TO panic;" "ALTER USER authentik WITH PASSWORD :'\''pw'\'';" | psql -v ON_ERROR_STOP=1 -q -U authentik -d authentik'
     ```
     From here until step 4 finishes, Authentik's new database connections fail.
  4. Restart Authentik onto the new password and wait:

     ```bash
     kubectl -n authentik rollout restart deploy/authentik-server deploy/authentik-worker
     kubectl -n authentik rollout status deploy/authentik-server && kubectl -n authentik rollout status deploy/authentik-worker
     ```
  5. Log in to Splunk Web through Authentik.

  If step 3 fails, the database still has the old password: put the old value back with `vault kv rollback -version=<previous> kv/authentik` and repeat step 2 before anything restarts. If Vault was changed by accident and Authentik already can't log in to its database, steps 3 and 4 fix it. The backup CronJob reads the Secret on each run, so it follows on its own.
- **Cloudflare DNS**: `A auth.bmosan.com`, `A cribl.bmosan.com` and `A splunk.bmosan.com → 100.124.194.87` (k3s-01's tailnet IP), DNS-only, like `vault`, `mcp` and `apiary`.
- **SAML signing keypair** `splunk-saml-signing`: a self-signed RSA 4096 certificate, valid 100 years, generated inside Authentik so the private key never left it. Its public cert is in `monitoring_stack/splunk/auth/authentik-saml.crt`. It has to be self-signed: Authentik's own `CertificateBuilder` (and the UI's Generate button) issue certificates from Authentik's internal CA, which Splunk rejects ("unable to get local issuer certificate", logged as "failed to verify signature with cert"). To regenerate it:

  ```bash
  kubectl -n authentik exec -i deploy/authentik-worker -- ak shell -c "
  import datetime
  from cryptography import x509
  from cryptography.x509.oid import NameOID
  from cryptography.hazmat.primitives import hashes, serialization
  from cryptography.hazmat.primitives.asymmetric import rsa
  from authentik.crypto.models import CertificateKeyPair
  key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
  name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'splunk-saml-signing'), x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'bmosan homelab')])
  now = datetime.datetime.now(datetime.timezone.utc)
  cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
      .not_valid_after(now + datetime.timedelta(days=36500))
      .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
      .sign(key, hashes.SHA256()))
  kp, _ = CertificateKeyPair.objects.get_or_create(name='splunk-saml-signing')
  kp.certificate_data = cert.public_bytes(serialization.Encoding.PEM).decode()
  kp.key_data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
  kp.save()
  print(kp.certificate_data)"
  ```

  Then commit the printed certificate as `auth/authentik-saml.crt` and redeploy the Splunk stack.
- **Argo CD Application**: `kubectl apply -f infrastructure_tooling/authentik/application.yaml`. Argo CD doesn't manage the Application itself, so re-apply it after changing that file.

## First login

1. Log in at `https://auth.bmosan.com` as `akadmin` with `bootstrap_password`.
2. Authentik asks you to enrol a second factor (TOTP app or passkey) before the login completes, as `require-mfa.yaml` requires.
3. Create your own user, add it to the `authentik Admins` group, log in as that user (enrolling its own second factor), then deactivate `akadmin`.

## Break-glass

`akadmin` is deactivated. If you're locked out, this prints a one-time admin login link for it (valid one year; treat it like a password):

```bash
kubectl -n authentik exec deploy/authentik-worker -- ak create_recovery_key 1 akadmin
```

## Backup and restore

The database (PVC `data-authentik-postgres-0`, local-path on k3s-01) holds everything that isn't a blueprint: users with their password hashes and MFA devices (TOTP secrets, passkeys), group membership, sessions, Authentik's event log, and the only copy of the `splunk-saml-signing` private key. Losing the volume or the node loses all of it.

### What a rebuild without a dump costs

The blueprints bring back the session length, the MFA requirement, the Cribl provider and application, and the Splunk provider, application and entitlement. Users, MFA enrolments and the signing key do not come back:

1. Let the database start empty. Argo CD never deletes the old PVC (`Prune=false,Delete=false`), so delete a broken one by hand: `kubectl -n authentik delete pvc data-authentik-postgres-0 && kubectl -n authentik delete pod authentik-postgres-0`. It initialises with the current `postgres_password`, so Authentik can connect, and Authentik treats it as a first start: `akadmin` is active again with `bootstrap_password`.
2. Generate a new `splunk-saml-signing` keypair (the command under [Outside Git](#outside-git)), then `kubectl -n authentik rollout restart deploy/authentik-worker` so `splunk.yaml` binds it now rather than within the hour.
3. Commit the printed cert as `monitoring_stack/splunk/auth/authentik-saml.crt` and redeploy the Splunk stack, which installs it as Splunk's IdP cert (Splunk README, "Splunk Web login"). Until then Splunk rejects every SAML login.
4. Do [First login](#first-login) again: every user enrols a second factor again.

### The backup

`postgres.backup.enabled: true` adds a CronJob (02:45 UTC) that runs `pg_dump --format=custom` of the `authentik` database with the database's own image (`postgres.image`), so the client always matches the server and Renovate moves both. It reads the password from `authentik-credentials`, writes `authentik-<UTC time>.dump` (mode 600) under a temporary name and renames it once `pg_restore --list` can read it, keeps the newest `keep` (7), and exits non-zero on any failure. It runs as the image's `postgres` user with a read-only root filesystem and no capabilities, and its NetworkPolicy lets it reach only DNS and the database.

The dump holds the SAML signing key, password hashes and MFA secrets, so where it lands is the decision. It is enabled with `nfs`: the dumps go to `authentik/` (mode 700) on the Unraid `splunk` share, next to the Splunk config backups, and that share's export rules decide who else can read them (Splunk README, "Unraid NFS share"). `postgres.backup.destination`:

| Destination | Survives | Doesn't survive | Notes |
|---|---|---|---|
| `pvc` (default) | Database corruption, a bad upgrade, a mistake in the UI | Losing k3s-01 or its disk | A second local-path PVC, `authentik-postgres-backup`. No new dependencies. |
| `nfs` (`nfs.server`, `nfs.path`) | Losing k3s-01 | Losing the NAS | A PersistentVolume on an NFS export (the namespace's `restricted` Pod Security level forbids inline NFS volumes). k3s-01 mounts it, so the export has to admit k3s-01, and k3s-01 needs the NFS client. Anyone who can read the export can read the dumps: the Unraid `splunk` share is set up for exactly one client (Splunk README, "Unraid NFS share") because it already holds secrets, and exported `Public` it is readable from any host on the network. Give the dumps their own share limited to k3s-01 rather than widening that one. |

Encrypting the dump before it leaves the pod (`age` to a public key in values, the private key kept offline) would make `nfs` safe even on a share others can read. The Postgres image has no `age`, so it needs a second pinned image (`pg_dump` into a memory-backed `emptyDir`, then an `age` container writing the encrypted file); it is not built yet.

A failed run leaves Kubernetes Warning events (`BackoffLimitExceeded`, `DeadlineExceeded`, `FailedMount`, ...). They reach `index=k8s sourcetype=kube:events`, but no alert watches the `authentik` namespace yet: `85_homelab_ops` watches only the homelab agents' namespace.

After enabling it, run it once rather than waiting for the schedule (the new PVC stays `Pending` until a pod uses it):

```bash
kubectl -n authentik create job --from=cronjob/authentik-postgres-backup authentik-postgres-backup-manual
kubectl -n authentik wait --for=condition=complete job/authentik-postgres-backup-manual --timeout=10m
kubectl -n authentik logs job/authentik-postgres-backup-manual
kubectl -n authentik delete job authentik-postgres-backup-manual
```

A failed or missing backup is reported by the Splunk alert "Authentik - database backup failed or missing" (`85_homelab_ops`): it posts to Slack when no dump has been written for 26 hours.

### Test a restore

This restores the newest dump into a throwaway Postgres inside one pod on the backup volume, so the dump never leaves the cluster, and prints row counts. Compare them with the live database:

```bash
IMAGE=$(kubectl -n authentik get sts authentik-postgres -o jsonpath='{.spec.template.spec.containers[0].image}')
COUNTS="select 'users', count(*) from authentik_core_user union all select 'groups', count(*) from authentik_core_group union all select 'applications', count(*) from authentik_core_application union all select 'flows', count(*) from authentik_flows_flow union all select 'keypairs with a key', count(*) from authentik_crypto_certificatekeypair where key_data <> ''"

kubectl -n authentik apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: authentik-restore-test
spec:
  automountServiceAccountToken: false
  restartPolicy: Never
  securityContext: {runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: restore
      image: $IMAGE
      command: [bash, -c]
      args:
        - |
          set -euo pipefail
          initdb -D /scratch/pg -U postgres --auth=trust > /dev/null
          pg_ctl -D /scratch/pg -o "-c listen_addresses='' -k /scratch" -w start > /dev/null
          f=\$(ls -1t /backup/authentik-*.dump | head -1); echo "dump: \$f"
          createdb -h /scratch -U postgres authentik
          pg_restore -h /scratch -U postgres -d authentik --no-owner --no-privileges --exit-on-error "\$f"
          psql -h /scratch -U postgres -d authentik -At -F ': ' -c "\$COUNTS"
      env:
        - {name: COUNTS, value: "$COUNTS"}
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
      volumeMounts:
        - {name: backup, mountPath: /backup, readOnly: true}
        - {name: scratch, mountPath: /scratch}
  volumes:
    - name: backup
      persistentVolumeClaim: {claimName: authentik-postgres-backup}
    - name: scratch
      emptyDir: {sizeLimit: 2Gi}
EOF
kubectl -n authentik wait --for=jsonpath='{.status.phase}'=Succeeded pod/authentik-restore-test --timeout=5m
kubectl -n authentik logs authentik-restore-test
kubectl -n authentik delete pod authentik-restore-test

# The live counts, over the local socket:
kubectl -n authentik exec authentik-postgres-0 -- psql -U authentik -d authentik -At -F ': ' -c "$COUNTS"
```

The counts match, apart from anything changed since the dump, and `keypairs with a key` includes `splunk-saml-signing`. If the pod fails, `kubectl logs` shows where.

### Restore after losing the database

1. **Vault first.** `kv/authentik` must be intact: the dump only works with the same `secret_key`. If Vault was lost too, restore it first (`infrastructure_tooling/vault`).
2. **Get a dump back on a backup volume.** With `nfs` it is still there. With `pvc` and k3s-01 gone, the dumps are gone too: rebuild instead ([above](#what-a-rebuild-without-a-dump-costs)).
3. **Stop Argo CD from restarting Authentik**, then stop Authentik, so it doesn't migrate an empty database first:

   ```bash
   kubectl -n argocd patch application authentik --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
   kubectl -n authentik scale deploy/authentik-server deploy/authentik-worker --replicas=0
   ```
4. **Start an empty database** if the old one is unusable (step 1 of the rebuild above), and wait for `authentik-postgres-0` to be ready.
5. **Restore** the newest dump from a pod with the backup pod's label, so the database's NetworkPolicy admits it. `--clean` drops anything already there. For an older dump, put its file name in place of the `ls` line:

   ```bash
   IMAGE=$(kubectl -n authentik get sts authentik-postgres -o jsonpath='{.spec.template.spec.containers[0].image}')
   kubectl -n authentik apply -f - <<EOF
   apiVersion: v1
   kind: Pod
   metadata:
     name: authentik-restore
     labels: {app.kubernetes.io/name: authentik-postgres-backup}
   spec:
     automountServiceAccountToken: false
     restartPolicy: Never
     securityContext: {runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999, seccompProfile: {type: RuntimeDefault}}
     containers:
       - name: restore
         image: $IMAGE
         command: [bash, -c]
         args:
           - |
             set -euo pipefail
             f=\$(ls -1t /backup/authentik-*.dump | head -1); echo "restoring \$f"
             pg_restore -h authentik-postgres -U authentik -d authentik --clean --if-exists --no-owner --exit-on-error "\$f"
         env:
           - name: PGPASSWORD
             valueFrom:
               secretKeyRef: {name: authentik-credentials, key: postgres_password}
         securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
         volumeMounts:
           - {name: backup, mountPath: /backup, readOnly: true}
     volumes:
       - name: backup
         persistentVolumeClaim: {claimName: authentik-postgres-backup}
   EOF
   kubectl -n authentik wait --for=jsonpath='{.status.phase}'=Succeeded pod/authentik-restore --timeout=10m
   kubectl -n authentik logs authentik-restore
   kubectl -n authentik delete pod authentik-restore
   ```
6. **Start Authentik and Argo CD again:** `kubectl apply -f infrastructure_tooling/authentik/application.yaml` brings back automated sync, which scales both Deployments back up.
7. **Check:** log in at `https://auth.bmosan.com` with your own user and second factor, then to Splunk Web and the Cribl UI. The signing key came back with the dump, so Splunk needs no change.

## Logs

Authentik's pod logs reach Splunk through the k3s OTel collector (`k8s.namespace.name=authentik`). Alerts on failed logins and MFA failures come with the Splunk change.
