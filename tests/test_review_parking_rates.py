"""Offline regression tests for the parking rate-text parser and pricing findings of the 2026-10 review
(parking-1, -2, -5, -6, -7, -9, parking-verifier-1, -2).

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
import json
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


class FreeEveningsAndWeekends(unittest.TestCase):    # parking-verifier-1
    def test_not_inverted(self):
        for text, rate in (("$2/hr 8am-6pm, free evenings and weekends", 2.0),
                           ("$3/hr 9am-6pm, free nights and weekends", 3.0)):
            self.assertEqual(price(text, WED10, 180), rate * 3, text)       # HEAD: 0.0
            self.assertEqual(price(text, SAT10, 180), 0.0, text)            # HEAD: 6.0 / 9.0
            self.assertEqual(price(text, WED10.replace(hour=19), 120), 0.0, text)

    def test_evening_hours_not_given_are_noted(self):
        rule, notes = pk.parse_rate_text("$3/hr, free evenings and weekends")
        self.assertEqual(rule.get("paid_days"), [0, 1, 2, 3, 4])
        self.assertIn("free evenings (hours not given)", notes)


class BaseRateWithAfterPrice(unittest.TestCase):     # parking-verifier-2
    def test_daytime_keeps_the_hourly_rate(self):
        for text in ("$3/hr, $10 after 5pm", "$3/hr before 5pm, $10 flat after 5pm"):
            self.assertEqual(price(text, WED10, 180), 9.0, text)            # HEAD: 0.0 "free at this time"
            self.assertEqual(price(text, WED10.replace(hour=18), 120), 10.0, text)
            self.assertEqual(price(text, WED10.replace(hour=16), 180), 13.0, text)   # 1 h at $3, then the $10


class OsmOpeningHours(unittest.TestCase):            # parking-5
    def test_day_forms_mark_the_lot_unfit(self):
        lot = osm_lot(SAT20, 180, charge="$3/hr", opening_hours="Mo-Fr 07:00-19:00")
        self.assertFalse(lot["fits_stay"])                                  # HEAD: True, no hours shown
        self.assertEqual(lot["unfit_reason"], "open Mo-Fr 07:00-19:00 only")
        self.assertIn("open Mo-Fr 07:00-19:00", lot["notes"])
        lot = osm_lot(SAT20, 180, charge="$3/hr", opening_hours="Mo-Sa 09:00-21:00; Su 11:00-18:00")
        self.assertFalse(lot["fits_stay"])
        self.assertTrue(osm_lot(SAT10, 120, charge="$3/hr", opening_hours="Mo-Sa 09:00-21:00; Su 11:00-18:00")["fits_stay"])

    def test_schedule_reader(self):
        s = pk.opening_schedule("Mo-Fr 06:00-21:00; Sa 07:00-18:00; Su off")
        self.assertTrue(pk.open_for_schedule(s, WED10, 120))
        self.assertFalse(pk.open_for_schedule(s, SUN10, 60))                # Sunday off
        self.assertFalse(pk.open_for_schedule(s, WED10.replace(hour=20), 120))   # past 21:00
        self.assertFalse(pk.open_for_schedule(pk.opening_schedule("Mo-Fr 08:00-18:00; PH off"),
                                              dt.datetime(2026, 10, 12, 10, 0), 60, {"2026-10-12"}))
        self.assertTrue(pk.open_for_schedule(pk.opening_schedule("Mo-Fr 08:00-18:00, Sa 09:00-12:00"), SAT10, 60))
        night = pk.opening_schedule("Fr-Sa 20:00-02:00")                    # past midnight
        self.assertTrue(pk.open_for_schedule(night, dt.datetime(2026, 10, 17, 1, 0), 60))      # Sat 01:00, Friday's
        self.assertFalse(pk.open_for_schedule(night, dt.datetime(2026, 10, 18, 3, 0), 60))     # Sun 03:00
        self.assertEqual(pk.opening_schedule("24/7"), "always")
        self.assertEqual(pk.opening_schedule("Mo-Su,PH 00:00-24:00"), "always")
        self.assertIsNone(pk.opening_schedule("Jan-Mar Mo-Fr 08:00-17:00"))
        self.assertIsNone(pk.opening_schedule("sunrise-sunset"))
        self.assertIsNone(pk.opening_schedule("Sa,Su off"))                 # never open: not read, not "closed"
        for text in ("PH off; Mo-Fr 08:00-18:00", "Mo-Fr,PH 08:00-18:00"):   # a PH rule still closes the weekend
            self.assertFalse(pk.open_for_schedule(pk.opening_schedule(text), SAT10, 60), text)

    def test_unreadable_hours_are_noted(self):
        lot = osm_lot(SAT20, 180, charge="$3/hr", opening_hours="Jan-Mar Mo-Fr 08:00-17:00")
        self.assertIn("hours (OSM, not read): Jan-Mar Mo-Fr 08:00-17:00", lot["notes"])
        self.assertTrue(pk.check_hint(lot)["why"].endswith("; opening hours not read"))
        lot = osm_lot(SAT20, 180, charge="$3/hr", opening_hours="Mo-Fr 08:00-18:00 \"call ahead\"")
        self.assertIn("opening hours in OpenStreetMap not read", lot["notes"])
        self.assertNotIn("call ahead", lot["notes"])                       # only the safe character set is shown

    def test_simple_form_message_unchanged(self):
        lot = osm_lot(dt.datetime(2026, 10, 19, 12, 0), 120, charge="$3/hr", opening_hours="20:00-06:00")
        self.assertEqual(lot["unfit_reason"], "open 20:00 to 06:00 only")
        self.assertIn("open 20:00-06:00", lot["notes"])


class FirstHourForms(unittest.TestCase):             # parking-9
    def test_n_hours_free(self):
        for text in ("2 hours free, then $3/hr", "Free for 2 hours, then $3/hr", "Free for the first 2 hours, $3/hr after"):
            self.assertEqual(price(text, WED10, 180), 3.0, text)            # HEAD: 9.0
            self.assertEqual(price(text, WED10, 90), 0.0, text)

    def test_n_hours_free_is_not_a_time_limit(self):
        rule, _ = pk.parse_rate_text("$3/hr, 2 hours free parking limit")
        self.assertNotIn("free_first_min", rule)

    def test_1st_hour(self):
        self.assertEqual(price("$5 1st hour, $3/hr after", WED10, 180), 11.0)   # HEAD: 9.0

    def test_first_hour_inside_the_day_maximum(self):
        text = "$5 first hour, $2 each additional hour, $12 max"
        self.assertEqual(price(text, WED10, 600), 12.0)                     # HEAD: 15.0
        self.assertEqual(price(text, WED10, 180), 9.0)
        self.assertEqual(price("$5 first hour, $2 each additional hour", WED10, 60), 5.0)


class AcrossMidnight(unittest.TestCase):             # parking-6
    def test_charge_units_do_not_restart_at_midnight(self):
        j = dt.datetime(2026, 7, 15, 23, 30)
        self.assertEqual(pk.price_rule({"hourly": 15.0, "paid_hours": ["00:00", "24:00"]}, j, 60)[0], 15.0)   # HEAD: 30.0
        self.assertEqual(pk.price_rule({"hourly": 3.0}, j.replace(hour=22), 165)[0], 9.0)                   # HEAD: 12.0
        self.assertEqual(price("$7 for 3 hours", j.replace(minute=0), 120), 7.0)                            # HEAD: 14.0
        fill = {"hourly": 3.0, "fill": True, "periods": [{"hours": ["08:00", "18:00"], "max": 12.0}]}
        self.assertEqual(pk.price_rule(fill, j, 60)[0], 3.0)                                                # HEAD: 6.0

    def test_day_cap_still_per_calendar_day(self):
        self.assertEqual(pk.price_rule({"hourly": 4.0, "daily_max": 20.0}, dt.datetime(2026, 7, 15, 20, 0), 600)[0], 36.0)

    def test_overnight_window_keeps_one_cap_per_night(self):
        night = {"hourly": 2.0, "periods": [{"hours": ["17:00", "07:00"], "max": 6.0}]}
        self.assertEqual(pk.price_rule(night, dt.datetime(2026, 7, 15, 22, 0), 240)[0], 6.0)

    def test_curated_lots(self):
        # a daily-max-only rule charges one price per calendar day touched on purpose, so it is left out
        for f in ("parking-places-west.json", "parking-places-east.json"):
            with open(os.path.join(pk.PLACES_DIR, f), encoding="utf-8") as fh:
                places = json.load(fh)["places"]
            for place in places:
                season = place.get("season") or {}
                for lot in place.get("lots") or []:
                    rule = dict(lot.get("rule") or {})
                    if not rule or (rule.get("daily_max") is not None and rule.get("hourly") is None
                                    and rule.get("flat") is None and not rule.get("periods")):
                        continue
                    if season.get("from") and "season" not in rule and not rule.get("free") and lot.get("kind") != "park":
                        rule["season"] = [season["from"], season["to"]]     # as find() gives the lot the town's season
                    late = pk.price_rule(rule, dt.datetime(2026, 7, 15, 23, 30), 60)[0]
                    early = pk.price_rule(rule, dt.datetime(2026, 7, 15, 22, 30), 60)[0]
                    if late and early:
                        self.assertLessEqual(late, early + 0.005, lot["name"])   # HEAD: 17 lots double


class EveningOnlyPrice(unittest.TestCase):           # parking-7
    def test_daytime_is_unknown_not_free(self):
        cost, basis, _ = pk.price_rule(pk.parse_rate_text("$5 after 6pm")[0], WED10, 180, set())
        self.assertIsNone(cost)                                             # HEAD: 0.0 "free at this time"
        self.assertIn("price published for 18:00-24:00 only", basis)
        self.assertEqual(price("$5 after 6pm", WED10.replace(hour=19), 120), 5.0)
        self.assertEqual(osm_lot(WED10, 180, charge="$5 after 6pm")["cost_text"], "price unknown, check signs")

    def test_a_base_rate_or_full_day_prices_are_not_partial(self):
        self.assertEqual(price("$1/Hr $6 Day (7am-6pm), $5 Eve (6pm-midnight)", WED10.replace(hour=8), 600), 6.0)
        self.assertEqual(price("Weekdays $15, Weekends $8", WED10, 180), 15.0)
        self.assertNotIn("_partial_hours", pk.parse_rate_text("Mon-Fri, 8:30 -18:00, $0.50/7.5 min, $19 Max")[0])


if __name__ == "__main__":
    unittest.main()
