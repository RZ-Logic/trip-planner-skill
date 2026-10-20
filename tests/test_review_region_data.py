"""Regression tests for the review's region data findings: the Ontario region's curated fares and timetables
(distance- and zone-based rural fares shown as approximate, route-level prices; hand-entered and projected
timetables flagged at run time, region files past their recheck age).

Offline: the bundled region files, synthetic legs, plan runs with every network call mocked, a fixed clock.
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
import copy
import datetime as dt
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

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


def ok_network(hosts, timeout=6):
    return {h: "ok" for h in hosts}


def one_feed_region(fid):
    """A test region holding a deep copy of one ontario.json feed (time zone, holidays, fares and verified date
    from ontario.json), box over all of Ontario."""
    f = copy.deepcopy(next(x for x in ONTARIO["feeds"] if x["id"] == fid))
    r = {k: ONTARIO[k] for k in ("timezone", "utc_std_offset_hours", "dst_rule", "holidays", "flat_fares", "verified")}
    r.update(id="t", name="T", bbox={"min_lat": 41.0, "max_lat": 47.0, "min_lon": -84.0, "max_lon": -74.0}, feeds=[f])
    return r


def run_plan(region, args, as_json=True):
    """th.main on one test region, network mocked: the parsed --json result or the text output."""
    out = io.StringIO()
    with mock.patch.object(th, "load_regions", return_value=[region]),             mock.patch.object(th, "check_network", side_effect=ok_network),             redirect_stdout(out), redirect_stderr(io.StringIO()):
        th.main(args + ["--region", "t", "--routing", "estimate", "--final-ride", "off", "--no-discover"]
                + (["--json"] if as_json else []))
    return json.loads(out.getvalue()) if as_json else out.getvalue()


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


WEGO = ["--origin", "43.14917,-79.04799", "--dest", "43.2494,-79.06157"]   # Floral Clock to Fort George
MEGABUS = ["--origin", "43.64415,-79.37747", "--dest", "44.2595,-76.50604"]   # Toronto Union to Kingston terminal


def assumed_warnings(res):
    return [w for w in res["warnings"] if "assumed" in w]


class AssumedTimetableTests(unittest.TestCase):
    """architecture-product-4: a hand-entered timetable whose notes say its times are projected (WEGO 604's 2027
    seasons, the HSAT 2027 Beach Bus, Megabus east after its announced 2027-01-06 change) is planned as fact.
    The leg must carry a mark, the run a WARNING, the FEEDS line a 'hand-entered' tag; and a region file whose
    verified date is over six months old must warn."""

    def test_projected_wego_604_times_carry_a_warning_and_a_leg_mark(self):
        region = one_feed_region("wego-604-notl")
        res = run_plan(region, WEGO + ["--depart", "2027-06-15 10:00"])
        ws = [w for w in res["warnings"] if "WEGO 604" in w and "assumed" in w and "2027-06-15" in w]
        self.assertEqual(len(ws), 1, res["warnings"])
        self.assertIn("projected from the 2026 schedule", ws[0])
        self.assertIs(res["transit_only"]["legs"][0].get("assumed"), True)
        self.assertIs(res["feeds"][0]["hand_entered"], True)
        self.assertIs(res["feeds"][0]["assumed_on_date"], True)
        txt = run_plan(region, WEGO + ["--depart", "2027-06-15 10:00"], as_json=False)
        self.assertIn("timetable assumed", txt)
        self.assertIn("(2026-10-04..2027-10-31, hand-entered)", txt)

    def test_published_fall_2026_wego_times_are_not_marked_assumed(self):
        res = run_plan(one_feed_region("wego-604-notl"), WEGO + ["--depart", "2026-10-15 10:00"])
        self.assertEqual(assumed_warnings(res), [])
        self.assertTrue(res["transit_only"]["legs"])
        legs = res["transit_only"]["legs"] + [lg for o in res["pareto"] + res["others"] for lg in o["legs"]]
        self.assertFalse(any(lg.get("assumed") for lg in legs))
        self.assertIs(res["feeds"][0]["assumed_on_date"], False)
        self.assertIs(res["feeds"][0]["hand_entered"], True)

    def test_megabus_east_after_its_announced_change_is_marked_assumed(self):
        region = one_feed_region("megabus-on-east")
        res = run_plan(region, MEGABUS + ["--depart", "2027-03-10 09:00"])
        self.assertTrue([w for w in assumed_warnings(res) if "Megabus" in w and "2027-01-06" in w], res["warnings"])
        res = run_plan(region, MEGABUS + ["--depart", "2026-10-20 09:00"])
        self.assertEqual(assumed_warnings(res), [])
        self.assertTrue(res["transit_only"]["legs"])

    def test_load_feed_marks_only_trips_on_assumed_service_days(self):
        f = {"id": "x", "name": "X", "observed_until": "2027-01-05", "assumed_dates": ["2026-12-24"],
             "manual": {"agency": "X", "timezone": "America/Toronto",
                        "services": {"s": {"days": "1111111", "start": "2026-10-01", "end": "2027-09-30"},
                                     "p": {"days": "1111111", "start": "2027-06-01", "end": "2027-06-30",
                                           "assumed": True}},
                        "routes": [{"route": "1", "trips": [
                            {"service": "s", "stops": [["A", 43.0, -80.0, "10:00"], ["B", 43.05, -80.0, "10:10"]]},
                            {"service": "p", "stops": [["A", 43.0, -80.0, "11:00"], ["B", 43.05, -80.0, "11:10"]]}]}]}}
        path = th.manual_feed_zip(f)
        for day, expect in ((dt.date(2027, 1, 5), 0), (dt.date(2027, 1, 6), 1), (dt.date(2026, 12, 24), 1),
                            (dt.date(2026, 12, 23), 0), (dt.date(2027, 6, 10), 2)):
            net = th.Network()
            net.warnings = []
            info = th.load_feed(net, "x", "X", path, day, 0, 30 * 3600, provenance=th.feed_provenance(f))
            self.assertEqual(len(net.assumed_trips), expect, day)
            self.assertIs(info["assumed_on_date"], bool(expect), day)
        self.assertIsNone(th.feed_provenance({"id": "g", "urls": ["https://example.org/gtfs.zip"]}))

    def test_region_file_older_than_six_months_warns(self):
        region = one_feed_region("wego-604-notl")
        with mock.patch.object(th, "utc_now", return_value=dt.datetime(2027, 5, 1, 12, 0)):
            res = run_plan(region, WEGO + ["--depart", "2027-05-20 10:00"])
        self.assertTrue([w for w in res["warnings"] if "last checked 2026-10-10" in w], res["warnings"])
        res = run_plan(region, WEGO + ["--depart", "2026-10-15 10:00"])
        self.assertFalse([w for w in res["warnings"] if "last checked" in w], res["warnings"])


if __name__ == "__main__":
    unittest.main()
