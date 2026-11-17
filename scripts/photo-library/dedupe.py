#!/usr/bin/env python3
"""Trash the uploaded copies of photos that are also in the external library (the external archive wins).

  export IMMICH_URL=https://immich.example.com
  read -rs IMMICH_API_KEY && export IMMICH_API_KEY
  uv run dedupe.py report --out ~/dedupe-1                      read-only: classify every duplicate group
  uv run dedupe.py apply --log ~/dedupe-1/log.jsonl --limit 10 --yes-trash <N>   pilot on the first groups
  uv run dedupe.py undo  --log ~/dedupe-1/log.jsonl             take everything in the log out of the trash

Only groups with exactly one external photo and uploaded copies that are not larger and have the same aspect ratio are
touched (tier A). Everything else is only listed. Uploaded copies go to Immich's trash, never deleted for good. Their albums
and favorite mark are copied to the external photo first. Key permissions: duplicate.read, album.read, albumAsset.create,
asset.update, asset.delete.
"""
import argparse
import os
import sys
from pathlib import Path

from photolib.dedupe import apply_verdicts, classify, summarize, undo_trash, write_report
from photolib.immich import ImmichClient


def _client() -> ImmichClient:
    url, key = os.environ.get("IMMICH_URL"), os.environ.get("IMMICH_API_KEY")
    if not url or not key:
        sys.exit("ERROR: set IMMICH_URL and IMMICH_API_KEY (never on the command line)")
    return ImmichClient(url, key)


def _show(a: dict) -> str:
    return f"{a['originalPath']}  {a.get('width')}x{a.get('height')}"


def cmd_report(args) -> int:
    verdicts = [classify(g) for g in _client().get_duplicates()]
    args.out.mkdir(parents=True, exist_ok=True)
    write_report(verdicts, args.out / "report.csv")
    s = summarize(verdicts)
    print(f"{len(verdicts)} duplicate groups")
    print(f"  tier A (uploaded copies can be trashed): {s['A']} groups, {s['trash']} uploaded photos")
    print(f"  tier B (left for you): {s['B']} groups")
    for k in sorted(k for k in s if k.startswith("B:")):
        print(f"      {k[2:]:20} {s[k]}")
    print("\nexamples, tier A (external first, then the uploaded copy that would be trashed):")
    for v in [v for v in verdicts if v.tier == "A"][:3]:
        print("  keep :", _show(v.external))
        for u in v.trash:
            print("  trash:", _show(u))
    for reason in sorted({v.reason for v in verdicts if v.tier == "B"}):
        print(f"\nexamples, B / {reason}:")
        for v in [v for v in verdicts if v.reason == reason][:2]:
            for a in v.assets:
                print("  ", _show(a))
            print("   --")
    print(f"\nfull list: {args.out / 'report.csv'}")
    return 0


def cmd_apply(args) -> int:
    verdicts = [classify(g) for g in _client().get_duplicates()]
    todo = [v for v in verdicts if v.tier == "A"]
    if args.limit is not None:
        todo = todo[:args.limit]
    n = sum(len(v.trash) for v in todo)
    if args.yes_trash != n:
        print(f"This would move {n} uploaded photos ({len(todo)} groups) to the trash. "
              f"Confirm by passing --yes-trash {n}", file=sys.stderr)
        return 2
    done = apply_verdicts(_client(), todo, args.log)
    print(f"{done} uploaded photos moved to the trash. Log: {args.log}")
    print(f"To put them back: dedupe.py undo --log {args.log}")
    return 0


def cmd_undo(args) -> int:
    print(f"{undo_trash(_client(), args.log)} photos restored from the trash")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("apply")
    a.add_argument("--log", type=Path, required=True)
    a.add_argument("--limit", type=int)
    a.add_argument("--yes-trash", type=int, required=True, dest="yes_trash")
    u = sub.add_parser("undo")
    u.add_argument("--log", type=Path, required=True)
    args = ap.parse_args(argv[1:])
    return {"report": cmd_report, "apply": cmd_apply, "undo": cmd_undo}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
