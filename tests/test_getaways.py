"""Tests for long trips to places with little transit (v0.8): several region files, hand-entered timetables,
holidays a feed does not mark, the out-of-area ride surcharge, and finishing with a ride.

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
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import STOPS, hms, make_feed, pin_clock, th  # noqa: E402

_CLOCK = pin_clock()   # plan() runs see a fixed "now" before their hard-coded trip dates (see test_engine)


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


BOX_HOME = {"min_lat": 43.0, "max_lat": 44.0, "min_lon": -80.5, "max_lon": -79.0}
BOX_PROVINCE = {"min_lat": 42.0, "max_lat": 46.0, "min_lon": -83.0, "max_lon": -76.0}
BOX_TOWN = {"min_lat": 43.2, "max_lat": 43.4, "min_lon": -81.9, "max_lon": -81.6}


class RegionSelection(unittest.TestCase):
    def setUp(self):
        self.home = {"id": "home", "bbox": BOX_HOME, "timezone": "America/Toronto", "notes_file": "home.md",
                     "ride_pricing": {"label": "home", "base": 3.0, "per_km": 0.8, "per_min": 0.2},
                     "feeds": [{"id": "go", "name": "GO"}], "flat_fares": {"MiWay": {"card": 3.5}},
                     "transfer_rules": {"verified": "x", "same_agency": {"MiWay": {"window_min": 120}},
                                        "pairs": [{"from": ["GO"], "to": ["MiWay"], "window_min": 180}]}}
        self.province = {"id": "province", "intercity": True, "bbox": BOX_PROVINCE, "notes_file": "prov.md",
                         "ride_pricing": {"label": "province", "base": 2.5, "per_km": 0.7, "per_min": 0.2},
                         "feeds": [{"id": "go", "name": "GO again"}, {"id": "via", "name": "VIA"}],
                         "holidays": [{"date": "2026-10-12", "name": "Thanksgiving"}],
                         "transfer_rules": {"same_agency": {"London Transit": {"window_min": 120}},
                                            "pairs": [{"from": ["VIA"], "to": ["X"]}]},
                         "ride_areas": [{"name": "Town", "lat": 43.3, "lon": -81.75, "radius_km": 8, "uber": "sparse"}]}
        self.regions = [self.home, self.province]

    def test_short_trips_use_the_home_region_only(self):
        home, extra = th.regions_for_trip(self.regions, "auto", (43.5, -79.8), (43.58, -79.65), 18000)
        self.assertIs(home, self.home)
        self.assertEqual(extra, [])

    def test_long_trips_add_the_destination_and_intercity_regions(self):
        home, extra = th.regions_for_trip(self.regions, "auto", (43.5, -79.8), (43.31, -81.76), 174000)
        self.assertIs(home, self.home)
        self.assertEqual(extra, [self.province])
        view = th.merge_regions(home, extra, 43.31, -81.76)
        self.assertEqual([f["id"] for f in view["feeds"]], ["go", "via"])    # home's copy of a shared feed wins
        self.assertEqual(view["ride_pricing"]["label"], "home")              # the first ride starts at home
        self.assertEqual(view["final_ride_pricing"]["label"], "province")    # the final ride ends out there
        self.assertEqual(view["notes_files"], ["home.md", "prov.md"])
        self.assertEqual(len(view["transfer_rules"]["pairs"]), 2)
        self.assertIn("London Transit", view["transfer_rules"]["same_agency"])
        self.assertEqual([h["date"] for h in view["holidays"]], ["2026-10-12"])
        self.assertEqual(th.ride_area(view, 43.31, -81.76)["uber"], "sparse")
        self.assertIsNone(th.ride_area(view, 43.5, -79.8))

    def test_origin_outside_every_local_region_uses_the_intercity_one(self):
        home, extra = th.regions_for_trip(self.regions, "auto", (43.0, -81.25), (43.31, -81.76), 50000)
        self.assertIs(home, self.province)
        self.assertEqual(extra, [])


class HandEnteredTimetables(unittest.TestCase):
    FEED = {"id": "hsat", "name": "Huron Shores", "manual": {
        "agency": "Huron Shores Area Transit", "url": "https://example.org", "timezone": "America/Toronto",
        "services": {"daily": {"days": "1111111", "start": "2026-01-01", "end": "2026-12-31",
                               "except": ["2026-12-25"]}},
        "routes": [{"route": "2", "name": "London - Grand Bend", "trips": [
            {"service": "daily", "headsign": "Grand Bend", "stops": [
                ["London #705", 43.02824, -81.27757, "10:41"], ["Exeter", 43.34748, -81.48149, "11:20"],
                ["Grand Bend Sobeys", 43.31306, -81.75406, "11:48"]]}]}]}}

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.saved = os.environ.get("TRIP_PLANNER_CACHE")
        os.environ["TRIP_PLANNER_CACHE"] = self.cache

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("TRIP_PLANNER_CACHE", None)
        else:
            os.environ["TRIP_PLANNER_CACHE"] = self.saved

    def test_builds_a_loadable_feed_that_respects_its_exceptions(self):
        path = th.manual_feed_zip(self.FEED)
        self.assertEqual(path, th.manual_feed_zip(self.FEED))   # cached by content
        net = th.Network()
        net.warnings = []
        th.load_feed(net, "hsat", "Huron Shores", path, th.dt.date(2026, 10, 12), hms(6, 0), hms(14, 0))
        self.assertEqual(len(net.conns), 2)
        self.assertEqual(net.trips[0][1], "Huron Shores Area Transit")
        self.assertEqual(net.conns[-1][1], hms(11, 48))
        net2 = th.Network()
        net2.warnings = []
        th.load_feed(net2, "hsat", "Huron Shores", path, th.dt.date(2026, 12, 25), hms(6, 0), hms(14, 0))
        self.assertEqual(len(net2.conns), 0)                     # no service on Christmas


class Holidays(unittest.TestCase):
    def test_a_holiday_the_file_does_not_mark_runs_the_chosen_schedule(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "h.zip")
        make_feed(p, STOPS, [("1", "WK", "w1", "C"), ("1", "SU", "s1", "C")],
                  [("w1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("w1", "10:20:00", "10:20:00", "C", 2, 0, 0),
                   ("s1", "11:00:00", "11:00:00", "A", 1, 0, 0), ("s1", "11:20:00", "11:20:00", "C", 2, 0, 0)],
                  calendar=[("WK", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231"),
                            ("SU", 0, 0, 0, 0, 0, 0, 1, "20260101", "20261231")])
        monday = th.dt.date(2026, 10, 12)

        def deps(holidays):
            net = th.Network()
            net.warnings = []
            th.load_feed(net, "f", "F", p, monday, hms(6, 0), hms(14, 0), holidays)
            return sorted(c[0] for c in net.conns), net.warnings

        self.assertEqual(deps(None)[0], [hms(10, 0)])                         # the file thinks it is a Monday
        sunday, w = deps({monday: "sunday"})
        self.assertEqual(sunday, [hms(11, 0)])
        self.assertTrue(any("holiday" in x for x in w))
        self.assertEqual(deps({monday: "none"})[0], [])
        region = {"holidays": [{"date": "2026-10-12"}]}
        self.assertEqual(th.holiday_modes(region, {"holiday_service": "sunday"}), {monday: "sunday"})
        self.assertEqual(th.holiday_modes(region, {}), {})                   # default: trust the file


class RidePricing(unittest.TestCase):
    def test_leaving_the_service_area_adds_the_per_km_surcharge(self):
        p = {"base": 3.0, "per_km": 1.0, "per_min": 0.0, "service_area": BOX_HOME, "out_of_area_per_km": 0.35}
        inside = th.ride_cost(p, 100, 0, ((43.5, -79.8), (43.6, -79.5)))
        out = th.ride_cost(p, 100, 0, ((43.5, -79.8), (43.31, -81.76)))
        self.assertAlmostEqual(inside, 103.0)
        self.assertAlmostEqual(out, 138.0)
        self.assertAlmostEqual(th.ride_cost(p, 100, 0), 103.0)               # no ends given: no surcharge


class FeedQuirks(unittest.TestCase):
    def feed(self, tz="America/Toronto"):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "q.zip")
        make_feed(p, STOPS, [("1", "WK", "t1", "C"), ("9", "WK", "t9", "C")],
                  [("t1", "15:00:00", "15:00:00", "A", 1, 0, 0), ("t1", "15:30:00", "15:30:00", "C", 2, 0, 0),
                   ("t9", "15:10:00", "15:10:00", "A", 1, 0, 0), ("t9", "15:40:00", "15:40:00", "C", 2, 0, 0)])
        if tz != "America/Toronto":
            from test_regressions import add_files
            add_files(p, {"agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nT,Coach,http://x,%s\n" % tz})
        return p

    def test_a_feed_in_utc_is_shifted_to_local_time(self):
        net = th.Network()
        net.warnings = []
        th.load_feed(net, "f", "F", self.feed("UTC"), th.dt.date(2026, 10, 15), hms(6, 0), hms(14, 0), None,
                     "America/Toronto", {"timezone": "America/Toronto", "utc_std_offset_hours": -5,
                                         "dst_rule": "north_america"})
        self.assertEqual(sorted(c[0] for c in net.conns), [hms(11, 0), hms(11, 10)])   # 15:00 UTC = 11:00 EDT
        self.assertEqual(net.timezones, {"America/Toronto"})

    def test_excluded_routes_are_dropped(self):
        net = th.Network()
        net.warnings = []
        th.load_feed(net, "f", "F", self.feed(), th.dt.date(2026, 10, 15), hms(6, 0), hms(20, 0),
                     exclude_routes={"9"})
        self.assertEqual([net.trips[c[4]][2] for c in net.conns], ["1"])

    def test_a_fare_with_no_card_price_falls_back_to_cash(self):
        class Net:
            fares, fare_urls, stop_zone = {}, {}, [""]
        leg = [{"type": "ride", "feed": "wego", "agency": "WEGO", "route": "604", "route_id": "1", "from": "A",
                "to": "B", "from_idx": 0, "to_idx": 0, "depart": 36000}]
        flat = {"WEGO": {"card": None, "contactless": None, "cash": 16.0}}
        self.assertEqual(th.journey_fare(Net, leg, flat, "card")[0], 16.0)
        self.assertEqual(th.journey_fare(Net, leg, flat, "contactless")[0], 16.0)

    def test_leg_names_do_not_repeat_the_brand(self):
        self.assertEqual(th.leg_name({"agency": "FlixBus-us", "route": "FlixBus 2702", "headsign": ""}), "FlixBus 2702")
        self.assertEqual(th.leg_name({"agency": "GO Transit", "route": "21", "headsign": "21"}), "GO Transit 21")


class PricingByArea(unittest.TestCase):
    REGION = {"ride_pricing_areas": [
        {"name": "Hamilton", "boxes": [{"min_lat": 43.05, "max_lat": 43.29, "min_lon": -80.25, "max_lon": -79.62}],
         "pricing": {"label": "Hamilton", "base": 3.0, "per_km": 1.0, "per_min": 0.0, "out_of_area_per_km": 0.35}},
        {"name": "Toronto", "boxes": [BOX_HOME],
         "pricing": {"label": "Toronto", "base": 3.0, "per_km": 1.0, "per_min": 0.0, "out_of_area_per_km": 0.35}}],
        "ride_pricing_outside": {"label": "outside", "base": 3.0, "per_km": 0.9, "per_min": 0.0},
        "taxi_pricing_default": {"base": 5.0, "per_km": 1.9}}

    def test_the_pickup_area_sets_the_card_and_leaving_it_costs_extra(self):
        base = {"base": 9.0, "per_km": 9.0, "per_min": 0.0}
        ham = th.pricing_at(self.REGION, 43.25, -79.87, base)
        self.assertEqual(ham["label"], "Hamilton")                         # first match wins over Toronto
        self.assertAlmostEqual(th.ride_cost(ham, 10, 0, ((43.25, -79.87), (43.20, -79.80))), 13.0)
        self.assertAlmostEqual(th.ride_cost(ham, 10, 0, ((43.25, -79.87), (43.5, -79.8))), 16.5)
        tor = th.pricing_at(self.REGION, 43.5, -79.8, base)
        self.assertEqual(tor["label"], "Toronto")
        self.assertAlmostEqual(th.ride_cost(tor, 10, 0, ((43.31, -81.76), (43.5, -79.8))), 13.0)  # enters: no extra
        self.assertEqual(th.pricing_at(self.REGION, 45.25, -81.66, base)["label"], "outside")
        self.assertEqual(th.pricing_at({}, 45.25, -81.66, base), base)
        taxi = th.taxi_pricing(self.REGION, {"name": "Tobermory", "uber": "no"})
        self.assertAlmostEqual(th.ride_cost(taxi, 100, 3600), 195.0)       # per km only, tax included


class OntarioRegionFile(unittest.TestCase):
    """The shipped ontario.json: every hand-entered timetable builds and loads, and keys are valid."""

    @classmethod
    def setUpClass(cls):
        cls.regions = th.load_regions()
        cls.ontario = next(r for r in cls.regions if r["id"] == "ontario")

    def test_hand_entered_timetables_build_and_load(self):
        cache = tempfile.mkdtemp()
        saved = os.environ.get("TRIP_PLANNER_CACHE")
        os.environ["TRIP_PLANNER_CACHE"] = cache
        try:
            for f in self.ontario["feeds"]:
                if not f.get("manual"):
                    self.assertTrue(f.get("urls"), f["id"])
                    continue
                path = th.manual_feed_zip(f)
                n = 0
                days = sorted({th.dt.date.fromisoformat(sv["start"]) + th.dt.timedelta(days=k)
                               for sv in f["manual"]["services"].values() for k in range(7)})
                for day in days:   # some day in the first week of one of its seasons has service
                    net = th.Network()
                    net.warnings = []
                    th.load_feed(net, f["id"], f["name"], path, day, 0, 30 * 3600)
                    n = max(n, len(net.conns))
                    if n:
                        break
                self.assertGreater(n, 0, f["id"])
                self.assertIn(f.get("holiday_service", "as_feed"), ("as_feed", "none", "sunday", "saturday"))
        finally:
            if saved is None:
                os.environ.pop("TRIP_PLANNER_CACHE", None)
            else:
                os.environ["TRIP_PLANNER_CACHE"] = saved

    def test_timetables_print_offline(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(["--timetable", "list"])
        self.assertIn("hsat", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(["--timetable", "hsat"])
        out = buf.getvalue()
        self.assertIn("ROUTE 2", out)
        self.assertIn("Sobeys Plaza", out)
        with self.assertRaises(SystemExit):
            th.main(["--timetable", "no-such-feed"])

    def test_a_trip_to_grand_bend_loads_both_region_files(self):
        home, extra = th.regions_for_trip(self.regions, "auto", (43.4988, -79.8560), (43.3127, -81.7568), 174000)
        self.assertEqual(home["id"], "gtha")
        self.assertEqual([r["id"] for r in extra], ["ontario"])
        view = th.merge_regions(home, extra, 43.3127, -81.7568)
        ids = [f["id"] for f in view["feeds"]]
        self.assertIn("hsat", ids)
        self.assertEqual(ids.count("go"), 1)
        self.assertEqual(th.ride_area(view, 43.3127, -81.7568)["uber"], "sparse")
        p = th.pricing_at(view, 43.03, -81.28, dict(th.DEFAULT_PRICING))   # London: London card
        self.assertIn("London", p["label"])
        milton = th.pricing_at(view, 43.4988, -79.8560, dict(th.DEFAULT_PRICING))
        self.assertIn("Toronto", milton["label"])
        self.assertGreater(th.ride_cost(milton, 174, 130 * 60, ((43.4988, -79.8560), (43.3127, -81.7568))),
                           th.ride_cost(milton, 174, 130 * 60))              # leaving the Toronto region costs extra


class ReviewFixes(unittest.TestCase):
    """Defects from the v0.8 review, each reproduced first."""

    def net_for(self, p, day=th.dt.date(2026, 10, 15), **kw):
        net = th.Network()
        net.warnings = []
        th.load_feed(net, "f", "F", p, day, hms(0, 0), hms(30, 0), **kw)
        return net

    def test_excluded_routes_match_route_ids_too(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "q.zip")
        make_feed(p, STOPS, [("90", "WK", "t1", "C"), ("101", "WK", "t2", "C")],
                  [("t1", "15:00:00", "15:00:00", "A", 1, 0, 0), ("t1", "15:30:00", "15:30:00", "C", 2, 0, 0),
                   ("t2", "15:10:00", "15:10:00", "A", 1, 0, 0), ("t2", "15:40:00", "15:40:00", "C", 2, 0, 0)])
        from test_regressions import add_files
        add_files(p, {"routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                                    "90,T,ONTC,Northlander,3\n101,T,ONTC,Toronto - North Bay,3\n"})
        net = self.net_for(p, exclude_routes={"90", "91"})
        self.assertEqual([net.trips[c[4]][7] for c in net.conns], ["101"])

    def test_utc_shift_follows_the_clock_change(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "u.zip")
        make_feed(p, STOPS, [("1", "ALL", "t1", "C")],
                  [("t1", "12:00:00", "12:00:00", "A", 1, 0, 0), ("t1", "12:30:00", "12:30:00", "C", 2, 0, 0)],
                  calendar=[("ALL", 1, 1, 1, 1, 1, 1, 1, "20260101", "20261231")])
        from test_regressions import add_files
        add_files(p, {"agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nT,Coach,http://x,UTC\n"})
        reg = {"timezone": "America/Toronto", "utc_std_offset_hours": -5, "dst_rule": "north_america"}
        net = th.Network()
        net.warnings = []
        th.load_feed(net, "f", "F", p, th.dt.date(2026, 10, 31), hms(0, 0), hms(48, 0), None, "America/Toronto", reg)
        deps = sorted(c[0] for c in net.conns)
        self.assertEqual(deps, [hms(8, 0), hms(24 + 7, 0)])   # 12:00 UTC: 08:00 EDT, then 07:00 EST on Nov 1

    def test_holiday_outside_the_file_keeps_the_no_service_warning(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "h.zip")
        make_feed(p, STOPS, [("1", "WK", "w1", "C")],
                  [("w1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("w1", "10:20:00", "10:20:00", "C", 2, 0, 0)],
                  calendar=[("WK", 1, 1, 1, 1, 1, 0, 0, "20260901", "20261130")])
        xmas = th.dt.date(2026, 12, 25)
        net = self.net_for(p, day=xmas, holidays={xmas: "sunday"})
        self.assertFalse(any("Sunday schedule is used" in w for w in net.warnings))
        self.assertTrue(any("NO service" in w for w in net.warnings))

    def test_coach_boarding_buffer_and_request_stops(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "b.zip")
        make_feed(p, STOPS, [("1", "WK", "local", "B"), ("2", "WK", "coach", "C")],
                  [("local", "10:00:00", "10:00:00", "A", 1, 0, 0), ("local", "10:10:00", "10:10:00", "B", 2, 0, 0),
                   ("coach", "10:15:00", "10:15:00", "B", 1, 0, 0), ("coach", "11:00:00", "11:00:00", "C", 2, 0, 3)])
        local = os.path.join(tmp, "l.zip")
        make_feed(local, STOPS, [("1", "WK", "local", "B")],
                  [("local", "10:00:00", "10:00:00", "A", 1, 0, 0), ("local", "10:10:00", "10:10:00", "B", 2, 0, 0)])
        coach = os.path.join(tmp, "c.zip")
        make_feed(coach, STOPS, [("2", "WK", "coach", "C")],
                  [("coach", "10:15:00", "10:15:00", "B", 1, 0, 0), ("coach", "11:00:00", "11:00:00", "C", 2, 0, 3)])
        for buf, reachable in ((0, True), (600, False)):
            net = th.Network()
            net.warnings = []
            th.load_feed(net, "l", "Local", local, th.dt.date(2026, 10, 15), hms(9, 0), hms(13, 0))
            th.load_feed(net, "c", "Coach", coach, th.dt.date(2026, 10, 15), hms(9, 0), hms(13, 0),
                         board_buffer_s=buf)
            idx = {n: i for i, n in enumerate(net.stop_name)}
            egress = {i: 0 for i, n in enumerate(net.stop_name) if n == "Dest C"}
            used = sorted({c[2] for c in net.conns} | {c[3] for c in net.conns})
            scan = th.ProfileScan(net, egress, th.build_footpaths(net, used, 400, 120), 120, {})
            arr, legs = scan.journey(idx["Hub A"], hms(9, 55))
            self.assertEqual(arr < th.INF, reachable)   # 10 min to board leaves no time after the 10:10 bus
            if reachable:
                self.assertTrue([lg for lg in legs if lg["type"] == "ride"][-1].get("request_alight"))
            else:
                _arr, legs = scan.journey([i for i, n in enumerate(net.stop_name) if n == "Transfer B"][-1], hms(10, 0))
                self.assertEqual(legs[0]["be_there_by"], hms(10, 5))
                self.assertIn("be at the stop by 10:05", th.legs_text(legs))

    def test_route_level_flat_fare(self):
        class Net:
            fares, fare_urls, stop_zone = {}, {}, [""]
        leg = [{"type": "ride", "feed": "niagara", "agency": "Niagara Region Transit", "route": "603",
                "route_id": "x", "from": "A", "to": "B", "from_idx": 0, "to_idx": 0, "depart": 36000}]
        flat = {"Niagara Region Transit 603": {"card": 16.0, "cash": 16.0}}
        self.assertEqual(th.journey_fare(Net, leg, flat, "card")[0], 16.0)

    def test_uncovered_destination_loads_the_intercity_file_on_short_trips(self):
        home = {"id": "home", "bbox": BOX_HOME}
        prov = {"id": "province", "intercity": True, "bbox": BOX_PROVINCE,
                "ride_pricing_areas": [{"name": "X", "boxes": [BOX_PROVINCE], "pricing": {"label": "X"}}]}
        h, extra = th.regions_for_trip([home, prov], "auto", (43.45, -80.49), (43.37, -80.98), 40500)
        self.assertEqual(extra, [] if th.in_bbox(43.37, -80.98, BOX_HOME) else [prov])
        h, extra = th.regions_for_trip([home, prov], "auto", (43.5, -79.8), (43.6, -79.6), 20000)
        self.assertEqual(extra, [])
        view = th.merge_regions(h, extra, 43.6, -79.6, th.pricing_regions([home, prov], 43.5, -79.8))
        self.assertEqual(view["ride_pricing_areas"][0]["name"], "X")   # rate areas even on short trips


class RideInstead(unittest.TestCase):
    def test_a_short_ride_to_the_coach_stop_is_offered_when_it_skips_local_buses(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "rc.zip")
        make_feed(p, STOPS, [("1", "WK", "local", "B"), ("2", "WK", "coach", "C")],
                  [("local", "10:00:00", "10:00:00", "A", 1, 0, 0), ("local", "10:20:00", "10:20:00", "B", 2, 0, 0),
                   ("coach", "10:40:00", "10:40:00", "B", 1, 0, 0), ("coach", "11:30:00", "11:30:00", "C", 2, 0, 0)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 09:50",
                     "--region", "none", "--no-discover", "--feed", "t=" + p, "--routing", "estimate",
                     "--min-saving", "0", "--json"])
        res = json.loads(buf.getvalue())
        ri = res["transit_only"]["ride_instead"]
        self.assertIn("Transfer B", ri["hub"])
        self.assertEqual(ri["skips"], 1)
        self.assertEqual(ri["arrive"], res["transit_only"]["arrive"])


class FinishWithARide(unittest.TestCase):
    def test_a_destination_with_no_stop_is_reached_by_transit_then_a_ride(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "line.zip")
        make_feed(p, STOPS, [("1", "WK", "t1", "Far E"), ("1", "WK", "t2", "Far E")],
                  [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:30:00", "10:30:00", "E", 2, 0, 0),
                   ("t2", "11:00:00", "11:00:00", "A", 1, 0, 0), ("t2", "11:30:00", "11:30:00", "E", 2, 0, 0)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(["--origin", "43.4990,-79.8000", "--dest", "43.6800,-79.8000", "--depart", "2026-10-15 09:30",
                     "--region", "none", "--no-discover", "--feed", "t=" + p, "--routing", "estimate",
                     "--final-ride", "on", "--min-saving", "0", "--json"])
        res = json.loads(buf.getvalue())
        self.assertEqual(res["pareto"], [])                                 # nothing reaches the door by transit
        fin = res["finish_with_ride"]
        self.assertTrue(fin)
        best = min(fin, key=lambda o: o["arrive"])
        self.assertEqual(best["legs"][-1]["type"], "final_ride")
        self.assertEqual(best["final_ride"]["from"], "Far E")
        self.assertGreater(best["arrive"], hms(10, 30))
        self.assertAlmostEqual(best["total_ride_cost"], best["ride_cost"] + best["final_ride"]["cost"], places=1)
        self.assertTrue(any("ends with a ride" in w for w in res["warnings"]))

    def test_long_trips_get_a_longer_search_window(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "line.zip")
        make_feed(p, STOPS, [("1", "WK", "t1", "Far E")],
                  [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:30:00", "10:30:00", "E", 2, 0, 0)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            th.main(["--origin", "43.4990,-79.8000", "--dest", "44.6000,-79.8000", "--depart", "2026-10-15 09:30",
                     "--region", "none", "--no-discover", "--feed", "t=" + p, "--routing", "estimate", "--json"])
        res = json.loads(buf.getvalue())
        self.assertGreater(res["window_h"], 4.0)


if __name__ == "__main__":
    unittest.main()
