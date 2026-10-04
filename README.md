# Home lab

Home lab on a single-node [Talos](https://www.talos.dev/) cluster,
GitOps-managed by [Flux](https://fluxcd.io/): Flux watches `main` and
reconciles `clusters/laptop/` onto the laptop cluster and `clusters/tierhive/` onto
a second single-node cluster on a [TierHive](docs/tierhive.md) VPS.

| Component                  | Status | Where                                                                                                                                                                                                                                                                                       |
| -------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Immich                     | ✅     | [`apps/base/immich/`](apps/base/immich/) — CloudNativePG + VectorChord, Valkey, ingress `immich.home.arpa`; SSO login via Pocket ID                                                                                                                                                         |
| SSO (Pocket ID)            | ✅     | [`identity/base/pocket-id/`](identity/base/pocket-id/) — OIDC provider, passkey login, ingress `id.home.arpa`                                                                                                                                                                               |
| Reverse proxy (Traefik)    | ✅     | [`infrastructure/controllers/base/traefik/`](infrastructure/controllers/base/traefik/) — hostPort DaemonSet                                                                                                                                                                                 |
| HTTPS / TLS (cert-manager) | ✅     | [`infrastructure/controllers/base/cert-manager/`](infrastructure/controllers/base/cert-manager/) — private CA, per-app certs via ingress-shim                                                                                                                                               |
| Tailscale                  | ✅     | [`infrastructure/controllers/base/tailscale-operator/`](infrastructure/controllers/base/tailscale-operator/) — Kubernetes operator; exposes the k8s API server (API-server-proxy) and the Talos API for remote admin access over the tailnet — see [`docs/tailscale.md`](docs/tailscale.md) |
| Nextcloud                  | ⬜     |                                                                                                                                                                                                                                                                                             |
| Home Assistant             | ⬜     | planned behind `oauth2-proxy` (no native OIDC support)                                                                                                                                                                                                                                      |

- **Working with Flux** — branches, secrets, day-to-day ops: [`docs/flux.md`](docs/flux.md)
- **HTTPS / TLS trust setup**: [`docs/tls.md`](docs/tls.md)
- **Tailscale remote admin access**: [`docs/tailscale.md`](docs/tailscale.md)
- **Second cluster on TierHive (Talos install)**: [`docs/tierhive.md`](docs/tierhive.md)
- **Bootstrapping a cluster**: [`bootstrap/README.md`](bootstrap/README.md)

Immich is at `https://immich.home.arpa/`, Pocket ID (SSO) at
`https://id.home.arpa/` — add both to `/etc/hosts` on the client, pointing
at `192.168.64.5` (node IP; Traefik binds hostPort 80/443, and redirects
HTTP to HTTPS). The cert is signed by an in-cluster private CA — see
[`docs/tls.md`](docs/tls.md) to trust it on your device first.

## Repo layout

```
clusters/laptop/   Flux entrypoint — Kustomizations, ordering, SOPS decryption patch
clusters/tierhive/ same for the TierHive VPS (TLS via managed HAProxy, no cert-manager)
infrastructure/
  controllers/     operators & ingress (CloudNativePG, Traefik, cert-manager)   — reconciled first
  configs/         cluster config (local-path-provisioner, private CA issuer)
identity/          SSO — Pocket ID OIDC provider
apps/              workloads (Immich)
```

Each of `infrastructure/*`, `identity/`, and `apps/` has `base/` (reusable) +
per-cluster Kustomize overlays (`laptop/`, `tierhive/`). Secrets that differ per cluster
live in that cluster's overlay, encrypted with that cluster's own age key.

## Design notes

- **One Flux `HelmRelease` / Kustomization per component**, not an umbrella
  chart. Ordering via `dependsOn`; stateful dependencies via operators
  (CloudNativePG) rather than bundled subcharts.
- **`controllers` → `configs` → `identity`/`apps`** ordering — operators and
  CRDs must exist before the resources that use them.
- **`base/` + per-cluster overlay** so a future VPS / on-prem cluster reuses
  `base/` and only overlays what differs (host, storage class, sizes,
  ingress exposure).
- **Traefik, not ingress-nginx** (the latter is in maintenance-only
  wind-down). On the laptop it runs as a hostPort DaemonSet — works behind
  UTM NAT with no LoadBalancer. A VPS/on-prem overlay would swap in MetalLB
  or a cloud controller-manager.
- **SOPS + age** for secrets. Decryption is patched onto every Kustomization
  from `clusters/laptop/kustomization.yaml`; the age private key lives only
  in the cluster (`sops-age` secret) and a password manager. No encrypted
  secret exists yet — CNPG generates the Immich DB credentials.

## Setup

This is currently running on a single VM.
The goal is to be able to reproduce the lab in different environment such as:

- a VM in a VPS/cloud
- a VM on prem on proxmox
- a VM on a laptop such as virtualbox or UTM on mac

### OS

For the OS, everal options were considered among which:

- [Fedora CoreOS](https://fedoraproject.org/coreos/): hybrid and podman centric
- [Flatcar](https://www.flatcar.org/): pure immutable OS based on docker. Good but owner works for Microsoft now and Microsoft stop supporting the distro recently.
- [openSUSE MicroOS](https://microos.opensuse.org/): alternative to CoreOS with podam too but immutable OS like Flatcar
- [Talos Linux](https://docs.siderolabs.com/talos/): kubernetes first OS. No shell, everything is k8s.
- [Debian 13](https://www.debian.org/index.fr.html): good old regular linux

I am all-in on k8s for all the goodies and to easily automate deployment pipeline.

#### Talos

[Guide for VirtualBox](https://docs.siderolabs.com/talos/v1.14/platform-specific-installations/local-platforms/virtualbox)
[Production ready](https://docs.siderolabs.com/talos/v1.14/getting-started/prodnotes)

> **NOTE**
>
> To add a Local Path provisioner, the VM must contain 2 disks: 1 for the system and 1 for the volume (nvme simulation if possible)
> In production, we should always use external/persistent storage to the VM that persists once the VM dies or is replaced.

Each cluster has its own folder under `talos/` (`talos/laptop/`, `talos/tierhive/`), holding
its patch and its SOPS-encrypted secrets bundle. Generate the secrets **first** and keep
only the encrypted copy, so the cluster PKI stays stable and configs can be regenerated at
any time (see [`docs/flux.md`](docs/flux.md) for the age keys, `.sops.yaml` selects the
right one by path).

Once the VM is started, there are a couple of env variables you need to set

```sh
export CONTROL_PLANE_IP=x.x.x.x
export WORKER_IP=x.x.x.x
export TALOSCONFIG="$PWD/talos/laptop/talosconfig"
```

##### UTM (macos)

On UTM, we need to patch the config to match disk locations ([`talos/laptop/patch.yaml`](talos/laptop/patch.yaml)).
The patch is applied at `apply-config` time, not at `gen config` time, so the generated
base config stays unpatched.

```sh
# Once: generate the secrets bundle, encrypt it with SOPS, drop the plaintext.
# Never regenerate it for a live cluster.
talosctl gen secrets -o talos/laptop/secrets.yaml
sops -e talos/laptop/secrets.yaml > talos/laptop/secrets.sops.yaml
rm talos/laptop/secrets.yaml

# Generate the base config from the decrypted secrets (no plaintext on disk)
talosctl gen config talos-homelab https://$CONTROL_PLANE_IP:6443 \
  --with-secrets <(sops -d talos/laptop/secrets.sops.yaml) \
  --output-dir talos/laptop --force
# Create control plane node, applying the UTM patch
talosctl apply-config --insecure --nodes $CONTROL_PLANE_IP \
  --file talos/laptop/controlplane.yaml \
  --config-patch @talos/laptop/patch.yaml
# Set endpoint in talosconfig. The endpoint is the reverse proxy ip where the control plane is reachable.
talosctl --talosconfig $TALOSCONFIG config endpoint $CONTROL_PLANE_IP
# Set the node ip. The node ip is the ip the VM that might be directly reachable from talosctl.
talosctl --talosconfig $TALOSCONFIG config node $CONTROL_PLANE_IP
# Bootstrap k8s
talosctl --talosconfig $TALOSCONFIG bootstrap
# Wait for it to be ready, it can take few minutes to bootstrap etcd and k8s
# Then retrieve k8s config
talosctl --talosconfig $TALOSCONFIG kubeconfig talos/laptop/
```

The generated `controlplane.yaml`, `worker.yaml`, `talosconfig` and `kubeconfig` embed
credentials and are git-ignored; only `secrets.sops.yaml` and the patch are committed. If
you lose them, regenerate from the secrets bundle:

```sh
talosctl gen config talos-homelab https://$CONTROL_PLANE_IP:6443 \
  --with-secrets <(sops -d talos/laptop/secrets.sops.yaml) --output-dir talos/laptop --force
```

Then validate

```sh
export KUBECONFIG="$PWD/talos/laptop/kubeconfig"
talosctl --talosconfig $TALOSCONFIG dashboard
kubectl get node
```
