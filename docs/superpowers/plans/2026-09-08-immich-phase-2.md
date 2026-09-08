# Immich (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This project runs interactively on a branch, not via subagents** (per
> the user). Flux tracks `feat/immich` during development; live checks run
> against the cluster; merge to `main` is user-driven.

**Goal:** Deploy Immich on the `laptop` cluster via Flux — a
CloudNativePG Postgres with the VectorChord extension, the chart's
embedded Valkey, a photo-library PVC, and a Traefik Ingress at
`immich.home.arpa`.

**Architecture:** New `apps/` layer. `apps/base/immich/` holds the
namespace, CNPG `Cluster` + `Database`, library PVC, Immich
`OCIRepository` + `HelmRelease`. `apps/laptop/immich/` is a thin overlay
(host, sizes). A new Flux `Kustomization` `apps` (in
`clusters/laptop/apps.yaml`) reconciles `apps/laptop`, `dependsOn:
infra-controllers` (needs the CNPG operator CRDs and the Traefik
IngressClass).

**Tech Stack:** Flux 2.9.5, Immich chart `oci://ghcr.io/immich-app/immich-charts/immich`
`0.13.1` (appVersion `v3.0.0`, bjw-s common lib 5.0.1), CloudNativePG
operator 1.30.0, PostgreSQL 18 + VectorChord (`vchord`), Valkey,
Traefik, local-path storage.

**Spec:** `docs/superpowers/specs/2026-09-08-gitops-flux-immich-design.md`

## Global Constraints

- Cluster: `laptop` only. Node IP `192.168.64.5`. Default StorageClass
  `local-path` (no volume expansion — sizes are final).
- POC sizing — the 2nd disk is **20 GiB total for all apps**. Be frugal:
  library PVC `5Gi`, Postgres storage `2Gi`, Valkey + ML cache stay
  `emptyDir` (chart defaults; models re-download on restart, acceptable).
- Immich host: `immich.home.arpa`. Ingress class: `traefik`.
- Every `HelmRelease` / `OCIRepository` pins an explicit version.
- No SOPS secret needed: CNPG auto-generates Secret
  `immich-database-app` (keys `host`, `user`, `password`, `dbname`,
  `port`, `uri`) in the app namespace; Immich reads DB creds from it.
- ImageVolume feature gate is enabled on the cluster — the declarative
  `vchord` extension image mechanism works.
- Flux tracks `feat/immich`. Commit + push after each task; verify live
  before the next.
- Commit messages: Conventional Commits.
- `kubeconfig`: `os/context/kubeconfig`.

## File Structure

```
apps/
  base/
    immich/
      namespace.yaml            # namespace: immich
      postgres-cluster.yaml     # CNPG Cluster immich-database (PG18 + vchord)
      postgres-database.yaml    # CNPG Database immich-database (extensions)
      library-pvc.yaml          # PVC immich-library-pvc
      ocirepository.yaml        # oci://ghcr.io/immich-app/immich-charts/immich 0.13.1
      helmrelease.yaml          # Immich: valkey on, DB env from secret, ingress
      kustomization.yaml
  laptop/
    immich/
      kustomization.yaml        # -> ../../base/immich
      helmrelease-patch.yaml    # host immich.home.arpa, library 5Gi, pg 2Gi
clusters/laptop/
  apps.yaml                     # Flux Kustomization "apps" -> ./apps/laptop (dependsOn infra-controllers)
```

`apps/laptop/kustomization.yaml` aggregates the overlay dirs (just
`immich` for now); the Flux `apps` Kustomization points its `path` at
`./apps/laptop`.

---

## Task 1: apps layer skeleton + namespace + Flux `apps` Kustomization

**Files:**
- Create: `apps/base/immich/namespace.yaml`
- Create: `apps/base/immich/kustomization.yaml`
- Create: `apps/laptop/immich/kustomization.yaml`
- Create: `apps/laptop/kustomization.yaml`
- Create: `clusters/laptop/apps.yaml`

**Interfaces:**
- Consumes: `flux-system` GitRepository; `infra-controllers` Kustomization.
- Produces:
  - Flux `Kustomization` `apps` (namespace `flux-system`, path
    `./apps/laptop`, `dependsOn: infra-controllers`, `prune: true`,
    `wait: true`, `interval: 10m`).
  - Namespace `immich`.

- [ ] **Step 1: Namespace**

Create `apps/base/immich/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: immich
```

- [ ] **Step 2: base kustomization (namespace only for now)**

Create `apps/base/immich/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
```

- [ ] **Step 3: laptop immich overlay (passthrough for now)**

Create `apps/laptop/immich/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base/immich
```

- [ ] **Step 4: laptop apps aggregator**

Create `apps/laptop/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - immich
```

- [ ] **Step 5: Flux `apps` Kustomization**

Create `clusters/laptop/apps.yaml`:
```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: apps
  namespace: flux-system
spec:
  dependsOn:
    - name: infra-controllers
  interval: 10m
  retryInterval: 1m
  timeout: 10m
  sourceRef:
    kind: GitRepository
    name: flux-system
  path: ./apps/laptop
  prune: true
  wait: true
```

- [ ] **Step 6: Commit, push, verify**

```bash
cd /Users/arnaud/Development/arnaud-deprez/home-lab
export KUBECONFIG="$PWD/os/context/kubeconfig"
git add apps/ clusters/laptop/apps.yaml
git commit -m "feat(apps): add apps layer and immich namespace"
git push
flux reconcile kustomization flux-system --with-source
flux get kustomizations
kubectl get ns immich
```
Expected: `apps` Kustomization `Ready: True`; namespace `immich` exists.

---

## Task 2: CloudNativePG Postgres for Immich

**Files:**
- Create: `apps/base/immich/postgres-cluster.yaml`
- Create: `apps/base/immich/postgres-database.yaml`
- Modify: `apps/base/immich/kustomization.yaml`

**Interfaces:**
- Consumes: CNPG operator (CRDs `clusters`, `databases`) from
  `infra-controllers`; namespace `immich` (Task 1).
- Produces:
  - CNPG `Cluster` `immich-database` in `immich` (1 instance, `2Gi`
    `local-path` storage, PG18 + `vchord`).
  - CNPG `Database` `immich-database` → database `app`, owner `app`,
    extensions `vector`, `vchord`, `earthdistance`, `cube`.
  - Secret `immich-database-app` (CNPG-generated) with connection keys.
  - Service `immich-database-rw` in `immich`.

- [ ] **Step 1: Cluster manifest**

Create `apps/base/immich/postgres-cluster.yaml` (from the immich-charts
`local/cloudnative-pg.yaml` example, with explicit storageClass):
```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: immich-database
  namespace: immich
spec:
  instances: 1
  imageName: ghcr.io/cloudnative-pg/postgresql:18-standard-trixie
  storage:
    size: 2Gi
    storageClass: local-path
  postgresql:
    shared_preload_libraries:
      - "vchord.so"
    extensions:
      - name: vchord
        image:
          reference: ghcr.io/tensorchord/vchord-scratch:pg18-v1.1.1
        dynamic_library_path:
          - /usr/lib/postgresql/18/lib
        extension_control_path:
          - /usr/share/postgresql/18/
```

- [ ] **Step 2: Database manifest**

Create `apps/base/immich/postgres-database.yaml`:
```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: immich-database
  namespace: immich
spec:
  name: app
  owner: app
  cluster:
    name: immich-database
  extensions:
    - name: vector
      ensure: present
    - name: vchord
      ensure: present
    - name: earthdistance
      ensure: present
    - name: cube
      ensure: present
```

- [ ] **Step 3: add to base kustomization**

Update `apps/base/immich/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - postgres-cluster.yaml
  - postgres-database.yaml
```

- [ ] **Step 4: Commit, push, verify**

```bash
cd /Users/arnaud/Development/arnaud-deprez/home-lab
export KUBECONFIG="$PWD/os/context/kubeconfig"
git add apps/base/immich/
git commit -m "feat(immich): add CloudNativePG Postgres with VectorChord"
git push
flux reconcile kustomization apps --with-source
# CNPG takes 1-3 min to provision
for i in $(seq 1 30); do sleep 6; kubectl -n immich get cluster immich-database -o jsonpath='{.status.phase}{"\n"}' 2>/dev/null; done
kubectl -n immich get cluster,database,pods,secret
```
Expected: `Cluster` phase `Cluster in healthy state`; `Database` shows
`Applied: true` (or `Ready`); pod `immich-database-1` `Running` (2/2 or
1/1); Secret `immich-database-app` present.

- [ ] **Step 5: Confirm the extension is really installed**

```bash
kubectl -n immich exec -it immich-database-1 -- \
  psql -U app -d app -c "\dx"
```
Expected: `vchord`, `vector`, `cube`, `earthdistance` listed.

---

## Task 3: Library PVC + Immich HelmRelease

**Files:**
- Create: `apps/base/immich/library-pvc.yaml`
- Create: `apps/base/immich/ocirepository.yaml`
- Create: `apps/base/immich/helmrelease.yaml`
- Create: `apps/laptop/immich/helmrelease-patch.yaml`
- Modify: `apps/base/immich/kustomization.yaml`
- Modify: `apps/laptop/immich/kustomization.yaml`

**Interfaces:**
- Consumes: Secret `immich-database-app`, Service `immich-database-rw`
  (Task 2); Traefik IngressClass `traefik` (infra); `local-path` SC.
- Produces:
  - PVC `immich-library-pvc` (`5Gi`, RWO, `local-path`) in `immich`.
  - `OCIRepository` `immich` (chart tag `0.13.1`) in `immich`.
  - `HelmRelease` `immich` in `immich` — Valkey enabled, server DB env
    from `immich-database-app`, library `existingClaim`, Ingress
    `immich.home.arpa` class `traefik`.
  - Services `immich-server`, `immich-machine-learning`, `immich-valkey`.

- [ ] **Step 1: Library PVC**

Create `apps/base/immich/library-pvc.yaml`:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: immich-library-pvc
  namespace: immich
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 5Gi
```

- [ ] **Step 2: OCIRepository**

Create `apps/base/immich/ocirepository.yaml`:
```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: immich
  namespace: immich
spec:
  interval: 24h
  url: oci://ghcr.io/immich-app/immich-charts/immich
  ref:
    tag: "0.13.1"
```

- [ ] **Step 3: HelmRelease (base — no host)**

Create `apps/base/immich/helmrelease.yaml`:
```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: immich
  namespace: immich
spec:
  interval: 30m
  timeout: 10m
  chartRef:
    kind: OCIRepository
    name: immich
  install:
    remediation:
      retries: 3
  upgrade:
    remediation:
      retries: 3
  values:
    valkey:
      enabled: true
    immich:
      persistence:
        library:
          existingClaim: immich-library-pvc
    server:
      controllers:
        main:
          containers:
            main:
              env:
                DB_HOSTNAME:
                  valueFrom:
                    secretKeyRef:
                      name: immich-database-app
                      key: host
                DB_USERNAME:
                  valueFrom:
                    secretKeyRef:
                      name: immich-database-app
                      key: user
                DB_PASSWORD:
                  valueFrom:
                    secretKeyRef:
                      name: immich-database-app
                      key: password
                DB_DATABASE_NAME:
                  valueFrom:
                    secretKeyRef:
                      name: immich-database-app
                      key: dbname
      ingress:
        main:
          enabled: false
```

- [ ] **Step 4: laptop patch (host + ingress on)**

Create `apps/laptop/immich/helmrelease-patch.yaml`:
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
          hosts:
            - host: immich.home.arpa
              paths:
                - path: "/"
                  service:
                    identifier: main
          tls: []
```

- [ ] **Step 5: kustomizations**

Update `apps/base/immich/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - postgres-cluster.yaml
  - postgres-database.yaml
  - library-pvc.yaml
  - ocirepository.yaml
  - helmrelease.yaml
```

Update `apps/laptop/immich/kustomization.yaml`:
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

- [ ] **Step 6: Offline render check**

```bash
cd /Users/arnaud/Development/arnaud-deprez/home-lab
kubectl kustomize apps/laptop/immich/ | grep -E 'kind:|host:|existingClaim|className|DB_'
```
Expected: HelmRelease shows `className: traefik`, `host: immich.home.arpa`,
`existingClaim: immich-library-pvc`, the four `DB_*` env entries.

- [ ] **Step 7: Commit, push, verify**

```bash
cd /Users/arnaud/Development/arnaud-deprez/home-lab
export KUBECONFIG="$PWD/os/context/kubeconfig"
git add apps/
git commit -m "feat(immich): add library PVC and Immich HelmRelease with ingress"
git push
flux reconcile kustomization apps --with-source
for i in $(seq 1 40); do sleep 8; kubectl -n immich get helmrelease immich -o jsonpath='{.status.conditions[?(@.type=="Ready")].status} {.status.conditions[?(@.type=="Ready")].message}{"\n"}' 2>/dev/null; done
kubectl -n immich get pods,pvc,ingress
```
Expected: `HelmRelease immich` `Ready: True`; pods
`immich-server-*`, `immich-machine-learning-*`, `immich-valkey-*`
`Running`; PVC `immich-library-pvc` `Bound`; Ingress `immich` with host
`immich.home.arpa`.

Note: `immich-server` may `CrashLoopBackOff` for the first 1-2 minutes
while Postgres finishes bootstrapping — it recovers on its own. If it is
still failing after 5 minutes, check
`kubectl -n immich logs deploy/immich-server` for the DB error.

---

## Task 4: Reach Immich end-to-end + close-out

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the hosts entry (user's Mac)**

```bash
echo "192.168.64.5  immich.home.arpa" | sudo tee -a /etc/hosts
```

- [ ] **Step 2: Hit the web UI through Traefik**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: immich.home.arpa' http://192.168.64.5/
curl -s -H 'Host: immich.home.arpa' http://192.168.64.5/api/server/ping
```
Expected: `200` (or `302` to `/auth/login`) for `/`; `{"res":"pong"}`
for the ping.

- [ ] **Step 3: Browser smoke test**

Open `http://immich.home.arpa/` — the Immich onboarding page loads.
Create the admin account, upload one photo, confirm the thumbnail
renders (exercises server + Postgres + library PVC + machine-learning).

- [ ] **Step 4: Persistence check**

```bash
kubectl -n immich rollout restart deploy/immich-server
kubectl -n immich rollout status deploy/immich-server
```
Reload the UI — the uploaded photo and the admin account are still
there (DB + library survived the restart).

- [ ] **Step 5: Update README**

In `README.md`, tick Immich off the list and link
`apps/base/immich/` + this plan. Note `immich.home.arpa` needs an
`/etc/hosts` entry pointing at the node IP.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: Immich deployed via Flux"
git push
```

- [ ] **Step 7: Final state**

```bash
export KUBECONFIG="$PWD/os/context/kubeconfig"
flux get kustomizations
flux get helmreleases -A
kubectl -n immich get cluster,pods,pvc,ingress
```
Expected: everything `Ready: True` / `Running` / `Bound`.

---

## Merge to main (user-driven, after Task 4)

1. On `feat/immich`: set `clusters/laptop/flux-system/gotk-sync.yaml`
   `ref.branch` back to `main`, remove the TEMPORARY comment, commit.
2. `git checkout main && git merge feat/immich && git push`.
3. `kubectl -n flux-system patch gitrepository flux-system --type=merge
   -p '{"spec":{"ref":{"branch":"main"}}}'`
4. `flux reconcile kustomization flux-system --with-source` — verify all
   green on `main`.

Do NOT push a `gotk-sync=main` commit to `feat/immich` while the live
GitRepository still tracks `feat/immich` (that prunes everything not yet
on main — it happened in Phase 1).

## Self-Review

**Spec coverage:**
- `apps/base/immich` + `apps/laptop/immich` overlay → Tasks 1, 3. ✓
- CNPG `Cluster` with vectorchord image → Task 2. ✓
- Library PVC + `existingClaim` → Task 3. ✓
- `HelmRelease` with `valkey.enabled: true` → Task 3. ✓
- DB env pointing at CNPG service/secret → Task 3 Step 3. ✓
- `clusters/laptop/apps.yaml` `dependsOn` infrastructure → Task 1 Step 5. ✓
- Ingress host for laptop access → Task 3 Step 4, Task 4. ✓
- Validate: HelmRelease Ready, Cluster healthy, pods up, UI reachable,
  upload a photo, restart & confirm persistence → Task 4. ✓
- Spec said "Postgres credentials via SOPS secret" → superseded: CNPG
  auto-generates `immich-database-app`; no SOPS secret in Phase 2.
  Ruling recorded here.

**Placeholder scan:** Versions pinned (`immich` `0.13.1`, PG image
`18-standard-trixie`, `vchord-scratch:pg18-v1.1.1`). Host
`immich.home.arpa` and sizes (`5Gi`/`2Gi`) fixed per user. No TODOs.
`DB_VECTOR_EXTENSION` is intentionally not set — Immich v3 (appVersion
`v3.0.0`) defaults to VectorChord; the upstream immich-charts example for
this chart version does not set it.

**Type consistency:** namespace `immich`, CNPG names `immich-database`
(Cluster + Database), Secret `immich-database-app`, PVC
`immich-library-pvc`, HelmRelease `immich`, Flux Kustomization `apps` —
used consistently across tasks and verification commands. Chart values
paths follow bjw-s common lib 5.0.1 (`server.controllers.main.containers.main.env`,
`server.ingress.main`, `immich.persistence.library.existingClaim`,
`valkey.enabled`) as in the upstream `local/values.yaml` example.
