"""Regression tests for the v0.9.8 hub-finder fixes (trip_hubs.py), each reproduced first, plus mocked coverage of
the OSRM, Transitous, catalog and discovery paths that no test ran before.

Offline: synthetic feeds, every network call mocked, a fixed clock.
Run: python -m unittest tests.test_v098_hubs -v
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
import glob
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import FIXED_NOW_UTC, STOPS, hms, make_feed, pin_clock, th  # noqa: E402
from test_regressions import add_files  # noqa: E402

DAY = dt.date(2026, 10, 15)   # a Thursday
ALL_DAYS = [("ALL", 1, 1, 1, 1, 1, 1, 1, "20260101", "20271231")]
BASE = ["--region", "none", "--no-discover", "--routing", "estimate"]
REGIONS = {r["id"]: r for r in th.load_regions()}
GTHA, ONTARIO = REGIONS["gtha"], REGIONS["ontario"]
_CLOCK = pin_clock()
_ENV = None


def setUpModule():
    global _ENV
    _CLOCK.start()
    tmp = tempfile.mkdtemp()
    _ENV = mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": os.path.join(tmp, "cache"),
                                        "TRIP_PLANNER_CONFIG": os.path.join(tmp, "config", "car.json")})
    _ENV.start()


def tearDownModule():
    _ENV.stop()
    _CLOCK.stop()


def ok_network(hosts, timeout=6):
    return {h: "ok" for h in hosts}


def feed(stops, trips, stop_times, calendar=None, name="f.zip"):
    p = os.path.join(tempfile.mkdtemp(), name)
    make_feed(p, stops, trips, stop_times, calendar=calendar)
    return p


def run(args, as_json=True, err=None):
    """main(args) -> parsed --json result, or the text output."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err if err is not None else io.StringIO()):
        th.main(args + (["--json"] if as_json else []))
    return json.loads(out.getvalue()) if as_json else out.getvalue()


def leg(agency, route, a, b, depart, feed_id=None):
    return {"type": "ride", "feed": feed_id or agency, "agency": agency, "route": route, "route_id": route,
            "from": a, "to": b, "depart": depart, "arrive": depart + 1200, "from_idx": 0, "to_idx": 0}


class NoFares:
    fares, fare_urls, stop_zone = {}, {}, [""]


class FakeResponse:
    """A urlopen() reply: read(n) in chunks, headers, context manager. `reads` counts read calls."""

    def __init__(self, body, headers=None):
        self.body, self.headers, self.reads = body, headers or {}, 0

    def read(self, n=-1):
        self.reads += 1
        n = len(self.body) if n is None or n < 0 else n
        chunk, self.body = self.body[:n], self.body[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def osrm_reply(url):
    """A fake OSRM table answer: driving at 15 m/s over 1.3 x the straight line from the first point."""
    coords = urllib.parse.urlsplit(url).path.split("/driving/")[1].split(";")
    pts = [tuple(float(x) for x in c.split(",")) for c in coords]   # lon, lat
    (lon0, lat0), rest = pts[0], pts[1:]
    dist = [th.haversine_m(lat0, lon0, lat, lon) * 1.3 for lon, lat in rest]
    return json.dumps({"code": "Ok", "durations": [[0.0] + [d / 15.0 for d in dist]],
                       "distances": [[0.0] + dist]}).encode()


# ---------------------------------------------------------------------------------------------------- item 1
class TimeZoneAliases(unittest.TestCase):
    def test_canada_and_us_link_names_without_a_tz_database(self):
        summer, winter = dt.datetime(2026, 7, 15, 12), dt.datetime(2026, 1, 15, 12)
        want = {"Canada/Eastern": (-4, -5), "Canada/Central": (-5, -6), "Canada/Mountain": (-6, -7),
                "Canada/Pacific": (-7, -8), "Canada/Atlantic": (-3, -4), "Canada/Newfoundland": (-2.5, -3.5),
                "Canada/Saskatchewan": (-6, -6), "Canada/Yukon": (-7, -7), "US/Eastern": (-4, -5),
                "US/Central": (-5, -6), "US/Mountain": (-6, -7), "US/Pacific": (-7, -8), "US/Arizona": (-7, -7),
                "EST5EDT": (-4, -5), "CST6CDT": (-5, -6), "MST7MDT": (-6, -7), "PST8PDT": (-7, -8)}
        with mock.patch.object(th, "_zoneinfo", return_value=None):
            for zone, (s, w) in want.items():
                self.assertEqual(th.offset_at_local(summer, zone, None), (dt.timedelta(hours=s), "built-in rule"), zone)
                self.assertEqual(th.offset_at_local(winter, zone, None)[0], dt.timedelta(hours=w), zone)
        self.assertTrue(th.same_clock("Canada/Eastern", "America/Toronto"))   # OC Transpo needs no shift at all
        self.assertEqual(th.local_currency("Canada/Pacific"), "CAD")

    def zone_feed(self, tz):
        p = feed(STOPS, [("1", "WK", "t1", "C")],
                 [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:30:00", "10:30:00", "C", 2, 0, 0)])
        add_files(p, {"agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nT,Test,http://x,%s\n" % tz})
        net = th.Network()
        net.warnings = []
        with mock.patch.object(th, "_zoneinfo", return_value=None), redirect_stderr(io.StringIO()):
            th.load_feed(net, "t", "test", p, DAY, hms(9, 0), hms(13, 0), local_tz="America/Toronto")
        return net

    def test_canada_eastern_feed_keeps_its_times(self):
        net = self.zone_feed("Canada/Eastern")
        self.assertEqual(sorted(c[0] for c in net.conns), [hms(10, 0)])
        self.assertEqual(net.warnings, [])

    def test_unknown_zone_warns_instead_of_using_the_machine_clock(self):
        # Mars/Olympus is in no table: the old code shifted by the computer's own UTC offset without a word
        net = self.zone_feed("Mars/Olympus")
        self.assertEqual(sorted(c[0] for c in net.conns), [hms(10, 0)])   # unchanged, not shifted
        self.assertTrue(any("cannot convert Mars/Olympus" in w and "tzdata" in w for w in net.warnings),
                        net.warnings)


# ---------------------------------------------------------------------------------------------------- item 2, 16
class PaperTransfers(unittest.TestCase):
    def fare(self, legs, payment="card"):
        return th.journey_fare(NoFares, legs, GTHA["flat_fares"], payment, GTHA.get("card_discount"),
                               GTHA["transfer_rules"], DAY)

    def test_leg_boarded_on_a_milton_paper_transfer_is_not_a_card_tap(self):
        legs = [leg("Milton Transit", "21", "Milton GO", "Lisgar GO Station", hms(21, 0)),
                leg("Brampton Transit", "511", "Lisgar GO Station", "Steeles / Mavis", hms(21, 25)),
                leg("MiWay", "61", "Steeles / Mavis", "City Centre Transit Terminal", hms(21, 50))]
        total, _cur, parts, complete = self.fare(legs)
        self.assertEqual([p["fare"] for p in parts], [4.25, 0.0, 3.5])   # was 4.25 in all
        self.assertEqual((total, complete), (7.75, True))
        self.assertIn("free from Milton Transit", parts[1]["note"])
        self.assertEqual(self.fare(legs, "cash")[0], 8.75)

    def test_card_taps_still_transfer(self):
        legs = [leg("Brampton Transit", "511", "A", "Steeles / Mavis", hms(21, 0)),
                leg("MiWay", "61", "Steeles / Mavis", "B", hms(21, 25))]
        self.assertEqual([p["fare"] for p in self.fare(legs)[2]], [3.55, 0.0])

    def test_paper_only_survives_a_merge_of_region_files(self):
        view = th.merge_regions(GTHA, [ONTARIO], 43.6, -79.6)
        self.assertEqual(view["transfer_rules"]["paper_only"], ["Milton Transit"])

    def test_one_feed_far_apart_pays_twice(self):
        far = [leg("MiWay", "1", "X", "Y", hms(21, 0)), leg("MiWay", "2", "Y", "Z", hms(23, 30))]
        total, _c, parts, _ok = self.fare(far)
        self.assertEqual((total, len(parts)), (7.0, 2))                     # 150 min apart, 120 min window
        near = [leg("MiWay", "1", "X", "Y", hms(21, 0)), leg("MiWay", "2", "Y", "Z", hms(22, 30))]
        total, _c, parts, _ok = self.fare(near)
        self.assertEqual((total, len(parts)), (3.5, 1))


# ---------------------------------------------------------------------------------------------------- item 3
class TrafficAtTheRideTime(unittest.TestCase):
    def test_arrive_by_prices_each_ride_at_its_own_start(self):
        stops = [("H", "Hub H", 43.4900, -79.8000), ("C", "Dest C", 43.6000, -79.8000)]
        p = feed(stops, [("X", "WK", "x1", "C")],
                 [("x1", "08:30:00", "08:30:00", "H", 1, 0, 0), ("x1", "08:50:00", "08:50:00", "C", 2, 0, 0)])
        res = run(["--origin", "43.3000,-79.8000", "--dest", "43.6000,-79.8000", "--arrive-by", "2026-10-15 11:30",
                   "--feed", "t=" + p, "--min-saving", "0", "--radius-km", "25", "--origin-walk-m", "300"] + BASE)
        o = next(x for x in res["pareto"] + res["others"] if x["hub"] == "Hub H")
        free_min = th.heuristic_ride(th.haversine_m(43.3, -79.8, 43.49, -79.8))[1] / 60.0
        self.assertAlmostEqual(o["ride_min"], round(free_min * 1.45, 1), places=1)   # 07:44 peak, not 10:30's x1.20
        self.assertEqual(th.fmt_t(o["book_by"]), "07:44")                            # was 07:50
        ra = res["ride_all"]
        free_all = th.heuristic_ride(th.haversine_m(43.3, -79.8, 43.6, -79.8))[1]
        pickup = dt.datetime.combine(DAY, dt.time()) + dt.timedelta(seconds=ra["arrive"] - ra["ride_s"])
        self.assertAlmostEqual(ra["ride_s"] / free_all, th.traffic_factor_for(pickup))
        fixed = run(["--origin", "43.3000,-79.8000", "--dest", "43.6000,-79.8000", "--arrive-by", "2026-10-15 11:30",
                     "--feed", "t=" + p, "--min-saving", "0", "--radius-km", "25", "--origin-walk-m", "300",
                     "--traffic-factor", "1.3"] + BASE)
        o = next(x for x in fixed["pareto"] + fixed["others"] if x["hub"] == "Hub H")
        self.assertAlmostEqual(o["ride_min"], round(free_min * 1.3, 1), places=1)   # an explicit factor stays

    def test_final_ride_is_priced_when_it_starts(self):
        stops = [("O", "Origin stop", 43.3010, -79.8000), ("T", "Town stop", 43.9000, -79.8000)]
        p = feed(stops, [("K", "WK", "k1", "T")],
                 [("k1", "06:30:00", "06:30:00", "O", 1, 0, 0), ("k1", "08:30:00", "08:30:00", "T", 2, 0, 0)])
        res = run(["--origin", "43.3000,-79.8000", "--dest", "43.9900,-79.8000", "--depart", "2026-10-15 06:00",
                   "--feed", "t=" + p, "--min-saving", "0"] + BASE)
        fin = res["finish_with_ride"][0]
        free_min = th.heuristic_ride(th.haversine_m(43.9, -79.8, 43.99, -79.8))[1] / 60.0
        self.assertAlmostEqual(fin["final_ride"]["min"], round(free_min * 1.45, 1), places=1)   # 08:3x peak
        self.assertAlmostEqual(fin["legs"][-1]["seconds"] / 60.0, fin["final_ride"]["min"], delta=0.1)
        self.assertGreater(fin["arrive"], hms(8, 30) + free_min * 1.45 * 60)


# ---------------------------------------------------------------------------------------------------- item 4
class ClosedStationLists(unittest.TestCase):
    def ttc(self, name, lat, lon, lot_lat):
        net = th.Network()
        s0 = net.add_stop(name + " - Eastbound Platform", lat, lon, "TTC", "")
        s1 = net.add_stop("Next Station - Eastbound Platform", lat, lon + 0.011, "TTC", "")
        net.trips.append(("TTC", "TTC", "2", "Line 2", 1, "Kennedy", "t1", "2"))
        net.conns.append((hms(8, 0), hms(8, 2), s0, s1, 0, True, True, 0))
        clusters = [{"lat": lat, "lon": lon, "stops": [s0], "straight_m": 10000.0}]
        lots = th.osm_lots([{"type": "way", "id": 1, "center": {"lat": lot_lat, "lon": lon},
                             "tags": {"amenity": "parking", "fee": "no"}}])
        return th.hub_parking(net, clusters, [GTHA["park_and_ride"]], lots, 240.0, DAY, set())

    def test_osm_lot_does_not_make_an_unlisted_ttc_station_a_hub(self):
        kept, _summary = self.ttc("Old Mill Station", 43.6500, -79.4950, 43.6522)   # lot 245 m away, fee=no
        self.assertEqual(kept, [])

    def test_osm_lot_does_not_beat_the_listed_ttc_rule(self):
        kept, _summary = self.ttc("Wilson Station", 43.7348, -79.4500, 43.7366)    # listed: $7 on weekdays
        self.assertEqual(kept[0]["parking"]["basis"], "TTC rule")
        self.assertEqual(kept[0]["parking"]["cost"], 7.0)


# ---------------------------------------------------------------------------------------------------- item 5
class HiddenHubsAreCounted(unittest.TestCase):
    def test_hubs_past_top_are_counted(self):
        stops = [("H%d" % k, "Hub %d" % k, 43.40 + 0.01 * k, -79.80) for k in range(1, 11)] + [
            ("C", "Dest C", 43.60, -79.80)]
        trips, st = [], []
        for k in range(1, 11):
            trips.append(("R%d" % k, "WK", "h%d" % k, "C"))
            st += [("h%d" % k, "10:30:00", "10:30:00", "H%d" % k, 1, 0, 0),
                   ("h%d" % k, "11:%02d:00" % (2 * k), "11:%02d:00" % (2 * k), "C", 2, 0, 0)]
        p = feed(stops, trips, st)
        args = ["--origin", "43.4000,-79.8000", "--dest", "43.6000,-79.8000", "--depart", "2026-10-15 10:00",
                "--feed", "t=" + p, "--min-saving", "0", "--radius-km", "15", "--origin-walk-m", "300",
                "--top", "3"] + BASE
        res = run(args)
        shown = len(res["pareto"]) + len(res["others"])
        self.assertEqual(shown, 3)
        self.assertEqual(res["hidden_hubs"], {"same_vehicle": 0, "past_top": 7, "top": 3})
        self.assertIn("NOT SHOWN  7 more hubs that beat the baselines: 7 past --top 3", run(args, as_json=False))

    def test_hubs_on_a_listed_vehicle_are_counted(self):
        stops = [("H1", "Hub One", 43.4100, -79.8000), ("H2", "Hub Two", 43.4200, -79.8000),
                 ("C", "Dest C", 43.6000, -79.8000)]
        p = feed(stops, [("R", "WK", "x1", "C")],
                 [("x1", "10:30:00", "10:30:00", "H1", 1, 0, 0), ("x1", "10:33:00", "10:33:00", "H2", 2, 0, 0),
                  ("x1", "11:00:00", "11:00:00", "C", 3, 0, 0)])
        res = run(["--origin", "43.4000,-79.8000", "--dest", "43.6000,-79.8000", "--depart", "2026-10-15 10:00",
                   "--feed", "t=" + p, "--min-saving", "0", "--radius-km", "15", "--origin-walk-m", "50"] + BASE)
        listed = len(res["pareto"]) + len(res["others"])
        self.assertEqual(listed + res["hidden_hubs"]["same_vehicle"] + res["hidden_hubs"]["past_top"], 2)
        self.assertEqual(res["hidden_hubs"]["same_vehicle"], 2 - listed)


# ---------------------------------------------------------------------------------------------------- items 6, 7
class ClockAndEnvironment(unittest.TestCase):
    def test_hub_tests_pass_on_the_trip_day(self):
        import test_engine
        import test_park_ride
        suite = unittest.TestSuite()
        for case in (test_engine.PlanTests, test_park_ride.CarModePlan):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        noon_on_trip_day = dt.datetime(2026, 10, 15, 16, 0)   # 12:00 EDT, the hour 23 tests used to fail
        result = unittest.TestResult()
        with mock.patch.object(th, "utc_now", return_value=noon_on_trip_day), redirect_stderr(io.StringIO()):
            suite.run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(th.utc_now(), FIXED_NOW_UTC)

    def test_cache_test_restores_an_exported_cache_path(self):
        import test_regressions
        mine = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": mine}):
            result = unittest.TestResult()
            test_regressions.ReviewRegressions("test_cache_name_depends_on_url").run(result)
            self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
            self.assertEqual(os.environ.get("TRIP_PLANNER_CACHE"), mine)


# ---------------------------------------------------------------------------------------------------- item 8
class OsrmAndPlanRouting(unittest.TestCase):
    def test_osrm_table_batches_caps_and_errors(self):
        pts = [(43.5 + i * 0.001, -79.8) for i in range(5)]
        with mock.patch.object(th, "OSRM_BATCH", 2), mock.patch.object(th, "OSRM_MAX_REQUESTS", 2), \
                mock.patch.object(th, "http_get", side_effect=lambda url, **k: osrm_reply(url)) as hg:
            out = th.osrm_table("https://osrm.example/", (43.49, -79.8), pts)
        self.assertEqual(hg.call_count, 2)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[4], None)                          # past the request cap
        self.assertTrue(all(isinstance(x, tuple) and x[0] > 0 for x in out[:4]))
        self.assertIn("osrm.example/table/v1/driving/-79.80000,43.49000;", hg.call_args_list[0][0][0])
        with mock.patch.object(th, "http_get", return_value=json.dumps({"code": "NoRoute"}).encode()):
            with self.assertRaises(RuntimeError):
                th.osrm_table("https://osrm.example", (43.49, -79.8), pts)
        nulls = json.dumps({"code": "Ok", "durations": [[0, None]], "distances": [[0, 5.0]]}).encode()
        with mock.patch.object(th, "http_get", return_value=nulls):
            self.assertEqual(th.osrm_table("https://osrm.example", (43.49, -79.8), pts[:1]), [None])

    def grid_feed(self):
        stops = [("G%d" % k, "Grid %d" % k, 43.42 + 0.01 * k, -79.80) for k in range(6)] + [("C", "Dest", 43.7, -79.8)]
        trips, st = [], []
        for k in range(6):
            trips.append(("R%d" % k, "WK", "g%d" % k, "C"))
            st += [("g%d" % k, "10:%02d:00" % (30 + k), "10:%02d:00" % (30 + k), "G%d" % k, 1, 0, 0),
                   ("g%d" % k, "11:30:00", "11:30:00", "C", 2, 0, 0)]
        return feed(stops, trips, st)

    def plan(self, extra, as_json=True):
        args = ["--origin", "43.4000,-79.8000", "--dest", "43.7000,-79.8000", "--depart", "2026-10-15 10:00",
                "--region", "none", "--no-discover", "--feed", "t=" + self.grid_feed(), "--min-saving", "0",
                "--origin-walk-m", "50"] + extra
        with mock.patch.object(th, "check_network", side_effect=ok_network):
            return run(args, as_json)

    def test_plan_uses_osrm_times(self):
        with mock.patch.object(th, "http_get", side_effect=lambda url, **k: osrm_reply(url)):
            res = self.plan([])
        self.assertTrue(res["ride_all"]["source"].startswith("osrm x"))
        opts = res["pareto"] + res["others"]
        self.assertTrue(opts and all(o["ride_source"].startswith("osrm x") for o in opts))
        self.assertEqual(res["hubs_routed"]["estimate"], 0)

    def test_request_cap_is_reported_in_coverage(self):
        with mock.patch.object(th, "OSRM_BATCH", 2), mock.patch.object(th, "OSRM_MAX_REQUESTS", 1), \
                mock.patch.object(th, "http_get", side_effect=lambda url, **k: osrm_reply(url)):
            res = self.plan([])
            txt = self.plan([], as_json=False)
        self.assertEqual(res["hubs_routed"], {"osrm": 1, "estimate": 5})
        self.assertIn("6 of them reach the destination in the window (1 with OSRM driving times, 5 on rough "
                      "estimates past the request cap).", txt)

    def test_osrm_failure_falls_back_or_stops(self):
        with mock.patch.object(th, "http_get", side_effect=OSError("down")):
            res = self.plan([])
            self.assertTrue(any("OSRM unavailable" in w for w in res["warnings"]))
            self.assertEqual(res["ride_all"]["source"], "estimate")
            with self.assertRaises(SystemExit) as ctx:
                self.plan(["--routing", "osrm"])
        self.assertIn("OSRM unavailable", str(ctx.exception))


class TransitousCrossCheck(unittest.TestCase):
    REPLY = {"itineraries": [{"legs": [
        {"mode": "WALK", "startTime": "2026-10-15T12:11:40Z", "endTime": "2026-10-15T12:20:00Z",
         "from": {"name": "Origin"}, "to": {"name": "Hub A"}},
        {"mode": "BUS", "agencyName": "Test Transit", "routeShortName": "1", "startTime": "2026-10-15T12:20:00Z",
         "endTime": "2026-10-15T12:40:00Z", "from": {"name": "Hub A"}, "to": {"name": "Dest C"}},
        {"mode": "WALK", "startTime": "2026-10-15T12:40:00Z", "endTime": "2026-10-15T12:40:30Z",
         "from": {"name": "Dest C"}, "to": {"name": "Destination"}}]}]}

    def test_transitous_parses_local_times_and_the_first_boarding(self):
        with mock.patch.object(th, "_zoneinfo", return_value=None), \
                mock.patch.object(th, "http_get", return_value=json.dumps(self.REPLY).encode()) as hg:
            its, how = th.transitous((43.4955, -79.8), (43.55, -79.8), dt.datetime(2026, 10, 15, 9, 0), True,
                                     "America/Toronto", None)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(hg.call_args[0][0]).query)
        self.assertEqual((q["time"], q["arriveBy"]), (["2026-10-15T13:00:00Z"], ["true"]))
        self.assertEqual(how, "built-in rule")
        self.assertEqual(its[0]["hub"], "Hub A")
        self.assertEqual(its[0]["depart_dt"], dt.datetime(2026, 10, 15, 8, 11, 40))
        self.assertEqual(its[0]["board_dt"], dt.datetime(2026, 10, 15, 8, 20))
        self.assertEqual(its[0]["legs"], ["Test Transit 1 Hub A->Dest C 08:20-08:40"])
        walk_only = {"itineraries": [{"legs": [self.REPLY["itineraries"][0]["legs"][0]]}]}
        with mock.patch.object(th, "http_get", return_value=json.dumps(walk_only).encode()):
            self.assertEqual(th.transitous((43.4955, -79.8), (43.55, -79.8), dt.datetime(2026, 10, 15, 9), True,
                                           "America/Toronto", None)[0], [])

    def crosscheck(self, when, reply):
        stops = [("A", "Hub A", 43.5000, -79.8000), ("C", "Dest C", 43.5500, -79.8000)]
        p = feed(stops, [("1", "WK", "a1", "C")],
                 [("a1", "08:20:00", "08:20:00", "A", 1, 0, 0), ("a1", "08:40:00", "08:40:00", "C", 2, 0, 0)])
        with mock.patch.object(th, "http_get", return_value=json.dumps(reply).encode()), \
                mock.patch.object(th, "check_network", side_effect=ok_network):
            return run(["--origin", "43.4955,-79.8000", "--dest", "43.5500,-79.8000", "--feed", "t=" + p,
                        "--crosscheck"] + when + BASE)

    def test_arrive_by_verdict_compares_the_first_vehicles(self):
        res = self.crosscheck(["--arrive-by", "2026-10-15 09:00"], self.REPLY)
        cc = res["crosscheck"]["transit_only"]
        self.assertEqual(cc["transitous"]["depart"], "08:11")     # its walk starts 8 min before the bus
        self.assertEqual(cc["verdict"], "agrees")                 # was "Transitous leaves 8 min earlier"

    def test_depart_verdict_flags_a_faster_route(self):
        faster = json.loads(json.dumps(self.REPLY))
        faster["itineraries"][0]["legs"][1]["endTime"] = "2026-10-15T12:30:00Z"
        faster["itineraries"][0]["legs"][2].update(startTime="2026-10-15T12:30:00Z", endTime="2026-10-15T12:30:30Z")
        res = self.crosscheck(["--depart", "2026-10-15 08:00"], faster)
        self.assertIn("check its route", res["crosscheck"]["transit_only"]["verdict"])
        self.assertTrue(any("Transitous finds a better connection for: transit only" in w for w in res["warnings"]))
        with mock.patch.object(th, "http_get", side_effect=OSError("down")):
            stops = [("A", "Hub A", 43.5000, -79.8000), ("C", "Dest C", 43.5500, -79.8000)]
            p = feed(stops, [("1", "WK", "a1", "C")],
                     [("a1", "08:20:00", "08:20:00", "A", 1, 0, 0), ("a1", "08:40:00", "08:40:00", "C", 2, 0, 0)])
            with mock.patch.object(th, "check_network", side_effect=ok_network):
                res = run(["--origin", "43.4955,-79.8000", "--dest", "43.5500,-79.8000", "--feed", "t=" + p,
                           "--crosscheck", "--depart", "2026-10-15 08:00"] + BASE)
        self.assertTrue(any("Transitous cross-check (transit only) failed" in w for w in res["warnings"]))


class CatalogAndDiscovery(unittest.TestCase):
    CSV = ("id,data_type,status,provider,name,location.country_code,location.subdivision_name,"
           "location.municipality,location.bounding_box.minimum_latitude,location.bounding_box.maximum_latitude,"
           "location.bounding_box.minimum_longitude,location.bounding_box.maximum_longitude,urls.latest,"
           "urls.direct_download,urls.authentication_type,is_official\n"
           "mdb-1,gtfs,active,Town Bus,,CA,ontario,Town,43.4,43.6,-79.9,-79.7,https://files.example/mdb-1.zip,,0,True\n")

    def test_load_catalog_downloads_once_then_uses_the_cache(self):
        with mock.patch.object(th, "http_get", return_value=self.CSV.encode()) as hg, \
                redirect_stderr(io.StringIO()):
            rows = th.load_catalog()
        self.assertEqual(hg.call_count, 1)
        self.assertEqual(rows[0]["provider"], "Town Bus")
        with mock.patch.object(th, "http_get", side_effect=AssertionError("cached")):
            self.assertEqual(len(th.load_catalog()), 1)
        old = time.time() - 30 * 86400
        os.utime(os.path.join(th.cache_dir(), "mobilitydatabase_feeds.csv"), (old, old))
        with mock.patch.object(th, "http_get", side_effect=OSError("offline")), redirect_stderr(io.StringIO()):
            self.assertEqual(len(th.load_catalog()), 1)          # a stale copy beats nothing
        os.remove(os.path.join(th.cache_dir(), "mobilitydatabase_feeds.csv"))
        with mock.patch.object(th, "http_get", side_effect=OSError("offline")), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                th.load_catalog()

    def test_reverse_place_info_sends_town_level_coordinates(self):
        reply = {"address": {"town": "Milton", "county": "Halton", "country_code": "ca", "state": "Ontario"}}
        with mock.patch.object(th, "http_get", return_value=json.dumps(reply).encode()) as hg:
            info = th.reverse_place_info(43.498757, -79.855987, [])
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(hg.call_args[0][0]).query)
        self.assertEqual((q["lat"], q["lon"], q["zoom"]), (["43.499"], ["-79.856"], ["10"]))   # not the door
        self.assertEqual(info, {"names": {"milton", "halton"}, "country": "CA", "state": "ontario"})
        w = []
        with mock.patch.object(th, "http_get", side_effect=OSError("offline")):
            self.assertEqual(th.reverse_place_info(43.5, -79.8, w)["names"], set())
        self.assertTrue(w and "reverse geocoding failed" in w[0])

    def test_discover_command_text_and_json(self):
        rows = list(__import__("csv").DictReader(io.StringIO(self.CSV)))
        rows[0]["provider"] = "Town\x1b[31m Bus"
        info = {"names": {"town"}, "country": "CA", "state": "ontario"}
        with mock.patch.object(th, "load_catalog", return_value=rows), \
                mock.patch.object(th, "reverse_place_info", return_value=info):
            txt = run(["--discover", "--origin", "43.5000,-79.8000", "--dest", "43.5500,-79.7500"], as_json=False)
            res = run(["--discover", "--origin", "43.5000,-79.8000"])
        self.assertIn("mdb-1", txt)
        self.assertIn("https://files.example/mdb-1.zip", txt)
        self.assertNotIn("\x1b", txt)                             # catalog text is data
        self.assertIn("Use with: --feed <id>=<url>", txt)
        self.assertEqual([f["id"] for f in res["feeds"]], ["mdb-1"])

    def test_catalog_urls_must_be_web_downloads(self):
        rows = [{"data_type": "gtfs", "status": "active", "id": "mdb-999", "provider": "Evil", "name": "x",
                 "urls.latest": "file:///C:/Users/Public/x.zip", "urls.direct_download": "ftp://example.org/x.zip",
                 "urls.authentication_type": "0", "location.bounding_box.minimum_latitude": "43.4",
                 "location.bounding_box.maximum_latitude": "43.6", "location.bounding_box.minimum_longitude": "-79.9",
                 "location.bounding_box.maximum_longitude": "-79.7"}]
        box = {"min_lat": 43.45, "max_lat": 43.55, "min_lon": -79.85, "max_lon": -79.75}
        self.assertEqual(th.discover_feeds(box, rows=rows)[0], [])
        rows[0]["urls.direct_download"] = "HTTPS://example.org/x.zip"
        self.assertEqual(th.discover_feeds(box, rows=rows)[0][0]["urls"], ["HTTPS://example.org/x.zip"])

    def destination_trip(self, catalog):
        """A region feed with stops at both ends but no bbox, on a 33 km trip: feeds are searched near the
        destination too."""
        stops = [("A", "Home stop", 43.5000, -79.8000), ("Z", "Far stop", 43.8000, -79.8000)]
        local = feed(stops, [("1", "WK", "t1", "Z")],
                     [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:50:00", "10:50:00", "Z", 2, 0, 0)])
        there = feed([("D1", "Dest stop", 43.8010, -79.8000), ("D2", "Dest centre", 43.8100, -79.8000)],
                     [("2", "WK", "d1", "D2")],
                     [("d1", "11:00:00", "11:00:00", "D1", 1, 0, 0), ("d1", "11:10:00", "11:10:00", "D2", 2, 0, 0)])
        region = {"id": "home", "timezone": "America/Toronto", "bbox": {"min_lat": 43.3, "max_lat": 43.7,
                                                                        "min_lon": -80.0, "max_lon": -79.6},
                  "feeds": [{"id": "local", "name": "Home Bus", "urls": [local]}]}
        urls = {"https://files.example/dest.zip": there, local: local}
        with mock.patch.object(th, "load_regions", return_value=[region]), \
                mock.patch.object(th, "check_network", side_effect=ok_network), \
                mock.patch.object(th, "load_catalog", side_effect=catalog), \
                mock.patch.object(th, "reverse_place_info", return_value={"names": set(), "country": "CA",
                                                                          "state": None}), \
                mock.patch.object(th, "fetch_url", side_effect=lambda fid, url, *a, **k: urls[url]):
            return run(["--origin", "43.5000,-79.8000", "--dest", "43.8100,-79.8000", "--depart", "2026-10-15 09:30",
                        "--routing", "estimate", "--min-saving", "0"])

    def test_destination_side_discovery(self):
        row = {"id": "mdb-7", "data_type": "gtfs", "status": "active", "provider": "Dest Transit", "name": "",
               "urls.latest": "https://files.example/dest.zip", "urls.direct_download": "",
               "urls.authentication_type": "0", "is_official": "True",
               "location.bounding_box.minimum_latitude": "43.75", "location.bounding_box.maximum_latitude": "43.85",
               "location.bounding_box.minimum_longitude": "-79.85", "location.bounding_box.maximum_longitude": "-79.75"}
        res = self.destination_trip(lambda max_age_h=168.0: [row])
        self.assertIn("Dest Transit", [f["name"] for f in res["feeds"]])
        self.assertTrue(any("feeds found there: Dest Transit" in w for w in res["warnings"]), res["warnings"])
        res = self.destination_trip(lambda max_age_h=168.0: (_ for _ in ()).throw(SystemExit("catalog offline")))
        self.assertTrue(any("could not search the feed catalog near the destination: catalog offline" in w
                            for w in res["warnings"]), res["warnings"])


class OverpassCache(unittest.TestCase):
    BOX = th.box_around(43.5, -79.8, 3000)

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def reply(self, *ids):
        return json.dumps({"elements": [{"type": "node", "id": i, "lat": 43.5, "lon": -79.8,
                                         "tags": {"amenity": "parking"}} for i in ids]}).encode()

    def cache_file(self):
        return glob.glob(os.path.join(th.cache_dir(), "overpass-*.json"))[0]

    def test_corrupt_cache_is_fetched_again(self):
        with mock.patch.object(th, "http_get", return_value=self.reply(1)):
            th.overpass_parking(self.BOX, [])
        with open(self.cache_file(), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with mock.patch.object(th, "http_get", return_value=self.reply(2)) as hg:
            els = th.overpass_parking(self.BOX, [])
        self.assertEqual((hg.call_count, [e["id"] for e in els]), (1, [2]))

    def test_error_remark_is_a_failure_and_not_cached(self):
        busy = json.dumps({"remark": "runtime error: Query timed out in \"query\"", "elements": []}).encode()
        w = []
        with mock.patch.object(th, "http_get", return_value=busy):
            self.assertIsNone(th.overpass_parking(self.BOX, w))
        self.assertTrue(w and "Overpass: runtime error" in w[0])
        self.assertEqual(glob.glob(os.path.join(th.cache_dir(), "overpass-*.json")), [])

    def test_stale_copy_is_used_with_a_warning(self):
        with mock.patch.object(th, "http_get", return_value=self.reply(3)):
            th.overpass_parking(self.BOX, [])
        old = time.time() - 3 * 86400
        os.utime(self.cache_file(), (old, old))
        w = []
        with mock.patch.object(th, "http_get", side_effect=OSError("offline")):
            els = th.overpass_parking(self.BOX, w)
        self.assertEqual([e["id"] for e in els], [3])
        self.assertTrue(w and "used a cached copy older than 24 h" in w[0])


# ---------------------------------------------------------------------------------------------------- items 9, 10
class SecretsAndControlCharacters(unittest.TestCase):
    URL = "https://feeds.example/gtfs.zip?apikey=SECRET_KEY_123&v=2"

    def test_safe_url(self):
        self.assertEqual(th.safe_url(self.URL), "https://feeds.example/gtfs.zip?apikey=REDACTED&v=REDACTED")
        self.assertEqual(th.safe_url("http://alice:PASSWD_456@feeds.example/g.zip"), "http://feeds.example/g.zip")
        self.assertEqual(th.safe_url(r"C:\feeds\g.zip"), r"C:\feeds\g.zip")
        self.assertEqual(th.parse_feed_spec("go=" + self.URL, set()), ("go", self.URL))   # the real URL is kept
        ns = th.argparse.Namespace(origin="a", dest="b", region="none", routing="estimate", crosscheck=False,
                                   feed=["k=http://alice:PASSWD_456@feeds.example/g.zip"], no_discover=True)
        self.assertIn("feeds.example", th.trip_domains(ns, [])[0])
        self.assertNotIn("PASSWD_456", " ".join(th.trip_domains(ns, [])[0]))

    def test_key_never_printed(self):
        good = feed(STOPS, [("X", "WK", "x1", "C")],
                    [("x1", "10:20:00", "10:20:00", "D", 1, 0, 0), ("x1", "10:35:00", "10:35:00", "C", 2, 0, 0)])
        boom = urllib.error.URLError("unknown url type: '%s'" % self.URL)
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 10:00",
                "--min-saving", "0", "--radius-km", "5", "--origin-walk-m", "300"] + BASE
        err = io.StringIO()
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=boom), \
                mock.patch.object(th, "check_network", side_effect=ok_network), \
                mock.patch.dict(os.environ, {"TRIP_PLANNER_QUIET": "0"}):   # the stderr lines are checked
            res = run(args + ["--feed", "good=" + good, "--feed", "k=" + self.URL], err=err)
            txt = run(args + ["--feed", "good=" + good, "--feed", "k=" + self.URL], as_json=False)
            with self.assertRaises(SystemExit) as ctx:
                run(args + ["--feed", "k=" + self.URL], err=err)
        self.assertTrue(any("feed k failed to load" in w for w in res["warnings"]))
        for where in (json.dumps(res), txt, err.getvalue(), str(ctx.exception)):
            self.assertNotIn("SECRET_KEY_123", where)
        self.assertIn("apikey=REDACTED", err.getvalue())

    def test_stale_copy_warning_hides_the_key(self):
        url = "https://feeds.example/g.zip?token=SECRET_KEY_123"
        path = os.path.join(th.cache_dir(), "k-%s.zip" % __import__("hashlib").sha1(url.encode()).hexdigest()[:10])
        make_feed(path, STOPS, [("X", "WK", "x1", "C")],
                  [("x1", "10:20:00", "10:20:00", "D", 1, 0, 0), ("x1", "10:35:00", "10:35:00", "C", 2, 0, 0)])
        os.utime(path, (time.time() - 10 * 86400,) * 2)
        w, err = [], io.StringIO()
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=ValueError("bad URL %s" % url)), \
                redirect_stderr(err), mock.patch.dict(os.environ, {"TRIP_PLANNER_QUIET": "0"}):
            self.assertEqual(th.fetch_url("k", url, 72, False, w), path)
        self.assertTrue(w and "used a cached copy" in w[0])
        self.assertIn("could not refresh k", err.getvalue())
        self.assertNotIn("SECRET_KEY_123", w[0] + err.getvalue())

    def test_control_characters_from_the_feed_never_reach_the_terminal(self):
        esc = "Hub D\x1b[2J\x1b]0;TITLE\x07\x1b[31mRED\u202e"
        stops = [(s[0], esc if s[0] == "D" else s[1], s[2], s[3]) for s in STOPS]
        p = feed(stops, [("X", "WK", "x1", "express\x1b[5mBLINK")],
                 [("x1", "10:20:00", "10:20:00", "D", 1, 0, 0), ("x1", "10:35:00", "10:35:00", "C", 2, 0, 0)])
        add_files(p, {"agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                                    "T,Test\x1b[0m Transit,http://x,America/Toronto\n"})
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 10:00",
                "--feed", "t=" + p, "--min-saving", "0", "--radius-km", "5", "--origin-walk-m", "300"] + BASE
        txt = run(args, as_json=False)
        res = run(args)
        self.assertIn("Hub D", txt)
        for bad in ("\x1b", "\x07", "\u202e"):
            self.assertNotIn(bad, txt)
            self.assertNotIn(bad, json.dumps(res, ensure_ascii=False))
        self.assertIn("\n", txt)                                     # line structure kept


# ---------------------------------------------------------------------------------------------------- items 11, 12, 13
class FeedDownloads(unittest.TestCase):
    def fetch(self, response, **patches):
        url = "https://feeds.example/%s.zip" % self.id().rsplit(".", 1)[-1]   # no cached copy to fall back on
        with mock.patch.object(th.urllib.request, "urlopen", return_value=response), redirect_stderr(io.StringIO()):
            if patches:
                with mock.patch.multiple(th, **patches):
                    return th.fetch_url("x", url, 72, True, [])
            return th.fetch_url("x", url, 72, True, [])

    def test_non_zip_reply_stops_after_the_first_chunk(self):
        resp = FakeResponse(b"<html>" + b"x" * (5 << 20))
        with self.assertRaises(ValueError) as ctx:
            self.fetch(resp)
        self.assertIn("not a zip file", str(ctx.exception))
        self.assertEqual(resp.reads, 1)

    def test_size_cap(self):
        with self.assertRaises(ValueError) as ctx:
            self.fetch(FakeResponse(b"PK" + b"\0" * 3000), FEED_MAX_BYTES=1000)
        self.assertIn("feed limit", str(ctx.exception))
        resp = FakeResponse(b"PK", headers={"Content-Length": str(5000)})
        with self.assertRaises(ValueError):
            self.fetch(resp, FEED_MAX_BYTES=1000)
        self.assertEqual(resp.reads, 0)                              # refused before reading anything

    def test_zip_bomb_member_is_refused(self):
        p = feed(STOPS, [("X", "WK", "x1", "C")],
                 [("x1", "10:20:00", "10:20:00", "D", 1, 0, 0), ("x1", "10:35:00", "10:35:00", "C", 2, 0, 0)])
        th.GtfsZip(p)                                                # a normal feed is fine
        with zipfile.ZipFile(p, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr("shapes.txt", "0" * 400000)                   # ~400 KB from ~1 KB
        with mock.patch.object(th, "MEMBER_RATIO_FROM", 100000):
            with self.assertRaises(ValueError) as ctx:
                th.feed_check(p, DAY, [(43.5, -79.8, 1000)])
        self.assertIn("shapes.txt would unpack", str(ctx.exception))
        with mock.patch.object(th, "MEMBER_MAX_BYTES", 1000):
            with self.assertRaises(ValueError):
                th.GtfsZip(p)

    def test_a_good_download_is_cached(self):
        p = feed(STOPS, [("X", "WK", "x1", "C")],
                 [("x1", "10:20:00", "10:20:00", "D", 1, 0, 0), ("x1", "10:35:00", "10:35:00", "C", 2, 0, 0)])
        with open(p, "rb") as fh:
            body = fh.read()
        path = self.fetch(FakeResponse(body, headers={"Content-Length": str(len(body))}))
        self.assertTrue(path.startswith(th.cache_dir()))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), body)

    def test_niagara_http_fallback_says_it_is_unchecked(self):
        nia = next(f for f in ONTARIO["feeds"] if f["id"] == "niagara")
        self.assertEqual(nia["urls"][1], "http://68.71.24.110/gtfs/GTFSExport.zip")   # kept: a known trade-off

        def fetch(fid, url, *a, **k):
            if "mobilitydatabase" in url:
                raise OSError("mirror 503")
            return "/fake/copy.zip"
        ns = th.argparse.Namespace(max_age_hours=24, refresh=False)
        w = []
        with mock.patch.object(th, "fetch_url", side_effect=fetch), \
                mock.patch.object(th, "feed_check", return_value=(True, (None, None), True)):
            th.choose_feed_file("niagara", nia["name"], nia["urls"], DAY, [], ns, w)
        self.assertIn("unencrypted http", w[0])
        self.assertIn("confirm the times with the operator", w[0])
        w = []
        with mock.patch.object(th, "fetch_url", side_effect=fetch), \
                mock.patch.object(th, "feed_check", return_value=(True, (None, None), True)):
            th.choose_feed_file("n", "N", ["https://files.mobilitydatabase.org/x.zip", "https://b.example/x.zip"],
                                DAY, [], ns, w)
        self.assertNotIn("unencrypted", w[0])

    def test_json_feed_path_is_the_file_name_only(self):
        p = feed(STOPS, [("1", "WK", "t1", "C")],
                 [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:10:00", "10:10:00", "C", 2, 0, 0)])
        res = run(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 09:50",
                   "--feed", "t=" + p] + BASE)
        self.assertEqual(res["feeds"][0]["path"], "f.zip")


# ---------------------------------------------------------------------------------------------------- item 17
class ClusterCells(unittest.TestCase):
    def pair(self, lat, lon, metres_east):
        net = th.Network()
        a = net.add_stop("a", lat, lon, "f", "")
        b = net.add_stop("b", lat, lon + metres_east / (111320.0 * th.math.cos(th.math.radians(lat))), "f", "")
        return len(th.cluster_stops(net, [(0.0, a), (1.0, b)]))

    def test_stops_190_m_apart_east_west_are_one_hub(self):
        for k in range(200):   # many grid offsets: the old cell split up to 31% of these pairs
            lat, lon = 43.55 + k * 0.00123, -79.65 + k * 0.00071
            self.assertEqual(self.pair(lat, lon, 190.0), 1, (lat, lon))
            self.assertEqual(self.pair(lat, lon, 215.0), 2, (lat, lon))
        self.assertEqual(self.pair(-33.87, 151.21, 190.0), 1)       # southern and eastern hemispheres too


# ---------------------------------------------------------------------------------------------------- items 18-25
class SmallFixes(unittest.TestCase):
    def test_short_ride_to_the_first_stop_never_arrives_after_the_deadline(self):
        stops = [("A", "Hub A", 43.5000, -79.8000), ("C", "Dest C", 43.5500, -79.8000)]
        p = feed(stops, [("1", "WK", "a1", "C"), ("1", "WK", "a2", "C")],
                 [("a1", "08:20:00", "08:20:00", "A", 1, 0, 0), ("a1", "08:40:00", "08:40:00", "C", 2, 0, 0),
                  ("a2", "08:50:00", "08:50:00", "A", 1, 0, 0), ("a2", "09:10:00", "09:10:00", "C", 2, 0, 0)])
        with mock.patch.object(th, "utc_now", return_value=dt.datetime(2026, 10, 15, 12, 8)):   # 08:08 EDT
            res = run(["--origin", "43.4955,-79.8000", "--dest", "43.5500,-79.8000", "--arrive-by",
                       "2026-10-15 09:00", "--feed", "t=" + p] + BASE)
        self.assertNotIn("short_ride", res["transit_only"])          # board 08:50, arrive 09:10: too late

    def test_book_later_is_worded_not_negative(self):
        stops = [("H", "Hub H", 43.4050, -79.8000), ("D", "Dest D", 43.7600, -79.8000)]
        p = feed(stops, [("X", "WK", "x1", "D")],
                 [("x1", "08:30:00", "08:30:00", "H", 1, 0, 0), ("x1", "08:50:00", "08:50:00", "D", 2, 0, 0)])
        txt = run(["--origin", "43.4000,-79.8000", "--dest", "43.7600,-79.8000", "--arrive-by", "2026-10-15 09:00",
                   "--feed", "t=" + p, "--origin-walk-m", "300"] + BASE, as_json=False)
        line = next(ln for ln in txt.splitlines() if ln.strip().startswith("vs ride all the way"))
        self.assertRegex(line, r"book \d+ min later")
        self.assertNotRegex(txt, r"-\d+ min")

    def test_ride_instead_is_not_repeated_for_a_listed_hub(self):
        p = feed(STOPS, [("1", "WK", "t1", "B"), ("2", "WK", "t2", "C"), ("3", "WK", "t3", "C")],
                 [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:10:00", "10:10:00", "B", 2, 0, 0),
                  ("t2", "10:15:00", "10:15:00", "B2", 1, 0, 0), ("t2", "10:40:00", "10:40:00", "C", 2, 0, 0),
                  ("t3", "10:05:00", "10:05:00", "D", 1, 0, 0), ("t3", "10:25:00", "10:25:00", "C", 2, 0, 0)])
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 09:50",
                "--feed", "t=" + p, "--min-saving", "0", "--origin-walk-m", "300", "--radius-km", "5",
                "--traffic-factor", "1.2"] + BASE   # 09:50 is a weekday shoulder (x1.3): keep the x1.2 scenario
        res = run(args)
        self.assertEqual(res["transit_only"]["ride_instead"]["hub"], "Hub D")
        self.assertIn("Hub D", [o["hub"] for o in res["pareto"]])
        txt = run(args, as_json=False)
        self.assertNotIn("km to Hub D instead", txt)
        self.assertIn(" 1. Hub D", txt)

    def test_bad_time_text_gives_a_format_hint(self):
        for value in ("garbage", "2026-10-15", "25:00"):
            with self.assertRaises(SystemExit) as ctx:
                th.main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", value] + BASE)
            self.assertIn("use 'YYYY-MM-DD HH:MM', 'HH:MM' or 'now'", str(ctx.exception))
        with self.assertRaises(SystemExit) as ctx:
            th.main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--arrive-by", "tomorrow"] + BASE)
        self.assertIn("--arrive-by 'tomorrow'", str(ctx.exception))

    def test_origin_equal_to_destination_stops_early(self):
        with mock.patch.object(th, "load_regions", side_effect=AssertionError("planned anyway")) as lr:
            lr.side_effect = None
            lr.return_value = []
            with self.assertRaises(SystemExit) as ctx:
                th.main(["--origin", "43.5000,-79.8000", "--dest", "43.50005,-79.8000", "--depart",
                         "2026-10-15 09:50"] + BASE)
        self.assertIn("the origin and destination are the same place", str(ctx.exception))

    def test_stops_near_the_origin_without_night_service(self):
        p = feed(STOPS, [("1", "ALL", "d1", "C"), ("N", "ALL", "n1", "C")],
                 [("d1", "07:00:00", "07:00:00", "A", 1, 0, 0), ("d1", "07:30:00", "07:30:00", "C", 2, 0, 0),
                  ("n1", "01:00:00", "01:00:00", "B", 1, 0, 0), ("n1", "01:20:00", "01:20:00", "C", 2, 0, 0)],
                 calendar=ALL_DAYS)
        res = run(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-18 00:30",
                   "--feed", "t=" + p] + BASE)
        self.assertTrue(any(w.startswith("no departures between 00:30 and 04:30 from the 2 stops within 1200 m of "
                                         "the origin") for w in res["warnings"]), res["warnings"])
        self.assertFalse(any("may be absent" in w for w in res["warnings"]))

    def test_no_ride_service_means_no_ride_link_and_the_area_note_is_quoted(self):
        area = next(a for a in ONTARIO["ride_areas"] if a["name"] == "Tobermory")
        reg = {"id": "tob", "name": "tob", "timezone": "America/Toronto", "utc_std_offset_hours": -5,
               "dst_rule": "north_america", "bbox": {"min_lat": 44.0, "max_lat": 46.0, "min_lon": -82.5,
                                                     "max_lon": -80.5},
               "feeds": [], "ride_areas": [area], "taxi_pricing_default": ONTARIO["taxi_pricing_default"]}
        stops = [("O", "Origin stop", 44.4010, -81.6625), ("T", "Tobermory north", 45.2834, -81.6625)]
        p = feed(stops, [("K", "WK", "k1", "T")],
                 [("k1", "09:00:00", "09:00:00", "O", 1, 0, 0), ("k1", "11:00:00", "11:00:00", "T", 2, 0, 0)])
        args = ["--origin", "44.4000,-81.6625", "--dest", "45.25638,-81.66248", "--depart", "2026-10-15 08:30",
                "--region", "tob", "--feed", "t=" + p, "--routing", "estimate", "--no-discover"]
        with mock.patch.object(th, "load_regions", return_value=[reg]), \
                mock.patch.object(th, "check_network", side_effect=ok_network):
            txt = run(args, as_json=False)
            res = run(args)
        self.assertTrue(res["finish_with_ride"])
        self.assertIsNone(res["finish_with_ride"][0]["final_ride"]["uber"])
        self.assertNotIn("final ride quote", txt)
        self.assertNotIn("m.uber.com", txt)
        warn = next(w for w in res["warnings"] if w.startswith("there is no ride-hail near Tobermory"))
        self.assertIn("area note: No Uber, no local taxi", warn)
        self.assertIn("Bayshore Taxi (Owen Sound)", warn)
        self.assertIn("availability not confirmed", warn)
        self.assertNotIn("pre-book a local taxi", warn)

    def test_window_across_a_clock_change_is_flagged(self):
        p = feed(STOPS, [("1", "ALL", "t1", "C")],
                 [("t1", "01:30:00", "01:30:00", "A", 1, 0, 0), ("t1", "01:50:00", "01:50:00", "C", 2, 0, 0)],
                 calendar=ALL_DAYS)
        reg = {"id": "zone", "timezone": "America/Toronto", "utc_std_offset_hours": -5, "dst_rule": "north_america",
               "bbox": {"min_lat": 43.0, "max_lat": 44.0, "min_lon": -80.5, "max_lon": -79.0}, "feeds": []}
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--feed", "t=" + p, "--region", "zone",
                "--routing", "estimate"]
        with mock.patch.object(th, "load_regions", return_value=[reg]), \
                mock.patch.object(th, "check_network", side_effect=ok_network):
            fall = run(args + ["--depart", "2026-11-01 00:30"])
            plain = run(args + ["--depart", "2026-11-08 00:30"])
        self.assertTrue(any("clocks change between 00:30 and 04:30 (UTC-4 to UTC-5)" in w for w in fall["warnings"]))
        self.assertFalse(any("clocks change" in w for w in plain["warnings"]))


# ---------------------------------------------------------------------------------------------------- item 27
class AcceptanceMessages(unittest.TestCase):
    def test_missing_transit_only_fails_with_a_message(self):
        import test_acceptance
        hubs = [{"hub": n, "arrive": hms(22, 0), "ride_cost": 10.0, "legs": [{"agency": "GO Transit"}]}
                for n in ("Trafalgar Rd. @ Hwy. 407 Park & Ride", "Bronte Rd. @ Hwy. 407 Park & Ride")]
        fake = {"region": "gtha", "hubs_checked": 400, "all_options": hubs, "transit_only": None,
                "ride_all": {"cost": 40.0}, "feeds": [], "warnings": ["a test warning"]}
        case = test_acceptance.MiltonToMississauga("test_407_park_and_rides_are_checked_and_competitive")
        with mock.patch.object(case, "run_plan", return_value=fake):
            with self.assertRaises(AssertionError) as ctx:
                case.test_407_park_and_rides_are_checked_and_competitive()
        self.assertIn("no transit-only baseline", str(ctx.exception))
        self.assertIn("a test warning", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
