# Immich External Photo Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index the user's curated photo archive in Immich as read-only external libraries on euronodes, with corrected dates, one shared album per folder, and backups, phase 1 (`Shared`, `Arnaud`) before phase 2 (`Amelie`).

**Architecture:** Python tooling on the user's Mac fixes dates in the files with exiftool (undo log, dry-run first) and lists the album set; rclone copies the tree into an `external/` prefix of the existing rclone-crypt S3 mount; Flux manifests expose it to immich-server at `/mnt/rclone/external`; a containerised folder-album tool creates and shares the albums. Only euronodes is touched now; the path convention lets other clusters opt in later.

**Tech Stack:** Python 3.13 stdlib (`unittest`) run with `uv` (`scripts/photo-library/pyproject.toml`, `uv.lock`: `uv run python -m unittest discover -s tests`), exiftool, rclone, Kustomize/Flux, Immich API, `salvoxia/immich-folder-album-creator` (Docker).

**Spec:** `docs/superpowers/specs/2026-11-15-immich-external-photo-library-design.md`

## Global Constraints

- Source root `/Users/arnaud/Pictures/Photos`; the hard drive holds the untouched originals; files are edited in place.
- Libraries: `Shared` and `Arnaud` (all subfolders except `Arnaud/papa`), `Amelie` (periods `2003-2004(3e)` through `2015-2016(Recherche d'emploi - Entrée P&T )`, 13 folders, copy names from `ls`). Album = direct subfolder for `Shared`/`Arnaud`; album = event folder under a period for `Amelie`; album name = the folder name as is.
- Excluded files: `.DS_Store`, `*.db`, `*.aae`, `*.thm`, `*.xml`, `*.mcf`, `*.xml~`, `*.wav`.
- exiftool always with `-P` (preserve modification time); undo CSV written before any write; dry-run report first.
- Date rule: keep EXIF `DateTimeOriginal` if its year is within 1 of the folder year; else file modification date if its year is within 1; else folder year with the modification date's month/day/time; else folder year, 1 January 12:00:00 plus 1 second per file in filename order. Folder without a parsable `YYYY` prefix: skip and report, never guess.
- `avi`, `mpg`, `mts` are not re-encoded; reported in the dry-run, modification date kept.
- Import paths: `<root>/<Library>`, `<root>` = `/mnt/rclone/external` on euronodes (amended 2026-11-15: Immich refuses import paths under its media location `/data`, so the first idea, a `/data/external` symlink, was dropped; other clusters pick their own `<root>` outside `/data`).
- Keep the rclone mount flags (HTTP/2 off, `--max-connections=8`) unchanged. Library scans manual only. Do not run a restic backup of `external/` during the first scan.
- Claude never decrypts SOPS files, never sees API keys or rclone secrets; the user runs every command that needs them. Before committing manifests, follow the `flux-ops` skill (kustomize build, dry-run, flux diff).
- On duplicates the external archive wins; nothing is deleted without the user reviewing the list.
- Work on branch `feat/immich-external-library`; commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- macOS folder names are Unicode NFD (`soirée`) but S3/Linux and Immich expect NFC: the copy and the album names must not diverge or double.
- Folder names with `&`, `(`, `)`, `'`, spaces and trailing spaces (`...P&T )`) must survive exiftool, rclone and the album tool.
- Feb 29 modification dates moved onto a non-leap folder year must not crash the date rule.
- Videos (`mov`, `mp4`) keep their date in QuickTime tags (UTC), not EXIF; `0000:00:00` and unreadable files must not abort a whole run.
- Same album name in two Amelie periods, or a filename present in several folders.

---

## File Structure

```
scripts/photo-library/
  config.json              scope: root, libraries, includes/excludes, ignored files, phases
  photolib/__init__.py
  photolib/config.py       load_config
  photolib/scope.py        iter_albums, find_collisions, folder_year
  photolib/dates.py        choose_date (pure rule)
  photolib/exif.py         read_dates, write_dates (exiftool wrapper)
  fix_dates.py             CLI: plan | apply | undo
  list_albums.py           CLI: print albums + collisions for a phase
  create-albums.sh         runs the album tool per library (needs the user's API key in env)
  tests/test_*.py
apps/euronodes/immich/helmrelease-patch.yaml   wait-mount init: external/ folder and .keep marker
apps/euronodes/immich/restic-backup.yaml       add /mnt/rclone/external
scripts/euronodes-disk-backup.sh               PHOTO_DIRS gains external
docs/immich/photo-external-library.md                          runbook; docs/euronodes.md, backup-restore.md, disk-backup.md updated
```

---

### Task 1: Scope config and album listing

**Files:**
- Create: `scripts/photo-library/config.json`, `photolib/__init__.py`, `photolib/config.py`, `photolib/scope.py`, `list_albums.py`
- Test: `scripts/photo-library/tests/test_scope.py`

**Interfaces:**
- Produces: `load_config(path: Path) -> Config` (`root: Path`, `libraries: list[Library]`, `ignore_globs: list[str]`, `phases: dict[int, list[str]]`); `Library(name: str, album_depth: Literal["child","grandchild"], include: list[str]|None, exclude: list[str])`; `Album(library: str, name: str, path: Path, year: int|None)`; `folder_year(name: str) -> int|None` (leading `YYYY` followed by space or `-`/`)`... only `19xx`/`20xx` and a space); `iter_albums(cfg: Config, phase: int) -> list[Album]` sorted by path; `find_collisions(albums: list[Album]) -> dict[str, list[Album]]` (names used by more than one album).

- [ ] **Step 1: Write failing tests** against a temp tree built in `setUp`: `Arnaud/{2007 - a, papa}`, `Shared/2009 - b`, `Amelie/P1/{2005 - 1) x, 2005 - 2) y}`, `Amelie/P2/2006 - 1) x`, `Amelie/Later/2020 - z` with `include=["P1","P2"]`. Assert `iter_albums(cfg,1)` names == `["2007 - a","2009 - b"]` (no `papa`); `iter_albums(cfg,2)` names == `["2005 - 1) x","2005 - 2) y","2006 - 1) x"]` (no `Later`); `folder_year("2005 - 1) londres")==2005`, `folder_year("papa") is None`, `folder_year("Padova") is None`; a tree with `P2/2005 - 1) x` makes `find_collisions` return `{"2005 - 1) x": [two albums]}`. Names compared after NFC normalisation (add a test with an NFD `soirée` folder equal to an NFC one → collision).
- [ ] **Step 2:** Run `cd scripts/photo-library && python3 -m unittest discover -s tests -v`. Expected: FAIL (import error).
- [ ] **Step 3:** Implement the signatures above; `config.json` holds the real scope (Amelie `include` = the 13 period names copied from `ls /Users/arnaud/Pictures/Photos/Amelie`, phases `{"1":["Shared","Arnaud"],"2":["Amelie"]}`). `list_albums.py <phase>` prints `library<TAB>name<TAB>year` and a final `COLLISIONS:` block.
- [ ] **Step 4:** Run the tests: PASS. Run `python3 list_albums.py 1` against the real tree; expected: about 17 Shared + about 45 Arnaud albums, no `papa`, empty collisions or a list for the user.
- [ ] **Step 5:** Commit `feat(photo-library): scope config and album listing`.

### Task 2: Date rule

**Files:**
- Create: `scripts/photo-library/photolib/dates.py`
- Test: `scripts/photo-library/tests/test_dates.py`

**Interfaces:**
- Produces: `Decision(date: datetime|None, rule: str)` with `rule` in `keep-exif | from-mtime | mtime-day-folder-year | jan1-seq | skip-no-year`; `choose_date(exif: datetime|None, mtime: datetime, folder_year: int|None, seq: int, now: datetime) -> Decision`. "Valid mtime" means year between 1990 and `now.year` inclusive.

- [ ] **Step 1: Write failing tests** (folder_year 2009 unless stated): exif 2009-08-01 → `keep-exif`, same date; exif 2010-01-02 → `keep-exif`; exif 2007-02-04 + mtime 2009-07-03 → `from-mtime` with the mtime; exif None + mtime 2009 → `from-mtime`; exif 2007 + mtime 2015-06-05 10:00:00 → `mtime-day-folder-year` == 2009-06-05 10:00:00; mtime 2024-02-29 with folder_year 2009 → 2009-02-28 (no crash); exif None + mtime 1970-01-01 → `jan1-seq` == 2009-01-01 12:00:00 plus `seq` seconds (seq=3 → 12:00:03); mtime in the future → `jan1-seq`; folder_year None → `skip-no-year`, `date is None`.
- [ ] **Step 2:** Run the tests; expected FAIL.
- [ ] **Step 3:** Implement `choose_date` in the order of the Global Constraints rule (first match wins).
- [ ] **Step 4:** Run the tests: PASS.
- [ ] **Step 5:** Commit `feat(photo-library): date decision rule`.

### Task 3: exiftool wrapper and `fix_dates.py plan` (dry-run)

**Files:**
- Create: `photolib/exif.py`, `fix_dates.py`
- Test: `tests/test_exif.py` (uses real exiftool on tiny generated files: a JPEG with EXIF set via exiftool, one with `0000:00:00`, one `mov`/`avi` placeholder copy)

**Interfaces:**
- Consumes: `iter_albums`, `choose_date`.
- Produces: `read_dates(paths: list[Path]) -> dict[Path, ExifInfo]` (`ExifInfo(exif: datetime|None, writable: bool)`; `0000:00:00` and unparsable → `exif=None`; read `DateTimeOriginal`, then `CreateDate`/`MediaCreateDate` for videos; `writable=False` for `avi`, `mpg`, `mts`); `write_dates(changes: dict[Path, datetime]) -> list[Path]` returns failures (Task 4). `fix_dates.py plan <phase> --out DIR` writes `DIR/plan.csv` (`path,old_exif,mtime,new_date,rule,writable`) and prints counts per rule per library and the list of skipped (`skip-no-year`) and non-writable files. Files are processed per album in filename order, `seq` = index in that order; ignored globs from config are skipped; exiftool runs in batches (one `-@` argfile per album) and a failed file is recorded, never fatal.

- [ ] **Step 1: Write failing tests**: `read_dates` returns the parsed datetime for a file with `DateTimeOriginal`, `None` for a `0000:00:00` file, and `writable False` for an `.avi`; `plan` on a temp tree with one album writes a CSV with the expected rule per file and exits 0 without modifying any file (compare mtime and bytes before/after).
- [ ] **Step 2:** Run: FAIL.
- [ ] **Step 3:** Implement with `exiftool -json -fast2 -d %Y-%m-%dT%H:%M:%S`. Handle filenames with `&`, quotes, accents by passing paths through an argfile with `-charset filename=UTF8`.
- [ ] **Step 4:** Run the tests: PASS. Run `python3 fix_dates.py plan 1 --out /private/tmp/claude-501/-Users-arnaud-Development-arnaud-deprez-home-lab/13b8148e-8166-4465-8983-cfc31654240d/scratchpad/plan1` and show the user the counts; expected: about 74% `keep-exif`, plus the rule breakdown for the rest.
- [ ] **Step 5:** Commit `feat(photo-library): date dry-run report`.

### Task 4: `fix_dates.py apply` and `undo`

**Files:**
- Modify: `fix_dates.py`, `photolib/exif.py`
- Test: `tests/test_apply.py`

**Interfaces:**
- Produces: `fix_dates.py apply DIR/plan.csv` writes `DIR/undo.csv` (`path,old DateTimeOriginal,old CreateDate,old MediaCreateDate,old mtime`) **before** the first write, then writes `DateTimeOriginal`+`CreateDate` (images) or QuickTime `CreateDate`/`MediaCreateDate`/`TrackCreateDate` with `-api QuickTimeUTC` (mov/mp4) only for rows whose `rule != keep-exif` and `writable`; always `-P -overwrite_original -m`. `fix_dates.py undo DIR/undo.csv` restores the tags and mtime. Exit non-zero with the failed paths listed if any write failed.

- [ ] **Step 1: Write failing tests** on a temp JPEG and a temp `mov`: after `apply`, the EXIF date equals `new_date` and the file mtime is unchanged; `undo.csv` exists and has the old values; after `undo` the original tags and mtime are back; a row with `keep-exif` and a non-writable `avi` row are not touched (bytes identical).
- [ ] **Step 2:** Run: FAIL.
- [ ] **Step 3:** Implement `write_dates` and the two subcommands; `apply` refuses to run when `undo.csv` cannot be written or `plan.csv` is older than the newest file mtime in it (stale plan).
- [ ] **Step 4:** Run the tests: PASS.
- [ ] **Step 5:** Commit `feat(photo-library): apply and undo date fixes`.

### Task 5: Euronodes manifests, restic, disk backup

**Files:**
- Modify: `apps/euronodes/immich/helmrelease-patch.yaml` (wait-mount `command`), `apps/euronodes/immich/restic-backup.yaml` (backup paths), `scripts/euronodes-disk-backup.sh` (`PHOTO_DIRS`, guard logic)

**Interfaces:**
- Produces: in immich-server, `/mnt/rclone/external` exists (marker `.keep`); restic snapshot paths include `/mnt/rclone/external`; the disk-backup script copies `external` too.

- [ ] **Step 1: Define the checks** (the failing "test"): `kustomize build apps/euronodes/immich | grep -c external` must show the `external/` folder creation and the restic path; `grep PHOTO_DIRS scripts/euronodes-disk-backup.sh` must show `external`.
- [ ] **Step 2:** Run them: expect no match.
- [ ] **Step 3: Implement.** In `wait-mount`: run `mkdir -p /mnt/rclone/external` and create the marker `/mnt/rclone/external/.keep` after `.mounted` is seen (no symlink: see the amendment above). Add `/mnt/rclone/external` to the `r backup ... /mnt/rclone/library /mnt/rclone/upload` line. In the disk-backup script, add `external` to the optional photo folders (skipped while empty, so an empty source never empties the disk copy). Do **not** run the script (it decrypts at runtime).
- [ ] **Step 4:** Run the Step 1 checks (expect matches), then the `flux-ops` skill validation: `kustomize build apps/euronodes | kubectl apply --dry-run=server -f -` if the cluster is reachable, otherwise a client-side build, and `flux diff kustomization apps --path ./clusters/euronodes` per the skill. Expected: only the two edited objects differ.
- [ ] **Step 5:** Commit `feat(euronodes): expose and back up the external photo library`.

### Task 6: Documentation

**Files:**
- Create: `docs/immich/photo-external-library.md`
- Modify: `docs/euronodes.md` (storage table and rclone section), `docs/backup-restore.md` (table at line ~20, restore commands at ~286: include `external`), `docs/disk-backup.md` (layout at ~22, steps ~173: `external`), `CLAUDE.md` (one line linking the new doc)

- [ ] **Step 1:** Write `docs/immich/photo-external-library.md`: purpose, `/mnt/rclone/external/<Library>` convention for other clusters (a PVC mounted at that path, read-only, is all a cluster needs), scope and album rules, date rule and the undo procedure, the phase workflow (Tasks 3-9), how to add Amelie's later periods (edit `config.json`), the NFD/NFC note, restore of one file, how to disable the HTTP/2 experiment later (pointer only).
- [ ] **Step 2:** Update the four existing docs consistently with Task 5.
- [ ] **Step 3:** Verify: `grep -rn "library.*upload" docs/*.md` shows no remaining statement that only `library` and `upload` are backed up.
- [ ] **Step 4:** Commit `docs: external photo library runbook`.

### Task 7: Mac rclone remote and phase 1 copy (guided, user runs the commands)

**Files:** none in the repo (add the exact commands to `docs/immich/photo-external-library.md` as they are proven).

- [ ] **Step 1:** Tell the user which keys to read from the decrypted `apps/euronodes/immich/rclone-secret.sops.yaml` (they run `sops -d` themselves): the S3 endpoint, access key, secret, bucket `home`, prefix `immich/`, the crypt passwords and filename-encryption setting. Never ask for the values to be pasted.
- [ ] **Step 2:** Guide `rclone config` on the Mac to create two remotes: `euro-s3` (S3 provider, same endpoint and V4/V2 signature setting as the cluster, see the secret's note) and `euro-crypt` (crypt over `euro-s3:home/immich`, same passwords and `filename_encryption`). Verify: `rclone lsd euro-crypt:` lists `library` and `upload`, and `rclone cat euro-crypt:.mounted` succeeds. If it does not, stop and compare with the cluster config.
- [ ] **Step 3:** Test copy: `rclone copy "<one small album>" euro-crypt:external/Test --local-unicode-normalization --progress`, then `kubectl -n immich exec deploy/immich-server -- ls /mnt/rclone/external/Test` (after Flux applied Task 5) shows the files with correct accents; delete `external/Test` afterwards.
- [ ] **Step 4:** After the user approved the phase 1 date report (Task 3) and ran `apply` (Task 4): `rclone copy` `Shared` and `Arnaud` (excluding the ignored globs and `Arnaud/papa`) to `euro-crypt:external/Shared` and `external/Arnaud`, `--transfers 4 --checkers 4`, then `rclone check` and compare `rclone size`. Before the copy: suspend the nightly backup (`kubectl -n immich patch cronjob restic-backup -p '{"spec":{"suspend":true}}'`).
- [ ] **Step 5:** No commit (docs commit with Task 9).

### Task 8: Roll out the manifests (Flux)

- [ ] **Step 1:** Push the branch and open a PR to `main` (user decides when to merge); after merge run `flux reconcile kustomization apps --with-source` and `flux get kustomizations` (expect READY=True).
- [ ] **Step 2:** Verify the pod: `kubectl -n immich exec deploy/immich-server -- ls /mnt/rclone/external` lists `Shared` and `Arnaud` once Task 7 is done.

### Task 9: Immich libraries, scan, folder view, dates

- [ ] **Step 1:** User creates external libraries in Administration (owner Arnaud for `Arnaud` and `Shared`; owner Amelie later for `Amelie`), import paths `/mnt/rclone/external/Arnaud` and `/mnt/rclone/external/Shared`, exclusion patterns for the ignored globs, no scheduled scan. Record the Immich assets count before the scan (Administration > Server stats) as the "before" duplicate check.
- [ ] **Step 2:** Run a manual scan of the library `Shared` first. Watch `kubectl -n immich top pod`, `kubectl -n immich get pods` (restarts) and the rclone pod's memory (limit 1 Gi). Expected: no restarts and no probe failures; if they appear, stop and reduce job concurrency in Immich settings before continuing.
- [ ] **Step 3:** Scan `Arnaud`. Check: Folders view shows `Shared/<album folder>/…`; timeline dates match the date plan for a sample of 10 files per rule; videos play (including any `avi`/`mpg`/`mts`).
- [ ] **Step 4:** Resume the nightly backup (`suspend: false`), then after the 04:00 run check `restic snapshots` paths include `/mnt/rclone/external` (user runs it; the repository password stays with them).

### Task 10: Albums and sharing

**Files:**
- Create: `scripts/photo-library/create-albums.sh`
- Test: dry-run output of `list_albums.py` compared with the created albums

**Interfaces:**
- Consumes: `config.json`, `IMMICH_URL` and `IMMICH_API_KEY` from the user's environment.
- Produces: `create-albums.sh <Library>` runs `salvoxia/immich-folder-album-creator` in Docker with `--root-path /mnt/rclone/external/<Library>` (and one run per included period for `Amelie`), album level 1 for `Shared`/`Arnaud` and the last folder level for `Amelie`, sharing with the other account as viewer; first run with the tool's dry-run/`--help`-documented preview option.

- [ ] **Step 1:** Run `python3 list_albums.py 1`; resolve any `COLLISIONS` with the user before creating albums.
- [ ] **Step 2:** Confirm the tool's flags with `docker run --rm salvoxia/immich-folder-album-creator:latest --help` (root path, album levels, share-with, ignore, unattended) and adjust the script accordingly; pin the image tag.
- [ ] **Step 3:** The user runs `create-albums.sh Shared` then `Arnaud` with their own API key; verify the album count equals the `list_albums.py 1` count and that Amelie's account sees them.
- [ ] **Step 4:** Commit `feat(photo-library): album creation script`.

### Task 11: Duplicates

- [ ] **Step 1:** Compare the asset count with the "before" figure; run Immich's duplicate detection (Utilities > Review duplicates) after its job finished.
- [ ] **Step 2:** Present the list to the user. For each group, the external asset is kept and the uploaded one is deleted only after the user confirms. Record decisions in `docs/immich/photo-external-library.md` if a recurring pattern appears.

### Task 12: Phase 2 (`Amelie`)

- [ ] **Step 1:** Re-run `list_albums.py 2` (collisions) and `fix_dates.py plan 2`; the user reviews the report, then `apply`. Re-measure the in-scope size and confirm the S3 and Storage Box headroom.
- [ ] **Step 2:** Repeat Task 7 steps 4 (copy `Amelie` periods only, backup suspended), Task 9 (library owned by Amelie, scan in one or two batches of periods), Task 10 (`create-albums.sh Amelie` with Amelie's key), Task 11.
- [ ] **Step 3:** Update `docs/immich/photo-external-library.md` with measured durations and any tuning; commit.

---

## Self-review notes

- Spec coverage: scope/albums (Tasks 1, 10), dates (2-4), hosting (5, 8), backup and disk backup (5, 6, 9 step 4), phasing (1, 12), duplicates (9, 11), video decision (3, 9), rclone setup (7), calls on scans/backup sequencing (7, 9).
- Deviations from the spec, to flag to the user: container path `/mnt/rclone/external` instead of `/external`; the base manifests are not modified (laptop and tierhive are untouched until they opt in), so "base declares the volume" becomes "documented convention".
- The date rule's third and fourth cases interpret the spec's "call": the third applies when the modification date is a valid date but its year is off; the fourth when it is not valid.
