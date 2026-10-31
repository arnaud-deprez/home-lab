# Backup to an external disk (EuroNodes cluster)

A third copy of the EuroNodes data on a personal external disk, made by hand every few months
(at the very least twice a year). The photos are **plain files** you can open in Finder; everything else is
restored from the restic repositories described in [`backup-restore.md`](backup-restore.md).

| Copy | What | How fresh |
| ---- | ---- | --------- |
| 1. Live | S3 bucket (rclone crypt) + local volumes | now |
| 2. Storage Box | restic repositories | last night |
| 3. This disk | plain photos, Immich dumps, Pocket ID dump | the day you last ran the script |

Because the disk is offline most of the time it protects against what the other two cannot: both providers
failing, the credentials being lost, a bad deploy or ransomware that deletes live data *and* its nightly
backups. It is not a way to get yesterday's data back.

## What lands on the disk

```
<disk>/
  immich/library/, upload/       photo originals, as plain files (decrypted from the bucket)
  immich-archive/<date>/…        photos removed or replaced since the previous run (see below)
  immich-extras/backups/         Immich database dumps (newest 30 kept)
  immich-extras/profile/         profile pictures
  pocket-id/<date>/              Pocket ID database dump + data folder, one folder per run
  .backup-state/                 date of the last run + SHA-256 manifest of every photo
```

The script keeps only the **latest** restic snapshot of each app per run, not the whole history. With one run every
few months that is one restore point per run, so the nightly/weekly/monthly retention of the Storage Box does not
matter here.

## Before the first run

1. **Format the disk** APFS (Mac only) or exFAT (also readable elsewhere). The disk will hold the Pocket ID
   dump and Immich dumps unencrypted next to the photos; if the disk could be stolen, choose **APFS (Encrypted)** in
   Disk Utility. Finder unlocks it with a password and the photos stay ordinary files once it is mounted.
2. `brew install rclone restic sops jq`
3. Keep these on the Mac (they are also in the password manager, see [`backup-restore.md`](backup-restore.md)):
   `~/.config/sops/age/euronodes.agekey` and `~/.ssh/euronodes-backup`. Nothing is copied to the disk:
   the script reads the rclone crypt and restic credentials from the repo's SOPS files, in memory.
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
5. **Immich dumps and profile pictures**: `restic restore` of the newest `immich` snapshot, only `/immich/backups`
   and `/immich/profile` (the originals are already there as plain files, the restore would be a pointless copy).
6. **Pocket ID**: `restic restore` of the newest `pocket-id` snapshot into `pocket-id/<date>/`.
7. Prunes dated folders beyond the last `KEEP_RUNS` (default 8) runs, writes the new manifest and the
   `last-run` date.

Settings (environment variables): `KEEP_RUNS`, `KEEP_DUMPS`, `MAX_DELETE`, `SKIP_DISK_CHECK=1` (skip step 2),
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

**One photo or an album's worth:** open `immich/library/…` in Finder. Immich's storage template names the folders;
search by date or filename.

**Photos deleted by mistake, found later:** look in `immich-archive/<date>/library/…` of the run after the deletion.

**Everything (the S3 bucket is gone):** make a new bucket, add it as an rclone remote with crypt, and copy back:

```sh
rclone copy /Volumes/Backup/immich/library crypt:library --transfers 8 --progress
rclone copy /Volumes/Backup/immich/upload  crypt:upload  --transfers 8 --progress
```

Then restore the database from `immich-extras/backups/` with the procedure in
[`backup-restore.md`](backup-restore.md) section 5.2, copying the dump out of the disk in place of the one from
restic. The `.immich` marker files must be present in `library/` and `upload/` (the sync copies them); see the
pitfalls table there.

**Pocket ID:** the dump and data folder of the newest `pocket-id/<date>/` are in the same layout as a restic restore;
follow section 5.3.

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
| `crypt:library is empty or unreachable` | Network, wrong S3 keys, or the bucket really is empty. Nothing was changed. Check with `rclone lsd crypt:` after exporting the config as the script does |
| `--max-delete` stops the sync | More files vanished from the source than `MAX_DELETE` allows. Look at the live library before raising it |
| `Host key verification failed` | The Storage Box key changed; the pinned keys come from `restic-secret.sops.yaml` (see [`backup-restore.md`](backup-restore.md) section 8) |
| `Fatal: wrong password or no key found` | Wrong `RESTIC_PASSWORD` in the SOPS file after a rotation |
| Restore ends with `security.selinux` errors | The script already passes `--exclude-xattr security.selinux` |
| exFAT: odd characters in names | Immich names are plain ASCII; if you see errors, use APFS |
