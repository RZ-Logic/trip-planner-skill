"""Offline regression tests for the parking rate-text parser and pricing findings of the 2026-10 review
(parking-1, -2, -5, parking-verifier-1, -2).

Every HTTP call is mocked (Overpass through parking._overpass); nothing here touches the network.

Run: python -m unittest tests.test_review_parking_rates -v
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
import parking as pk  # noqa: E402

WED10 = dt.datetime(2026, 10, 14, 10, 0)
SAT10 = dt.datetime(2026, 10, 17, 10, 0)
SAT20 = dt.datetime(2026, 10, 17, 20, 0)
SUN10 = dt.datetime(2026, 10, 18, 10, 0)
DEST = (43.6453, -79.3806)


def price(text, when, mins, holidays=None):
    rule, _ = pk.parse_rate_text(text)
    return pk.price_rule(rule, when, mins, holidays or set())[0]


def osm_lot(when, mins, **tags):
    """One OSM lot through osm_lots, with the Overpass reply mocked (no network)."""
    el = {"type": "way", "id": 1, "center": {"lat": 43.6455, "lon": -79.3810},
          "tags": dict({"amenity": "parking", "fee": "yes", "name": "Test Lot"}, **tags)}
    with mock.patch.object(pk, "_overpass", return_value={"elements": [el]}):
        lots, _ = pk.osm_lots(DEST[0], DEST[1], when, mins, set(), 900, None, [])
    return lots[0]


class PriceBeforeWindow(unittest.TestCase):          # parking-1
    def test_each_price_binds_to_the_window_after_it(self):
        text = "$2/hr Mon-Fri 8am-6pm; $1/hr Sat 9am-5pm"
        self.assertEqual(price(text, WED10, 120), 4.0)     # HEAD: 2.0 (weekday gets the Saturday rate)
        self.assertEqual(price(text, SAT10, 120), 2.0)     # HEAD: 4.0

    def test_evening_flat_stays_in_its_own_window(self):
        text = "$3/hr 7am-7pm, $8 flat 7pm-7am"
        self.assertEqual(price(text, WED10, 120), 6.0)                     # HEAD: 8.0 (the night flat)
        self.assertEqual(price(text, WED10.replace(hour=20), 120), 8.0)    # HEAD: 6.0

    def test_one_price_still_covers_every_listed_window(self):
        text = "$2/hr Mon-Fri 8am-6pm, Sat 9am-5pm"
        self.assertEqual((price(text, WED10, 120), price(text, SAT10, 120)), (4.0, 4.0))

    def test_window_first_text_keeps_its_price(self):
        rule, _ = pk.parse_rate_text("Mon-Fri, 8:30 -18:00, $0.50/7.5 min, $19 Max, 18:00-21:00, $0.50/15 min, $4 Max "
                                     "// Sat,Sun,Holidays - Free")
        self.assertEqual([(p["hours"], p["hourly"], p["max"]) for p in rule["periods"]],
                         [(["08:30", "18:00"], 4.0, 19.0), (["18:00", "21:00"], 2.0, 4.0)])

    def test_night_maximum_never_replaces_the_day_maximum(self):
        rule, notes = pk.parse_rate_text("$4/hr, $20/day, $10 overnight")
        self.assertEqual(rule.get("daily_max"), 20.0)                       # HEAD: 10.0
        self.assertEqual(pk.price_rule(rule, WED10, 600, set())[0], 20.0)
        self.assertTrue(any("night maximum" in n for n in notes))
        rule, notes = pk.parse_rate_text("$12 Day Max, $5 Night Max")
        self.assertEqual(pk.price_rule(rule, WED10, 600, set())[0], 12.0)   # HEAD: 5.0
        self.assertTrue(any("night maximum" in n for n in notes))
        # a night price that is the only price still caps the stay ("$10 overnight" alone)
        self.assertEqual(pk.parse_rate_text("$10 overnight")[0].get("daily_max"), 10.0)


class PaidDaysThenFreeDays(unittest.TestCase):       # parking-2
    def test_paid_list_then_free_list(self):
        for text, paid in (("$2/hr Mon-Fri, Sat-Sun free", 4.0), ("$1.50/hr Mon-Sat, Sun free", 3.0),
                           ("$2 per hour 9am-9pm Mon-Sat, Sunday free", 4.0),
                           ("$2/hr, Mon-Sat, Sundays and holidays free", 4.0)):
            self.assertEqual(price(text, WED10, 120), paid, text)           # HEAD: 0.0 (free every day)
            self.assertEqual(price(text, SUN10, 120), 0.0, text)

    def test_free_day_lists_still_merge(self):
        self.assertEqual(pk.parse_rate_text("$2/hr, Sat, Sun and holidays free")[0].get("paid_days"), [0, 1, 2, 3, 4])
        self.assertEqual(pk.parse_rate_text("Mon-Wed and Sat 8 am to 6 pm")[0]["paid_days"], [0, 1, 2, 5])

    def test_free_every_paid_day_is_unpriced_not_free(self):
        rule, notes = pk.parse_rate_text("$2/hr, free Mon-Sun")
        self.assertEqual(rule, {})                                          # HEAD: paid_days [] priced $0
        self.assertTrue(any("free days cover every paid day" in n for n in notes))
        self.assertIsNone(pk.osm_charge_rule({"fee": "yes", "charge": "$2/hr, free Mon-Sun"})[0])


if __name__ == "__main__":
    unittest.main()
