# EuroNodes — third Talos cluster

Runbook for the single-node Talos cluster on a [EuroNodes](https://euronodes.com) VPS (Portugal),
GitOps-managed by Flux like the other clusters. Design rationale:
[`superpowers/specs/2026-10-12-euronodes-cluster-design.md`](superpowers/specs/2026-10-12-euronodes-cluster-design.md).

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
| API exposure | Talos API (50000) and Kubernetes API (6443) are public, protected by mutual TLS. No source-IP firewall (dynamic home IP) |
| Ingress | Traefik terminates TLS on the node (hostPort 80/443); cert-manager, one Let's Encrypt certificate per host over HTTP-01 |
| Domain | `*.euronodes.powple.com` (placeholder: hosts live in `apps/euronodes/*`, change them there) |
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

`talosctl` always talks to the Talos API on **50000**, not 6443. If it times out while 443 and
6443 work, a network between you and the VM is filtering that port (seen once from another network);
try another connection.

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

Flux tracks the branch while it is developed, then `main` (see
[`flux.md`](flux.md#develop-on-a-branch) for the merge order; the rule that matters: never push a
`gotk-sync.yaml` set to `main` to a branch the live cluster still tracks).

```sh
export KUBECONFIG=$PWD/talos/euronodes/kubeconfig
kubectl create namespace flux-system
kubectl -n flux-system create secret generic sops-age --from-file=age.agekey=$HOME/.config/sops/age/euronodes.agekey
flux bootstrap github --owner=arnaud-deprez --repository=home-lab \
  --branch=<feat-branch-or-main> --path=clusters/euronodes --personal
flux get kustomizations      # infra-controllers, infra-configs, apps: READY True
```

The cluster has its own age key (`.sops.yaml` rules `*/euronodes/**` and `talos/euronodes/secrets*`).

## Tailscale (remote admin access)

Set up as described in [`tailscale.md`](tailscale.md); this cluster's own files (OAuth secret, names
`tailscale-operator-euronodes` / `talos-api-euronodes`, Talos API address `178.159.34.177`) are in
[`infrastructure/controllers/euronodes/`](../infrastructure/controllers/euronodes/).

1. Create an OAuth client for this cluster (`tailscale.md`, step 5).
2. Fill the credentials: `sops infrastructure/controllers/euronodes/operator-oauth.sops.yaml`, replace both
   `REPLACE_ME_…` values and save. **Do not push the placeholder** (it blocks `infra-controllers`, see `tailscale.md`).
3. Push, then check `kubectl -n tailscale-operator get pods` and the two new devices in the Tailscale admin console.
4. Add the Talos API Service's tailnet IP and MagicDNS name to the certificate (they are `--additional-sans` in the
   command above; regenerate with `--force`, apply, no reboot), then over the tailnet:
   `tailscale configure kubeconfig tailscale-operator-euronodes` and
   `talosctl -e <talos-api-euronodes MagicDNS name> -n 178.159.34.177 version`.

Closing the public Talos and Kubernetes APIs (Talos ingress firewall) is a separate, later step: only do it after
step 4 works, and keep EuroNodes' web console as the way back in.

## DNS and TLS

One wildcard record at the DNS provider: `*.euronodes.powple.com  A  178.159.34.177`. Traefik serves
TLS on 443 and redirects 80 to 443. Each ingress asks cert-manager for its own certificate with the
`letsencrypt-prod` `ClusterIssuer` (HTTP-01). Hostnames therefore appear in public Certificate
Transparency logs. Use `letsencrypt-staging` while debugging (production rate limits). The issuers
carry no ACME email.

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
  annotation (RBAC limited to `get`/`patch` on that one Deployment). Immich is writable again about
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

## Known risks

- The VM UUID changing makes the volumes unreadable (restore from backup).
- Google registries block this IP range: upgrades may fail until a mirror exists.
- The S3 bucket is the live copy, not a backup. The Storage Box is the second copy; enable its snapshots.
- The Talos and Kubernetes APIs are open to the internet behind mutual TLS; a Tailscale restriction
  is possible later.
- Traefik: port 80 (redirect and ACME only) has 30 s timeouts. Port 443 allows requests up to 1 h so large Immich
  uploads and downloads on slow links are not cut; shorten it if uploads are always small.
- rclone has a 512 MiB memory limit (it used 78 MiB, peaking at 174 MiB, during testing). An out-of-memory kill
  restarts the mount and, through the watchdog, Immich.
- rclone presents files as `rwxr-xr-x` (`--umask=022`) owned by root; Immich, the backup job and rclone all run as root.
- An rclone restart interrupts Immich for about 20 seconds (watchdog restart) and fails in-flight uploads.
- S3 request signing uses the legacy V2 scheme because the server rejects V4.
- **Nothing alerts when a backup fails or stops.** The jobs have deadlines (Immich 12 h, Pocket ID 1 h) so a
  hang cannot block later runs, but a failed or skipped run is only visible with `kubectl get jobs -A`.
  The disk-usage alert in the design spec is not implemented either. Check the jobs now and then until
  monitoring is added (for example a healthchecks-style ping at the end of each job).
