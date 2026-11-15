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
