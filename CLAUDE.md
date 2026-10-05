# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-node [Talos](https://www.talos.dev/) Kubernetes home lab, GitOps-managed by
[Flux](https://fluxcd.io/). Flux watches `main` and reconciles `clusters/laptop/` (and `clusters/tierhive/` for the TierHive VPS cluster) onto
the cluster every 10 minutes. There is no application build/lint/test pipeline — this
repo *is* the cluster's desired state, expressed as Kubernetes manifests, Kustomize
overlays, and Flux `HelmRelease`/`Kustomization` objects.

Full details live in [`docs/flux.md`](docs/flux.md) (day-to-day ops, secrets, branch
workflow) and [`flux-bootstrap/README.md`](flux-bootstrap/README.md) (one-time cluster
bootstrap). Read those before making non-trivial changes — this file only summarizes
what's needed to navigate and validate.

## Layout and reconcile order

```
talos/{laptop,tierhive}/       Talos OS config per cluster: patch + SOPS-encrypted secrets bundle
                               (secrets.sops.yaml); generated configs/kubeconfig are git-ignored.
                               Not applied by Flux — see README.md / docs/tierhive.md
clusters/{laptop,tierhive}/    one Flux entrypoint per cluster; same file set:
  kustomization.yaml    entrypoint: resources + the SOPS decryption patch
  flux-system/           Flux's own manifests, managed by `flux bootstrap` — don't hand-edit
  infrastructure.yaml    Flux Kustomizations: infra-controllers -> infra-configs
  apps.yaml               Flux Kustomization: apps (after infra-controllers, infra-configs)
infrastructure/
  controllers/{base,laptop,tierhive}   operators, ingress (CloudNativePG, Traefik, cert-manager) — reconciled first
  configs/{base,laptop,tierhive}       cluster config (local-path-provisioner, private CA issuer)
apps/{base,laptop,tierhive}            workloads (Immich, Pocket ID OIDC provider — id.home.arpa / id.vps.powple.com;
                                       Pocket ID lives in the `identity` namespace)
```

`controllers` -> `configs` -> `apps`, enforced via `dependsOn` in the Flux
`Kustomization` objects, because operators/CRDs must exist before the resources that
use them. Pocket ID is just another app; other apps opt into SSO individually (configuring
their own OIDC client against it), so there's no ordering between them. If something ever
needs Pocket ID up first, split `apps` into per-app Kustomizations with `dependsOn`.

Every `infrastructure/*` and `apps/` component has `base/` (reusable manifests) +
per-cluster overlays (`laptop/`, `tierhive/`; Kustomize patches). A new cluster reuses
`base/` and only overlays what differs (host, storage class, ingress exposure). The tierhive
cluster differs notably: TLS is terminated by TierHive's managed HAProxy (plain HTTP to
Traefik, no redirect, no cert-manager) — see `docs/tierhive.md`.

**Design conventions to preserve when adding a component:**
- One Flux `HelmRelease` / `Kustomization` per component, not an umbrella chart.
  Order dependencies via `dependsOn`.
- Traefik (hostPort DaemonSet), not ingress-nginx.
- Secrets are SOPS + age encrypted (`*.sops.yaml`); decryption is patched onto every
  Kustomization centrally from each cluster's `kustomization.yaml` — no per-app setup
  needed. Each cluster has its own age key (`.sops.yaml` selects by path); secrets that
  differ per cluster live in that cluster's overlay, never in `base/`.

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
