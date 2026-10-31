# Backup to an external disk (EuroNodes cluster)

A third copy of the EuroNodes data on a personal external disk, made by hand every few months
(at the very least twice a year). The photos are **plain files** you can open in Finder. The databases and the
other small files go into **small encrypted restic repositories** on the same disk, so the restore commands are the
ones from [`backup-restore.md`](backup-restore.md) with another repository path.

| Copy | What | How fresh |
| ---- | ---- | --------- |
| 1. Live | S3 bucket (rclone crypt) + local volumes | now |
| 2. Storage Box | restic repositories | last night |
| 3. This disk | plain photos + restic repos with the databases | the day you last ran the script |

Because the disk is offline most of the time it protects against what the other two cannot: both providers
failing, the credentials being lost, a bad deploy or ransomware that deletes live data *and* its nightly
backups. It is not a way to get yesterday's data back.

## What lands on the disk

```
<disk>/
  immich/library/, upload/       photo originals, as plain files (decrypted from the bucket)
  immich-archive/<date>/…        photos removed or replaced since the previous run (see below)
  restic-immich/                 restic repo: Immich database dumps + profile pictures (last 8 runs)
  restic-pocket-id/              restic repo: Pocket ID database dump + data folder (last 8 runs)
  .backup-state/                 date of the last run + SHA-256 manifest of every photo
```

Where each part comes from, and why:

- **Photos** are read straight from the S3 bucket with rclone. They never pass through the Storage Box.
- **The databases and small files** come from the newest snapshot on the Storage Box, because that works when the
  cluster is dead (no `kubectl`, no Tailscale) and the dump files are the same ones either way. Only
  `/immich/backups` and `/immich/profile` are restored from the Immich snapshot: the originals in that snapshot
  (about 42 GiB) are never downloaded, because `restic restore --include` fetches only the data of the paths asked for.
  (`restic copy` cannot do that: it copies whole snapshots, so the photos would land on the disk a second time.)
- The two repos use the **same restic password** as the Storage Box repos.

## Before the first run

1. **Format the disk** APFS (Mac only) or exFAT (also readable elsewhere). The restic repos are encrypted but the
   photos are not, and the disk stays in a drawer; if it could be stolen, choose **APFS (Encrypted)** in
   Disk Utility. Finder unlocks it with a password and the photos stay ordinary files once it is mounted.
2. `brew install rclone restic sops jq`, and `brew install libpq` if you want the Pocket ID dump checked
   (`pg_restore --list`).
3. Keep these on the Mac (they are also in the password manager, see [`backup-restore.md`](backup-restore.md)):
   `~/.config/sops/age/euronodes.agekey` and `~/.ssh/euronodes-backup`. Nothing is copied to the disk: the script reads
   the rclone and restic credentials from the repo's SOPS files, in memory.
4. Make sure the Mac can reach the Storage Box (SFTP, port 23) and `eu-west-1.euronodes.com`.

## Running it

```sh
scripts/euronodes-disk-backup.sh /Volumes/Backup                 # the backup
DRY_RUN=1 scripts/euronodes-disk-backup.sh /Volumes/Backup       # list what would change, write nothing
scripts/euronodes-disk-backup.sh /Volumes/Backup --verify        # + re-download every photo and compare (slow)
scripts/euronodes-disk-backup.sh /Volumes/Backup --status        # when did it last succeed?
```

The first run downloads the whole library (about 42 GiB at the time of writing) and takes hours; later runs only
transfer new or changed files. Leave the Mac awake (`caffeinate -s scripts/…`).

What it does, in order:

1. **Guard:** stops if `library` or `upload` looks empty or unreachable in the bucket, so a broken source can never
   wipe the disk.
2. **Bit-rot check:** compares every photo on the disk with the SHA-256 manifest written at the end of the previous
   run. A disk that sat in a drawer for months is exactly where silent corruption shows up.
3. **`rclone sync`** of `library` and `upload` from the crypt remote. Files that disappeared from the source, or were
   replaced, are moved to `immich-archive/<date>/` instead of being deleted. A run stops if it would remove more than
   `MAX_DELETE` files (default 500): raise it on purpose, after checking why.
4. With `--verify`, `rclone check --download` reads every photo from S3 again and compares it byte for byte. The crypt
   remote has no checksums of its own, so this is the only full comparison. Do it once a year or after any incident.
5. **For each app (Immich, then Pocket ID):** restores the newest Storage Box snapshot into a temporary folder
   (`/tmp/euronodes-disk-backup`, removed at the end) and **checks it before it touches the disk**:
   every Immich dump passes `gzip -t`, the newest Immich dump and the Pocket ID dump are not older than
   `MAX_DUMP_AGE_DAYS` (default 3), and the Pocket ID dump passes `pg_restore --list`. If a check fails the run stops
   and nothing is added to the disk, so a silently broken nightly job on the cluster is noticed instead of copied.
6. Adds the checked files as a new snapshot to `restic-immich` / `restic-pocket-id`, keeps the last `KEEP_RUNS`
   (default 8) snapshots and runs `restic check --read-data` on the repo (they are small).
7. Prunes archive folders beyond the last `KEEP_RUNS` runs, writes the new photo manifest and the `last-run` date.
   A run that stopped earlier never updates `last-run`.

Settings (environment variables): `KEEP_RUNS`, `MAX_DUMP_AGE_DAYS`, `MAX_DELETE`, `SKIP_DISK_CHECK=1` (skip step 2),
`SOPS_AGE_KEY_FILE`, `SSH_KEY`, and `HC_PING_URL` (see below).

## Schedule

Put a recurring event in your calendar: **every 3 months** is a good target, **every 6 months** the limit.
`--status` tells you how old the disk is. Run the script, check it ended with `Done`, unmount, put the disk away
(ideally somewhere other than where the server and laptop are).

Optionally create a [healthchecks.io](https://healthchecks.io) check with period 120 days and grace 60 days (same
Telegram channel as the other checks, see [`monitoring.md`](monitoring.md)) and run with
`HC_PING_URL=https://hc-ping.com/<uuid>`. It reminds you when you forgot.

## What the disk does *not* cover

The photos are as fresh as the last run, but the **database** is only as fresh as the newest Immich dump on the disk.
After restoring a disk that is 4 months old you get all photos present at that time and the albums, faces, shared
links and metadata edits from that day. Newer photos exist only on the other two copies. That is the price of
running by hand every few months; run more often before something risky (migration, provider change).

## Restoring from the disk

### Read this first

| You need | Without it |
| -------- | ---------- |
| The disk (mounted) | nothing to restore |
| The **restic password** (password manager) | the two restic repos on the disk are unreadable; the photos are still plain files |
| This repository (GitHub) and the **age key** `euronodes.agekey` | you cannot read the rclone crypt passwords. Without them you cannot write photos into a crypt bucket, but the plain photos on the disk are still yours to copy anywhere |
| A working internet link | restoring is bounded by the **upload** speed to the bucket (table below) |

Do I need the disk at all? Use the **Storage Box** first ([`backup-restore.md`](backup-restore.md) section 5): it is
newer and the procedures are tested. The disk is for when the Storage Box (or the bucket **and** the box) are gone,
or their contents were damaged or deleted.

| Situation | Use |
| --------- | --- |
| Look at or copy a photo | 1 |
| A photo was removed by mistake, long ago | 2 |
| Database lost, Storage Box fine | `backup-restore.md` 5.2 (disk not needed) |
| Bucket lost, Storage Box fine | `backup-restore.md` 5.4 (disk not needed) |
| **Bucket lost and Storage Box lost or damaged** | 3 + 4 |
| **Everything lost (new VM)** | 3, 4, 5 following `backup-restore.md` 5.5 for the cluster itself |
| Pocket ID lost, Storage Box gone | 5 |

How long the copy back takes (it is `42 GiB × 8 ÷ upload speed`, plus overhead; estimates, not measured):

| Upload speed | 42 GiB |
| ------------ | ------ |
| 100 Mbit/s | about 1 h |
| 50 Mbit/s | about 2 h |
| 20 Mbit/s | about 5 h |
| 10 Mbit/s | about 10 h |

If your home upload is slow, run the copy from a machine with a better link (the disk can be plugged in anywhere
with rclone and the repo checkout).

### Helpers (paste once per shell)

```sh
# restic repos on the disk: d immich snapshots / d pocket-id ls latest
export RESTIC_PASSWORD='...'                        # from the password manager
d() { restic -r "/Volumes/Backup/restic-$1" "${@:2}"; }

# rclone crypt remote, taken from the repo's SOPS file (edit the file first if you are restoring into a new bucket)
export SOPS_AGE_KEY_FILE=~/.config/sops/age/euronodes.agekey
while IFS= read -r kv; do export "$kv"; done < <(
  sops -d --extract '["stringData"]' --output-type json apps/euronodes/immich/rclone-secret.sops.yaml \
  | jq -r 'to_entries[] | "\(.key)=\(.value)"')
```

### 1. Open or copy a photo

Nothing to restore: open `/Volumes/Backup/immich/library/…` in Finder. Immich's storage template names the folders;
search by date or filename.

### 2. A photo was removed or replaced

Look in `immich-archive/<date>/library/…` (or `upload/`) for the run **after** the removal. Copy the file back through
Immich (upload it again) rather than into the bucket by hand: Immich also needs its database record.

### 3. The photos go back into the bucket

1. If the bucket is new, update `apps/euronodes/immich/rclone-secret.sops.yaml` first (see [`euronodes.md`](euronodes.md),
   and keep the **same crypt passwords**, or nothing already encrypted will ever be readable), commit, let Flux apply it,
   and re-run the second helper above so your shell uses the new values.
2. Pause Immich and the jobs (`backup-restore.md`, section 5 top).
3. Copy, with progress. rclone skips what is already there, so it can be interrupted and restarted:
   ```sh
   rclone copy /Volumes/Backup/immich/library crypt:library --transfers 8 --progress
   rclone copy /Volumes/Backup/immich/upload  crypt:upload  --transfers 8 --progress
   echo ok | rclone rcat crypt:.mounted            # marker Immich and the backup job wait for
   ```
   The `.immich` marker files inside `library/` and `upload/` are on the disk and come back with the folders. The
   `.mounted` marker at the root of the bucket is **not** (the script only copies `library` and `upload`, and the
   restic snapshots do not hold it either). It is a tiny file whose content does not matter, created once with the
   bucket ([`euronodes.md`](euronodes.md)); without it Immich waits forever and the nightly job fails.
4. Check the count and size match: `rclone size crypt:library` against `rclone size /Volumes/Backup/immich/library`.
5. Scale Immich back up and carry on with 4.

### 4. The Immich database from the disk

```sh
d immich snapshots                                   # one line per run
d immich restore latest --target ./restore           # or a snapshot id
ls restore/tmp/euronodes-disk-backup/immich/immich/backups/    # nightly dumps, newest last in the name
```

Take the newest `immich-db-backup-*.sql.gz` that predates the problem. **Then follow `backup-restore.md` section 5.2,
step 3**, starting at `gzip -t`: the dump is already on your laptop, so skip the `kubectl exec … cat` line and use your file as
`dump.sql.gz`. Remember the superuser restore, never the in-app one. Delete `./restore` afterwards (it holds e-mail
addresses and password hashes).

Profile pictures are in `restore/tmp/euronodes-disk-backup/immich/immich/profile/`. They are optional: copy them into
the Immich shell pod with `kubectl cp` or let users upload them again. After the restore, run the regenerating admin
jobs for **all** assets (5.2, step 5): the disk holds no thumbnails or encoded video.

Expect the database to be as old as the dump: photos uploaded after it are on the disk, but Immich has no record of them
(they show up as untracked files in Administration → Repair). Upload them again from `immich/library/`; Immich
skips what it already has by checksum. Only the albums and metadata edits of that period are lost.

### 5. Pocket ID from the disk

```sh
d pocket-id snapshots
d pocket-id restore latest --target ./restore
ls restore/tmp/euronodes-disk-backup/pocket-id/        # dump/pocket-id.dump  and  data/
```

Then follow `backup-restore.md` section 5.3, with one change: the restic shell pod cannot see the disk, so instead of
`r restore …` (step 3) copy the files into the pod:

```sh
kubectl apply -f docs/restore/restic-shell-pocket-id.yaml
kubectl -n identity cp restore/tmp/euronodes-disk-backup/pocket-id/. restic-shell:/restore -c postgres
kubectl -n identity exec restic-shell -c postgres -- ls -l /restore/dump
shasum -a 256 restore/tmp/euronodes-disk-backup/pocket-id/dump/pocket-id.dump
kubectl -n identity exec restic-shell -c postgres -- sha256sum /restore/dump/pocket-id.dump   # must match
```

then continue with step 3's `cp -a /restore/data/uploads/. /data/uploads/` (in the `restic` container) and step 4's
`pg_restore`. The two hashes must match: `kubectl cp` goes through the same Tailscale proxy that truncated piped streams
in 5.2, and a mismatch means copy again.

### If the disk is all you have left

1. Do not run the nightly jobs or `flux resume` until the photos and the databases are back (section 5 top).
2. Rebuild the cluster with `backup-restore.md` 5.5 steps 1 and 2, using the repository and age key.
3. Photos (3), Pocket ID (5), the Immich profile pictures and database (4), in that order: the database restore wants
   a first Immich start on empty folders, as 5.5 step 6 explains.
4. Run a manual backup job, check `restic check` passes, and make a fresh disk copy a week later.

## The disk check fails

`files on the disk differ from the last run` means a file was changed, damaged or removed after the previous run.
The output names the files.

- **Damaged or missing:** note the names, run `rclone check crypt:library /Volumes/Backup/immich/library
  --one-way` to see which side is right, and let the sync overwrite the bad file (delete it on the disk first if
  size and date match, so rclone copies it again). Several bad files in one run means the disk is failing: copy
  everything off and replace it.
- **You edited or moved files in Finder on purpose:** the disk is a mirror, don't. Rerun with `SKIP_DISK_CHECK=1`
  once; the next manifest then matches.

## Pitfalls

| Symptom | Cause and fix |
| ------- | ------------- |
| `crypt:library is empty or unreachable` | Network, wrong S3 keys, or the bucket really is empty. Nothing was changed. Check with `rclone lsd crypt:` after loading the config as in the helpers |
| `--max-delete` stops the sync | More files vanished from the source than `MAX_DELETE` allows. Look at the live library before raising it |
| `… is N days old … the app or its backup job has been broken` | The newest dump on the Storage Box is older than `MAX_DUMP_AGE_DAYS`. Check the jobs (`backup-restore.md` section 3) and the healthchecks.io history; nothing was added to the disk |
| `no Immich database dump in the latest snapshot` | Same cause, or the snapshot predates Immich's dump job. Look at `restic ls latest` on the Storage Box repo |
| `Host key verification failed` | The Storage Box key changed; the pinned keys come from `restic-secret.sops.yaml` (see `backup-restore.md` section 8) |
| `Fatal: wrong password or no key found` | Wrong `RESTIC_PASSWORD` in the SOPS file after a rotation. A disk repo created before a rotation keeps the old one until you run `restic key passwd` on it |
| `unable to create lock … already locked` on the disk repo | A previous run was killed. If nothing is running: `d immich unlock` |
| Restore from the Storage Box ends with `security.selinux` errors | The script already passes `--exclude-xattr security.selinux` |
| exFAT: odd characters in names | Immich names are plain ASCII; if you see errors, use APFS |

## What has been tested

On 2026-10-31, `DRY_RUN=1` of the photo part against the live bucket (listing, guard, `--dry-run` sync) and
`--status`. The script was changed afterwards (restic repos on the disk, dump checks).

**Not tested:** a real run, the Storage Box restores and the restic repos on the disk, the dump checks, `--verify`, the
copy back into a bucket, and every procedure in "Restoring from the disk". Run the script once for real and rehearse
sections 1, 4 (up to the `ls`) and 5 (up to the hash comparison) before you depend on them, then record the results here.
