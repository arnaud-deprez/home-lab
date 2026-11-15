"""Scope configuration for the photo library tooling."""
import json
from dataclasses import dataclass
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
    )
