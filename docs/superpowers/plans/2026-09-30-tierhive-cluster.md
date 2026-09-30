# TierHive Cluster Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `tierhive` overlay for every layer of the repo so the single-node TierHive Talos cluster runs Traefik, CNPG, Tailscale operator, Pocket ID and Immich behind TierHive's managed HAProxy, using per-cluster SOPS/age keys.

**Architecture:** Mirror the `laptop` layout: `clusters/tierhive/` Flux entrypoint plus `infrastructure/{controllers,configs}/tierhive`, `identity/tierhive`, `apps/tierhive` overlays on the existing `base/` dirs. TLS is terminated by TierHive's HAProxy, which forwards plain HTTP to Traefik on `10.10.8.2:80`; no cert-manager on this cluster. Per-cluster secrets move out of `base/` into overlays, each encrypted with that cluster's own age key.

**Tech Stack:** Flux v2, Kustomize, Helm (Traefik 41.5.0, tailscale-operator 1.102.4, Immich chart 0.13.1), SOPS + age, Talos.

**Spec:** [`docs/superpowers/specs/2026-09-30-tierhive-cluster-design.md`](../specs/2026-09-30-tierhive-cluster-design.md)

## Global Constraints

- Hostnames: `id.vps.powple.com` (Pocket ID), `immich.vps.powple.com` (Immich).
- TierHive node internal IP `10.10.8.2`; HAProxy forwards HTTP to `10.10.8.2:80`; HAProxy performs HTTP→HTTPS redirect.
- No cert-manager, no `ClusterIssuer`, no `tls:` sections on tierhive ingresses.
- Traefik on tierhive: hostPort 80 on `web` only, **no** redirect, `forwardedHeaders.trustedIPs: ["10.10.8.0/24"]`.
- Tailscale names must not collide with the laptop: operator hostname `tailscale-operator-tierhive`, Service hostname `talos-api-tierhive`.
- Laptop cluster behaviour must not change (same resource names; identical rendered output).
- One age key per cluster; `*/tierhive/**.sops.yaml` encrypted only to the tierhive key. Private keys are never committed (`*.agekey` is git-ignored).
- Never read `os/*context/kubeconfig` or `talosconfig`.
- Work on branch `feat/tierhive-cluster`. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Keep base `*.yaml` style: 2-space indent, no comments unless they explain a non-obvious reason.

## Review Focus

- Laptop overlay output changes after moving the secrets (would make Flux prune/recreate Secrets on the live laptop) — Task 1 diffs renders before/after.
- Traefik redirect accidentally inherited → infinite HTTP↔HTTPS loop behind HAProxy — Task 3 asserts no `redirections` in rendered values.
- Tailscale device-name collision with the laptop on the tailnet — Task 3 asserts distinct hostnames in render.
- A tierhive secret encrypted to the laptop key (Flux on the VPS can't decrypt it) — Task 5 asserts recipient.
- Immich OIDC/ingress left with `home.arpa` hosts or CA patches — Task 6 greps the rendered output.

---

### Task 1: Move per-cluster secrets out of `base/` into laptop overlays

**Files:**
- Move: `identity/base/pocket-id/pocket-id-secret.sops.yaml` → `identity/laptop/pocket-id/pocket-id-secret.sops.yaml`
- Move: `infrastructure/controllers/base/tailscale-operator/operator-oauth.sops.yaml` → `infrastructure/controllers/laptop/operator-oauth.sops.yaml`
- Modify: `identity/base/pocket-id/kustomization.yaml`, `identity/laptop/pocket-id/kustomization.yaml`, `infrastructure/controllers/base/tailscale-operator/kustomization.yaml`, `infrastructure/controllers/laptop/kustomization.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: base kustomizations no longer contain secrets; any overlay must supply `Secret pocket-id-secrets` (ns `identity`, key `encryptionKey`) and `Secret operator-oauth` (ns `tailscale-operator`, keys `client_id`, `client_secret`).

- [ ] **Step 1: Capture baseline renders of the laptop overlays**

```sh
S=/private/tmp/claude-501/-Users-arnaud-Development-arnaud-deprez-home-lab/e95bcd1c-b54d-4c61-855b-8aec52884809/scratchpad
mkdir -p $S/before
kubectl kustomize identity/laptop > $S/before/identity.yaml
kubectl kustomize infrastructure/controllers/laptop > $S/before/controllers.yaml
kubectl kustomize infrastructure/configs/laptop > $S/before/configs.yaml
kubectl kustomize apps/laptop > $S/before/apps.yaml
wc -l $S/before/*.yaml
```
Expected: four non-empty files.

- [ ] **Step 2: Move the secret files and edit the kustomizations**

```sh
git mv identity/base/pocket-id/pocket-id-secret.sops.yaml identity/laptop/pocket-id/pocket-id-secret.sops.yaml
git mv infrastructure/controllers/base/tailscale-operator/operator-oauth.sops.yaml infrastructure/controllers/laptop/operator-oauth.sops.yaml
```

Edit `identity/base/pocket-id/kustomization.yaml`: delete the line `  - pocket-id-secret.sops.yaml`.

Edit `infrastructure/controllers/base/tailscale-operator/kustomization.yaml`: delete the line `  - operator-oauth.sops.yaml`.

Replace `identity/laptop/pocket-id/kustomization.yaml` with:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base/pocket-id
  - pocket-id-secret.sops.yaml
  - ingress.yaml
```

Replace `infrastructure/controllers/laptop/kustomization.yaml` with (only `operator-oauth.sops.yaml` added):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../base/cnpg-operator
  - ../base/traefik
  - ../base/cert-manager
  - ../base/tailscale-operator
  - operator-oauth.sops.yaml
  - talos-api-endpoints.yaml
patches:
  - path: traefik-values-patch.yaml
    target:
      kind: HelmRelease
      name: traefik
```

- [ ] **Step 3: Render again and compare (sorted, since resource order may change)**

```sh
mkdir -p $S/after
kubectl kustomize identity/laptop > $S/after/identity.yaml
kubectl kustomize infrastructure/controllers/laptop > $S/after/controllers.yaml
kubectl kustomize infrastructure/configs/laptop > $S/after/configs.yaml
kubectl kustomize apps/laptop > $S/after/apps.yaml
for f in identity controllers configs apps; do
  python3 - "$S/before/$f.yaml" "$S/after/$f.yaml" <<'E'
import sys,yaml
a=sorted(map(repr,yaml.safe_load_all(open(sys.argv[1]))))
b=sorted(map(repr,yaml.safe_load_all(open(sys.argv[2]))))
print(sys.argv[2].split('/')[-1], "IDENTICAL" if a==b else "DIFFERENT")
E
done
```
Expected: four lines, all `IDENTICAL`. If any is `DIFFERENT`, fix before continuing.

- [ ] **Step 4: Commit**

```bash
git add -A identity infrastructure
git commit -m "refactor: move per-cluster SOPS secrets into laptop overlays

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Generate the tierhive age key and per-cluster SOPS rules

**Files:**
- Create (outside repo, git-ignored by pattern): `~/.config/sops/age/tierhive.agekey`
- Modify: `.sops.yaml`

**Interfaces:**
- Consumes: laptop age public key already in `.sops.yaml` (`age1nl9yjkq033pufe0hc50fn3z5xrup356cne34p7mezg5m9slt440sawk002`).
- Produces: `.sops.yaml` rules so any `*.sops.yaml` under a `tierhive/` path is encrypted to the tierhive public key, everything else to the laptop key; a combined key file `~/.config/sops/age/keys.txt` the owner uses to edit both.

- [ ] **Step 1: Generate the key outside the repo**

```sh
age-keygen -o ~/.config/sops/age/tierhive.agekey
age-keygen -y ~/.config/sops/age/tierhive.agekey
```
Expected: second command prints `age1...`; note it as `TIERHIVE_PUB`.

- [ ] **Step 2: Write `.sops.yaml` (substitute the printed public key for `TIERHIVE_PUB`)**

```yaml
creation_rules:
  - path_regex: (^|/)tierhive/.*\.sops\.ya?ml$
    encrypted_regex: ^(data|stringData)$
    age: TIERHIVE_PUB
  - path_regex: .*\.sops\.ya?ml$
    encrypted_regex: ^(data|stringData)$
    age: age1nl9yjkq033pufe0hc50fn3z5xrup356cne34p7mezg5m9slt440sawk002
```

The first matching rule wins, so the tierhive rule must stay first.

- [ ] **Step 3: Build a combined key file so the owner's `sops` can decrypt both clusters**

```sh
cat ~/.config/sops/age/home-lab.agekey ~/.config/sops/age/tierhive.agekey > ~/.config/sops/age/keys.txt
```
Expected: no error. (`sops` reads `~/.config/sops/age/keys.txt` by default on macOS via `SOPS_AGE_KEY_FILE`; if it doesn't, `export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt`.)

- [ ] **Step 4: Verify the rule selection without encrypting real data**

```sh
mkdir -p $S/ruletest/tierhive && printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: t\nstringData:\n  k: v\n' > $S/ruletest/tierhive/x.sops.yaml
cp .sops.yaml $S/ruletest/.sops.yaml
(cd $S/ruletest && sops --encrypt tierhive/x.sops.yaml | grep recipient)
```
Expected: the recipient printed is `TIERHIVE_PUB`, not the laptop key.

- [ ] **Step 5: Commit**

```bash
git add .sops.yaml
git commit -m "chore(sops): per-cluster age recipients, tierhive key for */tierhive/**

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `infrastructure/controllers/tierhive` overlay

**Files:**
- Create: `infrastructure/controllers/tierhive/kustomization.yaml`
- Create: `infrastructure/controllers/tierhive/traefik-values-patch.yaml`
- Create: `infrastructure/controllers/tierhive/talos-api-endpoints.yaml`
- Create: `infrastructure/controllers/tierhive/tailscale-patch.yaml`
- Create: `infrastructure/controllers/tierhive/talos-api-service-patch.yaml`
- Create: `infrastructure/controllers/tierhive/operator-oauth.sops.yaml` (owner supplies credentials)

**Interfaces:**
- Consumes: Task 1 (base tailscale kustomization has no secret), Task 2 (SOPS rule).
- Produces: Flux `Kustomization infra-controllers` path for `clusters/tierhive` (Task 7).

- [ ] **Step 1: Write `kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../base/cnpg-operator
  - ../base/traefik
  - ../base/tailscale-operator
  - operator-oauth.sops.yaml
  - talos-api-endpoints.yaml
patches:
  - path: traefik-values-patch.yaml
    target:
      kind: HelmRelease
      name: traefik
  - path: tailscale-patch.yaml
    target:
      kind: HelmRelease
      name: tailscale-operator
  - path: talos-api-service-patch.yaml
    target:
      kind: Service
      name: talos-api
```

- [ ] **Step 2: Write `traefik-values-patch.yaml`**

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: traefik
  namespace: traefik
spec:
  values:
    deployment:
      kind: DaemonSet
    updateStrategy:
      rollingUpdate:
        maxUnavailable: 1
        maxSurge: 0
    service:
      spec:
        type: ClusterIP
    ports:
      web:
        hostPort: 80
        forwardedHeaders:
          trustedIPs:
            - 10.10.8.0/24
```

Note for the file header is not needed; the reason (HAProxy terminates TLS and redirects) is documented in `docs/tierhive.md`.

- [ ] **Step 3: Write `talos-api-endpoints.yaml`**

```yaml
# Not kept in sync by any controller — update by hand if the node's internal IP changes.
apiVersion: v1
kind: Endpoints
metadata:
  name: talos-api
  namespace: tailscale-operator
subsets:
  - addresses:
      - ip: 10.10.8.2
    ports:
      - name: talos-grpc
        port: 50000
        protocol: TCP
```

- [ ] **Step 4: Write the two Tailscale patches**

`tailscale-patch.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: tailscale-operator
  namespace: tailscale-operator
spec:
  values:
    operatorConfig:
      hostname: tailscale-operator-tierhive
```

`talos-api-service-patch.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: talos-api
  namespace: tailscale-operator
  annotations:
    tailscale.com/hostname: talos-api-tierhive
```

- [ ] **Step 5: Verify the chart actually honours `operatorConfig.hostname` and `forwardedHeaders.trustedIPs`**

```sh
helm pull tailscale-operator --repo https://pkgs.tailscale.com/helmcharts --version 1.102.4 --untar -d $S/ts
grep -n "hostname" $S/ts/tailscale-operator/values.yaml | head
helm pull traefik --repo https://traefik.github.io/charts --version 41.5.0 --untar -d $S/tr
yq '.spec.values' <(kubectl kustomize infrastructure/controllers/tierhive | yq 'select(.kind=="HelmRelease" and .metadata.name=="traefik")') > $S/traefik-values.yaml
helm template traefik $S/tr/traefik -n traefik -f $S/traefik-values.yaml | grep -n "forwardedHeaders\|redirections\|hostPort"
```
Expected: `operatorConfig.hostname` exists in the Tailscale values; the Traefik render shows `forwardedHeaders.trustedIPs=10.10.8.0/24` on `web`, `hostPort: 80`, and **no** `redirections` line. If `operatorConfig.hostname` is not a chart value, stop and report (the collision guard must be redone).

- [ ] **Step 6: Create the encrypted Tailscale OAuth secret (owner step)**

The owner creates a **new** Tailscale OAuth client (scopes per `docs/tailscale.md`, tag `tag:k8s-operator`) and then runs, interactively (credentials must not pass through the agent):

```sh
sops infrastructure/controllers/tierhive/operator-oauth.sops.yaml
```
with this content in the editor:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: operator-oauth
  namespace: tailscale-operator
stringData:
  client_id: <client id>
  client_secret: <client secret>
```
Verify it is encrypted to the tierhive key:

```sh
grep recipient infrastructure/controllers/tierhive/operator-oauth.sops.yaml
```
Expected: one `recipient:` line equal to `TIERHIVE_PUB`.

- [ ] **Step 7: Render and assert**

```sh
kubectl kustomize infrastructure/controllers/tierhive > $S/tier-controllers.yaml
grep -c "tailscale-operator-tierhive\|talos-api-tierhive\|10.10.8.2" $S/tier-controllers.yaml
grep -c "cert-manager" $S/tier-controllers.yaml
```
Expected: first ≥ 3; second `0` (no cert-manager resources).

- [ ] **Step 8: Commit**

```bash
git add infrastructure/controllers/tierhive
git commit -m "feat(tierhive): controllers overlay (Traefik behind HAProxy, Tailscale)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `infrastructure/configs/tierhive` overlay

**Files:**
- Create: `infrastructure/configs/tierhive/kustomization.yaml`

**Interfaces:**
- Produces: Flux `Kustomization infra-configs` path for `clusters/tierhive` (Task 7); provides `local-path` StorageClass expected by the base PVCs and CNPG clusters.

- [ ] **Step 1: Write the file**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../base/local-path-provisioner
```

- [ ] **Step 2: Render and assert a StorageClass exists**

```sh
kubectl kustomize infrastructure/configs/tierhive | grep -n "kind: StorageClass\|name: local-path$"
```
Expected: both matched.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/configs/tierhive
git commit -m "feat(tierhive): configs overlay (local-path only)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `identity/tierhive` overlay (Pocket ID)

**Files:**
- Create: `identity/tierhive/kustomization.yaml`
- Create: `identity/tierhive/pocket-id/kustomization.yaml`
- Create: `identity/tierhive/pocket-id/ingress.yaml`
- Create: `identity/tierhive/pocket-id/app-url-patch.yaml`
- Create: `identity/tierhive/pocket-id/pocket-id-secret.sops.yaml`

**Interfaces:**
- Consumes: Task 1 (base has no secret), Task 2 (SOPS rule).
- Produces: Flux `Kustomization identity` path (Task 7); public URL `https://id.vps.powple.com` — the Immich OIDC issuer used in Task 6/docs.

- [ ] **Step 1: Write `identity/tierhive/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - pocket-id
```

- [ ] **Step 2: Write `identity/tierhive/pocket-id/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base/pocket-id
  - pocket-id-secret.sops.yaml
  - ingress.yaml
patches:
  - path: app-url-patch.yaml
    target:
      kind: Deployment
      name: pocket-id
```

- [ ] **Step 3: Write `ingress.yaml` (no TLS section; HAProxy terminates TLS)**

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: pocket-id
  namespace: identity
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  ingressClassName: traefik
  rules:
    - host: id.vps.powple.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: pocket-id
                port:
                  name: http
```

- [ ] **Step 4: Write `app-url-patch.yaml`**

A strategic-merge patch replaces the `env` list, so it must list every env var from the base Deployment with only `APP_URL` changed:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pocket-id
  namespace: identity
spec:
  template:
    spec:
      containers:
        - name: pocket-id
          env:
            - name: APP_URL
              value: https://id.vps.powple.com
            - name: TRUST_PROXY
              value: "true"
            - name: ANALYTICS_DISABLED
              value: "true"
            - name: VERSION_CHECK_DISABLED
              value: "true"
            - name: DB_PROVIDER
              value: postgres
            - name: DB_CONNECTION_STRING
              valueFrom:
                secretKeyRef:
                  name: pocket-id-database-app
                  key: uri
            - name: ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: pocket-id-secrets
                  key: encryptionKey
```

- [ ] **Step 5: Create the encrypted secret with a fresh random key**

```sh
cat > identity/tierhive/pocket-id/pocket-id-secret.sops.yaml <<E
apiVersion: v1
kind: Secret
metadata:
  name: pocket-id-secrets
  namespace: identity
stringData:
  encryptionKey: $(openssl rand -base64 32)
E
sops --encrypt --in-place identity/tierhive/pocket-id/pocket-id-secret.sops.yaml
grep -c "encryptionKey: ENC\[" identity/tierhive/pocket-id/pocket-id-secret.sops.yaml
grep recipient identity/tierhive/pocket-id/pocket-id-secret.sops.yaml
```
Expected: `1`; one recipient equal to `TIERHIVE_PUB`. The plaintext key must appear nowhere else (do not echo it).

- [ ] **Step 6: Render and assert**

```sh
kubectl kustomize identity/tierhive > $S/tier-identity.yaml
grep -n "APP_URL" -A1 $S/tier-identity.yaml
grep -c "home.arpa" $S/tier-identity.yaml
grep -c "DB_CONNECTION_STRING\|ENCRYPTION_KEY" $S/tier-identity.yaml
```
Expected: `value: https://id.vps.powple.com`; `0`; `2` (env list intact).

- [ ] **Step 7: Commit**

```bash
git add identity/tierhive
git commit -m "feat(tierhive): Pocket ID overlay at id.vps.powple.com

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `apps/tierhive/immich` overlay

**Files:**
- Create: `apps/tierhive/kustomization.yaml`
- Create: `apps/tierhive/immich/kustomization.yaml`
- Create: `apps/tierhive/immich/helmrelease-patch.yaml`

**Interfaces:**
- Consumes: base `HelmRelease immich` (values under `spec.values`).
- Produces: Flux `Kustomization apps` path (Task 7); Immich at `https://immich.vps.powple.com`.

- [ ] **Step 1: Write `apps/tierhive/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - immich
```

- [ ] **Step 2: Write `apps/tierhive/immich/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base/immich
patches:
  - path: helmrelease-patch.yaml
    target:
      kind: HelmRelease
      name: immich
```

- [ ] **Step 3: Write `helmrelease-patch.yaml`**

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: immich
  namespace: immich
spec:
  values:
    server:
      ingress:
        main:
          enabled: true
          className: traefik
          annotations:
            traefik.ingress.kubernetes.io/router.entrypoints: web
          hosts:
            - host: immich.vps.powple.com
              paths:
                - path: "/"
                  service:
                    identifier: main
    machine-learning:
      controllers:
        main:
          containers:
            main:
              resources:
                requests:
                  memory: 1Gi
                limits:
                  memory: 2Gi
```

- [ ] **Step 4: Validate against the chart schema and assert no laptop leftovers**

```sh
yq 'select(.kind=="HelmRelease").spec.values' <(kubectl kustomize apps/tierhive) > $S/immich-values.yaml
helm template immich $S/chart/immich -n immich -f $S/immich-values.yaml > $S/immich-render.yaml
grep -n "immich.vps.powple.com" $S/immich-render.yaml
grep -c "home.arpa\|NODE_EXTRA_CA_CERTS\|cert-manager.io" $S/immich-render.yaml
grep -n "memory: 2Gi" $S/immich-render.yaml
```
Expected: the Ingress host found; `0`; ML limit present. (`$S/chart/immich` was pulled earlier in this session; if missing: `helm pull oci://ghcr.io/immich-app/immich-charts/immich --version 0.13.1 --untar -d $S/chart`.)

- [ ] **Step 5: Commit**

```bash
git add apps/tierhive
git commit -m "feat(tierhive): Immich overlay at immich.vps.powple.com

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `clusters/tierhive` Flux entrypoint

**Files:**
- Create: `clusters/tierhive/kustomization.yaml`, `infrastructure.yaml`, `identity.yaml`, `apps.yaml`
- Do **not** create `clusters/tierhive/flux-system/` (generated by `flux bootstrap`).

**Interfaces:**
- Consumes: overlay paths from Tasks 3–6.
- Produces: the path passed to `flux bootstrap --path=clusters/tierhive` (Task 8 docs).

- [ ] **Step 1: Copy the laptop files and retarget paths**

```sh
mkdir -p clusters/tierhive
for f in kustomization infrastructure identity apps; do
  sed 's#/laptop#/tierhive#g' clusters/laptop/$f.yaml > clusters/tierhive/$f.yaml
done
diff -r clusters/laptop clusters/tierhive --exclude=flux-system
```
Expected diff: only the four `path:` lines differ (`./infrastructure/controllers/tierhive`, `./infrastructure/configs/tierhive`, `./identity/tierhive`, `./apps/tierhive`); `kustomization.yaml` has no diff. If `kustomization.yaml` mentions `laptop` in a comment, adjust wording so it is cluster-neutral.

- [ ] **Step 2: Assert every referenced path exists and renders**

```sh
for p in $(grep -h "path: ./" clusters/tierhive/*.yaml | awk '{print $2}'); do
  kubectl kustomize "$p" >/dev/null && echo "OK $p"
done
```
Expected: four `OK` lines.

- [ ] **Step 3: Commit**

```bash
git add clusters/tierhive
git commit -m "feat(tierhive): Flux entrypoint for the tierhive cluster

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/tierhive.md`, `docs/flux.md`, `CLAUDE.md`, `README.md`, `.claude/skills/flux-ops/SKILL.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: `docs/tierhive.md` — replace the Ports table rows and "Open items"**

Change the ingress row of the Ports table to:

```
| — | — | HTTP/HTTPS for apps | Via TierHive managed HAProxy (see below), **not** a port forward: TierHive assigns public forward ports, so 80/443 cannot be forwarded |
```

Add a section before "Troubleshooting":

```markdown
## Public ingress (HAProxy)

TLS is terminated by TierHive's managed HAProxy; Traefik only sees plain HTTP on
`10.10.8.2:80`.

1. Add one HAProxy domain per hostname — `id.vps.powple.com` and `immich.vps.powple.com`.
   Backend `10.10.8.2`, port `80`. Point the DNS records at the address TierHive shows and
   click *Activate SSL*.
2. The HAProxy redirects HTTP→HTTPS, so the Traefik `web` entrypoint must **not** redirect
   (it would loop). The tierhive overlay omits the redirect.
3. Traefik trusts `X-Forwarded-*` from `10.10.8.0/24`. If logins fail the passkey/origin
   check or the scheme looks like `http`, check Traefik access logs for the HAProxy source
   address and narrow/adjust `forwardedHeaders.trustedIPs`
   (`infrastructure/controllers/tierhive/traefik-values-patch.yaml`).
4. Known limits to test early: large Immich uploads (body size, timeouts).

## Flux on this cluster

Uses its own age key so a compromise of the VPS does not expose the laptop's secrets.

```sh
age-keygen -o ~/.config/sops/age/tierhive.agekey   # once; never commit
export KUBECONFIG=os/tierhivecontext/kubeconfig
kubectl create namespace flux-system
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey=$HOME/.config/sops/age/tierhive.agekey
flux bootstrap github --owner=<owner> --repository=<repo> \
  --branch=<feat/tierhive-cluster while validating, then main> \
  --path=clusters/tierhive --personal
```

To edit secrets of both clusters, concatenate both key files into
`~/.config/sops/age/keys.txt`. Rules: see `.sops.yaml` (`*/tierhive/**` → tierhive key).
Follow the branch rules in [`flux.md`](flux.md) — never flip `gotk-sync.yaml` to `main`
while the cluster still tracks the feature branch.

After the first reconcile: create the Immich OIDC client in Pocket ID
(`https://id.vps.powple.com`), configure it in Immich, then remove the public `6072` /
`6075` forwards once `talosctl`/`kubectl` work over the tailnet
(`talos-api-tierhive`, operator `tailscale-operator-tierhive`).
```

Replace the "Open items" Flux bullet with: `- Flux bootstrap for this cluster: see "Flux on this cluster" above (not yet run).` Remove the "Remote admin over Tailscale" item's "see tailscale.md" if redundant; keep the link.

- [ ] **Step 2: `CLAUDE.md` and `docs/flux.md` and `README.md` — two clusters**

In `CLAUDE.md` layout block, change `clusters/laptop/` to also list `clusters/tierhive/` ("TierHive VPS cluster; TLS by managed HAProxy, no cert-manager; own age key"). Change every "`base/` + `laptop/`" sentence to "`base/` + per-cluster overlays (`laptop/`, `tierhive/`)". Add under design conventions: "Secrets that differ per cluster live in the cluster's overlay, encrypted to that cluster's own age key (`.sops.yaml`)". In `docs/flux.md` layout block do the same and note the two-key setup in the Secrets section. In `README.md` line 5 and the layout block mention `clusters/tierhive/`.

- [ ] **Step 3: `.claude/skills/flux-ops/SKILL.md` — add one line**

After "Requires `export KUBECONFIG=...`" add: "For the tierhive cluster use `clusters/tierhive` paths and the tierhive kubeconfig/age key (`~/.config/sops/age/tierhive.agekey`); `sops` needs both keys in `~/.config/sops/age/keys.txt` to edit either cluster's secrets."

- [ ] **Step 4: Commit**

```bash
git add docs CLAUDE.md README.md .claude/skills/flux-ops/SKILL.md
git commit -m "docs: document the tierhive cluster, HAProxy ingress and per-cluster SOPS keys

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Final verification

**Files:** none modified (fix-ups only if a check fails).

- [ ] **Step 1: Render everything for both clusters**

```sh
for p in infrastructure/controllers/laptop infrastructure/configs/laptop identity/laptop apps/laptop \
         infrastructure/controllers/tierhive infrastructure/configs/tierhive identity/tierhive apps/tierhive; do
  kubectl kustomize $p >/dev/null && echo "OK $p" || echo "FAIL $p"
done
```
Expected: eight `OK`.

- [ ] **Step 2: Laptop output unchanged versus Task 1 baseline** (re-run the Step 3 comparison of Task 1). Expected: four `IDENTICAL`.

- [ ] **Step 3: Tierhive secrets decrypt with the tierhive key only**

```sh
for f in identity/tierhive/pocket-id/pocket-id-secret.sops.yaml infrastructure/controllers/tierhive/operator-oauth.sops.yaml; do
  SOPS_AGE_KEY_FILE=$HOME/.config/sops/age/tierhive.agekey sops -d $f >/dev/null && echo "decrypts $f"
done
git grep -l "AGE-SECRET-KEY" || echo "no private keys in repo"
```
Expected: two `decrypts` lines; `no private keys in repo`.

- [ ] **Step 4: Status check and hand-off**

```sh
git status --short; git log --oneline main..HEAD
```
Expected: clean tree, one commit per task plus the two spec commits. Report to the owner that the cluster is **not yet bootstrapped** — the remaining steps (Flux bootstrap, Namecheap/HAProxy, Pocket ID OIDC client, removing public forwards) are the owner's, per `docs/tierhive.md`. Do not merge to `main` or push without the owner's say-so.
