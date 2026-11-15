import base64
import subprocess
from pathlib import Path

TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////"
    "////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)


def make_jpeg(path: Path, date: str = None, zero: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TINY_JPEG)
    if date:
        subprocess.run(["exiftool", "-q", "-overwrite_original",
                        f"-DateTimeOriginal={date}", str(path)], check=True)
    if zero:
        subprocess.run(["exiftool", "-q", "-overwrite_original",
                        "-DateTimeOriginal=2009:08:01 10:00:00", str(path)], check=True)
        path.write_bytes(path.read_bytes().replace(b"2009:08:01 10:00:00", b"0000:00:00 00:00:00"))
    return path


def make_mov(path: Path, date: str = None) -> Path:
    import struct
    path.parent.mkdir(parents=True, exist_ok=True)

    def box(t, b):
        return struct.pack(">I4s", 8 + len(b), t) + b

    mvhd = box(b"mvhd", struct.pack(">B3xIIII", 0, 0, 0, 600, 0)
               + struct.pack(">IH10x", 0x00010000, 0x0100)
               + struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
               + b"\0" * 24 + struct.pack(">I", 2))
    path.write_bytes(box(b"ftyp", b"qt  \0\0\0\0qt  ") + box(b"moov", mvhd))
    if date:
        subprocess.run(["exiftool", "-q", "-m", "-overwrite_original", f"-CreateDate={date}", str(path)], check=True)
    return path


def read_csv(path):
    import csv
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
