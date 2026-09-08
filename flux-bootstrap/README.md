# Bootstrap

One-time steps to bring a fresh cluster under Flux.

## Prerequisites

- `flux`, `sops`, `age`, `kubectl` installed
- `KUBECONFIG` pointing at the target cluster
- age key restored from the password manager to
  `~/.config/sops/age/home-lab.agekey`

## Steps (laptop)

```sh
export KUBECONFIG="$PWD/os/context/kubeconfig"
export GITHUB_TOKEN=...    # fine-grained PAT, Contents + Administration RW
export GITHUB_USER=arnaud-deprez

flux bootstrap github \
  --owner="$GITHUB_USER" --repository=home-lab \
  --branch=main --path=clusters/laptop --personal

cat ~/.config/sops/age/home-lab.agekey |
  kubectl create secret generic sops-age -n flux-system \
    --from-file=age.agekey=/dev/stdin
```

Flux then reconciles `clusters/laptop/` on its own. The bootstrap PAT can
be revoked afterwards — Flux authenticates with the read-only deploy key
it created.

## SOPS decryption

`clusters/laptop/flux-system/kustomization.yaml` patches the `flux-system`
Kustomization with `spec.decryption` (provider `sops`, secret `sops-age`),
so any `*.sops.yaml` committed under `clusters/laptop/` is decrypted at
apply time. Encryption rules live in `.sops.yaml` at the repo root.
