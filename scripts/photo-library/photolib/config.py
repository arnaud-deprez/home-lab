"""Scope configuration for the photo library tooling."""
import json
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional


@dataclass
class Library:
    name: str
    album_depth: Literal["child", "grandchild"]
    include: Optional[list]
    exclude: list


@dataclass
class Config:
    root: Path
    libraries: list
    ignore_globs: list
    phases: dict
    date_overrides: dict = field(default_factory=dict)  # "Library/album name" (NFC) -> "YYYY-MM[-DD]"


def parse_override(value: str) -> datetime:
    """'2007-08' -> 1 Aug 2007 12:00; '2007-08-15' -> 15 Aug 2007 12:00."""
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(value, fmt).replace(hour=12)
        except ValueError:
            pass
    raise ValueError(f"bad date override {value!r}: use YYYY-MM or YYYY-MM-DD")


def load_config(path: Path) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Config(
        root=Path(raw["root"]),
        libraries=[
            Library(l["name"], l["album_depth"], l.get("include"), l.get("exclude", []))
            for l in raw["libraries"]
        ],
        ignore_globs=raw["ignore_globs"],
        phases={int(k): v for k, v in raw["phases"].items()},
        date_overrides={unicodedata.normalize("NFC", k): v for k, v in raw.get("date_overrides", {}).items()},
    )
