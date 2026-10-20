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


if __name__ == "__main__":
    unittest.main()
