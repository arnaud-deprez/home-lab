# Fold `identity/` into `apps/` — migration plan

**Goal:** Pocket ID becomes a normal app (`apps/{base,laptop,tierhive}/pocket-id`);
the `identity` top-level directory and the `identity` Flux Kustomization go away.

**Hard constraint:** no data loss. Pocket ID state = PVC `pocket-id-data`
(`/app/data`, local-path) + CNPG cluster `pocket-id-database` (local-path) +
secret `pocket-id-secrets` (`encryptionKey`, SOPS — in git, but losing the key
makes the DB unreadable, so keep it identical).

## Why this is dangerous

Flux ownership is tracked by each Kustomization's inventory. Deleting the
`identity` Kustomization with `prune: true` garbage-collects everything in its
inventory — namespace, PVC, CNPG cluster — and local-path PVs are
`reclaimPolicy: Delete`, so the data is gone with the PVC.

## Design decisions

- **Keep the namespace name `identity`.** Renaming = new namespace = new PVCs.
  Only the *file location* and *owning Flux Kustomization* change. Objects keep
  the same kind/name/namespace, so `apps` adopts them via server-side apply.
- **Two commits, two reconciles.** Commit 1 makes the old Kustomization
  non-destructive; only after that is live do we remove it (commit 2).
- **`deletionPolicy: Orphan`** (Flux v2.9.5 supports it) on `identity`: deleting
  the Kustomization leaves its objects in place.

## Steps (per cluster; do laptop first, tierhive only if it is live)

Work on a feature branch (`refactor/identity-into-apps`) and point the live
cluster at it for validation, per `docs/flux.md`. Do not merge without the
user's go-ahead.

### 0. Safety net (before touching git)

```sh
# a) Retain the PVs so a mistaken PVC delete doesn't delete data
for pvc in pocket-id-data pocket-id-database-1; do
  pv=$(kubectl -n identity get pvc $pvc -o jsonpath='{.spec.volumeName}')
  kubectl patch pv "$pv" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
done

# b) Logical DB backup
kubectl -n identity exec pocket-id-database-1 -c postgres -- \
  pg_dump -U postgres -Fc app > pocket-id-db-$(date +%F).dump

# c) File backup of /app/data (keys, uploads) and the encryption key secret
kubectl -n identity exec deploy/pocket-id -- tar czf - -C /app data \
  > pocket-id-data-$(date +%F).tgz
kubectl -n identity get secret pocket-id-secrets -o yaml > pocket-id-secrets.bak.yaml  # keep OUT of git
```

Verify each artifact is non-empty and `pg_restore -l` lists the dump. Record
the pre-migration object UIDs for the final check:
`kubectl -n identity get ns,pvc,cluster,deploy -o custom-columns=K:.kind,N:.metadata.name,UID:.metadata.uid`.

### 1. Commit 1 — make `identity` non-destructive

In `clusters/<cluster>/identity.yaml` add:

```yaml
spec:
  deletionPolicy: Orphan
  prune: false
```

Push, `flux reconcile kustomization flux-system --with-source`, then **verify
it is live before continuing**:

```sh
kubectl -n flux-system get kustomization identity \
  -o jsonpath='{.spec.deletionPolicy} {.spec.prune}{"\n"}'   # Orphan false
```

### 2. Commit 2 — move the files, drop the Kustomization

```sh
git mv identity/base/pocket-id    apps/base/pocket-id
git mv identity/laptop/pocket-id  apps/laptop/pocket-id
git mv identity/tierhive/pocket-id apps/tierhive/pocket-id
git rm clusters/{laptop,tierhive}/identity.yaml
git rm -r identity            # leftover identity/*/kustomization.yaml
```

Edits:
- `apps/{laptop,tierhive}/kustomization.yaml`: add `- pocket-id` to `resources`.
- Remove `- identity.yaml` from `clusters/{laptop,tierhive}/kustomization.yaml`.
- Relative refs inside the overlays (`../../base/pocket-id`) stay valid —
  same depth.
- Do NOT change `namespace.yaml`, PVC, CNPG cluster, or secret contents.

Validate locally before pushing (flux-ops skill):
`kustomize build apps/laptop` (and tierhive) — confirm Pocket ID objects render
with unchanged names/namespaces, and diff the render against the old
`kustomize build identity/laptop` output: only metadata labels may differ.
`flux diff kustomization apps --path ./apps/laptop` against the live cluster
should show no spec changes to the PVC / CNPG Cluster / Deployment.

### 3. Verify after reconcile

```sh
flux reconcile kustomization flux-system --with-source
flux reconcile kustomization apps --with-source
flux get kustomizations                       # identity gone, apps Ready
kubectl -n identity get ns,pvc,cluster,deploy -o custom-columns=K:.kind,N:.metadata.name,UID:.metadata.uid
```

- UIDs identical to step 0 (objects were adopted, not recreated).
- PVs still `Bound`; Pocket ID pod not restarted unexpectedly
  (`kubectl -n identity get pod`), login with an existing passkey works.
- `kubectl -n identity get ns identity -L kustomize.toolkit.fluxcd.io/name`
  → `apps`.

### 4. Cleanup (after a day of confidence)

- Set the PVs back to `Delete` only if wanted (default for local-path); leaving
  `Retain` is harmless but leaks volumes on future intentional deletes.
- Delete local backup files once verified restorable.

## Rollback

- Before step 2 is pushed: revert commit 1 (or just leave it, harmless).
- After: objects were never deleted, so `git revert` of commit 2 re-creates the
  `identity` Kustomization, which re-adopts them. If anything was deleted,
  restore from the PV (Retain) or the step-0 dumps.

## Docs to update in commit 2

- `CLAUDE.md`: layout block (drop `identity.yaml` and `identity/` lines),
  reconcile-order paragraph (`controllers -> configs -> apps`), "Every
  `infrastructure/*`, `identity/`, and `apps/`" sentence; note that apps opt into
  SSO and Pocket ID lives in `apps/`.
- `docs/flux.md` lines ~24–36 (same layout/ordering text).
- `docs/tierhive.md:260` ("identity/apps are never deployed" → "apps").
- Historic spec/plan files under `docs/superpowers/` are left as-is.

## Future

If something later needs Pocket ID up *first* (oauth2-proxy, forward-auth),
split `apps` into per-app Flux Kustomizations (`apps-pocket-id` → `apps-immich`)
and use `dependsOn` — don't resurrect a top-level `identity` layer.
