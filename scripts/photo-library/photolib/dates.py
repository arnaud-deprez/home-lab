"""The date decision rule (pure, no I/O)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class Decision:
    date: Optional[datetime]
    rule: str  # keep-exif | from-mtime | mtime-day-folder-year | jan1-seq | skip-no-year


def _near(year: int, folder_year: int) -> bool:
    return abs(year - folder_year) <= 1


def choose_date(exif: Optional[datetime], mtime: datetime, folder_year: Optional[int],
                seq: int, now: datetime) -> Decision:
    if folder_year is None:
        return Decision(None, "skip-no-year")
    if exif is not None and _near(exif.year, folder_year):
        return Decision(exif, "keep-exif")
    if _near(mtime.year, folder_year):
        return Decision(mtime, "from-mtime")
    if 1990 <= mtime.year <= now.year:
        try:
            moved = mtime.replace(year=folder_year)
        except ValueError:  # 29 February onto a non-leap year
            moved = mtime.replace(year=folder_year, day=28)
        return Decision(moved, "mtime-day-folder-year")
    return Decision(datetime(folder_year, 1, 1, 12) + timedelta(seconds=seq), "jan1-seq")
