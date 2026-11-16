"""exiftool wrapper: read capture dates (write comes in the apply task)."""
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

NOT_WRITABLE = {".avi", ".mpg", ".mpeg", ".mts", ".m2ts", ".bmp", ".wmv"}
_TAGS = ("DateTimeOriginal", "CreateDate", "MediaCreateDate")
_BATCH = 200


@dataclass
class ExifInfo:
    exif: Optional[datetime]
    writable: bool


def _parse(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None  # "0000:00:00 00:00:00" and other garbage


def _run_batch(paths: list) -> dict:
    cmd = ["exiftool", "-json", "-fast2", "-q", "-m", "-charset", "filename=UTF8",
           "-d", "%Y-%m-%dT%H:%M:%S"] + [f"-{t}" for t in _TAGS] + [str(p) for p in paths]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        rows = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        rows = []
    return {row["SourceFile"]: row for row in rows}


def read_dates(paths: list) -> dict:
    result = {}
    for i in range(0, len(paths), _BATCH):
        batch = paths[i:i + _BATCH]
        rows = _run_batch(batch)
        for p in batch:
            row = rows.get(str(p), {})
            exif = next((d for d in (_parse(row.get(t)) for t in _TAGS) if d), None)
            result[p] = ExifInfo(exif, Path(p).suffix.lower() not in NOT_WRITABLE)
    return result


VIDEO_EXT = {".mov", ".mp4", ".m4v", ".3gp"}
RAW_TAGS = ("DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate")
_WRITE_COMMON = ["-P", "-overwrite_original", "-m", "-q", "-api", "QuickTimeUTC=1",
                 "-charset", "filename=UTF8"]


def tags_for(path: Path) -> tuple:
    if Path(path).suffix.lower() in VIDEO_EXT:
        return ("CreateDate", "MediaCreateDate", "TrackCreateDate")
    return ("DateTimeOriginal", "CreateDate")


def read_raw(paths: list) -> dict:
    """Raw tag strings as stored (for the undo log); missing tags are absent."""
    result = {}
    for i in range(0, len(paths), _BATCH):
        batch = paths[i:i + _BATCH]
        cmd = (["exiftool", "-json", "-q", "-m", "-api", "QuickTimeUTC=1", "-charset", "filename=UTF8"]
               + [f"-{t}" for t in RAW_TAGS] + [str(p) for p in batch])
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        rows = {r["SourceFile"]: r for r in (json.loads(out) if out.strip() else [])}
        for p in batch:
            row = rows.get(str(p), {})
            result[p] = {t: str(row[t]) for t in RAW_TAGS if t in row}
    return result


def set_tags(settings: dict) -> None:
    """settings: path -> {tag: value or None (delete)}. One exiftool run per batch via an argfile."""
    import os
    import tempfile
    items = list(settings.items())
    for i in range(0, len(items), _BATCH):
        lines = []
        for p, tags in items[i:i + _BATCH]:
            for t, v in tags.items():
                lines.append(f"-{t}={'' if v is None else v}")
            lines += [str(p), "-execute"]
        with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        try:
            subprocess.run(["exiftool", "-charset", "exif=UTF8", "-@", fh.name, "-common_args"]
                           + _WRITE_COMMON, capture_output=True, text=True)
        finally:
            os.unlink(fh.name)


def rebuild_exif(paths: list) -> None:
    """Copy every tag into a freshly built metadata block (drops damaged directories such as a truncated
    InteropIFD that make exiftool refuse to write). The image data is not touched."""
    for p in paths:
        subprocess.run(["exiftool", *_WRITE_COMMON, "-all=", "-tagsfromfile", "@", "-all:all", "-unsafe",
                        "-icc_profile", str(p)], capture_output=True, text=True)


def write_dates(changes: dict) -> list:
    """changes: path -> new datetime. Returns the paths whose date did not read back as written.
    A file that refuses the write gets its metadata block rebuilt once, then the write is retried."""
    fmt = "%Y:%m:%d %H:%M:%S"

    def attempt(items: dict) -> list:
        set_tags({p: {t: dt.strftime(fmt) for t in tags_for(p)} for p, dt in items.items()})
        back = read_dates(list(items))
        return [p for p, dt in items.items() if back[p].exif != dt]

    failed = attempt(changes)
    if failed:
        rebuild_exif(failed)
        failed = attempt({p: changes[p] for p in failed})
    return failed


def image_hashes(paths: list) -> dict:
    """MD5 of the image data only (metadata excluded): two files with the same picture but different EXIF match."""
    result = {}
    for i in range(0, len(paths), _BATCH):
        batch = paths[i:i + _BATCH]
        cmd = ["exiftool", "-json", "-q", "-m", "-charset", "filename=UTF8", "-api", "ImageHashType=MD5",
               "-ImageDataHash"] + [str(p) for p in batch]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        try:
            rows = {r["SourceFile"]: r for r in (json.loads(out) if out.strip() else [])}
        except json.JSONDecodeError:
            rows = {}
        for p in batch:
            result[p] = rows.get(str(p), {}).get("ImageDataHash")
    return result
