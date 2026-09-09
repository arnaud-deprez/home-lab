# Home lab

Home lab on a single-node [Talos](https://www.talos.dev/) cluster,
GitOps-managed by [Flux](https://fluxcd.io/): Flux watches `main` and
reconciles `clusters/laptop/` onto the cluster.

| Component | Status | Where |
|---|---|---|
| Immich | ✅ | [`apps/base/immich/`](apps/base/immich/) — CloudNativePG + VectorChord, Valkey, ingress `immich.home.arpa` |
| Reverse proxy (Traefik) | ✅ | [`infrastructure/controllers/base/traefik/`](infrastructure/controllers/base/traefik/) — hostPort DaemonSet |
| Tailscale | ⬜ | |
| Nextcloud | ⬜ | |

- **Working with Flux** — branches, secrets, day-to-day ops: [`docs/flux.md`](docs/flux.md)
- **Bootstrapping a cluster**: [`bootstrap/README.md`](bootstrap/README.md)

Immich is at `http://immich.home.arpa/` — add `192.168.64.5 immich.home.arpa`
to `/etc/hosts` on the client (node IP; Traefik binds hostPort 80/443).

## Repo layout

```
clusters/laptop/   Flux entrypoint — Kustomizations, ordering, SOPS decryption patch
infrastructure/
  controllers/     operators & ingress (CloudNativePG, Traefik)   — reconciled first
  configs/         cluster config (local-path-provisioner)
apps/              workloads (Immich)
```

Each of `infrastructure/*` and `apps/` has `base/` (reusable) + `laptop/`
(per-cluster Kustomize overlay).

## Design notes

- **One Flux `HelmRelease` / Kustomization per component**, not an umbrella
  chart. Ordering via `dependsOn`; stateful dependencies via operators
  (CloudNativePG) rather than bundled subcharts.
- **`controllers` → `configs` → `apps`** ordering — operators and CRDs must
  exist before the resources that use them.
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

Once the VM is started, there are a couple of env variables you need to set

```sh
export CONTROL_PLANE_ID=x.x.x.x
export WORKER_ID=x.x.x.x
export TALOSCONFIG="_out/talosconfig"
```

##### UTM (macos)

On UTM, we need to patch the gen config to match disk locations.

```sh
talosctl gen config talos-homelab https://$CONTROL_PLANE_IP:6443 --output-dir _out --force --config-patch @utm.patch.yaml
# Create control plane node
talosctl apply-config --insecure --nodes $CONTROL_PLANE_IP --file _out/controlplane.yaml
# Set endpoint config in talosconfig
talosctl --talosconfig $TALOSCONFIG config endpoint $CONTROL_PLANE_IP
talosctl --talosconfig $TALOSCONFIG config node $CONTROL_PLANE_IP
# Bootstrap k8s
talosctl --talosconfig $TALOSCONFIG bootstrap
# Wait for it to be ready, it can take few minutes to bootstrap etcd and k8s
# Then retrieve k8s config
talosctl --talosconfig $TALOSCONFIG kubeconfig _out/
```

Then backup config

```sh
mv _out context
export TALOSCONFIG="$PWD/context/talosconfig"
export KUBECONFIG="$PWD/context/kubeconfig"
# validate
talosctl --talosconfig $TALOSCONFIG dashboard
kubectl get node
```
