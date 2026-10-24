"""Unit tests for trip_hubs.py on small synthetic GTFS feeds with known answers.

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
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import trip_hubs as th  # noqa: E402

DAY = dt.date(2026, 10, 15)  # a Thursday
# "now" for every plan() run in these tests: Mon 2026-10-12 12:00 in Toronto, before every hard-coded trip date, so
# the results do not depend on the day or hour the suite runs (a run on the trip day itself used to fail)
FIXED_NOW_UTC = dt.datetime(2026, 10, 12, 16, 0)


def pin_clock():
    """A patcher that freezes trip_hubs' clock at FIXED_NOW_UTC (start() it in setUpModule, stop() in tearDownModule)."""
    return mock.patch.object(th, "utc_now", return_value=FIXED_NOW_UTC)


_CLOCK = pin_clock()


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


def make_feed(path, stops, trips, stop_times, calendar=None, calendar_dates=None):
    """stops: (id, name, lat, lon); trips: (route_id, service_id, trip_id, headsign);
    stop_times: (trip_id, arr, dep, stop_id, seq, pickup, dropoff)."""
    def csv_text(header, rows):
        return "\n".join([",".join(header)] + [",".join(str(x) for x in r) for r in rows]) + "\n"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("agency.txt", csv_text(["agency_id", "agency_name", "agency_url", "agency_timezone"],
                                          [("T", "Test Transit", "http://x", "America/Toronto")]))
        z.writestr("routes.txt", csv_text(["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
                                          sorted({(t[0], "T", t[0], "Route " + t[0], 3) for t in trips})))
        z.writestr("stops.txt", csv_text(["stop_id", "stop_name", "stop_lat", "stop_lon"], stops))
        z.writestr("trips.txt", csv_text(["route_id", "service_id", "trip_id", "trip_headsign"], trips))
        z.writestr("stop_times.txt", csv_text(["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
                                               "pickup_type", "drop_off_type"], stop_times))
        z.writestr("calendar.txt", csv_text(
            ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
             "start_date", "end_date"],
            calendar or [("WK", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231")]))
        if calendar_dates:
            z.writestr("calendar_dates.txt", csv_text(["service_id", "date", "exception_type"], calendar_dates))


# Stops along a line of longitude. ~111 m per 0.001 deg of latitude.
STOPS = [
    ("A", "Hub A", 43.5000, -79.8000),
    ("B", "Transfer B", 43.5200, -79.8000),
    ("B2", "Transfer B across street", 43.5201, -79.8001),
    ("C", "Dest C", 43.5500, -79.8000),
    ("D", "Hub D", 43.5050, -79.8000),
    ("E", "Far E", 43.6000, -79.8000),
]


def scan_for(path, t0, t_end, dest=(43.5500, -79.8000), dest_walk_m=500, day=DAY):
    net = th.Network()
    th.load_feed(net, "t", "test", path, day, t0, t_end)
    used = sorted({c[2] for c in net.conns} | {c[3] for c in net.conns})
    egress = {}
    for s in used:
        d = th.haversine_m(dest[0], dest[1], net.stop_lat[s], net.stop_lon[s])
        if d <= dest_walk_m:
            egress[s] = th.walk_s(d)
    foot = th.build_footpaths(net, used, 400, 120)
    scan = th.ProfileScan(net, egress, foot, 120, th.same_stop_change(net, 120))
    idx = {name: i for i, name in enumerate(net.stop_name)}
    return net, scan, idx


def hms(h, m, s=0):
    return h * 3600 + m * 60 + s


class ProfileScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def feed(self, trips, stop_times, **kw):
        p = os.path.join(self.tmp, "f%d.zip" % len(os.listdir(self.tmp)))
        make_feed(p, STOPS, trips, stop_times, **kw)
        return p

    def test_direct_and_transfer(self):
        p = self.feed(
            [("1", "WK", "t1", "to B"), ("2", "WK", "t2", "to C"), ("3", "WK", "t3", "to C slow")],
            [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:10:00", "10:10:00", "B", 2, 0, 0),
             ("t2", "10:15:00", "10:15:00", "B2", 1, 0, 0), ("t2", "10:25:00", "10:25:00", "C", 2, 0, 0),
             ("t3", "10:05:00", "10:05:00", "D", 1, 0, 0), ("t3", "10:40:00", "10:40:00", "C", 2, 0, 0)])
        net, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        arr, legs = scan.journey(idx["Hub A"], hms(9, 55))
        self.assertEqual(arr, hms(10, 25))                       # transfer at B -> B2 via footpath
        self.assertEqual([lg["route"] for lg in legs if lg["type"] == "ride"], ["1", "2"])
        self.assertTrue(any(lg["type"] == "walk" and lg["to"] == "Transfer B across street" for lg in legs))
        arr_d, _ = scan.evaluate(idx["Hub D"], hms(10, 0))
        self.assertEqual(arr_d, hms(10, 40))
        arr_late, _ = scan.evaluate(idx["Hub A"], hms(10, 1))      # missed t1
        self.assertGreaterEqual(arr_late, th.INF)

    def test_min_change_time_blocks_impossible_transfer(self):
        p = self.feed(
            [("1", "WK", "t1", "to B"), ("2", "WK", "t2", "to C")],
            [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:10:00", "10:10:00", "B", 2, 0, 0),
             ("t2", "10:11:00", "10:11:00", "B", 1, 0, 0), ("t2", "10:25:00", "10:25:00", "C", 2, 0, 0)])
        _, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        arr, _ = scan.evaluate(idx["Hub A"], hms(9, 55))
        self.assertGreaterEqual(arr, th.INF)                       # 1 min < 2 min change time

    def test_pickup_and_dropoff_restrictions(self):
        p = self.feed(
            [("1", "WK", "t1", "to C")],
            [("t1", "10:00:00", "10:00:00", "A", 1, 1, 0),          # no pickup at A
             ("t1", "10:10:00", "10:10:00", "D", 2, 0, 0),
             ("t1", "10:30:00", "10:30:00", "C", 3, 0, 0)])
        _, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        self.assertGreaterEqual(scan.evaluate(idx["Hub A"], hms(9, 55))[0], th.INF)
        self.assertEqual(scan.evaluate(idx["Hub D"], hms(10, 0))[0], hms(10, 30))
        p2 = self.feed(
            [("1", "WK", "t1", "to E")],
            [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0),
             ("t1", "10:30:00", "10:30:00", "C", 2, 0, 1),          # no drop-off at C
             ("t1", "10:50:00", "10:50:00", "E", 3, 0, 0)])
        _, scan2, idx2 = scan_for(p2, hms(9, 0), hms(13, 0))
        self.assertGreaterEqual(scan2.evaluate(idx2["Hub A"], hms(9, 55))[0], th.INF)

    def test_calendar_dates_removal_and_weekend(self):
        trips = [("1", "WK", "t1", "to C")]
        st = [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:30:00", "10:30:00", "C", 2, 0, 0)]
        p = self.feed(trips, st, calendar_dates=[("WK", "20261015", 2)])
        net, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        self.assertEqual(len(net.conns), 0)
        self.assertTrue(any("NO service" in w for w in net.warnings))
        p2 = self.feed(trips, st)
        net2, _, _ = scan_for(p2, hms(9, 0), hms(13, 0), day=dt.date(2026, 10, 17))  # Saturday
        self.assertEqual(len(net2.conns), 0)

    def test_after_midnight_trip_from_previous_service_day(self):
        p = self.feed(
            [("N", "WK", "n1", "night")],
            [("n1", "24:30:00", "24:30:00", "A", 1, 0, 0), ("n1", "25:00:00", "25:00:00", "C", 2, 0, 0)])
        # Friday 00:15 should see Thursday's 24:30 trip as 00:30
        _, scan, idx = scan_for(p, hms(0, 15), hms(4, 0), day=dt.date(2026, 10, 16))
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(0, 20))[0], hms(1, 0))
        # Thursday 23:50 should see the same trip at 24:30 (+1d)
        _, scan2, idx2 = scan_for(p, hms(23, 50), hms(27, 0), day=dt.date(2026, 10, 15))
        self.assertEqual(scan2.evaluate(idx2["Hub A"], hms(23, 55))[0], hms(25, 0))

    def test_missing_times_are_interpolated(self):
        p = self.feed(
            [("1", "WK", "t1", "to C")],
            [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "", "", "D", 2, 0, 0),
             ("t1", "10:20:00", "10:20:00", "C", 3, 0, 0)])
        _, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        # D is 556 m into the 5.56 km trip, so it is passed at 10:02 (by distance, not halfway by stop count)
        self.assertEqual(scan.evaluate(idx["Hub D"], hms(10, 1))[0], hms(10, 20))
        self.assertGreaterEqual(scan.evaluate(idx["Hub D"], hms(10, 3))[0], th.INF)

    def test_unsorted_stop_times(self):
        p = self.feed(
            [("1", "WK", "t1", "to C")],
            [("t1", "10:20:00", "10:20:00", "C", 3, 0, 0), ("t1", "10:00:00", "10:00:00", "A", 1, 0, 0),
             ("t1", "10:10:00", "10:10:00", "D", 2, 0, 0)])
        _, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(9, 0))[0], hms(10, 20))

    def test_profile_keeps_later_departures(self):
        p = self.feed(
            [("1", "WK", "early", "to C"), ("1", "WK", "late", "to C")],
            [("early", "10:00:00", "10:00:00", "A", 1, 0, 0), ("early", "10:30:00", "10:30:00", "C", 2, 0, 0),
             ("late", "11:00:00", "11:00:00", "A", 1, 0, 0), ("late", "11:30:00", "11:30:00", "C", 2, 0, 0)])
        _, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(9, 50))[0], hms(10, 30))
        self.assertEqual(scan.evaluate(idx["Hub A"], hms(10, 1))[0], hms(11, 30))


class PricingFareAndTimeTests(unittest.TestCase):
    def test_tiered_booking_fee_tax_and_uplift(self):
        p = {"base": 3.17, "per_km": 0.81, "per_min": 0.18, "min_fare": 5.25,
             "booking_fee_tiers": [[4, 1.60], [10, 1.92], [21, 2.06], [9999, 2.77]], "tax": 0.13, "uplift": 1.3}
        # Milton -> Mississauga: 21.7 km, 24 min. Live Lyft quote was CAD 40-45.
        self.assertAlmostEqual(th.ride_cost(p, 21.7, 24 * 60), 40.9, delta=0.2)
        short = th.ride_cost(p, 2.0, 5 * 60)
        self.assertAlmostEqual(short, (3.17 + 1.62 + 0.90 + 1.60) * 1.13 * 1.3, places=2)
        self.assertAlmostEqual(th.ride_cost({"base": 0, "per_km": 0, "per_min": 0, "min_fare": 8}, 1, 60), 8.0)

    def test_zone_fares_and_flat_fallback(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "f.zip")
        make_feed(p, STOPS, [("1", "WK", "t1", "C")],
                  [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:30:00", "10:30:00", "C", 2, 0, 0)])
        # add zones and a zone fare table to the synthetic feed
        with zipfile.ZipFile(p) as z:
            files = {n: z.read(n).decode() for n in z.namelist()}
        files["stops.txt"] = "stop_id,stop_name,stop_lat,stop_lon,zone_id\n" + "\n".join(
            "%s,%s,%s,%s,%s" % (s[0], s[1], s[2], s[3], "13" if s[0] == "A" else "20") for s in STOPS) + "\n"
        files["fare_attributes.txt"] = "fare_id,price,currency_type,payment_method,transfers\n13-20,6.65,CAD,1,\n"
        files["fare_rules.txt"] = "fare_id,origin_id,destination_id\n13-20,13,20\n"
        with zipfile.ZipFile(p, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        net, scan, idx = scan_for(p, hms(9, 0), hms(13, 0))
        _, legs = scan.journey(idx["Hub A"], hms(9, 50))
        total, cur, parts, complete = th.journey_fare(net, legs)
        self.assertEqual((total, cur, complete), (6.65, "CAD", True))
        # an agency with no fare table falls back to the region's flat fare
        net.fares.clear()
        total, _, parts, complete = th.journey_fare(net, legs, {"test": {"card": 3.5, "cash": 4.0, "currency": "CAD"}})
        self.assertEqual((total, complete, parts[0]["source"]), (3.5, True, "region table, card"))
        total, _, _, _ = th.journey_fare(net, legs, {"test": {"card": 3.5, "cash": 4.0}}, payment="cash")
        self.assertEqual(total, 4.0)
        total, _, _, complete = th.journey_fare(net, legs)
        self.assertEqual((total, complete), (None, False))

    def test_dst_rules_without_tz_database(self):
        reg = {"dst_rule": "north_america", "utc_std_offset_hours": -5}
        self.assertEqual(th.offset_at_local(dt.datetime(2026, 10, 15, 21, 0), None, reg)[0], dt.timedelta(hours=-4))
        self.assertEqual(th.offset_at_local(dt.datetime(2026, 12, 15, 21, 0), None, reg)[0], dt.timedelta(hours=-5))
        # 'now' near the switch is computed from the UTC instant: 06:30Z on 2026-11-01 is 01:30 EST
        off, _ = th.offset_at_utc(dt.datetime(2026, 11, 1, 6, 30), None, reg)
        self.assertEqual(off, dt.timedelta(hours=-5))
        off, _ = th.offset_at_utc(dt.datetime(2026, 3, 8, 6, 30), None, reg)   # 01:30 EST, before the jump
        self.assertEqual(off, dt.timedelta(hours=-5))
        off, _ = th.offset_at_utc(dt.datetime(2026, 3, 8, 7, 30), None, reg)   # 03:30 EDT
        self.assertEqual(off, dt.timedelta(hours=-4))

    def test_builtin_zone_table_when_zoneinfo_missing(self):
        orig = th._zoneinfo
        th._zoneinfo = lambda name: None
        try:
            # Portland trip planned from anywhere: 07:30 PDT on 2026-10-16 is 14:30Z
            off, how = th.offset_at_local(dt.datetime(2026, 10, 16, 7, 30), "America/Los_Angeles", None)
            self.assertEqual((off, how), (dt.timedelta(hours=-7), "built-in rule"))
            # Berlin: CEST until 2026-10-25 01:00Z, then CET
            self.assertEqual(th.offset_at_utc(dt.datetime(2026, 10, 25, 0, 30), "Europe/Berlin", None)[0],
                             dt.timedelta(hours=2))
            self.assertEqual(th.offset_at_utc(dt.datetime(2026, 10, 25, 1, 30), "Europe/Berlin", None)[0],
                             dt.timedelta(hours=1))
            # London is GMT after the switch (the old rule got this wrong)
            self.assertEqual(th.offset_at_local(dt.datetime(2026, 10, 25, 2, 30), "Europe/London", None)[0],
                             dt.timedelta(hours=0))
            # Sydney: daylight saving in January, not in July
            self.assertEqual(th.offset_at_local(dt.datetime(2026, 1, 15, 9), "Australia/Sydney", None)[0],
                             dt.timedelta(hours=11))
            self.assertEqual(th.offset_at_local(dt.datetime(2026, 7, 15, 9), "Australia/Sydney", None)[0],
                             dt.timedelta(hours=10))
        finally:
            th._zoneinfo = orig
        self.assertTrue(th.same_clock("America/Toronto", "America/New_York"))
        self.assertFalse(th.same_clock("America/Toronto", "America/Chicago"))

    def test_hhmm_in_the_past_rolls_to_tomorrow(self):
        orig = th.local_now
        th.local_now = lambda tz, reg: (dt.datetime(2026, 10, 9, 20, 35), "test")
        try:
            w = []
            self.assertEqual(th.parse_when("09:00", None, None, w), dt.datetime(2026, 10, 10, 9, 0))
            self.assertTrue(w and "tomorrow" in w[0])
            self.assertEqual(th.parse_when("23:00", None, None, []), dt.datetime(2026, 10, 9, 23, 0))
        finally:
            th.local_now = orig

    def test_feed_spec_parsing_and_unique_ids(self):
        used = set()
        self.assertEqual(th.parse_feed_spec("go=https://x/a.zip?y=1", used), ("go", "https://x/a.zip?y=1"))
        fid, src = th.parse_feed_spec("https://api.example.org/gtfs/download?agency=12&format=zip", used)
        self.assertEqual((fid, src), ("download", "https://api.example.org/gtfs/download?agency=12&format=zip"))
        a = th.parse_feed_spec("https://a.example/google_transit.zip", used)[0]
        b = th.parse_feed_spec("https://b.example/google_transit.zip", used)[0]
        self.assertNotEqual(a, b)

    def test_discover_filters_by_overlap_size_and_duplicates(self):
        def row(fid, provider, a, b, c, d, status="active"):
            return {"id": fid, "data_type": "gtfs", "provider": provider, "name": "", "status": status,
                    "location.bounding_box.minimum_latitude": str(a), "location.bounding_box.maximum_latitude": str(b),
                    "location.bounding_box.minimum_longitude": str(c), "location.bounding_box.maximum_longitude": str(d),
                    "urls.latest": "https://files.example/%s/latest.zip" % fid, "urls.direct_download": "",
                    "urls.authentication_type": "0", "is_official": "True"}
        rows = [row("mdb-1", "Local Bus", 43.4, 43.6, -79.9, -79.7),
                row("tld-9", "Local Bus", 43.4, 43.6, -79.9, -79.7),          # duplicate listing
                row("mdb-2", "Neighbour Bus", 43.55, 43.8, -79.75, -79.5),    # overlaps, does not contain point
                row("mdb-3", "Nationwide Rail", 25, 60, -125, -60),           # too big
                row("mdb-4", "Far Away", 10, 11, 10, 11),
                row("mdb-5", "Old Bus", 43.4, 43.6, -79.9, -79.7, status="deprecated")]
        box = th.box_around(43.5, -79.85, 15000)
        rows.append(dict(row("mdb-6", "Neighbour Bus", 43.55, 43.8, -79.75, -79.5), name="Rail"))  # same agency, other feed
        rows[2]["name"] = "Bus"
        found, too_big, far, dups = th.discover_feeds(box, rows)
        self.assertEqual(sorted(f["id"] for f in found), ["mdb-1", "mdb-2", "mdb-6"])
        self.assertEqual([f["id"] for f in too_big], ["mdb-3"])
        self.assertEqual([f["id"] for f in dups], ["tld-9"])
        self.assertEqual(found[0]["urls"], ["https://files.example/mdb-1/latest.zip"])


class LatestDepartureTests(unittest.TestCase):
    def test_latest_departure_meets_deadline(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "f.zip")
        make_feed(p, STOPS, [("1", "WK", "a", "C"), ("1", "WK", "b", "C"), ("1", "WK", "c", "C")],
                  [("a", "09:00:00", "09:00:00", "A", 1, 0, 0), ("a", "09:30:00", "09:30:00", "C", 2, 0, 0),
                   ("b", "09:20:00", "09:20:00", "A", 1, 0, 0), ("b", "09:50:00", "09:50:00", "C", 2, 0, 0),
                   ("c", "09:40:00", "09:40:00", "A", 1, 0, 0), ("c", "10:10:00", "10:10:00", "C", 2, 0, 0)])
        _, scan, idx = scan_for(p, hms(8, 0), hms(11, 0))
        a = idx["Hub A"]
        self.assertEqual(scan.latest_departure(a, hms(10, 0))[0], hms(9, 20))
        self.assertEqual(scan.latest_departure(a, hms(10, 10))[0], hms(9, 40))
        self.assertEqual(scan.latest_departure(a, hms(9, 30))[0], hms(9, 0))
        self.assertIsNone(scan.latest_departure(a, hms(9, 29)))


class PlanTests(unittest.TestCase):
    def test_arrive_by_mode(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "f.zip")
        trips = [("X", "WK", "x1", "express"), ("X", "WK", "x2", "express"), ("L", "WK", "l1", "local")]
        st = [("x1", "08:20:00", "08:20:00", "D", 1, 0, 0), ("x1", "08:35:00", "08:35:00", "C", 2, 0, 0),
              ("x2", "08:40:00", "08:40:00", "D", 1, 0, 0), ("x2", "08:55:00", "08:55:00", "C", 2, 0, 0),
              ("l1", "07:30:00", "07:30:00", "A", 1, 0, 0), ("l1", "08:40:00", "08:40:00", "C", 2, 0, 0)]
        make_feed(p, STOPS, trips, st)
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--arrive-by", "2026-10-15 09:00",
                "--region", "none", "--no-discover", "--feed", "test=" + p, "--routing", "estimate", "--json", "--min-saving", "0",
                "--radius-km", "5", "--origin-walk-m", "300"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(args)
        res = json.loads(buf.getvalue())
        self.assertEqual(res["mode"], "arrive_by")
        d = [o for o in res["pareto"] + res["others"] if o["hub"] == "Hub D"]
        self.assertTrue(d)
        self.assertEqual(d[0]["board"], hms(8, 40))               # 08:55 arrival meets 09:00
        self.assertLess(d[0]["book_by"], hms(8, 40))
        self.assertEqual(res["transit_only"]["arrive"], hms(8, 40))

    """End-to-end plan() on a synthetic feed with offline routing."""

    def test_plan_ranks_hubs_and_reports_coverage(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "f.zip")
        trips = [("X", "WK", "x1", "express"), ("L", "WK", "l1", "local")]
        st = [("x1", "10:20:00", "10:20:00", "D", 1, 0, 0), ("x1", "10:35:00", "10:35:00", "C", 2, 0, 0),
              ("l1", "10:10:00", "10:10:00", "A", 1, 0, 0), ("l1", "11:30:00", "11:30:00", "C", 2, 0, 0)]
        make_feed(p, STOPS, trips, st)
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 10:00",
                "--region", "none", "--no-discover", "--feed", "test=" + p, "--routing", "estimate", "--json", "--min-saving", "0",
                "--radius-km", "5", "--origin-walk-m", "300"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(args)
        res = json.loads(buf.getvalue())
        hubs = [o["hub"] for o in res["pareto"] + res["others"]]
        self.assertIn("Hub D", hubs)
        self.assertGreaterEqual(res["hubs_checked"], 2)
        self.assertIsNotNone(res["transit_only"])
        self.assertTrue(all(o["arrive"] < res["transit_only"]["arrive"] for o in res["pareto"]))


if __name__ == "__main__":
    unittest.main()
