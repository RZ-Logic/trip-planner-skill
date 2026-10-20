"""Offline regression tests for the review fixes in park and ride, the drive-vs-park-and-ride comparison, the
407 money in drive_plan, the park-and-ride timeout and the README calibration table.

Every HTTP call is mocked; tools/ci/sitecustomize.py fails the run on any network attempt.
Run: python -m unittest tests.test_review_park_ride -v
"""
import os as _os
for _k in ("TOMTOM_API_KEY", "NLR_API_KEY", "ON511_API_KEY", "GOOGLE_PLACES_API_KEY"):
    _os.environ.pop(_k, None)   # offline tests never use the developer's real API keys
import atexit as _atexit
import shutil as _shutil
import tempfile as _tempfile
_TMP = _tempfile.mkdtemp(prefix="trip-planner-tests-")
_atexit.register(_shutil.rmtree, _TMP, True)
_os.environ["TRIP_PLANNER_CONFIG"] = _os.path.join(_TMP, "config", "car.json")
_os.environ["TRIP_PLANNER_CACHE"] = _os.path.join(_TMP, "cache")
_os.environ.setdefault("TRIP_PLANNER_QUIET", "1")
import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_drive_plan as tdp   # noqa: E402  (module import only: its TestCases are not collected twice)
from test_engine import STOPS, make_feed, pin_clock, th   # noqa: E402  (functions only)
from test_regressions import add_files   # noqa: E402
dp, dc = tdp.dp, tdp.dc

_CLOCK = pin_clock()
def setUpModule(): _CLOCK.start()       # noqa: E302,E704
def tearDownModule(): _CLOCK.stop()     # noqa: E302,E704


# ---------------------------------------------------------------------- ext-8

PR_BLOCK = {"verified": "2026-10-10", "agencies": [
    {"agency": "Test Transit", "label": "T rule", "route_types": [2],
     "price": {"weekday_daily": 10.0, "weekend_holiday_free": True}, "text": "paid T lot",
     "no_free_parking": ["Transfer B"], "source": "https://example.org/t"}],
    "stop_name_words": ["park & ride"], "stop_name_text": "free carpool lot"}
RT_REGION = {"id": "testreg", "bbox": {"min_lat": 43.3, "max_lat": 43.8, "min_lon": -80.0, "max_lon": -79.6},
             "timezone": "America/Toronto", "feeds": [], "holidays": [], "park_and_ride": PR_BLOCK,
             "flat_fares": {"Test Transit R": {"card": 1.0, "currency": "CAD"},
                            "Test Transit L": {"card": 10.0, "currency": "CAD"}}}
D_LOT = {"type": "way", "id": 1, "center": {"lat": 43.5060, "lon": -79.8000},
         "tags": {"amenity": "parking", "park_ride": "yes", "fee": "no", "name": "D lot"}}


class RoundTripCheapestKept(unittest.TestCase):
    """ext-8: car mode ranks one way (drive + whole-stay hub parking + one fare), but drive_plan ranks park and
    ride there and back (drive both ways, hub parking once, fare both ways). Hub A ($10 lot, $1 rail fare) wins
    there and back ($12.06 against Hub D's $20.30) but loses one way and was cut by --top 1."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": os.path.join(self.tmp, "cache")})
        env.start()
        self.addCleanup(env.stop)
        self.path = os.path.join(self.tmp, "feed.zip")
        trips = [("R", "WK", "r1", "C"), ("L", "WK", "l1", "C")]
        st = [("r1", "10:20:00", "10:20:00", "A", 1, 0, 0), ("r1", "10:45:00", "10:45:00", "C", 2, 0, 0),
              ("l1", "10:25:00", "10:25:00", "D", 1, 0, 0), ("l1", "10:40:00", "10:40:00", "C", 2, 0, 0)]
        make_feed(self.path, STOPS, trips, st)
        add_files(self.path, {"routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                                            "L,T,L,Route L,3\nR,T,R,Route R,2\n"})

    def run_hubs(self, extra):
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 10:00",
                "--no-discover", "--feed", "test=" + self.path, "--routing", "estimate", "--radius-km", "4",
                "--origin-walk-m", "50", "--access", "car", "--top", "1"] + extra
        buf = io.StringIO()
        with mock.patch.object(th, "load_regions", return_value=[RT_REGION]), \
                mock.patch.object(th, "check_network", side_effect=lambda hosts, timeout=6: {h: "ok" for h in hosts}), \
                mock.patch.object(th, "http_get", return_value=json.dumps({"version": 0.6, "elements": [D_LOT]})
                                  .encode("utf-8")), redirect_stdout(buf):
            th.main(args)
        return buf.getvalue()

    def test_round_trip_cheapest_hub_survives_top(self):
        res = json.loads(self.run_hubs(["--json"]))
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub D"])    # cheapest and earliest one way
        others = {o["hub"]: o for o in res["others"]}
        self.assertIn("Hub A", others)                                    # cheapest there and back
        self.assertIs(others["Hub A"].get("rt_cheapest"), True)
        self.assertEqual(res["hidden_hubs"]["past_top"], 0)               # it does not count against --top
        self.assertNotIn("rt_cheapest", res["pareto"][0])

    def test_round_trip_cheapest_is_marked_in_text(self):
        out = self.run_hubs([])
        self.assertRegex(out, r"Hub A  \[[^\]\n]*\]  \(cheapest there and back\)")
        self.assertNotRegex(out, r"Hub D  \[[^\]\n]*\]  \(cheapest there and back\)")

    def test_drive_plan_offers_it(self):
        res = json.loads(self.run_hubs(["--json"]))
        warnings = []
        when = dt.datetime(2026, 10, 15, 10, 0)
        with mock.patch.object(th, "plan", return_value=res):
            pr = dp.park_and_ride((43.4990, -79.8000), (43.5500, -79.8000), when, False, 0.17, 4.0, 1.0, 30.0,
                                  warnings, 30, ctx={"drive_rt": 1000.0, "vot": 0, "day": when.date()})
        self.assertTrue(pr["ran"])
        self.assertEqual(pr["options"][0]["hub"], "Hub A")               # the lowest there-and-back total
        self.assertEqual(pr["options"][0]["rt_total"], 12.06)


# ---------------------------------------------------------------------- drive-core-4

class ParkRideDriveBaseline(unittest.TestCase):
    """drive-core-4: on a one-way run the park-and-ride baseline doubled the drive there, so the drive back paid
    the outbound 407 band and direction (and any one-way border toll twice). Milton to Markham at 07:30 with the
    407 recommended: the camera toll is $46.30 at peak, but the car leaves the lot at 10:48 (off peak), where the
    fake router prices the 407 back at $26.30 (40 km x 0.5 + 1.00 + 5.30)."""

    ARGV = tdp.BASE + ["--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-14 07:30", "--value-of-time", "200"]

    def test_one_way_baseline_prices_the_drive_back_at_its_own_band(self):
        with mock.patch.object(th, "plan", return_value={"access": "car", "pareto": [], "others": []}):
            res, _ = tdp.run(*self.ARGV, parking_cost=22.0)
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        self.assertEqual(rec["letter"], "A")                      # the 407 route
        self.assertAlmostEqual(rec["tolls"], 46.30, places=2)     # outbound camera toll, 07:30 peak
        want = (rec["money"] - rec["account_fee"]) + rec["energy"]["cost"] + 26.30 + rec["account_fee"] + 22.0
        self.assertAlmostEqual(res["park_and_ride"]["drive_rt"], round(want, 2), places=2)   # 123.44, not 143.44
        self.assertFalse(any("drive back could not be priced" in w for w in res["warnings"]))

    def test_baseline_falls_back_and_warns_when_the_return_fails(self):
        with mock.patch.object(th, "plan", return_value={"access": "car", "pareto": [], "others": []}),                 tdp.Env(parking_cost=22.0):
            sys.modules["drive_route"].reverse_for_return = mock.Mock(side_effect=RuntimeError("no route back"))
            res = dp.plan(tdp.args_for(*self.ARGV))
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        old = 2 * (rec["money"] - rec["account_fee"]) + rec["account_fee"] + 22.0
        self.assertAlmostEqual(res["park_and_ride"]["drive_rt"], round(old, 2), places=2)   # the doubling, 143.44
        self.assertTrue(any("park and ride: the drive back could not be priced (no route back)" in w
                            for w in res["warnings"]), res["warnings"])

    def test_round_trip_baseline_is_unchanged(self):
        calls = []
        with mock.patch.object(th, "plan", return_value={"access": "car", "pareto": [], "others": []}):
            res, _ = tdp.run(*(self.ARGV + ["--round-trip"]), parking_cost=22.0, calls=calls)
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        self.assertAlmostEqual(res["park_and_ride"]["drive_rt"], round(rec["money"] + 22.0, 2), places=2)
        self.assertEqual(sum(1 for c in calls if c[0] == "reverse"), len(res["routes"]))   # no extra return built


# ---------------------------------------------------------------------- docs-contract-verifier-1

def lot(name, cost, confidence="official", check=None, unverified=False, walk_m=290):
    return {"name": name, "lat": 43.3270, "lon": -79.7990, "walk_m": walk_m, "walk_min": 4, "search_min": 2,
            "cost": cost, "cost_text": "free" if not cost else "%s for the stay" % dc.money(cost), "basis": "",
            "kind": "lot", "source": "test data", "source_url": "", "confidence": confidence, "free": cost == 0.0,
            "fits_stay": True, "max_stay_min": None, "capacity": 100, "often_full": False, "reservation": None,
            "notes": "", "unverified": unverified, "check": check}


def lots_parking(lots):
    m = types.ModuleType("parking")

    def find_parking(lat, lon, arrive, stay_min, opts, warnings):
        return {"options": [dict(x) for x in lots], "place_rules": [], "reservations": [], "sources": ["test"],
                "radius_m": 800, "caveat": "x"}
    m.find_parking = find_parking
    return m


class ParkRideVerifiedParking(unittest.TestCase):
    """docs-contract-verifier-1: the park-and-ride trigger and the driving baseline took lot #1's price as is, so an
    unverified OpenStreetMap '$5' at #1 kept park and ride off in Burlington (GTHA) though the only checked lot
    costs $18, and an unverified 'free' #1 counted the parking as $0 in 'vs driving + parking'."""

    ARGV = tdp.BASE + ["--dest", "43.325500,-79.799000", "--depart", "2026-10-14 08:00", "--stay-hours", "9"]

    def run_lots(self, lots, *extra):
        with tdp.Env(), mock.patch.dict(sys.modules, {"parking": lots_parking(lots)}), \
                mock.patch.object(th, "plan", return_value={"access": "car", "pareto": [], "others": []}):
            return dp.plan(tdp.args_for(*(self.ARGV + list(extra))))

    def test_osm_price_at_1_does_not_switch_park_and_ride_off(self):
        res = self.run_lots([lot("Unnamed lot", 5.0, "osm", {"why": "price per OpenStreetMap, unverified"}),
                             lot("City lot 4", 18.0)])
        pr = res["park_and_ride"]
        self.assertIsNotNone(pr)
        self.assertIn("$18.00", pr["trigger"])
        self.assertIn("unverified", pr["trigger"])
        self.assertEqual(pr["trigger"], "parking near the destination costs $18.00 for the stay (the cheapest lot "
                                        "with a checked price; #1 ($5.00) needs a check: price per OpenStreetMap, "
                                        "unverified)")
        self.assertEqual(pr["park_cost"], 18.0)
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        self.assertEqual(rec["tolls"], 0)
        self.assertAlmostEqual(pr["drive_rt"], round(2 * rec["money"] + 18.0, 2), places=2)

    def test_unverified_free_at_1_is_not_counted_as_zero(self):
        res = self.run_lots([lot("Unnamed lot", 0.0, "osm", {"why": "free per OpenStreetMap, unverified"},
                                 unverified=True), lot("City lot 4", 12.0)], "--park-and-ride", "on")
        pr = res["park_and_ride"]
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        self.assertEqual(pr["park_cost"], 12.0)
        self.assertEqual(rec["tolls"], 0)
        self.assertAlmostEqual(pr["drive_rt"], round(2 * rec["money"] + 12.0, 2), places=2)
        self.assertIn("run because asked for; parking counted at $12.00 (the cheapest lot with a checked price; "
                      "#1 (free) needs a check: free per OpenStreetMap, unverified)", dp.render(res))

    def test_checked_price_at_1_is_used_as_is(self):
        res = self.run_lots([lot("City lot 4", 22.0), lot("City lot 9", 30.0)])
        pr = res["park_and_ride"]
        self.assertIn("$22.00", pr["trigger"])
        self.assertNotIn("unverified", pr["trigger"])
        self.assertEqual(pr["park_cost"], 22.0)

    def test_pr_parking_cost_rules(self):
        osm5 = lot("Unnamed lot", 5.0, "osm", {"why": "price per OpenStreetMap, unverified"})
        far = dict(lot("Far lot", 9.0), beyond_walk=True)
        unfit = dict(lot("Short lot", 7.0), fits_stay=False)
        self.assertEqual(dp.pr_parking_cost([osm5, far, unfit], osm5), (5.0, None))   # nothing checked to use
        dear = lot("City lot", 4.0)
        self.assertEqual(dp.pr_parking_cost([osm5, dear], osm5), (5.0, None))         # #1 is already dearer
        unknown = lot("Unknown lot", None, check={"why": "price unknown"})
        cost, note = dp.pr_parking_cost([unknown, lot("City lot", 14.0)], unknown)
        self.assertEqual(cost, 14.0)
        self.assertIn("price unknown", note)
        self.assertEqual(dp.pr_parking_cost([], None), (None, None))


# ---------------------------------------------------------------------- tests-ci-eng-3

class Toll407Money(unittest.TestCase):
    """tests-ci-eng-3: the plan tests asserted money against the plan's own a['tolls'], so a route_tolls that billed
    the transponder price without a transponder (or the reverse), a hub lot counted twice in car_rt, or a band that
    started a minute late all passed. These pin absolute values from the fixtures: fake_retime prices the 407 at
    07:30 as 40 km x $1.00 + $1.00 = $41.00 with a transponder and $5.30 more ($46.30) without."""

    ARGV = tdp.BASE + ["--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off"]
    SEG = {"entry_km": 34.8, "exit_km": 84.4, "direction": "E"}   # test_drive_route.Price407.SEG, Milton to Markham

    def test_camera_toll_without_transponder(self):
        res, _ = tdp.run(*self.ARGV)
        a = res["routes"][0]
        self.assertEqual(a["tolls"], 46.30)
        self.assertEqual(a["tolls"], a["toll_407_camera"])
        self.assertEqual(a["account_fee"], 5.0)
        self.assertAlmostEqual(a["money"], round(a["energy"]["cost"] + 46.30 + 5.0, 2), places=2)

    def test_transponder_toll_with_transponder(self):
        res, _ = tdp.run(*(self.ARGV + ["--transponder"]))
        a = res["routes"][0]
        self.assertEqual(a["tolls"], 41.00)
        self.assertEqual(a["tolls"], a["toll_407_transponder"])
        self.assertEqual(a["account_fee"], 0.0)
        self.assertAlmostEqual(a["money"], round(a["energy"]["cost"] + 41.00, 2), places=2)

    def test_route_tolls_adds_other_tolls(self):
        r = {"toll_407_camera": 46.30, "toll_407_transponder": 41.00, "toll_other_cad": 3.75}
        self.assertAlmostEqual(dp.route_tolls(r, False), 50.05, places=2)
        self.assertAlmostEqual(dp.route_tolls(r, True), 44.75, places=2)
        self.assertEqual(dp.route_tolls({}, False), 0.0)

    def test_round_trip_return_toll_is_the_camera_toll(self):
        res, _ = tdp.run(*tdp.BASE, "--dest", "%f,%f" % tdp.GRAND_BEND, "--depart", "2026-10-12 09:00",
                         "--stay-hours", "5", "--round-trip")
        back = res["routes"][0]["return"]
        self.assertGreater(back["tolls"], 0)
        self.assertEqual(back["tolls"], back["toll_407_camera"])
        self.assertNotEqual(back["toll_407_camera"], back["toll_407_transponder"])

    def test_hub_car_rt_counts_hub_parking_once(self):
        op = {"hub": "Test GO", "ride_km": 4.9, "ride_min": 5, "book_by": 7 * 3600 + 26 * 60,
              "board": 7 * 3600 + 56 * 60, "arrive": 8 * 3600 + 45 * 60, "transit_fare": 3.30, "fare_complete": True,
              "fare_parts": [], "car_cost": 7.79, "backup": None,
              "parking": {"text": "paid station lot, $7.00 for the stay", "cost": 7.0},
              "legs": [{"type": "ride", "agency": "GO Transit", "route": "LW", "headsign": "Union Station",
                        "depart": 7 * 3600 + 56 * 60, "arrive": 8 * 3600 + 45 * 60}]}
        warnings = []
        with mock.patch.object(th, "plan", return_value={"access": "car", "pareto": [op], "others": []}):
            pr = dp.park_and_ride(tdp.MILTON, tdp.UNION, dt.datetime(2026, 10, 14, 9, 0), True, 0.16, 9.0, 20.0,
                                  18.0, warnings, 30, ctx={"drive_rt": 40.0, "day": dt.date(2026, 10, 14),
                                                           "drive_leave": dt.datetime(2026, 10, 14, 7, 9)})
        self.assertTrue(pr["ran"], warnings)
        got = pr["options"][0]
        self.assertEqual(got["hub"], "Test GO")
        self.assertEqual(got["car_rt"], 8.58)     # 2 x $0.79 driving + the $7.00 lot once
        self.assertEqual(got["rt_total"], 15.18)  # + 2 x $3.30 fares

    def test_407_band_starts_at_its_start_time(self):
        import tolls
        day = dt.date(2026, 10, 14)

        def toll(h, m):
            return tolls.price_407(self.SEG, dt.datetime.combine(day, dt.time(h, m)), True)["toll"]
        self.assertEqual(toll(7, 0), 50.62)    # 07:00 band from its first minute
        self.assertEqual(toll(6, 59), 39.70)   # still the 05:00 band
        self.assertEqual(toll(9, 30), 39.79)   # 09:30 band
        self.assertEqual(tolls.band_index(dt.datetime(2026, 10, 14, 7, 0), ["05:00", "07:00", "09:30"]), 1)


if __name__ == "__main__":
    unittest.main()
