#!/usr/bin/env python3
"""Remove redundant copies from the photo archive on the Mac, reversibly (they go to a quarantine folder).

  uv run archive_dupes.py plan  --report ~/dedupe-2/report.csv --out ~/archive-dupes-1   read-only: plan.csv + summary
  uv run archive_dupes.py scan  --library Amelie --out ~/amelie-dupes   exact duplicates on disk, read-only
  uv run archive_dupes.py apply ~/archive-dupes-1/plan.csv --quarantine ~/Pictures/Photos-removed --yes-move <N>
  uv run archive_dupes.py undo  ~/archive-dupes-1/moves.csv

Actionable groups only: identical pixels (one copy kept, the best named) or a much smaller copy of the same picture
(thumbnail). With --resized, the smaller of two copies of the same picture at any other size is removed too (the larger
is kept). Different pictures (burst shots, other aspect ratio) are never touched. `apply` also writes bucket-removals.txt: the
paths to remove from the bucket with rclone (see docs/immich/photo-external-library.md).
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from photolib.archive_dupes import PLAN_COLUMNS, apply_plan, plan_rows, undo_moves
from photolib.config import load_config
from photolib.local_dupes import scan_local


def _root() -> Path:
    return load_config(Path(__file__).with_name("config.json")).root


def cmd_plan(args) -> int:
    rows = plan_rows(_root(), args.report, args.resized, args.leftovers)
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "plan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, PLAN_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    groups = Counter(r["reason"] for r in rows)
    print("groups by outcome:")
    for k, n in groups.most_common():
        print(f"  {k:20} {n}")
    todo = [r for r in rows if r["remove"]]
    print(f"\n{len(todo)} files would move to the quarantine. Full list: {args.out / 'plan.csv'}")
    for r in todo[:4]:
        print(f"  keep  {r['keep']}\n  move  {r['remove']}  ({r['reason']})")
    return 0


def cmd_scan(args) -> int:
    cfg = load_config(Path(__file__).with_name("config.json"))
    rows = scan_local(cfg.root, args.library, cfg.ignore_globs)
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "plan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, PLAN_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len({r['group'] for r in rows})} groups of exact duplicates; {len(rows)} files would move "
          f"({sum(int(r['remove_bytes']) for r in rows) / 1e9:.1f} GB). Full list: {args.out / 'plan.csv'}")
    for k, n in Counter(r["reason"] for r in rows).most_common():
        print(f"  {k:16} {n}")
    return 0


def cmd_apply(args) -> int:
    with open(args.plan, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    n = sum(1 for r in rows if r["remove"])
    if args.yes_move != n:
        print(f"This would move {n} files to {args.quarantine}. Confirm with --yes-move {n}", file=sys.stderr)
        return 2
    moved = apply_plan(_root(), rows, args.quarantine, args.plan.parent)
    print(f"{moved} of {n} files moved to {args.quarantine} (the others failed the re-check and stayed).")
    print(f"Bucket removals: {args.plan.parent / 'bucket-removals.txt'}; undo: archive_dupes.py undo {args.plan.parent / 'moves.csv'}")
    return 0


def cmd_undo(args) -> int:
    print(f"{undo_moves(_root(), args.moves)} files put back")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--resized", action="store_true",
                   help="also remove the smaller of two copies of the same picture at different sizes")
    p.add_argument("--leftovers", action="store_true",
                   help="also pairs left over, that you have checked by hand: identical files (any name) and, "
                        "for the rest, the smaller file; delete the rows you do not want from plan.csv")
    sc = sub.add_parser("scan", help="exact duplicates on disk, no Immich needed (read-only: writes plan.csv)")
    sc.add_argument("--library", action="append", required=True, help="library folder under the photo root; repeatable")
    sc.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("apply")
    a.add_argument("plan", type=Path)
    a.add_argument("--quarantine", type=Path, required=True)
    a.add_argument("--yes-move", type=int, required=True, dest="yes_move")
    u = sub.add_parser("undo")
    u.add_argument("moves", type=Path)
    args = ap.parse_args(argv[1:])
    return {"scan": cmd_scan, "plan": cmd_plan, "apply": cmd_apply, "undo": cmd_undo}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
