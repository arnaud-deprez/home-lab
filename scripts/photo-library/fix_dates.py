#!/usr/bin/env python3
"""Fix capture dates in the photo files.

  fix_dates.py plan <phase> --out DIR     dry run: writes DIR/plan.csv and prints a report
  fix_dates.py apply DIR/plan.csv         (added with the apply task)
  fix_dates.py undo DIR/undo.csv          (added with the apply task)
"""
import argparse
import csv
import fnmatch
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from photolib.config import Config, load_config
from photolib.dates import choose_date
from photolib.exif import read_dates
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
            for seq, p in enumerate(files):
                info = infos[p]
                mtime = datetime.fromtimestamp(p.stat().st_mtime).replace(microsecond=0)
                d = choose_date(info.exif, mtime, album.year, seq, now)
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


def print_report(counts: Counter) -> None:
    for (lib, rule), n in sorted(counts.items()):
        print(f"{lib:8} {rule:24} {n}")


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("phase", type=int)
    p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv[1:])
    if args.cmd == "plan":
        cfg = load_config(Path(__file__).with_name("config.json"))
        print_report(build_plan(cfg, args.phase, args.out))
        print(f"plan written to {args.out / 'plan.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
