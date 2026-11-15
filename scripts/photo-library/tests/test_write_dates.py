import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from photolib import exif
from photolib.exif import ExifInfo

P = Path("/x/a.jpg")
NEW = datetime(2005, 10, 1, 12, 0, 1)


class WriteDatesRetryTest(unittest.TestCase):
    """The control flow of the repair: a file that refuses the write is rebuilt once, then retried."""

    def run_write(self, reads):
        calls = []
        with mock.patch.object(exif, "set_tags", side_effect=lambda s: calls.append(("set", list(s)))), \
             mock.patch.object(exif, "rebuild_exif", side_effect=lambda ps: calls.append(("rebuild", list(ps)))), \
             mock.patch.object(exif, "read_dates", side_effect=reads):
            failed = exif.write_dates({P: NEW})
        return failed, calls

    def test_file_that_refuses_the_write_is_rebuilt_then_written(self):
        failed, calls = self.run_write([{P: ExifInfo(None, True)}, {P: ExifInfo(NEW, True)}])
        self.assertEqual(failed, [])
        self.assertEqual([c[0] for c in calls], ["set", "rebuild", "set"])

    def test_file_that_still_fails_after_the_rebuild_is_reported(self):
        failed, calls = self.run_write([{P: ExifInfo(None, True)}, {P: ExifInfo(None, True)}])
        self.assertEqual(failed, [P])
        self.assertEqual([c[0] for c in calls].count("rebuild"), 1)

    def test_file_that_writes_fine_is_never_rebuilt(self):
        failed, calls = self.run_write([{P: ExifInfo(NEW, True)}])
        self.assertEqual((failed, [c[0] for c in calls]), ([], ["set"]))


if __name__ == "__main__":
    unittest.main()
