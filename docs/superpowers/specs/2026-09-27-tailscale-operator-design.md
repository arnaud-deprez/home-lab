# Tailscale operator for admin access — design

**Date:** 2026-09-27
**Status:** approved (design), pending implementation plan

## Goal

Provide secure remote admin access to this home lab cluster — the Kubernetes
API server and the Talos API (`talosctl`, port 50000) — without exposing
either publicly, and without broadening the cluster's attack surface more
than necessary. Access must follow a whitelist (default-deny) model: only
explicitly granted tailnet identities can reach either target, on exactly the
port needed and nothing else.

Out of scope: exposing application workloads (e.g. Immich) via Tailscale —
those stay on the existing public Traefik + cert-manager path. A Flux/GitOps
web dashboard (e.g. Capacitor) is also out of scope for now; CLI visibility
(`flux get`, `flux logs`) works over the k8s API alone.

## Why the Tailscale Kubernetes operator (not a subnet router)

A subnet router advertises a whole routed CIDR (or at minimum a host `/32`)
to the tailnet; narrowing it to a single port requires an ACL `dst` rule
layered on top of an inherently broader mechanism. The Tailscale Kubernetes
operator instead creates one dedicated tailnet node per exposed target
(a Service, or the k8s API server itself via its built-in proxy), each with
its own hostname and ACL tag. This maps directly onto least-privilege:
nothing is reachable unless a target's tag is explicitly granted, and each
target's exposure is scoped to its own port by construction, not by an ACL
carve-out from a broader route.

This also rules out running `tailscaled` as a Talos system extension on the
node's own OS: that would only relocate *where* the tailnet identity lives,
without changing what a compromised proxy can do, since the Talos API
(`apid`) enforces its own mutual-TLS authentication (Talos-issued client
certs) independent of the network path used to reach it. The operator
approach achieves the same security boundary while staying fully
GitOps-managed — no Talos image rebuild or machine-config upgrade required.

## Architecture

```
tailnet (your devices)
   |
   |  ACL grants (whitelist, default-deny)
   v
+----------------------------------------------------+
| tailscale-operator (in-cluster, namespace           |
| tailscale-operator)                                 |
|                                                      |
|  - joins tailnet as tag:k8s-operator                |
|  - apiServerProxyConfig: proxies kubectl traffic,    |
|    authenticated by caller's tailnet identity        |
|    (impersonation) -> real k8s API server            |
|                                                      |
|  - watches annotated/LoadBalancer-class Services;    |
|    for the Talos API Service below, creates a        |
|    dedicated proxy joining tailnet as tag:talos-api  |
+----------------------------------------------------+
                          |
                          v
              Service (no selector) + Endpoints
              -> <node-ip>:50000 (Talos apid, mTLS)
```

Two distinct tailnet-facing identities result:
- `tag:k8s-apiserver` — the operator's own API-server-proxy identity,
  reachable on 443, proxying to the real k8s API with caller impersonation.
- `tag:talos-api` — a dedicated node for the Talos API Service, reachable
  only on 50000, forwarding to `<node-ip>:50000`.

## Components (new)

`infrastructure/controllers/base/tailscale-operator/`:
- `namespace.yaml` — namespace `tailscale-operator`.
- `helmrepository.yaml` — HelmRepository pointing at
  `https://pkgs.tailscale.com/helmcharts`.
- `helmrelease.yaml` — HelmRelease for chart `tailscale-operator`, with
  `values.apiServerProxyConfig.enabled: true`, `values.oauth.clientId` /
  `values.oauth.clientSecret` referencing the SOPS-encrypted secret below,
  and default tags including `tag:k8s-operator`.
- `tailscale-oauth.sops.yaml` — SOPS-encrypted Secret holding the OAuth
  client ID and secret (age-encrypted, decrypted via the central
  `clusters/laptop/kustomization.yaml` patch, same as
  `pocket-id-secret.sops.yaml`).
- `talos-api-service.yaml` — headless Service (`type: LoadBalancer`,
  `loadBalancerClass: tailscale`, port 50000), annotated with
  `tailscale.com/hostname: talos-api` and `tailscale.com/tags: tag:talos-api`.
- `kustomization.yaml` — lists the above.

`infrastructure/controllers/laptop/`:
- `talos-api-endpoints.yaml` — Endpoints object pointing at
  `<node-ip>:50000` for the Talos API Service above. Kept in the `laptop`
  overlay (not base) since the node's real IP is cluster-specific; `base/`
  stays reusable for a future cluster.
- `kustomization.yaml` — add `../base/tailscale-operator` to `resources`,
  add `talos-api-endpoints.yaml` as an additional resource (it's a new
  object, not a patch to an existing one).

No changes to `infrastructure.yaml`, `dependsOn`, or reconcile ordering: the
operator only needs the `infra-controllers` tier itself (its own namespace,
CRDs installed by its Helm chart), the same as `cert-manager` and `traefik`.

## Secrets & tailnet setup (manual, out-of-band)

Tailscale ACLs and OAuth clients are configured in the Tailscale admin
console / tailnet policy file — there is no Flux-reconciled resource for
them, so these steps are documented here rather than expressed as manifests:

1. Create an OAuth client scoped to `devices:core:write`, pre-authorized for
   tag `tag:k8s-operator`.
2. Define tags `tag:k8s-operator`, `tag:k8s-apiserver`, `tag:talos-api` in
   the tailnet policy file, with `tag:k8s-operator` as the owner of the
   other two (per Tailscale's tag-ownership model — a tag's owner is the
   only identity allowed to assign it to a new device).
3. Add whitelist ACL grants, default-deny for everything else, e.g.:
   ```json
   {
     "grants": [
       {
         "src": ["autogroup:admin"],
         "dst": ["tag:k8s-apiserver"],
         "ip":  ["443"]
       },
       {
         "src": ["autogroup:admin"],
         "dst": ["tag:talos-api"],
         "ip":  ["50000"]
       }
     ]
   }
   ```
   (Replace `autogroup:admin` with the actual user/device group that should
   have admin access, once decided.)
4. Encrypt the OAuth client ID + secret into
   `infrastructure/controllers/base/tailscale-operator/tailscale-oauth.sops.yaml`
   via `sops`, following the `pocket-id-secret.sops.yaml` pattern.

## Error handling / operational notes

- If the OAuth client or ACL tags aren't set up before the HelmRelease is
  applied, the operator pod will crashloop or log auth failures — expected
  and self-resolving once secrets/ACLs are in place; no special handling in
  manifests.
- The Talos API Service has no selector and no pods behind it in the normal
  sense — its Endpoints are manually maintained. If the node's IP ever
  changes (e.g. reprovisioned), `talos-api-endpoints.yaml` must be updated
  by hand; this is a single-node home lab so there's no reconciliation loop
  to keep it in sync automatically.
- ACL misconfiguration (e.g. missing a grant) fails closed — Tailscale's
  default is deny, so a mistake here manifests as "can't reach it," not as
  an unintended opening.

## Testing / validation

1. Local (via `flux-ops` skill conventions):
   - `kustomize build infrastructure/controllers/laptop` renders cleanly.
   - `sops -d` on the new secret file decrypts without error.
   - `helm template` against the tailscale-operator chart + values to
     sanity-check `apiServerProxyConfig` and OAuth value wiring.
2. After push + `flux reconcile kustomization infra-controllers --with-source`:
   - `flux get kustomizations` shows `READY=True`.
   - Operator pod is running; it appears in the Tailscale admin console
     under `tag:k8s-operator`.
3. End-to-end, from an authorized tailnet device:
   - `tailscale status` shows nodes tagged `tag:k8s-apiserver` and
     `tag:talos-api`.
   - `kubectl` via the operator's API-server-proxy context succeeds.
   - `talosctl -e <talos-api-tailnet-ip> version` succeeds.
4. Negative check, from a non-whitelisted tailnet device (or by temporarily
   removing the grant): confirm both targets are unreachable, proving the
   ACLs are actually restrictive rather than merely present.

## Explicitly out of scope

- Public/app exposure via Tailscale (Immich stays on Traefik + cert-manager).
- A Flux/GitOps web dashboard.
- Running `tailscaled` as a Talos system extension (considered and
  rejected — see rationale above).
- A future cloud node joining this tailnet — noted as a motivating future
  use case but not designed here; the ACL tag/grant structure above should
  extend to it without rework (a future cloud node gets its own tag and
  grant, same pattern).
