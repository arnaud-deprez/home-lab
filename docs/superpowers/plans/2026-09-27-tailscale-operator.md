# Tailscale Operator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the Tailscale Kubernetes operator so the k8s API server and the Talos API (port 50000) are reachable from an authorized tailnet device, each under its own ACL tag, with everything else default-denied.

**Architecture:** One new Flux-managed component (`infrastructure/controllers/base/tailscale-operator/`) installs the operator via HelmRelease with its built-in in-process API-server-proxy enabled (`apiServerProxyConfig.mode: "true"`), authenticated to the tailnet via a SOPS-encrypted `operator-oauth` Secret. A second Service (base) + Endpoints (laptop overlay, node-IP-specific) exposes the Talos API as its own tailnet node via the operator's L3 ingress mechanism (`loadBalancerClass: tailscale`), tagged separately (`tag:talos-api`) from the operator itself (`tag:k8s-operator`). A ClusterRoleBinding grants the operator's impersonated caller identity access to the real k8s API, since the proxy alone grants no RBAC by default.

**Tech Stack:** Flux (HelmRelease/HelmRepository/Kustomization), Helm chart `tailscale-operator` v1.102.4 from `https://pkgs.tailscale.com/helmcharts`, SOPS + age for secrets, Kustomize overlays (`base`/`laptop`).

**Spec:** `docs/superpowers/specs/2026-09-27-tailscale-operator-design.md`

## Global Constraints

- Chart: `tailscale-operator` version `1.102.4`, repo `https://pkgs.tailscale.com/helmcharts` (matches spec's "Tailscale Kubernetes operator").
- Namespace for the operator and its Secret/HelmRelease: `tailscale-operator` (per spec's Components section).
- Node's real IP (from `os/context/kubeconfig`): `192.168.64.5`. Talos API port: `50000`.
- The oauth Secret **must** be named exactly `operator-oauth` in the operator's namespace with keys `client_id` and `client_secret` — this is a hardcoded fallback in the chart's `deployment.yaml` (confirmed by reading the chart template), triggered only when `values.oauth.clientId` is left unset. Do not set `oauth.clientId`/`oauth.clientSecret` in the HelmRelease values — that would require the plaintext OAuth secret to appear in the HelmRelease manifest itself, defeating the SOPS pattern.
- Tags: `tag:k8s-operator` (operator device), `tag:talos-api` (Talos API Service's dedicated proxy). Per spec, no `tag:k8s-apiserver` device is separately created — the in-process API-server-proxy runs *as* the operator itself and is reached at `tag:k8s-operator`'s address, not a separate tagged device.
- All new SOPS files must match `*.sops.yaml` and only touch `data`/`stringData` fields, per the repo's `.sops.yaml` `creation_rules` (`encrypted_regex: ^(data|stringData)$`).
- Follow existing repo conventions exactly: `namespace.yaml` + `helmrepository.yaml` + `helmrelease.yaml` + `kustomization.yaml` shape (see `infrastructure/controllers/base/cert-manager/`), and the `stringData:` SOPS shape (see `identity/base/pocket-id/pocket-id-secret.sops.yaml`).
- No changes to `infrastructure.yaml`, `dependsOn`, or Flux Kustomization ordering — this component only needs the `infra-controllers` tier.

## Review Focus

- **Missing RBAC binding**: the spec documents the operator and the Service/Endpoints, but Tailscale's own docs are explicit that "access to the proxy over the tailnet does not grant users any default permissions to the Kubernetes API" — without a ClusterRoleBinding, the whole point of Task 1–3 (kubectl access) silently doesn't work. Task 4 adds and tests this explicitly.
- **HTTPS not enabled on the tailnet**: the in-process API-server-proxy provisions a TLS cert for its MagicDNS hostname; if the tailnet's HTTPS certificates feature isn't turned on in the admin console first, the proxy will be reachable but TLS handshakes will fail. Task 1's manual checklist calls this out and Task 6's validation checks for it explicitly (not just "pod is running").
- **OAuth secret filename/key typo**: since the Secret name (`operator-oauth`) and its keys (`client_id`, `client_secret`) are a hardcoded chart fallback rather than a configurable value, a typo in any of the three silently degrades to "operator pod exists but never authenticates" rather than a rendering error `kustomize build` would catch. Task 2 verifies the decrypted Secret's exact name and keys as its own step, separate from the general "sops decrypts without error" check.
- **ACL fails open instead of closed**: it's easy to write a `grants` rule that's broader than intended (e.g. `src: ["*"]` instead of a specific group) and have it look correct in `kustomize build`/`flux diff` (neither validates ACLs — they live outside this repo). Task 6 includes an explicit negative test from a non-whitelisted device/context, not just a positive "it works" check.
- **Talos API Endpoints drift**: the Endpoints object hardcodes `192.168.64.5:50000` with no controller keeping it in sync (spec's own "Error handling" section flags this). Task 3's test step confirms the literal IP matches the live node's current address (`kubectl get nodes -o wide` or the kubeconfig server field) at implementation time, so a stale copy-paste is caught before commit, not after.

---

### Task 1: Tailnet setup (manual, out-of-band) — tags, OAuth client, HTTPS

**Files:** None (no repo changes this task — this is admin-console configuration that later tasks depend on).

**Interfaces:**
- Produces: an OAuth client ID + secret (used as literal input to Task 2), and a tailnet policy file containing the `tagOwners` and `grants` blocks below (used as-is; no further changes needed for these tags in later tasks).

- [ ] **Step 1: Enable HTTPS certificates for the tailnet**

In the [DNS admin console page](https://login.tailscale.com/admin/dns), enable "HTTPS Certificates". This is required for the API-server-proxy's automatic TLS certificate provisioning (Review Focus item 2).

- [ ] **Step 2: Add tag ownership to the tailnet policy file**

In [Access controls](https://login.tailscale.com/admin/acls/file), add (merge into the existing `tagOwners` block if one exists):

```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s": ["tag:k8s-operator"],
  "tag:talos-api": ["tag:k8s-operator"]
}
```

- [ ] **Step 3: Add whitelist grants**

In the same policy file, add (replace `"arnaudeprez@gmail.com"` if a different tailnet identity should have admin access):

```json
"grants": [
  {
    "src": ["arnaudeprez@gmail.com"],
    "dst": ["tag:k8s-operator"],
    "ip": ["tcp:443"]
  },
  {
    "src": ["arnaudeprez@gmail.com"],
    "dst": ["tag:talos-api"],
    "ip": ["tcp:50000"]
  }
]
```

- [ ] **Step 4: Create the OAuth client**

In [Trust credentials → OAuth clients](https://login.tailscale.com/admin/settings/trust-credentials), create a client with `write` scope for `General/Services`, `Devices/Core`, and `Keys/Auth Keys`, all scoped to `tag:k8s-operator`. Copy the generated **client ID** and **client secret** — the secret is shown only once.

- [ ] **Step 5: Save the credentials somewhere you can paste from in Task 2**

Not into the repo — a password manager or scratch note. Task 2 consumes these as literal `sops` edit input, not as a repo file.

---

### Task 2: Scaffold the tailscale-operator base component

**Files:**
- Create: `infrastructure/controllers/base/tailscale-operator/namespace.yaml`
- Create: `infrastructure/controllers/base/tailscale-operator/helmrepository.yaml`
- Create: `infrastructure/controllers/base/tailscale-operator/helmrelease.yaml`
- Create: `infrastructure/controllers/base/tailscale-operator/operator-oauth.sops.yaml`
- Create: `infrastructure/controllers/base/tailscale-operator/kustomization.yaml`

**Interfaces:**
- Consumes: OAuth client ID/secret from Task 1, Step 4.
- Produces: namespace `tailscale-operator`, HelmRelease `tailscale-operator` (chart `tailscale-operator` v1.102.4), Secret `operator-oauth` in that namespace with keys `client_id`/`client_secret`. Task 3 adds resources into the same namespace and same `kustomization.yaml`.

- [ ] **Step 1: Create the namespace manifest**

`infrastructure/controllers/base/tailscale-operator/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: tailscale-operator
```

- [ ] **Step 2: Create the HelmRepository manifest**

`infrastructure/controllers/base/tailscale-operator/helmrepository.yaml`:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: tailscale
  namespace: tailscale-operator
spec:
  interval: 24h
  url: https://pkgs.tailscale.com/helmcharts
```

- [ ] **Step 3: Create the HelmRelease manifest**

`infrastructure/controllers/base/tailscale-operator/helmrelease.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: tailscale-operator
  namespace: tailscale-operator
spec:
  interval: 30m
  chart:
    spec:
      chart: tailscale-operator
      version: "1.102.4"
      sourceRef:
        kind: HelmRepository
        name: tailscale
  install:
    crds: CreateReplace
  upgrade:
    crds: CreateReplace
    remediation:
      retries: 3
  values:
    operatorConfig:
      defaultTags:
        - "tag:k8s-operator"
    apiServerProxyConfig:
      mode: "true"
```

Note: `oauth.clientId`/`oauth.clientSecret` are deliberately absent from these values — the chart falls back to mounting a pre-existing Secret named `operator-oauth`, which Step 4 creates.

- [ ] **Step 4: Create the OAuth Secret (plaintext placeholder first)**

`infrastructure/controllers/base/tailscale-operator/operator-oauth.sops.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
    name: operator-oauth
    namespace: tailscale-operator
stringData:
    client_id: "REPLACE_WITH_OAUTH_CLIENT_ID"
    client_secret: "REPLACE_WITH_OAUTH_CLIENT_SECRET"
```

- [ ] **Step 5: Encrypt the Secret with the real credentials**

```bash
sops infrastructure/controllers/base/tailscale-operator/operator-oauth.sops.yaml
```

This opens `$EDITOR` with the decrypted content. Replace the two placeholder values with the real OAuth client ID/secret from Task 1, Step 4, save, and exit — `sops` re-encrypts in place on save.

- [ ] **Step 6: Verify the encrypted file's shape**

```bash
sops -d infrastructure/controllers/base/tailscale-operator/operator-oauth.sops.yaml
```

Expected: decrypts cleanly, and shows exactly `metadata.name: operator-oauth`, `metadata.namespace: tailscale-operator`, and `stringData` keys `client_id`/`client_secret` with no typos (Review Focus item 3 — this is the step that catches it, since a typo here doesn't fail `kustomize build`).

- [ ] **Step 7: Create the kustomization**

`infrastructure/controllers/base/tailscale-operator/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - helmrepository.yaml
  - helmrelease.yaml
  - operator-oauth.sops.yaml
```

- [ ] **Step 8: Render and verify**

```bash
kubectl kustomize infrastructure/controllers/base/tailscale-operator
```

Expected: renders all four objects with no errors, and the `Secret`'s `stringData` values appear as SOPS `ENC[...]` ciphertext (not the plaintext placeholders) — confirming git will never see the real credentials in the working tree beyond this Step's momentary edit.

- [ ] **Step 9: Commit**

```bash
git add infrastructure/controllers/base/tailscale-operator/
git commit -m "feat(infra): scaffold Tailscale operator with API-server-proxy"
```

---

### Task 3: Expose the Talos API via the operator, wire into the laptop overlay

**Files:**
- Create: `infrastructure/controllers/base/tailscale-operator/talos-api-service.yaml`
- Modify: `infrastructure/controllers/base/tailscale-operator/kustomization.yaml`
- Create: `infrastructure/controllers/laptop/talos-api-endpoints.yaml`
- Modify: `infrastructure/controllers/laptop/kustomization.yaml`

**Interfaces:**
- Consumes: namespace `tailscale-operator` from Task 2.
- Produces: Service `talos-api` (ClusterIP allocated by k8s, `EXTERNAL-IP` allocated by the operator once ready) in namespace `tailscale-operator`; later tasks (5, 6) reference it by this exact name/namespace for validation commands.

- [ ] **Step 1: Create the Talos API Service (cluster-agnostic, no IP)**

`infrastructure/controllers/base/tailscale-operator/talos-api-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: talos-api
  namespace: tailscale-operator
  annotations:
    tailscale.com/hostname: talos-api
    tailscale.com/tags: "tag:talos-api"
spec:
  type: LoadBalancer
  loadBalancerClass: tailscale
  ports:
    - name: talos-grpc
      port: 50000
      targetPort: 50000
      protocol: TCP
```

No `selector` — this Service has no matching pods; Task 3 Step 3 supplies manual `Endpoints` instead, following the standard "Service with externally-managed Endpoints" pattern (not specific to Tailscale).

- [ ] **Step 2: Add the Service to the base kustomization**

Edit `infrastructure/controllers/base/tailscale-operator/kustomization.yaml`, add `talos-api-service.yaml` to `resources`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - helmrepository.yaml
  - helmrelease.yaml
  - operator-oauth.sops.yaml
  - talos-api-service.yaml
```

- [ ] **Step 3: Verify the node IP is still current**

```bash
grep server: os/context/kubeconfig
```

Expected: `server: https://192.168.64.5:6443`. If this differs from `192.168.64.5`, use the IP this command prints in Step 4 instead (Review Focus item 5 — catches node re-provisioning before it's baked into a stale commit).

- [ ] **Step 4: Create the Endpoints object (laptop-specific IP)**

`infrastructure/controllers/laptop/talos-api-endpoints.yaml`:

```yaml
apiVersion: v1
kind: Endpoints
metadata:
  name: talos-api
  namespace: tailscale-operator
subsets:
  - addresses:
      - ip: 192.168.64.5
    ports:
      - name: talos-grpc
        port: 50000
        protocol: TCP
```

`metadata.name` must match the Service name (`talos-api`) exactly — Kubernetes associates `Endpoints` with a `Service` by matching name and namespace.

- [ ] **Step 5: Add the Endpoints to the laptop kustomization**

Edit `infrastructure/controllers/laptop/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../base/cnpg-operator
  - ../base/traefik
  - ../base/cert-manager
  - ../base/tailscale-operator
  - talos-api-endpoints.yaml
patches:
  - path: traefik-values-patch.yaml
    target:
      kind: HelmRelease
      name: traefik
```

- [ ] **Step 6: Render and verify the full laptop overlay**

```bash
kubectl kustomize infrastructure/controllers/laptop
```

Expected: renders cleanly, includes the `tailscale-operator` namespace, `HelmRepository`, `HelmRelease`, the encrypted `Secret`, the `talos-api` `Service` (no selector, `loadBalancerClass: tailscale`), and the `talos-api` `Endpoints` pointing at `192.168.64.5:50000`.

- [ ] **Step 7: Commit**

```bash
git add infrastructure/controllers/base/tailscale-operator/talos-api-service.yaml \
        infrastructure/controllers/base/tailscale-operator/kustomization.yaml \
        infrastructure/controllers/laptop/talos-api-endpoints.yaml \
        infrastructure/controllers/laptop/kustomization.yaml
git commit -m "feat(infra): expose Talos API via Tailscale operator L3 ingress"
```

---

### Task 4: Grant Kubernetes RBAC to the impersonated tailnet identity

**Files:**
- Create: `infrastructure/controllers/base/tailscale-operator/clusterrolebinding.yaml`
- Modify: `infrastructure/controllers/base/tailscale-operator/kustomization.yaml`

**Interfaces:**
- Consumes: none (standalone RBAC object; not referenced by name elsewhere in this plan).
- Produces: `ClusterRoleBinding` granting `cluster-admin` to the Kubernetes `User` `arnaudeprez@gmail.com` — this is the exact string the API-server-proxy impersonates for a non-tagged (user) tailnet device, per Tailscale's auth-mode docs (Review Focus item 1).

- [ ] **Step 1: Create the ClusterRoleBinding**

`infrastructure/controllers/base/tailscale-operator/clusterrolebinding.yaml`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: tailscale-admin-access
subjects:
  - kind: User
    name: arnaudeprez@gmail.com
    apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
```

- [ ] **Step 2: Add to the base kustomization**

Edit `infrastructure/controllers/base/tailscale-operator/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - helmrepository.yaml
  - helmrelease.yaml
  - operator-oauth.sops.yaml
  - talos-api-service.yaml
  - clusterrolebinding.yaml
```

- [ ] **Step 3: Render and verify**

```bash
kubectl kustomize infrastructure/controllers/laptop | grep -A6 "kind: ClusterRoleBinding"
```

Expected: shows the `tailscale-admin-access` binding with `subjects[0].name: arnaudeprez@gmail.com` and `roleRef.name: cluster-admin`.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/controllers/base/tailscale-operator/clusterrolebinding.yaml \
        infrastructure/controllers/base/tailscale-operator/kustomization.yaml
git commit -m "feat(infra): bind tailnet admin identity to cluster-admin RBAC"
```

---

### Task 5: Push and reconcile

**Files:** None (deployment task, no new manifests).

**Interfaces:**
- Consumes: all resources from Tasks 2–4.
- Produces: a live `tailscale-operator` Deployment and `talos-api` Service, validated in Task 6.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/tailscale-operator
```

- [ ] **Step 2: Point-in-time diff before merging (optional but recommended)**

```bash
export KUBECONFIG="$PWD/os/context/kubeconfig"
flux diff kustomization infra-controllers --path ./infrastructure/controllers/laptop
```

Expected: shows only additive changes (new namespace, HelmRepository, HelmRelease, Secret, Service, Endpoints, ClusterRoleBinding) — no unexpected diffs to `cert-manager`, `traefik`, or `cnpg-operator`.

- [ ] **Step 3: Merge to main** (per this repo's normal workflow — Flux only reconciles `main`)

```bash
git checkout main
git merge --no-ff feat/tailscale-operator
git push
```

- [ ] **Step 4: Force reconciliation**

```bash
flux reconcile kustomization infra-controllers --with-source
flux get kustomizations
```

Expected: `infra-controllers` shows `READY=True`.

---

### Task 6: End-to-end validation (including the negative ACL check)

**Files:** None (verification only).

**Interfaces:**
- Consumes: the live cluster state from Task 5.

- [ ] **Step 1: Confirm the operator pod is running and joined the tailnet**

```bash
export KUBECONFIG="$PWD/os/context/kubeconfig"
kubectl get pods -n tailscale-operator
```

Expected: an `operator-...` pod in `Running` state. Then check the [Machines page](https://login.tailscale.com/admin/machines) for a device named `tailscale-operator` tagged `tag:k8s-operator`.

- [ ] **Step 2: Confirm the Talos API Service got a tailnet address**

```bash
kubectl get service talos-api -n tailscale-operator
```

Expected: `EXTERNAL-IP` column populated with a `100.x.x.x` address (may take a minute after Step 1). Also confirm a device named `talos-api` tagged `tag:talos-api` appears on the Machines page.

- [ ] **Step 3: From an authorized tailnet device, access the k8s API**

```bash
tailscale configure kubeconfig tailscale-operator
kubectl get nodes
```

Expected: succeeds and lists the cluster's node — this exercises both the API-server-proxy (Task 2) and the RBAC binding (Task 4) together. If this fails with a TLS error, re-check Task 1 Step 1 (HTTPS certificates) — this is exactly the failure mode Review Focus item 2 calls out.

- [ ] **Step 4: From the same device, access the Talos API**

```bash
talosctl --talosconfig os/context/talosconfig -e <talos-api-tailnet-ip> -n 192.168.64.5 version
```

Expected: succeeds and prints the Talos version. (`-e` is the tailnet address from Step 2; `-n` stays the real node IP since that's Talos's own node-identity argument, unrelated to the network path.)

- [ ] **Step 5: Negative check — confirm default-deny actually holds**

From a tailnet device that is **not** in the `src` list from Task 1 Step 3 (or temporarily log out the primary device's tailscale client and use a guest/second identity if no second device is available):

```bash
tailscale ping talos-api
```

Expected: **fails** (no route / connection refused) — this is the check Review Focus item 4 calls for, proving the grants are restrictive and not merely present. If it unexpectedly succeeds, re-check the `grants` block from Task 1 Step 3 for an overly broad `src`.

- [ ] **Step 6: Record the outcome**

No commit needed for this task — if all five checks pass, the feature is complete. If any fail, return to the task whose Review Focus item matches the failure mode above.
