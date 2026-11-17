import csv
import tempfile
import unicodedata
import unittest
from pathlib import Path

from photolib.archive_dupes import (Info, apply_plan, decide, keep_score, local_path, plan_rows, undo_moves)
from tests.helpers import make_valid_jpeg as make_jpeg


def info(path, h="h1", w=604, hgt=453, size=0, sha1=None):
    return Info(path, h, w, hgt, size, sha1)


class ScoreTest(unittest.TestCase):
    def test_camera_name_beats_a_facebook_name(self):
        self.assertGreater(keep_score("Shared/2008 - x/DSCN2857.JPG"),
                           keep_score("Shared/2008 - x/n658828888_746169_1605.jpg"))
        self.assertGreater(keep_score("Shared/2011 - x/depart skip.jpg"),
                           keep_score("Shared/2011 - x/261432_2192725013427_1106196993_32620691_4602767_n.jpg"))

    def test_a_dated_event_folder_beats_vrac(self):
        self.assertGreater(keep_score("Arnaud/2006 - TU/DSCN2857.JPG"), keep_score("Arnaud/2006 - vrac/DSCN2857.JPG"))


class DecideTest(unittest.TestCase):
    def test_identical_pixels_keep_the_best_named_one(self):
        d = decide([info("Shared/a/n658828888_746169_1605.jpg"), info("Shared/a/depart skip.jpg")])
        self.assertEqual((d.reason, d.keep, d.remove), ("identical", "Shared/a/depart skip.jpg", ["Shared/a/n658828888_746169_1605.jpg"]))

    def test_identical_pair_in_two_albums_keeps_the_dated_one(self):
        d = decide([info("Arnaud/2006 - vrac/DSCN2857.JPG", w=960, hgt=1280), info("Arnaud/2006 - TU/DSCN2857.JPG", w=960, hgt=1280)])
        self.assertEqual(d.keep, "Arnaud/2006 - TU/DSCN2857.JPG")

    def test_group_of_three_identical_removes_two(self):
        d = decide([info("S/a/1.jpg"), info("S/a/2.jpg"), info("S/a/3.jpg")])
        self.assertEqual((d.reason, len(d.remove)), ("identical", 2))

    def test_much_smaller_same_aspect_copy_is_removed(self):
        d = decide([info("Shared/a/P7103686.JPG", "h1", 3072, 2304), info("Arnaud/b/6773_1_n.jpg", "h2", 604, 453)])
        self.assertEqual((d.reason, d.keep, d.remove), ("much-smaller", "Shared/a/P7103686.JPG", ["Arnaud/b/6773_1_n.jpg"]))

    def test_the_80_percent_resized_copy_is_left_alone(self):
        d = decide([info("S/a/P7240072.JPG", "h1", 3072, 1713), info("S/a/holzheim 295.jpg", "h2", 2457, 1370)])
        self.assertEqual((d.reason, d.remove), ("resized-copy", []))

    def test_resized_copy_is_removed_only_when_asked(self):
        pair = [info("S/a/P7240072.JPG", "h1", 3072, 1713), info("S/a/holzheim 295.jpg", "h2", 2457, 1370)]
        d = decide(pair, include_resized=True)
        self.assertEqual((d.reason, d.keep, d.remove), ("resized-copy", "S/a/P7240072.JPG", ["S/a/holzheim 295.jpg"]))

    def test_resized_rule_works_without_hashes_for_videos(self):
        pair = [info("S/a/big.mpg", None, 720, 576), info("S/a/small.mpg", None, 576, 461)]
        d = decide(pair, include_resized=True)
        self.assertEqual((d.reason, d.keep, d.remove), ("resized-copy", "S/a/big.mpg", ["S/a/small.mpg"]))

    def test_resized_rule_never_removes_burst_shots_or_other_aspect_ratios(self):
        burst = [info("S/a/DSC_0850.JPG", "h1", 6000, 4000), info("S/a/DSC_0851.JPG", "h2", 6000, 4000)]
        cropped = [info("S/a/a.jpg", "h1", 3000, 2000), info("S/a/b.jpg", "h2", 2000, 2000)]
        self.assertEqual(decide(burst, include_resized=True).remove, [])
        self.assertEqual(decide(cropped, include_resized=True).remove, [])

    def test_leftovers_identical_bytes_keep_the_best_named_copy(self):
        pair = [info("S/a/camp loup jf 2008 060.mpg", None, 640, 480, 100, "s1"), info("S/a/camp loup 2008 060.mpg", None, 640, 480, 100, "s1")]
        d = decide(pair, include_leftovers=True)
        self.assertEqual((d.reason, d.keep, d.remove), ("identical-bytes", "S/a/camp loup 2008 060.mpg", ["S/a/camp loup jf 2008 060.mpg"]))

    def test_leftovers_same_picture_at_another_compression_keeps_the_larger_file(self):
        pair = [info("S/a/x 027.jpg", "h1", 2816, 2112, 721689), info("S/a/jf 027.jpg", "h2", 2816, 2112, 2132780)]
        d = decide(pair, include_leftovers=True)
        self.assertEqual((d.reason, d.keep, d.remove), ("larger-file", "S/a/jf 027.jpg", ["S/a/x 027.jpg"]))

    def test_leftovers_other_aspect_ratio_is_still_skipped(self):
        pair = [info("S/a/a.jpg", "h1", 3000, 2000, 5), info("S/a/b.jpg", "h2", 2000, 2000, 9)]
        self.assertEqual(decide(pair, include_leftovers=True).remove, [])

    def test_without_the_leftovers_flag_same_size_different_pixels_stay(self):
        pair = [info("S/a/x.jpg", "h1", 2816, 2112, 1), info("S/a/y.jpg", "h2", 2816, 2112, 2)]
        self.assertEqual(decide(pair, include_resized=True).remove, [])

    def test_burst_shots_are_different_pictures(self):
        d = decide([info("S/a/DSC_0850.JPG", "h1", 6000, 4000), info("S/a/DSC_0851.JPG", "h2", 6000, 4000)])
        self.assertEqual((d.reason, d.remove), ("different-pictures", []))

    def test_unhashable_files_are_skipped(self):
        d = decide([info("S/a/v1.mpg", None), info("S/a/v2.mpg", None)])
        self.assertEqual((d.reason, d.remove), ("no-hash", []))
        d = decide([info("S/a/v1.mpg", None), info("S/a/v2.mpg", None)], include_resized=True)
        self.assertEqual((d.reason, d.remove), ("no-hash", []))   # same size: cannot tell which is better


class PlanApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root, self.quar, self.out = t / "Photos", t / "quarantine", t / "out"
        self.out.mkdir()
        self.keep = make_jpeg(self.root / "Shared" / "2008 - é" / "DSCN0001.JPG", date="2008:01:01 10:00:00")
        self.dupe = make_jpeg(self.root / "Shared" / "2008 - é" / "n1_2_3_n.jpg", date="2009:09:09 09:09:09")  # other EXIF, same pixels

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, *photos):
        p = self.out / "report.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["group", "tier", "reason", "count", "photos"])
            w.writerow(["g1", "B", "multi-external", len(photos), " | ".join(f"/mnt/rclone/external/{x} 1x1" for x in photos)])
        return p

    def test_local_path_finds_nfd_named_files_from_nfc_report_paths(self):
        nfd_dir = unicodedata.normalize("NFD", "2010 - é")
        real = make_jpeg(self.root / "Shared" / nfd_dir / "x.jpg")
        self.assertEqual(local_path(self.root, unicodedata.normalize("NFC", "Shared/2010 - é/x.jpg")), real)

    def test_plan_finds_identical_pixels_even_when_the_exif_differs(self):
        rows = plan_rows(self.root, self.report("Shared/2008 - é/DSCN0001.JPG", "Shared/2008 - é/n1_2_3_n.jpg"))
        self.assertEqual([(r["reason"], r["keep"], r["remove"]) for r in rows],
                         [("identical", "Shared/2008 - é/DSCN0001.JPG", "Shared/2008 - é/n1_2_3_n.jpg")])

    def test_a_group_with_a_file_that_is_gone_is_skipped_not_fatal(self):
        rows = plan_rows(self.root, self.report("Shared/2008 - é/DSCN0001.JPG", "Shared/2008 - é/already-moved.jpg"))
        self.assertEqual([(r["reason"], r["remove"]) for r in rows], [("missing-file", "")])

    def test_apply_moves_the_duplicate_to_the_quarantine_and_lists_the_bucket_removals_then_undo(self):
        rows = plan_rows(self.root, self.report("Shared/2008 - é/DSCN0001.JPG", "Shared/2008 - é/n1_2_3_n.jpg"))
        moves = apply_plan(self.root, rows, self.quar, self.out)
        self.assertEqual(moves, 1)
        self.assertTrue(self.keep.exists())
        self.assertFalse(self.dupe.exists())
        self.assertTrue((self.quar / "Shared" / "2008 - é" / "n1_2_3_n.jpg").exists())
        self.assertEqual((self.out / "bucket-removals.txt").read_text(encoding="utf-8").splitlines(),
                         ["Shared/2008 - é/n1_2_3_n.jpg"])
        undo_moves(self.root, self.out / "moves.csv")
        self.assertTrue(self.dupe.exists())

    def test_identical_bytes_are_rechecked_with_the_full_file_before_moving(self):
        a, b = self.root / "Shared" / "v" / "camp loup 2008 060.mpg", self.root / "Shared" / "v" / "camp loup jf 2008 060.mpg"
        a.parent.mkdir(parents=True)
        a.write_bytes(b"same bytes"); b.write_bytes(b"same bytes")
        rows = [{"group": "g", "reason": "identical-bytes", "keep": "Shared/v/camp loup 2008 060.mpg",
                 "remove": "Shared/v/camp loup jf 2008 060.mpg", "keep_bytes": "10", "remove_bytes": "10"}]
        a.write_bytes(b"changed!!!")
        self.assertEqual(apply_plan(self.root, rows, self.quar, self.out), 0)
        a.write_bytes(b"same bytes")
        self.assertEqual(apply_plan(self.root, rows, self.quar, self.out), 1)
        self.assertFalse(b.exists())

    def test_apply_refuses_when_the_kept_file_is_gone_or_different(self):
        rows = plan_rows(self.root, self.report("Shared/2008 - é/DSCN0001.JPG", "Shared/2008 - é/n1_2_3_n.jpg"))
        self.keep.write_bytes(b"changed")
        self.assertEqual(apply_plan(self.root, rows, self.quar, self.out), 0)
        self.assertTrue(self.dupe.exists())


if __name__ == "__main__":
    unittest.main()
