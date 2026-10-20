"""Regression tests for the review's region data findings: the Ontario region's curated fares and timetables
(distance- and zone-based rural fares shown as approximate, route-level prices).

Offline: the bundled region files, synthetic legs, no network, a fixed clock.
Run: python -m unittest tests.test_review_region_data -v
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
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import pin_clock, th  # noqa: E402  (test_engine inserts the scripts path)

REGIONS = {r["id"]: r for r in th.load_regions()}
ONTARIO = REGIONS["ontario"]
_CLOCK = pin_clock()   # utc_now() = 2026-10-12 16:00


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


class NoFares:   # journey_fare's net: flat fares only
    fares, fare_urls, stop_zone = {}, {}, [""]


def ride_leg(feed, agency, route, frm="A", to="B", depart=36000):
    return {"type": "ride", "feed": feed, "agency": agency, "route": route, "route_id": route, "from": frm,
            "to": to, "depart": depart, "arrive": depart + 1200, "from_idx": 0, "to_idx": 0}


HSAT = ("Huron Shores Area Transit (Routes 1-3 and Route 2 Beach Bus)", "Huron Shores Area Transit")
LINX = ("Simcoe County LINX", "Simcoe County Linx")


class RuralFareTests(unittest.TestCase):
    """data-integrity-6: a distance- or zone-based rural fare is one common price on a scale. It must print as
    'about X (scale)', not as a firm figure, and LINX Route 4 has its own $2 fare."""

    def test_distance_based_fare_prints_as_approximate_with_its_scale(self):
        total, cur, parts, complete = th.journey_fare(NoFares, [ride_leg(HSAT[0], HSAT[1], "1")],
                                                      ONTARIO["flat_fares"], "card")
        self.assertEqual(parts[0]["fare"], 12.0)
        self.assertIn("approx", parts[0])
        self.assertIn("$6", parts[0]["approx"])
        self.assertIn("$20", parts[0]["approx"])
        o = {"transit_fare": total, "fare_parts": parts, "fare_currency": cur, "fare_complete": complete}
        txt = th.fare_text(o, "CAD", True, rules_on=True)
        self.assertIn("transit fare about 12.00 CAD", txt)
        self.assertIn("Huron Shores Area Transit about 12.00 (distance-based", txt)

    def test_every_distance_or_zone_fare_entry_is_flagged(self):
        for key in ("Huron Shores Area Transit", "PC Connect", "GOST (Guelph Owen Sound Transportation)",
                    "Simcoe County Linx", "Corridor 11 Bus", "Middlesex County Connect (Sarnia-London)"):
            self.assertTrue(ONTARIO["flat_fares"][key].get("approx"), key)

    def test_linx_route_4_uses_its_own_two_dollar_fare(self):
        for payment, want in (("cash", 2.0), ("card", 1.8)):
            _t, _c, parts, _ok = th.journey_fare(NoFares, [ride_leg(LINX[0], LINX[1], "4")],
                                                 ONTARIO["flat_fares"], payment)
            self.assertEqual(parts[0]["fare"], want, payment)
            self.assertNotIn("approx", parts[0], payment)
        _t, _c, parts, _ok = th.journey_fare(NoFares, [ride_leg(LINX[0], LINX[1], "2")],
                                             ONTARIO["flat_fares"], "cash")
        self.assertEqual(parts[0]["fare"], 6.0)
        self.assertTrue(parts[0].get("approx"))

    def test_free_part_carries_no_approx(self):
        flat = dict(ONTARIO["flat_fares"])
        flat["Huron Shores Area Transit"] = dict(flat["Huron Shores Area Transit"], card=0.0)
        _t, _c, parts, _ok = th.journey_fare(NoFares, [ride_leg(HSAT[0], HSAT[1], "1")], flat, "card")
        self.assertEqual(parts[0]["fare"], 0)
        self.assertNotIn("approx", parts[0])

    def test_approx_is_kept_apart_from_the_transfer_caveat(self):
        # an approximate fare is not a fare before transfer discounts: the two caveats stay separate
        total, cur, parts, complete = th.journey_fare(NoFares, [ride_leg(HSAT[0], HSAT[1], "1")],
                                                      ONTARIO["flat_fares"], "card")
        self.assertNotIn("transfers_unknown", parts[0])
        o = {"transit_fare": total, "fare_parts": parts, "fare_currency": cur, "fare_complete": complete}
        self.assertNotIn("before transfer discounts", th.fare_text(o, "CAD", True, rules_on=True))


if __name__ == "__main__":
    unittest.main()
