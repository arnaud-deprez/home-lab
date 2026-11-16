import json
import tempfile
import unittest
from pathlib import Path

from photolib.dedupe import apply_verdicts, classify, summarize, undo_trash, write_report

EXT = "/mnt/rclone/external/Arnaud/2005 - x/"
UP = "/data/library/admin/2005/2005-08-01/"


def asset(aid, path, w=1600, h=1200, owner="u1", fav=False, trashed=False, offline=False):
    return {"id": aid, "originalPath": path, "width": w, "height": h, "ownerId": owner,
            "isFavorite": fav, "isTrashed": trashed, "isOffline": offline}


def group(*assets, gid="g1"):
    return {"duplicateId": gid, "assets": list(assets)}


class ClassifyTest(unittest.TestCase):
    def test_one_external_and_one_equal_upload_is_tier_a(self):
        v = classify(group(asset("e", EXT + "a.jpg"), asset("u", UP + "IMG_1.jpg")))
        self.assertEqual((v.tier, v.reason, v.external["id"], [a["id"] for a in v.trash]), ("A", "ok", "e", ["u"]))

    def test_names_do_not_matter(self):
        v = classify(group(asset("e", EXT + "DSCN0001.JPG"), asset("u", UP + "IMG_9999.jpg")))
        self.assertEqual(v.tier, "A")

    def test_smaller_upload_may_be_trashed(self):
        v = classify(group(asset("e", EXT + "a.jpg"), asset("u", UP + "a.jpg", w=800, h=600)))
        self.assertEqual(v.tier, "A")

    def test_larger_upload_is_never_trashed(self):
        v = classify(group(asset("e", EXT + "a.jpg", w=800, h=600), asset("u", UP + "a.jpg")))
        self.assertEqual((v.tier, v.reason), ("B", "uploaded-larger"))

    def test_rotated_dimensions_count_as_the_same_aspect(self):
        v = classify(group(asset("e", EXT + "a.jpg", w=1600, h=1200), asset("u", UP + "a.jpg", w=1200, h=1600)))
        self.assertEqual(v.tier, "A")

    def test_different_aspect_ratio_is_manual(self):
        v = classify(group(asset("e", EXT + "a.jpg", w=1600, h=1200), asset("u", UP + "a.jpg", w=1200, h=1200)))
        self.assertEqual((v.tier, v.reason), ("B", "aspect-differs"))

    def test_two_externals_are_left_alone(self):
        v = classify(group(asset("e1", EXT + "a.jpg"), asset("e2", EXT + "b.jpg")))
        self.assertEqual((v.tier, v.reason, v.trash), ("B", "multi-external", []))

    def test_no_external_is_left_alone(self):
        v = classify(group(asset("u1", UP + "a.jpg"), asset("u2", UP + "b.jpg")))
        self.assertEqual((v.tier, v.reason), ("B", "no-external"))

    def test_other_owner_is_manual(self):
        v = classify(group(asset("e", EXT + "a.jpg"), asset("u", UP + "a.jpg", owner="u2")))
        self.assertEqual((v.tier, v.reason), ("B", "owner-differs"))

    def test_unknown_location_is_manual(self):
        v = classify(group(asset("e", EXT + "a.jpg"), asset("x", "/somewhere/else/a.jpg")))
        self.assertEqual((v.tier, v.reason), ("B", "unknown-location"))

    def test_missing_dimensions_are_manual(self):
        v = classify(group(asset("e", EXT + "a.jpg"), asset("u", UP + "a.jpg", w=0, h=0)))
        self.assertEqual((v.tier, v.reason), ("B", "no-dimensions"))

    def test_trashed_or_offline_assets_are_manual(self):
        v = classify(group(asset("e", EXT + "a.jpg", offline=True), asset("u", UP + "a.jpg")))
        self.assertEqual((v.tier, v.reason), ("B", "not-usable"))

    def test_every_upload_must_pass(self):
        v = classify(group(asset("e", EXT + "a.jpg"), asset("u1", UP + "a.jpg"), asset("u2", UP + "b.jpg", w=4000, h=3000)))
        self.assertEqual((v.tier, v.reason), ("B", "uploaded-larger"))


class FakeClient:
    def __init__(self, albums=None):
        self.calls = []
        self.albums = albums or {}

    def albums_of(self, asset_id):
        self.calls.append(("albums_of", asset_id))
        return self.albums.get(asset_id, [])

    def add_to_albums(self, album_ids, asset_ids):
        self.calls.append(("add_to_albums", sorted(album_ids), asset_ids))

    def set_favorite(self, asset_ids):
        self.calls.append(("set_favorite", asset_ids))

    def trash(self, asset_ids):
        self.calls.append(("trash", asset_ids))

    def restore(self, asset_ids):
        self.calls.append(("restore", asset_ids))


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "dedupe-log.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def verdict(self, gid="g1", eid="e", uid="u", fav=False):
        return classify(group(asset(eid, EXT + "a.jpg"), asset(uid, UP + "a.jpg", fav=fav), gid=gid))

    def test_albums_and_favorite_are_synced_then_the_upload_is_trashed(self):
        c = FakeClient({"u": ["alb1", "alb2"]})
        apply_verdicts(c, [self.verdict(fav=True)], self.log)
        self.assertEqual(c.calls, [("albums_of", "u"), ("add_to_albums", ["alb1", "alb2"], ["e"]),
                                   ("set_favorite", ["e"]), ("trash", ["u"])])

    def test_no_album_call_when_the_upload_is_in_no_album_and_no_favorite(self):
        c = FakeClient()
        apply_verdicts(c, [self.verdict()], self.log)
        self.assertEqual(c.calls, [("albums_of", "u"), ("trash", ["u"])])

    def test_log_is_written_before_the_trash_call(self):
        class Spy(FakeClient):
            def trash(s, ids):
                s.logged_before = self.log.exists() and "u" in self.log.read_text()
                super().trash(ids)
        c = Spy()
        apply_verdicts(c, [self.verdict()], self.log)
        self.assertTrue(c.logged_before)

    def test_tier_b_groups_are_never_touched(self):
        c = FakeClient()
        b = classify(group(asset("e", EXT + "a.jpg", w=100, h=100), asset("u", UP + "a.jpg")))
        apply_verdicts(c, [b], self.log)
        self.assertEqual(c.calls, [])

    def test_limit_processes_only_the_first_groups(self):
        c = FakeClient()
        vs = [self.verdict("g1", "e1", "u1"), self.verdict("g2", "e2", "u2")]
        apply_verdicts(c, vs, self.log, limit=1)
        self.assertEqual([x for x in c.calls if x[0] == "trash"], [("trash", ["u1"])])

    def test_undo_restores_everything_in_the_log(self):
        c = FakeClient()
        apply_verdicts(c, [self.verdict("g1", "e1", "u1"), self.verdict("g2", "e2", "u2")], self.log)
        c2 = FakeClient()
        undo_trash(c2, self.log)
        self.assertEqual(c2.calls, [("restore", ["u1", "u2"])])
        self.assertEqual(len(self.log.read_text().splitlines()), 2)


class ReportTest(unittest.TestCase):
    def test_summary_counts_by_tier_and_reason_and_report_lists_every_group(self):
        vs = [classify(group(asset("e1", EXT + "a.jpg"), asset("u1", UP + "x.jpg"), gid="g1")),
              classify(group(asset("e2", EXT + "b.jpg", w=800, h=600), asset("u2", UP + "y.jpg"), gid="g2")),
              classify(group(asset("u3", UP + "c.jpg"), asset("u4", UP + "d.jpg"), gid="g3"))]
        s = summarize(vs)
        self.assertEqual((s["A"], s["B"], s["B:uploaded-larger"], s["B:no-external"], s["trash"]), (1, 2, 1, 1, 1))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.csv"
            write_report(vs, p)
            lines = p.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("group,tier,reason,"))
        self.assertIn("g1,A,ok,e1,", lines[1])

    def test_report_lists_every_photo_of_every_group_even_without_an_external_one(self):
        v = classify(group(asset("e1", EXT + "a.jpg", w=800, h=600), asset("e2", EXT + "b.jpg"), gid="g9"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.csv"
            write_report([v], p)
            import csv
            row = next(csv.DictReader(open(p, encoding="utf-8")))
        self.assertEqual(row["reason"], "multi-external")
        self.assertEqual(row["photos"], f"{EXT}a.jpg 800x600 | {EXT}b.jpg 1600x1200")
        self.assertEqual(row["count"], "2")


if __name__ == "__main__":
    unittest.main()
