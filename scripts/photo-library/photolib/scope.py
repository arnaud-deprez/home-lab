"""Which folders are albums, and what year they belong to."""
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config, Library

_YEAR = re.compile(r"^((?:19|20)\d\d) ")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@dataclass
class Album:
    library: str
    name: str  # NFC
    path: Path  # real path on disk (may be NFD)
    year: Optional[int]


def folder_year(name: str) -> Optional[int]:
    m = _YEAR.match(nfc(name))
    return int(m.group(1)) if m else None


def _subdirs(path: Path):
    return sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: nfc(p.name))


def _album_dirs(root: Path, lib: Library):
    base = root / lib.name
    excluded = {nfc(x) for x in lib.exclude}
    if lib.album_depth == "child":
        return [d for d in _subdirs(base) if nfc(d.name) not in excluded]
    included = None if lib.include is None else {nfc(x) for x in lib.include}
    out = []
    for period in _subdirs(base):
        if included is not None and nfc(period.name) not in included:
            continue
        out.extend(d for d in _subdirs(period) if nfc(d.name) not in excluded)
    return out


def iter_albums(cfg: Config, phase: int) -> list:
    by_name = {lib.name: lib for lib in cfg.libraries}
    albums = []
    for lib_name in cfg.phases[phase]:
        for d in _album_dirs(cfg.root, by_name[lib_name]):
            albums.append(Album(lib_name, nfc(d.name), d, folder_year(d.name)))
    return albums


def find_collisions(albums: list) -> dict:
    seen: dict = {}
    for a in albums:
        seen.setdefault(a.name, []).append(a)
    return {k: v for k, v in seen.items() if len(v) > 1}
