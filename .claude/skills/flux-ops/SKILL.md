---
name: flux-ops
description: Validate Flux/Kustomize changes locally before pushing (kustomize render, server-side dry-run, flux diff, helm template), run common flux CLI commands, and manage SOPS-encrypted secrets in this home-lab repo.
---

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

Requires `export KUBECONFIG="$PWD/talos/laptop/kubeconfig"` and, for SOPS-encrypted
files, the cluster's age private key (laptop: `~/.config/sops/age/home-lab.agekey`). Each cluster has
its own key, injected as the `sops-age` Secret in that cluster's `flux-system` namespace (see
`docs/flux.md#age-keys-one-per-cluster`).
For the tierhive cluster use `clusters/tierhive` / `*/tierhive` paths, its own kubeconfig (`talos/tierhive/kubeconfig`) and the
age key `~/.config/sops/age/tierhive.agekey`; `sops` needs both keys in
`~/.config/sops/age/keys.txt` (and `export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt`) to edit
either cluster's secrets.

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
sops --encrypt --in-place apps/<cluster>/<app>/foo.sops.yaml   # create
sops apps/<cluster>/<app>/foo.sops.yaml                          # edit (decrypts in $EDITOR, re-encrypts on save)
sops -d apps/<cluster>/<app>/foo.sops.yaml                        # view plaintext
```

Filename must match `*.sops.yaml` and live under a cluster overlay (`laptop/` or `tierhive/`,
never `base/`: no rule matches there); only `data`/`stringData` fields get encrypted; add
the file to that app's `kustomization.yaml` `resources:`. Encryption rules and the age
public keys are in `.sops.yaml` at the repo root.

Talos secrets bundles (`talos/<cluster>/secrets.sops.yaml`) are not Kubernetes manifests: they
are encrypted as a whole file by their own rules, not applied by Flux. See `README.md` (laptop)
and `docs/tierhive.md`.
