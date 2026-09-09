# Working with Flux

This cluster is GitOps-managed by [Flux](https://fluxcd.io/). Flux watches
`main` and reconciles `clusters/laptop/` onto the cluster every 10 minutes.
Full docs: <https://fluxcd.io/flux/>.

## Prerequisites

- `flux`, `kubectl`, `sops`, `age` — `brew install fluxcd/tap/flux sops age`
- `export KUBECONFIG="$PWD/os/context/kubeconfig"`
- age private key at `~/.config/sops/age/home-lab.agekey` (restore from the
  password manager)

## Layout

```
clusters/laptop/
  kustomization.yaml    # explicit entrypoint: resources + the SOPS decryption patch
  flux-system/          # Flux's own manifests — managed by `flux bootstrap`, don't hand-edit
  infrastructure.yaml   # Flux Kustomizations: infra-controllers -> infra-configs
  apps.yaml             # Flux Kustomization: apps (after infra-controllers)
infrastructure/
  controllers/{base,laptop}   # operators, ingress   (reconciled first)
  configs/{base,laptop}       # storage classes, cluster config
apps/{base,laptop}            # workloads
```

`base/` holds reusable definitions; `laptop/` is the per-cluster overlay
(Kustomize patches). Ordering: `controllers` -> `configs` -> `apps`, because
operators/CRDs must exist before the resources that use them.

## Make a change

```sh
# edit something under infrastructure/ or apps/
git add -A && git commit -m "..." && git push
flux reconcile kustomization apps --with-source     # or infra-controllers / infra-configs
flux get kustomizations                             # expect READY=True everywhere
```

The change lands on its own within the reconcile interval; `flux reconcile`
just makes it immediate.

## Develop on a branch

Flux tracks `main`. To validate changes on a branch first, point Flux at it:

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
`clusters/laptop/kustomization.yaml` — no per-app setup. `.sops.yaml` at the
repo root holds the encryption rules and the age **public** key.

Create an encrypted secret:

```sh
cat > apps/base/<app>/foo.sops.yaml <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: foo
  namespace: <app>
stringData:
  token: the-plaintext-value
EOF
sops --encrypt --in-place apps/base/<app>/foo.sops.yaml
# add foo.sops.yaml to that app's kustomization.yaml `resources:`
git add apps/base/<app>/foo.sops.yaml && git commit -m "..." && git push
```

- Edit later: `sops apps/base/<app>/foo.sops.yaml` (decrypts in `$EDITOR`, re-encrypts on save)
- View: `sops -d apps/base/<app>/foo.sops.yaml`
- Filename must match `*.sops.yaml`; only `data` / `stringData` get encrypted
- Rotate the age key: generate a new key, update `.sops.yaml`, run
  `sops updatekeys` on each `*.sops.yaml`, recreate the `sops-age` secret

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

See [`../bootstrap/README.md`](../bootstrap/README.md).

## Upgrade Flux

```sh
brew upgrade fluxcd/tap/flux
flux bootstrap github --owner=arnaud-deprez --repository=home-lab \
  --branch=main --path=clusters/laptop --personal   # regenerates flux-system/
```

Docs: <https://fluxcd.io/flux/installation/upgrade/>
