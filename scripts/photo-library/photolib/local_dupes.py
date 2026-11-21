"""Find exact duplicates inside the photo archive on the Mac, before anything is copied to the bucket.

Two files are duplicates when their image data has the same hash (same picture, metadata may differ) or, for
files without an image hash (videos), when their bytes are identical. Resized copies are not found here: that
needs Immich's perceptual duplicate detection (dedupe.py + archive_dupes.py plan --resized)."""
import fnmatch
import os
from collections import defaultdict
from pathlib import Path

from .archive_dupes import Decision, Info, _nfc, _sha1, decide, keep_score, size_of_rows
from .exif import image_hashes


def _files(root: Path, libraries: list, ignore_globs: list) -> list:
    out = []
    for lib in libraries:
        for r, _, fs in os.walk(root / lib):
            for f in fs:
                if not any(fnmatch.fnmatch(f.casefold(), g.casefold()) for g in ignore_globs):
                    out.append(Path(r) / f)
    return sorted(out)


def scan_local(root: Path, libraries: list, ignore_globs: list) -> list:
    """Plan rows (same columns as archive_dupes.PLAN_COLUMNS) for every group of exact duplicates."""
    files = _files(root, libraries, ignore_globs)
    rel = {p: _nfc(str(p.relative_to(root))) for p in files}
    hashes = image_hashes(files)
    by_hash, unhashed = defaultdict(list), defaultdict(list)
    for p in files:
        if hashes.get(p):
            by_hash[hashes[p]].append(p)
        else:
            unhashed[p.stat().st_size].append(p)
    rows, n = [], 0

    def emit(d: Decision):
        nonlocal n
        n += 1
        rows.extend({"group": f"local-{n}", "reason": d.reason, "keep": d.keep, "remove": r,
                     "keep_bytes": size_of_rows(root, d.keep), "remove_bytes": size_of_rows(root, r)}
                    for r in d.remove)

    for group in by_hash.values():
        if len(group) > 1:
            emit(decide([Info(rel[p], hashes[p], 0, 0) for p in group]))
    for group in unhashed.values():
        if len(group) < 2:
            continue
        by_sha = defaultdict(list)
        for p in group:
            by_sha[_sha1(p)].append(p)
        for same in by_sha.values():
            if len(same) > 1:
                ordered = sorted((rel[p] for p in same), key=lambda r: (-keep_score(r), r))
                emit(Decision("identical-bytes", ordered[0], ordered[1:]))
    return rows


def split_by_album(rows: list, depth: dict) -> tuple:
    """(same, cross): rows whose kept and removed files are in the same album folder, and the others.
    depth: library -> number of path parts that name the album (Amelie: period/event = 3 with the library)."""
    def album(path: str) -> tuple:
        parts = path.split("/")
        return tuple(parts[:depth.get(parts[0], 2)])

    same = [r for r in rows if album(r["keep"]) == album(r["remove"])]
    return same, [r for r in rows if album(r["keep"]) != album(r["remove"])]
