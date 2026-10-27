# EuroNodes — third Talos cluster

Runbook for the single-node Talos cluster on a [EuroNodes](https://euronodes.com) VPS (Portugal),
GitOps-managed by Flux like the other clusters. The decisions and their reasons are in the table below;
backups and restore are in [`backup-restore.md`](backup-restore.md), alerting in
[`monitoring.md`](monitoring.md), remote admin access in [`tailscale.md`](tailscale.md).

Talos files live in [`talos/euronodes/`](../talos/euronodes/): `patch.yaml` and the SOPS-encrypted
`secrets.sops.yaml`. **The generated `controlplane.yaml`, `worker.yaml`, `talosconfig` and
`kubeconfig` are never committed**: they embed the cluster CA keys and tokens. Regenerate them (below).

## What EuroNodes is

- KVM on Proxmox, custom ISO supported, hourly billing. The VM has a **public IPv4 directly on the
  NIC** (no NAT): `178.159.34.177/24` on `ens18`, gateway `178.159.34.1`. IPv6 is also present.
- One disk (`/dev/sda`, 161 GB); no detachable block storage. S3-compatible object storage exists
  (Ceph RGW; **requires signature V2**, see _Immich storage_).
- Plan used: 4 vCPU / 8 GB / 161 GB, dedicated IPv4.
- Hostname `euronodes` (set by Talos; the panel value is only a label).

## Decisions

| Topic | Decision |
| ----- | -------- |
| Topology | One node, control plane and worker |
| Disk | `EPHEMERAL` capped at 40 GB, `local-path-provisioner` user volume takes the rest; all volumes LUKS2 with the **node-ID key** |
| Storage engine | `local-path` (one node; replicated engines add nothing) |
| API exposure | Talos API (50000) and Kubernetes API (6443) are **closed to the internet** by the EuroNodes console firewall and reached over Tailscale only (see _Reaching the cluster_). Only 80 and 443 are open. |
| Ingress | Traefik terminates TLS on the node (hostPort 80/443); cert-manager, one Let's Encrypt certificate per host over HTTP-01 |
| Domain | `*.vps.powple.com` (hosts live in `apps/euronodes/*`; see _Changing the public domain_ before changing it) |
| Immich originals | `library` and `upload` on EuroNodes S3 through an rclone-crypt mount; everything else local |
| Backups | restic to a Hetzner Storage Box, one repository per app |

## Provision the VM

1. Order the VPS (Portugal, 4 vCPU / 8 GB, dedicated IPv4). Hostname `euronodes`.
2. Generate an Image Factory ISO at [factory.talos.dev](https://factory.talos.dev): bare-metal
   `amd64`, extension `iscsi-tools`. Keep the **schematic ID** (`c9078f94…8bac` for this cluster) and
   the Talos version (`v1.14.2`).
3. Attach the ISO and boot. The node is in maintenance mode: anyone can reach its API until a
   config is applied, so apply it promptly.
4. Read the disk and NIC: `talosctl get disks|links|addresses --insecure -n <ip> -e <ip>`.

## Talos

### Secrets and generated config

```sh
export SOPS_AGE_KEY_FILE=~/.config/sops/age/euronodes.agekey
export E=178.159.34.177 SCHEMATIC=c9078f9419961640c712a8bf2bb9174933dfcf1da383fd8ea2b7dc21493f8bac TALOS_VERSION=v1.14.2
# tailnet address and name of the Talos API Service (kubectl get service talos-api -n tailscale-operator)
TS_IP=<tailnet-ip> TS_NAME=<talos-api-euronodes.your-tailnet.ts.net>

# once, never for a live cluster
talosctl gen secrets -o talos/euronodes/secrets.yaml
sops -e talos/euronodes/secrets.yaml > talos/euronodes/secrets.sops.yaml && rm talos/euronodes/secrets.yaml

# any time (the secrets bundle keeps the PKI stable)
talosctl gen config --with-secrets <(sops -d talos/euronodes/secrets.sops.yaml) \
  --additional-sans $E,$TS_IP,$TS_NAME \
  --install-image factory.talos.dev/metal-installer/$SCHEMATIC:$TALOS_VERSION \
  --output-dir talos/euronodes euronodes https://$E:6443
```

### The patch

[`talos/euronodes/patch.yaml`](../talos/euronodes/patch.yaml) is a multi-document patch:

| Document | Purpose |
| -------- | ------- |
| `HostnameConfig`, `LinkConfig` (`ens18`), `ResolverConfig`, `TimeSyncConfig` | Hostname, static address and route, DNS, NTP |
| `KubeNodeConfig` | Removes the control-plane taint |
| `UnattendedInstallConfig` | Installs on `/dev/sda` |
| `VolumeConfig` `STATE` and `EPHEMERAL` (40 GB), `UserVolumeConfig` `local-path-provisioner` | Layout, each with `encryption: luks2` and `nodeID` key |
| `UserVolumeConfig` `rclone` (`volumeType: directory`) | A writable `/var/mnt/rclone` for the rclone FUSE mount (`/var/mnt` is read-only for the kubelet) |
| `RegistryMirrorConfig` `registry.k8s.io` | See _Google registry block_ |

Encryption can only be set when a volume is created; changing it later means wiping the volume.
The node-ID key derives from the VM UUID: it protects against a copied disk or snapshot, **not**
against the provider reading a running VM. If EuroNodes ever changes the VM UUID (clone or
provider-side restore) the volumes will not unlock; treat that as a new VM and restore from backup.
Do not write the UUID in the repo.

### Apply, bootstrap, kubeconfig

```sh
# first time: maintenance mode
talosctl apply-config -f talos/euronodes/controlplane.yaml --config-patch @talos/euronodes/patch.yaml \
  -n $E -e $E --insecure
# eject the ISO in the panel, then:
export TALOSCONFIG=talos/euronodes/talosconfig
talosctl config endpoint $E && talosctl config node $E
talosctl bootstrap && talosctl health
talosctl kubeconfig talos/euronodes/kubeconfig

# later changes (the patch is not stored in controlplane.yaml, pass it every time)
talosctl apply-config -f talos/euronodes/controlplane.yaml --config-patch @talos/euronodes/patch.yaml -n $E -e $E
```

Check `talosctl get volumestatus`: `STATE`, `EPHEMERAL` and `u-local-path-provisioner` must show
`encryptionProvider: luks2`. Rebooting must bring the node back with all volumes unlocked.

### Reaching the cluster

The EuroNodes console firewall allows inbound TCP **80 and 443** only; ports 50000 (Talos API) and 6443
(Kubernetes API) are closed to the internet. Tailscale needs no inbound rule (it connects out, and a direct
path forms through the VM's public address), so do not block outbound traffic or the replies to it.
Everything administrative goes through the tailnet:

```sh
# Kubernetes: adds a context to your kubeconfig that goes through the operator's proxy
tailscale configure kubeconfig tailscale-operator-euronodes
kubectl get nodes

# Talos: use the Talos API Service's MagicDNS name as the endpoint, never the public address
export TALOSCONFIG=$PWD/talos/euronodes/talosconfig
talosctl -e talos-api-euronodes.<your-tailnet>.ts.net -n 178.159.34.177 version
```

- **Always set `TALOSCONFIG`.** The default `~/.talos/config` may belong to another cluster; the symptom is
  `x509: certificate signed by unknown authority`.
- The endpoint's certificate must list the tailnet name and IP as SANs (the `--additional-sans` above); without
  them `talosctl` fails with `certificate is valid for …, not 100.x.x.x`.
- `talosctl` always talks to the Talos API on 50000; a time-out on the public address is the firewall doing its job.
- **If Tailscale ever breaks** (the operator is down, credentials expired, a bad ACL), the way back in is the
  EuroNodes web console: temporarily allow 50000 and 6443 in the console firewall and use the public address, then
  close them again. The `talosconfig` and `kubeconfig` generated for this cluster are git-ignored and live only on
  your laptop; keep them (or regenerate them with the commands above).
- A local DNS forwarder (for example `dnsmasq`) in front of Tailscale's DNS caused intermittent time-outs once;
  scope such a resolver to private domains only.

### Google registry block

`registry.k8s.io` (and its `europe-west4-docker.pkg.dev` backend) returns **403 to this VM's IP
range**, intermittently. The patch mirrors `registry.k8s.io` to the Artifact Registry backend, which
worked at install time but stopped working later (even `etcd` failed to pull). Consequences:
the control-plane images are cached on the node, but a **Kubernetes or Talos upgrade that pulls new
`registry.k8s.io` images may fail**; any chart using `registry.k8s.io/sig-storage` images
(CSI sidecars) cannot start. Raise it with EuroNodes, and plan a mirror (for example copying the
images to `ghcr.io`) before an upgrade.

Take a restic backup and an etcd snapshot before every Talos upgrade (single node, no fallback).
Check the Talos 1.14 docs for the exact `talosctl upgrade` options that keep data on one node.

## Flux

Flux tracks `main`. To try a change on the live cluster first, follow [`flux.md`](flux.md#develop-on-a-branch);
the rule that matters: never push a `gotk-sync.yaml` set to `main` to a branch the live cluster still tracks, and
repoint the cluster to `main` before deleting a branch it follows.

```sh
export KUBECONFIG=$PWD/talos/euronodes/kubeconfig
kubectl create namespace flux-system
kubectl -n flux-system create secret generic sops-age --from-file=age.agekey=$HOME/.config/sops/age/euronodes.agekey
flux bootstrap github --owner=arnaud-deprez --repository=home-lab \
  --branch=main --path=clusters/euronodes --personal
flux get kustomizations      # infra-controllers, infra-configs, apps: READY True
```

The cluster has its own age key (`.sops.yaml` rules `*/euronodes/**` and `talos/euronodes/secrets*`).

## Tailscale (remote admin access)

Deployed and verified: the operator (`tailscale-operator-euronodes`) proxies the Kubernetes API and exposes the Talos
API as `talos-api-euronodes`. Set up as described in [`tailscale.md`](tailscale.md); this cluster's own files (OAuth
secret, names, Talos API address `178.159.34.177`) are in
[`infrastructure/controllers/euronodes/`](../infrastructure/controllers/euronodes/).

To (re)create it: make an OAuth client for this cluster (`tailscale.md`, step 5), put its credentials into
`operator-oauth.sops.yaml` with `sops` (**never push the placeholder values**: they block `infra-controllers`, see
`tailscale.md`), push, and check `kubectl -n tailscale-operator get pods` and the two devices in the Tailscale admin
console. Then add the Talos API Service's tailnet IP and MagicDNS name to the certificate
(`kubectl get service talos-api -n tailscale-operator`, the `--additional-sans` in the config command above,
regenerate with `--force`, apply; no reboot).

## DNS and TLS

One wildcard record at the DNS provider: `*.vps.powple.com  A  178.159.34.177`. Traefik serves
TLS on 443 and redirects 80 to 443. Each ingress asks cert-manager for its own certificate with the
`letsencrypt-prod` `ClusterIssuer` (HTTP-01). Hostnames therefore appear in public Certificate
Transparency logs. Use `letsencrypt-staging` while debugging (production rate limits). The issuers
carry no ACME email.

## Changing the public domain

The hostnames are Pocket ID's identity: **passkeys are tied to the hostname** (`id.<domain>`) and Immich's sign-in
is configured with the issuer address and callback URLs. Changing the domain therefore needs these steps, in this
order (the live cluster went from `*.euronodes.powple.com` to `*.vps.powple.com` this way):

1. **DNS first.** Point the new wildcard (`*.<domain>  A  178.159.34.177`) at the VM and check it with `dig +short id.<domain>`.
   Certificates for the new names cannot be issued until it resolves. Keep the old record until the end.
2. **Check the last Pocket ID backup** is recent (`backup-restore.md`).
3. **Change the three files** and merge: `apps/euronodes/pocket-id/ingress.yaml` (host and TLS host),
   `apps/euronodes/pocket-id/app-url-patch.yaml` (`APP_URL`), `apps/euronodes/immich/helmrelease-patch.yaml` (host and TLS host).
   cert-manager issues the new certificates within about a minute (`kubectl get certificate -A`; until then the old
   certificate is shown for the new name).
4. **Pocket ID passkeys stop working** on the new hostname. Do not panic: generate a one-time sign-in link for each
   user and register a new passkey (ideally two devices) from it:
   ```sh
   kubectl -n identity exec deploy/pocket-id -- /app/pocket-id one-time-access-token <username-or-email>
   ```
   The usernames are listed in Pocket ID's admin UI. Open the printed link on `https://id.<domain>`.
5. **Pocket ID → OIDC clients → Immich:** change the two `https://immich.<old>/…` callback URLs to the new host
   (`/auth/login` and `/user-settings`). The app callback `app.immich:///oauth-callback` does not change.
6. **Immich → Administration → Settings → Authentication → OAuth:** set the issuer URL to `https://id.<domain>`. Until
   you do, "Login with OAuth" fails; the normal password login still works as a fallback.
7. **Phones:** in the Immich app change the server URL to `https://immich.<domain>` and sign in again.
8. When everything works, remove the old DNS record.

Two things that bit during the first switch: a **stale DNS cache** (a laptop or phone kept the old address for a
name that used to point elsewhere, so it reached the wrong server: a 404 from a different nginx and a certificate
that did not match). `dig` bypasses the OS cache, so test with a normal request, and flush the cache
(`sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder` on macOS) or wait out the old record's TTL. And
the one-time sign-in links last **one hour**: generate each user's link when they are at their device.

## Immich storage

| Data | Where |
| ---- | ----- |
| `library`, `upload` (originals) | EuroNodes S3 bucket `home`, prefix `immich/`, through rclone crypt (client-side encrypted, file names too) |
| `profile`, `backups` (Immich's nightly DB dumps), `thumbs`, `encoded-video`, ML cache, Postgres | local `local-path` |

How it works ([`apps/euronodes/immich/`](../apps/euronodes/immich/)):

- `rclone-mount.yaml`: a privileged Deployment mounts the crypt remote at `/var/mnt/rclone` (the Talos
  directory volume) with bidirectional propagation. Immich bind-mounts `library` and `upload` from it
  (`HostToContainer`) and an init container waits for the marker object `.mounted`.
- `rclone-secret.sops.yaml`: the rclone config as environment variables. The S3 server rejects V4
  request signing (`SignatureDoesNotMatch`), so the remote needs `v2_auth=true`, path-style addressing
  and `directory_markers=true` (so empty folders persist).
- **Watchdog.** After the rclone pod is replaced, Immich keeps a dead bind mount until restarted. A
  sidecar in the rclone pod watches the mount's device number and patches Immich's `restartedAt`
  annotation (RBAC limited to `patch` on that one Deployment; the service-account token is mounted only in that
  sidecar, not in the privileged rclone container). Immich is writable again about
  20 seconds after a kill.
- Moving an existing library onto the mount: Immich's integrity check refuses to start if the `.immich`
  marker file is missing in `library/` or `upload/`. Copy the markers across from the old volume.
- Failed or killed uploads can leave partial files on S3 that Immich does not track. Delete them in
  Administration → Repair (untracked files). They are included in backups until deleted.

Test gate results on the live cluster (all passed): upload and thumbnails, encryption at rest (no
readable names in the bucket), storage-template migration (moves files between folders on the
mount), killing rclone mid-upload (upload fails cleanly, no partial asset, retry succeeds with an
identical checksum), starting Immich with rclone down (it waits in init and writes nothing), full
node reboot.

Keys to keep outside the cluster: the rclone crypt `password` and `salt`, the S3 keys. They are also
in `rclone-secret.sops.yaml`, readable only with the age key.

## Backups and restore

Two nightly restic jobs (Immich 04:00 UTC, Pocket ID 03:30 UTC) write encrypted backups to a Hetzner Storage Box,
one repository per app. **Everything about them, and every restore procedure, is in
[`backup-restore.md`](backup-restore.md)**: what is covered, how the jobs work, daily checks, restoring a file,
a database, the S3 bucket or the whole VM, and what has and has not been tested. Tested restore pods are in
[`restore/`](restore/).

Keep in a password manager: the age private key, the restic password, the Storage Box SSH key
(`~/.ssh/euronodes-backup`) and the rclone crypt passwords. Turn on Storage Box snapshots in Hetzner's console.

## Monitoring

Prometheus and Alertmanager run in the `monitoring` namespace (UI over Tailscale only), Flux errors and backup
runs are reported to Telegram, healthchecks.io watches the backups and the monitoring itself, and a GitHub
Actions probe checks the public URLs. Everything about it, including how to test it, is in
[`monitoring.md`](monitoring.md). The cluster cannot pull `registry.k8s.io`, so kube-state-metrics comes from a
mirror (see its section 7).

## Known risks

- The VM UUID changing makes the volumes unreadable (restore from backup).
- Google registries block this IP range: upgrades may fail until a mirror exists.
- The S3 bucket is the live copy, not a backup. The Storage Box is the second copy; enable its snapshots.
- The Talos and Kubernetes APIs are reachable only through Tailscale. If the tailnet path breaks, access needs
  the EuroNodes console (see _Reaching the cluster_).
- Immich and Pocket ID are public (ports 80/443). Making them private behind Tailscale is possible, but it needs
  DNS-01 certificates (HTTP-01 needs port 80 reachable) and every user on the VPN; passkeys are tied to the
  hostname, so keep the hostnames unchanged.
- Traefik: port 80 (redirect and ACME only) has 30 s timeouts. Port 443 allows requests up to 1 h so large Immich
  uploads and downloads on slow links are not cut; shorten it if uploads are always small.
- rclone has a 512 MiB memory limit (it used 78 MiB, peaking at 174 MiB, during testing). An out-of-memory kill
  restarts the mount and, through the watchdog, Immich.
- rclone presents files as `rwxr-xr-x` (`--umask=022`) owned by root; Immich, the backup job and rclone all run as root.
- An rclone restart interrupts Immich for about 20 seconds (watchdog restart) and fails in-flight uploads.
- S3 request signing uses the legacy V2 scheme because the server rejects V4.
- Alerting (Flux errors, node and pod health, certificates, backups, Storage Box space, public URLs) goes to
  Telegram: see [`monitoring.md`](monitoring.md). The S3 bucket's own usage is not monitored.
