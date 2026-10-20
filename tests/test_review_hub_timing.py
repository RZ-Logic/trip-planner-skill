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
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import hms, make_feed, pin_clock, th  # noqa: E402  (test_engine adds the scripts folder)

BASE = ["--region", "none", "--no-discover", "--routing", "estimate"]
DAY = dt.date(2026, 10, 15)   # a Thursday
_CLOCK = pin_clock()


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


def ok_network(hosts, timeout=6):
    return {h: "ok" for h in hosts}


def feed(stops, trips, stop_times, calendar=None):
    p = os.path.join(tempfile.mkdtemp(dir=_TMP), "f.zip")
    make_feed(p, stops, trips, stop_times, calendar=calendar)
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


if __name__ == "__main__":
    unittest.main()
