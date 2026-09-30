# TierHive — second Talos cluster

Runbook for installing a single-node Talos cluster on a [TierHive](https://tierhive.com)
VPS. Scope: Talos + Kubernetes, then Flux. The Flux overlay (`clusters/tierhive/`) and bootstrap steps are in
*Flux on this cluster* below.

The Talos patch used here lives in [`os/thierhive.patch.yaml`](../os/thierhive.patch.yaml).
Generated secrets, ISOs and kubeconfigs go in `os/tierhivecontext/` and `_out/`
(git-ignored) — never commit them.

## What TierHive is (and what that means for Talos)

Budget hourly VPS host, **public alpha**, not enterprise-grade. Facts that shape the setup:

- **KVM + custom ISO.** The panel mounts an ISO from an HTTPS URL (Virtual Media, up to
  2 slots) and offers a web VNC console. That is the only way to boot Talos: no
  raw/qcow2 import, no PXE, no cloud-init/metadata service.
  - The URL must be valid HTTPS, return 200 after at most 3 redirects, and support
    HTTP Range requests (Image Factory URLs do).
  - A mounted ISO is **auto-ejected after 48h**, and main-disk I/O is throttled while it
    is mounted. Mount it right before installing, eject it right after.
- **NAT-native networking.** The VPS only has a private IP (`10.10.8.2/24`, gateway
  `10.10.8.1`, **DHCP off**). Inbound traffic needs a port forward from a public port.
  The public address is `172.99.188.64`.
- **Storage classes.** NVMe (capped at 50 GB), HDD (large, but the VM is capped at 4 GB
  RAM while it has HDD), and — per older forum posts, **not visible in the panel** —
  network storage. Plan on NVMe only.
- **Architecture:** x86_64 (`amd64`). No ARM offering was found.
- **Live resize** of CPU/RAM/disk from *Upgrade / Downgrade*.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Topology | One node, control plane + worker | Mirrors the laptop cluster; validate before adding complexity |
| Sizing | 2 vCPU, 6 GB RAM, **NVMe ≥ 30 GB** | Talos minimum is 2 vCPU / 2 GB / 10 GB; 10 GB disk is too small once images + PVCs land |
| Disk | NVMe only, never HDD for the system disk | etcd fsync latency on HDD causes leader elections and instability |
| Image | Image Factory → **Bare-metal Machine**, `amd64`, **ISO** | TierHive is not a supported cloud platform, so `platform=metal` (no metadata service); ISO is the only boot path |
| Node IP | Static `10.10.8.2/24` via `LinkConfig` | TierHive DHCP is off; etcd and the endpoint depend on this IP being stable |
| Storage | One disk split into `EPHEMERAL` + a `local-path-provisioner` user volume | Matches the repo's `local-path` config (`/var/mnt/local-path-provisioner`) — no manifest changes |
| Kube API exposure | Plain TCP port forward, **not** TierHive HAProxy | k8s/Talos APIs use mutual TLS; a TLS-terminating proxy breaks client-cert auth |

If bulk storage is needed later, add a second VM as a Talos **worker** with the HDD as a
user volume and pin the bulk workloads to it (`nodeSelector`). Keep databases on NVMe.

## Ports

Forward these in the panel (Overview → Forwarded Ports):

| Public port | → Internal | Purpose | Used by |
|---|---|---|---|
| `6072` | `10.10.8.2:50000` | Talos API | `talosctl` |
| `6075` | `10.10.8.2:6443` | Kubernetes API | `kubectl`, Flux |
| — | — | HTTP/HTTPS for apps | Not a port forward: TierHive assigns public forward ports (e.g. `5777 → 443`), so 80/443 cannot be forwarded. Use the managed HAProxy, see *Public ingress* below |

Port forwards are **unauthenticated at the network layer**. Until a machine config is
applied, the Talos maintenance API accepts `--insecure` requests from anyone who finds the
port. Apply the config promptly, and restrict the forward by source IP if the panel allows.

## Procedure

### 0. Prerequisites

- TierHive VPS created (*Manual Install*), sized as above. **Resize the NVMe disk to
  ≥ 30 GB first** — a second partition needs room.
- Forwarded ports `6072` and `6075` added.
- Generate an ISO at [factory.talos.dev](https://factory.talos.dev): Bare-metal Machine,
  `amd64`, pick the Talos version, add extensions if needed (`iscsi-tools` for iSCSI
  storage). Keep the **schematic ID**. The ISO link is
  `https://factory.talos.dev/image/<schematic-id>/<version>/metal-amd64.iso` — paste the
  URL, don't download it.

### 1. Boot the ISO

Panel → **Virtual Media** → mount the ISO URL in slot A, then reboot. In the **Console**
(VNC) the Talos dashboard shows maintenance mode.

The VM has no DHCP lease, so it has no network yet. Either enable DHCP in the panel, or
press **F3** on the dashboard and set a static address (`10.10.8.2/24`, gateway
`10.10.8.1`, DNS `1.1.1.1`). Confirm it can ping out before continuing — the install pulls
the installer image from `factory.talos.dev`.

### 2. Reach the Talos API

```sh
export ENDPOINT=172.99.188.64:6072   # public forward -> node :50000
export NODE=10.10.8.2                # internal IP

talosctl -n $NODE -e $ENDPOINT --insecure get disks   # note the system disk (/dev/vda?)
talosctl -n $NODE -e $ENDPOINT --insecure get links   # note the NIC name (ens3?)
```

`-e` (endpoint) may include a port. **`-n` (node) must be a plain IP — no port**, or
`talosctl` fails with `invalid target`. The endpoint proxies the call to the node.

If `--insecure` calls through the forward fail, forward public `50000 → 50000` instead.

### 3. Generate the config

```sh
talosctl gen config tierhive https://10.10.8.2:6443 \
  --additional-sans 172.99.188.64 \
  --output-dir os/tierhivecontext \
  --config-patch @os/thierhive.patch.yaml
```

- The cluster endpoint uses the **internal** IP so the node never reaches itself through
  the NAT.
- `--additional-sans` puts the public IP into the certificates so your laptop passes TLS
  validation. Without it, `talosctl`/`kubectl` fail on the forwarded address.

### 4. Review the patch

[`os/thierhive.patch.yaml`](../os/thierhive.patch.yaml) is a multi-document patch:

| Document | Does |
|---|---|
| `LinkConfig` (`ens3`) | Static address and default route (DHCP is off) |
| `KubeNodeConfig` | Removes the control-plane taint so workloads schedule on this single node |
| `UnattendedInstallConfig` | **Which disk** Talos installs on (`/dev/vda`). Does not size partitions |
| `VolumeConfig` `EPHEMERAL` | **Caps** the Talos data partition (images, containerd, logs, etcd) with `maxSize`. Without a cap it takes the whole disk |
| `UserVolumeConfig` `local-path-provisioner` | Takes the remaining space, mounted at `/var/mnt/local-path-provisioner` — what the repo's `local-path` provisioner expects |

Before applying, check that:

- `maxSize` for `EPHEMERAL` plus the user volume fit the real disk, leaving some room for
  `STATE`/`META`/`BOOT`. The patch currently has `EPHEMERAL` at 10 GB and the user volume
  1–20 GB, which fits a 30 GB disk.
- `installer.image` is still unset, so the stock installer is used. To keep ISO
  extensions (for example `iscsi-tools`), add
  `installer.image: factory.talos.dev/metal-installer/<schematic-id>:<version>`
  (path from the v1.14 docs — verify for your version).
- DNS and NTP (`1.1.1.1`, `time.cloudflare.com`) are still **commented out** at the top of
  the patch; they use the old `machine:` syntax. Port them to the multi-document kinds
  (`ResolverConfig`, `TimeSyncConfig`) and check the field names against the docs.
- A volume config only applies while the volume is **not yet provisioned**. A node that
  already installed with a full-disk `EPHEMERAL` needs a clean reinstall: uncomment
  `wipe: true` in `UnattendedInstallConfig` (only while the node holds no data), or
  `talosctl reset --system-labels-to-wipe EPHEMERAL`.

### 5. Apply, then eject the ISO

```sh
talosctl -n $NODE -e $ENDPOINT --insecure apply-config -f os/tierhivecontext/controlplane.yaml
```

The node installs to disk and reboots. **Eject ISO slot A in the panel right away**, or it
may boot the ISO again.

### 6. Bootstrap (once)

```sh
export TALOSCONFIG=os/tierhivecontext/talosconfig
talosctl config endpoint $ENDPOINT
talosctl config node $NODE

talosctl bootstrap
talosctl health
```

### 7. Kubeconfig

```sh
talosctl kubeconfig os/tierhivecontext/kubeconfig
```

Set `server:` in it to `https://172.99.188.64:6075`, then:

```sh
KUBECONFIG=os/tierhivecontext/kubeconfig kubectl get nodes
```

### 8. Verify storage

```sh
talosctl get volumestatus
```

`EPHEMERAL` should be capped at its `maxSize` and `u-local-path-provisioner` should be
`ready` and mounted at `/var/mnt/local-path-provisioner`.

## Public ingress (HAProxy)

TLS is terminated by TierHive's managed HAProxy; Traefik only sees plain HTTP on
`10.10.8.2:80`. There is no cert-manager on this cluster.

1. Add one HAProxy domain per hostname — `id.vps.powple.com` and `immich.vps.powple.com`.
   Backend `10.10.8.2`, port `80`. Point the DNS records (Namecheap) at the address TierHive
   shows, then *Activate SSL*.
2. HAProxy redirects HTTP→HTTPS, so Traefik's `web` entrypoint must **not** redirect (it would
   loop). The tierhive overlay omits the redirect the laptop has.
3. Traefik trusts `X-Forwarded-*` from `10.10.8.0/24`
   (`infrastructure/controllers/tierhive/traefik-values-patch.yaml`). If logins fail the passkey
   origin check, or apps see `http`, read the HAProxy source address from Traefik's access logs
   and adjust `forwardedHeaders.trustedIPs`.
4. Test early: large Immich uploads (body-size limit, timeouts).
5. Test early: Immich's server-side OIDC calls to `https://id.vps.powple.com` leave the node and come back through HAProxy (hairpin). If TierHive's NAT blocks that, SSO login fails even though browsers work; fallback is a CoreDNS rewrite to Traefik (which then needs matching TLS).

Risks: TierHive decrypts all traffic and the hop to the node is plain HTTP; HAProxy is a single
point of failure on a public alpha. Keep Immich registration closed and review Pocket ID's
signup/admin settings.

## Flux on this cluster

The cluster has its own age key, so a compromise of the VPS does not expose the laptop's
secrets. Rules in `.sops.yaml`: `*/tierhive/**` → tierhive key.

```sh
age-keygen -o ~/.config/sops/age/tierhive.agekey     # once; never commit
cat ~/.config/sops/age/home-lab.agekey ~/.config/sops/age/tierhive.agekey \
  > ~/.config/sops/age/keys.txt                      # lets `sops` edit both clusters' secrets
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt # macOS sops does not read it by default

# Prerequisite: create a NEW Tailscale OAuth client (docs/tailscale.md), then
sops infrastructure/controllers/tierhive/operator-oauth.sops.yaml
# and list `operator-oauth.sops.yaml` in infrastructure/controllers/tierhive/kustomization.yaml.
# Without it the operator never starts, infra-controllers (wait: true) never turns Ready and
# identity/apps are never deployed.

export KUBECONFIG=os/tierhivecontext/kubeconfig
kubectl create namespace flux-system
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey=$HOME/.config/sops/age/tierhive.agekey
flux bootstrap github --owner=<owner> --repository=<repo> \
  --branch=<feat/tierhive-cluster while validating, then main> \
  --path=clusters/tierhive --personal
```

Follow the branch rules in [`flux.md`](flux.md): never flip `gotk-sync.yaml` to `main` while the
cluster still tracks the feature branch.

After the first reconcile:

1. Create the Immich OIDC client in Pocket ID (`https://id.vps.powple.com`) and configure it in
   Immich (UI steps).
2. Once `talosctl`/`kubectl` work over the tailnet (operator `tailscale-operator-tierhive`,
   Talos API `talos-api-tierhive`, see [`tailscale.md`](tailscale.md)), remove the public
   `6072` / `6075` forwards.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `nc: Network is unreachable` from the laptop | Local routing (VPN / exit node / IPv6-only network), not TierHive. Try `route -n get 172.99.188.64` and another network |
| `invalid target "host:port"` | Put the port on `-e`, not `-n` |
| Node has no internet in maintenance mode | DHCP is off — set a static address (F3) or enable DHCP |
| TLS error through the public IP | `--additional-sans 172.99.188.64` missing from `gen config` |
| Node boots back into maintenance mode after install | ISO not ejected, or install failed; check the VNC console |
| `EPHEMERAL` still fills the disk | Volume config only applies to unprovisioned volumes — reinstall with `wipe` |

## Open items

- Confirm whether DHCP can be enabled in the panel (not needed if the static config stays).
- Flux bootstrap for this cluster: see *Flux on this cluster* above (overlays exist, bootstrap not yet run).
- Remote admin over Tailscale for this cluster: see [`docs/tailscale.md`](tailscale.md).
