"""Offline tests: curated lot hours (no parking, no overnight, lot opening hours) are structured no_parking rules,
so price_rule marks a stay that overlaps them as not fitting. Reads the bundled curated data; no network.

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
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import parking as pk  # noqa: E402

SUN_EVE = dt.datetime(2026, 10, 11, 20, 39)   # the audit run: parked 20:39 for 254 min, to 00:53
TABLE_ROCK = (43.07644, -79.079956)
GRAND_BEND_BEACH = (43.3125, -81.7569)

# lots whose notes say "no overnight" without hours; left out on purpose (hours would be invented)
NO_HOURS_STATED = {"P2 Village Base (paid)"}


def lot(place_id, name):
    for p in pk.load_places():
        if p.get("id") == place_id:
            for x in p.get("lots") or []:
                if x.get("name") == name:
                    return x
    raise AssertionError("curated lot not found: %s / %s" % (place_id, name))


def fits(place_id, name, arrive, stay_min):
    return pk.price_rule(lot(place_id, name)["rule"], arrive, stay_min)[2]


class NiagaraHours(unittest.TestCase):
    def test_parks_lots_across_midnight_do_not_fit(self):
        for name in ("Niagara Parks Lot A, Falls Parking (Table Rock)", "Queen Victoria Place lot",
                     "Niagara Parks Lot B, Floral Showhouse", "Dufferin Islands lot", "Whirlpool Aero Car lot"):
            self.assertFalse(fits("niagara-falls", name, SUN_EVE, 254), name)
            self.assertFalse(fits("niagara-falls", name, dt.datetime(2026, 10, 12, 1, 0), 120), name)

    def test_parks_lot_daytime_fits(self):
        cost, _b, ok = pk.price_rule(lot("niagara-falls", "Niagara Parks Lot A, Falls Parking (Table Rock)")["rule"],
                                     dt.datetime(2026, 10, 14, 10, 0), 240)
        self.assertTrue(ok)
        self.assertEqual(cost, 30.0)   # October flat
        self.assertTrue(fits("niagara-falls", "Queen Victoria Place lot", dt.datetime(2026, 10, 14, 18, 0), 360))

    def test_city_lots_03_to_08(self):
        for name in ("City Lot 4, Ellen Ave", "City Lot 20, Palmer Ave", "City Lot 18, Kitchener St"):
            self.assertFalse(fits("niagara-falls", name, dt.datetime(2026, 10, 14, 22, 0), 330), name)
            self.assertTrue(fits("niagara-falls", name, dt.datetime(2026, 10, 14, 9, 0), 300), name)
            self.assertTrue(fits("niagara-falls", name, SUN_EVE, 254), name)   # leaves 00:53, before 03:00

    def test_curated_ranking_sees_the_ban(self):
        lots, _rules, _res, _src = pk.curated(TABLE_ROCK[0], TABLE_ROCK[1], SUN_EVE, 254, set(), 2000, [])
        by = {x["name"]: x for x in lots}
        self.assertFalse(by["Niagara Parks Lot A, Falls Parking (Table Rock)"]["fits_stay"])
        self.assertFalse(by["Queen Victoria Place lot"]["fits_stay"])
        self.assertIn("00:00 to 06:00", by["Niagara Parks Lot B, Floral Showhouse"]["notes"])
        day, _r, _s, _o = pk.curated(TABLE_ROCK[0], TABLE_ROCK[1], dt.datetime(2026, 10, 14, 11, 0), 240, set(), 2000, [])
        self.assertTrue({x["name"]: x for x in day}["Niagara Parks Lot A, Falls Parking (Table Rock)"]["fits_stay"])


class GrandBendHours(unittest.TestCase):
    def test_beach_lot_open_06_to_23_does_not_fit_overnight(self):
        night = dt.datetime(2026, 7, 18, 19, 0)
        for name in ("Main Beach lot (HotSpot zone 5720)", "North Beach lot (HotSpot zone 5721)", "Gill Rd lot"):
            self.assertFalse(fits("grand-bend", name, night, 15 * 60), name)
            self.assertFalse(fits("grand-bend", name, dt.datetime(2026, 7, 18, 22, 30), 60), name)

    def test_beach_lot_daytime_fits(self):
        cost, _b, ok = pk.price_rule(lot("grand-bend", "Main Beach lot (HotSpot zone 5720)")["rule"],
                                     dt.datetime(2026, 7, 18, 10, 0), 300)
        self.assertTrue(ok)
        self.assertEqual(cost, 40.0)
        self.assertTrue(fits("grand-bend", "Gill Rd lot", dt.datetime(2026, 7, 18, 6, 0), 17 * 60))

    def test_curated_output(self):
        lots, _r, _s, _o = pk.curated(GRAND_BEND_BEACH[0], GRAND_BEND_BEACH[1], dt.datetime(2026, 7, 18, 19, 0),
                                      15 * 60, set(), 1500, [])
        by = {x["name"]: x for x in lots}
        self.assertFalse(by["Main Beach lot (HotSpot zone 5720)"]["fits_stay"])
        self.assertIn("06:00 to 23:00", by["North Beach lot (HotSpot zone 5721)"]["notes"])


class SeasonalBans(unittest.TestCase):
    def test_winter_ban_applies_only_in_its_season(self):
        name = "Centennial Beach lot"
        self.assertFalse(fits("barrie-waterfront", name, dt.datetime(2027, 1, 15, 22, 0), 240))
        self.assertTrue(fits("barrie-waterfront", name, dt.datetime(2026, 7, 15, 22, 0), 240))
        self.assertFalse(fits("bracebridge", "Shier St lot", dt.datetime(2026, 10, 20, 23, 0), 300))
        self.assertTrue(fits("bracebridge", "Shier St lot", dt.datetime(2026, 10, 14, 23, 0), 300))
        self.assertFalse(fits("collingwood", "Hurontario St meters", dt.datetime(2026, 11, 2, 0, 30), 60))

    def test_season_key_uses_the_window_start_date(self):
        rule = {"free": True, "no_parking": [{"days": list(range(7)), "hours": ["23:00", "02:00"], "all_days": True,
                                              "season": ["11-01", "03-31"]}]}
        # window starting Oct 31 23:00 is outside the season; the one starting Nov 1 23:00 is inside
        self.assertTrue(pk.price_rule(rule, dt.datetime(2026, 10, 31, 22, 0), 180)[2])
        self.assertFalse(pk.price_rule(rule, dt.datetime(2026, 11, 1, 22, 0), 180)[2])

    def test_stratford_pay_lots_and_algonquin_permit(self):
        self.assertFalse(fits("stratford", "Albert Street Lot", dt.datetime(2026, 10, 14, 1, 30), 60))
        self.assertTrue(fits("stratford", "Albert Street Lot", dt.datetime(2026, 10, 14, 10, 0), 120))
        self.assertFalse(fits("algonquin-hwy60-east", "Algonquin Visitor Centre", dt.datetime(2026, 10, 14, 20, 0), 180))
        self.assertTrue(fits("algonquin-hwy60-east", "Algonquin Visitor Centre", dt.datetime(2026, 10, 14, 9, 0), 300))
        self.assertFalse(fits("conservation-halton-rattlesnake-point", "Rattlesnake Point main lot",
                              dt.datetime(2026, 10, 14, 19, 0), 180))


class DataGuard(unittest.TestCase):
    def test_notes_with_hours_have_a_structured_rule(self):
        """A lot whose notes state a no-parking window or no overnight must carry no_parking in its rule."""
        pat = re.compile(r"no parking \d\d:\d\d|no overnight|lot open \d\d:\d\d to \d\d:\d\d|^\d\d:\d\d to \d\d:\d\d", re.I)
        missing = []
        for p in pk.load_places():
            for x in p.get("lots") or []:
                notes = x.get("notes") or ""
                if any(pat.search(s.strip()) for s in notes.split(";")) and x.get("name") not in NO_HOURS_STATED:
                    if "lot open 06:00 to 03:00" in notes:
                        continue   # Grand Bend Huron Ave and Mid Town: a $10 overnight pass is sold, not a ban
                    if not (x.get("rule") or {}).get("no_parking"):
                        missing.append("%s / %s" % (p.get("id"), x.get("name")))
        self.assertEqual(missing, [])

    def test_every_window_is_well_formed(self):
        for p in pk.load_places():
            for x in p.get("lots") or []:
                for w in (x.get("rule") or {}).get("no_parking") or []:
                    self.assertEqual(sorted(w["days"]), list(range(7)), x["name"])
                    self.assertTrue(w.get("all_days"), x["name"])
                    self.assertEqual(len(w["hours"]), 2)
                    for h in w["hours"]:
                        self.assertRegex(h, r"^\d\d:\d\d$")
                    if w.get("season"):
                        self.assertEqual(len(w["season"]), 2)


if __name__ == "__main__":
    unittest.main()
