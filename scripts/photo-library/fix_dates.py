#!/usr/bin/env python3
"""Fix capture dates in the photo files.

  fix_dates.py plan <phase> --out DIR     dry run: writes DIR/plan.csv and prints a report
  fix_dates.py apply DIR/plan.csv         (added with the apply task)
  fix_dates.py undo DIR/undo.csv          (added with the apply task)
"""
import argparse
import csv
import fnmatch
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from photolib.config import Config, load_config, parse_override
from photolib.dates import PLACEHOLDER_MIN_FILES, choose_date
from photolib.exif import RAW_TAGS, read_dates, read_raw, set_tags, write_dates
from photolib.scope import iter_albums, nfc

MEDIA = {".jpg", ".jpeg", ".jfif", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff", ".bmp",
         ".mov", ".mp4", ".m4v", ".avi", ".mpg", ".mpeg", ".mts", ".m2ts", ".3gp", ".wmv"}
PLAN_COLUMNS = ["path", "old_exif", "mtime", "new_date", "rule", "writable"]
ISO = "%Y-%m-%dT%H:%M:%S"


def _files(album_path: Path, ignore_globs: list) -> list:
    files = [p for p in album_path.rglob("*") if p.is_file()
             and not any(fnmatch.fnmatch(p.name, g) for g in ignore_globs)
             and p.suffix.lower() in MEDIA]
    return sorted(files, key=lambda p: nfc(str(p.relative_to(album_path))))


def _placeholder_days(mtimes, folder_year) -> frozenset:
    """Days (with a year far from the folder's) that many files of one album share."""
    if folder_year is None:
        return frozenset()
    days = Counter(m.date() for m in mtimes if abs(m.year - folder_year) > 1)
    return frozenset(d for d, n in days.items() if n >= PLACEHOLDER_MIN_FILES)


def build_plan(cfg: Config, phase: int, out: Path, now: Optional[datetime] = None) -> Counter:
    now = now or datetime.now()
    out.mkdir(parents=True, exist_ok=True)
    counts: Counter = Counter()
    with open(out / "plan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, PLAN_COLUMNS)
        w.writeheader()
        for album in iter_albums(cfg, phase):
            files = _files(album.path, cfg.ignore_globs)
            infos = read_dates(files)
            mtimes = {p: datetime.fromtimestamp(p.stat().st_mtime).replace(microsecond=0) for p in files}
            placeholders = _placeholder_days(mtimes.values(), album.year)
            override = cfg.date_overrides.get(f"{album.library}/{album.name}")
            start = parse_override(override) if override else None
            for seq, p in enumerate(files):
                info = infos[p]
                mtime = mtimes[p]
                d = choose_date(info.exif, mtime, album.year, seq, now, placeholders, start)
                counts[(album.library, d.rule)] += 1
                if not info.writable:
                    counts[(album.library, "not-writable")] += 1
                w.writerow({
                    "path": str(p),
                    "old_exif": info.exif.strftime(ISO) if info.exif else "",
                    "mtime": mtime.strftime(ISO),
                    "new_date": d.date.strftime(ISO) if d.date else "",
                    "rule": d.rule,
                    "writable": info.writable,
                })
    return counts


UNDO_COLUMNS = ["path", *RAW_TAGS, "mtime"]


def _read_csv(path: Path) -> list:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def apply_plan(plan_csv: Path, undo_csv: Path) -> list:
    """Write the planned dates. Writes the undo log first. Returns the failed paths."""
    plan_csv = Path(plan_csv)
    rows = [r for r in _read_csv(plan_csv)
            if r["rule"] not in ("keep-exif", "skip-no-year") and r["writable"] == "True" and r["new_date"]]
    paths = [Path(r["path"]) for r in rows]
    plan_time = plan_csv.stat().st_mtime
    newer = [p for p in paths if p.stat().st_mtime > plan_time]
    if newer:
        raise RuntimeError(f"plan is stale: {len(newer)} files changed after it was made (e.g. {newer[0]})")
    old = read_raw(paths)
    with open(undo_csv, "w", newline="", encoding="utf-8") as fh:  # fails before any write if not writable
        w = csv.DictWriter(fh, UNDO_COLUMNS)
        w.writeheader()
        for p in paths:
            w.writerow({"path": str(p), **old[p], "mtime": repr(p.stat().st_mtime)})
        fh.flush()
        os.fsync(fh.fileno())
    mtimes = {p: p.stat().st_mtime for p in paths}
    changes = {Path(r["path"]): datetime.strptime(r["new_date"], ISO) for r in rows}
    failed = write_dates(changes)
    for p in paths:  # -P keeps mtime; restore anyway if something moved it
        if p.stat().st_mtime != mtimes[p]:
            os.utime(p, (mtimes[p], mtimes[p]))
    return failed


def undo_changes(undo_csv: Path) -> list:
    """Restore the old tag values and modification times. Returns paths with values that cannot be restored."""
    settings, mtimes, unrestorable = {}, {}, []
    for r in _read_csv(undo_csv):
        p = Path(r["path"])
        tags = {}
        for t in RAW_TAGS:
            v = r[t]
            if v.startswith("0000"):  # exiftool refuses to write a zero date back
                unrestorable.append(p)
                continue
            tags[t] = v or None
        settings[p] = tags
        mtimes[p] = float(r["mtime"])
    set_tags(settings)
    for p, m in mtimes.items():
        os.utime(p, (m, m))
    return unrestorable


def print_report(counts: Counter) -> None:
    for (lib, rule), n in sorted(counts.items()):
        print(f"{lib:8} {rule:24} {n}")


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("phase", type=int)
    p.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("apply")
    a.add_argument("plan", type=Path)
    u = sub.add_parser("undo")
    u.add_argument("undo_csv", type=Path)
    args = ap.parse_args(argv[1:])
    if args.cmd == "plan":
        cfg = load_config(Path(__file__).with_name("config.json"))
        print_report(build_plan(cfg, args.phase, args.out))
        print(f"plan written to {args.out / 'plan.csv'}")
    elif args.cmd == "apply":
        failed = apply_plan(args.plan, args.plan.with_name("undo.csv"))
        for p in failed:
            print(f"FAILED {p}")
        print(f"done, undo log: {args.plan.with_name('undo.csv')}; failures: {len(failed)}")
        return 1 if failed else 0
    elif args.cmd == "undo":
        for p in undo_changes(args.undo_csv):
            print(f"not restorable (zero date): {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
