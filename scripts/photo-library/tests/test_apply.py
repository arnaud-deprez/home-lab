import csv
import hashlib
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fix_dates import PLAN_COLUMNS, apply_plan, undo_changes
from photolib.exif import read_dates
from tests.helpers import make_jpeg, read_csv, make_mov


def set_mtime(p: Path, dt: datetime):
    os.utime(p, (dt.timestamp(), dt.timestamp()))


def write_plan(path: Path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, PLAN_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({"old_exif": "", "mtime": "", **r})


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jpg = make_jpeg(self.root / "soirée (1).jpg", "2001:01:01 00:00:00")
        set_mtime(self.jpg, datetime(2009, 7, 3, 9))
        self.keep = make_jpeg(self.root / "keep.jpg", "2009:08:01 10:00:00")
        self.avi = self.root / "v.avi"
        self.avi.write_bytes(b"RIFF....")
        self.mov = make_mov(self.root / "m.mov")
        set_mtime(self.mov, datetime(2009, 7, 4, 9))
        self.plan = self.root / "plan.csv"
        self.undo = self.root / "undo.csv"
        write_plan(self.plan, [
            {"path": self.jpg, "new_date": "2009-07-03T09:00:00", "rule": "from-mtime", "writable": True},
            {"path": self.keep, "new_date": "2009-08-01T10:00:00", "rule": "keep-exif", "writable": True},
            {"path": self.avi, "new_date": "2009-05-05T00:00:00", "rule": "from-mtime", "writable": False},
            {"path": self.mov, "new_date": "2009-07-04T09:00:00", "rule": "from-mtime", "writable": True},
        ])
        os.utime(self.plan, (datetime(2030, 1, 1).timestamp(),) * 2)  # plan newer than the files

    def tearDown(self):
        self.tmp.cleanup()

    def sha(self, p):
        return hashlib.sha1(p.read_bytes()).hexdigest()

    def test_apply_writes_dates_keeps_mtime_and_skips_keep_and_unwritable(self):
        keep_sha, avi_sha = self.sha(self.keep), self.sha(self.avi)
        failures = apply_plan(self.plan, self.undo)
        self.assertEqual(failures, [])
        self.assertEqual(read_dates([self.jpg])[self.jpg].exif, datetime(2009, 7, 3, 9))
        self.assertEqual(read_dates([self.mov])[self.mov].exif, datetime(2009, 7, 4, 9))
        self.assertEqual(datetime.fromtimestamp(self.jpg.stat().st_mtime), datetime(2009, 7, 3, 9))
        self.assertEqual(datetime.fromtimestamp(self.mov.stat().st_mtime), datetime(2009, 7, 4, 9))
        self.assertEqual((self.sha(self.keep), self.sha(self.avi)), (keep_sha, avi_sha))

    def test_undo_log_is_written_with_old_values_and_undo_restores_them(self):
        apply_plan(self.plan, self.undo)
        rows = {Path(r["path"]).name: r for r in read_csv(self.undo)}
        self.assertEqual(rows["soirée (1).jpg"]["DateTimeOriginal"], "2001:01:01 00:00:00")
        self.assertNotIn("keep.jpg", rows)
        undo_changes(self.undo)
        self.assertEqual(read_dates([self.jpg])[self.jpg].exif, datetime(2001, 1, 1))
        self.assertEqual(datetime.fromtimestamp(self.jpg.stat().st_mtime), datetime(2009, 7, 3, 9))

    def test_undo_log_exists_before_any_write(self):
        bad_undo = self.root / "missing-dir" / "undo.csv"
        before = self.sha(self.jpg)
        with self.assertRaises(Exception):
            apply_plan(self.plan, bad_undo)
        self.assertEqual(self.sha(self.jpg), before)

    def test_unwritable_video_with_a_wrong_date_gets_its_modification_date_set_and_undone(self):
        clip = self.root / "clip.avi"
        clip.write_bytes(b"RIFF....")
        set_mtime(clip, datetime(2009, 2, 9, 21, 36, 28))
        write_plan(self.plan, [{"path": clip, "new_date": "2007-02-09T21:36:28",
                                "rule": "mtime-day-folder-year", "writable": False}])
        os.utime(self.plan, (datetime(2030, 1, 1).timestamp(),) * 2)
        before = self.sha(clip)
        self.assertEqual(apply_plan(self.plan, self.undo), [])
        self.assertEqual(datetime.fromtimestamp(clip.stat().st_mtime), datetime(2007, 2, 9, 21, 36, 28))
        self.assertEqual(self.sha(clip), before)
        undo_changes(self.undo)
        self.assertEqual(datetime.fromtimestamp(clip.stat().st_mtime), datetime(2009, 2, 9, 21, 36, 28))

    def test_stale_plan_is_refused(self):
        os.utime(self.plan, (datetime(2000, 1, 1).timestamp(),) * 2)
        before = self.sha(self.jpg)
        with self.assertRaises(RuntimeError):
            apply_plan(self.plan, self.undo)
        self.assertEqual(self.sha(self.jpg), before)


if __name__ == "__main__":
    unittest.main()
