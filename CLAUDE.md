# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-node [Talos](https://www.talos.dev/) Kubernetes home lab, GitOps-managed by
[Flux](https://fluxcd.io/). Flux watches `main` and reconciles `clusters/laptop/` onto
the cluster every 10 minutes. There is no application build/lint/test pipeline — this
repo *is* the cluster's desired state, expressed as Kubernetes manifests, Kustomize
overlays, and Flux `HelmRelease`/`Kustomization` objects.

Full details live in [`docs/flux.md`](docs/flux.md) (day-to-day ops, secrets, branch
workflow) and [`flux-bootstrap/README.md`](flux-bootstrap/README.md) (one-time cluster
bootstrap). Read those before making non-trivial changes — this file only summarizes
what's needed to navigate and validate.

## Layout and reconcile order

```
clusters/laptop/
  kustomization.yaml    entrypoint: resources + the SOPS decryption patch
  flux-system/           Flux's own manifests, managed by `flux bootstrap` — don't hand-edit
  infrastructure.yaml    Flux Kustomizations: infra-controllers -> infra-configs
  identity.yaml           Flux Kustomization: identity (after infra-controllers, infra-configs)
  apps.yaml               Flux Kustomization: apps (after infra-controllers, infra-configs)
infrastructure/
  controllers/{base,laptop}   operators, ingress (CloudNativePG, Traefik, cert-manager) — reconciled first
  configs/{base,laptop}       cluster config (local-path-provisioner, private CA issuer)
identity/{base,laptop}        SSO — Pocket ID OIDC provider (id.home.arpa)
apps/{base,laptop}            workloads (Immich)
```

`controllers` -> `configs` -> `identity`/`apps`, enforced via `dependsOn` in the Flux
`Kustomization` objects, because operators/CRDs must exist before the resources that
use them. `apps` does not `dependsOn` `identity` — apps opt into SSO individually
(configuring their own OIDC client against Pocket ID), so there's no hard ordering
requirement between the two Kustomizations themselves.

Every `infrastructure/*`, `identity/`, and `apps/` component has `base/` (reusable manifests) +
`laptop/` (per-cluster Kustomize overlay/patches). A future VPS/on-prem cluster reuses
`base/` and only overlays what differs (host, storage class, ingress exposure).

**Design conventions to preserve when adding a component:**
- One Flux `HelmRelease` / `Kustomization` per component, not an umbrella chart.
  Order dependencies via `dependsOn`.
- Traefik (hostPort DaemonSet), not ingress-nginx.
- Secrets are SOPS + age encrypted (`*.sops.yaml`); decryption is patched onto every
  Kustomization centrally from `clusters/laptop/kustomization.yaml` — no per-app setup
  needed.

## Validating changes locally (no cluster mutation, no commit needed)

Iterate against the working tree, then push once — no temp commits.

```sh
# 1. Render a Kustomize overlay — catches path/patch/merge errors
kubectl kustomize apps/laptop/immich

# 2. Server-side dry-run — schema/CRD/admission validation, needs cluster creds, changes nothing
kubectl apply --dry-run=server -k apps/laptop/immich

# 3. flux diff — field-level diff of local files against what's live, including prunes
flux diff kustomization apps --path ./apps/laptop
flux diff kustomization infra-controllers --path ./infrastructure/controllers/laptop
# for a Kustomization that doesn't exist in-cluster yet, add:
#   --kustomization-file clusters/laptop/apps.yaml
# whole-cluster preview:
flux diff kustomization flux-system --path ./clusters/laptop
```

`flux diff` validates the `HelmRelease` object itself but not the chart it renders.
To check chart values against the chart's schema (needs `yq`):

```sh
helm pull oci://ghcr.io/immich-app/immich-charts/immich --version 0.13.1 --untar -d /tmp/chart
yq '.spec.values' apps/base/immich/helmrelease.yaml > /tmp/values.yaml
helm template immich /tmp/chart/immich -n immich -f /tmp/values.yaml
```

Requires `export KUBECONFIG="$PWD/os/context/kubeconfig"` and, for SOPS-encrypted
files, the age private key at `~/.config/sops/age/home-lab.agekey`.

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

## Common Flux commands

```sh
flux get all -A
flux get kustomizations
flux get helmreleases -A
flux reconcile kustomization <name> --with-source
flux reconcile helmrelease <name> -n <ns> --with-source [--force]
flux suspend|resume kustomization <name>       # pause reconciliation for manual debugging
flux tree kustomization <name>                 # resources it applied
flux events --for kustomization/<name>
flux logs -f --level=error
```

## Secrets

```sh
sops --encrypt --in-place apps/base/<app>/foo.sops.yaml   # create
sops apps/base/<app>/foo.sops.yaml                          # edit (decrypts in $EDITOR, re-encrypts on save)
sops -d apps/base/<app>/foo.sops.yaml                        # view plaintext
```

Filename must match `*.sops.yaml`; only `data`/`stringData` fields get encrypted; add
the file to that app's `kustomization.yaml` `resources:`. Encryption rules and the age
public key are in `.sops.yaml` at the repo root.
