import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from photolib.exif import read_dates
from tests.helpers import make_jpeg


class ReadDatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_date_time_original(self):
        p = make_jpeg(self.root / "a.jpg", "2009:08:01 10:00:00")
        self.assertEqual(read_dates([p])[p].exif, datetime(2009, 8, 1, 10, 0, 0))

    def test_zero_date_is_none(self):
        p = make_jpeg(self.root / "z.jpg", zero=True)
        self.assertIsNone(read_dates([p])[p].exif)

    def test_no_exif_is_none_and_unreadable_does_not_abort(self):
        good = make_jpeg(self.root / "n.jpg")
        bad = self.root / "bad.jpg"
        bad.write_bytes(b"not an image")
        res = read_dates([good, bad])
        self.assertIsNone(res[good].exif)
        self.assertIsNone(res[bad].exif)

    def test_avi_is_not_writable_and_jpg_is(self):
        avi = self.root / "v.avi"
        avi.write_bytes(b"RIFF")
        jpg = make_jpeg(self.root / "b.jpg")
        res = read_dates([avi, jpg])
        self.assertFalse(res[avi].writable)
        self.assertTrue(res[jpg].writable)

    def test_accented_and_special_filenames(self):
        p = make_jpeg(self.root / "soirée & (x) 'y'.jpg", "2008:01:02 03:04:05")
        self.assertEqual(read_dates([p])[p].exif, datetime(2008, 1, 2, 3, 4, 5))


if __name__ == "__main__":
    unittest.main()
