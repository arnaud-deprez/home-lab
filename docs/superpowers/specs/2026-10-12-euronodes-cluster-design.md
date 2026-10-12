# EuroNodes — third Talos cluster (design)

Status: draft for review. Branch: `feat/euronodes`. Nothing here is applied to any node or to Flux.

## Goal

A third single-node Talos cluster on a [EuroNodes](https://euronodes.com) VPS (Portugal), GitOps-managed
by Flux from this repo, running the same apps as the other clusters (Immich, Pocket ID). Compared to
tierhive it is closer to production: it has a public IPv4 directly on the VM, TLS terminated by
Traefik with public ACME certificates, Immich originals on encrypted S3, encrypted disks, and an
off-site encrypted backup with a documented, tested restore.

## Facts (observed on the node)

| Item | Value |
| ---- | ----- |
| Provider platform | KVM on Proxmox (`QEMU Standard PC (i440FX + PIIX)`), custom ISO supported |
| Plan | 4 vCPU / 8 GB RAM / ~161 GB disk, dedicated IPv4 (+IPv6 included) |
| Public IPv4 | `178.159.34.177/24` on `ens18`, gateway `178.159.34.1`, no NAT |
| IPv6 | `2a0d:9a40:100:100::/64` on `ens18` (left to the default config) |
| System disk | `/dev/sda` (virtio, QEMU HARDDISK, 161 GB); `sr0` is the ISO |
| Hostname | `euronodes` (set in the Talos config; the panel value is only a label) |
| Boot ISO | Image Factory bare-metal `amd64` with `iscsi-tools`; the version is read from the node (`talosctl version --insecure`) before generating the config |
| VM UUID | A random per-VM UUID (Proxmox). **Not recorded in the repo**: it is the key material for the node-ID disk key |

EuroNodes has no detachable block storage. The disk can be grown (about €0.03/GB/month); S3-compatible
object storage is about €4/TB/month. Prices come from public pages and are to be re-checked.

## Decisions

### 1. Cluster and Talos

- One node, control plane + worker (control-plane taint removed), same as the other clusters.
- Endpoint `https://178.159.34.177:6443`; the public IP is added as a certificate SAN
  (`--additional-sans`). No port forwards.
- Talos API (50000) and Kubernetes API (6443) are reachable from the internet and protected by mutual
  TLS. No source-IP firewall, because the operator's home IP is dynamic. Until the machine config is
  applied the maintenance API is anonymous, so the config is applied right after generation. Optional
  hardening later: put the node on Tailscale and restrict both ports to the tailnet.
- `LinkConfig` on `ens18`: static `178.159.34.177/24`, default route via `178.159.34.1`. DHCP currently
  works but a static address keeps etcd and the cert SANs stable.
- DNS and NTP use the multi-document kinds (`ResolverConfig`, `TimeSyncConfig`), fixing the commented-out
  legacy block in the tierhive patch.
- `installer.image` pinned to the Image Factory installer for the same schematic and Talos version as the
  booted ISO, so the `iscsi-tools` extension survives install and upgrades.
- One secrets bundle, SOPS-encrypted (`talos/euronodes/secrets.sops.yaml`); the generated configs,
  `talosconfig` and kubeconfig are git-ignored, like the other clusters.

### 2. Disk layout and encryption

| Volume | Size | Notes |
| ------ | ---- | ----- |
| `STATE`, `EPHEMERAL` | `EPHEMERAL` capped at 40 GB | LUKS2 |
| `local-path-provisioner` user volume | `minSize: 1GB`, `grow: true` (about 115 GB) | LUKS2, mounted at `/var/mnt/local-path-provisioner`, so `local-path` works unchanged |

- Install disk selector `/dev/sda`.
- Encryption uses the **node-ID key** (derived from the VM UUID). It protects against a copied or
  decommissioned disk image and VM snapshots. It does **not** protect against the provider reading a running VM
  (the provider knows the UUID and has hypervisor access). A static key or an external KMS would change
  that and is out of scope.
- Encryption can only be set when a volume is created, so it is configured before the first install.
  Changing it later means wiping the volumes (and reinstalling for `STATE`). To be verified against the docs for the
  installed Talos version, including which key types `STATE` accepts.
- If EuroNodes ever changes the VM UUID (clone, provider-side restore), the volumes will not unlock. That is
  treated as the "new VM" case: rebuild and restore from backup.

### 3. Storage engine

Keep `local-path` (single node). Longhorn, Rook-Ceph and OpenEBS replicated engines only add value with
several nodes. Gaps (no quota, snapshots or expansion) are covered by moving originals off the disk, a disk-usage
alert, and tested backups. Revisit when a second node is added.

### 4. Ingress and TLS

- Domain `*.euronodes.powple.com` (temporary; hosts live in overlays so changing it is a patch). One
  wildcard `A` record at Namecheap → `178.159.34.177`.
- Traefik terminates TLS (hostPort 80/443) as on the laptop. No proxy in front, so no `forwardedHeaders` trust
  and no "no redirect" workaround.
- cert-manager with Let's Encrypt, **one certificate per hostname over HTTP-01**
  (`id.euronodes.powple.com`, `immich.euronodes.powple.com`). Reason: no DNS API credentials in the cluster
  (Namecheap's API is awkward). Trade-off accepted: hostnames appear in Certificate Transparency logs.
  A wildcard through DNS-01 (possibly with a delegated `_acme-challenge` zone) remains a later option.
- The existing `ca-issuer` is laptop-only. The euronodes configs overlay gets a Let's Encrypt `ClusterIssuer`
  (staging first, then production).

### 5. Immich storage

| Data | Location |
| ---- | -------- |
| `library`, `upload` (originals) | EuroNodes S3 through an **rclone crypt** mount (client-side encrypted, file names too) |
| `profile`, `backups` (DB dumps), `thumbs`, `encoded-video`, caches | Local `local-path` |
| Postgres (CNPG) | Local `local-path` |
| ML model cache | Local |

Rationale: originals are large write-once files, the rest is small, random-access and regenerable, or too
important to sit behind a FUSE layer. Keeping `backups` and `profile` local also avoids a circular
dependency between the backups and the mount.

**rclone first, with a gate.** Immich has no official S3 support and the maintainers have no plan for it, so
this is a community workaround. Evidence found: people run `library` and `upload` on `rclone mount`
(`--vfs-cache-mode writes`, `--allow-other`) with thumbnails local; an old hard-link failure in Immich's
move code was fixed in 2023. Nothing found on rclone crypt, large libraries or Kubernetes. So the plan includes
a test gate before any real photos are stored:

1. Upload photos and videos; generate thumbnails; play a video.
2. Run the storage-template migration job (the operation that once failed on rclone).
3. Restart the Immich or rclone pod mid-upload; check that nothing is lost or corrupted.
4. Restore a sample into a fresh bucket from the Hetzner backup.

If any step fails: fall back to JuiceFS with client-side encryption (needs a metadata Postgres that is backed up
straight to Hetzner, never through the mount), or to a larger local disk. rclone mount settings (persistent write
cache size, cache dir on the local volume) are decided in the plan. How the FUSE mount is exposed to the Immich pod
on Talos (privileged rclone pod with bidirectional mount propagation on a `/var` host path, or an rclone CSI driver)
is chosen in the plan. Both need a relaxed PodSecurity label on that namespace.

Keys that must also live outside the cluster (password manager): the rclone crypt password and salt and the
restic repository password. Losing them loses the data.

### 6. Backups

- Target: Hetzner Storage Box (SFTP). About €3.20/month per TB; to be confirmed on Hetzner's page.
- Tool: restic (client-side encrypted, snapshots, so a mistaken delete can be undone; not `rclone sync`, which
  would mirror deletions). A CronJob reads the decrypted originals through the mount plus `profile` and the Immich
  DB dumps, and writes to the box with its own key.
- Two independent keys (rclone crypt and restic), so losing one does not lose both copies.
- Postgres: Immich's own scheduled dumps (in the local `backups` folder), included in the restic job. CNPG's barman
  backup needs S3 and is not used. No point-in-time recovery; daily granularity is accepted.
- The S3 bucket is the live copy, not a backup. The Hetzner box is the second, independent copy.
- Thumbnails and encoded video are not backed up; they are regenerated (Immich admin jobs) after a restore.
- Restore runbook (in `docs/euronodes.md`): Talos + Flux first, then Postgres restore, then Immich. It is exercised
  once before the cluster is trusted with real data.

### 7. Repo changes (all on branch `feat/euronodes`)

| Path | Content |
| ---- | ------- |
| `talos/euronodes/patch.yaml`, `secrets.sops.yaml` | Talos patch (network, disk, volumes, encryption, DNS, NTP), SOPS secrets bundle |
| `clusters/euronodes/` | Flux entrypoint (`kustomization.yaml`, `infrastructure.yaml`, `apps.yaml`, `flux-system/` from `flux bootstrap`) |
| `infrastructure/controllers/euronodes/` | cnpg-operator, cert-manager (base exists), Traefik overlay; no Tailscale unless added later |
| `infrastructure/configs/euronodes/` | `local-path-provisioner`, Let's Encrypt `ClusterIssuer` |
| `apps/euronodes/` | Immich and Pocket ID overlays (hosts, TLS, rclone storage, backup CronJob, secrets as `*.sops.yaml`) |
| `.sops.yaml` | New age key; rules for `talos/euronodes/secrets*` and `*/euronodes/**` |
| `docs/euronodes.md`, `README.md` | Runbook, layout entry |
| `.gitignore` | `euronodes-pwd.txt` style files are never committed |

The live cluster is not pointed at this branch while it is being developed. When the node runs, `gotk-sync.yaml`
follows the branch procedure in `docs/flux.md`, and `main` is never set before the merge.

## Out of scope

Tailscale on this cluster (can follow), a wildcard certificate, a second node, replicated storage, an external KMS
for the disk key, native Immich S3 support.

## Open items to verify while writing the plan

- Talos disk encryption in the installed version: syntax for the node-ID key; allowed key types for `STATE`; the exact
  `talosctl upgrade` options that preserve data on a single node.
- How the Image Factory installer image path looks for the installed Talos version.
- The EuroNodes S3 endpoint, bucket creation, and compatibility (multipart uploads) as tested by the rclone gate.
- Whether the Hetzner Storage Box needs its SSH port and key-based access enabled for restic.
- Current EuroNodes and Hetzner prices.
