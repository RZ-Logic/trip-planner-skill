"""Regression tests for the review findings on trip_hubs.py GTFS loading and the connection scan: stop_times order,
feeds on another clock, frequencies.txt, station-level transfers and the boarding window.

Offline: synthetic feeds (local zip paths, nothing fetched), every network call mocked, a fixed clock.
Run: PYTHONPATH=tools/ci TRIP_PLANNER_NO_NETWORK=1 python -m unittest tests.test_review_gtfs -v
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
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import STOPS, hms, make_feed, pin_clock, scan_for, th  # noqa: E402  (test_engine adds the scripts folder)
from test_regressions import add_files  # noqa: E402

DAY = dt.date(2026, 10, 15)   # a Thursday
ALL_DAYS = [("ALL", 1, 1, 1, 1, 1, 1, 1, "20260101", "20271231")]
BASE = ["--region", "none", "--no-discover", "--routing", "estimate"]
REG = {"timezone": "America/Toronto", "utc_std_offset_hours": -5, "dst_rule": "north_america"}
UTC_AGENCY = "agency_id,agency_name,agency_url,agency_timezone\nT,Coach,http://x,UTC\n"
_CLOCK = pin_clock()


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


def feed(trips, stop_times, extra=None, stops=STOPS, **kw):
    """make_feed into a fresh temp folder, then add or replace the files in `extra`."""
    p = os.path.join(tempfile.mkdtemp(dir=_TMP), "f.zip")
    make_feed(p, stops, trips, stop_times, **kw)
    if extra:
        add_files(p, extra)
    return p


def drop_files(path, names):
    """Rewrite the zip without `names` (make_feed always writes calendar.txt)."""
    with zipfile.ZipFile(path) as z:
        keep = {n: z.read(n) for n in z.namelist() if n not in names}
    with zipfile.ZipFile(path, "w") as z:
        for n, data in keep.items():
            z.writestr(n, data)


def ok_network(hosts, timeout=6):
    return {h: "ok" for h in hosts}


def run(args, as_json=True):
    out = io.StringIO()
    with mock.patch.object(th, "check_network", side_effect=ok_network), \
            redirect_stdout(out), redirect_stderr(io.StringIO()):
        th.main(args + (["--json"] if as_json else []))
    return json.loads(out.getvalue()) if as_json else out.getvalue()


class StopTimesOrderTests(unittest.TestCase):
    """hubs-engine-4: times that fall along a trip never become a connection that arrives before it leaves."""

    def test_midnight_wrap_is_read_as_after_midnight(self):
        p = feed([("N", "ALL", "n1", "C")],
                 [("n1", "23:50:00", "23:50:00", "A", 1, 0, 0), ("n1", "23:58:00", "23:58:00", "B", 2, 0, 0),
                  ("n1", "00:06:00", "00:06:00", "C", 3, 0, 0)], calendar=ALL_DAYS)
        net, scan, idx = scan_for(p, hms(23, 30), hms(27, 30))
        self.assertTrue(net.conns)
        self.assertTrue(all(c[1] >= c[0] for c in net.conns), net.conns)
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(23, 40))[0], hms(24, 6))
        self.assertEqual(len([w for w in net.warnings if "ran backwards past midnight" in w]), 1, net.warnings)

    def test_trip_with_times_running_backwards_is_skipped(self):
        p = feed([("1", "WK", "t1", "C"), ("2", "WK", "t2", "C")],
                 [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:30:00", "10:30:00", "C", 2, 0, 0),
                  ("t2", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t2", "09:50:00", "09:50:00", "D", 2, 0, 0),
                  ("t2", "10:20:00", "10:20:00", "C", 3, 0, 0)])
        net, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        self.assertFalse([c for c in net.conns if c[1] < c[0]])
        self.assertFalse([c for c in net.conns if net.trips[c[4]][6] == "t2"])
        self.assertTrue(any("skipped" in w and "backwards" in w for w in net.warnings), net.warnings)
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(9, 55))[0], hms(10, 30))


if __name__ == "__main__":
    unittest.main()
