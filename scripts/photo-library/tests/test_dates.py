import unittest
from datetime import datetime

from photolib.dates import choose_date

NOW = datetime(2026, 11, 15, 12, 0, 0)


def choose(exif, mtime, year=2009, seq=0):
    return choose_date(exif, mtime, year, seq, NOW)


class ChooseDateTest(unittest.TestCase):
    def test_exif_in_folder_year_is_kept(self):
        d = choose(datetime(2009, 8, 1, 10), datetime(2020, 1, 1))
        self.assertEqual((d.rule, d.date), ("keep-exif", datetime(2009, 8, 1, 10)))

    def test_exif_one_year_off_is_kept(self):
        self.assertEqual(choose(datetime(2010, 1, 2), datetime(2020, 1, 1)).rule, "keep-exif")

    def test_bad_exif_uses_plausible_mtime(self):
        d = choose(datetime(2007, 2, 4), datetime(2009, 7, 3, 9))
        self.assertEqual((d.rule, d.date), ("from-mtime", datetime(2009, 7, 3, 9)))

    def test_missing_exif_uses_plausible_mtime(self):
        self.assertEqual(choose(None, datetime(2009, 7, 3)).rule, "from-mtime")

    def test_valid_mtime_with_wrong_year_keeps_day_and_time(self):
        d = choose(datetime(2007, 1, 1), datetime(2015, 6, 5, 10))
        self.assertEqual((d.rule, d.date), ("mtime-day-folder-year", datetime(2009, 6, 5, 10)))

    def test_feb_29_on_non_leap_folder_year_does_not_crash(self):
        d = choose(None, datetime(2024, 2, 29, 8))
        self.assertEqual(d.date, datetime(2009, 2, 28, 8))

    def test_invalid_mtime_falls_back_to_jan1_plus_seq(self):
        d = choose(None, datetime(1970, 1, 1), seq=3)
        self.assertEqual((d.rule, d.date), ("jan1-seq", datetime(2009, 1, 1, 12, 0, 3)))

    def test_future_mtime_falls_back_to_jan1(self):
        self.assertEqual(choose(None, datetime(2030, 1, 1)).rule, "jan1-seq")

    def test_placeholder_mtime_day_is_treated_as_invalid(self):
        mtime = datetime(2001, 12, 31, 22)
        d = choose_date(None, mtime, 2009, 4, NOW, placeholder_days=frozenset({mtime.date()}))
        self.assertEqual((d.rule, d.date), ("jan1-seq", datetime(2009, 1, 1, 12, 0, 4)))

    def test_placeholder_day_does_not_override_a_plausible_mtime(self):
        mtime = datetime(2009, 5, 5, 10)
        d = choose_date(None, mtime, 2009, 0, NOW, placeholder_days=frozenset({mtime.date()}))
        self.assertEqual(d.rule, "from-mtime")

    def test_no_folder_year_is_skipped(self):
        d = choose_date(datetime(2009, 1, 1), datetime(2009, 1, 1), None, 0, NOW)
        self.assertEqual((d.rule, d.date), ("skip-no-year", None))


if __name__ == "__main__":
    unittest.main()
