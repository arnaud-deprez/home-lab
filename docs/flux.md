# Working with Flux

This cluster is GitOps-managed by [Flux](https://fluxcd.io/). Flux watches
`main` and reconciles `clusters/laptop/` onto the laptop cluster every 10 minutes.
The TierHive VPS cluster uses the same layout under `clusters/tierhive/` (see
[`tierhive.md`](tierhive.md)); replace `laptop` by `tierhive` in the paths below.
Full docs: <https://fluxcd.io/flux/>.

## Prerequisites

- `flux`, `kubectl`, `sops`, `age` — `brew install fluxcd/tap/flux sops age`
- `export KUBECONFIG="$PWD/talos/laptop/kubeconfig"`
- the age private key of the cluster you work on (laptop: `~/.config/sops/age/home-lab.agekey`,
  tierhive: `~/.config/sops/age/tierhive.agekey`), restored from the password manager — see
  [Age keys](#age-keys-one-per-cluster)

## Layout

```
clusters/laptop/
  kustomization.yaml    # explicit entrypoint: resources + the SOPS decryption patch
  flux-system/          # Flux's own manifests — managed by `flux bootstrap`, don't hand-edit
  infrastructure.yaml   # Flux Kustomizations: infra-controllers -> infra-configs
  apps.yaml             # Flux Kustomization: apps (after infra-controllers, infra-configs)
infrastructure/
  controllers/{base,laptop,tierhive}   # operators, ingress   (reconciled first)
  configs/{base,laptop,tierhive}       # storage classes, cluster config
apps/{base,laptop,tierhive}            # workloads (Immich, Pocket ID OIDC provider)
```

`base/` holds reusable definitions; `laptop/` and `tierhive/` are the per-cluster overlays
(Kustomize patches). Ordering: `controllers` -> `configs` -> `apps`, because operators/CRDs must exist before the
resources that use them. Apps reconcile independently once `infra-configs` is ready.

## Make a change

```sh
# edit something under infrastructure/ or apps/
git add -A && git commit -m "..." && git push
flux reconcile kustomization apps --with-source     # or infra-controllers / infra-configs
flux get kustomizations                             # expect READY=True everywhere
```

The change lands on its own within the reconcile interval; `flux reconcile`
just makes it immediate.

## Validate locally before pushing

You don't need to commit to test. Iterate with these against your working
tree, then push once — no temp commits.

**1. Render an overlay** (catches Kustomize path/patch/merge errors, no cluster):

```sh
kubectl kustomize apps/laptop/immich
```

**2. Server-side dry-run** (schema, CRD and admission validation; needs the
cluster, changes nothing):

```sh
kubectl apply --dry-run=server -k apps/laptop/immich
```

**3. `flux diff` — what would actually change.** Renders your **local
files** the way the named Kustomization would, server-side dry-runs them,
and prints a field-level diff against what's live (including prunes):

```sh
flux diff kustomization apps --path ./apps/laptop
flux diff kustomization infra-controllers --path ./infrastructure/controllers/laptop
```

Exit code is `0` when clean, `1` when there are differences. For a
Kustomization that doesn't exist in-cluster yet, add
`--kustomization-file clusters/laptop/apps.yaml`. Whole-cluster preview:
`flux diff kustomization flux-system --path ./clusters/laptop`.

**Limit:** `flux diff` validates the `HelmRelease` object itself, not the
chart it renders (helm-controller does that in-cluster). To check chart
values against the chart's schema locally (`brew install yq` first):

```sh
helm pull oci://ghcr.io/immich-app/immich-charts/immich --version 0.13.1 --untar -d /tmp/chart
yq '.spec.values' apps/base/immich/helmrelease.yaml > /tmp/values.yaml
helm template immich /tmp/chart/immich -n immich -f /tmp/values.yaml
```

Once `flux diff` shows only what you intend: one commit, one push, one
`flux reconcile`.

## Validate in CI

A PR check can render every overlay and schema-validate it with no cluster —
`flux build` piped to [`kubeconform`](https://github.com/yannh/kubeconform).
See the workflow in
[fluxcd/flux2-kustomize-helm-example](https://github.com/fluxcd/flux2-kustomize-helm-example/tree/main/.github/workflows).

## Develop on a branch

For most changes, **local validation above is enough** — `flux diff` against
your working tree, then one commit to `main`. Point Flux at a branch only
when you want the cluster to actually run the change for a while before
merging (a new app, a risky upgrade).

Flux tracks `main`. To have it track a branch:

```sh
git switch -c feat/x
# edit clusters/laptop/flux-system/gotk-sync.yaml -> spec.ref.branch: feat/x
git add -A && git commit -m "tmp(flux): track feat/x" && git push -u origin feat/x
kubectl -n flux-system patch gitrepository flux-system --type=merge \
  -p '{"spec":{"ref":{"branch":"feat/x"}}}'
flux reconcile kustomization flux-system --with-source
```

Then iterate: edit -> commit -> push -> `flux reconcile kustomization <name> --with-source`.

**At merge time — order matters:**

```sh
# 1. on the branch: set gotk-sync.yaml spec.ref.branch back to `main`, commit
# 2. merge and push
git switch main && git merge feat/x && git push
# 3. repoint the live cluster
kubectl -n flux-system patch gitrepository flux-system --type=merge \
  -p '{"spec":{"ref":{"branch":"main"}}}'
flux reconcile kustomization flux-system --with-source
git branch -D feat/x            # squash-merged branches aren't seen as merged
```

> ⚠️ Never push a `gotk-sync.yaml` = `main` commit to the branch while the
> live cluster still tracks that branch. Flux follows it to `main` and
> **prunes everything not yet merged**. Get `main` right first, then repoint.

## Secrets (SOPS + age)

Decryption is applied to every Flux Kustomization by the patch in
`clusters/<cluster>/kustomization.yaml` — no per-app setup. `.sops.yaml` at the
repo root holds the encryption rules and the age **public** keys: one key per cluster,
selected by path (`*/tierhive/**` → tierhive key, `*/laptop/**` → laptop key). Talos secrets
bundles (`talos/<cluster>/secrets.sops.yaml`) follow the same per-cluster keys but are encrypted
as a whole file (no `encrypted_regex`) and are not applied by Flux. Secrets that
differ per cluster live in that cluster's overlay, not in `base/`. To edit both clusters'
secrets put both private keys in `~/.config/sops/age/keys.txt` and
`export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt` (on macOS sops does not look there by default).
**Never put `*.sops.yaml` in `base/`**: no rule matches there, so `sops` refuses to create it (a secret
shared by both clusters would need both recipients added to a dedicated rule).

### Age keys (one per cluster)

Each cluster decrypts with its own key, so compromising one cluster does not expose the
other's secrets.

| Cluster    | Key file                             | `.sops.yaml` rules                        |
| ---------- | ------------------------------------ | ----------------------------------------- |
| `laptop`   | `~/.config/sops/age/home-lab.agekey` | `*/laptop/**`, `talos/laptop/secrets*`    |
| `tierhive` | `~/.config/sops/age/tierhive.agekey` | `*/tierhive/**`, `talos/tierhive/secrets*` |

- **Injected into Flux** as the `sops-age` Secret (key `age.agekey`) in the `flux-system`
  namespace of *that cluster* — same name everywhere, different content. The
  `clusters/<cluster>/kustomization.yaml` patch points every Kustomization at it
  (`spec.decryption.secretRef.name: sops-age`). Steps:
  [`../flux-bootstrap/README.md`](../flux-bootstrap/README.md).
- **Add a cluster:** `age-keygen -o ~/.config/sops/age/<cluster>.agekey`; add its public key as
  two rules in `.sops.yaml` (`(^|/)<cluster>/.*\.sops\.ya?ml$` with
  `encrypted_regex: ^(data|stringData)$`, and a whole-file rule for `talos/<cluster>/secrets*`);
  store the private key in the password manager.
- **Edit secrets of several clusters:** concatenate the key files into one,
  `cat ~/.config/sops/age/*.agekey > ~/.config/sops/age/keys.txt`, and
  `export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt` (see above).

Create an encrypted secret:

```sh
cat > apps/<cluster>/<app>/foo.sops.yaml <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: foo
  namespace: <app>
stringData:
  token: the-plaintext-value
EOF
sops --encrypt --in-place apps/<cluster>/<app>/foo.sops.yaml
# add foo.sops.yaml to that app's kustomization.yaml `resources:`
git add apps/<cluster>/<app>/foo.sops.yaml && git commit -m "..." && git push
```

- Edit later: `sops apps/<cluster>/<app>/foo.sops.yaml` (decrypts in `$EDITOR`, re-encrypts on save)
- View: `sops -d apps/<cluster>/<app>/foo.sops.yaml`
- Filename must match `*.sops.yaml`; only `data` / `stringData` get encrypted (Kubernetes
  secrets; Talos bundles are fully encrypted)
- Rotate a cluster's age key: generate a new key, update that cluster's rules in `.sops.yaml`,
  run `sops updatekeys` on that cluster's `*.sops.yaml` files (including
  `talos/<cluster>/secrets.sops.yaml`), recreate the `sops-age` secret **in that cluster**
  (`kubectl -n flux-system create secret generic sops-age --from-file=age.agekey=<key> --dry-run=client -o yaml | kubectl apply -f -`)

Guide: <https://fluxcd.io/flux/guides/mozilla-sops/>

## Common commands

```sh
flux get all -A
flux get kustomizations
flux get helmreleases -A
flux reconcile kustomization <name> --with-source
flux reconcile helmrelease <name> -n <ns> --with-source [--force]
flux suspend|resume kustomization <name>       # pause reconciliation for manual debugging
flux tree kustomization <name>                 # resources it applied
flux events --for kustomization/<name>
flux logs -f --level=error                     # controller logs
```

## New cluster

See [`../flux-bootstrap/README.md`](../flux-bootstrap/README.md).

## Upgrade Flux

```sh
brew upgrade fluxcd/tap/flux
# once per cluster: use that cluster's kubeconfig and --path
export KUBECONFIG="$PWD/talos/<cluster>/kubeconfig"
flux bootstrap github --owner=arnaud-deprez --repository=home-lab \
  --branch=main --path=clusters/<cluster> --personal   # regenerates flux-system/
```

Docs: <https://fluxcd.io/flux/installation/upgrade/>
