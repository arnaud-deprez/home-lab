import tempfile
import unittest
from pathlib import Path

from photolib.local_dupes import scan_local
from tests.helpers import make_valid_jpeg


class ScanLocalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def jpg(self, rel, shade=128):
        return make_valid_jpeg(self.root / rel, shade)

    def test_same_file_in_two_folders_is_one_identical_group_keeping_the_best_name(self):
        self.jpg("Amelie/p/2017 - Wedding/Photos Triées/IMG_0001.jpg")
        self.jpg("Amelie/p/2017 - Wedding/Wedding Phone/copy of it.jpg")
        rows = scan_local(self.root, ["Amelie"], ignore_globs=[])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["reason"], r["keep"]), ("identical", "Amelie/p/2017 - Wedding/Photos Triées/IMG_0001.jpg"))
        self.assertEqual(r["remove"], "Amelie/p/2017 - Wedding/Wedding Phone/copy of it.jpg")

    def test_different_pictures_are_not_grouped(self):
        self.jpg("Amelie/p/e/a.jpg", 50)
        self.jpg("Amelie/p/e/b.jpg", 200)
        self.assertEqual(scan_local(self.root, ["Amelie"], ignore_globs=[]), [])

    def test_ignored_files_are_skipped(self):
        self.jpg("Amelie/p/e/a.jpg")
        self.jpg("Amelie/p/e/b.jpg")
        self.assertEqual(scan_local(self.root, ["Amelie"], ignore_globs=["b.jpg"]), [])

    def test_byte_identical_videos_are_found_by_sha1(self):
        for rel in ("Amelie/p/e/v1.mp4", "Amelie/p/e/v2.mp4"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(b"not really a video" * 10)
        (self.root / "Amelie/p/e/v3.mp4").write_bytes(b"other content" * 10)
        rows = scan_local(self.root, ["Amelie"], ignore_globs=[])
        self.assertEqual([r["reason"] for r in rows], ["identical-bytes"])
        self.assertEqual({rows[0]["keep"], rows[0]["remove"]}, {"Amelie/p/e/v1.mp4", "Amelie/p/e/v2.mp4"})

    def test_only_the_requested_libraries_are_scanned(self):
        self.jpg("Amelie/p/e/a.jpg")
        self.jpg("Shared/2010 - x/a.jpg")
        self.assertEqual(scan_local(self.root, ["Amelie"], ignore_globs=[]), [])
        self.assertEqual(len(scan_local(self.root, ["Amelie", "Shared"], ignore_globs=[])), 1)


if __name__ == "__main__":
    unittest.main()
