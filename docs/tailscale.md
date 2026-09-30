# Tailscale — remote admin access

The Tailscale Kubernetes operator (`infrastructure/controllers/base/tailscale-operator/`)
gives whitelisted tailnet identities remote access to two things, without
exposing either publicly:

- The **Kubernetes API server**, via the operator's built-in in-process
  API-server-proxy (`apiServerProxyConfig.mode: "true"`). The proxy
  impersonates the caller's tailnet identity against real Kubernetes RBAC.
- The **Talos API** (`talosctl`, port 50000), via a Service with no
  selector and manually-maintained `Endpoints` pointing at the node's real
  IP — exposed to the tailnet through the operator's L3 ingress mechanism
  (`loadBalancerClass: tailscale`), same as any other Service.

Each gets its own tailnet identity/tag (`tag:k8s-operator` for the proxy,
`tag:talos-api` for the Talos API Service) so ACL grants can be scoped
per-target — a whitelist, not a broad subnet route. Application workloads
(Immich, etc.) are **not** exposed this way; they stay on the public
Traefik + cert-manager path.

## One-time tailnet setup (not Flux-managed)

Tailscale ACLs, tags, and OAuth clients live in the tailnet policy file /
admin console — there's no Flux-reconciled resource for them.

1. **Enable HTTPS certificates**: [DNS admin console
   page](https://login.tailscale.com/admin/dns) → enable "HTTPS
   Certificates". Required for the API-server-proxy's TLS cert.
2. **Tag ownership** — [Access
   controls](https://login.tailscale.com/admin/acls/file), `tagOwners`:
   ```json
   "tagOwners": {
     "tag:k8s-operator": [],
     "tag:k8s": ["tag:k8s-operator"],
     "tag:talos-api": ["tag:k8s-operator"]
   }
   ```
3. **Whitelist grants** (same policy file):
   ```json
   "grants": [
     { "src": ["you@example.com"], "dst": ["tag:k8s-operator"], "ip": ["tcp:443"] },
     { "src": ["you@example.com"], "dst": ["tag:talos-api"], "ip": ["tcp:50000"] }
   ]
   ```
4. **Remove any default allow-all rule.** A fresh tailnet's policy file
   ships with `"acls": [{"action": "accept", "src": ["*"], "dst":
   ["*:*"]}]`. `acls` and `grants` both apply — a leftover allow-all
   entry makes the grants above meaningless (every device can reach
   everything). Delete it, or narrow it, before relying on the grants as
   an actual boundary. Confirm with a negative test: from a second
   identity/device not in the `src` list, `tailscale ping talos-api`
   should fail. Alternatively, add policy `tests` — the admin console
   refuses to save a policy that fails its own tests:
   ```json
   "tests": [
     { "src": "tag:talos-api", "deny": ["tag:k8s-operator:443"] },
     { "src": "tag:k8s-operator", "deny": ["tag:talos-api:50000"] }
   ]
   ```
5. **Create an OAuth client** — [Trust credentials → OAuth
   clients](https://login.tailscale.com/admin/settings/trust-credentials),
   `write` scope for `General/Services`, `Devices/Core`, `Keys/Auth Keys`,
   scoped to `tag:k8s-operator`. Copy the client ID/secret (shown once).
6. **Encrypt the OAuth credentials**:
   ```sh
   sops infrastructure/controllers/<cluster>/operator-oauth.sops.yaml   # laptop or tierhive
   ```
   Each cluster has its own secret (and its own OAuth client) in its overlay, encrypted to
   that cluster's age key, and the file must be listed in that overlay's `kustomization.yaml`.
   Replace the placeholder `client_id`/`client_secret` values, save. The
   Secret must be named exactly `operator-oauth` with those two keys — this
   is a hardcoded fallback in the operator's Helm chart (used because
   `oauth.clientId`/`clientSecret` are deliberately left unset in the
   HelmRelease, to keep the OAuth secret SOPS-encrypted rather than
   templated in plaintext).

## Connecting

```sh
# Kubernetes API, over the tailnet — impersonates your tailnet identity
tailscale configure kubeconfig tailscale-operator
kubectl get nodes
```

The first request after the proxy (re)starts provisions its TLS cert on
demand and can time out — just retry once.

```sh
# Talos API — use the tailnet address shown for the talos-api Service
kubectl get service talos-api -n tailscale-operator   # EXTERNAL-IP / MagicDNS name
talosctl -e <talos-api-tailnet-address> -n 192.168.64.5 version   # tierhive: -n 10.10.8.2
```

RBAC for the impersonated identity is a normal `ClusterRoleBinding`
(`infrastructure/controllers/base/tailscale-operator/clusterrolebinding.yaml`)
— the proxy alone grants zero Kubernetes permissions by itself.

## Known gotchas (hit during setup, worth knowing before you debug them again)

- **`requested tags [...] are invalid or not permitted (400)`** in the
  operator's logs means the tailnet policy file's `tagOwners` block
  doesn't actually grant `tag:k8s-operator` ownership of the tag being
  requested — check for a missing entry or typo (`tag:talos-api` vs.
  `tag:talos_api`, etc.).
- **`FailedCreate ... violates PodSecurity "baseline:latest"`** on the
  operator's ingress-proxy `StatefulSet`: its containers need privileged
  mode for iptables/nftables DNAT, which this cluster's default `baseline`
  Pod Security Standard rejects. Fixed by labeling the operator's
  namespace `pod-security.kubernetes.io/enforce: privileged` — same fix
  already applied to `infrastructure/controllers/base/traefik/namespace.yaml`
  for its hostPort DaemonSet, same underlying reason.
- **The Talos API `Endpoints` IP is not kept in sync by anything.** It's
  hardcoded in `infrastructure/controllers/laptop/talos-api-endpoints.yaml`
  (`192.168.64.5`, matching the node's kubeconfig server address). If the
  node's address ever changes, update it there by hand. The tierhive cluster has its own
  file (`infrastructure/controllers/tierhive/talos-api-endpoints.yaml`, `10.10.8.2`) and uses the
  names `tailscale-operator-tierhive` / `talos-api-tierhive` (`tailscale configure kubeconfig
  tailscale-operator-tierhive`).

## Explicitly out of scope

- Public/app exposure via Tailscale (Immich stays on Traefik + cert-manager).
- A Flux/GitOps web dashboard (CLI visibility — `flux get`, `flux logs` —
  works fine over the k8s API access above).
- Running `tailscaled` as a Talos system extension on the node's own OS:
  considered and rejected. It would only relocate *where* the tailnet
  identity lives, not change what a compromised proxy can do, since the
  Talos API (`apid`) enforces its own mutual-TLS (Talos-issued client
  certs) independent of the network path used to reach it. The operator
  approach gets the same security boundary while staying fully
  GitOps-managed.
