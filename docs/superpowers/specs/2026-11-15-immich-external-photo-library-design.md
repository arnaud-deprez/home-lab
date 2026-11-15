# Immich external photo library — design

Status: draft for review. Date: 2026-11-15.

## Intent

Get the family's curated photo archive (about 45,000 photos and a few videos, organised in
folders on a Mac and on an external hard drive) into Immich **without losing the folder
structure**, with **correct dates**, **albums that mirror the folders**, and **the same setup
on every cluster** (laptop, tierhive, euronodes). Euronodes goes first.

Why not just upload: Immich's storage template (`{{y}}/{{y-MM-dd}}/{{filename}}`) moves uploaded
files into a date tree and forgets the original path. The archive's folder tree is itself the
backup format and must survive. So the archive is mounted as a read-only **external library**:
Immich indexes it in place, never moves or edits the files.

## Source data (inspected 2026-11-15, read-only)

Root: `/Users/arnaud/Pictures/Photos` on the user's Mac. The hard drive holds untouched originals.

| Folder | In scope | Album rule |
|---|---|---|
| `Shared` | all (about 17 albums, `Crête 2011` was just moved here, yearless folders removed) | each direct subfolder is an album |
| `Arnaud` | all except `papa` | each direct subfolder is an album |
| `Amelie` | periods `2003-2004(3e)` through `2015-2016(Recherche d'emploi - Entrée P&T )` only (13 periods) | each **event folder** under a period is an album; the period is not in the album name |
| `Fla`, `manou`, later `Amelie` periods | out of scope for now | — |

- Mostly `.jpg`; also `.heic`, `.png`, `.mov`, `.mp4`, a few `.avi/.mpg/.mts/.bmp`.
- Junk to exclude: `.DS_Store`, `*.db`, `*.aae`, `*.thm`, `*.xml`, `*.mcf`, `*.wav` (confirm `wav`).
- Size: Shared 14 GB, Arnaud 4.7 GB, Amelie (all periods) 138 GB, so the in-scope Amelie is smaller.
- Folder names carry the year (`2007 - camp Cheoux`, `2005 - 1) londres en famille`).
- Dates (measured on all three trees before the scope was narrowed): about 74% of EXIF years match the folder year,
  about 5% are one year off (plausible, New Year), 486 files have an empty `0000:00:00` date, about
  1,350 are more than one year off (wrong camera clock, e.g. a 2009 camp stamped 2007). The user also
  reports that file modification dates are right where creation dates are wrong.

## Goals

1. Browse the archive in Immich's Folders view exactly as on disk.
2. One Immich album per folder per the rules above, shared with the other account.
3. Correct capture dates, fixed once in the files themselves.
4. Identical Immich-side configuration on every cluster; only storage backing and backup differ.
5. The new data is backed up on euronodes (restic) and covered by the disk-backup procedure.
6. Phased: phase 1 = `Shared` + `Arnaud` (about 19 GB), phase 2 = in-scope `Amelie`.

## Non-goals / anti-goals

- No nested albums (Immich has none); no re-organising the user's folders.
- Not a migration of the existing Immich `library/`/`upload/` content.
- Anti-goal: anything that silently leaves the new data unbacked-up, or that destabilises the
  euronodes rclone mount and Immich (it has been tuned after real outages).
- Claude never decrypts SOPS secrets, never handles API keys or rclone credentials; the user runs
  every command needing them.

## Decisions (made by the user unless marked *call*)

### Accounts and sharing
- Two Immich users: Arnaud and Amelie, each other's **partner** (timelines already shared).
- Libraries: `Arnaud` → owned by Arnaud, `Amelie` → owned by Amelie, `Shared` → owned by Arnaud
  (*call*; partner sharing makes it visible to Amelie anyway).
- Partner sharing does not cover albums, so the album script shares every album explicitly with
  the other account (editor or viewer role is left to the builder; default viewer, *call*).

### Dates (fixed on the Mac, once, before upload)
- Edit files in place with `exiftool`; the hard drive is the pristine backup (user decision).
- Always `-P` (preserve file modification time): modification time is the user's trusted fallback date,
  and exiftool otherwise resets it to now.
- Before any write: dump the old dates to a CSV (undo log), and run a dry-run report listing, per
  rule, how many files would change.
- Proposed rule (*call*, to be validated on phase 1 with the dry-run report): keep the EXIF
  `DateTimeOriginal` if its year is within one year of the folder's year; otherwise use the file
  modification date if its year is within one year of the folder's year; otherwise set the folder
  year (1 January, 12:00). Files with no date at all follow the same fallback. Video/other formats
  that exiftool cannot write (`avi`, `mpg`, `mts`) are reported, not forced (builder decides: sidecar
  `.xmp` or leave).
- Applies to the scoped folders only.

### Hosting, common to all clusters
- The base Immich manifests (`apps/base/immich`) declare a read-only volume mounted at `/external`
  in the immich-server pod. Immich import paths are `/external/Shared`, `/external/Arnaud`,
  `/external/Amelie` on every cluster, so the album script and its config are identical everywhere.
- Each cluster overlay chooses the backing storage:
  - **euronodes**: an `external/` prefix in the existing rclone-crypt S3 mount, next to `library/` and
    `upload/`, reached through the same single `/mnt/rclone` mount (do not add a second mount point;
    see the EXDEV comment in `apps/euronodes/immich/helmrelease-patch.yaml`). S3 has room (94 GB used of
    1 TB). The restic CronJob (`restic-backup.yaml`) gets `external/` added to its paths, and
    `docs/backup-restore.md`, `docs/disk-backup.md` and `scripts/euronodes-disk-backup.sh` are updated.
  - **laptop, tierhive**: a `local-path` PVC. Backup per cluster is out of scope here beyond noting it.
- Keep the euronodes rclone mount settings as they are, including the HTTP/2 cap and connection limit
  (they fix real resets). Revisit them only as a separate, revertible experiment after the import.
  Expect the first scan to be heavy on that mount; scan phase 1 first and watch the pod's probes and
  the mount's memory (1 Gi limit).

### Album automation
- A script driven by a small config (libraries, in-scope Amelie periods, album-name rule, share-with
  account, ignore patterns). Candidate tool: `immich-folder-album-creator` (Salvoxia), or a small
  script against the Immich API; builder chooses. It runs from the user's Mac against the Immich URL
  with an API key the user provides. A CronJob per cluster is a possible later step, not now.
- Album name = the folder name as is (e.g. `2007 - camp Cheoux`, `2005 - 1) londres en famille`).
  Two Amelie events with the same name in different periods would collide; the builder must detect
  this and report it (resolution left to the user).

### Order of work
1. Dry-run date report on phase 1; user reviews; write dates; keep undo CSV.
2. Copy phase 1 to the cluster's `external` storage (e.g. `rclone copy` from the Mac to the crypt remote,
   run by the user).
3. GitOps change: base `/external` volume, euronodes overlay, restic path, docs. Flux reconcile.
4. Create the two/three external libraries in Immich, run the scan, check the Folders view and dates.
5. Create and share albums with the script.
6. Verify the next restic run includes `external/`; test a restore of a few files.
7. Repeat 1–6 for in-scope Amelie. Extend to laptop/tierhive later by adding their overlays.

## Calls made after the builder check (author's calls, to be confirmed on review)

- **Read-only on euronodes**: `/external` is a symlink to `/mnt/rclone/external`, created by the
  existing `wait-mount` init container, like `library/` and `upload/`. The volume stays writable;
  read-only is by convention (Immich never writes to external libraries). Other clusters mount their
  PVC `readOnly: true`.
- **Year source for dates**: the year comes from the album folder name (`YYYY - …`). All in-scope
  `Shared` and `Arnaud` folders and Amelie event folders have one (`papa` is out of scope). A file
  whose folder has no parsable year is skipped and reported, never given a guessed date.
- **Files that fall back to the folder year**: not all stamped identically. Use the folder year with
  the month/day/time of the file modification date when only the year is implausible; if the
  modification date is also implausible, use 1 January 12:00:00 plus one second per file in filename
  order, so the album keeps its order. The dry-run report lists these separately.
- **Scans**: manual only (no scheduled library scan) until the rclone mount has proven stable, so a
  mount outage during a scan cannot mark assets offline.
- **Sequencing**: the first restic run of `external/` is not started during the first library scan.
- **Album script**: committed under `scripts/` with its config; one run per library owner using that
  owner's API key (`Arnaud` and `Shared` with Arnaud's key, `Amelie` with Amelie's key), each run
  sharing the albums with the other account.

## Answers from the user after the builder check

- **Mac to S3**: the user has rclone and the crypt keys but no remote configured on the Mac. The plan
  must include a guided one-time setup of an rclone remote on the Mac that reaches the same S3 bucket
  (`home`, under `immich/`) through the same crypt layer as the cluster (same passwords and filename
  encryption), plus a test (`rclone lsd`, `rclone copy` of a small folder, then check it is visible
  from the Immich pod). The user decrypts the SOPS secret and enters the values themselves; Claude only
  names the keys to look for (see `apps/euronodes/immich/rclone-secret.sops.yaml` key names) and
  never sees the values. The remote must write to the **same** prefix the cluster mounts, or the
  files will not be visible to Immich.
- **Duplicates**: some overlap with photos already uploaded to Immich is likely. The plan must
  include a duplicate check before and after phase 1 (Immich's duplicate detection, plus a
  comparison by checksum or filename and date), and a decision rule that is the author's guess, not the user's:
  the external archive wins and the uploaded copy is removed, unless the user decides otherwise per case.
  Nothing is deleted without the user's review of the list.
- **Storage Box**: has enough capacity for both phases.

## Video formats (decided with the user)

`avi`, `mpg`, `mts` files are not re-encoded: Immich transcodes them for playback, re-encoding is lossy
and would make the archive differ from the hard drive. They are listed in the dry-run report and keep
their file modification date (`-P`). After phase 1, if playback is poor, `mts` may be remuxed losslessly
to mp4 (`ffmpeg -c copy`, date set in the same step); `avi`/`mpg` are re-encoded only per file as a last
resort, the original staying on the drive.

Status: approved by the user on 2026-11-15.

## Left to the builder

Exact manifest shape (volume name, PVC sizes, read-only enforcement), the exiftool command lines,
script language/tool, library scan schedule, role of the shared albums, handling of non-writable
video formats and duplicates (same photo in two folders, or already in the Immich library).

## Open risks

- Initial scan load on the FUSE mount (see above).
- Wrong dates the rule cannot detect (EXIF within one year but still wrong).
- Duplicates between this archive and photos already uploaded to Immich.
