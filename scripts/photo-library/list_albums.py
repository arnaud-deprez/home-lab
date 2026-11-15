#!/usr/bin/env python3
"""Print the albums of a phase and any name collisions: list_albums.py <phase>"""
import sys
from pathlib import Path

from photolib.config import load_config
from photolib.scope import find_collisions, iter_albums


def main(argv) -> int:
    phase = int(argv[1])
    cfg = load_config(Path(__file__).with_name("config.json"))
    albums = iter_albums(cfg, phase)
    for a in albums:
        print(f"{a.library}\t{a.name}\t{a.year or '-'}")
    print("COLLISIONS:")
    for name, items in find_collisions(albums).items():
        print(f"  {name}: " + ", ".join(str(i.path) for i in items))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
