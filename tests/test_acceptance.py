"""Live acceptance test: the trip that started this project.

Milton (805 Santa Maria Blvd) to 3975 Grand Park Dr, Mississauga, Thursday 21:00. Google Maps never
suggested the Hwy 407 park-and-ride lots; the skill must check them. Needs network access, so it
only runs with TRIP_PLANNER_LIVE=1:

    TRIP_PLANNER_LIVE=1 python -m unittest tests.test_acceptance -v
"""
import datetime as dt
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import trip_hubs as th  # noqa: E402


def next_thursday(today=None):
    today = today or dt.date.today()
    return today + dt.timedelta(days=(3 - today.weekday()) % 7 or 7)


@unittest.skipUnless(os.environ.get("TRIP_PLANNER_LIVE") == "1", "set TRIP_PLANNER_LIVE=1 to run (needs network)")
class MiltonToMississauga(unittest.TestCase):
    def run_plan(self, *extra):
        args = ["--origin", "43.498757,-79.855987", "--dest", "43.5812457,-79.6474698",
                "--depart", "%s 21:00" % next_thursday().isoformat(), "--json", "--all", "--min-saving", "0"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(args + list(extra))
        return json.loads(buf.getvalue())

    def test_407_park_and_rides_are_checked_and_competitive(self):
        res = self.run_plan()
        why = "live data changed? warnings: %s" % res.get("warnings")
        self.assertEqual(res["region"], "gtha")
        # thresholds below are from the 2026-10 timetables: a wide margin, but a timetable change can still move them
        self.assertGreater(res["hubs_checked"], 300, why)
        hubs = {o["hub"]: o for o in res["all_options"] or []}
        for name in ("Trafalgar Rd. @ Hwy. 407 Park & Ride", "Bronte Rd. @ Hwy. 407 Park & Ride"):
            self.assertIn(name, hubs, why)
            self.assertEqual(hubs[name]["legs"][0]["agency"], "GO Transit")
        # a 407 lot must beat transit-only by a wide margin and cost well under the full ride
        self.assertIsNotNone(res["transit_only"], "no transit-only baseline on %s 21:00, so the 407 lots cannot be "
                                                  "compared with it; %s" % (next_thursday(), why))
        best_407 = min(hubs[n]["arrive"] for n in hubs if "Hwy. 407 Park & Ride" in n)
        self.assertLess(best_407 + 30 * 60, res["transit_only"]["arrive"], why)
        self.assertLess(min(hubs[n]["ride_cost"] for n in hubs if "Hwy. 407" in n), 0.6 * res["ride_all"]["cost"],
                        why)
        feeds = {f["name"] for f in res["feeds"]}
        self.assertTrue({"GO Transit", "MiWay", "Milton Transit", "Oakville Transit"} <= feeds)


if __name__ == "__main__":
    unittest.main()
