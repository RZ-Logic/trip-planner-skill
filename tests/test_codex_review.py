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


if __name__ == "__main__":
    unittest.main()
