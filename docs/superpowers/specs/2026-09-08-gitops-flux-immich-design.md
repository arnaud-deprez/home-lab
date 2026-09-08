# GitOps with Flux + Immich — Design

Date: 2026-09-08
Status: Approved for planning
Scope: `laptop` cluster only. Multi-environment support is a structural
concern (the layout must not need reshaping later) but no second cluster
is built here.

## Goal

Adopt Flux as the GitOps reconciler for the home lab, validate it end to
end on the laptop cluster, then deploy Immich through it — the Helm
release plus its external dependencies (Postgres via CloudNativePG,
Valkey via the chart's embedded subchart).

## Why not an umbrella chart

Umbrella / parent charts are for shipping a coordinated product bundle,
not for installing an app plus its databases into a cluster. Their
weaknesses here: subcharts are only configurable through the parent, no
ordering or health-gating between components, awkward CRD lifecycle,
manual dependency version bumps. The current mainstream pattern is one
Flux `HelmRelease` per chart, dependencies expressed with `dependsOn`,
stateful dependencies run by operators (CloudNativePG), chart versions
bumped by Renovate PRs. This is also what Immich's own docs assume.

## Repository layout

Monorepo, Kustomize `base/` catalog + per-cluster overlays. `base/` holds
reusable components; each cluster overlay references only the subset it
wants.

```
home-lab/
├─ os/                          # Talos config — outside Flux, unchanged
├─ bootstrap/
│  └─ README.md                 # one-time: install Flux, apply age key, reconcile
├─ clusters/
│  └─ laptop/
│     ├─ flux-system/           # bootstrap-generated
│     ├─ infrastructure.yaml    # Flux Kustomization → infrastructure overlays
│     └─ apps.yaml              # Flux Kustomization → apps/laptop (dependsOn: infrastructure)
├─ infrastructure/
│  ├─ controllers/
│  │  ├─ base/                  # cnpg-operator (catalog; more added later)
│  │  └─ laptop/                # overlay: opts into cnpg-operator
│  └─ configs/
│     ├─ base/                  # local-path-provisioner (from day1/), other cluster config
│     └─ laptop/                # overlay: opts into local-path-provisioner, sets it default SC
└─ apps/
   ├─ base/
   │  └─ immich/
   │     ├─ namespace.yaml
   │     ├─ ocirepository.yaml
   │     ├─ helmrelease.yaml
   │     ├─ postgres-cluster.yaml
   │     ├─ library-pvc.yaml
   │     └─ kustomization.yaml
   └─ laptop/
      └─ immich/
         ├─ kustomization.yaml
         ├─ helmrelease-patch.yaml
         └─ secrets.sops.yaml
```

Multi-env later: add `clusters/hetzner/`, `infrastructure/*/hetzner/`,
`apps/hetzner/immich/` — new overlays, no change to `base/` or `laptop/`.

## Layer wiring (laptop)

- `clusters/laptop/infrastructure.yaml` — Flux Kustomization reconciling
  `infrastructure/controllers/laptop` then `infrastructure/configs/laptop`
  (configs `dependsOn` controllers so CRDs exist before CRs).
- `clusters/laptop/apps.yaml` — Flux Kustomization for `apps/laptop`,
  `dependsOn: infrastructure`, so Immich never applies before the CNPG
  operator and storage class exist.
- Each Flux Kustomization uses `wait: true` / health checks to gate the
  next layer.

## Secrets

SOPS + age.

- One age keypair generated locally. Private key stored only as a cluster
  Secret (`sops-age` in `flux-system`) and backed up in the user's
  password manager. Never committed.
- `.sops.yaml` at repo root with a creation rule matching
  `.*secrets\.sops\.yaml` → encrypt with the age recipient.
- Flux `kustomize-controller` configured with
  `decryption.provider: sops` referencing the `sops-age` Secret.
- For local iteration before secrets are wired, a plain gitignored Secret
  manifest is acceptable.

## Delivery phases

### Phase 1 — Flux on the laptop, validated

1. Pre-reqs: `flux` CLI, `kubeconfig` pointing at the Talos cluster,
   `flux check --pre`.
2. Bootstrap Flux against the GitHub repo (`flux bootstrap github`),
   targeting `clusters/laptop`. Needs a GitHub PAT (repo scope) once.
3. Generate the age key, create the `sops-age` Secret, add `.sops.yaml`,
   patch `kustomize-controller` for SOPS decryption.
4. Migrate `day1/local-path-provisioner/` into
   `infrastructure/configs/base/local-path-provisioner/` (kustomization
   unchanged in substance). Add `infrastructure/configs/laptop` overlay
   that includes it. Add `infrastructure/controllers/{base,laptop}` with
   the CNPG operator HelmRelease/OCIRepository.
5. Add `clusters/laptop/infrastructure.yaml` and (empty-target for now or
   deferred) `apps.yaml`.
6. Validate: `flux get kustomizations` all Ready; local-path-provisioner
   pods running; `storageclass local-path` is default; a throwaway PVC
   binds. Confirm reconciliation by editing a value in Git and watching
   Flux apply it, and by reverting a manual `kubectl` change and watching
   Flux restore it.
7. Update `day1/README.md` / `bootstrap/README.md` to reflect the new
   flow; remove the superseded manual `kustomize build | kubectl apply`
   instructions for local-path-provisioner.

Exit criteria: Flux reconciles the infrastructure layer from Git,
local-path-provisioner runs under Flux, drift correction observed.

### Phase 2 — Immich

1. `apps/base/immich/`:
   - `namespace.yaml` — `immich` namespace.
   - `ocirepository.yaml` — `oci://ghcr.io/immich-app/immich-charts`,
     pinned chart version.
   - `postgres-cluster.yaml` — CNPG `Cluster`, image
     `ghcr.io/tensorchord/cloudnative-vectorchord` (pinned), 1 instance,
     storage on `local-path`, DB + owner from the SOPS secret.
   - `library-pvc.yaml` — RWO PVC on `local-path` for the photo library.
   - `helmrelease.yaml` — `valkey.enabled: true`; `image.tag` pinned;
     `immich.persistence.library.existingClaim` → the PVC; DB env
     pointing at the CNPG service; `env` for the VectorChord extension
     per chart docs.
   - `kustomization.yaml` — ties the above together.
2. `apps/laptop/immich/`:
   - `kustomization.yaml` → `../../base/immich`.
   - `helmrelease-patch.yaml` — ingress host for laptop access, 1
     replica, modest resource limits, PVC sizes, no CNPG backup.
   - `secrets.sops.yaml` — Postgres credentials, Immich secrets.
3. Point `clusters/laptop/apps.yaml` at `apps/laptop`.
4. Validate: `flux get helmreleases -n immich` Ready; CNPG `Cluster`
   healthy; Immich pods up; web UI reachable; upload a photo; restart
   pods and confirm the library and DB persist.

Exit criteria: Immich reachable and persistent on the laptop, fully
reconciled from Git.

## Open questions for the plan

- Ingress: is there an ingress controller yet, or does Phase 1 also add
  one (Traefik / ingress-nginx) to `infrastructure/controllers`? If none,
  Phase 2 can start with a `NodePort` / `port-forward` and add ingress
  later.
- GitHub repo: bootstrap needs the repo to be on GitHub with a PAT.
  Confirm the repo is pushed there.
- CNPG chart vs. plain manifests for the operator install.

## Non-goals

- Second/third clusters.
- Renovate automation (worth adding after Phase 2).
- Backups of the Immich library and database (separate design).
- Tailscale, reverse proxy, Nextcloud (later, same pattern).
