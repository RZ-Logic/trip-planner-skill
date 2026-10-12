"""Driving planner when the network is blocked (claude.ai sandboxes): a NETWORK BLOCKED warning with the hosts
to allow, and parking from the curated files only.

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
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "plugins", "trip-planner", "skills", "trip-planner", "scripts")
sys.path.insert(0, SCRIPTS)

import drive_plan as dp  # noqa: E402
import drive_route as dr  # noqa: E402
import parking as pk  # noqa: E402

GRAND_BEND = "43.3125,-81.7569"
HOME = "43.498757,-79.855987"


def blocked_status(hosts, timeout=6):
    return {h: "blocked: refused" for h in hosts}


class NetworkBlocked(unittest.TestCase):
    def run_plan(self, *extra, status=blocked_status):
        args = dp.build_parser().parse_args(["--origin", HOME, "--dest", GRAND_BEND, "--depart", "2026-10-12 09:00",
                                             "--stay-hours", "5"] + list(extra))
        calls = []
        real = pk.find_parking

        def spy(*a, **k):
            calls.append(a[4])
            return real(*a, **k)

        def no_network(*a, **k):
            raise OSError("network is unreachable")

        with mock.patch.object(dr, "get_routes", return_value=[]), \
                mock.patch.object(dp.th, "check_network", side_effect=status), \
                mock.patch.object(pk, "find_parking", side_effect=spy), \
                mock.patch.object(dp.dc, "fetch", side_effect=no_network):
            res = dp.plan(args)
        return res, calls

    def test_blocked_warning_lists_hosts_and_curated_parking_still_shows(self):
        res, calls = self.run_plan()
        self.assertTrue(res["network_blocked"])
        self.assertTrue(res["warnings"][0].startswith("NETWORK BLOCKED"))
        for host in ("valhalla1.openstreetmap.de", "overpass-api.de", "www.ontario.ca", "maps.ottawa.ca"):
            self.assertIn(host, res["warnings"][0])
        self.assertTrue(calls and calls[0]["offline"])
        names = [o["name"] for o in res["parking"]["options"]]
        self.assertTrue(any("Gill Rd" in n or "Municipal Dr" in n for n in names), names)
        text = dp.render(res)
        self.assertIn("ROUTES     none found", text)
        self.assertIn("arrival estimated without a route", text)
        self.assertLess(text.index("PARKING"), text.index("WARNINGS"))
        self.assertNotIn("—", text)
        self.assertNotIn("–", text)

    def test_reachable_hosts_mean_no_blocked_note(self):
        res, calls = self.run_plan(status=lambda hosts, timeout=6: {h: "ok" for h in hosts})
        self.assertFalse(res["network_blocked"])
        self.assertFalse(any(w.startswith("NETWORK BLOCKED") for w in res["warnings"]))
        self.assertFalse(calls[0]["offline"])

    def test_offline_find_parking_skips_network_sources(self):
        with mock.patch.object(pk, "city_lots", side_effect=AssertionError("network source called")), \
                mock.patch.object(pk, "toronto_lots", side_effect=AssertionError("network source called")), \
                mock.patch.object(pk, "osm_lots", side_effect=AssertionError("network source called")):
            import datetime as dt
            res = pk.find_parking(43.3125, -81.7569, dt.datetime(2026, 7, 18, 11, 0), 240,
                                  {"offline": True, "holidays": set()}, [])
        self.assertTrue(res["options"])
        self.assertTrue(all("curated" in o["source"] or o["confidence"] == "curated" for o in res["options"]))

    def test_check_network_needs_no_trip(self):
        with mock.patch.object(dp.th, "check_network", side_effect=lambda hosts, timeout=6: {h: "ok" for h in hosts}):
            with mock.patch("sys.stdout"):
                self.assertEqual(dp.main(["--check-network"]), 0)
        with mock.patch.object(dp.th, "check_network", side_effect=blocked_status):
            with mock.patch("sys.stdout"):
                self.assertEqual(dp.main(["--check-network"]), 1)
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                dp.main(["--origin", HOME])


if __name__ == "__main__":
    unittest.main()
