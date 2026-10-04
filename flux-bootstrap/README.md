# Bootstrap

One-time steps to bring a fresh cluster under Flux. The same steps apply to every cluster
(`laptop`, `tierhive`, ...): only the variables below change.

The Talos cluster must already be installed and bootstrapped, with its kubeconfig in
`talos/<cluster>/kubeconfig`: see the Talos section of [`../README.md`](../README.md)
(laptop / UTM) or [`../docs/tierhive.md`](../docs/tierhive.md) (TierHive).

## Age keys

Every cluster has **its own age key pair**. The public key is in `.sops.yaml` (rules selected
by path), the private key is stored in your password manager, in
`~/.config/sops/age/<name>.agekey`, and in the cluster as the `sops-age` Secret in
`flux-system` (the secret has the same name in every cluster but holds that cluster's key).

| Cluster    | Private key file                     | In-cluster secret | Rules in `.sops.yaml`                      |
| ---------- | ------------------------------------ | ----------------- | ------------------------------------------ |
| `laptop`   | `~/.config/sops/age/home-lab.agekey` | `sops-age`        | `*/laptop/**`, `talos/laptop/secrets*`     |
| `tierhive` | `~/.config/sops/age/tierhive.agekey` | `sops-age`        | `*/tierhive/**`, `talos/tierhive/secrets*` |

For a **new** cluster, create its key first and add a rule for it (see
[`../docs/flux.md`](../docs/flux.md#age-keys-one-per-cluster)).

## Prerequisites

- `flux`, `sops`, `age`, `kubectl` installed
- the cluster's age private key restored from the password manager to its key file above

## Steps

```sh
export CLUSTER=laptop                                   # or tierhive
export AGE_KEY=$HOME/.config/sops/age/$CLUSTER.agekey
export KUBECONFIG="$PWD/talos/$CLUSTER/kubeconfig"
export GITHUB_TOKEN=...    # fine-grained PAT, Contents + Administration RW
export GITHUB_USER=arnaud-deprez

# 1. Give Flux the cluster's decryption key *before* it reconciles any *.sops.yaml
kubectl create namespace flux-system
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey="$AGE_KEY"

# 2. Bootstrap Flux on this cluster's entrypoint
flux bootstrap github \
  --owner="$GITHUB_USER" --repository=home-lab \
  --branch=main --path=clusters/$CLUSTER --personal
```

Flux then reconciles `clusters/$CLUSTER/` on its own. The bootstrap PAT can be revoked
afterwards — Flux authenticates with the read-only deploy key it created.

Per-cluster extras (TierHive: feature branch while validating, Tailscale OAuth secret to
create beforehand) are in [`../docs/tierhive.md`](../docs/tierhive.md).

## SOPS decryption

`clusters/<cluster>/kustomization.yaml` patches every Flux Kustomization with
`spec.decryption` (provider `sops`, secret `sops-age`), so any `*.sops.yaml` committed under
that cluster's overlays is decrypted at apply time with **that cluster's** key. A secret
encrypted for another cluster's key cannot be decrypted and fails to reconcile. Encryption
rules live in `.sops.yaml` at the repo root.
