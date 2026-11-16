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
- `Amelie`: the 13 school periods `2003-2004(3e)` to `2015-2016(Recherche d'emploi - Entrée P&T )`: every *event* folder
  under a period is an album (the period is not in the name). Add later periods to `include`.
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
until you confirm). `Amelie` is not supported by the script yet: its events are two levels down (phase 2).

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
uv run dedupe.py apply --log ~/dedupe-1/log.jsonl --limit 10 --yes-trash <N>   # pilot: N is printed if you omit it
# check the 10 groups in Immich (albums, favorites, the external photos are still there), then without --limit
uv run dedupe.py undo --log ~/dedupe-1/log.jsonl  # if something is wrong
```

Do not empty Immich's trash until you are sure, and not before the first backup of `external/` has run.
