import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from photolib.config import load_config, parse_override


class ConfigTest(unittest.TestCase):
    def test_parse_override_accepts_month_and_day(self):
        self.assertEqual(parse_override("2007-08"), datetime(2007, 8, 1, 12))
        self.assertEqual(parse_override("2007-08-15"), datetime(2007, 8, 15, 12))

    def test_parse_override_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_override("august")

    def test_load_config_reads_overrides_nfc_normalised(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"root": "/x", "ignore_globs": [], "phases": {"1": []}, "libraries": [],
                                     "date_overrides": {"Shared/2007 - soire\u0301e": "2007-08"}}), encoding="utf-8")
            cfg = load_config(p)
        self.assertEqual(list(cfg.date_overrides), ["Shared/2007 - soir\u00e9e"])


if __name__ == "__main__":
    unittest.main()
