"""Regression tests for defects found in the 2026-10-09 review (each one was reproduced first).

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
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import STOPS, hms, make_feed, pin_clock, scan_for, th  # noqa: E402

_CLOCK = pin_clock()   # plan() runs see a fixed "now" before their hard-coded trip dates (see test_engine)


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


def add_files(path, files):
    with zipfile.ZipFile(path) as z:
        existing = {n: z.read(n).decode() for n in z.namelist()}
    existing.update(files)
    with zipfile.ZipFile(path, "w") as z:
        for n, data in existing.items():
            z.writestr(n, data)


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def feed(self, name, trips, stop_times, extra=None, stops=STOPS):
        p = os.path.join(self.tmp, name)
        make_feed(p, stops, trips, stop_times)
        if extra:
            add_files(p, extra)
        return p

    def test_frequencies_expand_headway_trips(self):
        # exact_times=1: timed runs every 10 min (exact_times 0 is 'every N min' service, test_review_gtfs.HeadwayTests)
        p = self.feed("freq.zip", [("F", "WK", "f1", "C")],
                      [("f1", "06:00:00", "06:00:00", "A", 1, 0, 0), ("f1", "06:20:00", "06:20:00", "C", 2, 0, 0)],
                      {"frequencies.txt": "trip_id,start_time,end_time,headway_secs,exact_times\nf1,06:00:00,22:00:00,600,1\n"})
        net, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        self.assertGreaterEqual(len(net.conns), 24)
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(9, 5))[0], hms(9, 30))   # 09:10 run arrives 09:30

    def test_footpath_transfer_respects_min_change(self):
        stops = STOPS + [("B3", "Transfer B next door", 43.52009, -79.80001)]   # ~10 m from B
        p = self.feed("foot.zip", [("1", "WK", "t1", "B"), ("2", "WK", "t2", "C")],
                      [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:10:00", "08:10:00", "B", 2, 0, 0),
                       ("t2", "08:11:20", "08:11:20", "B3", 1, 0, 0), ("t2", "08:30:00", "08:30:00", "C", 2, 0, 0)],
                      stops=stops)
        net = th.Network()
        th.load_feed(net, "t", "test", p, th.dt.date(2026, 10, 15), hms(7, 0), hms(11, 0))
        used = sorted({c[2] for c in net.conns} | {c[3] for c in net.conns})
        idx = {n: i for i, n in enumerate(net.stop_name)}
        egress = {idx["Dest C"]: 0}
        for change, expect_ok in ((60, True), (120, False)):   # 80 s gap: fine at 1 min, impossible at 2
            foot = th.build_footpaths(net, used, 400, change)
            scan = th.ProfileScan(net, egress, foot, change, th.same_stop_change(net, change))
            arr, _ = scan.evaluate(idx["Hub A"], hms(7, 55))
            self.assertEqual(arr < th.INF, expect_ok, "min change %d s" % change)

    def test_transfers_txt_forbids_and_delays(self):
        p = self.feed("xfer.zip", [("1", "WK", "t1", "B"), ("2", "WK", "t2", "C")],
                      [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:10:00", "08:10:00", "B", 2, 0, 0),
                       ("t2", "08:15:00", "08:15:00", "B", 1, 0, 0), ("t2", "08:30:00", "08:30:00", "C", 2, 0, 0)],
                      {"transfers.txt": "from_stop_id,to_stop_id,transfer_type,min_transfer_time\nB,B,2,600\n"})
        net, scan, idx = scan_for(p, hms(7, 0), hms(11, 0))
        self.assertGreaterEqual(scan.evaluate(idx["Hub A"], hms(7, 55))[0], th.INF)   # 5 min < required 10
        p2 = self.feed("xfer3.zip", [("1", "WK", "t1", "B"), ("2", "WK", "t2", "C")],
                       [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:10:00", "08:10:00", "B", 2, 0, 0),
                        ("t2", "08:15:00", "08:15:00", "B2", 1, 0, 0), ("t2", "08:30:00", "08:30:00", "C", 2, 0, 0)],
                       {"transfers.txt": "from_stop_id,to_stop_id,transfer_type,min_transfer_time\nB,B2,3,\n"})
        net, scan, idx = scan_for(p2, hms(7, 0), hms(11, 0))
        self.assertGreaterEqual(scan.evaluate(idx["Hub A"], hms(7, 55))[0], th.INF)   # walking B->B2 forbidden

    def test_interpolation_survives_window_prefilter(self):
        p = self.feed("interp.zip", [("1", "WK", "T", "C")],
                      [("T", "08:00:00", "08:00:00", "A", 1, 0, 0), ("T", "", "", "D", 2, 0, 0),
                       ("T", "", "", "B", 3, 0, 0), ("T", "", "", "B2", 4, 0, 0),
                       ("T", "10:00:00", "10:00:00", "C", 5, 0, 0)])
        net, scan, idx = scan_for(p, hms(9, 15), hms(12, 15))
        # B2 is interpolated to 09:30, inside the window, even though A (08:00) is before t0 - 1 h
        self.assertEqual(scan.evaluate(idx["Transfer B across street"], hms(9, 20))[0], hms(10, 0))

    def test_same_trip_id_in_two_feeds_is_not_the_same_vehicle(self):
        a = self.feed("a.zip", [("R", "WK", "T1", "C")],
                      [("T1", "08:30:00", "08:30:00", "A", 1, 0, 0), ("T1", "09:20:00", "09:20:00", "C", 2, 0, 0)])
        b = self.feed("b.zip", [("R", "WK", "T1", "C")],
                      [("T1", "08:40:00", "08:40:00", "D", 1, 0, 0), ("T1", "09:30:00", "09:30:00", "E", 2, 0, 0)])
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 08:00",
                "--region", "none", "--no-discover", "--feed", "aaa=" + a, "--feed", "bbb=" + b,
                "--routing", "estimate", "--json", "--min-saving", "0", "--min-gain-min", "0",
                "--pickup-wait-min", "2", "--safety-min", "0", "--radius-km", "5", "--origin-walk-m", "50"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(args)
        res = json.loads(buf.getvalue())
        keys = [o["legs"][0]["trip_key"] for o in res["pareto"] + res["others"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_rides_that_barely_beat_transit_only_are_hidden(self):
        # transit only from A reaches C at 08:30; riding to D gets the same arrival
        p = self.feed("compete.zip", [("1", "WK", "t1", "C")],
                      [("t1", "08:10:00", "08:10:00", "A", 1, 0, 0), ("t1", "08:15:00", "08:15:00", "D", 2, 0, 0),
                       ("t1", "08:30:00", "08:30:00", "C", 3, 0, 0)])
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 08:00",
                "--region", "none", "--no-discover", "--feed", "t=" + p, "--routing", "estimate", "--json",
                "--min-saving", "0", "--radius-km", "5", "--origin-walk-m", "300"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(args)
        res = json.loads(buf.getvalue())
        self.assertEqual(res["pareto"], [])
        self.assertTrue(res["transit_competitive"])

    def test_cache_name_depends_on_url(self):
        saved = os.environ.get("TRIP_PLANNER_CACHE")   # an exported cache path must survive this test
        os.environ["TRIP_PLANNER_CACHE"] = os.path.join(self.tmp, "cache")
        try:
            a = self.feed("ga.zip", [("1", "WK", "t1", "C")],
                          [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:30:00", "08:30:00", "C", 2, 0, 0)])
            url_a = "file:///" + a.replace(os.sep, "/").lstrip("/")
            p1 = th.fetch_url("same", url_a, 72, False)
            p2 = th.fetch_url("same", url_a + "?v=2", 72, False)
            self.assertNotEqual(p1, p2)
        finally:
            if saved is None:
                os.environ.pop("TRIP_PLANNER_CACHE", None)
            else:
                os.environ["TRIP_PLANNER_CACHE"] = saved


class SecondReviewRegressions(unittest.TestCase):
    """Defects found in the second review (v0.3) and the Airdrie blind test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def feed(self, name, trips, stop_times, extra=None, stops=STOPS):
        p = os.path.join(self.tmp, name)
        make_feed(p, stops, trips, stop_times)
        if extra:
            add_files(p, extra)
        return p

    def run_main(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(args)
        return json.loads(buf.getvalue())

    def test_arrive_by_never_offers_bookings_in_the_past(self):
        p = self.feed("ab.zip", [("X", "WK", "x0", "C"), ("X", "WK", "x1", "C")],
                      [("x0", "20:40:00", "20:40:00", "D", 1, 0, 0), ("x0", "21:00:00", "21:00:00", "C", 2, 0, 0),
                       ("x1", "21:10:00", "21:10:00", "D", 1, 0, 0), ("x1", "21:30:00", "21:30:00", "C", 2, 0, 0)])
        orig = th.local_now
        th.local_now = lambda tz, reg: (th.dt.datetime(2026, 10, 15, 21, 0), "test")
        try:
            res = self.run_main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000",
                                 "--arrive-by", "2026-10-15 21:40", "--region", "none", "--no-discover",
                                 "--feed", "t=" + p, "--routing", "estimate", "--json", "--min-saving", "0",
                                 "--radius-km", "5", "--origin-walk-m", "50"])
        finally:
            th.local_now = orig
        now = hms(21, 0)
        for o in res["pareto"] + res["others"]:
            self.assertGreaterEqual(o["book_by"], now - 60)
            if o["backup"]:
                self.assertGreaterEqual(o["backup"]["book_by"], now - 60)
        self.assertTrue(any("booked before now" in w for w in res["warnings"]))

    def test_transit_only_prefers_the_near_stop_on_equal_arrival(self):
        stops = [("FAR", "Far upstream", 43.4891, -79.8), ("NEAR", "Near stop", 43.4999, -79.8),
                 ("C", "Dest C", 43.55, -79.8)]
        p = self.feed("tie.zip", [("1", "WK", "t1", "C")],
                      [("t1", "08:25:00", "08:25:00", "FAR", 1, 0, 0), ("t1", "08:28:00", "08:28:00", "NEAR", 2, 0, 0),
                       ("t1", "08:50:00", "08:50:00", "C", 3, 0, 0)], stops=stops)
        res = self.run_main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000",
                             "--depart", "2026-10-15 08:00", "--region", "none", "--no-discover", "--feed", "t=" + p,
                             "--routing", "estimate", "--json"])
        self.assertEqual(res["transit_only"]["stop"], "Near stop")
        self.assertNotIn("short_ride", res["transit_only"])

    def test_route_scoped_transfer_rules_are_not_applied_stop_wide(self):
        p = self.feed("scoped.zip", [("1", "WK", "t1", "B"), ("2", "WK", "t2", "C")],
                      [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:10:00", "08:10:00", "B", 2, 0, 0),
                       ("t2", "08:20:00", "08:20:00", "B", 1, 0, 0), ("t2", "08:30:00", "08:30:00", "C", 2, 0, 0)],
                      {"transfers.txt": "from_stop_id,to_stop_id,from_route_id,to_route_id,transfer_type,"
                                        "min_transfer_time\nB,B,9,9,3,\n"})
        net, scan, idx = scan_for(p, hms(7, 0), hms(11, 0))
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(7, 55))[0], hms(8, 30))

    def test_equal_arrival_prefers_fewer_vehicles(self):
        stops = [("A", "Start A", 43.50, -79.80), ("PC", "Platform C", 43.5200, -79.8000),
                 ("PE", "Platform E", 43.52058, -79.8000), ("X", "Detour X", 43.5240, -79.8000),
                 ("Z", "Dest Z", 43.55, -79.80)]
        p = self.feed("detour.zip", [("1", "WK", "t1", "C"), ("2", "WK", "t2", "Z"), ("3", "WK", "t3", "X")],
                      [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:10:00", "08:10:00", "PC", 2, 0, 0),
                       ("t2", "08:30:00", "08:30:00", "PE", 1, 0, 0), ("t2", "08:50:00", "08:50:00", "Z", 2, 0, 0),
                       ("t3", "08:15:00", "08:15:00", "PC", 1, 0, 0), ("t3", "08:18:00", "08:18:00", "X", 2, 0, 0)],
                      stops=stops)
        net, scan, idx = scan_for(p, hms(7, 0), hms(11, 0), dest=(43.55, -79.80))
        arr, legs = scan.journey(idx["Start A"], hms(7, 55))
        self.assertEqual(arr, hms(8, 50))
        self.assertEqual([lg["route"] for lg in legs if lg["type"] == "ride"], ["1", "2"])

    def test_cost_filter_does_not_trigger_transit_only_note(self):
        stops = [("A", "Near origin", 43.4995, -79.8), ("H", "Near dest", 43.5450, -79.8), ("C", "Dest", 43.55, -79.8)]
        p = self.feed("note.zip", [("L", "WK", "l1", "C"), ("X", "WK", "x1", "C")],
                      [("l1", "08:10:00", "08:10:00", "A", 1, 0, 0), ("l1", "10:30:00", "10:30:00", "C", 2, 0, 0),
                       ("x1", "08:30:00", "08:30:00", "H", 1, 0, 0), ("x1", "08:35:00", "08:35:00", "C", 2, 0, 0)],
                      stops=stops)
        res = self.run_main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000",
                             "--depart", "2026-10-15 08:00", "--region", "none", "--no-discover", "--feed", "t=" + p,
                             "--routing", "estimate", "--json", "--radius-km", "6"])
        self.assertFalse(res["transit_competitive"])

    def test_stop_headsign_shows_when_trip_headsign_is_empty(self):
        p = self.feed("sh.zip", [("R", "WK", "t1", "")],
                      [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:30:00", "08:30:00", "C", 2, 0, 0)])
        with zipfile.ZipFile(p) as z:
            st = z.read("stop_times.txt").decode()
        rows = st.strip().split("\n")
        rows[0] += ",stop_headsign"
        rows[1:] = [r + ",Airport" for r in rows[1:]]
        add_files(p, {"stop_times.txt": "\n".join(rows) + "\n"})
        net, scan, idx = scan_for(p, hms(7, 0), hms(11, 0))
        _, legs = scan.journey(idx["Hub A"], hms(7, 55))
        self.assertEqual(legs[0]["headsign"], "Airport")

    def test_discovery_dedupes_overlapping_listings_and_matches_towns(self):
        def row(fid, provider, bbox, name=""):
            r = {"id": fid, "data_type": "gtfs", "provider": provider, "name": name, "status": "active",
                 "urls.latest": "https://files.example/%s/latest.zip" % fid, "urls.direct_download": "",
                 "urls.authentication_type": "0", "is_official": "True", "location.municipality": ""}
            if bbox:
                for k, v in zip(("minimum_latitude", "maximum_latitude", "minimum_longitude", "maximum_longitude"),
                                bbox):
                    r["location.bounding_box." + k] = str(v)
            return r
        rows = [row("mdb-782", "Verkehrsverbund Berlin-Brandenburg (VBB)", (50.84, 54.31, 10.79, 17.04)),
                row("mdb-3233", "Verkehrsverbund Berlin-Brandenburg (VBB)", (50.84, 54.31, 10.55, 17.04),
                    name="Powered by VBB"),
                row("tld-4819", "Airdrie Transit", None),
                row("tld-9999", "Somewhere Else Transit", None)]
        box = th.box_around(52.52, 13.40, 20000)
        found, _, _, dups = th.discover_feeds(box, rows, places={"airdrie"})
        ids = [f["id"] for f in found]
        self.assertIn("mdb-782", ids)
        self.assertNotIn("mdb-3233", ids)
        self.assertEqual([d["id"] for d in dups], ["mdb-3233"])
        self.assertIn("tld-4819", ids)
        self.assertNotIn("tld-9999", ids)

    def test_geocode_sanity_and_currency(self):
        w = []
        th.check_geocode("Main Street North & 1 Avenue NE, Airdrie", "8 Street NW, Airdrie", "origin", w)
        self.assertTrue(w and "intersection" in w[0])
        w = []
        th.check_geocode("Main Street North, Airdrie", "8 Street NW, Airdrie, Alberta", "origin", w)
        self.assertTrue(w and "main" in w[0])
        w = []
        th.check_geocode("805 Santa Maria Blvd, Milton, ON", "805 Santa Maria Boulevard, Milton, Ontario", "origin", w)
        self.assertEqual(w, [])
        self.assertEqual(th.local_currency("America/Edmonton"), "CAD")
        self.assertEqual(th.local_currency("America/Chicago"), "USD")
        self.assertEqual(th.local_currency("Europe/Berlin"), "EUR")
        self.assertIsNone(th.local_currency("America/Bogota"))


class ThirdReviewRegressions(unittest.TestCase):
    """Defects found in the third review (v0.4) and the Chicago blind test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(th, k, v)

    def patch(self, name, value):
        self._saved.setdefault(name, getattr(th, name))
        setattr(th, name, value)

    def feed(self, name, trips, stop_times, stops=STOPS):
        p = os.path.join(self.tmp, name)
        make_feed(p, stops, trips, stop_times)
        return p

    def run_main(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(args)
        return json.loads(buf.getvalue())

    def test_arrive_by_ignores_a_transit_only_plan_that_has_left(self):
        stops = [("A", "Near origin", 43.4995, -79.8), ("D", "Hub D", 43.5050, -79.8), ("C", "Dest", 43.55, -79.8)]
        p = self.feed("gone.zip", [("L", "WK", "l1", "C"), ("X", "WK", "x1", "C")],
                      [("l1", "21:02:00", "21:02:00", "A", 1, 0, 0), ("l1", "21:30:00", "21:30:00", "C", 2, 0, 0),
                       ("x1", "21:20:00", "21:20:00", "D", 1, 0, 0), ("x1", "21:35:00", "21:35:00", "C", 2, 0, 0)],
                      stops=stops)
        self.patch("local_now", lambda tz, reg: (th.dt.datetime(2026, 10, 15, 21, 0), "test"))
        res = self.run_main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000",
                             "--arrive-by", "2026-10-15 21:40", "--region", "none", "--no-discover",
                             "--feed", "t=" + p, "--routing", "estimate", "--json", "--min-saving", "0",
                             "--radius-km", "5", "--origin-walk-m", "100"])
        self.assertIsNone(res["transit_only"])
        self.assertFalse(res["transit_competitive"])
        self.assertIn("Hub D", [o["hub"] for o in res["pareto"] + res["others"]])

    def test_depart_slightly_in_the_past_starts_now_and_keeps_hubs(self):
        stops = [("D", "Hub D", 43.5050, -79.8), ("C", "Dest", 43.55, -79.8)]
        p = self.feed("late.zip", [("X", "WK", "x1", "C"), ("X", "WK", "x2", "C")],
                      [("x1", "21:10:00", "21:10:00", "D", 1, 0, 0), ("x1", "21:20:00", "21:20:00", "C", 2, 0, 0),
                       ("x2", "21:25:00", "21:25:00", "D", 1, 0, 0), ("x2", "21:35:00", "21:35:00", "C", 2, 0, 0)],
                      stops=stops)
        self.patch("local_now", lambda tz, reg: (th.dt.datetime(2026, 10, 15, 21, 0), "test"))
        res = self.run_main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000",
                             "--depart", "2026-10-15 20:55", "--region", "none", "--no-discover",
                             "--feed", "t=" + p, "--routing", "estimate", "--json", "--min-saving", "0",
                             "--radius-km", "5", "--origin-walk-m", "100"])
        self.assertTrue(res["depart"].endswith("21:00"))
        opts = res["pareto"] + res["others"]
        self.assertTrue(opts)
        self.assertTrue(all(o["book_by"] >= hms(21, 0) - 60 for o in opts))

    def test_transit_only_backup_and_bookable_short_ride(self):
        stops = [("A", "Station", 43.5040, -79.8), ("C", "Dest", 43.55, -79.8)]
        p = self.feed("bk.zip", [("R", "WK", "r1", "C"), ("R", "WK", "r2", "C")],
                      [("r1", "08:20:00", "08:20:00", "A", 1, 0, 0), ("r1", "08:48:00", "08:48:00", "C", 2, 0, 0),
                       ("r2", "08:42:00", "08:42:00", "A", 1, 0, 0), ("r2", "09:10:00", "09:10:00", "C", 2, 0, 0)],
                      stops=stops)
        self.patch("local_now", lambda tz, reg: (th.dt.datetime(2026, 10, 15, 8, 0), "test"))
        res = self.run_main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000",
                             "--depart", "2026-10-15 08:00", "--region", "none", "--no-discover",
                             "--feed", "t=" + p, "--routing", "estimate", "--json"])
        to = res["transit_only"]
        self.assertEqual(to["backup"]["board"], hms(8, 42))
        sr = to.get("short_ride")
        self.assertTrue(sr is None or sr["book_by"] >= hms(8, 0) - 60)
        self.assertEqual(th.legs_text([{"type": "ride", "agency": "M", "route": "BNSF", "headsign": "", "from": "A",
                                         "to": "B", "depart": 0, "arrive": 60, "stops": 1}]).count("nonstop"), 1)

    def test_dedupe_same_data_under_other_names_but_not_other_operators(self):
        def row(fid, provider, name, bbox):
            r = {"id": fid, "data_type": "gtfs", "provider": provider, "name": name, "status": "active",
                 "urls.latest": "https://files.example/%s/latest.zip" % fid, "urls.direct_download": "",
                 "urls.authentication_type": "0", "is_official": "True", "location.municipality": ""}
            for k, v in zip(("minimum_latitude", "maximum_latitude", "minimum_longitude", "maximum_longitude"), bbox):
                r["location.bounding_box." + k] = str(v)
            return r
        nyc = (40.5, 40.9, -74.1, -73.7)
        rows = [row("mdb-516", "Metropolitan Transit Authority (MTA)", "NYC Subway", nyc),
                row("mdb-511", "Metropolitan Transit Authority (MTA)", "NYC Subway Supplemented", nyc),
                row("mdb-1", "Metropolitan Transit Authority (MTA)", "Manhattan Bus", nyc),
                row("jbda-a", "株式会社高知駅前観光", "空港バス", (33.5, 33.6, 133.5, 133.7)),
                row("jbda-b", "とさでん交通株式会社", "空港バス", (33.5, 33.6, 133.5, 133.7))]
        found, _, _, dups = th.discover_feeds(th.box_around(40.75, -73.98, 10000), rows)
        ids = [f["id"] for f in found]
        self.assertIn("mdb-511", ids)            # the supplemented copy wins
        self.assertNotIn("mdb-516", ids)
        self.assertIn("mdb-1", ids)               # other MTA feeds stay
        found_jp, _, _, _ = th.discover_feeds(th.box_around(33.55, 133.6, 10000), rows)
        self.assertEqual(sorted(f["id"] for f in found_jp), ["jbda-a", "jbda-b"])
        self.assertEqual(th._provider_key("Public Transport Victoria (PTV)"), th._provider_key("Public Transport Victoria"))

    def test_national_feed_promotion_prefers_same_country(self):
        # Amsterdam-like trip: no local feed; a foreign intercity feed has the smaller bbox than the national feed
        stops_nl = [("NA", "Centraal", 52.3790, 4.9000), ("NB", "Airport", 52.3090, 4.7620)]
        nl = os.path.join(self.tmp, "nl.zip")
        make_feed(nl, stops_nl, [("T", "WK", "t1", "Airport")],
                  [("t1", "09:10:00", "09:10:00", "NA", 1, 0, 0), ("t1", "09:25:00", "09:25:00", "NB", 2, 0, 0)])
        stops_x = [("XA", "Paris", 48.88, 2.355), ("XB", "Somewhere", 50.0, 3.0)]
        xb = os.path.join(self.tmp, "x.zip")
        make_feed(xb, stops_x, [("T", "WK", "x1", "Paris")],
                  [("x1", "09:10:00", "09:10:00", "XA", 1, 0, 0), ("x1", "11:10:00", "11:10:00", "XB", 2, 0, 0)])

        def row(fid, provider, country, bbox, path):
            r = {"id": fid, "data_type": "gtfs", "provider": provider, "name": "", "status": "active",
                 "urls.latest": path, "urls.direct_download": "", "urls.authentication_type": "0",
                 "is_official": "True", "location.municipality": "", "location.country_code": country,
                 "location.subdivision_name": ""}
            for k, v in zip(("minimum_latitude", "maximum_latitude", "minimum_longitude", "maximum_longitude"), bbox):
                r["location.bounding_box." + k] = str(v)
            return r
        # catalog rows carry web URLs only (v0.9.8 drops file:// and local paths from the catalog): map them to the
        # local test zips instead of downloading
        local = {"https://files.example/tdg-1.zip": xb, "https://files.example/mdb-1077.zip": nl}
        rows = [row("tdg-1", "Cross-border Rail", "FR", (48.0, 53.0, 0.0, 10.0), "https://files.example/tdg-1.zip"),
                row("mdb-1077", "OVapi", "NL", (50.0, 54.0, -2.0, 25.0), "https://files.example/mdb-1077.zip")]
        self.patch("load_catalog", lambda max_age_h=168.0, refresh=False: rows)
        self.patch("fetch_url", lambda fid, url, *a, **k: local[url])
        self.patch("check_network", lambda hosts, timeout=6: {h: "ok" for h in hosts})
        self.patch("reverse_place_info", lambda lat, lon, w: {"names": set(), "country": "NL", "state": None})
        res = self.run_main(["--origin", "52.3780,4.9000", "--dest", "52.3100,4.7630", "--depart", "2026-10-15 09:00",
                             "--routing", "estimate", "--json", "--region", "auto"])
        self.assertEqual([f["id"] for f in res["feeds"]], ["mdb-1077"])
        self.assertIsNotNone(res["transit_only"])

    def test_failed_url_is_not_downloaded_twice(self):
        calls = []
        self.patch("fetch_url", lambda *a, **k: calls.append(a[1]) or (_ for _ in ()).throw(ValueError("boom")))
        bad = {}
        ns = type("A", (), {"max_age_hours": 72, "refresh": False})()
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                th.choose_feed_file("f", "F", ["https://x/f.zip"], th.dt.date(2026, 10, 15), [], ns, [], bad)
        self.assertEqual(calls, ["https://x/f.zip"])


class ClaudeAiSandboxRegressions(unittest.TestCase):
    """From the claude.ai test: blocked network, slow cold downloads."""

    def test_stop_map_skips_far_feeds_without_downloading(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "f.zip")
        make_feed(p, STOPS, [("1", "WK", "t1", "C")],
                  [("t1", "08:00:00", "08:00:00", "A", 1, 0, 0), ("t1", "08:30:00", "08:30:00", "C", 2, 0, 0)])
        cells = th.stop_cells(p, 0.02)
        cov = {"cell_deg": 0.02, "cells": ";".join("%d,%d" % c for c in sorted(cells))}
        self.assertTrue(th.coverage_near(cov, [(43.50, -79.80, 1000)]))           # on top of a stop
        self.assertTrue(th.coverage_near(cov, [(43.50, -79.75, 5000)]))           # 4 km away, 5 km reach
        self.assertFalse(th.coverage_near(cov, [(43.50, -79.60, 5000)]))          # 16 km away
        self.assertFalse(th.coverage_near(cov, [(45.0, -75.0, 40000)]))

    def test_blocked_network_is_not_reported_as_a_bad_address(self):
        import socket
        dns = th.urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        refused = th.urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        answered = th.urllib.error.HTTPError("https://x/", 404, "Not Found", {}, None)
        reset = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
        self.assertTrue(th._unreachable(dns))
        self.assertTrue(th._unreachable(refused))
        self.assertFalse(th._unreachable(answered))
        self.assertFalse(th._unreachable(reset))
        th.close_quietly(answered)   # Python 3.8: closing an HTTPError made without a body raises
        saved = th.http_get
        th.http_get = lambda *a, **k: (_ for _ in ()).throw(dns)
        try:
            with self.assertRaises(SystemExit) as ctx:
                th._geocode("805 Santa Maria Blvd, Milton", "origin", [])
            self.assertIn("NETWORK BLOCKED", str(ctx.exception))
        finally:
            th.http_get = saved
        msg = th.network_blocked_message(["a"], ["feeds.example.org", "api.transitous.org"], "note")
        self.assertIn("feeds.example.org", msg)
        self.assertIn("not an address problem", msg)

    def test_allowlist_names_only_what_the_trip_needs(self):
        regions = th.load_regions()
        ns = th.argparse.Namespace(origin="43.4988,-79.8560", dest="43.5812,-79.6475", region="auto",
                                   routing="auto", crosscheck=False, feed=None, no_discover=False,
                                   radius_km=None, dest_walk_m=1500)
        hosts, note = th.trip_domains(ns, regions)
        self.assertIn("assets.metrolinx.com", hosts)        # GO
        self.assertIn("www.miapp.ca", hosts)                # MiWay
        self.assertIn("metrolinx.tmix.se", hosts)           # Milton
        for far in ("ckan0.cf.opendata.inter.prod-toronto.ca", "www.yrt.ca", "maps.durham.ca",
                    "drive.google.com", "api.transitous.org", "nominatim.openstreetmap.org"):
            self.assertNotIn(far, hosts)
        ns.origin = "805 Santa Maria Blvd, Milton, ON"
        hosts, note = th.trip_domains(ns, regions)
        self.assertIn("nominatim.openstreetmap.org", hosts)
        self.assertIn("lat,lon", note)

    def test_house_number_matched_only_to_the_street_stops_the_plan(self):
        street = [{"lat": "43.51", "lon": "-79.82", "addresstype": "road", "display_name": "Santa Maria Boulevard, Milton"}]
        replies = iter([json.dumps(street).encode(), json.dumps({"features": []}).encode()] * 2)
        saved = th.http_get
        th.http_get = lambda *a, **k: next(replies)
        try:
            with self.assertRaises(SystemExit) as ctx:
                th._geocode("805 Santa Maria Blvd, Milton, ON", "origin", [], strict=True)
            self.assertIn("ADDRESS NOT PRECISE", str(ctx.exception))
            w = []
            lat, lon, _ = th._geocode("805 Santa Maria Blvd, Milton, ON", "origin", w, strict=False)
            self.assertEqual((lat, lon), (43.51, -79.82))
            self.assertTrue(w and "street level" in w[0])
        finally:
            th.http_get = saved

    def test_shared_platforms_are_optional_when_a_mirror_exists(self):
        regions = th.load_regions()
        ns = th.argparse.Namespace(origin="43.4988,-79.8560", dest="43.5812,-79.6475", region="auto",
                                   routing="auto", crosscheck=False, feed=None, no_discover=False,
                                   radius_km=None, dest_walk_m=1500)
        hosts, note = th.trip_domains(ns, regions)
        self.assertNotIn("www.arcgis.com", hosts)           # Oakville and Brampton publish only via ArcGIS
        self.assertIn("files.mobilitydatabase.org", hosts)  # their mirror is listed instead
        self.assertIn("Optional", note)
        self.assertIn("www.arcgis.com", note)


class FareRegressions(unittest.TestCase):
    """From the user's 2026-10-10 report: UberX far cheaper than estimated, transit fares inflated."""

    @classmethod
    def setUpClass(cls):
        cls.region = next(r for r in th.load_regions() if r["id"] == "gtha")
        cls.rules = cls.region["transfer_rules"]

    def group(self, agency, depart, fare, first="A", last="B", routes=("1",)):
        return {"agency": agency, "feed": agency.lower(), "depart": th._hm(depart) * 60 if ":" in str(depart) else depart,
                "first_stop": first, "last_stop": last, "routes": set(routes), "fare": fare}

    def charged(self, groups, payment="card", day=th.dt.date(2026, 10, 13)):
        th.apply_transfers(groups, self.rules, payment, day)
        return [g["charged"] for g in groups]

    def test_go_to_local_is_free_with_a_card_within_3_hours(self):
        self.assertEqual(self.charged([self.group("GO Transit", "13:15", 5.61), self.group("MiWay", "13:37", 3.5)]),
                         [5.61, 0.0])
        self.assertEqual(self.charged([self.group("GO Transit", "13:15", 5.61), self.group("MiWay", "16:30", 3.5)]),
                         [5.61, 3.5])
        self.assertEqual(self.charged([self.group("GO Transit", "13:15", 6.65), self.group("MiWay", "13:37", 4.5)],
                                      payment="cash"), [6.65, 4.5])

    def test_local_fare_is_credited_when_tapping_go_within_2_hours(self):
        self.assertEqual(self.charged([self.group("MiWay", "08:00", 3.5), self.group("GO Transit", "08:40", 5.0),
                                       self.group("TTC", "09:30", 3.3)]), [0.0, 5.0, 0.0])

    def test_milton_is_free_with_go_at_its_go_stops_at_any_hour(self):
        # milton.ca: the co-fare is limited by place (Milton GO, Drew Centre, the 401 lot), not by hour.
        def trip(depart, day, at="Milton GO Station - Platform 5", go_at="Milton GO", payment="card"):
            return self.charged([self.group("Milton Transit", depart, 4.25, last=at),
                                 self.group("GO Transit", th._hm(depart) * 60 + 900, 7.63, first=go_at)],
                                payment=payment, day=day)
        self.assertEqual(trip("08:15", th.dt.date(2026, 10, 13)), [0.0, 7.63])     # Tuesday peak
        self.assertEqual(trip("11:35", th.dt.date(2026, 10, 13)), [0.0, 7.63])     # Tuesday midday
        self.assertEqual(trip("13:00", th.dt.date(2026, 10, 17), payment="cash"), [0.0, 7.63])  # Saturday, GO ticket
        self.assertEqual(trip("08:15", th.dt.date(2026, 10, 13), at="Trudeau at Derry", go_at="Derry Rd. @ Trudeau Dr."),
                         [4.25, 7.63])                                              # not a Milton GO connection point
        groups = [self.group("GO Transit", "20:00", 7.63, last="Milton GO"),
                  self.group("Milton Transit", "20:20", 4.25, first="Milton GO Station - Platform 4")]
        self.assertEqual(self.charged(groups), [7.63, 0.0])                         # off the last train

    def test_a_free_leg_does_not_restart_the_transfer_window(self):
        chain = [self.group("MiWay", "08:00", 3.5), self.group("TTC", "08:30", 3.3), self.group("MiWay", "10:15", 3.5)]
        self.assertEqual(self.charged(chain), [3.5, 0.0, 3.5])                     # 2 h from the 08:00 tap
        chain = [self.group("GO Transit", "08:00", 9.0), self.group("TTC", "10:30", 3.3),
                 self.group("York Region Transit", "11:30", 4.24)]
        self.assertEqual(self.charged(chain), [9.0, 0.0, 4.24])                    # GO's 3 h ended at 11:00
        chain = [self.group("MiWay", "08:00", 3.5), self.group("TTC", "08:30", 3.3), self.group("MiWay", "09:45", 3.5)]
        self.assertEqual(self.charged(chain), [3.5, 0.0, 0.0])                     # still inside 2 h

    def test_contactless_and_cash_limits(self):
        # A bank-card tap gets One Fare like PRESTO.
        self.assertEqual(self.charged([self.group("GO Transit", "13:15", 5.61), self.group("MiWay", "13:37", 4.5)],
                                      payment="contactless"), [5.61, 0.0])
        # TTC cash gets no 2-hour transfer.
        self.assertEqual(self.charged([self.group("TTC", "08:00", 3.35), self.group("GO Transit", "08:20", 4.4),
                                       self.group("TTC", "08:40", 3.35)], payment="cash"), [3.35, 4.4, 3.35])
        # A paper transfer comes only from the previous agency.
        self.assertEqual(self.charged([self.group("Hamilton Street Railway", "08:00", 3.75),
                                       self.group("Burlington Transit", "08:20", 3.75),
                                       self.group("Oakville Transit", "08:40", 4.0)], payment="cash"), [3.75, 0.0, 4.0])
        # Brampton takes a GO paper ticket only from a GO train station.
        self.assertEqual(self.charged([self.group("GO Transit", "08:00", 6.0, last="Bramalea Rd. @ Balmoral Dr."),
                                       self.group("Brampton Transit", "08:20", 4.75, first="Bramalea Rd at Goodwood Rd")],
                                      payment="cash"), [6.0, 4.75])
        self.assertEqual(self.charged([self.group("GO Transit", "08:00", 6.0, last="Bramalea GO"),
                                       self.group("Brampton Transit", "08:20", 4.75, first="Bramalea GO Station")],
                                      payment="cash"), [6.0, 0.0])

    def test_each_free_leg_names_the_rule_that_made_it_free(self):
        g = [self.group("MiWay", "07:00", 3.5), self.group("GO Transit", "07:20", 7.0), self.group("TTC", "08:30", 3.3)]
        self.charged(g)
        self.assertIn("credited back when you tap GO", g[0]["note"])
        self.assertIn("One Fare", g[2]["note"])   # free from GO (3 h): the credited MiWay fare gives no window
        self.assertIn("free from GO Transit", g[2]["note"])
        g = [self.group("TTC", "07:00", 3.3), self.group("MiWay", "07:30", 3.5)]
        self.charged(g)
        self.assertEqual(g[1]["note"], "free from TTC (One Fare, within 2 h)")
        # Cash: the note names the agency whose paper transfer or ticket the rider holds.
        g = [self.group("GO Transit", "07:00", 7.0), self.group("Oakville Transit", "08:00", 4.0),
             self.group("Burlington Transit", "08:30", 3.75)]
        self.assertEqual(self.charged(g, payment="cash"), [7.0, 0.0, 0.0])
        self.assertIn("free from GO Transit", g[2]["note"])
        # YRT gives cash riders a 2-hour transfer too.
        g = [self.group("York Region Transit", "07:00", 4.65), self.group("Brampton Transit", "07:20", 4.75),
             self.group("York Region Transit", "08:10", 4.65)]
        self.assertEqual(self.charged(g, payment="cash"), [4.65, 0.0, 0.0])

    def test_contactless_prices_use_the_agency_bank_card_rate(self):
        class Net:
            fares, fare_urls, stop_zone = {}, {}, [""]
        flat = self.region["flat_fares"]
        for agency, card, tap in (("MiWay", 3.5, 4.5), ("Brampton Transit", 3.55, 4.75), ("TTC", 3.3, 3.3),
                                  ("York Region Transit", 4.24, 4.65)):
            leg = [{"type": "ride", "feed": agency.lower(), "agency": agency, "route": "1", "route_id": "1",
                    "from": "A", "to": "B", "from_idx": 0, "to_idx": 0, "depart": 28800}]
            self.assertEqual(th.journey_fare(Net, leg, flat, "card")[0], card)
            self.assertEqual(th.journey_fare(Net, leg, flat, "contactless")[0], tap)

    def test_stop_specific_rule_needs_a_direct_change_at_that_stop(self):
        groups = [self.group("Milton Transit", "21:50", 4.25, last="Winston Churchill at Steeles", routes=("21",)),
                  self.group("Brampton Transit", "22:21", 3.55, first="Winston Churchill - Zum Steeles Station Stop EB",
                             last="Steeles Ave W at Windmill Blvd", routes=("11",)),
                  self.group("MiWay", "22:41", 3.5, first="Steeles Ave East Of Tait Blvd", routes=("61",))]
        # v0.9.8: Milton buses have no card readers, so the Brampton leg is boarded on Milton's paper transfer, not
        # a card tap; MiWay does not take a Milton transfer off Brampton, so it is a new fare (was 0.0 before)
        self.assertEqual(self.charged(groups), [4.25, 0.0, 3.5])
        self.assertIn("Milton route 21", groups[1]["note"])
        self.assertIsNone(groups[2]["note"])

    def test_same_agency_transfer_lasts_2_hours(self):
        self.assertEqual(self.charged([self.group("MiWay", "08:00", 3.5), self.group("Brampton Transit", "08:30", 3.55),
                                       self.group("MiWay", "09:50", 3.5)]), [3.5, 0.0, 0.0])
        self.assertEqual(self.charged([self.group("MiWay", "08:00", 3.5), self.group("Brampton Transit", "08:30", 3.55),
                                       self.group("MiWay", "10:10", 3.5)]), [3.5, 0.0, 3.5])
        self.assertEqual(self.charged([self.group("MiWay", "08:00", 3.5), self.group("MiWay", "10:30", 3.5)]),
                         [3.5, 3.5])

    def test_fare_text_drops_the_caveat_when_rules_were_applied(self):
        o = {"transit_fare": 5.61, "fare_currency": "CAD", "fare_complete": True, "ride_cost": 20.0,
             "fare_parts": [{"agency": "GO Transit", "feed": "go", "fare": 5.61, "currency": "CAD"},
                            {"agency": "MiWay", "feed": "miway", "fare": 0.0, "full_fare": 3.5,
                             "note": "free from GO Transit (One Fare)"}]}
        txt = th.fare_text(o, "CAD", True, rules_on=True)
        self.assertIn("MiWay free from GO Transit", txt)
        self.assertNotIn("before transfer discounts", txt)
        self.assertIn("before transfer discounts", th.fare_text(o, "CAD", True, rules_on=False))

    def test_uberx_estimate_matches_ubers_published_averages(self):
        p = self.region["ride_pricing"]
        self.assertEqual(p["uplift"], 1.0)
        self.assertAlmostEqual(th.ride_cost(p, 12.0, 14 * 60), 19.8, delta=1.0)   # Milton to Trafalgar 407
        self.assertAlmostEqual(th.ride_cost(p, 24.0, 25 * 60), 33.8, delta=1.0)   # Milton to Square One
        self.assertLess(th.ride_cost(p, 25.0, 26 * 60), 38.0)                      # Uber's all-hours average


if __name__ == "__main__":
    unittest.main()
