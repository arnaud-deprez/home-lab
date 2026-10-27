# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-node [Talos](https://www.talos.dev/) Kubernetes home lab, GitOps-managed by
[Flux](https://fluxcd.io/). Flux watches `main` and reconciles `clusters/laptop/` (and `clusters/tierhive/`, `clusters/euronodes/` for the TierHive and EuroNodes VPS clusters) onto
the cluster every 10 minutes. There is no application build/lint/test pipeline — this
repo *is* the cluster's desired state, expressed as Kubernetes manifests, Kustomize
overlays, and Flux `HelmRelease`/`Kustomization` objects.

Full details live in [`docs/flux.md`](docs/flux.md) (day-to-day ops, secrets, branch
workflow), [`flux-bootstrap/README.md`](flux-bootstrap/README.md) (one-time cluster
bootstrap) [`docs/euronodes.md`](docs/euronodes.md) (EuroNodes cluster: Talos, S3 storage) and
[`docs/backup-restore.md`](docs/backup-restore.md) (its restic backups and how to restore) and
[`docs/monitoring.md`](docs/monitoring.md) (alerting: what watches what, where alerts go, how to test them). Read those before making non-trivial changes — this file only summarizes
what's needed to navigate and validate.

## Layout and reconcile order

```
talos/{laptop,tierhive,euronodes}/       Talos OS config per cluster: patch + SOPS-encrypted secrets bundle
                               (secrets.sops.yaml); generated configs/kubeconfig are git-ignored.
                               Not applied by Flux — see README.md / docs/tierhive.md / docs/euronodes.md
clusters/{laptop,tierhive,euronodes}/    one Flux entrypoint per cluster; same file set:
  kustomization.yaml    entrypoint: resources + the SOPS decryption patch
  flux-system/           Flux's own manifests, managed by `flux bootstrap` — don't hand-edit
  infrastructure.yaml    Flux Kustomizations: infra-controllers -> infra-configs
  apps.yaml               Flux Kustomization: apps (after infra-controllers, infra-configs)
infrastructure/
  controllers/{base,laptop,tierhive,euronodes}   operators, ingress (CloudNativePG, Traefik, cert-manager), Prometheus — reconciled first
  configs/{base,laptop,tierhive,euronodes}       cluster config (local-path-provisioner, private CA issuer, Flux notifications)
apps/{base,laptop,tierhive,euronodes}            workloads (Immich, Pocket ID OIDC provider — id.home.arpa / id.tierhive.powple.com /
                                       id.vps.powple.com (euronodes);
                                       Pocket ID lives in the `identity` namespace)
```

`controllers` -> `configs` -> `apps`, enforced via `dependsOn` in the Flux
`Kustomization` objects, because operators/CRDs must exist before the resources that
use them. Pocket ID is just another app; other apps opt into SSO individually (configuring
their own OIDC client against it), so there's no ordering between them. If something ever
needs Pocket ID up first, split `apps` into per-app Kustomizations with `dependsOn`.

Every `infrastructure/*` and `apps/` component has `base/` (reusable manifests) +
per-cluster overlays (`laptop/`, `tierhive/`, `euronodes/`; Kustomize patches). A new cluster reuses
`base/` and only overlays what differs (host, storage class, ingress exposure). The tierhive
cluster differs notably: TLS is terminated by TierHive's managed HAProxy (plain HTTP to
Traefik, no redirect, no cert-manager) — see `docs/tierhive.md`. The euronodes cluster has a public IPv4, so Traefik
terminates TLS itself (cert-manager, Let's Encrypt HTTP-01), disks are LUKS-encrypted, Immich originals live on an
rclone-crypt S3 mount and both apps are backed up with restic — see `docs/euronodes.md`.

**Design conventions to preserve when adding a component:**
- One Flux `HelmRelease` / `Kustomization` per component, not an umbrella chart.
  Order dependencies via `dependsOn`.
- Traefik (hostPort DaemonSet), not ingress-nginx.
- Every cluster runs the Tailscale operator for remote admin access (`docs/tailscale.md`); its OAuth secret,
  tailnet hostnames and Talos API endpoint IP live in the cluster's `infrastructure/controllers/<cluster>/`.
- Secrets are SOPS + age encrypted (`*.sops.yaml`); decryption is patched onto every
  Kustomization centrally from each cluster's `kustomization.yaml` — no per-app setup
  needed. Each cluster has its own age key (`.sops.yaml` selects by path); secrets that
  differ per cluster live in that cluster's overlay, never in `base/`.
- Monitoring is a shared base a cluster opts into (`infrastructure/controllers/base/monitoring`,
  `infrastructure/configs/base/flux-notifications`); only the cluster name, chat id, tailnet hostname and secrets
  are overlaid. Cluster-specific checks (backup stack, public URLs) live in that cluster's overlay or in the
  probe workflow's matrix, not in the base. The Prometheus UI is reachable over Tailscale only
  (`docs/monitoring.md`).

Before making non-trivial changes, use the `flux-ops` skill to validate locally
(kustomize render, dry-run, flux diff, helm template) — it also covers common Flux
commands and SOPS secrets management.

## Making a change

```sh
# edit something under infrastructure/ or apps/, validate as above, then:
git add -A && git commit -m "..." && git push
flux reconcile kustomization apps --with-source     # or infra-controllers / infra-configs
flux get kustomizations                             # expect READY=True everywhere
```

The change lands on its own within the reconcile interval; `flux reconcile` just makes
it immediate.

## Developing on a branch

Only point live Flux at a feature branch when you want the cluster to actually run the
change for a while before merging (see [`docs/flux.md`](docs/flux.md) for the full
switch-branch / merge-back procedure). The critical rule:

> Never push a `gotk-sync.yaml` = `main` commit to the branch while the live cluster
> still tracks that branch — Flux follows it to `main` and **prunes everything not yet
> merged**. Set `main` right first (merged), then repoint the cluster's `GitRepository`
> to `main`.

Common Flux commands and secrets management (`sops`) are also covered by the
`flux-ops` skill.
