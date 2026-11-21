# Curated photo archive (Immich external libraries)

The family archive (folders per year and event) is indexed by Immich **in place** as read-only
*external libraries*. Immich never moves, renames or edits these files, so the folder tree stays the
backup format, unlike uploads, which the storage template (`{{y}}/{{y-MM-dd}}/{{filename}}`) re-files.
Design: [`superpowers/specs/2026-11-15-immich-external-photo-library-design.md`](../superpowers/specs/2026-11-15-immich-external-photo-library-design.md).

## Where it lives

| | |
| --- | --- |
| Import paths | `<root>/<Library>` in the immich-server pod (`Shared`, `Arnaud`, `Amelie`). `<root>` is `/mnt/rclone/external` on euronodes |
| euronodes | `immich/external/` in the rclone-crypt S3 mount, which the pod already sees at `/mnt/rclone`; created by the `wait-mount` init container |
| Other clusters | mount a read-only volume anywhere **outside `/data`** (Immich answers `Cannot use media upload folder for external libraries` for paths under its media location) and use that as `<root>`. Not done yet |
| Backup (euronodes) | restic job (`/mnt/rclone/external`), and the manual disk copy ([`disk-backup.md`](../disk-backup.md)) |
| Source of truth | the folders on your Mac and the original hard drive |

## Scope and albums

Defined in [`scripts/photo-library/config.json`](../../scripts/photo-library/config.json).

- `Shared`, `Arnaud`: every direct sub-folder is an album (`Arnaud/Papa` has no year: its 2 photos keep their dates and are
  listed as `skip-no-year`).
- `Amelie`: the 22 school periods `2003-2004(3e)` to `2024-2025 (RH5)` (not `A trier`): every *event* folder under a period is an
  album (the period is not in the name). Every event folder needs a `YYYY - ` prefix (the `YYYY-YYYY - ZzZ` leftovers folders were
  renamed to the ending year). Add later periods to `include`.
- Album name = the folder name. `python3 scripts/photo-library/list_albums.py <phase>` lists them and any name that
  would collide. macOS stores some names decomposed (NFD: `é` as `e` + accent); all names and filters are compared
  normalised to NFC. Make sure the copy to S3 normalises too (`rclone --local-unicode-normalization`).
- Ignored files: `.DS_Store`, `*.db`, `*.aae`, `*.thm`, `*.xml`, `*.mcf`, `*.xml~`, `*.wav`.

## Fixing dates (on the Mac, once, before copying)

Immich trusts the EXIF capture date; many old files have a wrong or empty one. Dates are written into the files
with exiftool (always `-P`: the modification time is kept, it is the fallback date).

Requirements: `brew install uv exiftool` (uv pins Python 3.13 and the empty lockfile, `scripts/photo-library/uv.lock`; the scripts
use the standard library only, so `uv run` needs no install step). exiftool is not pinned: the date writing and the repair of
damaged EXIF blocks were developed and tested against **exiftool 13.55** (`exiftool -ver`), newer releases are expected to
work, so upgrade it normally and re-run the tests afterwards. Tests: `uv run python -m unittest discover -s tests`.

```sh
cd scripts/photo-library
uv run fix_dates.py plan 1 --out ~/photo-plan-1     # dry run: report + ~/photo-plan-1/plan.csv, changes nothing
uv run fix_dates.py apply ~/photo-plan-1/plan.csv   # writes ~/photo-plan-1/undo.csv FIRST, then the dates
uv run fix_dates.py undo ~/photo-plan-1/undo.csv    # puts the old tags and modification times back
uv run list_albums.py 1                              # albums of a phase and any name collision
```

Never run `apply` twice on the same folder: the second run would overwrite `undo.csv` with the values written by the
first. To retry failures, build a plan with only those rows in a new folder (its own `undo.csv`).

A JPEG whose EXIF block is damaged (old Nikon files: "Truncated InteropIFD directory") gets its metadata block rebuilt
once (`-all= -tagsfromfile @ -all:all -unsafe`; the image data is not touched), then the date is written again.

Rule per file, first match wins (the folder year is the `YYYY` at the start of the album folder name):

| Rule | When | New date |
| --- | --- | --- |
| `keep-exif` | EXIF year within 1 of the folder year | unchanged |
| `from-mtime` | modification year within 1 of the folder year | the modification date |
| `mtime-day-folder-year` | modification date is a real date but its year is off | folder year with the modification's month, day, time |
| `override-seq` | as `jan1-seq`, for an album listed in `date_overrides` of `config.json` (e.g. `"Arnaud/2006 - TU": "2006-10"`) | the 1st of that month (or the given day), 12:00:00 + 1 s per file |
| `jan1-seq` | no usable modification date, or one shared by 20+ files of the album with a wrong year (placeholder) | 1 January of the folder year, 12:00:00 + 1 s per file in filename order |
| `skip-no-year` | folder name has no `YYYY ` prefix | not touched, listed in the report |

`avi`, `mpg`, `mts` (and `bmp`, `wmv`) cannot be written by exiftool: they are listed as `not-writable`. Their date is the file
modification date, which Immich uses; `apply` sets it only when it is wrong (the file's content is untouched, the old
value goes in the undo log). They are not re-encoded (Immich transcodes them for playback). Values that were
a zero date (`0000:00:00`) cannot be written back by `undo`; they are reported. The hard drive holds the originals.

## Copying to the bucket (euronodes)

`rclone copy` from the Mac to the crypt remote (`crypt:`), which comes from the `RCLONE_CONFIG_*` variables of
`apps/euronodes/immich/rclone-secret.sops.yaml` (decrypted by you, in your terminal; there is no `rclone.conf`). Check with
`rclone lsd crypt:`. The proxy in front of S3 refuses single uploads over about 100 MB (`StatusCode: 413`, shown with a
confusing `invalid character entity &`), so use the same settings as the cluster's rclone mount:

```sh
cd ~/Pictures/Photos
F=(--local-unicode-normalization --ignore-case --transfers 4 --checkers 4 --progress
   --s3-upload-cutoff 50M --s3-chunk-size 32M --s3-disable-http2 --max-connections 8
   --exclude ".DS_Store" --exclude "*.db" --exclude "*.aae" --exclude "*.thm" --exclude "*.xml"
   --exclude "*.mcf" --exclude "*.xml~" --exclude "*.wav" --exclude ".device_info_*")
rclone copy Amelie crypt:external/Amelie "${F[@]}" --exclude "/A trier/**" --dry-run   # then without --dry-run
rclone check Amelie crypt:external/Amelie "${F[@]}" --size-only --exclude "/A trier/**"   # 0 differences expected
```

`copy` is safe to repeat: it skips what is already there and sends the rest. An interrupted upload leaves no partial file (S3
objects are atomic). Without `--one-way`, `check` also lists files that exist only in the bucket (for example stale `.xmp`
sidecars, which Immich would read as dates: delete them with `rclone delete crypt:external/<Library> --include "*.xmp"`).
To move a folder between libraries, rename it on the Mac and use `rclone move` between the two bucket paths, then
`rclone rmdirs` on the old (empty) folder.

## Albums and sharing

[`scripts/photo-library/create-albums.sh`](../../scripts/photo-library/create-albums.sh) creates one album per folder and shares
the new albums with the other user. It runs the third-party [immich-folder-album-creator](https://github.com/Salvoxia/immich-folder-album-creator)
(MIT), pinned to release 2.0.3 by commit and checksum, with `uv`; it only talks to your Immich server.

1. In Immich, as the **owner of the library** (Account settings → API keys), create a key with these permissions:
   `asset.read`, `album.create`, `album.read`, `album.update`, `albumAsset.create`, `albumUser.create`, `user.read`.
2. In a terminal (the key is read from the environment and passed to the tool through a temporary private file, not on the
   command line):
   ```sh
   cd scripts/photo-library
   export IMMICH_URL=https://immich.vps.powple.com
   read -rs IMMICH_API_KEY && export IMMICH_API_KEY      # paste the key, nothing is echoed
   export SHARE_WITH="<the other user's name or email>"  # viewer by default (SHARE_ROLE=editor to change)
   ./create-albums.sh Shared                              # lists the albums, asks before creating; then Arnaud
   ```
3. Check the albums in Immich, and that the other user sees them. Re-running is safe: existing albums only get new photos, and
   only newly created albums are shared.

Version note: the tool's client library targets Immich server **3.3 or newer**; this cluster ran 3.2.0 when the albums were
created. If the tool fails on the first API calls (before it lists the albums), upgrade Immich first (the check is read-only
until you confirm).

`Amelie` is handled with the album levels `2,2` (period/event): the folder two levels below the library names the album, deeper
sub-folders (`Photos Triées/Civil/…`) go into that event's album, and a file directly in a period folder gets no album. Run it
with **Amelie's own API key** (she owns that library) and `SHARE_WITH` set to the other user.

### Renaming or moving a folder after it was scanned

Immich identifies an external photo by its **path**. A renamed or moved folder is therefore a set of *new* photos, and the
old ones turn offline. The album tool matches albums by **name**. To keep the album (and its shares) instead of getting a second one:

1. Rename or move the folder on the Mac, then in the bucket (`rclone move`, then `rclone rmdirs` on the empty old folder).
2. In Immich, rename the album to the new folder name, exactly (`2015 - 5) New-York` becomes `2015 - New-York`).
3. Rescan the library (Administration → Libraries): the new photos are imported, the old ones go offline.
4. Run `./create-albums.sh <Library>`: it finds the album by its new name and adds the new photos.
5. Administration → Libraries → the library's menu → **Remove offline files**; the album then only holds the new photos.

A folder moved to **another library** (for example `Amelie` to `Shared`) was never scanned in the old one if you move it before
the first scan: nothing to clean up, the new library just creates the album.

## Duplicates with older uploads

If photos of the archive were already uploaded to Immich, Immich's duplicate finder (Utilities → Review duplicates) groups them.
The archive wins: the uploaded copy goes to the trash, never the external one (trashing an external photo would only remove it
from Immich and from the albums created above). [`scripts/photo-library/dedupe.py`](../../scripts/photo-library/dedupe.py) does it safely:

- It only touches groups with **exactly one external photo** and uploaded copies that are **not larger** than it and have the
  **same aspect ratio** (rotation ignored). File names are not compared (uploads rename files). Everything else (two external
  photos, only uploads, a larger upload, another owner, unknown dimensions) is only listed, for you to review in Immich.
- Before trashing an uploaded copy it adds the external photo to the same albums and copies the favorite mark.
- Uploaded copies go to Immich's trash (30 days). The log (`log.jsonl`) lists every trashed photo; `undo` restores them.

Create a **separate API key** (Account settings → API keys) with `duplicate.read`, `album.read`, `albumAsset.create`,
`asset.update`, `asset.delete`, and delete it when done.

```sh
cd scripts/photo-library
export IMMICH_URL=https://immich.vps.powple.com
read -rs IMMICH_API_KEY && export IMMICH_API_KEY
uv run dedupe.py report --out ~/dedupe-1          # read-only: counts per reason, examples, report.csv
uv run dedupe.py apply --log ~/dedupe-1/log.jsonl --limit 10 --yes-trash <N>   # pilot: N = number of uploaded photos it would trash (a wrong N is refused and the right one printed)
# check the 10 groups in Immich (albums, favorites, the external photos are still there), then without --limit
uv run dedupe.py undo --log ~/dedupe-1/log.jsonl  # if something is wrong
```

Burst shots: Immich groups consecutive frames, so an upload can sit in a group with **several** external photos, which the tool
leaves alone by default. With `--match-names` (the same flag for `report` and `apply`) such an upload is trashed too when exactly
one external photo has the same file name (any case or extension), the same owner and the same pixel size (rotation ignored);
ambiguous cases (two external files with that name, another size) stay untouched. The report column `pairs` shows each upload
with the external photo it is matched to: review it before `apply`.

Do not empty Immich's trash until you are sure, and not before the first backup of `external/` has run.

### Duplicates inside the archive

Immich cannot remove files of an external library (trashing one only hides it until the next scan), so redundant copies are
removed from the Mac folders and the bucket, in a reversible way, by
[`scripts/photo-library/archive_dupes.py`](../../scripts/photo-library/archive_dupes.py). It reads the `report.csv` of
`dedupe.py` (groups with several external photos) and only acts on:

- **identical pixels** (compared on the image data, so different EXIF or file names do not matter): the best named copy is
  kept (camera names over Facebook-style names, dated folders over `vrac`),
- a **much smaller copy** (at most half the size, same aspect ratio) of the same picture: a thumbnail.

It never touches burst shots (same size, different pixels), other aspect ratios or files it cannot hash. With `plan --resized` it
also removes the smaller of two copies of the same picture at another size (for example the 80% versions): the larger one is
the better quality and is kept. Always use a fresh `dedupe.py report` (groups with files already moved are skipped).

```sh
cd scripts/photo-library
uv run archive_dupes.py plan --report ~/dedupe-2/report.csv --out ~/archive-dupes-1   # read-only; review plan.csv
uv run archive_dupes.py apply ~/archive-dupes-1/plan.csv --quarantine ~/Pictures/Photos-removed --yes-move <N>
```

`apply` re-checks that the kept file still has the same pixels, then **moves** the other file to the quarantine folder (same
sub-folders; keep it outside the photo root) and writes `moves.csv` (for `undo`) and `bucket-removals.txt`. Then remove the
same files from the bucket (rclone variables loaded as for the copy; try `--dry-run` first), and rescan the libraries in Immich:

```sh
rclone delete crypt:external --files-from-raw ~/archive-dupes-1/bucket-removals.txt --dry-run
rclone delete crypt:external --files-from-raw ~/archive-dupes-1/bucket-removals.txt
```

To go back: `uv run archive_dupes.py undo ~/archive-dupes-1/moves.csv`, then copy the files to the bucket again.
