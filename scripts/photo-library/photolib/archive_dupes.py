"""Remove, from the photo archive on the Mac, copies that are redundant for sure, in a reversible way.

Input is the report of dedupe.py (groups of several external photos). A group is actionable only if
  - all its photos have the same pixels (identical): keep the best named one, or
  - it is a pair, same aspect ratio, the smaller one at most half the size of the other (a thumbnail).
Resized copies (80%) and different pictures (burst shots) are left alone."""
import csv
import hashlib
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .exif import image_hashes

EXTERNAL_PREFIX = "/mnt/rclone/external/"
_FACEBOOK = re.compile(r"^(n?\d+_\d+_\d+(_\d+)*(_[a-z])?\.|\d+_\d+_\d+_\d+_\d+_[a-z]\.)", re.I)
_CAMERA = re.compile(r"^(DSCN?_?|IMG_|P\d{6,}|PICT|DCP_|PA\d)", re.I)
THUMBNAIL_MAX_LINEAR = 0.5
ASPECT_TOLERANCE = 0.01


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@dataclass
class Info:
    path: str            # relative to the photo root, NFC
    hash: object         # str or None (hash of the image data)
    width: int
    height: int
    size: int = 0        # file size in bytes
    sha1: object = None  # hash of the whole file, only computed for files without an image hash


@dataclass
class Decision:
    reason: str
    keep: str = ""
    remove: list = None

    def __post_init__(self):
        self.remove = self.remove or []


def keep_score(path: str) -> int:
    """Higher = better to keep: camera name, dated event folder; Facebook-style names and 'vrac' folders lose."""
    parts = _nfc(path).split("/")
    name, folders = parts[-1], parts[1:-1]
    score = 0
    if _FACEBOOK.match(name):
        score -= 10
    if _CAMERA.match(name):
        score += 3
    low = [f.lower() for f in folders]
    if any("vrac" in f or "zzz" in f or "cewe" in f or "copie" in f or "copy" in f for f in low):
        score -= 5
    if any(f == "print" or f.startswith("print ") for f in low):
        score -= 2   # exports for printing
    if any("triée" in f or "triee" in f for f in low):
        score += 4   # hand-curated sub-folder
    return score


def decide(infos: list, include_resized: bool = False, include_leftovers: bool = False) -> Decision:
    """include_resized: also remove the smaller of two copies of the same picture at another size (same aspect ratio),
    even without hashes (videos). The larger one is the better quality and is kept."""
    include_resized = include_resized or include_leftovers   # leftovers: for pairs the user has checked by hand
    hashed = all(i.hash is not None for i in infos)
    if not hashed and not include_resized:
        return Decision("no-hash")
    if hashed and len({i.hash for i in infos}) == 1:
        ordered = sorted(infos, key=lambda i: (-keep_score(i.path), i.path))
        return Decision("identical", ordered[0].path, [i.path for i in ordered[1:]])
    if len(infos) == 2:
        small, big = sorted(infos, key=lambda i: i.width * i.height)
        aspect = abs(small.width / small.height - big.width / big.height) / (big.width / big.height)
        linear = ((small.width * small.height) / (big.width * big.height)) ** 0.5
        if aspect <= ASPECT_TOLERANCE and linear <= THUMBNAIL_MAX_LINEAR:
            return Decision("much-smaller", big.path, [small.path])
        if aspect <= ASPECT_TOLERANCE and linear < 0.95:
            if include_resized:
                return Decision("resized-copy", big.path, [small.path])
            return Decision("resized-copy")
        if include_leftovers and aspect <= ASPECT_TOLERANCE:
            a, b = infos
            if a.sha1 and a.sha1 == b.sha1:
                ordered = sorted(infos, key=lambda i: (-keep_score(i.path), i.path))
                return Decision("identical-bytes", ordered[0].path, [ordered[1].path])
            ordered = sorted(infos, key=lambda i: (-(i.width * i.height), -i.size, -keep_score(i.path), i.path))
            return Decision("larger-file", ordered[0].path, [ordered[1].path])
    return Decision("different-pictures" if hashed else "no-hash")


_index_cache: dict = {}


def local_path(root: Path, rel_nfc: str) -> Path:
    """The real file for an NFC relative path (macOS may store the names decomposed)."""
    key = str(root)
    if key not in _index_cache or _nfc(rel_nfc) not in _index_cache[key]:
        idx = {}
        for r, _, fs in os.walk(root):
            for f in fs:
                full = Path(r) / f
                idx[_nfc(str(full.relative_to(root)))] = full
        _index_cache[key] = idx
    return _index_cache[key][_nfc(rel_nfc)]


def _parse_report(report_csv: Path) -> list:
    groups = []
    with open(report_csv, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["reason"] != "multi-external":
                continue
            photos = []
            for item in r["photos"].split(" | "):
                path, _, wh = item.rpartition(" ")
                if not path.startswith(EXTERNAL_PREFIX):
                    continue   # an uploaded copy in the same group: dedupe.py handles it, not the archive
                w, h = (int(x) for x in wh.split("x"))
                photos.append((_nfc(path[len(EXTERNAL_PREFIX):]), w, h))
            if len(photos) > 1:
                groups.append((r["group"], photos))
    return groups


PLAN_COLUMNS = ["group", "reason", "keep", "remove", "keep_bytes", "remove_bytes"]


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def size_of_rows(root: Path, rel_nfc: str) -> int:
    return local_path(root, rel_nfc).stat().st_size


def plan_rows(root: Path, report_csv: Path, include_resized: bool = False, include_leftovers: bool = False) -> list:
    _index_cache.pop(str(root), None)
    groups = _parse_report(report_csv)
    paths, rows = {}, []
    for gid, photos in list(groups):
        try:
            paths.update({rel: local_path(root, rel) for rel, _, _ in photos})
        except KeyError:  # a file of the group is not in the photo root any more (moved by an earlier run)
            rows.append({"group": gid, "reason": "missing-file", "keep": "", "remove": "", "keep_bytes": "", "remove_bytes": ""})
            groups.remove((gid, photos))
    hashes = image_hashes(list(paths.values()))
    size = lambda rel: paths[rel].stat().st_size  # noqa: E731
    for gid, photos in groups:
        infos = []
        for rel, w, h in photos:
            sha = _sha1(paths[rel]) if include_leftovers and hashes.get(paths[rel]) is None else None
            infos.append(Info(rel, hashes.get(paths[rel]), w, h, size(rel), sha))
        d = decide(infos, include_resized, include_leftovers)
        if d.remove:
            rows.extend({"group": gid, "reason": d.reason, "keep": d.keep, "remove": r,
                         "keep_bytes": size(d.keep), "remove_bytes": size(r)} for r in d.remove)
        else:
            rows.append({"group": gid, "reason": d.reason, "keep": "", "remove": "", "keep_bytes": "", "remove_bytes": ""})
    return rows


def apply_plan(root: Path, rows: list, quarantine: Path, out: Path) -> int:
    """Move the 'remove' files into the quarantine (same sub-folders). Re-checks the kept file first."""
    _index_cache.pop(str(root), None)
    moved = 0
    todo = [r for r in rows if r["remove"]]
    for r in todo:
        try:
            keep, gone = local_path(root, r["keep"]), local_path(root, r["remove"])
        except KeyError:
            continue
        if r["reason"] == "identical":
            h = image_hashes([keep, gone])
            if h[keep] is None or h[keep] != h[gone]:
                continue
        elif r["reason"] == "identical-bytes":
            if _sha1(keep) != _sha1(gone):
                continue
        elif r["reason"] == "larger-file" and keep.stat().st_size < gone.stat().st_size:
            continue
        rel = gone.relative_to(root)
        dest = quarantine / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        new_log = not (out / "moves.csv").exists()
        with open(out / "moves.csv", "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new_log:
                w.writerow(["original", "quarantine", "rel_nfc"])
            w.writerow([str(gone), str(dest), _nfc(str(rel))])
            fh.flush()
            os.fsync(fh.fileno())
        shutil.move(str(gone), str(dest))
        with open(out / "bucket-removals.txt", "a", encoding="utf-8") as fh:
            fh.write(_nfc(str(rel)) + "\n")
        moved += 1
    return moved


def undo_moves(root: Path, moves_csv: Path) -> int:
    restored = 0
    with open(moves_csv, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            src, dst = Path(r["quarantine"]), Path(r["original"])
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                restored += 1
    return restored
