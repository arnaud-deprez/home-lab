# EuroNodes Cluster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring up a third single-node Talos cluster on a EuroNodes VPS (Portugal), managed by Flux from this repo, running Pocket ID and Immich with encrypted disks, Let's Encrypt TLS, Immich originals on rclone-crypt S3, and tested encrypted restic backups on a Hetzner Storage Box.

**Architecture:** Mirror the existing `tierhive` layout (`talos/euronodes`, `clusters/euronodes`, `infrastructure/{controllers,configs}/euronodes`, `apps/euronodes`) on top of the existing `base/` dirs. Traefik terminates TLS directly on the node's public IPv4, cert-manager issues one Let's Encrypt certificate per host over HTTP-01. Immich keeps Postgres, thumbnails and encoded video on `local-path`; `library` and `upload` live on an rclone-crypt FUSE mount of EuroNodes S3, exposed to Immich through a privileged rclone pod and bidirectional host-path mount propagation, gated by a test before real data. A restic CronJob backs originals, profile and DB dumps up to a Hetzner Storage Box.

**Tech Stack:** Talos 1.14 (`talosctl` v1.14.2 on the laptop), Flux v2, Kustomize, Helm (Traefik, cert-manager v1.21.2, CNPG operator, Immich chart), SOPS + age, rclone, restic, Let's Encrypt.

**Spec:** [`docs/superpowers/specs/2026-10-12-euronodes-cluster-design.md`](../specs/2026-10-12-euronodes-cluster-design.md)

## Global Constraints

- Work on branch `feat/euronodes`. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push without the user's say-so.
- Node: public IPv4 `178.159.34.177/24` on `ens18`, gateway `178.159.34.1`, system disk `/dev/sda` (161 GB), hostname `euronodes`.
- Endpoint `https://178.159.34.177:6443`; add `178.159.34.177` as certificate SAN. No port forwards, no source-IP firewall.
- `EPHEMERAL` capped at 40 GB; `local-path-provisioner` user volume `minSize: 1GB`, `grow: true`; mounted at `/var/mnt/local-path-provisioner`. All volumes (`STATE`, `EPHEMERAL`, user volume) LUKS2 with the node-ID key.
- The VM UUID is never written to the repo, docs or commit messages.
- Domain `*.euronodes.powple.com`; hosts `id.euronodes.powple.com` (Pocket ID) and `immich.euronodes.powple.com` (Immich). One `A` record per wildcard at Namecheap (done by the user).
- TLS: Let's Encrypt, **one certificate per host over HTTP-01**, Traefik `websecure` hostPort 443, `web` hostPort 80 redirecting to `websecure`. No DNS-01.
- Immich: `library` and `upload` only on the rclone-crypt mount. `profile`, `backups`, `thumbs`, `encoded-video`, Postgres stay on `local-path`.
- Never read `talos/*/kubeconfig`, `talosconfig`, or any `*.agekey`. Private keys, rclone passwords, restic password and Storage Box keys are never committed unencrypted; they go in SOPS files and in the user's password manager.
- One age key per cluster: `~/.config/sops/age/euronodes.agekey`; `*/euronodes/**.sops.yaml` and `talos/euronodes/secrets*` encrypted only to it.
- Laptop and tierhive behaviour must not change (no edits under their overlays).
- Style: 2-space YAML, no comments unless they explain a non-obvious reason.
- The live cluster tracks `feat/euronodes` during development. Never push a `gotk-sync.yaml` set to `main` to this branch while the cluster still tracks it (see Task 10).

## Review Focus

- The node-ID LUKS key makes volumes unreadable after a VM clone or UUID change; a wrong encryption block silently leaves volumes unencrypted — Task 3 asserts `talosctl get volumestatus` shows `luks2` on `STATE`, `EPHEMERAL` and the user volume.
- Immich started before the rclone mount is up writes originals into an empty host directory on the local disk, then the mount hides them — Task 7 adds a wait-for-sentinel init container and tests restarting in the wrong order.
- Write-cache loss: a pod killed mid-upload loses data that never reached S3 — Task 7's gate kills the rclone pod during an upload and compares checksums.
- The Pocket ID database is only on `local-path` and is covered by no Immich dump — Task 8 adds a `pg_dump` CronJob and restores it in the drill.
- HTTP→HTTPS redirect on `web` breaking the ACME HTTP-01 challenge, or hitting production rate limits while debugging — Task 6 issues from the staging issuer first and checks the certificate chain before switching to production.

---

### Task 1: Age key and SOPS rules for euronodes

**Files:**
- Modify: `.sops.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: `~/.config/sops/age/euronodes.agekey` (outside the repo) and `.sops.yaml` rules selecting that key for `talos/euronodes/secrets*` and `*/euronodes/*.sops.yaml`. Every later task that encrypts a secret relies on this.

- [ ] **Step 1: Generate the key outside the repo**

```sh
test -e ~/.config/sops/age/euronodes.agekey && echo "EXISTS - stop" || age-keygen -o ~/.config/sops/age/euronodes.agekey
grep '^# public key' ~/.config/sops/age/euronodes.agekey
```
Expected: prints `# public key: age1...`. Tell the user to store the private key in their password manager now.

- [ ] **Step 2: Add the rules**

Insert in `.sops.yaml` (replace `AGE_PUBLIC_KEY` with the printed key), the talos rule before the generic rules, the others next to the tierhive ones:

```yaml
  - path_regex: (^|/)talos/euronodes/secrets(\.sops)?\.ya?ml$
    age: AGE_PUBLIC_KEY
  - path_regex: (^|/)euronodes/.*\.sops\.ya?ml$
    encrypted_regex: ^(data|stringData)$
    age: AGE_PUBLIC_KEY
```

- [ ] **Step 3: Verify with a round trip**

```sh
export SOPS_AGE_KEY_FILE=~/.config/sops/age/euronodes.agekey
mkdir -p /tmp/euronodes-sops-test/euronodes
cat > /tmp/euronodes-sops-test/euronodes/t.sops.yaml <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: t
stringData:
  k: v
EOF
cp .sops.yaml /tmp/euronodes-sops-test/ && (cd /tmp/euronodes-sops-test && sops --encrypt --in-place euronodes/t.sops.yaml && grep -c 'ENC\[' euronodes/t.sops.yaml && sops -d euronodes/t.sops.yaml | grep 'k: v')
rm -rf /tmp/euronodes-sops-test
```
Expected: `1` (or more) then `  k: v`. A failure means the regex or key is wrong.

- [ ] **Step 4: Commit**

```bash
git add .sops.yaml
git commit -m "chore(sops): euronodes age recipient rules

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Talos patch and generated config (no node contact yet)

**Files:**
- Create: `talos/euronodes/patch.yaml`
- Create: `talos/euronodes/secrets.sops.yaml`

**Interfaces:**
- Consumes: age key and rules from Task 1.
- Produces: `talos/euronodes/patch.yaml`; git-ignored `talos/euronodes/{controlplane.yaml,worker.yaml,talosconfig}`; `$SCHEMATIC` and `$TALOS_VERSION` shell variables used again in Task 3.

- [ ] **Step 1: Read the schematic ID and Talos version from the running node**

```sh
export ENDPOINT=178.159.34.177
talosctl version --insecure -n $ENDPOINT -e $ENDPOINT | grep -A2 Server
talosctl get extensions --insecure -n $ENDPOINT -e $ENDPOINT
```
Expected: `Tag: v1.x.y` under Server, and an extensions list that includes `iscsi-tools` and a row named `schematic` whose version is the 64-hex schematic ID. If there is no `schematic` row, ask the user for the schematic ID from factory.talos.dev. Then:

```sh
export TALOS_VERSION=v1.x.y     # the Server tag
export SCHEMATIC=<64 hex>       # from the extensions list
```

- [ ] **Step 2: Write the patch**

```yaml
apiVersion: v1alpha1
kind: HostnameConfig
auto: "off"
hostname: euronodes
---
apiVersion: v1alpha1
kind: LinkConfig
name: ens18
addresses:
  - address: 178.159.34.177/24
routes:
  - gateway: 178.159.34.1
---
apiVersion: v1alpha1
kind: ResolverConfig
nameservers:
  - address: 1.1.1.1
  - address: 9.9.9.9
---
apiVersion: v1alpha1
kind: TimeSyncConfig
ntp:
  servers:
    - time.cloudflare.com
---
apiVersion: v1alpha1
kind: KubeNodeConfig
taints:
  node-role.kubernetes.io/control-plane:
    $patch: delete
---
apiVersion: v1alpha1
kind: UnattendedInstallConfig
provisioning:
  diskSelector:
    match: disk.dev_path == "/dev/sda"
---
apiVersion: v1alpha1
kind: VolumeConfig
name: STATE
encryption:
  provider: luks2
  keys:
    - slot: 0
      nodeID: {}
---
apiVersion: v1alpha1
kind: VolumeConfig
name: EPHEMERAL
provisioning:
  diskSelector:
    match: system_disk
  minSize: 40GB
  maxSize: 40GB
encryption:
  provider: luks2
  keys:
    - slot: 0
      nodeID: {}
---
apiVersion: v1alpha1
kind: UserVolumeConfig
name: local-path-provisioner
provisioning:
  diskSelector:
    match: system_disk
  minSize: 1GB
  grow: true
encryption:
  provider: luks2
  keys:
    - slot: 0
      nodeID: {}
```

- [ ] **Step 3: Validate the field names against the installed docs**

```sh
talosctl docs --help >/dev/null 2>&1 && echo has-docs
```
Check each kind and field (`HostnameConfig.auto`, `LinkConfig`, `ResolverConfig`, `TimeSyncConfig`, `VolumeConfig.encryption` for `STATE`, `UserVolumeConfig.encryption`, `nodeID`) in the Talos v1.14 docs at docs.siderolabs.com. Fix any mismatch in the patch. Also check whether `STATE` accepts `nodeID` and note the exact upgrade options for a single node for Task 9's docs.

- [ ] **Step 4: Generate secrets, encrypt, generate the base config**

```sh
export SOPS_AGE_KEY_FILE=~/.config/sops/age/euronodes.agekey
talosctl gen secrets -o talos/euronodes/secrets.yaml
sops -e talos/euronodes/secrets.yaml > talos/euronodes/secrets.sops.yaml
rm talos/euronodes/secrets.yaml
talosctl gen config --with-secrets <(sops -d talos/euronodes/secrets.sops.yaml) \
  --additional-sans 178.159.34.177 \
  --install-image factory.talos.dev/metal-installer/$SCHEMATIC:$TALOS_VERSION \
  --output-dir talos/euronodes \
  euronodes https://178.159.34.177:6443
git status --short talos/euronodes
```
Expected: only `patch.yaml` and `secrets.sops.yaml` appear (generated configs are git-ignored). `grep -c ENC talos/euronodes/secrets.sops.yaml` is non-zero.

- [ ] **Step 5: Dry-run the merged config against the node**

```sh
talosctl apply-config --dry-run -f talos/euronodes/controlplane.yaml \
  --config-patch @talos/euronodes/patch.yaml \
  -n $ENDPOINT -e $ENDPOINT --insecure
```
Expected: a diff or "dry run" output with no validation error. A schema error names the bad field: fix the patch and repeat.

- [ ] **Step 6: Commit**

```bash
git add talos/euronodes/patch.yaml talos/euronodes/secrets.sops.yaml
git commit -m "feat(euronodes): talos patch and sops secrets bundle

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Install Talos, bootstrap Kubernetes, verify encryption

**Files:** none in the repo (live operations).

**Interfaces:**
- Consumes: `talos/euronodes/controlplane.yaml`, `patch.yaml`, `talosconfig` from Task 2; `$ENDPOINT`.
- Produces: a running node, `talos/euronodes/kubeconfig` (git-ignored) for every later live task.

- [ ] **Step 1: Confirm with the user, then apply**

This wipes `/dev/sda`. Ask the user to confirm that the VM holds no data. Then:

```sh
talosctl apply-config -f talos/euronodes/controlplane.yaml \
  --config-patch @talos/euronodes/patch.yaml \
  -n $ENDPOINT -e $ENDPOINT --insecure
```
The node installs and reboots. Ask the user to **eject the ISO in the EuroNodes panel right away** so it boots from disk.

- [ ] **Step 2: Bootstrap**

```sh
export TALOSCONFIG=talos/euronodes/talosconfig
talosctl config endpoint 178.159.34.177
talosctl config node 178.159.34.177
talosctl bootstrap
talosctl health
```
Expected: `health` ends successfully. If `bootstrap` fails with a connection error, wait one minute for the reboot and retry.

- [ ] **Step 3: Kubeconfig**

```sh
talosctl kubeconfig talos/euronodes/kubeconfig
export KUBECONFIG=talos/euronodes/kubeconfig
kubectl get nodes
```
Expected: node `euronodes` `Ready` (control-plane taint removed).

- [ ] **Step 4: Verify disks and encryption**

```sh
talosctl get volumestatus
```
Expected: `STATE`, `EPHEMERAL` and `u-local-path-provisioner` all `ready`, each showing encryption provider `luks2`; `EPHEMERAL` size about 40 GB; the user volume mounted at `/var/mnt/local-path-provisioner` with about 115 GB. If any volume shows no encryption, stop and fix the patch (encryption cannot be added later without a wipe).

- [ ] **Step 5: Restart test**

```sh
talosctl reboot && sleep 120 && talosctl health
```
Expected: the node comes back and volumes unlock unattended. This is the check that the node-ID key works across reboots.

No commit (nothing in the repo changed).

---

### Task 4: Flux entrypoint and infrastructure overlays

**Files:**
- Create: `clusters/euronodes/kustomization.yaml`, `clusters/euronodes/infrastructure.yaml`, `clusters/euronodes/apps.yaml`
- Create: `infrastructure/controllers/euronodes/kustomization.yaml`, `infrastructure/controllers/euronodes/traefik-values-patch.yaml`
- Create: `infrastructure/configs/euronodes/kustomization.yaml`, `infrastructure/configs/euronodes/letsencrypt-issuers.yaml`

**Interfaces:**
- Consumes: base dirs `infrastructure/controllers/base/{cnpg-operator,traefik,cert-manager}`, `infrastructure/configs/base/local-path-provisioner`.
- Produces: Flux Kustomizations `infra-controllers` (path `./infrastructure/controllers/euronodes`), `infra-configs` (path `./infrastructure/configs/euronodes`), `apps` (path `./apps/euronodes`); `ClusterIssuer` names `letsencrypt-staging` and `letsencrypt-prod` used by Task 5 ingresses.

- [ ] **Step 1: Ask the user for the ACME contact email**

Ask: "Which email should Let's Encrypt use for registration? It is committed to the repo." Store it: `export ACME_EMAIL=<answer>`.

- [ ] **Step 2: Create the cluster entrypoint (copy of tierhive, paths changed)**

```sh
mkdir -p clusters/euronodes
cp clusters/tierhive/kustomization.yaml clusters/euronodes/kustomization.yaml
sed 's#/tierhive#/euronodes#' clusters/tierhive/infrastructure.yaml > clusters/euronodes/infrastructure.yaml
sed 's#/tierhive#/euronodes#' clusters/tierhive/apps.yaml > clusters/euronodes/apps.yaml
grep -n 'path:' clusters/euronodes/*.yaml
```
Expected: three paths ending in `/euronodes`. `flux-system/` is created later by `flux bootstrap` (Task 6), so `clusters/euronodes/kustomization.yaml` will fail to render until then.

- [ ] **Step 3: Controllers overlay**

`infrastructure/controllers/euronodes/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../base/cnpg-operator
  - ../base/traefik
  - ../base/cert-manager
patches:
  - path: traefik-values-patch.yaml
    target:
      kind: HelmRelease
      name: traefik
```

`infrastructure/controllers/euronodes/traefik-values-patch.yaml`:

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
        http:
          redirections:
            entryPoint:
              to: websecure
              scheme: https
              permanent: true
        transport:
          respondingTimeouts:
            readTimeout: 0
            writeTimeout: 0
            idleTimeout: 600s
      websecure:
        hostPort: 443
        transport:
          respondingTimeouts:
            readTimeout: 0
            writeTimeout: 0
            idleTimeout: 600s
```
The timeouts mirror tierhive's: large Immich uploads must not be cut off.

- [ ] **Step 4: Configs overlay**

`infrastructure/configs/euronodes/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../base/local-path-provisioner
  - letsencrypt-issuers.yaml
```

`infrastructure/configs/euronodes/letsencrypt-issuers.yaml` (write with `$ACME_EMAIL` substituted):

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: ACME_EMAIL_HERE
    privateKeySecretRef:
      name: letsencrypt-staging-account
    solvers:
      - http01:
          ingress:
            ingressClassName: traefik
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ACME_EMAIL_HERE
    privateKeySecretRef:
      name: letsencrypt-prod-account
    solvers:
      - http01:
          ingress:
            ingressClassName: traefik
```

```sh
sed -i '' "s/ACME_EMAIL_HERE/$ACME_EMAIL/" infrastructure/configs/euronodes/letsencrypt-issuers.yaml
grep -c ACME_EMAIL_HERE infrastructure/configs/euronodes/letsencrypt-issuers.yaml
```
Expected: `0`.

- [ ] **Step 5: Render both overlays**

```sh
kubectl kustomize infrastructure/controllers/euronodes | grep -E '^kind: HelmRelease' -A3 | grep name:
kubectl kustomize infrastructure/controllers/euronodes | grep -c 'hostPort: 443'
kubectl kustomize infrastructure/controllers/euronodes | grep -c 'trustedIPs'
kubectl kustomize infrastructure/configs/euronodes | grep -E 'name: (letsencrypt|local-path)'
```
Expected: HelmReleases `cnpg-operator`, `traefik`, `cert-manager`; `hostPort: 443` count `1`; `trustedIPs` count `0`; both issuers and local-path resources present.

- [ ] **Step 6: Commit**

```bash
git add clusters/euronodes infrastructure/controllers/euronodes infrastructure/configs/euronodes
git commit -m "feat(euronodes): flux entrypoint and infrastructure overlays

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Apps overlays (Pocket ID and Immich on local storage)

**Files:**
- Create: `apps/euronodes/kustomization.yaml`
- Create: `apps/euronodes/pocket-id/{kustomization.yaml,ingress.yaml,app-url-patch.yaml,pocket-id-secret.sops.yaml}`
- Create: `apps/euronodes/immich/{kustomization.yaml,helmrelease-patch.yaml,library-pvc-patch.yaml}`

**Interfaces:**
- Consumes: `apps/base/immich`, `apps/base/pocket-id`; issuers `letsencrypt-staging`/`letsencrypt-prod` from Task 4.
- Produces: Immich release `immich` in namespace `immich` with a local `immich-library-pvc` (later narrowed in Task 7); Secret `pocket-id-secrets` (key `encryptionKey`) in namespace `identity`.

- [ ] **Step 1: Pocket ID overlay**

`apps/euronodes/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - immich
  - pocket-id
```

`apps/euronodes/pocket-id/kustomization.yaml`:

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

`apps/euronodes/pocket-id/app-url-patch.yaml` — copy of the tierhive file with the URL changed and `TRUST_PROXY` removed (no proxy in front):

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
              value: https://id.euronodes.powple.com
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

`apps/euronodes/pocket-id/ingress.yaml` (staging issuer first):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: pocket-id
  namespace: identity
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-staging
spec:
  ingressClassName: traefik
  tls:
    - hosts:
        - id.euronodes.powple.com
      secretName: pocket-id-tls
  rules:
    - host: id.euronodes.powple.com
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

- [ ] **Step 2: Encrypted Pocket ID secret (new random key for this cluster)**

```sh
export SOPS_AGE_KEY_FILE=~/.config/sops/age/euronodes.agekey
cat > apps/euronodes/pocket-id/pocket-id-secret.sops.yaml <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: pocket-id-secrets
  namespace: identity
stringData:
  encryptionKey: $(openssl rand -base64 32)
EOF
sops --encrypt --in-place apps/euronodes/pocket-id/pocket-id-secret.sops.yaml
grep -c 'ENC\[' apps/euronodes/pocket-id/pocket-id-secret.sops.yaml
```
Expected: non-zero. Tell the user to save the plaintext key (`sops -d ... | grep encryptionKey`) in their password manager: losing it loses encrypted Pocket ID data.

- [ ] **Step 3: Immich overlay**

`apps/euronodes/immich/kustomization.yaml`:

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
  - path: library-pvc-patch.yaml
    target:
      kind: PersistentVolumeClaim
      name: immich-library-pvc
```

`apps/euronodes/immich/library-pvc-patch.yaml` (local phase: room for thumbs, encoded video and profile; originals move off-disk in Task 7):

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: immich-library-pvc
  namespace: immich
spec:
  resources:
    requests:
      storage: 40Gi
```

`apps/euronodes/immich/helmrelease-patch.yaml`:

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
            cert-manager.io/cluster-issuer: letsencrypt-staging
            traefik.ingress.kubernetes.io/router.entrypoints: websecure
          hosts:
            - host: immich.euronodes.powple.com
              paths:
                - path: "/"
                  service:
                    identifier: main
          tls:
            - hosts:
                - immich.euronodes.powple.com
              secretName: immich-tls
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

Immich still serves ACME challenge paths on `web`; only certificate-bearing traffic needs `websecure`.

- [ ] **Step 4: Render and check**

```sh
kubectl kustomize apps/euronodes > /tmp/euronodes-apps.yaml
grep -c 'euronodes.powple.com' /tmp/euronodes-apps.yaml
grep -E 'home.arpa|vps.powple.com|TRUST_PROXY' /tmp/euronodes-apps.yaml | wc -l
grep -c 'cert-manager.io/cluster-issuer: letsencrypt-staging' /tmp/euronodes-apps.yaml
rm /tmp/euronodes-apps.yaml
```
Expected: non-zero, `0`, and `2`.

- [ ] **Step 5: Check chart values against the Immich chart schema**

```sh
helm pull oci://ghcr.io/immich-app/immich-charts/immich --version 0.13.1 --untar -d /tmp/chart
kubectl kustomize apps/euronodes/immich | yq 'select(.kind=="HelmRelease") | .spec.values' > /tmp/values.yaml
helm template immich /tmp/chart/immich -n immich -f /tmp/values.yaml | grep -A12 'kind: Ingress'
rm -rf /tmp/chart /tmp/values.yaml
```
Expected: an `Ingress` with host `immich.euronodes.powple.com` and a `tls:` block. If the chart version in `apps/base/immich/ocirepository.yaml` differs from `0.13.1`, use that version.

- [ ] **Step 6: Commit**

```bash
git add apps/euronodes
git commit -m "feat(euronodes): pocket id and immich overlays on local storage

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Bootstrap Flux on the branch, DNS and certificates

**Files:**
- Created by `flux bootstrap`: `clusters/euronodes/flux-system/*`
- Modify: `apps/euronodes/pocket-id/ingress.yaml`, `apps/euronodes/immich/helmrelease-patch.yaml` (staging → prod)

**Interfaces:**
- Consumes: everything from Tasks 1–5, a running cluster from Task 3, and `~/.config/sops/age/euronodes.agekey`.
- Produces: a Flux-managed cluster tracking `feat/euronodes`; `clusters/euronodes/flux-system/gotk-sync.yaml` with `spec.ref.branch: feat/euronodes` (reset to `main` only at merge, Task 10).

- [ ] **Step 1: User creates the DNS record**

Ask the user to add at Namecheap: `A  *.euronodes  178.159.34.177`. Verify:

```sh
dig +short id.euronodes.powple.com immich.euronodes.powple.com
```
Expected: `178.159.34.177` twice. Do not continue until it resolves (Let's Encrypt will fail otherwise and back off).

- [ ] **Step 2: Push the branch and give Flux the decryption key**

Ask the user to confirm pushing, then:

```sh
git push -u origin feat/euronodes
export KUBECONFIG=talos/euronodes/kubeconfig
kubectl create namespace flux-system
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey=$HOME/.config/sops/age/euronodes.agekey
```

- [ ] **Step 3: Bootstrap on the branch**

Ask the user for a fine-grained GitHub PAT (Contents + Administration RW) in `GITHUB_TOKEN`; then:

```sh
export GITHUB_USER=arnaud-deprez
flux bootstrap github --owner="$GITHUB_USER" --repository=home-lab \
  --branch=feat/euronodes --path=clusters/euronodes --personal
git pull
grep 'branch:' clusters/euronodes/flux-system/gotk-sync.yaml
```
Expected: `branch: feat/euronodes`. The bootstrap commit lands on the branch.

- [ ] **Step 4: Watch reconciliation**

```sh
flux get kustomizations
```
Expected after a few minutes: `flux-system`, `infra-controllers`, `infra-configs`, `apps` all `READY True`. If `infra-controllers` is stuck, `flux logs --level=error` and `kubectl -n flux-system describe kustomization infra-controllers`.

- [ ] **Step 5: Staging certificates**

```sh
kubectl get certificate -A
kubectl get challenge -A
curl -skI https://id.euronodes.powple.com | head -3
```
Expected: both certificates `READY True` from the staging issuer (the browser will warn: staging is untrusted); the HTTP request redirects to HTTPS. If challenges stay pending, check `kubectl describe challenge -A` for DNS or port 80 errors.

- [ ] **Step 6: Switch to production issuer**

```sh
sed -i '' 's/letsencrypt-staging/letsencrypt-prod/' apps/euronodes/pocket-id/ingress.yaml apps/euronodes/immich/helmrelease-patch.yaml
git add -A apps/euronodes && git commit -m "feat(euronodes): use production Let's Encrypt issuer

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Ask the user before pushing, then `git push && flux reconcile kustomization apps --with-source`. Delete the staging secrets so cert-manager reissues:

```sh
kubectl -n identity delete secret pocket-id-tls
kubectl -n immich delete secret immich-tls
kubectl get certificate -A
curl -sI https://id.euronodes.powple.com | head -3
curl -sI https://immich.euronodes.powple.com | head -3
```
Expected: certificates `READY True`, and `curl` (without `-k`) answers `200` or `302` with a valid chain.

- [ ] **Step 7: Smoke test**

Ask the user to open `https://id.euronodes.powple.com/setup` and `https://immich.euronodes.powple.com`, create the admin accounts, and report whether login works. Immich is still on local storage and has no real photos yet.

---

### Task 7: rclone crypt mount for Immich originals, gated by tests

**Files:**
- Create: `apps/euronodes/immich/rclone-mount.yaml`
- Create: `apps/euronodes/immich/rclone-secret.sops.yaml`
- Create: `apps/euronodes/immich/namespace-patch.yaml`
- Modify: `apps/euronodes/immich/kustomization.yaml`, `apps/euronodes/immich/helmrelease-patch.yaml`

**Interfaces:**
- Consumes: namespace `immich` and `immich-library-pvc` (local, at Immich's `UPLOAD_LOCATION`), SOPS key from Task 1.
- Produces: host path `/var/mnt/rclone/library` and `/var/mnt/rclone/upload` (rclone-crypt of S3) mounted into the Immich server at `/usr/src/app/upload/library` and `/usr/src/app/upload/upload`; Secret `rclone-config` (namespace `immich`); the sentinel object `.mounted` at the root of the crypt remote; Deployment `rclone-mount` that Task 8's backup job reads through the same host path.

- [ ] **Step 1: Ask the user for the S3 details**

Ask the user to create, in the EuroNodes panel, a bucket and an access key limited to it, and give: endpoint URL, region (if any), bucket name, access key, secret key. Ask the user to generate two rclone crypt passwords (`password` and `password2`) with `openssl rand -base64 32` each and store them in the password manager. Store as shell variables `S3_ENDPOINT S3_BUCKET S3_REGION S3_ACCESS S3_SECRET CRYPT_PW CRYPT_SALT`.

- [ ] **Step 2: Pin the rclone image**

```sh
gh release list -R rclone/rclone -L 1
```
Use that tag (without the leading `v`) as `RCLONE_TAG` in the manifest below.

- [ ] **Step 3: Secret with the rclone configuration as environment variables**

`rclone obscure` produces the obscured form rclone expects:

```sh
export SOPS_AGE_KEY_FILE=~/.config/sops/age/euronodes.agekey
cat > apps/euronodes/immich/rclone-secret.sops.yaml <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: rclone-config
  namespace: immich
stringData:
  RCLONE_CONFIG_S3_TYPE: s3
  RCLONE_CONFIG_S3_PROVIDER: Other
  RCLONE_CONFIG_S3_ENDPOINT: $S3_ENDPOINT
  RCLONE_CONFIG_S3_REGION: $S3_REGION
  RCLONE_CONFIG_S3_ACCESS_KEY_ID: $S3_ACCESS
  RCLONE_CONFIG_S3_SECRET_ACCESS_KEY: $S3_SECRET
  RCLONE_CONFIG_CRYPT_TYPE: crypt
  RCLONE_CONFIG_CRYPT_REMOTE: s3:$S3_BUCKET
  RCLONE_CONFIG_CRYPT_PASSWORD: $(rclone obscure "$CRYPT_PW")
  RCLONE_CONFIG_CRYPT_PASSWORD2: $(rclone obscure "$CRYPT_SALT")
  RCLONE_CONFIG_CRYPT_FILENAME_ENCRYPTION: standard
EOF
sops --encrypt --in-place apps/euronodes/immich/rclone-secret.sops.yaml
grep -c 'ENC\[' apps/euronodes/immich/rclone-secret.sops.yaml
```
Expected: non-zero. Never echo the variables afterwards.

- [ ] **Step 4: Pre-flight against S3 from the laptop (not the cluster)**

```sh
RCLONE_CONFIG_S3_TYPE=s3 RCLONE_CONFIG_S3_PROVIDER=Other \
RCLONE_CONFIG_S3_ENDPOINT=$S3_ENDPOINT RCLONE_CONFIG_S3_REGION=$S3_REGION \
RCLONE_CONFIG_S3_ACCESS_KEY_ID=$S3_ACCESS RCLONE_CONFIG_S3_SECRET_ACCESS_KEY=$S3_SECRET \
RCLONE_CONFIG_CRYPT_TYPE=crypt RCLONE_CONFIG_CRYPT_REMOTE=s3:$S3_BUCKET \
RCLONE_CONFIG_CRYPT_PASSWORD=$(rclone obscure "$CRYPT_PW") \
RCLONE_CONFIG_CRYPT_PASSWORD2=$(rclone obscure "$CRYPT_SALT") \
sh -c 'echo ok | rclone rcat crypt:.mounted && rclone mkdir crypt:library && rclone mkdir crypt:upload && rclone lsf crypt: && rclone lsf s3:'$S3_BUCKET
```
Expected: `crypt:` lists `.mounted`, `library/`, `upload/`; the raw `s3:` listing shows only encrypted names. Failure here means a wrong endpoint, key, or an S3 compatibility problem (note it for the spec's open items).

- [ ] **Step 5: rclone Deployment**

`apps/euronodes/immich/rclone-mount.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rclone-cache
  namespace: immich
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rclone-mount
  namespace: immich
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: rclone-mount
  template:
    metadata:
      labels:
        app: rclone-mount
    spec:
      terminationGracePeriodSeconds: 120
      containers:
        - name: rclone
          image: rclone/rclone:RCLONE_TAG
          args:
            - mount
            - "crypt:"
            - /mnt/rclone
            - --allow-other
            - --vfs-cache-mode=writes
            - --cache-dir=/cache
            - --vfs-cache-max-size=15G
            - --vfs-write-back=5s
            - --dir-cache-time=1m
            - --umask=000
          envFrom:
            - secretRef:
                name: rclone-config
          securityContext:
            privileged: true
          volumeMounts:
            - name: mount
              mountPath: /mnt/rclone
              mountPropagation: Bidirectional
            - name: cache
              mountPath: /cache
            - name: fuse
              mountPath: /dev/fuse
      volumes:
        - name: mount
          hostPath:
            path: /var/mnt/rclone
            type: DirectoryOrCreate
        - name: cache
          persistentVolumeClaim:
            claimName: rclone-cache
        - name: fuse
          hostPath:
            path: /dev/fuse
            type: CharDevice
```

```sh
sed -i '' "s/RCLONE_TAG/$RCLONE_TAG/" apps/euronodes/immich/rclone-mount.yaml
grep -c RCLONE_TAG apps/euronodes/immich/rclone-mount.yaml
```
Expected: `0`.

- [ ] **Step 6: Namespace label for privileged pods**

`apps/euronodes/immich/namespace-patch.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: immich
  labels:
    pod-security.kubernetes.io/enforce: privileged
```

Add to `apps/euronodes/immich/kustomization.yaml`:

```yaml
resources:
  - ../../base/immich
  - rclone-secret.sops.yaml
  - rclone-mount.yaml
patches:
  - path: namespace-patch.yaml
    target:
      kind: Namespace
      name: immich
  # (keep the existing helmrelease-patch.yaml and library-pvc-patch.yaml entries)
```

- [ ] **Step 7: Wire the mounts into Immich**

Append under `spec.values.server` in `apps/euronodes/immich/helmrelease-patch.yaml`:

```yaml
      persistence:
        rclone-library:
          type: hostPath
          hostPath: /var/mnt/rclone/library
          hostPathType: Directory
          globalMounts:
            - path: /usr/src/app/upload/library
              mountPropagation: HostToContainer
        rclone-upload:
          type: hostPath
          hostPath: /var/mnt/rclone/upload
          hostPathType: Directory
          globalMounts:
            - path: /usr/src/app/upload/upload
              mountPropagation: HostToContainer
        rclone-sentinel:
          type: hostPath
          hostPath: /var/mnt/rclone
          hostPathType: Directory
          advancedMounts:
            main:
              wait-mount:
                - path: /mnt/rclone
                  mountPropagation: HostToContainer
      controllers:
        main:
          initContainers:
            wait-mount:
              image:
                repository: busybox
                tag: "1.37"
              command:
                - sh
                - -c
                - until [ -f /mnt/rclone/.mounted ]; do echo waiting for rclone mount; sleep 5; done
```
If `helm template` (Step 8) shows the chart's schema differs (bjw-s `common` keys), adapt the keys but keep the semantics: two host-path mounts with `HostToContainer` propagation at the paths above, plus the init container that waits for `/mnt/rclone/.mounted`. The sentinel exists only when the crypt mount is up, so Immich can never write originals into an empty host directory.

- [ ] **Step 8: Render, template and diff**

```sh
kubectl kustomize apps/euronodes/immich > /tmp/immich-render.yaml
grep -c 'privileged: true' /tmp/immich-render.yaml
kubectl kustomize apps/euronodes/immich | yq 'select(.kind=="HelmRelease") | .spec.values' > /tmp/values.yaml
helm pull oci://ghcr.io/immich-app/immich-charts/immich --version 0.13.1 --untar -d /tmp/chart
helm template immich /tmp/chart/immich -n immich -f /tmp/values.yaml | grep -E 'rclone|HostToContainer|wait-mount'
flux diff kustomization apps --path ./apps/euronodes
rm -rf /tmp/chart /tmp/values.yaml /tmp/immich-render.yaml
```
Expected: `privileged: true` count `1`; the templated Immich Deployment contains the two hostPath volumes with `HostToContainer` and the `wait-mount` init container; `flux diff` shows the rclone resources and Immich change.

- [ ] **Step 9: Commit, push (ask user), reconcile**

```bash
git add apps/euronodes/immich
git commit -m "feat(euronodes): rclone crypt S3 mount for immich originals

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
After the user agrees to push: `git push && flux reconcile kustomization apps --with-source`, then:

```sh
kubectl -n immich get pods
kubectl -n immich logs deploy/rclone-mount --tail=20
```
Expected: `rclone-mount` Running, Immich Running after its init container passes.

- [ ] **Step 10: The gate — all must pass before real photos are uploaded**

Run each and record the result in the PR description. Use a throwaway Immich user and a handful of test photos and a 100 MB video.

1. **Upload and read:** upload via the web UI; open each photo; play the video; confirm thumbnails generated (`kubectl -n immich logs deploy/immich-server --tail=50` has no `ENOSYS` or `EIO`).
2. **Storage is encrypted at rest:** `rclone lsf s3:$S3_BUCKET` (with the Step 4 environment) shows only encrypted names; none match the uploaded filenames.
3. **Storage-template migration:** in Immich Admin → Settings → Storage Template, enable it and run the *Storage Template Migration* job. Expected: job completes, no errors, files appear under `library/` via `kubectl -n immich exec deploy/rclone-mount -- ls /mnt/rclone/library`.
4. **Kill mid-upload:** start uploading a 500 MB file; within 2 seconds run `kubectl -n immich delete pod -l app=rclone-mount --force`; let the pod return; retry the upload to completion; compare `sha256sum` of the original with a download from Immich. Expected: identical hashes, and no partial file left in the library listing.
5. **Wrong start order:** `kubectl -n immich scale deploy/rclone-mount --replicas=0`, then `kubectl -n immich rollout restart deploy/immich-server`. Expected: the new Immich pod stays in `Init` waiting for the mount and writes nothing. Scale rclone back to 1; Immich starts.
6. **Reboot:** `talosctl reboot`, wait for the node; expected: rclone, then Immich come back without manual action and the photos are still readable.

If any item fails: do not proceed. Record the failure in the spec (open items) and switch to the fallback in the spec (JuiceFS with client-side encryption, or a larger local disk) with a new plan.

---

### Task 8: Backups to the Hetzner Storage Box

**Files:**
- Create: `apps/euronodes/backup/{kustomization.yaml,namespace.yaml,restic-secret.sops.yaml,pocket-id-dump.yaml,restic-backup.yaml}`
- Modify: `apps/euronodes/kustomization.yaml`

**Interfaces:**
- Consumes: the rclone host path `/var/mnt/rclone` and the PVC `immich-library-pvc` (namespace `immich`); CNPG secret `pocket-id-database-app` (key `uri`, namespace `identity`).
- Produces: CronJob `pocket-id-dump` (writes to PVC `backup-staging`), CronJob `restic-backup` (restic repo at the Storage Box); Secret `restic-secrets` with `RESTIC_PASSWORD`, `RESTIC_REPOSITORY`, `ssh-privatekey`, `known_hosts`.

All backup workloads live in namespace `immich` so they can mount the Immich PVC and the shared host path; they are in a separate kustomization for clarity.

- [ ] **Step 1: User creates the Storage Box and SSH access**

Ask the user to: order a Storage Box, enable SSH support, create an ed25519 key (`ssh-keygen -t ed25519 -f ~/.ssh/euronodes-backup -N ''`), install the public key on the box (`install-ssh-key` in Hetzner's docs), create a directory `restic-immich`, and give the box username `uNNNNNN` and host `uNNNNNN.your-storagebox.de`. Verify:

```sh
ssh -p 23 -i ~/.ssh/euronodes-backup uNNNNNN@uNNNNNN.your-storagebox.de ls
ssh-keyscan -p 23 uNNNNNN.your-storagebox.de > /tmp/known_hosts
```
Expected: lists the home directory; `known_hosts` has host keys. The private key then goes only into the SOPS secret and the password manager.

- [ ] **Step 2: Restic secret**

```sh
export SOPS_AGE_KEY_FILE=~/.config/sops/age/euronodes.agekey
export RESTIC_PW=$(openssl rand -base64 32)    # save in the password manager now
mkdir -p apps/euronodes/backup
cat > apps/euronodes/backup/restic-secret.sops.yaml <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: restic-secrets
  namespace: immich
stringData:
  RESTIC_PASSWORD: $RESTIC_PW
  RESTIC_REPOSITORY: sftp:uNNNNNN@uNNNNNN.your-storagebox.de:restic-immich
  known_hosts: |
$(sed 's/^/    /' /tmp/known_hosts)
  ssh-privatekey: |
$(sed 's/^/    /' ~/.ssh/euronodes-backup)
EOF
sops --encrypt --in-place apps/euronodes/backup/restic-secret.sops.yaml
rm /tmp/known_hosts
grep -c 'ENC\[' apps/euronodes/backup/restic-secret.sops.yaml
```
(Replace `uNNNNNN` with the real username before running.) Expected: non-zero. Tell the user the restic password must also be in their password manager: without it the backup is unreadable.

- [ ] **Step 3: Pocket ID dump CronJob**

`apps/euronodes/backup/pocket-id-dump.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: backup-staging
  namespace: immich
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 2Gi
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: pocket-id-dump
  namespace: immich
spec:
  schedule: "30 2 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: dump
              image: ghcr.io/cloudnative-pg/postgresql:18-standard-trixie
              command:
                - sh
                - -c
                - pg_dump --format=custom --file=/staging/pocket-id.dump "$POCKET_ID_DB_URI"
              env:
                - name: POCKET_ID_DB_URI
                  valueFrom:
                    secretKeyRef:
                      name: pocket-id-database-app-copy
                      key: uri
              volumeMounts:
                - name: staging
                  mountPath: /staging
          volumes:
            - name: staging
              persistentVolumeClaim:
                claimName: backup-staging
```
A Secret cannot be read across namespaces, so this CronJob needs a copy of the Pocket ID DB URI in namespace `immich`. Add this step to Step 2's flow: after Pocket ID's database exists, create it without storing it plaintext:

```sh
URI=$(kubectl -n identity get secret pocket-id-database-app -o jsonpath='{.data.uri}' | base64 -d)
cat > apps/euronodes/backup/pocket-id-db-secret.sops.yaml <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: pocket-id-database-app-copy
  namespace: immich
stringData:
  uri: $URI
EOF
sops --encrypt --in-place apps/euronodes/backup/pocket-id-db-secret.sops.yaml
unset URI
```
If CNPG rotates that password the copy goes stale; note this in `docs/euronodes.md` (Task 9) and re-create the copy then.

- [ ] **Step 4: restic backup CronJob**

`apps/euronodes/backup/restic-backup.yaml`:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: restic-backup
  namespace: immich
spec:
  schedule: "0 4 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          initContainers:
            - name: wait-mount
              image: busybox:1.37
              command: [sh, -c, "test -f /mnt/rclone/.mounted"]
              volumeMounts:
                - name: rclone
                  mountPath: /mnt/rclone
                  mountPropagation: HostToContainer
          containers:
            - name: restic
              image: restic/restic:RESTIC_TAG
              command:
                - sh
                - -c
                - |
                  set -e
                  restic snapshots >/dev/null 2>&1 || restic init
                  restic backup --tag immich \
                    /mnt/rclone/library /mnt/rclone/upload \
                    /immich/profile /immich/backups /staging
                  restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
                  restic check --read-data-subset=5%
              envFrom:
                - secretRef:
                    name: restic-secrets
              env:
                - name: RESTIC_SFTP_ARGS
                  value: "-p 23 -i /ssh/ssh-privatekey -o UserKnownHostsFile=/ssh/known_hosts -o StrictHostKeyChecking=yes"
              volumeMounts:
                - name: rclone
                  mountPath: /mnt/rclone
                  mountPropagation: HostToContainer
                  readOnly: true
                - name: immich
                  mountPath: /immich
                  readOnly: true
                - name: staging
                  mountPath: /staging
                  readOnly: true
                - name: ssh
                  mountPath: /ssh
                  readOnly: true
          volumes:
            - name: rclone
              hostPath:
                path: /var/mnt/rclone
                type: Directory
            - name: immich
              persistentVolumeClaim:
                claimName: immich-library-pvc
            - name: staging
              persistentVolumeClaim:
                claimName: backup-staging
            - name: ssh
              secret:
                secretName: restic-secrets
                defaultMode: 0400
                items:
                  - key: ssh-privatekey
                    path: ssh-privatekey
                  - key: known_hosts
                    path: known_hosts
```
The restic sftp backend takes extra arguments with `-o sftp.args="..."`: add `args: ["-o", "sftp.args=$(RESTIC_SFTP_ARGS)"]`-style flags to each `restic` call, or export `RESTIC_REPOSITORY` with `sftp:` and set `-o sftp.args` in the script. Use this in the script, replacing each `restic` call by `restic -o sftp.args="$RESTIC_SFTP_ARGS"`. Pin the tag:

```sh
gh release list -R restic/restic -L 1
sed -i '' "s/RESTIC_TAG/<tag without v>/" apps/euronodes/backup/restic-backup.yaml
grep -c RESTIC_TAG apps/euronodes/backup/restic-backup.yaml
```
Expected: `0`.

The Immich PVC is ReadWriteOnce: the CronJob pod must run on the same node as Immich. On a single node that is always true.

- [ ] **Step 5: Kustomization and wiring**

`apps/euronodes/backup/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - restic-secret.sops.yaml
  - pocket-id-db-secret.sops.yaml
  - pocket-id-dump.yaml
  - restic-backup.yaml
```
Add `  - backup` to `resources:` in `apps/euronodes/kustomization.yaml`.

- [ ] **Step 6: Render and diff**

```sh
kubectl kustomize apps/euronodes | grep -E 'name: (restic-backup|pocket-id-dump|backup-staging)'
flux diff kustomization apps --path ./apps/euronodes
```
Expected: the three names present; `flux diff` shows only the new resources.

- [ ] **Step 7: Commit, push (ask user), run once by hand**

```bash
git add apps/euronodes
git commit -m "feat(euronodes): restic backups to hetzner storage box

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
After pushing and reconciling:

```sh
kubectl -n immich create job --from=cronjob/pocket-id-dump dump-manual
kubectl -n immich wait --for=condition=complete job/dump-manual --timeout=300s
kubectl -n immich create job --from=cronjob/restic-backup backup-manual
kubectl -n immich wait --for=condition=complete job/backup-manual --timeout=3600s
kubectl -n immich logs job/backup-manual | tail -20
```
Expected: both jobs complete; the log shows a snapshot ID and a successful `restic check`. Delete the manual jobs afterwards.

---

### Task 9: Restore drill and runbook

**Files:**
- Create: `docs/euronodes.md`
- Modify: `README.md`, `flux-bootstrap/README.md`, `docs/flux.md` (age-key tables), `.gitignore`

**Interfaces:**
- Consumes: everything above; restic repo from Task 8.
- Produces: `docs/euronodes.md` as the single runbook; a verified restore procedure.

- [ ] **Step 1: File restore test (originals)**

```sh
kubectl -n immich run restic-restore --rm -i --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"r","image":"restic/restic:RESTIC_TAG","command":["sh","-c","restic -o sftp.args=\"$RESTIC_SFTP_ARGS\" restore latest --target /tmp/r --include /mnt/rclone/library && find /tmp/r -type f | head"],"envFrom":[{"secretRef":{"name":"restic-secrets"}}],"env":[{"name":"RESTIC_SFTP_ARGS","value":"-p 23 -i /ssh/ssh-privatekey -o UserKnownHostsFile=/ssh/known_hosts"}],"volumeMounts":[{"name":"ssh","mountPath":"/ssh"}]}],"volumes":[{"name":"ssh","secret":{"secretName":"restic-secrets","defaultMode":256}}]}}' \
  --image=restic/restic:RESTIC_TAG
```
(Use the pinned tag.) Expected: the test photos from Task 7 are listed. Compare the checksum of one restored file with the original.

- [ ] **Step 2: Database restore test into a scratch database**

```sh
kubectl -n immich exec -it deploy/immich-server -- true   # sanity: namespace is healthy
```
Restore `pocket-id.dump` from the latest snapshot to a temporary PVC path, then:

```sh
pg_restore --dbname="postgresql://.../scratch" --no-owner pocket-id.dump
```
against a throwaway database created in the Pocket ID CNPG cluster (`CREATE DATABASE scratch;`), check table counts match the live database, then drop `scratch`. Record the exact working commands in the runbook.

- [ ] **Step 3: Write `docs/euronodes.md`**

Sections (all with the commands used in Tasks 2–8 so it stands alone): *What EuroNodes is and what that means* (facts from the spec: Proxmox/KVM, custom ISO, public IPv4, no block storage); *Decisions*; *Provision* (panel steps: order, hostname, ISO, dedicated IPv4); *Talos* (steps 1–5 of Task 2 and 3, encryption notes, upgrade command from Task 2 Step 3); *Flux on this cluster* (Task 6); *DNS and TLS*; *Immich storage* (rclone mount, the gate results from Task 7 Step 10); *Backups* (restic, keys that live in the password manager: age key, rclone crypt passwords, restic password); *Restore* (the order below, with the commands from this task's drill); *Known risks* (VM UUID change makes volumes unreadable; Pocket ID DB URI copy goes stale after a password rotation; S3 bucket is the live copy and not a backup).

Restore order to document: (1) new VM, Talos with the same `secrets.sops.yaml`; (2) Flux bootstrap with the same age key; (3) wait for CNPG and the apps; stop Immich (`scale deploy/immich-server --replicas=0`); (4) restore Immich's DB dump from restic (`/immich/backups`) and the Pocket ID dump; (5) remount rclone crypt (same bucket and passwords) and verify `.mounted`; (6) if the bucket is gone, restore `library` and `upload` from restic into a new bucket first; (7) start Immich; run the admin jobs *Missing* for thumbnails, encoded video, smart search and faces.

- [ ] **Step 4: Update the existing docs**

- `README.md`: add `talos/euronodes/` and `docs/euronodes.md` to the layout list, matching the tierhive lines.
- `flux-bootstrap/README.md` and `docs/flux.md`: add a row to both age-key tables: `euronodes | ~/.config/sops/age/euronodes.agekey | */euronodes/**, talos/euronodes/secrets*`.
- `.gitignore`: add `*-pwd.txt` so a password note can never be staged by `git add -A`.

Verify:

```sh
grep -n euronodes README.md flux-bootstrap/README.md docs/flux.md | head
git check-ignore -v test-pwd.txt
```
Expected: matches in all three docs; the ignore rule reports `.gitignore`.

- [ ] **Step 5: Commit**

```bash
git add docs/euronodes.md README.md flux-bootstrap/README.md docs/flux.md .gitignore
git commit -m "docs(euronodes): runbook, restore procedure and key tables

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Merge procedure (user-driven)

**Files:**
- Modify (at merge time only): `clusters/euronodes/flux-system/gotk-sync.yaml`

**Interfaces:**
- Consumes: the branch with everything above; the live cluster tracking `feat/euronodes`.
- Produces: `main` containing the cluster; the live cluster tracking `main`.

- [ ] **Step 1: Stop and hand over**

Do not merge. Summarize the branch, the gate results (Task 7) and the restore drill (Task 9) to the user and ask whether to merge. The user decides.

- [ ] **Step 2: When (and only when) the user agrees — follow `docs/flux.md` "At merge time"**

Order matters. On the branch, set `clusters/euronodes/flux-system/gotk-sync.yaml` `spec.ref.branch` to `main` and commit **locally only**, then merge to `main` and push `main` first, and only then repoint the live cluster:

```sh
kubectl -n flux-system patch gitrepository flux-system --type=merge \
  -p '{"spec":{"ref":{"branch":"main"}}}'
flux reconcile kustomization flux-system --with-source
flux get kustomizations
```
Expected: all Kustomizations `READY True`. Never push the `gotk-sync.yaml` = `main` commit to `feat/euronodes` while the cluster still tracks that branch: Flux would follow it to `main` and prune everything not yet merged.

- [ ] **Step 3: Clean up**

```sh
git branch -D feat/euronodes     # squash-merged branches are not seen as merged
```

---

## Self-review notes

- **Spec coverage:** network, disk, encryption, install pin (Tasks 2–3); `local-path` (Task 4 configs, Task 3 verification); Traefik/TLS/HTTP-01 (Tasks 4–6); rclone crypt and the gate (Task 7); restic to Hetzner, two keys, restore test (Tasks 8–9); repo layout, age key, docs (Tasks 1, 9); merge rules (Task 10). Out-of-scope items are not planned.
- **Gap found while planning:** the spec relies on Immich DB dumps for Postgres backups but Pocket ID's database had no backup path; Task 8 adds `pocket-id-dump`.
- **Open items from the spec that become steps:** Talos encryption syntax and `STATE` key types (Task 2 Step 3), installer image path (Task 2 Step 4 uses `--install-image`), S3 endpoint behavior (Task 7 Steps 4 and 10), Storage Box SSH access (Task 8 Step 1).
- **Values supplied by the user at run time** (not decided here): ACME email, S3 endpoint/bucket/keys, rclone crypt passwords, Storage Box username. The tasks ask for them explicitly before writing any file that contains them.
