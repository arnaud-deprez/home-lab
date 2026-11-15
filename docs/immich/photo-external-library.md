# Curated photo archive (Immich external libraries)

The family archive (folders per year and event) is indexed by Immich **in place** as read-only
*external libraries*. Immich never moves, renames or edits these files, so the folder tree stays the
backup format, unlike uploads, which the storage template (`{{y}}/{{y-MM-dd}}/{{filename}}`) re-files.
Design: [`superpowers/specs/2026-11-15-immich-external-photo-library-design.md`](../superpowers/specs/2026-11-15-immich-external-photo-library-design.md).

## Where it lives

| | |
| --- | --- |
| Path in every cluster | `/data/external/<Library>` in the immich-server pod (`Shared`, `Arnaud`, `Amelie`) |
| euronodes | `immich/external/` in the rclone-crypt S3 mount; `/data/external` is a symlink to it (init container `wait-mount`) |
| Other clusters | mount a PVC read-only at `/data/external` in the Immich pod; nothing else differs. Not done yet |
| Backup (euronodes) | restic job (`/mnt/rclone/external`), and the manual disk copy ([`disk-backup.md`](../disk-backup.md)) |
| Source of truth | the folders on your Mac and the original hard drive |

## Scope and albums

Defined in [`scripts/photo-library/config.json`](../../scripts/photo-library/config.json).

- `Shared`, `Arnaud` (not `Arnaud/papa`): every direct sub-folder is an album.
- `Amelie`: the 13 school periods `2003-2004(3e)` to `2015-2016(Recherche d'emploi - Entrée P&T )`: every *event* folder
  under a period is an album (the period is not in the name). Add later periods to `include`.
- Album name = the folder name. `python3 scripts/photo-library/list_albums.py <phase>` lists them and any name that
  would collide. macOS stores some names decomposed (NFD: `é` as `e` + accent); all names and filters are compared
  normalised to NFC. Make sure the copy to S3 normalises too (`rclone --local-unicode-normalization`).
- Ignored files: `.DS_Store`, `*.db`, `*.aae`, `*.thm`, `*.xml`, `*.mcf`, `*.xml~`, `*.wav`.

## Fixing dates (on the Mac, once, before copying)

Immich trusts the EXIF capture date; many old files have a wrong or empty one. Dates are written into the files
with exiftool (always `-P`: the modification time is kept, it is the fallback date).

Requirements: `brew install uv exiftool` (uv pins Python 3.13 and the empty lockfile, `scripts/photo-library/uv.lock`; the scripts use
the standard library only, so `uv run` needs no install step). Tests: `uv run python -m unittest discover -s tests`.

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
