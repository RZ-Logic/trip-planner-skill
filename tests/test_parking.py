"""Offline tests for scripts/parking.py (rate-text parser, price_rule, sources, ranking).

Every HTTP call is mocked through drive_common.fetch. The rate strings are real values returned by the city
layers, Green P and OSM on 2026-10-10.

Run: python -m unittest discover -s tests -v
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
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import parking as pk  # noqa: E402

WED = dt.datetime(2026, 10, 14, 14, 0)    # Wednesday
SAT = dt.datetime(2026, 10, 17, 11, 0)    # Saturday
THANKSGIVING = dt.datetime(2026, 10, 12, 11, 30)
HOL = {"2026-10-12"}

# --------------------------------------------------------------------------- fixtures
PLACES_FIXTURE = {"verified": "2026-10-10", "places": [
    {"id": "test-beach", "name": "Test Beach", "match": {"lat": 43.3125, "lon": -81.7569, "radius_km": 3},
     "season": {"from": "05-15", "to": "09-15", "off_season": "free", "note": "paid season"},
     "source": "https://example.org/brochure.pdf", "source_date": "2026", "confidence": "curated",
     "rules": ["Main St lots fill by 11:00 on summer weekends"],
     "reservation": {"required": False, "text": "", "url": "", "lead_days": 0},
     "lots": [
         {"name": "North Beach lot", "lat": 43.3140, "lon": -81.7580, "kind": "lot",
          "rule": {"hourly": 8.0, "daily_max": 40.0, "paid_hours": ["08:00", "18:00"]}, "capacity": 300,
          "notes": "", "source": "https://example.org/brochure.pdf"},
         {"name": "Gill Rd lot", "lat": 43.3100, "lon": -81.7550, "kind": "lot", "rule": {"free": True},
          "capacity": None, "notes": "", "source": "https://example.org/brochure.pdf"},
         {"name": "Far provincial park", "lat": 43.2700, "lon": -81.8300, "kind": "park",
          "rule": {"flat": 21.0}, "capacity": None, "notes": "Ontario Parks day-use permit",
          "source": "https://example.org/park",
          "reservation": {"required": True, "text": "book a day-use permit", "url": "https://example.org/book",
                          "lead_days": 5}}]},
    {"id": "elsewhere", "name": "Elsewhere", "match": {"lat": 45.0, "lon": -80.0, "radius_km": 2}, "lots": []}]}

BURL_LOTS = {"features": [
    {"attributes": {"LOTNAME": "Elizabeth Street Lot", "LOT_TYPE": "Public", "ADDRESS": "421 John St (Enter Elizabeth)",
                    "PAID_AVAILABILITY": "Monday-Saturday, 9am - 6pm", "HOURLY_RATE": "$2.00", "DAILY_RATE": "$18.00",
                    "FREE_PARKING": "Mon-Sat after 6pm, Sun, Holidays", "SPACES_REGULAR": 93},
     "geometry": {"rings": [[[-79.7970, 43.3261], [-79.7966, 43.3264], [-79.7965, 43.3260], [-79.7970, 43.3261]]]}},
    {"attributes": {"LOTNAME": "City Hall Lot", "LOT_TYPE": "Public", "ADDRESS": "426 Brant St",
                    "PAID_AVAILABILITY": "Monday-Friday, 9am - 6pm (3 hr limit)", "HOURLY_RATE": "$2.00",
                    "DAILY_RATE": "$18.00", "FREE_PARKING": "Mon-Fri after 6pm, Sat, Sun, Holidays", "SPACES_REGULAR": 44},
     "geometry": {"rings": [[[-79.7993, 43.3258], [-79.7990, 43.3260], [-79.7988, 43.3257], [-79.7993, 43.3258]]]}},
    {"attributes": {"LOTNAME": "Staff Lot", "LOT_TYPE": "Permit", "ADDRESS": "1 Nowhere", "HOURLY_RATE": "n/a"},
     "geometry": {"rings": [[[-79.7990, 43.3255], [-79.7991, 43.3256], [-79.7990, 43.3256], [-79.7990, 43.3255]]]}}]}
BURL_STREET = {"features": [
    {"attributes": {"TYPE": "Paid parking", "RATE": "Mon-Sat, 9am-6pm $2.00/hr  3hr limit"},
     "geometry": {"paths": [[[-79.8000, 43.3250], [-79.7995, 43.3254]]]}},
    {"attributes": {"TYPE": "Vehicle Loading zone", "RATE": "20 minute limit - 10AM - 10PM / HOLIDAY EXCEPTED"},
     "geometry": {"paths": [[[-79.7991, 43.3255], [-79.7990, 43.3256]]]}}]}
OVERPASS = {"elements": [
    {"type": "way", "id": 1, "center": {"lat": 43.32612, "lon": -79.79680}, "tags": {"amenity": "parking", "fee": "yes", "capacity": "120"}},
    {"type": "way", "id": 2, "center": {"lat": 43.3240, "lon": -79.8010}, "tags": {"amenity": "parking", "fee": "no", "name": "Church lot"}},
    {"type": "way", "id": 3, "center": {"lat": 43.3250, "lon": -79.7980}, "tags": {"amenity": "parking", "access": "private", "fee": "no"}},
    {"type": "node", "id": 4, "lat": 43.3262, "lon": -79.7985, "tags": {"amenity": "parking"}},
    {"type": "way", "id": 5, "center": {"lat": 43.3245, "lon": -79.7995}, "tags": {"amenity": "parking", "fee": "yes", "charge": "$2/hr;$5 max", "maxheight": "1.9"}},
    {"type": "way", "id": 6, "center": {"lat": 43.3266, "lon": -79.8001}, "tags": {"amenity": "parking"}},
    {"type": "way", "id": 7, "center": {"lat": 43.3249, "lon": -79.7979}, "tags": {"amenity": "parking", "fee": "yes"}}]}

GREENP = {"carparks": [
    {"id": "36", "address": "40 York Street", "lat": "43.6437", "lng": "-79.3820", "rate_half_hour": "4.00",
     "carpark_type": "garage", "is_ttc": False, "capacity": "150", "max_height": "2.00", "slug": "https://parking.greenp.com/carpark/36/",
     "rate_details": {"periods": [{"title": "Monday - Friday", "rates": [{"when": "Day Maximum (7am - 7pm)", "rate": "$25.00"},
                                                                       {"when": "Night Maximum (5pm - 7am)", "rate": "$9.00"}]},
                                  {"title": "Saturday - Sunday", "rates": [{"when": "Maximum per 12 hour period (Sat 7am - Mon 7am)", "rate": "$9.00"}]}]}},
    {"id": "801", "address": "Kipling South Lot - 400 Munster Avenue", "lat": "43.6460", "lng": "-79.3790", "rate_half_hour": "0.00",
     "carpark_type": "surface", "is_ttc": True, "capacity": "300", "max_height": "", "slug": "",
     "rate_details": {"periods": [{"title": "Monday - Friday", "rates": [{"when": "Flat Rate (5am - 3pm)", "rate": "$6.00"},
                                                                       {"when": "Flat Rate (3pm - 2am)", "rate": "$2.00"}]},
                                  {"title": "Saturday - Sunday & Holidays", "rates": [{"when": "(5am - 3pm)", "rate": "FREE"},
                                                                                     {"when": "(3pm - 2am)", "rate": "FREE"}]}]}},
    {"id": "1", "address": "20 Charles Street East", "lat": "43.6693", "lng": "-79.3853", "rate_half_hour": "2.50",
     "carpark_type": "garage", "is_ttc": False, "capacity": "641", "max_height": "2.00",
     "rate_details": {"periods": [{"title": "Monday - Sunday & Holidays", "rates": [{"when": "Day Maximum (7am - 6pm)", "rate": "$14.00"},
                                                                                  {"when": "Night Maximum (6pm - 7am)", "rate": "$6.00"}]}]}}]}
OCCUPANCY = {"result": {"records": [
    {"Car Park Number": "CP36", "Average Daily Peak Occupancy %": "92%", "Year": "2026", "Quarter": "Q1"},
    {"Car Park Number": "CP36", "Average Daily Peak Occupancy %": "40%", "Year": "2025", "Quarter": "Q4"},
    {"Car Park Number": "CP801", "Average Daily Peak Occupancy %": "50%", "Year": "2026", "Quarter": "Q1"}]}}


def fake_fetch(routes):
    """drive_common.fetch stand-in: the first (substring, reply) whose substring is in the URL answers;
    replies that are exceptions are raised. Unknown URLs fail like a dead network."""
    calls = []

    def fetch(url, data=None, **kw):
        calls.append((url, data))
        for key, reply in routes:
            if key in url:
                if isinstance(reply, Exception):
                    raise reply
                if callable(reply):
                    reply = reply(url, data)
                return json.dumps(reply).encode("utf-8")
        raise urllib.error.URLError("offline test: %s" % url)
    fetch.calls = calls
    return fetch


class PlacesDir(object):
    def __enter__(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "parking-places-test.json"), "w", encoding="utf-8") as f:
            json.dump(PLACES_FIXTURE, f)
        self.patch = mock.patch.object(pk, "PLACES_DIR", self.tmp)
        self.patch.start()
        return self.tmp

    def __exit__(self, *a):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def empty_places():
    tmp = tempfile.mkdtemp()
    return mock.patch.object(pk, "PLACES_DIR", tmp), tmp


# --------------------------------------------------------------------------- parser

class RateTextParser(unittest.TestCase):
    def test_burlington_street(self):
        r, _ = pk.parse_rate_text("Mon-Sat, 9am-6pm $2.00/hr  3hr limit")
        self.assertEqual(r["hourly"], 2.0)
        self.assertEqual(r["paid_days"], [0, 1, 2, 3, 4, 5])
        self.assertEqual(r["paid_hours"], ["09:00", "18:00"])
        self.assertEqual(r["max_stay_min"], 180)

    def test_burlington_schedule_with_limit(self):
        r, _ = pk.parse_rate_text("Monday-Friday, 9am - 6pm (3 hr limit)")
        self.assertEqual((r["paid_days"], r["paid_hours"], r["max_stay_min"]), ([0, 1, 2, 3, 4], ["09:00", "18:00"], 180))
        r, _ = pk.parse_rate_text("7 Days, 9am - 4pm")
        self.assertEqual((r["paid_days"], r["paid_hours"]), (list(range(7)), ["09:00", "16:00"]))
        r, _ = pk.parse_rate_text("Mon\u2013Fri 9am\u20136pm, $1.50/hr")
        self.assertEqual((r["paid_days"], r["paid_hours"], r["hourly"]), ([0, 1, 2, 3, 4], ["09:00", "18:00"], 1.5))
        r, _ = pk.parse_rate_text("Mon-Wed and Sat 8 am to 6 pm")
        self.assertEqual((r["paid_days"], r["paid_hours"]), ([0, 1, 2, 5], ["08:00", "18:00"]))

    def test_ottawa_machine_seconds_and_french(self):
        r, _ = pk.parse_rate_text("$0.25/4 min 18s, Monday / Lundi to / \u00e0 Friday / Vendredi ( 8:30am - 5:30pm )")
        self.assertAlmostEqual(r["hourly"], 3.488, places=2)
        self.assertAlmostEqual(r["per_min"], 4.3, places=2)
        self.assertEqual((r["paid_days"], r["paid_hours"]), ([0, 1, 2, 3, 4], ["08:30", "17:30"]))
        r, _ = pk.parse_rate_text("$0.25/15 min, Monday / Lundi to / \ufffd Friday / Vendredi (9:30am - 3:30pm)")
        self.assertEqual((r["hourly"], r["per_min"]), (1.0, 15.0))

    def test_ottawa_machine_weekday_and_saturday(self):
        r, _ = pk.parse_rate_text("$0.25/3 min 45s, Monday / Lundi to / \u00e0 Friday / Vendredi ( 8:30am - 7:30pm ) "
                                  "Saturday / Samedi ( 10:00am - 7:30pm)")
        self.assertEqual(r["hourly"], 4.0)
        self.assertEqual([(p["days"], p["hours"]) for p in r["periods"]],
                         [([0, 1, 2, 3, 4], ["08:30", "19:30"]), ([5], ["10:00", "19:30"])])
        cost, _, _ = pk.price_rule(r, SAT.replace(hour=9), 120)   # Saturday 09:00 to 11:00: 1 h paid
        self.assertEqual(cost, 4.0)

    def test_ottawa_peak_gap_is_no_parking(self):
        r, notes = pk.parse_rate_text("$0.25/4 min 18s, Monday / Lundi to / \u00e0 Friday / Vendredi ( 9:30am - 3:30pm + "
                                      "6:00pm - 7:30pm ) Saturday / Samedi ( 10:00am - 7:30pm)")
        self.assertEqual(r["no_parking"], [{"days": [0, 1, 2, 3, 4], "hours": ["15:30", "18:00"]}])
        self.assertTrue(any("peak" in n for n in notes))
        _, _, fits = pk.price_rule(r, WED, 120)
        self.assertFalse(fits)
        _, _, fits = pk.price_rule(r, WED.replace(hour=10), 120)
        self.assertTrue(fits)

    def test_ottawa_lot_two_windows_with_caps(self):
        r, _ = pk.parse_rate_text("Mon-Fri, 8:30 -18:00, $0.50/7.5 min, $19 Max, 18:00-21:00, $0.50/15 min, $4 Max "
                                  "// Sat,Sun,Holidays - Free")
        self.assertTrue(r["holidays_free"])
        p1, p2 = r["periods"]
        self.assertEqual((p1["hours"], p1["hourly"], p1["max"]), (["08:30", "18:00"], 4.0, 19.0))
        self.assertEqual((p2["hours"], p2["hourly"], p2["max"]), (["18:00", "21:00"], 2.0, 4.0))
        self.assertEqual(pk.price_rule(r, WED.replace(hour=9), 600)[0], 21.0)   # 09:00-19:00: $19 cap + 1 h at $2
        self.assertEqual(pk.price_rule(r, SAT, 180)[0], 0.0)

    def test_london_hours_first(self):
        r, _ = pk.parse_rate_text("10:00am - 03:00pm (Mon - Fri) | 08:00am - 06:00pm (Sat)")
        self.assertEqual([(p["days"], p["hours"]) for p in r["periods"]],
                         [([0, 1, 2, 3, 4], ["10:00", "15:00"]), ([5], ["08:00", "18:00"])])
        r, _ = pk.parse_rate_text("08:00am - 06:00pm (Mon - Sat)")
        self.assertEqual((r["paid_days"], r["paid_hours"]), ([0, 1, 2, 3, 4, 5], ["08:00", "18:00"]))

    def test_mississauga_day_and_evening_caps(self):
        r, notes = pk.parse_rate_text("$1/Hr $6 Day (7am-6pm), $5 Eve (6pm-midnight) (No Overnight Parking)")
        self.assertEqual(r["hourly"], 1.0)
        self.assertTrue(r["fill"])
        self.assertEqual([(p["hours"], p["max"]) for p in r["periods"]], [(["07:00", "18:00"], 6.0), (["18:00", "24:00"], 5.0)])
        self.assertIn("no overnight parking", notes)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=8), 600)[0], 6.0)

    def test_osm_charge_strings(self):
        cases = {"$2/hr;$5 max": {"hourly": 2.0, "daily_max": 5.0},
                 "1.75 CAD/30 minutes": {"hourly": 3.5, "per_min": 30.0},
                 "$4CAD/hour": {"hourly": 4.0},
                 "3 CAD/30 min, 15 CAD/day max": {"hourly": 6.0, "per_min": 30.0, "daily_max": 15.0},
                 "2.00 CAD/30min, 8.00 CAD/24 hour max": {"hourly": 4.0, "daily_max": 8.0},
                 "First hour FREE. $1 per 1/2 hour. $9 Daily.": {"free_first_min": 60, "hourly": 2.0, "per_min": 30.0, "daily_max": 9.0},
                 "$10 CND per car": {"flat": 10.0},
                 "$22 a day weekdays as of 2019": {"_partial_days": [0, 1, 2, 3, 4]}}
        for text, want in cases.items():
            r, _ = pk.parse_rate_text(text)
            for k, v in want.items():
                self.assertEqual(r.get(k), v, "%s: %s" % (text, k))
        self.assertEqual(pk.parse_rate_text("4 USD/h")[0]["currency"], "USD")
        self.assertEqual(pk.parse_rate_text("Free: For Members, Patrons (For the first 2 to 3 hours)")[0], {})
        self.assertEqual(pk.parse_rate_text("n/a"), ({}, []))
        self.assertEqual(pk.parse_rate_text("$100")[0], {})
        self.assertEqual(pk.parse_rate_text("$40")[0], {})       # a bare large amount is not a price

    def test_osm_rule(self):
        self.assertEqual(pk.osm_charge_rule({"fee": "no"})[0], {"free": True})
        self.assertIsNone(pk.osm_charge_rule({"fee": "yes"})[0])
        r, _ = pk.osm_charge_rule({"fee": "yes", "charge": "$2/hr;$5 max", "maxstay": "2 hours"})
        self.assertEqual((r["hourly"], r["daily_max"], r["max_stay_min"]), (2.0, 5.0, 120))
        self.assertEqual(pk.price_rule(r, WED, 180)[2], False)

    def test_small_parsers(self):
        self.assertEqual(pk.parse_duration("2 hours"), 120)
        self.assertEqual(pk.parse_duration("3"), 180)
        self.assertEqual(pk.parse_duration("15 minutes"), 15)
        self.assertIsNone(pk.parse_duration("Monthly"))
        self.assertEqual(pk.money_field("$18.00"), 18.0)
        self.assertEqual(pk.money_field(3.25), 3.25)
        self.assertIsNone(pk.money_field("n/a"))
        self.assertEqual(pk.parse_height("2.0 m max"), 2.0)
        self.assertEqual(pk.parse_height("6'8\""), 2.03)
        self.assertEqual(pk.days_text([0, 1, 2, 3, 4, 5]), "Mon-Sat")
        self.assertEqual(pk.days_text([0, 1, 2, 5]), "Mon-Wed, Sat")


# --------------------------------------------------------------------------- price_rule

class PriceRule(unittest.TestCase):
    def test_free_and_unknown(self):
        self.assertEqual(pk.price_rule({"free": True}, WED, 120)[0], 0.0)
        cost, basis, fits = pk.price_rule(None, WED, 120)
        self.assertIsNone(cost)
        self.assertTrue(fits)

    def test_hourly_inside_and_outside_window(self):
        rule = {"hourly": 2.0, "daily_max": 18.0, "paid_days": [0, 1, 2, 3, 4, 5], "paid_hours": ["09:00", "18:00"],
                "holidays_free": True}
        self.assertEqual(pk.price_rule(rule, WED, 120)[0], 4.0)
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=17), 180)[0], 2.0)     # only 17-18 paid
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=8), 12 * 60)[0], 18.0)  # capped
        sun = dt.datetime(2026, 10, 18, 11, 0)
        cost, basis, _ = pk.price_rule(rule, sun, 180)
        self.assertEqual(cost, 0.0)
        self.assertIn("free at this time", basis)
        self.assertEqual(pk.price_rule(rule, THANKSGIVING, 120, HOL)[0], 0.0)
        self.assertEqual(pk.price_rule(rule, THANKSGIVING, 120, set())[0], 4.0)

    def test_per_unit_rounding_and_min_charge(self):
        rule = {"hourly": 1.0, "per_min": 15}
        self.assertEqual(pk.price_rule(rule, WED, 50)[0], 1.0)     # 4 units of 15 min
        self.assertEqual(pk.price_rule({"hourly": 2.0, "min_charge": 5.0}, WED, 20)[0], 5.0)   # minimum above the metered $2

    def test_season(self):
        rule = {"hourly": 8.0, "daily_max": 40.0, "season": ["05-15", "09-15"]}
        cost, basis, _ = pk.price_rule(rule, THANKSGIVING, 300)
        self.assertEqual(cost, 0.0)
        self.assertIn("off season", basis)
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 7, 4, 11, 0), 300)[0], 40.0)
        closed = dict(rule, off_season="closed")
        self.assertEqual(pk.price_rule(closed, THANKSGIVING, 300)[:1], (None,))
        self.assertFalse(pk.price_rule(closed, THANKSGIVING, 300)[2])
        winter = {"flat": 10.0, "season": ["12-01", "03-31"]}   # wraps the new year
        self.assertEqual(pk.price_rule(winter, dt.datetime(2027, 1, 10, 9, 0), 60)[0], 10.0)

    def test_tiers_and_flat(self):
        p2 = {"tiers": [{"up_to_min": 60, "cost": 0}, {"up_to_min": 720, "cost": 15}, {"cost": 30}]}
        self.assertEqual(pk.price_rule(p2, SAT, 45)[0], 0.0)
        self.assertEqual(pk.price_rule(p2, SAT, 240)[0], 15.0)
        self.assertEqual(pk.price_rule(p2, SAT, 800)[0], 30.0)
        self.assertEqual(pk.price_rule({"flat": 21.0}, SAT, 300)[0], 21.0)
        town_hall = {"flat": 10.0, "paid_days": [5, 6]}
        self.assertEqual(pk.price_rule(town_hall, WED, 120)[0], 0.0)
        self.assertEqual(pk.price_rule(town_hall, SAT, 120)[0], 10.0)

    def test_free_first_and_evening(self):
        rule = {"hourly": 2.0, "per_min": 30, "free_first_min": 60, "daily_max": 9.0}
        self.assertEqual(pk.price_rule(rule, WED, 50)[0], 0.0)
        self.assertEqual(pk.price_rule(rule, WED, 150)[0], 3.0)     # 90 min paid = 3 half hours
        ev = {"hourly": 1.5, "paid_hours": ["08:00", "18:00"], "evening_flat": 6.0, "evening_from": "18:00", "daily_max": 8.0}
        self.assertEqual(pk.price_rule(ev, WED.replace(hour=17), 180)[0], 7.5)   # 1 h + evening flat

    def test_max_stay(self):
        rule = {"hourly": 2.0, "paid_days": [0, 1, 2, 3, 4], "paid_hours": ["09:00", "18:00"], "max_stay_min": 180}
        self.assertTrue(pk.price_rule(rule, WED, 180)[2])
        self.assertFalse(pk.price_rule(rule, WED, 240)[2])
        self.assertTrue(pk.price_rule(rule, SAT, 300)[2])                        # limit binds only when paid
        self.assertTrue(pk.price_rule(rule, WED.replace(hour=16), 300)[2])       # 2 h inside the window
        self.assertFalse(pk.price_rule(dict(rule, max_stay_applies="always"), SAT, 300)[2])

    def test_periods_wrap_and_overlap(self):
        greenp = {"hourly": 8.0, "per_min": 30, "fill": True, "periods": [
            {"days": [0, 1, 2, 3, 4], "hours": ["07:00", "19:00"], "max": 25.0},
            {"days": [0, 1, 2, 3, 4], "hours": ["17:00", "07:00"], "max": 9.0}]}
        self.assertEqual(pk.price_rule(greenp, WED.replace(hour=8, minute=30), 9 * 60)[0], 25.0)
        self.assertEqual(pk.price_rule(greenp, WED.replace(hour=20), 10 * 60)[0], 9.0)   # 20:00 to 06:00
        cost, _, _ = pk.price_rule(greenp, WED.replace(hour=18), 13 * 60)                 # 18:00 to 07:00 next day
        self.assertEqual(cost, 17.0)   # 1 h of day window ($8) + night cap $9

    def test_holiday_periods(self):
        rule = pk._greenp_rule(GREENP["carparks"][1])     # TTC lot: flat weekdays, free weekends and holidays
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=8), 9 * 60)[0], 6.0)
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=16), 120)[0], 2.0)
        self.assertEqual(pk.price_rule(rule, SAT, 240)[0], 0.0)
        self.assertEqual(pk.price_rule(rule, THANKSGIVING, 240, HOL)[0], 0.0)
        self.assertEqual(pk.price_rule(rule, THANKSGIVING, 240, set())[0], 6.0)

    def test_no_parking_and_usd(self):
        rule = {"hourly": 2.0, "no_parking": [{"days": [0, 1, 2, 3, 4], "hours": ["16:00", "18:00"]}]}
        self.assertFalse(pk.price_rule(rule, WED, 180)[2])
        self.assertTrue(pk.price_rule(rule, SAT, 180)[2])
        self.assertIsNone(pk.price_rule({"hourly": 4.0, "currency": "USD"}, WED, 60)[0])

    def test_greenp_day_maximum(self):
        rule = pk._greenp_rule(GREENP["carparks"][2])
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=8), 4 * 60)[0], 14.0)    # 8 x $2.50 capped at $14
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=8), 60)[0], 5.0)
        self.assertEqual(pk.price_rule(rule, THANKSGIVING, 120, HOL)[0], 10.0)
        self.assertEqual(pk._greenp_rule({"rate_half_hour": "0.00", "rate_details": {"periods": []}}), None)


# --------------------------------------------------------------------------- find_parking

class FindParking(unittest.TestCase):
    def setUp(self):
        self.sleep = mock.patch.object(pk.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def run_find(self, lat, lon, when, stay, routes, opts=None, places=True):
        warnings = []
        fetch = fake_fetch(routes)
        with mock.patch.object(dc, "fetch", fetch):
            if places:
                with PlacesDir():
                    res = pk.find_parking(lat, lon, when, stay, opts or {}, warnings)
            else:
                patch, tmp = empty_places()
                with patch:
                    res = pk.find_parking(lat, lon, when, stay, opts or {}, warnings)
                shutil.rmtree(tmp, ignore_errors=True)
        return res, warnings, fetch.calls

    def burl_routes(self):
        return [("ParkingDowntown/MapServer/2", BURL_LOTS), ("ParkingDowntown/MapServer/1", BURL_STREET),
                ("overpass-api.de", OVERPASS)]

    def test_burlington_saturday(self):
        res, warnings, calls = self.run_find(43.3255, -79.7990, SAT, 180, self.burl_routes(), places=False)
        opts = res["options"]
        self.assertEqual(warnings, [])
        self.assertEqual(res["radius_m"], 800)
        self.assertEqual(res["caveat"], pk.CAVEAT)
        names = [o["name"] for o in opts]
        # free first: City Hall lot is free on Saturday and is official; the OSM church lot (fee=no, no official
        # source) is labelled unverified and ranks as $5.005, so it comes next; the $5 OSM-priced lot ranks as if
        # it cost 25% more ($6.25), so the official $6 Elizabeth St lot comes before it (review parking-4)
        self.assertTrue(names[0].startswith("City Hall Lot"))
        self.assertEqual(opts[0]["cost_text"], "free")
        self.assertTrue(opts[0]["fits_stay"])
        self.assertEqual(opts[0]["confidence"], "official")
        church = [o for o in opts if o["name"] == "Church lot"][0]
        self.assertEqual(church["confidence"], "osm")
        self.assertEqual(church["cost_text"], pk.UNVERIFIED_FREE_TEXT)
        self.assertTrue(church["unverified"])
        self.assertEqual(opts[1]["name"], "Church lot")
        self.assertTrue(opts[2]["name"].startswith("Elizabeth"))
        osm5 = [i for i, o in enumerate(opts) if o["cost"] == 5.0 and o["confidence"] == "osm"]
        self.assertEqual(len(osm5), 1)
        self.assertGreater(osm5[0], 2)                    # the $5 OSM price ranks after the official $6 lot
        self.assertTrue(all(o["cost"] >= 5.0 for o in opts[3:] if o["cost"]))
        # the Elizabeth St lot is paid Mon-Sat; the OSM lot 40 m away merged into it with its capacity
        eliz = [o for o in opts if o["name"].startswith("Elizabeth")][0]
        self.assertEqual(eliz["cost"], 6.0)
        self.assertEqual(eliz["cost_text"], "$6.00 for 3 h")
        self.assertEqual(eliz["capacity"], 120)
        self.assertIn("also in OpenStreetMap", eliz["notes"])
        street = [o for o in opts if o["kind"] == "street"]
        self.assertEqual(len(street), 1)                  # the loading zone is filtered out
        self.assertEqual(street[0]["search_min"], 8)      # downtown street
        self.assertEqual(street[0]["cost"], 6.0)
        self.assertNotIn("Staff Lot, 1 Nowhere", names)   # permit lot filtered
        self.assertFalse(any(o["source_url"].endswith("/way/3") for o in opts))   # private
        unknown = [o for o in opts if o["cost"] is None]
        self.assertEqual(len(unknown), 2)                 # at most 2 unknown-price lots
        self.assertEqual(unknown[0]["cost_text"], "price unknown, check signs")
        # priced lots in order of the cost they rank at: an osm or stale price counts 25% more
        priced = [o["cost"] * (1.0 + pk.UNVERIFIED_PRICE_RANK_PCT if o["confidence"] in ("osm", "stale") else 1.0)
                  for o in opts if o["cost"] is not None and not o["unverified"]]
        self.assertEqual(priced, sorted(priced))
        for o in opts:
            for k in ("name", "lat", "lon", "walk_m", "walk_min", "search_min", "cost", "cost_text", "basis", "kind",
                      "source", "source_url", "confidence", "free", "fits_stay", "max_stay_min", "capacity",
                      "often_full", "reservation", "notes"):
                self.assertIn(k, o)
            self.assertFalse(any(k.startswith("_") for k in o))
        self.assertTrue(any("Overpass" in s for s in res["sources"]))
        over = [c for c in calls if "overpass" in c[0]]
        self.assertEqual(len(over), 1)
        self.assertIn(b"around:", over[0][1] if isinstance(over[0][1], bytes) else json.dumps(over[0][1]).encode())

    def test_walk_estimate(self):
        res, _, _ = self.run_find(43.3255, -79.7990, SAT, 180, self.burl_routes(), places=False)
        church = [o for o in res["options"] if o["name"] == "Church lot"][0]
        straight = dc.th.haversine_m(43.3255, -79.7990, 43.3240, -79.8010)
        self.assertEqual(church["walk_m"], int(round(straight * 1.3)))
        self.assertEqual(church["walk_min"], -(-church["walk_m"] // 75))

    def test_weekday_ranking_and_height(self):
        res, _, _ = self.run_find(43.3255, -79.7990, WED.replace(hour=10), 120, self.burl_routes(),
                                  opts={"height_m": 2.1}, places=False)
        names = [o["name"] for o in res["options"]]
        # the only free lot is OSM fee=no (unverified): official $4 lots come first
        self.assertTrue(names[0].startswith("City Hall Lot"))
        self.assertLess(max(names.index(n) for n in names if n.startswith(("City Hall", "Elizabeth"))), names.index("Church lot"))
        self.assertFalse(any(o["source_url"].endswith("/way/5") for o in res["options"]))   # 1.9 m clearance
        city_hall = [o for o in res["options"] if o["name"].startswith("City Hall")][0]
        self.assertEqual(city_hall["cost"], 4.0)

    def test_curated_place_off_season(self):
        routes = [("overpass-api.de", {"elements": []})]
        res, warnings, _ = self.run_find(43.3125, -81.7569, THANKSGIVING, 300, routes, opts={"holidays": HOL})
        opts = res["options"]
        by = {o["name"]: o for o in opts}
        self.assertEqual(by["North Beach lot"]["cost"], 0.0)
        self.assertIn("off season", by["North Beach lot"]["basis"])
        self.assertEqual(by["North Beach lot"]["confidence"], "curated")
        self.assertEqual(by["Gill Rd lot"]["cost"], 0.0)
        park = by["Far provincial park"]       # kept although far: a park is the destination's own lot
        self.assertEqual(park["cost"], 21.0)
        self.assertEqual(park["kind"], "park")
        self.assertEqual(park["reservation"], "book a day-use permit")
        self.assertTrue(any("off season" in r for r in res["place_rules"]))
        self.assertTrue(any("fill by 11:00" in r for r in res["place_rules"]))
        self.assertEqual(res["reservations"][0]["lead_days"], 5)
        self.assertTrue(any("Test Beach" in s for s in res["sources"]))
        self.assertLess(opts.index(by["Gill Rd lot"]), opts.index(park))

    def test_curated_place_in_season(self):
        routes = [("overpass-api.de", {"elements": []})]
        july = dt.datetime(2026, 7, 4, 11, 0)
        res, _, _ = self.run_find(43.3125, -81.7569, july, 300, routes)
        by = {o["name"]: o for o in res["options"]}
        self.assertEqual(by["North Beach lot"]["cost"], 40.0)
        self.assertEqual(res["options"][0]["name"], "Gill Rd lot")

    def test_widen_when_nothing_priced(self):
        far = {"elements": [{"type": "way", "id": 9, "center": {"lat": 43.6846 + 0.0095, "lon": -80.4307},
                             "tags": {"amenity": "parking", "fee": "no", "name": "Arena lot"}},
                            {"type": "way", "id": 10, "center": {"lat": 43.6850, "lon": -80.4310},
                             "tags": {"amenity": "parking", "fee": "yes"}}]}
        res, warnings, _ = self.run_find(43.6846, -80.4307, SAT, 180, [("overpass-api.de", far)], places=False)
        self.assertEqual(res["radius_m"], 1500)
        self.assertTrue(any("widened" in w for w in warnings))
        self.assertEqual(res["options"][0]["name"], "Arena lot")
        self.assertGreater(res["options"][0]["walk_m"], 800)

    def test_network_down_never_crashes(self):
        res, warnings, _ = self.run_find(43.3255, -79.7990, SAT, 180, [], places=False)
        self.assertEqual(res["options"], [])
        self.assertTrue(any("Burlington parking layer unavailable" in w for w in warnings))
        self.assertTrue(any("OpenStreetMap parking search failed" in w for w in warnings))

    def test_overpass_429_waits_and_retries_once(self):
        state = {"n": 0}

        def busy(url, data):
            state["n"] += 1
            if state["n"] == 1:
                raise http_error(self, url, 429, "Too Many Requests")
            return OVERPASS
        res, warnings, _ = self.run_find(43.6846, -80.4307, SAT, 180, [("overpass-api.de", busy)], places=False)
        self.assertEqual(state["n"], 2)
        pk.time.sleep.assert_called_with(30)
        self.assertFalse(any("OpenStreetMap" in w for w in warnings))

    def test_overpass_429_twice_warns(self):
        def busy(url, data):
            raise http_error(self, url, 429, "Too Many Requests")
        res, warnings, calls = self.run_find(43.6846, -80.4307, SAT, 180, [("overpass-api.de", busy)], places=False)
        self.assertEqual(len([c for c in calls if "overpass" in c[0]]), 2)
        self.assertTrue(any("HTTP 429" in w for w in warnings))

    def test_arcgis_error_reply(self):
        routes = [("ParkingDowntown/MapServer/2", {"error": {"code": 400, "message": "Invalid query"}}),
                  ("ParkingDowntown/MapServer/1", BURL_STREET), ("overpass-api.de", {"elements": []})]
        res, warnings, _ = self.run_find(43.3255, -79.7990, SAT, 180, routes, places=False)
        self.assertTrue(any("Invalid query" in w for w in warnings))
        self.assertEqual(len(res["options"]), 1)

    def test_toronto_greenp_stale_and_occupancy(self):
        routes = [("green-p-parking-2019", GREENP), ("datastore_search", OCCUPANCY), ("overpass-api.de", {"elements": []})]
        res, warnings, _ = self.run_find(43.6453, -79.3806, WED.replace(hour=8, minute=30), 9 * 60, routes, places=False)
        by = {o["name"]: o for o in res["options"]}
        york = by["Green P 40 York Street"]
        self.assertEqual(york["cost"], 25.0)
        self.assertEqual(york["confidence"], "stale")
        self.assertTrue(york["often_full"])               # 92% in the latest quarter
        self.assertIn("(2019 data)", york["cost_text"])
        self.assertIn("2019 rates", york["notes"])
        self.assertEqual(york["kind"], "garage")
        ttc = by["Green P Kipling South Lot - 400 Munster Avenue"]
        self.assertEqual((ttc["kind"], ttc["cost"], ttc["often_full"]), ("park_ride", 6.0, False))
        self.assertNotIn("Green P 20 Charles Street East", by)    # 2.7 km away
        self.assertEqual(res["options"][0]["name"], ttc["name"])
        res2, _, _ = self.run_find(43.6453, -79.3806, WED, 120, routes, opts={"height_m": 2.2}, places=False)
        self.assertNotIn("Green P 40 York Street", [o["name"] for o in res2["options"]])

    def test_dedupe_prefers_official_and_merges(self):
        a = pk.make_lot("Lot A", 43.0, -79.0, "lot", {"hourly": 2.0}, "City", "u", "official", (43.0, -79.0), WED, 60, set())
        b = pk.make_lot("Unnamed public lot", 43.0003, -79.0, "lot", None, "OpenStreetMap", "u", "osm", (43.0, -79.0), WED, 60, set(),
                        capacity=50)
        c = pk.make_lot("Lot C", 43.002, -79.0, "lot", {"free": True}, "OpenStreetMap", "u", "osm", (43.0, -79.0), WED, 60, set())
        out = pk.dedupe([b, a, c])
        self.assertEqual(len(out), 2)
        merged = [x for x in out if x["name"] == "Lot A"][0]
        self.assertEqual((merged["confidence"], merged["capacity"]), ("official", 50))

    def test_rank_order_and_caps(self):
        mk = lambda name, rule, conf="osm", lat=43.0, kind="lot": pk.make_lot(
            name, lat, -79.0, kind, rule, name, "u", conf, (43.0, -79.0), WED, 120, set())
        lots = [mk("u1", None), mk("u2", None, lat=43.001), mk("u3", None, lat=43.002),
                mk("paid", {"hourly": 3.0}), mk("cheap", {"hourly": 1.0}), mk("free-osm", {"free": True}, lat=43.003),
                mk("free-off", {"free": True}, conf="official", lat=43.004),
                mk("short", {"free": True, "max_stay_min": 60}, conf="official")]
        out = [x["name"] for x in pk.rank(lots)]
        self.assertEqual(out, ["free-off", "free-osm", "cheap", "paid", "u1", "u2", "short"])

    def test_curated_loader_tolerates_bad_file(self):
        with PlacesDir() as tmp:
            with open(os.path.join(tmp, "parking-places-bad.json"), "w") as f:
                f.write("{not json")
            warnings = []
            places = pk.load_places(warnings)
        self.assertEqual(len(places), 2)
        self.assertTrue(any("unreadable" in w for w in warnings))


class RealConfig(unittest.TestCase):
    def test_parking_json_shape(self):
        c = dc.load_data("parking")
        self.assertEqual(c["verified"], "2026-10-10")
        ids = [l["id"] for l in c["city_layers"]]
        for want in ("burlington_lots", "burlington_street", "hamilton_lots", "hamilton_meters", "ottawa_street",
                     "ottawa_machines", "ottawa_lots", "mississauga", "oakville", "london_meters", "kingston_lots",
                     "kingston_park_ride", "niagara_falls_lots"):
            self.assertIn(want, ids)
        for l in c["city_layers"]:
            self.assertTrue(l["url"].startswith("https://"))
            self.assertTrue(l["source"].startswith("https://"))
            self.assertIn(l["confidence"], ("official", "stale"))
            for k in ("min_lat", "max_lat", "min_lon", "max_lon"):
                self.assertIn(k, l["bbox"])

    def test_layer_rules_from_real_rows(self):
        c = dc.load_data("parking")
        layers = {l["id"]: l for l in c["city_layers"]}
        r, _ = pk.layer_rule(layers["ottawa_street"], {"HOURLY_RATE": "3.5", "MAX_STAY": "2", "DAYS_IN_EFFECT": "5"})
        self.assertEqual((r["hourly"], r["max_stay_min"]), (3.5, 120))
        self.assertEqual([p["days"] for p in r["periods"]], [[0, 1, 2, 3, 4]])
        r, notes = pk.layer_rule(layers["hamilton_meters"], {"HOURLY_RATE": "$2.00", "TIME_LIMIT_DURATION": "2 hours",
                                                             "RUSH_HOUR_RESTRICTION": "both"})
        self.assertEqual(pk.price_rule(r, dt.datetime(2026, 10, 15, 19, 0), 60)[0], 2.0)   # Thursday meters run to 21:00
        self.assertEqual(pk.price_rule(r, WED.replace(hour=19), 60)[0], 0.0)
        self.assertFalse(pk.price_rule(r, WED.replace(hour=15), 90)[2])                  # 16:00 rush hour
        r, _ = pk.layer_rule(layers["mississauga"], {"Rates": "$1/Hr", "Hourly": "$6 Day (8am-6pm)", "MaxTime": "(No Overnight Parking)",
                                                     "PaidOrFree": "Paid", "LotNumber": 32})
        self.assertEqual(pk.price_rule(r, SAT, 180)[0], 0.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=8), 600)[0], 6.0)
        r, _ = pk.layer_rule(layers["london_meters"], {"FeeRate1Hour": "1.50", "FeeRateDaily": "n/a", "FeeRateEvening": "n/a",
                                                       "HoursOfOperation": "08:00am - 06:00pm (Mon - Sat)", "MaximumDuration": "2 hours"})
        self.assertEqual(pk.price_rule(r, WED, 120)[0], 3.0)
        self.assertIsNone(pk.layer_rule(layers["kingston_lots"], {"MAP_LABEL": "X"})[0])
        r, notes = pk.layer_rule(layers["ottawa_machines"], {
            "RATE_DESC": "$0.25/15 min, Monday / Lundi to / \u00e0 Friday / Vendredi (9:30am - 3:30pm)", "Details": "On-street # 104PH"})
        self.assertFalse(pk.price_rule(r, WED, 120)[2])     # runs into the 15:30 peak-hour restriction
        self.assertTrue(any("peak-hour" in n for n in notes))


# --------------------------------------------------------------------------- fixes after the 2026-10-10 review

FRI_18 = dt.datetime(2026, 10, 16, 18, 0)
OTT = (45.4289, -75.6919)
BYWARD_MACHINE = ("$0.25/3 min 45s, Monday / Lundi to / à Friday / Vendredi ( 8:30am - 7:30pm ) "
                  "Saturday / Samedi ( 10:00am - 7:30pm)")


def real_find(lat, lon, when, stay_min, opts=None, overpass=None):
    """find_parking on the real curated files, everything on the network offline (Overpass answers `overpass`)."""
    warnings = []
    fetch = fake_fetch([("overpass-api.de", overpass or {"elements": []})])
    with mock.patch.object(dc, "fetch", fetch), mock.patch.object(pk.time, "sleep"):
        res = pk.find_parking(lat, lon, when, stay_min, opts or {}, warnings)
    return res, warnings


def by_name(res):
    return {o["name"]: o for o in res["options"]}


def http_error(test, url, code, msg):
    """An HTTPError closed when the test ends (recent Pythons warn about one left open)."""
    e = urllib.error.HTTPError(url, code, msg, {}, io.BytesIO(b""))
    test.addCleanup(e.close)
    return e


def places_file(name):
    """One curated parking-places file, parsed (the file is closed again)."""
    with open(os.path.join(pk.PLACES_DIR, name), encoding="utf-8") as f:
        return json.load(f)


class ParserFixes(unittest.TestCase):
    def test_days_qualify_a_price(self):            # P3
        r, _ = pk.parse_rate_text("$18 a day weekdays")
        self.assertEqual(pk.price_rule(r, WED, 180)[0], 18.0)
        cost, basis, _ = pk.price_rule(r, SAT, 180)
        self.assertIsNone(cost)                      # not free: the price for Saturday is unknown
        self.assertIn("Mon-Fri only", basis)
        r, _ = pk.parse_rate_text("Weekdays $15, Weekends $8")
        self.assertEqual((pk.price_rule(r, WED, 180)[0], pk.price_rule(r, SAT, 180)[0]), (15.0, 8.0))
        r, _ = pk.parse_rate_text("$8 evenings and weekends")
        self.assertIsNone(pk.price_rule(r, WED.replace(hour=10), 180)[0])
        self.assertEqual(pk.price_rule(r, SAT, 180)[0], 8.0)
        # a days token next to hours still opens a paid window
        r, _ = pk.parse_rate_text("Mon-Sat, 9am-6pm $2.00/hr  3hr limit")
        self.assertEqual(r["paid_days"], [0, 1, 2, 3, 4, 5])

    def test_free_after_is_a_free_window(self):     # P3
        r, _ = pk.parse_rate_text("$3/h, $12 max, free after 6pm")
        self.assertEqual(r["paid_hours"], ["00:00", "18:00"])
        self.assertEqual(pk.price_rule(r, WED.replace(hour=10), 120)[0], 6.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=19), 120)[0], 0.0)

    def test_day_price_and_after_price(self):       # P3
        r, _ = pk.parse_rate_text("$20 per day, $10 after 5pm")
        self.assertEqual(pk.price_rule(r, WED.replace(hour=10), 120)[0], 20.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=18), 120)[0], 10.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=10), 600)[0], 20.0)   # day cap

    def test_first_hour_limits_and_events(self):    # P12
        r, _ = pk.parse_rate_text("$5 first hour, $2 each additional hour")
        self.assertEqual((pk.price_rule(r, WED, 180)[0], pk.price_rule(r, WED, 60)[0]), (9.0, 5.0))
        r, _ = pk.parse_rate_text("$2.25/hour, max 3 hours")
        self.assertEqual(r["max_stay_min"], 180)
        self.assertFalse(pk.price_rule(r, WED, 240)[2])
        r, _ = pk.parse_rate_text("$6 Day (7am-6pm), $5 Eve (6pm-midnight)")
        self.assertEqual(pk.price_rule(r, WED.replace(hour=10), 120)[0], 6.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=19), 120)[0], 5.0)
        r, _ = pk.parse_rate_text("$5 Max Overnight Rate (Sun-Thu 6pm-8am)")
        self.assertEqual(pk.price_rule(r, WED.replace(hour=19), 120)[0], 5.0)
        r, notes = pk.parse_rate_text("$40 event")
        self.assertEqual(r, {})
        self.assertTrue(any("event" in n for n in notes))
        r, _ = pk.parse_rate_text("$2/hr, $10 event parking")
        self.assertEqual((r.get("hourly"), r.get("flat")), (2.0, None))

    def test_durations(self):                        # P12
        self.assertEqual(pk.parse_duration("1 day"), 1440)
        self.assertEqual(pk.parse_duration("2:30"), 150)
        self.assertEqual(pk.parse_duration("90", default_unit="min"), 90)
        self.assertEqual(pk.osm_charge_rule({"fee": "yes", "charge": "$2/hr", "maxstay": "4320"})[0]["max_stay_min"], 4320)
        self.assertEqual(pk.osm_charge_rule({"fee": "yes", "charge": "$2/hr", "maxstay": "1 day"})[0]["max_stay_min"], 1440)

    def test_range_wins_over_from(self):
        self.assertEqual(pk.find_hours("from 9am - 5pm"), (540, 1020))
        self.assertEqual(pk.find_hours("If entering between 5 AM and 3 PM"), (300, 900))
        self.assertEqual(pk.find_hours("after 6pm"), (1080, 1440))

    def test_no_overnight_does_not_fit(self):        # P5
        r, notes = pk.parse_rate_text("$1/Hr $6 Day (7am-6pm), $5 Eve (6pm-midnight) (No Overnight Parking)")
        self.assertIn("no overnight parking", notes)
        self.assertFalse(pk.price_rule(r, WED.replace(hour=20), 720)[2])
        self.assertTrue(pk.price_rule(r, WED.replace(hour=8), 600)[2])
        self.assertFalse(pk.price_rule({"free": True, "no_parking": r["no_parking"]}, WED.replace(hour=20), 720)[2])
        self.assertFalse(pk.price_rule(r, THANKSGIVING.replace(hour=22), 360, HOL)[2])   # holidays too

    def test_limit_all_week_binds_always(self):      # P6
        r, _ = pk.parse_rate_text("$1.5/Hr Monday-Sunday 2 Hr Max $5 Max Overnight Rate (Sunday-Thursday 6pm-8am) "
                                  "2 Hr Parking Maximum (Monday - Sunday)")
        self.assertEqual(r["max_stay_applies"], "always")
        self.assertFalse(pk.price_rule(r, SAT.replace(hour=10), 300)[2])
        self.assertTrue(pk.price_rule(r, SAT.replace(hour=10), 90)[2])


class PriceFixes(unittest.TestCase):
    def test_split_windows_merge_for_max_stay(self):  # P6
        rule = {"hourly": 2.0, "max_stay_min": 120, "periods": [{"hours": ["08:00", "12:00"]}, {"hours": ["12:00", "18:00"]}]}
        self.assertFalse(pk.price_rule(rule, WED.replace(hour=10), 240)[2])
        self.assertTrue(pk.price_rule(rule, WED.replace(hour=10), 120)[2])
        fill = {"hourly": 2.0, "fill": True, "max_stay_min": 120, "periods": [{"hours": ["18:00", "08:00"], "max": 5.0}]}
        self.assertFalse(pk.price_rule(fill, SAT.replace(hour=10), 300)[2])   # fill time counts toward the limit

    def test_evening_flat_not_on_top_of_hourly(self):  # P9
        london = {"hourly": 2.0, "daily_max": 10.0, "evening_flat": 6.0, "evening_from": "18:00"}
        self.assertEqual(pk.price_rule(london, WED.replace(hour=19), 180)[0], 6.0)
        self.assertEqual(pk.price_rule({"hourly": 2.0, "evening_flat": 5.0}, WED.replace(hour=19), 120)[0], 5.0)
        self.assertEqual(pk.price_rule(london, WED.replace(hour=16), 180)[0], 10.0)    # 2 h at $2 + evening $6

    def test_daily_max_only(self):                   # P10
        self.assertEqual(pk.price_rule({"daily_max": 12.0}, WED, 2 * 1440)[0], 24.0)
        self.assertEqual(pk.price_rule({"daily_max": 12.0, "holidays_free": True}, THANKSGIVING, 120, HOL)[0], 0.0)
        self.assertEqual(pk.price_rule({"daily_max": 9.0, "free_first_min": 60}, WED, 30)[0], 0.0)
        self.assertFalse(pk.price_rule({"daily_max": 12.0, "max_stay_min": 120}, WED, 300)[2])
        r, _ = pk.parse_rate_text("5 CAD/day;50 CAD/year")
        self.assertEqual(pk.price_rule(r, WED, 3 * 1440)[0], 15.0)

    def test_flat_and_tiers_checks(self):            # P10
        self.assertEqual(pk.price_rule({"flat": 12.0, "holidays_free": True}, THANKSGIVING, 120, HOL)[0], 0.0)
        self.assertEqual(pk.price_rule({"flat": 9.0, "free_first_min": 60}, WED, 30)[0], 0.0)
        tiers = {"tiers": [{"up_to_min": 60, "cost": 4.0}, {"cost": 8.0}], "free_first_min": 30}
        self.assertEqual(pk.price_rule(tiers, WED, 20)[0], 0.0)
        self.assertEqual(pk.price_rule(dict(tiers, paid_days=[5, 6]), WED, 50)[0], 0.0)
        self.assertFalse(pk.price_rule({"flat": 9.0, "max_stay_min": 60}, WED, 120)[2])

    def test_season_per_day(self):                   # P13
        rule = {"hourly": 2.0, "daily_max": 10.0, "season": ["05-15", "09-07"]}
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 9, 7, 10, 0), 3 * 1440)[0], 10.0)
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 5, 13, 10, 0), 3 * 1440)[0], 20.0)
        closed = dict(rule, off_season="closed")
        self.assertIsNone(pk.price_rule(closed, dt.datetime(2026, 9, 7, 10, 0), 3 * 1440)[0])

    def test_season_anchors(self):                   # W1
        self.assertEqual(pk.anchor_date("labour_day", 2027), dt.date(2027, 9, 6))
        self.assertEqual(pk.anchor_date("victoria_day", 2027), dt.date(2027, 5, 24))
        self.assertEqual(pk.anchor_date("fri_before_victoria_day", 2027), dt.date(2027, 5, 21))
        self.assertEqual(pk.anchor_date("thanksgiving", 2027), dt.date(2027, 10, 11))
        self.assertEqual(pk.anchor_date("civic_holiday", 2027), dt.date(2027, 8, 2))
        self.assertEqual(pk.anchor_date("easter_monday", 2027), dt.date(2027, 3, 29))
        rule = {"hourly": 8.0, "daily_max": 30.0, "season": ["05-15", "labour_day"]}
        self.assertEqual(pk.price_rule(rule, dt.datetime(2027, 9, 7, 11, 0), 300)[0], 0.0)
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 9, 7, 11, 0), 300)[0], 30.0)

    def test_off_season_extras(self):                # W4, W7
        sauble = {"hourly": 7.0, "season": ["06-01", "labour_day"], "off_season_max_stay_min": 120}
        cost, basis, fits = pk.price_rule(sauble, SAT, 180)
        self.assertEqual((cost, fits), (0.0, False))
        self.assertIn("2 h max off season", basis)
        bruce = {"flat": 21.0, "season": ["06-19", "09-07"], "off_season_flat": 17.5}
        self.assertEqual(pk.price_rule(bruce, SAT, 300)[0], 17.5)
        self.assertEqual(pk.price_rule(bruce, dt.datetime(2026, 7, 18, 9, 0), 300)[0], 21.0)

    def test_tax(self):                              # W5
        rule = {"hourly": 6.0, "per_min": 20, "min_charge": 12.0, "daily_max": 35.0, "tax": 0.13}
        cost, basis, _ = pk.price_rule(rule, SAT, 360)
        self.assertEqual(cost, 39.55)
        self.assertIn("13% HST", basis)

    def test_entry_flats(self):                      # P4 (d)
        rule = {"hourly": 3.0, "per_min": 30, "periods": [
            {"days": [0, 1, 2, 3, 4], "hours": ["05:00", "02:00"], "flat": 7.0, "entry": True},
            {"days": [0, 1, 2, 3, 4], "hours": ["15:00", "02:00"], "flat": 2.0, "entry": True}]}
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=19), 120)[0], 2.0)    # the evening rate
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=8), 120)[0], 7.0)
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=17), 17 * 60)[0], 9.0)  # stays into Thursday's window

    def test_months_and_window_free_first(self):     # W6
        rule = {"hourly": 1.25, "periods": [
            {"days": [5], "hours": ["09:00", "18:00"], "months": [12], "free_first_min": 240},
            {"days": [5], "hours": ["09:00", "20:00"], "months": [7]}]}
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 12, 12, 13, 0), 180)[0], 0.0)
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 12, 12, 13, 0), 360)[0], 1.25)
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 7, 18, 18, 30), 60)[0], 1.25)
        self.assertEqual(pk.months_text([11, 12, 1, 2, 3]), "Nov-Mar")


class GreenPFixes(unittest.TestCase):
    CP = {
        "528": {"id": "528", "rate_half_hour": "1.00", "rate_details": {"periods": [
            {"title": "Monday - Friday ", "rates": [{"when": "Day Maximum (7am - 3pm)", "rate": "NO MAXIMUM"}]},
            {"title": "FREE PARKING", "rates": [{"when": "Monday - Friday", "rate": "AFTER 3pm"},
                                                {"when": "Saturday & Sunday", "rate": "ALL DAY"}]}], "addenda": []}},
        "829": {"id": "829", "rate_half_hour": "0.00", "rate_details": {"periods": [
            {"title": "Monday - Friday", "rates": [{"when": "If entering between 5 AM and 3 PM", "rate": "$5.00"},
                                                   {"when": "If entering between 3 PM and 2 AM", "rate": "$2.00"}]},
            {"title": "Saturday - Sunday & Holidays", "rates": [{"when": "(5am - 3pm)", "rate": "FREE"},
                                                                {"when": "(3pm - 2am)", "rate": "FREE"}]}],
            "addenda": [" NO PARKING FROM 2AM - 5AM EVERY NIGHT "]}},
        "26": {"id": "26", "rate_half_hour": "3.50", "rate_details": {"periods": [
            {"title": "Day Maximum until 6 PM - $30.00 (Entering 7 AM - 10 AM).\r\nDay Maximum until 6 PM - $20.00 (Entering 10 AM - 6 PM).",
             "rates": [{"when": "Night Maximum (6 pm - 7 am)", "rate": "$7.00"}]},
            {"title": "Saturday - Sunday & Holidays", "rates": [{"when": "Day Maximum (7am - 6pm)", "rate": "$7.00"},
                                                                {"when": "Night Maximum (6pm - 7am)", "rate": "$7.00"}]}], "addenda": []}},
        "833": {"id": "833", "rate_half_hour": "1.50", "rate_details": {"periods": [
            {"title": "Monday - Friday ", "rates": [{"when": "Flat Rate (5am - 2am)", "rate": "$7.00"},
                                                    {"when": "Evening Rate (3pm - 2am)", "rate": "$2.00"}]},
            {"title": "Weekend and Statutory Holidays ", "rates": [{"when": "Weekend and Statutory Holidays", "rate": "Free"}]}],
            "addenda": []}},
        "110": {"id": "110", "rate_half_hour": "1.50", "rate_details": {"periods": [
            {"title": "nday - Sunday & Holidays", "rates": [{"when": "Day Maximum (7am - 6pm)", "rate": "$9.00"},
                                                           {"when": "Night Maximum (6pm - 7am)", "rate": "$4.00"}]}], "addenda": []}}}

    def rule(self, cid):
        return pk._greenp_rule(self.CP[cid])

    def test_free_parking_title_is_a_window(self):
        r = self.rule("528")
        self.assertEqual(pk.price_rule(r, WED.replace(hour=10), 120)[0], 4.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=16), 120)[0], 0.0)
        self.assertEqual(pk.price_rule(r, SAT, 240)[0], 0.0)

    def test_entering_between_and_no_parking(self):
        r = self.rule("829")
        self.assertEqual(pk.price_rule(r, WED.replace(hour=8, minute=50), 540)[0], 5.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=16), 120)[0], 2.0)
        self.assertFalse(pk.price_rule(r, WED.replace(hour=23), 240)[2])          # 02:00-05:00 no parking

    def test_title_without_days_and_day_caps(self):
        r = self.rule("26")
        self.assertEqual(pk.price_rule(r, WED.replace(hour=8, minute=50), 540)[0], 30.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=11), 300)[0], 20.0)
        self.assertLess(pk.price_rule(r, WED.replace(hour=8, minute=50), 3 * 1440)[0], 130.0)
        self.assertEqual(pk.price_rule(r, SAT, 540)[0], 14.0)

    def test_weekend_free_and_evening_entry(self):
        r = self.rule("833")
        self.assertEqual(pk.price_rule(r, SAT, 240)[0], 0.0)
        self.assertEqual(pk.price_rule(r, THANKSGIVING, 120, HOL)[0], 0.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=19), 120)[0], 2.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=8), 120)[0], 7.0)

    def test_typo_title(self):
        r = self.rule("110")
        self.assertEqual(pk.price_rule(r, SAT, 540)[0], 13.0)     # 11:00-18:00 capped $9 + 18:00-20:00 $4 night cap

    def test_free_results_carry_the_stale_label(self):
        routes = [("green-p-parking-2019", {"carparks": [dict(self.CP["833"], address="3950 Keele Street", lat="43.6453",
                                                               lng="-79.3806", carpark_type="surface", is_ttc=False)]}),
                  ("datastore_search", OCCUPANCY), ("overpass-api.de", {"elements": []})]
        warnings = []
        patch, tmp = empty_places()
        with patch, mock.patch.object(dc, "fetch", fake_fetch(routes)):
            res = pk.find_parking(43.6453, -79.3806, SAT, 120, {}, warnings)
        shutil.rmtree(tmp, ignore_errors=True)
        lot = by_name(res)["Green P 3950 Keele Street"]
        self.assertEqual(lot["cost_text"], "free (2019 data)")

    def test_occupancy_url_newest_first(self):       # P15
        self.assertIn("sort=_id%20desc", dc.load_data("parking")["toronto"]["occupancy_url"])


class LayerFixes(unittest.TestCase):
    def layers(self):
        return {l["id"]: l for l in dc.load_data("parking")["city_layers"]}

    def test_burlington_free_text(self):             # P8
        lay = self.layers()["burlington_lots"]
        martha = {"LOTNAME": "Martha Street Lot", "LOT_TYPE": "Public", "PAID_AVAILABILITY": "n/a", "HOURLY_RATE": "$2.00",
                  "FREE_PARKING": "Mon-Fri after 6pm, Sat, Sun, Holidays"}
        r, _ = pk.layer_rule(lay, martha)
        self.assertEqual(pk.price_rule(r, dt.datetime(2026, 10, 18, 11, 0), 180)[0], 0.0)    # Sunday
        self.assertEqual(pk.price_rule(r, WED.replace(hour=19), 180)[0], 0.0)
        self.assertEqual(pk.price_rule(r, WED.replace(hour=10), 120)[0], 4.0)
        west = {"LOTNAME": "Waterfront Park Lot West", "LOT_TYPE": "Public", "PAID_AVAILABILITY": "7 Days, 9am - 4pm",
                "HOURLY_RATE": "$2.00", "FREE_PARKING": "Midnight - 9 am; Reserved Parking  4 pm - Midnight"}
        r, notes = pk.layer_rule(lay, west)
        self.assertFalse(pk.price_rule(r, WED.replace(hour=17), 180)[2])
        self.assertTrue(pk.price_rule(r, WED.replace(hour=10), 180)[2])
        self.assertTrue(any("reserved" in n for n in notes))

    def test_ottawa_street_default_to_1930(self):    # P1
        r, _ = pk.layer_rule(self.layers()["ottawa_street"], {"HOURLY_RATE": "4", "MAX_STAY": "2", "DAYS_IN_EFFECT": "6"})
        self.assertEqual(pk.price_rule(r, FRI_18, 60)[0], 4.0)

    def test_per_feature_edit_date(self):            # P11
        lay = self.layers()["hamilton_lots"]
        today = dt.date(2026, 10, 14)
        self.assertFalse(pk._stale(lay, today, pk._feature_date(1753920000000)))   # 2025-07-31
        self.assertTrue(pk._stale(lay, today, pk._feature_date(1656547200000)))    # 2022-06-30
        self.assertTrue(pk._stale(lay, today))                                     # layer date 2023-03
        routes = [("ParkingInventory_FA/FeatureServer/2", {"features": [
            {"attributes": {"LOCATION": "York Blvd", "LOT_NUMBER": 37, "ADDRESS": "1 York", "RATE_PER_HOUR": "$2.00",
                            "RATE_PER_DAY": "$10.00", "LAST_EDITED_DATE": 1776297600000},
             "geometry": {"x": -79.8711, "y": 43.2557}},
            {"attributes": {"LOCATION": "King St", "LOT_NUMBER": 5, "ADDRESS": "5 King", "RATE_PER_HOUR": "$2.00",
                            "RATE_PER_DAY": "$10.00", "LAST_EDITED_DATE": 1656547200000},
             "geometry": {"x": -79.8715, "y": 43.2559}}]}), ("overpass-api.de", {"elements": []})]
        patch, tmp = empty_places()
        warnings = []
        with patch, mock.patch.object(dc, "fetch", fake_fetch(routes)):
            res = pk.find_parking(43.2557, -79.8711, WED, 120, {}, warnings)
        shutil.rmtree(tmp, ignore_errors=True)
        conf = {o["name"]: (o["confidence"], o["notes"]) for o in res["options"]}
        self.assertEqual(conf["York Blvd lot 37, 1 York"][0], "official")
        self.assertEqual(conf["King St lot 5, 5 King"][0], "stale")
        self.assertIn("data last edited 2022-06", conf["King St lot 5, 5 King"][1])


class MergeAndRankFixes(unittest.TestCase):
    def test_machine_hours_beat_default_hours(self):   # P1
        seg_rule = {"hourly": 4.0, "per_min": 15, "max_stay_applies": "paid",
                    "periods": [{"days": [0, 1, 2, 3, 4], "hours": ["08:30", "17:30"]}], "hours_note": "default hours"}
        seg = pk.make_lot("York (Dalhousie to William)", 45.42890, -75.69190, "street", seg_rule, "Ottawa open data (paid on-street spaces)",
                          "u", "official", OTT, FRI_18, 60, set())
        mrule, _ = pk.parse_rate_text(BYWARD_MACHINE)
        mach = pk.make_lot("Pay machine, 71 York St", 45.42900, -75.69200, "street", mrule, "Ottawa open data (pay machines)",
                           "u", "official", OTT, FRI_18, 60, set())
        self.assertEqual(seg["cost"], 0.0)
        out = pk.dedupe([seg, mach])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["cost"], 4.0)
        self.assertEqual(out[0]["name"], "Pay machine, 71 York St")
        # same confidence, nearer one free from its own data: the paid reading still wins (conservative)
        a = pk.make_lot("A", 45.4289, -75.6919, "street", {"free": True}, "S1", "u", "official", OTT, FRI_18, 60, set())
        b = pk.make_lot("B", 45.4290, -75.6919, "street", {"hourly": 3.0}, "S2", "u", "official", OTT, FRI_18, 60, set())
        self.assertEqual(pk.dedupe([a, b])[0]["cost"], 3.0)

    def test_unverified_free_ranking(self):            # K8, P2
        mk = lambda name, rule, conf="official", lat=43.0: pk.make_lot(
            name, lat, -79.0, "lot", rule, name, "u", conf, (43.0, -79.0), WED, 120, set())
        plain = mk("osm free", {"free": True}, "osm", 43.001)
        plain["_unverified_free"] = "plain"
        fac = mk("facility", {"free": True}, "osm", 43.0005)
        fac["_unverified_free"] = "facility"
        lots = [mk("unknown", None, "osm"), fac, plain, mk("p8", {"flat": 8.0}), mk("p4", {"flat": 4.0}, "curated"),
                mk("p5", {"flat": 5.0}), mk("verified free", {"free": True}, "official", 43.002)]
        self.assertEqual([x["name"] for x in pk.rank(lots)], ["verified free", "p4", "p5", "osm free", "p8", "facility", "unknown"])

    def test_osm_names_and_labels(self):               # K8, P2, P14
        ov = {"elements": [
            {"type": "way", "id": 549844123, "center": {"lat": 45.4300, "lon": -75.6890},
             "tags": {"amenity": "parking", "operator": "City of Ottawa", "access": "permissive", "fee": "no", "ref": "4912",
                      "addr:housenumber": "172", "addr:street": "Guigues Avenue", "source:date": "2017-12-28"}},
            {"type": "way", "id": 2, "center": {"lat": 45.4280, "lon": -75.6930}, "tags": {"amenity": "parking", "fee": "no", "name": "Church lot"}},
            {"type": "way", "id": 3, "center": {"lat": 45.4295, "lon": -75.6925}, "tags": {"amenity": "parking"}},
            {"type": "way", "id": 4, "center": {"lat": 45.4283, "lon": -75.6912}, "tags": {"amenity": "parking", "access": "yes"}}]}
        patch, tmp = empty_places()
        with patch, mock.patch.object(pk, "city_lots", lambda *a: ([], [])):
            res, _ = real_find(OTT[0], OTT[1], WED, 120, overpass=ov)
        shutil.rmtree(tmp, ignore_errors=True)
        names = by_name(res)
        fac = names["City of Ottawa facility lot, 172 Guigues Avenue"]
        self.assertEqual(fac["cost_text"], pk.FACILITY_FREE_TEXT)
        self.assertEqual(names["Church lot"]["cost_text"], pk.UNVERIFIED_FREE_TEXT)
        self.assertTrue(any(n.endswith("(access not tagged)") and n.startswith("Lot ") for n in names))
        self.assertTrue(any(n.startswith("Public lot ") for n in names))
        order = [o["name"] for o in res["options"]]
        self.assertLess(order.index("Church lot"), order.index(fac["name"]))

    def test_overpass_busy_once_per_run(self):         # UX14
        calls = {"n": 0}

        def busy(url, data):
            calls["n"] += 1
            raise http_error(self, url, 504, "Gateway Timeout")
        warnings = []
        patch, tmp = empty_places()
        with patch, mock.patch.object(dc, "fetch", fake_fetch([("overpass-api.de", busy)])), \
                mock.patch.object(pk.time, "sleep") as sl, mock.patch.object(pk, "_cached_reply", lambda u, q: None):
            pk.find_parking(43.6846, -80.4307, SAT, 180, {}, warnings)
            pk.find_parking(43.6846, -80.4307, SAT.replace(hour=12), 180, {}, warnings)
        shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(calls["n"], 2)      # the second search in the same run skips Overpass
        self.assertEqual(sl.call_count, 1)


class CuratedDataFixes(unittest.TestCase):
    def test_ferry_not_at_union(self):                 # K7, P7
        res, _ = real_find(43.6453, -79.3806, WED, 180)
        self.assertFalse(any("ferry" in r.lower() for r in res["place_rules"]))
        self.assertFalse(any("ferry" in r["text"].lower() for r in res["reservations"]))
        res, _ = real_find(43.6406, -79.3756, WED, 180)
        self.assertTrue(any("ferry" in r.lower() for r in res["place_rules"]))

    def test_grand_bend_free_lots_listed(self):        # W2, W1
        res, warnings = real_find(43.3140, -81.7640, dt.datetime(2027, 7, 17, 11, 0), 300)
        names = by_name(res)
        self.assertEqual(names["Gill Rd lot"]["cost"], 0.0)
        self.assertEqual(names["Municipal Dr lot"]["cost"], 0.0)
        self.assertEqual(res["options"][0]["cost"], 30.0)   # walkable first, free ones after
        self.assertTrue(any("free parking a longer walk away" in w for w in warnings))
        res, _ = real_find(43.3140, -81.7640, dt.datetime(2027, 9, 7, 11, 0), 300)
        self.assertEqual(by_name(res)["Mid Town lot (HotSpot zone 5723)"]["cost"], 0.0)   # Labour Day 2027 is Sep 6

    def test_collingwood_anchors_and_free_days(self):  # W1, W10
        for day, want in (((2027, 5, 16), 0.0), ((2027, 10, 12), 0.0), ((2027, 5, 22), 40.0)):
            res, _ = real_find(44.5078, -80.2040, dt.datetime(*day, 13, 0), 240)
            self.assertEqual(by_name(res)["Sunset Point Park lot"]["cost"], want, day)
        res, _ = real_find(44.5010, -80.2165, dt.datetime(2027, 8, 2, 11, 0), 120)
        self.assertEqual(by_name(res)["Ste. Marie St municipal lot"]["cost"], 0.0)
        res, _ = real_find(44.5010, -80.2165, dt.datetime(2027, 8, 3, 11, 0), 120)
        self.assertEqual(by_name(res)["Ste. Marie St municipal lot"]["cost"], 2.0)

    def test_elora_gorge(self):                        # W3
        res, _ = real_find(43.6720, -80.4467, dt.datetime(2026, 10, 24, 11, 0), 180)
        self.assertNotIn("Elora Gorge Conservation Area (day use)", by_name(res))
        self.assertTrue(any("Elora Gorge" in r and "closed for the season" in r for r in res["place_rules"]))
        res, _ = real_find(43.6720, -80.4467, dt.datetime(2027, 7, 17, 11, 0), 180)
        g = by_name(res)["Elora Gorge Conservation Area (day use)"]
        self.assertTrue(g["per_person"])
        self.assertIn("per adult", g["cost_text"])
        res, _ = real_find(43.6720, -80.4467, dt.datetime(2027, 7, 17, 11, 0), 180,
                           opts={"party": {"adults": 2, "children": 2}})
        self.assertEqual(by_name(res)["Elora Gorge Conservation Area (day use)"]["cost"], 27.0)

    def test_sauble_off_season_limit(self):            # W4, W11
        res, _ = real_find(44.6325, -81.2700, SAT.replace(hour=10), 180)
        lot = [o for o in res["options"] if o["name"] == "Lakeshore Blvd N beach parking"][0]
        self.assertEqual(lot["cost"], 0.0)
        self.assertFalse(lot["fits_stay"])
        data = places_file("parking-places-west.json")
        sb = [p for p in data["places"] if p["id"] == "sauble-beach"][0]
        self.assertIsNone([l for l in sb["lots"] if l["name"] == "Lakeshore Blvd N beach parking"][0]["osm"])

    def test_wasaga_hst_and_beach_area(self):          # W5, W8
        res, _ = real_find(44.5205, -80.0215, dt.datetime(2027, 7, 17, 10, 0), 360)
        names = by_name(res)
        self.assertEqual(names["Nancy Lot (town)"]["cost"], 39.55)
        self.assertEqual(names["Third Street Lot (town, premium)"]["cost"], 40.68)
        self.assertIn("season dates are the 2026 dates", names["Nancy Lot (town)"]["notes"])
        res, _ = real_find(44.5160, -80.0267, dt.datetime(2026, 7, 18, 10, 0), 240)
        self.assertEqual(by_name(res)["Beach Area 1 and 2 lot (town from 2026)"]["cost"], 27.12)   # $24 + HST
        self.assertFalse(any("New Wasaga) need" in r for r in res["place_rules"]))

    def test_stratford_winter(self):                   # W6
        st = (43.3697, -80.9822)
        res, _ = real_find(st[0], st[1], dt.datetime(2026, 11, 18, 18, 0), 120)
        self.assertEqual(by_name(res)["Lower Erie Lot"]["cost"], 0.0)
        res, _ = real_find(st[0], st[1], dt.datetime(2026, 12, 12, 13, 0), 180)
        self.assertEqual(by_name(res)["Lower Erie Lot"]["cost"], 0.0)
        res, _ = real_find(st[0], st[1], dt.datetime(2027, 7, 17, 18, 30), 60)
        self.assertEqual(by_name(res)["Lower Erie Lot"]["cost"], 1.25)
        self.assertIn("George St lot", by_name(res))

    def test_bruce_off_season(self):                   # W7
        res, _ = real_find(45.229, -81.479, SAT.replace(hour=10), 300)
        lot = by_name(res)["Halfway Log Dump lot"]
        self.assertEqual(lot["cost"], 17.5)
        self.assertIsNone(lot["reservation"])
        self.assertEqual(res["reservations"], [])
        res, _ = real_find(45.229, -81.479, dt.datetime(2026, 7, 18, 9, 0), 300)
        self.assertEqual(by_name(res)["Halfway Log Dump lot"]["cost"], 21.0)
        self.assertTrue(res["reservations"])

    def test_tobermory_legion_lot_paid(self):          # W9
        res, _ = real_find(45.253, -81.660, dt.datetime(2027, 7, 17, 11, 0), 240)
        names = by_name(res)
        self.assertNotIn("Free Public Parking (Legion St)", names)
        self.assertEqual(names["Legion St lot (all-day)"]["cost"], 15.0)
        self.assertEqual(names["Bay St S harbour street parking"]["cost"], 12.0)

    def test_spencer_gorge(self):                      # E1, E8
        with mock.patch.object(pk, "_today", lambda: dt.date(2026, 10, 10)):
            res, warnings = real_find(43.2785, -79.9795, dt.datetime(2026, 10, 10, 11, 0), 240)
            self.assertTrue(any("same-day visit will be turned away" in w for w in warnings))
            self.assertIn("no same-day", res["reservations"][0]["text"])
            res, warnings = real_find(43.2785, -79.9795, dt.datetime(2026, 10, 17, 11, 0), 240)
            self.assertFalse(any("same-day" in w for w in warnings))
            lot = by_name(res)["Webster's Falls lot (28 Fallsview Rd)"]
            self.assertEqual((lot["cost"], lot["fits_stay"]), (32.0, True))
            self.assertIn("per passenger", lot["cost_text"])
            res, _ = real_find(43.2785, -79.9795, dt.datetime(2026, 10, 17, 11, 0), 120, opts={"party": {"adults": 3}})
            self.assertEqual(by_name(res)["Webster's Falls lot (28 Fallsview Rd)"]["cost"], 32.0)   # 21 + 2 x 5.50
            res, _ = real_find(43.2785, -79.9795, dt.datetime(2027, 7, 18, 11, 0), 120)
            self.assertEqual(res["reservations"], [])

    def test_conservation_halton_per_person(self):     # E2
        res, _ = real_find(43.505, -79.942, SAT, 180)
        lot = by_name(res)["Kelso main lot"]
        self.assertTrue(lot["per_person"])
        self.assertTrue(lot["cost_text"].startswith("$11.87 per adult"))
        res, _ = real_find(43.505, -79.942, SAT, 180, opts={"party": {"adults": 2, "children": 2}})
        self.assertEqual(by_name(res)["Kelso main lot"]["cost"], 40.12)

    def test_niagara_winter_and_wego(self):            # E3, E4, E7
        res, _ = real_find(43.0790, -79.0780, dt.datetime(2027, 1, 16, 11, 0), 240, opts={"max_walk_m": 3000})
        names = by_name(res)
        self.assertNotIn("Niagara Parks Lot C, Rapidsview (WEGO park and ride)", names)
        self.assertNotIn("City Lot 5, College Cres", names)
        self.assertIn("January is usually $20", names["Niagara Parks Lot A, Falls Parking (Table Rock)"]["notes"])
        self.assertTrue(any("$16 per adult" in r for r in res["place_rules"]))
        self.assertFalse(any(r.startswith("Niagara Falls (Niagara Parks, Clifton Hill, Fallsview): Cheapest") for r in res["place_rules"]))
        res, _ = real_find(43.0790, -79.0780, dt.datetime(2027, 7, 17, 11, 0), 240)
        self.assertEqual(by_name(res)["Niagara Parks Lot C, Rapidsview (WEGO park and ride)"]["cost"], 10.0)

    def test_byward_garage(self):                      # E5
        data = places_file("parking-places-east.json")
        rule = [l for p in data["places"] for l in p["lots"] if l["name"].startswith("ByWard Market Garage")][0]["rule"]
        sat = dt.datetime(2026, 10, 17)
        self.assertEqual(pk.price_rule(rule, sat.replace(hour=17), 180)[0], 11.5)
        self.assertEqual(pk.price_rule(rule, sat.replace(day=18, hour=11), 180)[0], 9.0)
        self.assertEqual(pk.price_rule(rule, sat.replace(hour=20), 120)[0], 6.0)
        self.assertEqual(pk.price_rule(rule, sat.replace(hour=12), 150)[0], 10.0)
        cost, _, fits = pk.price_rule(rule, sat.replace(hour=20), 600)
        self.assertEqual((cost, fits), (8.0, True))
        self.assertFalse(pk.price_rule(rule, sat.replace(hour=10), 240)[2])

    def test_kingston_sunday(self):                    # E6
        sun = dt.datetime(2026, 7, 19, 10, 0)
        res, _ = real_find(44.2300, -76.4812, sun, 480)
        names = by_name(res)
        self.assertEqual(names["Hanson Memorial Garage"]["cost"], 4.0)
        self.assertEqual(names["Ontario and Brock Lot"]["cost"], 6.25)
        data = places_file("parking-places-east.json")
        ob = [l for p in data["places"] for l in p["lots"] if l["name"] == "Ontario and Brock Lot"][0]["rule"]
        self.assertEqual(pk.price_rule(ob, dt.datetime(2026, 7, 18, 15, 0), 240)[0], 13.25)
        self.assertEqual(pk.price_rule(ob, dt.datetime(2026, 7, 18, 11, 0), 240)[0], 14.0)

    def test_wonderland_low_confidence(self):          # E10
        res, _ = real_find(43.84208, -79.542508, dt.datetime(2027, 7, 17, 11, 0), 300)
        lot = by_name(res)["Canada's Wonderland general parking"]
        self.assertEqual(lot["confidence"], "low")
        self.assertIn("reseller", lot["notes"])

    def test_exhibition_west_lots(self):               # E12
        res, _ = real_find(43.6305, -79.4155, dt.datetime(2026, 10, 14, 10, 0), 180)
        names = by_name(res)
        self.assertEqual(names["Exhibition Place Lot 855 (west end)"]["cost"], 15.0)
        self.assertEqual(res["options"][0]["name"], "Exhibition Place Lot 855 (west end)")
        res, _ = real_find(43.6305, -79.4155, dt.datetime(2027, 3, 10, 10, 0), 180)
        self.assertIn("2026 rates", by_name(res)["Exhibition Place Lot 852"]["notes"])


if __name__ == "__main__":
    unittest.main()
