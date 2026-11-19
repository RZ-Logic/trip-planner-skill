"""Regression tests for three defects an outside review (Codex) found in fixes shipped in 1.0.0: the footpath
grid's cell size, a stop-pair transfers.txt rule under a station rule, and a trim word typed beside "AWD".

Offline: synthetic feeds (local zip paths, nothing fetched) and the bundled car index, no download.
Run: PYTHONPATH=tools/ci TRIP_PLANNER_NO_NETWORK=1 python -m unittest tests.test_codex_review -v
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
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import STOPS, hms, make_feed, scan_for, th  # noqa: E402  (test_engine adds the scripts folder)
from test_regressions import add_files  # noqa: E402
import vehicles as V  # noqa: E402

R_M = 6371008.8   # haversine_m's sphere


class FootpathGridEdgeTests(unittest.TestCase):
    """The grid cells were radius / 111 320 degrees, 0.1% under the radius on haversine_m's sphere (111 195 m per
    degree), so two stops just under the radius apart could sit two cells apart and get no footpath."""

    RADIUS = 400

    def build(self, lat_a, lon_a, lat_b, lon_b):
        net = th.Network()
        far = [net.add_stop("s%d" % j, 47.5 + 0.1 * j, -80.0 + 0.3 * j, "f", "") for j in range(5)]
        a = net.add_stop("bay", lat_a, lon_a, "f", "")
        b = net.add_stop("rail", lat_b, lon_b, "f", "")
        used = far + [a, b]
        foot = th.build_footpaths(net, used, self.RADIUS, 120)
        grid = th.Grid(net.stop_lat, net.stop_lon, used, self.RADIUS, max(abs(net.stop_lat[i]) for i in used))
        return b in dict(foot.get(a, ())), grid

    def test_pair_under_the_radius_across_a_cell_edge_gets_a_footpath(self):
        lat, lon = 54.3, -100.0
        for dist in (399.6, 399.8, 399.95):
            # north-south: the bay just south of a cell edge, the platform `dist` metres north of it
            _found, g = self.build(lat, lon, lat + math.degrees(dist / R_M), lon)
            la = (math.floor(lat / g.dlat) + 1) * g.dlat - 1e-9
            lb = la + math.degrees(dist / R_M)
            with self.subTest(way="north-south", dist=dist):
                self.assertLessEqual(th.haversine_m(la, lon, lb, lon), self.RADIUS)
                self.assertTrue(self.build(la, lon, lb, lon)[0])
            # east-west: the bay just west of a cell edge (both stops at `lat`, so the same grid is built again)
            dlon = math.degrees(2 * math.asin(math.sin(dist / (2 * R_M)) / math.cos(math.radians(lat))))
            _found, g = self.build(lat, lon, lat, lon + dlon)
            lo = (math.floor(lon / g.dlon) + 1) * g.dlon - 1e-9
            with self.subTest(way="east-west", dist=dist):
                self.assertLessEqual(th.haversine_m(lat, lo, lat, lo + dlon), self.RADIUS)
                self.assertTrue(self.build(lat, lo, lat, lo + dlon)[0])

    def test_pair_past_the_radius_still_gets_none(self):
        lat, lon = 54.3, -100.0
        self.assertFalse(self.build(lat, lon, lat + math.degrees(401.0 / R_M), lon)[0])


class StopPairBeatsStationRuleTests(unittest.TestCase):
    """transfers.txt: a rule naming two stops wins over a rule on their station. A plain (type 0 or blank) or timed
    (type 1) stop-pair rule recorded nothing, so the station's minimum time or ban still applied to that pair."""

    STOPS_TXT = ("stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
                 "P,Big Station,43.52005,-79.80005,1,\n"
                 "A,Hub A,43.5000,-79.8000,0,\n"
                 "B,Transfer B,43.5200,-79.8000,0,P\n"
                 "B2,Transfer B across street,43.5201,-79.8001,0,P\n"
                 "C,Dest C,43.5500,-79.8000,0,\n")
    TRIPS = [("1", "WK", "t1", "B"), ("2", "WK", "t2", "C"), ("2", "WK", "t3", "C")]
    TIMES = [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:10:00", "08:10:00", "B", 2, 0, 0),
             ("t2", "08:13:00", "08:13:00", "B2", 1, 0, 0), ("t2", "08:25:00", "08:25:00", "C", 2, 0, 0),
             ("t3", "08:43:00", "08:43:00", "B2", 1, 0, 0), ("t3", "08:55:00", "08:55:00", "C", 2, 0, 0)]
    HEAD = "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"

    def arrive(self, rules):
        """Arrival at C leaving Hub A at 07:55 (INF when the change at the station is not possible)."""
        p = os.path.join(tempfile.mkdtemp(dir=_TMP), "f.zip")
        make_feed(p, STOPS, self.TRIPS, self.TIMES)
        extra = {"stops.txt": self.STOPS_TXT}
        if rules:
            extra["transfers.txt"] = self.HEAD + rules
        add_files(p, extra)
        _net, scan, idx = scan_for(p, hms(7, 0), hms(11, 0))
        return scan.evaluate(idx["Hub A"], hms(7, 55))[0]

    def test_station_rules_alone_still_apply(self):
        self.assertEqual(self.arrive(""), hms(8, 25))
        self.assertEqual(self.arrive("P,P,2,480\n"), hms(8, 55))   # 8 minutes to change: the 08:13 is missed
        self.assertGreaterEqual(self.arrive("P,P,3,\n"), th.INF)

    def test_plain_or_timed_stop_pair_lifts_the_station_rule(self):
        for kind in ("0", "1", ""):
            with self.subTest(transfer_type=kind):
                self.assertEqual(self.arrive("P,P,2,480\nB,B2,%s,\n" % kind), hms(8, 25))
                self.assertEqual(self.arrive("P,P,3,\nB,B2,%s,\n" % kind), hms(8, 25))
                self.assertEqual(self.arrive("B,B2,%s,\nP,P,2,480\n" % kind), hms(8, 25))   # either row order

    def test_stop_pair_minimum_or_ban_still_wins(self):
        self.assertEqual(self.arrive("P,P,3,\nB,B2,2,60\n"), hms(8, 25))
        self.assertEqual(self.arrive("B,B2,0,\nB,B2,2,480\n"), hms(8, 55))   # the stricter row of the same pair
        self.assertGreaterEqual(self.arrive("P,P,2,60\nB,B2,3,\n"), th.INF)


class TrimWordWithAwdTests(unittest.TestCase):
    """'AWD' kept only the rows named with a drive word before the other words were read, so a trim typed beside
    it that only a dual-motor trim's row carries ('Mach-E Performance AWD' -> 'GT Performance Edition') was
    dropped and the ordinary AWD versions were rated instead. Real bundled index, no download."""

    def setUp(self):
        self.nodl = mock.patch.object(V, "_download_year", return_value=False)
        self.nodl.start()

    def tearDown(self):
        self.nodl.stop()

    def look(self, text):
        w = []
        return V.lookup(text, w), w

    def test_trim_word_beside_awd_picks_its_own_row(self):
        for year in (2021, 2022, 2023):
            got, w = self.look("%d Ford Mustang Mach-E Performance AWD" % year)
            with self.subTest(year=year):
                self.assertEqual(got["variant_names"], ["Mustang Mach-E GT Performance Edition"])
                self.assertEqual((got["city"], got["hwy"]), (23.8, 27.8))
                self.assertFalse(any("ignored" in x for x in w), w)

    def test_bare_awd_and_an_unlisted_trim_are_unchanged(self):
        got, w = self.look("2023 Ford Mustang Mach-E AWD")
        self.assertFalse(any("GT" in n for n in got["variant_names"]), got["variant_names"])
        got, w = self.look("2025 Ford Mustang Mach-E Performance AWD")   # no 2025 row carries the word
        self.assertEqual(got["variant_names"], ["Mustang Mach-E Extended Range AWD", "Mustang Mach-E Standard Range AWD"])
        self.assertTrue(any("ignored 'performance'" in x for x in w), w)
        got, w = self.look("2025 Tesla Model 3 AWD")
        self.assertEqual(got["variant_names"], ["Model 3 Long Range AWD (Import)"])


if __name__ == "__main__":
    unittest.main()
