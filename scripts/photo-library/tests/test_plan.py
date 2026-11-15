import csv
import hashlib
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fix_dates import build_plan
from photolib.config import Config, Library
from tests.helpers import make_jpeg

NOW = datetime(2026, 11, 15)


def set_mtime(p: Path, dt: datetime):
    ts = dt.timestamp()
    os.utime(p, (ts, ts))


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Photos"
        a = self.root / "Shared" / "2009 - event"
        self.keep = make_jpeg(a / "a.jpg", "2009:08:01 10:00:00")
        self.fromm = make_jpeg(a / "b.jpg", "2001:01:01 00:00:00")
        set_mtime(self.fromm, datetime(2009, 7, 3, 9))
        self.zero = make_jpeg(a / "c.jpg", zero=True)
        set_mtime(self.zero, datetime(1970, 1, 2))
        self.avi = a / "d.avi"
        self.avi.write_bytes(b"RIFF")
        set_mtime(self.avi, datetime(2009, 5, 5))
        (a / ".DS_Store").write_bytes(b"x")
        self.noyear = make_jpeg(self.root / "Shared" / "Padova" / "e.jpg")
        self.cfg = Config(self.root, [Library("Shared", "child", None, [])],
                          [".DS_Store"], {1: ["Shared"]})
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return {p: (hashlib.sha1(p.read_bytes()).hexdigest(), p.stat().st_mtime)
                for p in self.root.rglob("*") if p.is_file()}

    def test_plan_writes_csv_with_expected_rules_and_changes_nothing(self):
        before = self.snapshot()
        counts = build_plan(self.cfg, 1, self.out, NOW)
        self.assertEqual(before, self.snapshot())
        rows = {Path(r["path"]).name: r for r in csv.DictReader(open(self.out / "plan.csv", encoding="utf-8"))}
        self.assertEqual(rows["a.jpg"]["rule"], "keep-exif")
        self.assertEqual(rows["b.jpg"]["rule"], "from-mtime")
        self.assertEqual(rows["b.jpg"]["new_date"], "2009-07-03T09:00:00")
        self.assertEqual(rows["c.jpg"]["rule"], "jan1-seq")
        self.assertEqual(rows["d.avi"]["writable"], "False")
        self.assertEqual(rows["e.jpg"]["rule"], "skip-no-year")
        self.assertNotIn(".DS_Store", rows)
        self.assertEqual(counts[("Shared", "keep-exif")], 1)

    def test_many_files_sharing_a_wrong_mtime_day_fall_back_to_jan1_in_filename_order(self):
        album = self.root / "Shared" / "2005 - placeholder"
        for i in range(25):
            f = make_jpeg(album / f"img{i:02d}.jpg", zero=True)
            set_mtime(f, datetime(2001, 12, 31, 22))
        build_plan(self.cfg, 1, self.out, NOW)
        rows = {Path(r["path"]).name: r for r in csv.DictReader(open(self.out / "plan.csv", encoding="utf-8"))}
        self.assertEqual(rows["img00.jpg"]["rule"], "jan1-seq")
        self.assertEqual(rows["img00.jpg"]["new_date"], "2005-01-01T12:00:00")
        self.assertEqual(rows["img24.jpg"]["new_date"], "2005-01-01T12:00:24")


if __name__ == "__main__":
    unittest.main()
