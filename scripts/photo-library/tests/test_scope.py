import tempfile
import unicodedata
import unittest
from pathlib import Path

from photolib.config import Config, Library
from photolib.scope import find_collisions, folder_year, iter_albums


def make_tree(root: Path, dirs):
    for d in dirs:
        (root / d).mkdir(parents=True)
        (root / d / "a.jpg").write_bytes(b"x")


def cfg(root: Path, amelie_include=("P1", "P2")) -> Config:
    return Config(
        root=root,
        libraries=[
            Library("Arnaud", "child", None, ["papa"]),
            Library("Shared", "child", None, []),
            Library("Amelie", "grandchild", list(amelie_include), []),
        ],
        ignore_globs=[".DS_Store"],
        phases={1: ["Shared", "Arnaud"], 2: ["Amelie"]},
    )


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_tree(self.root, [
            "Arnaud/2007 - a", "Arnaud/papa", "Shared/2009 - b",
            "Amelie/P1/2005 - 1) x", "Amelie/P1/2005 - 2) y",
            "Amelie/P2/2006 - 1) x", "Amelie/Later/2020 - z",
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_phase1_lists_child_folders_without_excluded(self):
        names = [a.name for a in iter_albums(cfg(self.root), 1)]
        self.assertEqual(names, ["2009 - b", "2007 - a"])

    def test_phase2_lists_event_folders_of_included_periods_only(self):
        names = [a.name for a in iter_albums(cfg(self.root), 2)]
        self.assertEqual(names, ["2005 - 1) x", "2005 - 2) y", "2006 - 1) x"])

    def test_exclude_and_include_ignore_letter_case_like_macos(self):
        c = cfg(self.root, amelie_include=("p1",))   # folders are P1 and papa on disk
        c.libraries[0].exclude = ["PAPA"]
        self.assertEqual([a.name for a in iter_albums(c, 1)], ["2009 - b", "2007 - a"])
        self.assertEqual([a.name for a in iter_albums(c, 2)], ["2005 - 1) x", "2005 - 2) y"])

    def test_folder_year(self):
        self.assertEqual(folder_year("2005 - 1) londres"), 2005)
        self.assertIsNone(folder_year("papa"))
        self.assertIsNone(folder_year("Padova"))

    def test_collision_between_periods(self):
        make_tree(self.root, ["Amelie/P2/2005 - 1) x"])
        col = find_collisions(iter_albums(cfg(self.root), 2))
        self.assertEqual(list(col), ["2005 - 1) x"])
        self.assertEqual(len(col["2005 - 1) x"]), 2)

    def test_nfd_and_nfc_names_collide_and_names_are_nfc(self):
        nfc = unicodedata.normalize("NFC", "2007 - soirée")
        nfd = unicodedata.normalize("NFD", nfc)
        make_tree(self.root, ["Shared/" + nfd, "Arnaud/" + nfc])
        albums = iter_albums(cfg(self.root), 1)
        self.assertTrue(all(unicodedata.is_normalized("NFC", a.name) for a in albums))
        self.assertIn(nfc, find_collisions(albums))

    def test_include_matches_nfd_folder_with_nfc_config(self):
        period = unicodedata.normalize("NFD", "2006-2007(rhéto)")
        make_tree(self.root, ["Amelie/" + period + "/2006 - 1) e"])
        names = [a.name for a in iter_albums(cfg(self.root, ["2006-2007(rhéto)"]), 2)]
        self.assertEqual(names, ["2006 - 1) e"])


if __name__ == "__main__":
    unittest.main()
