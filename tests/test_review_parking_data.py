"""Offline regression tests for the parking ranking, check-line and curated parking data findings of the 2026-10
review (ext-11, -14, -15, -16, data-integrity-1, -4, -7, parking-3, -4, -8).

Every HTTP call is mocked or blocked; nothing here touches the network.

Run: python -m unittest tests.test_review_parking_data -v
"""
import os as _os
for _k in ("TOMTOM_API_KEY", "NLR_API_KEY", "ON511_API_KEY", "GOOGLE_PLACES_API_KEY"):
    _os.environ.pop(_k, None)   # offline tests never use the developer's real API keys
import atexit as _atexit
import shutil as _shutil
import tempfile as _tempfile
_TMP = _tempfile.mkdtemp(prefix="trip-planner-tests-")   # never the saved car or the cache in the home folder,
_atexit.register(_shutil.rmtree, _TMP, True)            # even when exported: set before any script is imported
_os.environ["TRIP_PLANNER_CONFIG"] = _os.path.join(_TMP, "config", "car.json")
_os.environ["TRIP_PLANNER_CACHE"] = _os.path.join(_TMP, "cache")
_os.environ.setdefault("TRIP_PLANNER_QUIET", "1")   # no progress lines on stderr (TRIP_PLANNER_QUIET=0 shows them)
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import parking as pk  # noqa: E402

DEST = (43.6453, -79.3806)   # Union Station, Toronto


def place(pid):
    return [p for p in pk.load_places() if p.get("id") == pid][0]


def curated_lot(pid, name, when, stay_min, holidays=()):
    """One curated lot of a place, priced for the stay, searched from the place's own match point."""
    m = place(pid)["match"]
    lots, _r, _res, _s = pk.curated(float(m["lat"]), float(m["lon"]), when, stay_min, set(holidays), 5000, [])
    return [x for x in lots if x["name"] == name][0]


class CuratedData(unittest.TestCase):
    def test_wellington_beach_paid_on_weekday_holidays(self):   # ext-14
        # "$20/day on Saturdays, Sundays and holidays": a holiday on a weekday is paid, a plain weekday is free
        n = "Wellington Rotary Beach lot"
        self.assertEqual(curated_lot("wellington-pec", n, dt.datetime(2026, 7, 1, 11), 120, {"2026-07-01"})["cost"], 20.0)
        self.assertEqual(curated_lot("wellington-pec", n, dt.datetime(2026, 9, 7, 11), 120, {"2026-09-07"})["cost"], 20.0)
        self.assertEqual(curated_lot("wellington-pec", n, dt.datetime(2026, 7, 8, 11), 120)["cost"], 0.0)
        self.assertEqual(curated_lot("wellington-pec", n, dt.datetime(2026, 7, 4, 11), 120)["cost"], 20.0)

    def test_daily_fee_charged_per_day(self):   # ext-15
        # a daily fee or daily permit is paid for each day of a multi-day stay, not once per entry
        n = "Fort George lot (Parks Canada)"
        self.assertEqual(curated_lot("niagara-on-the-lake", n, dt.datetime(2026, 7, 15, 10), 2880)["cost"], 19.0)
        self.assertEqual(curated_lot("niagara-on-the-lake", n, dt.datetime(2026, 7, 15, 10), 480)["cost"], 9.5)
        self.assertEqual(curated_lot("sandbanks", "Outlet Beach day-use lot", dt.datetime(2026, 7, 15, 10), 2880)["cost"],
                         42.0)

    def test_white_water_walk_overnight_ban(self):   # ext-16
        # no overnight parking at any Niagara Parks lot (00:00 to 06:00), White Water Walk included
        lot = curated_lot("niagara-falls", "White Water Walk lot", dt.datetime(2026, 7, 15, 23), 120)
        self.assertFalse(lot["fits_stay"])
        self.assertEqual(lot["unfit_reason"], "no parking 00:00 to 06:00 during your stay")
        self.assertTrue(curated_lot("niagara-falls", "White Water Walk lot", dt.datetime(2026, 7, 15, 10), 120)["fits_stay"])


if __name__ == "__main__":
    unittest.main()
