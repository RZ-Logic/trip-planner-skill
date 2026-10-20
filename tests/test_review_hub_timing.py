"""Regression tests for the review findings on trip_hubs.plan() timing: ride legs, book-by and arrival maths,
traffic factors for ride mode, final-ride drop points and holidays.

Offline: synthetic feeds, every network call mocked, a fixed clock.
Run: PYTHONPATH=tools/ci TRIP_PLANNER_NO_NETWORK=1 python -m unittest tests.test_review_hub_timing -v
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
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import hms, make_feed, pin_clock, th  # noqa: E402  (test_engine adds the scripts folder)
from test_regressions import add_files  # noqa: E402

BASE = ["--region", "none", "--no-discover", "--routing", "estimate"]
ALL_YEARS = [("WK", 1, 1, 1, 1, 1, 1, 1, "20260101", "20291231")]
DAY = dt.date(2026, 10, 15)   # a Thursday
TT = th._driving_json("traffic.json")
_CLOCK = pin_clock()


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


def ok_network(hosts, timeout=6):
    return {h: "ok" for h in hosts}


def feed(stops, trips, stop_times, calendar=None, routes=None):
    p = os.path.join(tempfile.mkdtemp(dir=_TMP), "f.zip")
    make_feed(p, stops, trips, stop_times, calendar=calendar)
    if routes:   # {route_id: route_type}
        add_files(p, {"routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n" + "".join(
            "%s,T,%s,Route %s,%d\n" % (r, r, r, t) for r, t in sorted(routes.items()))})
    return p


def run(args, as_json=True, regions=None):
    out = io.StringIO()
    patches = [mock.patch.object(th, "check_network", side_effect=ok_network)]
    if regions is not None:
        patches.append(mock.patch.object(th, "load_regions", return_value=regions))
    for p in patches:
        p.start()
    try:
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            th.main(args + (["--json"] if as_json else []))
    finally:
        for p in patches:
            p.stop()
    return json.loads(out.getvalue()) if as_json else out.getvalue()


def option(res, hub):
    return next(o for o in res["pareto"] + res["others"] if o["hub"] == hub)


def at(day, h, m):
    return dt.datetime.combine(day, dt.time(h, m))


HUB_STOPS = [("H", "Hub H", 43.4900, -79.8000), ("C", "Dest C", 43.6000, -79.8000)]
HUB_ARGS = ["--origin", "43.3000,-79.8000", "--dest", "43.6000,-79.8000", "--min-saving", "0", "--radius-km", "25",
            "--origin-walk-m", "300"]
FREE_S = th.heuristic_ride(th.haversine_m(43.3, -79.8, 43.49, -79.8))[1]   # estimate: origin to Hub H


def train_at(dep, arr, calendar=None):
    return feed(HUB_STOPS, [("X", "WK", "x1", "C")],
                [("x1", dep, dep, "H", 1, 0, 0), ("x1", arr, arr, "C", 2, 0, 0)], calendar=calendar)


# ------------------------------------------------------------------------------------- hubs-engine-1 (batch 1)
class DepartBookByTraffic(unittest.TestCase):
    """Depart mode: the ride starts at book-by + pickup wait, so it is timed in the traffic of that moment."""

    def test_book_by_uses_the_traffic_at_the_pickup(self):
        res = run(HUB_ARGS + ["--depart", "2026-10-15 15:00", "--feed", "t=" + train_at("16:30:00", "16:50:00")]
                  + BASE)
        o = option(res, "Hub H")
        f_peak = th.traffic_factor_for(at(DAY, 15, 50))           # 1.45: weekday peak from 15:30
        self.assertEqual(o["book_by"], hms(16, 30) - (6 * 60 + int(FREE_S * f_peak) + th.PLATFORM_S + 3 * 60))
        self.assertEqual(th.fmt_t(o["book_by"]), "15:44")                  # was 15:50 (ride timed at 15:00)
        self.assertAlmostEqual(o["ride_min"], round(FREE_S * f_peak / 60.0, 1), places=1)

    def test_later_traffic_too_slow_books_at_the_start(self):
        # leaving at 15:23 (pickup 15:29) makes the 16:07; booking later would put the pickup in the peak
        res = run(HUB_ARGS + ["--depart", "2026-10-15 15:23", "--feed", "t=" + train_at("16:07:00", "16:30:00")]
                  + BASE)
        o = option(res, "Hub H")
        f_now = th.traffic_factor_for(at(DAY, 15, 29))
        self.assertEqual(o["book_by"], hms(15, 23))                         # was 15:27, pickup 15:33 in the peak
        self.assertEqual(o["board"], hms(16, 7))
        self.assertAlmostEqual(o["ride_min"], round(FREE_S * f_now / 60.0, 1), places=1)

    def test_arrive_by_backup_book_by_uses_its_own_traffic(self):
        # main: the 10:15 (book about 09:35, after the peak); backup: the 09:50, booked inside the peak
        p = feed(HUB_STOPS, [("X", "WK", "x1", "C"), ("X", "WK", "x2", "C")],
                 [("x1", "10:15:00", "10:15:00", "H", 1, 0, 0), ("x1", "10:45:00", "10:45:00", "C", 2, 0, 0),
                  ("x2", "09:50:00", "09:50:00", "H", 1, 0, 0), ("x2", "10:20:00", "10:20:00", "C", 2, 0, 0)])
        o = option(run(HUB_ARGS + ["--arrive-by", "2026-10-15 11:00", "--feed", "t=" + p] + BASE), "Hub H")
        self.assertEqual(o["backup"]["board"], hms(9, 50))
        f_peak = th.traffic_factor_for(at(DAY, 9, 10))
        self.assertEqual(o["backup"]["book_by"], hms(9, 50) - (360 + int(FREE_S * f_peak) + th.PLATFORM_S + 180))

    def test_short_ride_to_the_first_stop_uses_the_traffic_at_the_pickup(self):
        # transit only walks 875 m to Stop S for the 15:50; the short ride there is booked about 15:34 (peak 15:40)
        stops = [("S", "Stop S", 43.3063, -79.8000), ("C", "Dest C", 43.6000, -79.8000)]
        p = feed(stops, [("X", "WK", "x1", "C")],
                 [("x1", "15:50:00", "15:50:00", "S", 1, 0, 0), ("x1", "16:30:00", "16:30:00", "C", 2, 0, 0)])
        res = run(["--origin", "43.3000,-79.8000", "--dest", "43.6000,-79.8000", "--min-saving", "0",
                   "--origin-walk-m", "1000", "--depart", "2026-10-15 15:00", "--feed", "t=" + p] + BASE)
        free = th.heuristic_ride(th.haversine_m(43.3, -79.8, 43.3063, -79.8))[1]
        f_peak = th.traffic_factor_for(at(DAY, 15, 40))
        self.assertEqual(res["transit_only"]["short_ride"]["book_by"],
                         hms(15, 50) - (360 + int(free * f_peak) + th.PLATFORM_S + 180))   # was timed at x1.2

    def test_explicit_traffic_factor_is_kept(self):
        res = run(HUB_ARGS + ["--depart", "2026-10-15 15:00", "--feed", "t=" + train_at("16:30:00", "16:50:00"),
                              "--traffic-factor", "1.2"] + BASE)
        self.assertEqual(option(res, "Hub H")["book_by"], hms(16, 30) - (360 + int(FREE_S * 1.2) + 60 + 180))


# --------------------------------------------------------------------------------------------- ext-2 (batch 2)
def one_way_s(a, b):
    """(seconds, metres) from a to b: northbound at 15 m/s, southbound at 7.5 m/s (one-way streets)."""
    d = th.haversine_m(a[0], a[1], b[0], b[1]) * 1.3
    return d / (15.0 if b[0] >= a[0] else 7.5), d


def osrm_one_way(url, **_k):
    """A fake OSRM table reply that honours sources= and destinations= (default: every point)."""
    u = urllib.parse.urlsplit(url)
    pts = [tuple(float(x) for x in c.split(","))[::-1] for c in u.path.split("/driving/")[1].split(";")]
    q = urllib.parse.parse_qs(u.query)
    src = [int(i) for i in q["sources"][0].split(";")] if "sources" in q else list(range(len(pts)))
    dst = [int(i) for i in q["destinations"][0].split(";")] if "destinations" in q else list(range(len(pts)))
    cell = lambda i, j, k: 0.0 if i == j else one_way_s(pts[i], pts[j])[k]  # noqa: E731
    return json.dumps({"code": "Ok", "durations": [[cell(i, j, 0) for j in dst] for i in src],
                       "distances": [[cell(i, j, 1) for j in dst] for i in src]}).encode()


class FinalRideDirection(unittest.TestCase):
    def test_osrm_table_to_origin_reads_point_to_origin(self):
        with mock.patch.object(th, "http_get", side_effect=osrm_one_way) as hg:
            out = th.osrm_table("https://x", (43.99, -79.80), [(43.90, -79.80)], to_origin=True)
        self.assertAlmostEqual(out[0][0], one_way_s((43.90, -79.80), (43.99, -79.80))[0])
        self.assertIn("destinations=0", hg.call_args[0][0])

    def test_osrm_table_default_still_reads_origin_to_point(self):
        with mock.patch.object(th, "http_get", side_effect=osrm_one_way):
            out = th.osrm_table("https://x", (43.30, -79.80), [(43.49, -79.80), (43.20, -79.80)])
        self.assertAlmostEqual(out[0][0], one_way_s((43.30, -79.80), (43.49, -79.80))[0])
        self.assertAlmostEqual(out[1][0], one_way_s((43.30, -79.80), (43.20, -79.80))[0])

    def test_final_ride_is_timed_from_the_drop_point_to_the_door(self):
        stops = [("O", "Origin stop", 43.3010, -79.8000), ("T", "Town stop", 43.9000, -79.8000)]
        p = feed(stops, [("K", "WK", "k1", "T")],
                 [("k1", "06:30:00", "06:30:00", "O", 1, 0, 0), ("k1", "08:30:00", "08:30:00", "T", 2, 0, 0)])
        with mock.patch.object(th, "http_get", side_effect=osrm_one_way):
            res = run(["--origin", "43.3000,-79.8000", "--dest", "43.9900,-79.8000", "--depart", "2026-10-15 06:00",
                       "--feed", "t=" + p, "--min-saving", "0", "--traffic-factor", "1.0", "--region", "none",
                       "--no-discover", "--routing", "osrm"])
        fin = res["finish_with_ride"][0]["final_ride"]
        x_to_d = one_way_s((43.90, -79.80), (43.99, -79.80))[0]
        self.assertAlmostEqual(fin["min"], round(x_to_d / 60.0, 1), places=1)   # was the D->X time, twice as long



# -------------------------------------------------------------- architecture-product-3, data-integrity-10 (batch 5)
ONTARIO = {"id": "ontario", "name": "Ontario (test)", "timezone": "America/Toronto", "feeds": [],
           "bbox": {"min_lat": 41.6, "max_lat": 57.0, "min_lon": -95.2, "max_lon": -74.3}}


def row(table, name):
    return next(r for r in TT[table] if r["name"] == name)


class RideTrafficByArea(unittest.TestCase):
    """Ride legs use traffic.json's rows: congested inside its areas, elsewhere outside, holidays as weekends."""

    def test_rural_ontario_ride_uses_the_elsewhere_rows(self):
        stops = [("H", "Hub H", 44.5900, -80.2000), ("C", "Dest C", 44.7000, -80.2000)]
        p = feed(stops, [("X", "WK", "x1", "C")],
                 [("x1", "08:40:00", "08:40:00", "H", 1, 0, 0), ("x1", "09:00:00", "09:00:00", "C", 2, 0, 0)])
        res = run(["--origin", "44.5000,-80.2000", "--dest", "44.7000,-80.2000", "--depart", "2026-10-15 08:00",
                   "--feed", "t=" + p, "--min-saving", "0", "--no-discover", "--routing", "estimate"],
                  regions=[ONTARIO])
        free = th.heuristic_ride(th.haversine_m(44.5, -80.2, 44.7, -80.2))[1]
        self.assertAlmostEqual(res["ride_all"]["ride_s"] / free, row("elsewhere", "daytime")["typical"])   # was 1.45

    def test_ride_factor_matches_drive_route_rows(self):
        import drive_route as drt
        inside = ((43.50, -79.70), (43.60, -79.60))     # Mississauga and Toronto boxes
        outside = ((44.50, -80.20), (44.70, -80.20))    # Grey County, outside every area box
        hol = {"2027-07-01"}
        for when in (at(DAY, 8, 0), at(DAY, 9, 45), at(DAY, 12, 0), at(DAY, 22, 0), at(dt.date(2027, 7, 1), 8, 0)):
            self.assertAlmostEqual(th.drive_factor(when, hol, inside), drt.factors({"urban": True}, when, hol)[0])
            self.assertAlmostEqual(th.drive_factor(when, hol, outside),
                                   drt.factors({"urban": False, "hwy": False}, when, hol)[0])
            self.assertAlmostEqual(th.drive_factor(when, hol, outside, "plan"),
                                   drt.factors({"urban": False, "hwy": False}, when, hol)[1])

    def test_ride_factor_comes_from_traffic_json(self):
        self.assertEqual(th.traffic_factor_for(at(DAY, 9, 45)), row("congested", "weekday shoulder")["typical"])
        self.assertEqual(th.traffic_factor_for(at(dt.date(2027, 7, 1), 8, 0), {"2027-07-01"}),
                         row("congested", "daytime")["typical"])                # Canada Day: no weekday peak

    def test_listed_holiday_ride_is_not_priced_at_the_weekday_peak(self):
        p = train_at("08:30:00", "08:50:00", calendar=ALL_YEARS)
        res = run(HUB_ARGS + ["--arrive-by", "2027-07-01 11:30", "--feed", "t=" + p] + BASE)
        o = option(res, "Hub H")
        self.assertAlmostEqual(o["ride_min"], round(FREE_S * row("congested", "daytime")["typical"] / 60.0, 1),
                               places=1)                                         # was x1.45 on Canada Day



# --------------------------------------------------------------------------- architecture-product-8 (batch 5)
class FinishDropPoints(unittest.TestCase):
    """Finish with a ride: a train line with few trips is still tried, and untried coach stops are named."""

    def plan(self, coaches=3, region=None, place_info=None):
        stops = [("O", "Origin stop", 43.3010, -79.8000), ("K1", "Coach K1", 43.9500, -79.8000),
                 ("K2", "Coach K2", 44.0000, -79.8700), ("K3", "Coach K3", 44.0500, -79.8000),
                 ("K4", "Coach K4", 44.0000, -79.7300), ("R", "Rail R", 43.9600, -79.7500),
                 ("L1", "Local L1", 43.9850, -79.8000), ("L2", "Local L2", 44.0150, -79.8000)]
        trips, st, routes = [], [], {"T": 2, "L": 3}
        for k in range(1, coaches + 1):
            routes["C%d" % k] = 3
            for j in range(3 if k <= 3 else 1):   # three trips on each of the first three coach lines
                tid = "c%d%d" % (k, j)
                trips.append(("C%d" % k, "WK", tid, "K%d" % k))
                st += [(tid, "%02d:00:00" % (12 + j), "%02d:00:00" % (12 + j), "O", 1, 0, 0),
                       (tid, "%02d:%d0:00" % (13 + j, k + 1), "%02d:%d0:00" % (13 + j, k + 1), "K%d" % k, 2, 0, 0)]
        trips += [("T", "WK", "t1", "R"), ("L", "WK", "l1", "L2")]
        st += [("t1", "09:30:00", "09:30:00", "O", 1, 0, 0), ("t1", "11:00:00", "11:00:00", "R", 2, 0, 0),
               ("l1", "13:40:00", "13:40:00", "K1", 1, 0, 0), ("l1", "13:45:00", "13:45:00", "L1", 2, 0, 0),
               ("l1", "13:50:00", "13:50:00", "L2", 3, 0, 0)]
        p = feed(stops, trips, st, routes=routes)
        args = ["--origin", "43.3000,-79.8000", "--dest", "44.0000,-79.8000", "--depart", "2026-10-15 09:00",
                "--max-hours", "7"]
        if region is None:
            return run(args + ["--feed", "t=" + p] + BASE)
        region = dict(region, feeds=[{"id": "t", "name": "Test Transit", "urls": [p], "bbox": region["bbox"]}])
        with mock.patch.object(th, "reverse_place_info", side_effect=place_info):
            return run(args + ["--no-discover", "--routing", "estimate", "--final-ride", "on"], regions=[region])

    def test_a_train_with_few_trips_is_still_tried(self):
        res = self.plan()
        self.assertIn("Rail R", [d["name"] for d in res["finish_info"]["drop_points"]])
        rail = [o for o in res["finish_with_ride"] if o["final_ride"]["from"] == "Rail R"]
        self.assertTrue(rail and rail[0]["arrive"] < hms(12, 0), [o["final_ride"]["from"] for o in
                                                                  res["finish_with_ride"]])   # about 11:21
        self.assertEqual(res["finish_info"]["untried"], [])
        self.assertFalse(any("not tried" in w for w in res["warnings"]), res["warnings"])

    def test_untried_coach_stop_is_named_in_warnings(self):
        res = self.plan(coaches=4)
        self.assertEqual(res["finish_info"]["untried"], ["Coach K4"])
        self.assertTrue(any("not tried" in w and "Coach K4" in w for w in res["warnings"]), res["warnings"])

    def test_a_train_stop_across_the_border_is_still_left_out(self):
        region = {"id": "home", "name": "Home (test)", "country": "CA", "timezone": "America/Toronto",
                  "bbox": {"min_lat": 43.0, "max_lat": 44.3, "min_lon": -80.2, "max_lon": -79.4}}
        res = self.plan(region=region,   # the destination (44.00) is in US, Rail R (43.96) in CA
                        place_info=lambda lat, lon, warnings: {"names": set(), "country": "US" if lat >= 43.97
                                                               else "CA", "state": None})
        names = [d["name"] for d in res["finish_info"]["drop_points"]]
        self.assertNotIn("Rail R", names)
        self.assertNotIn("Coach K1", names)
        self.assertTrue(any("2 drop points across the border" in w for w in res["warnings"]), res["warnings"])


# ------------------------------------------------------------------------------------- tests-ci-eng-5 (batch 6)
class TimeMarginsPinned(unittest.TestCase):
    """Each test pins one margin in plan()'s booking and arrival maths (a mutant that drops it fails)."""
    FIXED = ["--traffic-factor", "1.0", "--safety-min", "3", "--pickup-wait-min", "6"]

    def test_arrive_by_keeps_the_safety_margin_before_the_deadline(self):
        p = feed(HUB_STOPS, [("X", "WK", "x1", "C"), ("X", "WK", "x2", "C")],
                 [("x1", "10:50:00", "10:50:00", "H", 1, 0, 0), ("x1", "11:26:00", "11:26:00", "C", 2, 0, 0),
                  ("x2", "11:00:00", "11:00:00", "H", 1, 0, 0), ("x2", "11:29:00", "11:29:00", "C", 2, 0, 0)])
        res = run(HUB_ARGS + ["--arrive-by", "2026-10-15 11:30", "--feed", "t=" + p] + self.FIXED + BASE)
        o = option(res, "Hub H")
        self.assertEqual((o["board"], o["arrive"]), (hms(10, 50), hms(11, 26)))   # not the 11:29 arrival
        self.assertEqual(o["book_by"], hms(10, 50) - (360 + int(FREE_S) + th.PLATFORM_S + 180))
        self.assertEqual(res["ride_all"]["arrive"], hms(11, 27))

    def test_walk_between_the_stops_of_one_hub_is_in_the_lead(self):
        stops = HUB_STOPS + [("H2", "Hub H north", 43.491350, -79.8000)]      # 150 m north: one hub
        p = feed(stops, [("X", "WK", "x1", "C"), ("X", "WK", "x0", "C")],
                 [("x1", "10:50:00", "10:50:00", "H2", 1, 0, 0), ("x1", "11:20:00", "11:20:00", "C", 2, 0, 0),
                  ("x0", "11:10:00", "11:10:00", "H", 1, 0, 0), ("x0", "11:45:00", "11:45:00", "C", 2, 0, 0)])
        res = run(HUB_ARGS + ["--arrive-by", "2026-10-15 11:30", "--feed", "t=" + p] + self.FIXED + BASE)
        o = option(res, "Hub H north")
        walk = th.walk_s(th.haversine_m(43.4900, -79.8000, 43.491350, -79.8000))
        self.assertEqual(o["book_by"], hms(10, 50) - (360 + int(FREE_S) + th.PLATFORM_S + walk + 180))

    def test_ride_all_arrival_includes_the_pickup_wait(self):
        res = run(HUB_ARGS + ["--depart", "2026-10-15 10:00", "--feed", "t=" + train_at("10:50:00", "11:20:00"),
                              "--traffic-factor", "1.0", "--pickup-wait-min", "7"] + BASE)
        ra = res["ride_all"]
        self.assertAlmostEqual(ra["arrive"], hms(10, 0) + 7 * 60 + ra["ride_s"])

    def test_no_uber_final_ride_waits_for_a_taxi(self):
        region = {"id": "test", "name": "Test", "timezone": "America/Toronto", "feeds": [],
                  "bbox": {"min_lat": 43.0, "max_lat": 44.5, "min_lon": -80.5, "max_lon": -79.0},
                  "ride_areas": [{"name": "Test Town", "lat": 43.99, "lon": -79.80, "radius_km": 15, "uber": "no"}]}
        stops = [("O", "Origin stop", 43.3010, -79.8000), ("T", "Town stop", 43.9000, -79.8000)]
        p = feed(stops, [("K", "WK", "k1", "T")],
                 [("k1", "06:30:00", "06:30:00", "O", 1, 0, 0), ("k1", "08:30:00", "08:30:00", "T", 2, 0, 0)])
        res = run(["--origin", "43.3000,-79.8000", "--dest", "43.9900,-79.8000", "--depart", "2026-10-15 06:00",
                   "--feed", "t=" + p, "--min-saving", "0", "--traffic-factor", "1.0", "--pickup-wait-min", "6",
                   "--no-discover", "--routing", "estimate"], regions=[region])
        fin = res["finish_with_ride"][0]
        self.assertEqual(fin["arrive"], hms(8, 30) + th.PLATFORM_S + 30 * 60 + fin["legs"][-1]["seconds"])

    def test_arrival_after_midnight_shows_the_next_day(self):
        out = run(HUB_ARGS + ["--depart", "2026-10-15 23:00", "--feed", "t=" + train_at("23:50:00", "24:40:00"),
                              "--traffic-factor", "1.0"] + BASE, as_json=False)
        self.assertIn("ARRIVE 00:40 (+1d)", out)


if __name__ == "__main__":
    unittest.main()
