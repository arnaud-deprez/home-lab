# Backups and restore (EuroNodes cluster)

How the EuroNodes cluster is backed up with [restic](https://restic.net), and how to get things back.
Written to be followed under pressure: every scenario has numbered steps with copy-paste commands.
Cluster setup itself (Talos, Flux, rclone, TLS) is in [`euronodes.md`](euronodes.md).
A third copy on a personal external disk, made by hand every few months, is in [`disk-backup.md`](disk-backup.md).

**Read this first:** you need three things to restore anything, and none of them is in the cluster.

| You need | Where it is | If you lose it |
| -------- | ----------- | -------------- |
| The **restic password** | Your password manager (also in the repo, encrypted, readable only with the age key) | The backups are unreadable. There is no recovery. |
| The **Storage Box SSH key** (`~/.ssh/euronodes-backup`) | Your password manager and your laptop | Install a new key on the box in Hetzner's console; nothing is lost |
| The **age private key** (`~/.config/sops/age/euronodes.agekey`) | Your password manager | Every encrypted secret in the repo becomes unreadable (also the rclone crypt passwords). Keep it safe. |

## 1. What is backed up

| App | What | Where it lives | Repository on the Storage Box |
| --- | ---- | -------------- | ----------------------------- |
| Immich | Photo originals (`library`, `upload`) | EuroNodes S3, behind the rclone mount `/mnt/rclone` | `restic-immich` |
| Immich | Profile pictures (`profile`) and Immich's own nightly database dumps (`backups`) | Immich's local volume, mounted at `/immich` in the job | `restic-immich` |
| Pocket ID | A fresh `pg_dump` of its database, taken by the job | Postgres (CloudNativePG) | `restic-pocket-id` |
| Pocket ID | Its data folder `/app/data` (application images, profile pictures) | Local volume `pocket-id-data` | `restic-pocket-id` |

**Not backed up on purpose:** Immich thumbnails and encoded video (Immich regenerates them), Postgres
files of the running database (the dumps cover it), the container images.

The S3 bucket is the *live* copy of your photos, not a backup. The Storage Box is the second, independent copy.
The third copy is an offline external disk with the photos as plain files ([`disk-backup.md`](disk-backup.md)).

## 2. How it is set up

| | Immich | Pocket ID |
| - | ------ | --------- |
| CronJob | `restic-backup` in namespace `immich` | `pocket-id-backup` in namespace `identity` |
| Defined in | [`apps/euronodes/immich/restic-backup.yaml`](../apps/euronodes/immich/restic-backup.yaml) | [`apps/euronodes/pocket-id/restic-backup.yaml`](../apps/euronodes/pocket-id/restic-backup.yaml) |
| Credentials | [`immich/restic-secret.sops.yaml`](../apps/euronodes/immich/restic-secret.sops.yaml) | [`pocket-id/restic-secret.sops.yaml`](../apps/euronodes/pocket-id/restic-secret.sops.yaml) |
| Schedule (UTC) | 04:00 | 03:30 |
| Repository | `sftp:u695423@u695423.your-storagebox.de:restic-immich` | `…:restic-pocket-id` |
| Retention | 7 daily, 4 weekly, 6 monthly | same |
| Verification | `restic check`, reading 5% of the data | `restic check` (whole repository) |
| Run deadline | 12 hours | 1 hour |

Everything is encrypted on the cluster before it leaves it. Both repositories share **one password**; the
SSH key is shared too. The credentials secret exists once per namespace: change both together.

What the nightly run does, in order:

1. **Immich only:** an init container waits for the S3 mount marker `.mounted`; if the mount is missing the job
   fails instead of backing up an empty folder. **Pocket ID only:** an init container runs `pg_dump`.
2. Checks that the repository exists. If restic says it does not exist (exit code 10) it creates it; any other
   failure (network, wrong password = 12, lock) fails the job and creates nothing.
3. `restic backup` of the paths above (`--ignore-inode` for Immich, because the S3 mount hands out new inode
   numbers after every remount).
4. `restic forget --prune` with the retention above, then `restic check`.
5. **Immich only:** at the very end, fails the job if no Immich database dump newer than 26 hours exists in
   `/immich/backups`, so a silently broken Immich dump job does not go unnoticed. The backup itself has already run.

Guards: SSH keep-alives and a run deadline so a stalled connection cannot block later nights; strict host key
checking against the box's pinned keys.

**Immich's own database dumps:** Immich writes `immich-db-backup-*.sql.gz` into its `backups` folder every night at
02:00 and keeps the last 14. The restic job copies that folder, which is how the Immich database is backed up.

**Alerting.** Each job pings a [healthchecks.io](https://healthchecks.io) check: `/start` when the script begins and
its exit code when it ends. healthchecks.io alerts the "Powple Homelab alerts" Telegram channel (and email) when a
run fails or when no ping arrives in time, which also covers a job that never started (failed init container,
suspended CronJob, dead cluster). The ping URL is the `HC_PING_URL` key of each job's `restic-secrets` Secret
(`https://hc-ping.com/<uuid>`); without it the job runs unchanged and nothing is reported. The checks are
`euronodes-immich-backup` (cron `0 4 * * *` UTC, grace 14 h) and `euronodes-pocket-id-backup` (cron `30 3 * * *`
UTC, grace 2 h). Failed or stuck runs can still be inspected with the commands in section 3. A third daily
check, `euronodes-storagebox-usage`, alerts when the Storage Box is 80% full. How all of it fits together and
how to test it: [`monitoring.md`](monitoring.md).

## 3. Day-to-day

```sh
# the Kubernetes API is only reachable over Tailscale: see "Reaching the cluster" in euronodes.md
export KUBECONFIG=$PWD/talos/euronodes/kubeconfig

kubectl get cronjob -A                          # schedule and last run
kubectl -n immich get jobs                      # recent runs; a failed one shows Failed
kubectl -n identity get jobs
kubectl -n immich logs job/<job-name> -c restic | tail -30

# run a backup now
kubectl -n immich create job --from=cronjob/restic-backup manual-1
kubectl -n identity create job --from=cronjob/pocket-id-backup manual-1
```

A healthy run ends with `no errors were found` from `restic check`. Delete finished manual jobs afterwards.

### Look at the repositories from your laptop (no cluster needed)

```sh
brew install restic
export RESTIC_PASSWORD='...'                    # from the password manager
SB=u695423@u695423.your-storagebox.de
r() {   # usage: REPO=restic-immich r snapshots
  restic -r "sftp:$SB:${REPO:?set REPO=restic-immich or restic-pocket-id}" \
    -o sftp.command="ssh -p 23 -i $HOME/.ssh/euronodes-backup $SB -s sftp" "$@"
}

REPO=restic-immich r snapshots                  # one line per night
REPO=restic-immich r ls latest /immich/backups  # list files in the newest snapshot
REPO=restic-immich r check                      # structure check; add --read-data to verify everything (slow, big download)
```

The first connection asks you to trust the box's host key. Its ED25519 fingerprint is
`SHA256:XqONwb1S0zuj5A1CDxpOSuD2hnAArV1A3wKY7Z3sdgM`.
If a command says the repository is locked and no job is running, `r unlock` removes the stale lock.

## 4. Two ways to restore

**A. From your laptop** (works even if the cluster is gone): `restic restore` into a folder on your machine.
Good for single files and for getting the database dumps out.

```sh
REPO=restic-immich r restore latest --tag immich --target ./restore --include /immich/backups
REPO=restic-immich r dump latest /immich/backups/<file>.sql.gz > <file>.sql.gz     # one file, streamed
REPO=restic-immich r restore <snapshot-id> --target ./restore --include /immich/profile   # an older snapshot
```

**B. Inside the cluster**, with a throwaway "restic shell" pod that has the credentials and the same mounts as the
nightly job. Use it when the data has to go back into the cluster (a volume or the S3 mount):

```sh
# Immich: restic + the S3 mount (/mnt/rclone) + Immich's volume (/immich) + scratch folder (/restore)
kubectl apply -f docs/restore/restic-shell-immich.yaml
kubectl -n immich exec -it restic-shell -- sh
#   r snapshots        <- `r` is restic already wired to the box

# Pocket ID: a restic container and a postgres container sharing /restore; restic also mounts /data
kubectl apply -f docs/restore/restic-shell-pocket-id.yaml
kubectl -n identity exec -it restic-shell -c restic -- sh
kubectl -n identity exec -it restic-shell -c postgres -- sh     # pg_restore, and $DB_URI points at the live database

# when finished
kubectl -n immich delete pod restic-shell
kubectl -n identity delete pod restic-shell
```

The manifests are in [`restore/`](restore/); Flux does not apply them. They were tested on the live cluster.

**Two warnings for restores inside the cluster:**
- Always add `--exclude-xattr security.selinux`. Without it restic restores everything but exits with an error,
  because Talos does not let a container set SELinux labels.
- The scratch folder `/restore` lives on the node's 40 GB system partition. Use it for small things (dumps, profile
  pictures). **Never restore the photo originals into it:** restore them straight into the S3 mount instead (see 5.4).

## 5. Scenarios

### Before any restore that follows a loss: pause the jobs

A rebuilt cluster starts with empty databases. If the nightly jobs run, they back up that emptiness, and after
7 days they delete the older restore points. So pause them first and resume only when everything is back.
**Pause Flux first**: it owns the CronJobs and resets a manual `suspend` on its next reconcile (seen within minutes
of a new commit during the 2026-10-26 test), which would silently re-enable the backups.

```sh
flux suspend kustomization apps
kubectl -n immich patch cronjob restic-backup -p '{"spec":{"suspend":true}}'
kubectl -n identity patch cronjob pocket-id-backup -p '{"spec":{"suspend":true}}'
flux suspend helmrelease immich -n immich        # otherwise an Immich upgrade scales it back up mid-restore

# ... restore ...

flux resume helmrelease immich -n immich
flux resume kustomization apps                   # Flux puts the CronJobs back to active
kubectl get cronjob -A                           # check SUSPEND is false
kubectl -n immich create job --from=cronjob/restic-backup after-restore-1   # take a fresh backup
```

### 5.1 A file was deleted by mistake

- **Pocket ID file** (logo, profile picture): in the Pocket ID shell, `r restore latest --tag pocket-id --target /restore
  --exclude-xattr security.selinux --include /data/uploads`, then copy the file from `/restore/data/uploads/…` into `/data/uploads/…`.
- **An Immich photo you deleted inside Immich** (and emptied the trash): restoring the file alone is not enough, because
  Immich also removed its database record. Either upload the restored file again through the app, or restore an older
  database dump from before the deletion (section 5.2) — which also undoes everything done since.
- **An Immich file damaged or missing on S3 while Immich still has its record:** restore it into the same path
  (section 5.4 shows the command; use `--include <exact path>`).

### 5.2 The Immich database is lost or damaged

**Do not use Immich's in-app restore here** (Administration → Maintenance → Restore database backup). It replays the
dump as the application's database user, which on this cluster is deliberately not a superuser, and the dump re-creates
the extensions. It fails with `ERROR: permission denied to create extension "earthdistance"` /
`Must be superuser to create this extension`. It runs in one transaction, so the database is left untouched and the
attempt is harmless, but it cannot succeed. (Immich also writes `restore-point-immich-db-backup-*.sql.gz` copies of the
database as it was before each attempt; they are not nightly dumps.) The superuser restore below is the supported path
and was tested end to end on 2026-10-26.

1. **Pause the jobs and Flux** (section 5, top), and scale Immich down: `kubectl -n immich scale deploy/immich-server --replicas=0`.
2. **Get the dump.** If Immich's local volume is intact, its `backups` folder holds the nightly dumps. If not, restore them
   from restic into the volume with the Immich shell (section 4B):
   `r restore latest --tag immich --target / --exclude-xattr security.selinux --include '/immich/backups'`.
   Pick the newest `immich-db-backup-*` that predates the problem.
3. **Replay it as the PostgreSQL superuser.** The database is `app` in the CloudNativePG cluster `immich-database`.
   Do it in **two separate steps**: copy the dump to your laptop, then replay the local file. Do not pipe one
   `kubectl exec` into another: through the Tailscale API proxy such a double-hop stream is cut short (it failed twice
   on 2026-10-26 with `websocket: close 1006`, then `missing data for column` / `invalid hexadecimal data`, always rolled
   back).
   ```sh
   kubectl apply -f docs/restore/restic-shell-immich.yaml        # if the shell is not running
   kubectl -n immich exec restic-shell -- cat /immich/backups/<dump>.sql.gz > dump.sql.gz
   gzip -t dump.sql.gz                                           # must print nothing; compare the size with the original
   gzip -dc dump.sql.gz \
    | sed "s/SELECT pg_catalog.set_config('search_path', '', false);/SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g" \
    | kubectl -n immich exec -i immich-database-1 -c postgres -- \
        psql --dbname=app --username=postgres --single-transaction --set ON_ERROR_STOP=on
   ```
   Expect exit code 0 and no `ERROR` lines (about a thousand lines of normal output). Run as the superuser, objects keep
   their owner `app` because the dump contains the `OWNER TO app` statements. Check:
   `kubectl -n immich exec immich-database-1 -c postgres -- psql -U postgres -d app -c "select count(*) from asset"`.
   Any error rolls everything back, so a failed attempt is safe to repeat. Delete `dump.sql.gz` afterwards: it holds
   user e-mail addresses and password hashes.
4. **Scale Immich up** (`--replicas=1`, `flux resume helmrelease immich -n immich`) and check your photos in the app.
5. **Regenerate what is not in the database.** Only two things are missing after the restore: **thumbnails/previews** and
   **encoded video**. They are not in the backup, and the restored database still records their paths, so the job's
   default "missing only" mode finds nothing to do (thumbnails and playback return 404). Run those two jobs for **all**
   assets: Administration → Jobs → Thumbnail generation → **All**, and Video conversion → **All** (API:
   `PUT /api/jobs/thumbnailGeneration` and `PUT /api/jobs/videoConversion` with `{"command":"start","force":true}`).
   **Smart search and detected faces need no rerun**: their data lives in the database and came back with it (tested:
   searches worked immediately after the restore). With the default setting a web-compatible video has no encoded copy
   at all and plays from the original, so only videos Immich had transcoded are affected. Resume the jobs and Flux
   (section 5, top).

### 5.3 The Pocket ID database or data is lost

1. Pause the jobs (above). Scale Pocket ID down: `kubectl -n identity scale deploy/pocket-id --replicas=0`.
2. `kubectl apply -f docs/restore/restic-shell-pocket-id.yaml` and wait for it to be Ready.
3. Restic container: restore the newest snapshot into the scratch folder, then put the data back:
   ```sh
   kubectl -n identity exec -it restic-shell -c restic -- sh
   r restore latest --tag pocket-id --target /restore --exclude-xattr security.selinux
   cp -a /restore/data/uploads/. /data/uploads/
   exit
   ```
4. Postgres container: replace the database contents with the dump (this clears what a freshly started Pocket ID created):
   ```sh
   kubectl -n identity exec -it restic-shell -c postgres -- sh
   pg_restore --clean --if-exists --no-owner --no-privileges -d "$DB_URI" /restore/dump/pocket-id.dump
   ```
5. Delete the shell, scale Pocket ID up (`--replicas=1`), sign in with your passkey, resume the jobs.

The passkeys live in the database, so they come back with it. They work as long as the hostname is unchanged.

### 5.4 The S3 bucket is lost or corrupted

1. Create a new bucket (or a new prefix) at EuroNodes and update `apps/euronodes/immich/rclone-secret.sops.yaml`
   (`sops` to edit; keep the same crypt passwords unless you are rotating them). Commit and let Flux apply it, or
   apply it by hand. Wait until the rclone pod is running and Immich restarts.
2. Pause the jobs and Immich (above, plus `kubectl -n immich scale deploy/immich-server --replicas=0`).
3. Apply the Immich shell and restore **straight into the S3 mount** (the snapshot stores the absolute paths):
   ```sh
   kubectl apply -f docs/restore/restic-shell-immich.yaml
   kubectl -n immich exec -it restic-shell -- sh
   r restore latest --tag immich --target / --exclude-xattr security.selinux \
     --include /mnt/rclone/library --include /mnt/rclone/upload
   ```
   Restore time is the size of the library over the link, so it can take hours; the shell pod keeps running. The
   `.immich` marker files come back with the folders.
4. Scale Immich up (`flux resume`, `--replicas=1`), check a few photos open, resume the jobs.

### 5.5 The VM is gone (new server)

Full order. Stop at any step that fails and fix it before moving on.

1. Order the new VM and install Talos using the same secrets bundle, then bootstrap Flux with the same age key
   (see [`euronodes.md`](euronodes.md)). The cluster identity and all encrypted secrets come back from git. Until the
   Tailscale operator is up on the new VM, ports 50000 and 6443 may need to be opened temporarily in the console firewall.
2. When the apps are deployed, **pause the jobs and Immich** (section 5, top) and scale Pocket ID down.
3. If the S3 bucket and its crypt passwords are intact, nothing to do for the photos: check the rclone pod is running
   and the mount shows `.mounted`. If the bucket is gone too, do 5.4.
4. **Pocket ID:** do 5.3 (data and database).
5. **Immich volume files:** in the Immich shell, restore `profile` and `backups`:
   ```sh
   r restore latest --tag immich --target /restore --exclude-xattr security.selinux --include /immich/profile --include /immich/backups
   cp -a /restore/immich/profile/. /immich/profile/
   cp -a /restore/immich/backups/. /immich/backups/
   ```
6. Let Immich start once on its fresh database (so it creates its own folder markers), then scale it down and restore the
   database as in section 5.2 (the superuser restore, **not** the in-app one), scale it up and run the regenerating admin
   jobs for **all** assets.
7. Resume the jobs and run a manual backup. Check `restic check` passes.

Why this matters: Postgres cannot be restored while the app is writing to it, and the nightly jobs would overwrite good
restore points with empty ones. That is the reason for the pausing.

### 5.6 The Storage Box is lost or its data is damaged

You still have the live copies (S3 and the databases). Order a new box, install the SSH key and enable SSH
support and external reachability, update the host key in both `restic-secret.sops.yaml` files
(`ssh-keyscan -p 23 <host>`; compare the fingerprint with Hetzner's), and run the jobs. They create the empty
repositories and upload everything again; the first Immich run takes hours. Until it finishes you have one fewer copy,
so do this quickly. Turn on Storage Box snapshots in Hetzner's console: they are the only protection against
someone deleting the repository with the cluster's key.

## 6. Pitfalls

| Symptom | Cause and fix |
| ------- | ------------- |
| `Fatal: wrong password or no key found` (exit 12) | Wrong `RESTIC_PASSWORD` in your shell or in the secret |
| `repository does not exist` (exit 10) from your laptop | Wrong `REPO`, or the box is new. The jobs create it; from the laptop `r init` creates it by hand. Double-check the name first. |
| `unable to create lock … already locked` | Another run is active, or a crashed run left a lock. If no job is running: `r unlock` |
| Restore ends with `Fatal: There were N errors` mentioning `security.selinux` | Add `--exclude-xattr security.selinux` (data is already restored) |
| `ssh … UNPROTECTED PRIVATE KEY FILE` in a pod | Do not set `fsGroup` on pods that mount the key; Kubernetes then makes it group-readable |
| Immich refuses to start after folders were replaced | Its folder check wants an `.immich` marker file in each storage folder. `library`, `upload`, `profile` and `backups` get theirs back with the restore; `thumbs` and `encoded-video` are not in the backup, so let Immich create them on a first start before restoring the database (5.5, step 6) or follow the fix on Immich's folder-check page |
| Immich's maintenance screen: `permission denied to create extension "earthdistance"` | The in-app restore cannot work on this cluster (the app user is not a superuser). Nothing was changed; use the superuser restore in 5.2 |
| After a database restore every photo is in the timeline but thumbnails are blank/404 | Thumbnails are not backed up, and the job's "missing only" mode trusts the restored database. Run the thumbnail job for **all** assets (5.2, step 5) |
| `psql` ends with `missing data for column …` or `invalid hexadecimal data`, or `kubectl` prints `websocket: close 1006` | You piped one `kubectl exec` into another and the stream was truncated. Nothing was restored (single transaction). Copy the dump to a local file first, then replay it (5.2, step 3) |
| CronJobs are active again although you paused them | Flux reset them. Pause with `flux suspend kustomization apps` first (section 5, top) |
| Backup job runs for hours after a reboot | Normally avoided by `--ignore-inode`; check the job is the current manifest |

## 7. What has been tested

**Full disaster-recovery test on the live cluster, 2026-10-26** (the library held only test data): four test photos
(three 2.3 MB images and one 44 MB file) were uploaded, Immich's own database dump and the restic backup were taken and
checked (the snapshot held all four originals with their exact sizes and the dump), then the loss was simulated: the
photos were deleted in Immich (database records, originals on S3 and thumbnails gone) and the test dumps removed from
Immich's backup folder. Recovery, using only restic: originals restored straight into the S3 mount (`--target /`), the
dumps restored into Immich's volume, the database replayed as the PostgreSQL superuser, thumbnails regenerated.
Result: Immich showed the same four photos with the **same asset IDs and usage**, every original downloaded
**byte-identical** to its checksum from before the loss, and thumbnails came back after running the job for all assets.
The test found three defects that are fixed in this guide: Immich's in-app restore cannot work here, the thumbnail job
must be run for all assets, and a manual CronJob pause is undone by Flux.

**Second run, with a real video (a 38 s, 8.7 MB MP4 with a face), same day:** Immich was set to transcode everything so
an encoded copy existed, then the video was lost completely (database rows, face, search embedding, original, thumbnails,
encoded copy). Recovery followed this guide with the pause, Immich scaled down, the original restored from restic into
the S3 mount and the database replayed as the superuser. Immediately after the restore: the original was byte-identical,
smart search found the video for every query, the detected face and the search data were in the database; thumbnails,
preview and playback returned 404. After running only the thumbnail and video-conversion jobs for all assets everything
was back, identical in size to before. It also showed that piping two `kubectl exec` streams fails (see the pitfalls).

Earlier checks (2026-10-19 and 2026-10-20): the Immich repository restores the newest dumps (each passes `gzip -t`)
and `profile`; Pocket ID's dump restored into a throwaway Postgres with identical row counts for every table the app
uses, and its data folder restored; both restic shell pods start, list snapshots and restore files; the nightly jobs
run on schedule at 03:30 and 04:00.

**Not tested:** restoring the originals into a *new* S3 bucket; grouping faces into named people (Immich needs at least
three similar faces); rebuilding the whole cluster on a new VM from these steps; the Storage Box replacement.
The Pocket ID restore into the live database (5.3) has been rehearsed only through the throwaway-Postgres drill.

**Suggested habit:** once a month restore the newest Pocket ID dump and an Immich dump to your laptop (section 4A),
check that the files open (`gunzip -t`, `pg_restore --list`), and look at the job history.

## 8. Changing credentials

- **restic password:** `r key passwd` on each repository (it re-encrypts the key, not the data), then update
  `RESTIC_PASSWORD` in **both** `restic-secret.sops.yaml` files (`sops` to edit), commit, and save the new
  password in the password manager. Do this with the jobs paused.
- **SSH key:** generate a new key, add it to the box, put it in both secrets (`ssh-privatekey`), then remove the old
  one from the box. The pinned host keys (`known_hosts`) only change if Hetzner changes the box's keys.
