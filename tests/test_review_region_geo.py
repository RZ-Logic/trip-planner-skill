"""Regression tests for the review fixes in region selection, ride areas, geocoding and discovery (trip_hubs.py):
region files never claim a point in another country (US border cities), and the first ride from a place with no
ride-hail is a taxi.

Offline: synthetic feeds and regions, every network call mocked, a fixed clock.
Run: PYTHONPATH=tools/ci TRIP_PLANNER_NO_NETWORK=1 python -m unittest tests.test_review_region_geo -v
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
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import pin_clock, th  # noqa: E402  (test_engine adds the scripts folder)
from test_regressions import add_files  # noqa: E402
from test_v098_hubs import feed, ok_network, run  # noqa: E402  (functions only: never import TestCase classes)

DAY = dt.date(2026, 10, 15)   # Thursday
BASE = ["--region", "none", "--no-discover", "--routing", "estimate"]
REGIONS = {r["id"]: r for r in th.load_regions()}
ONTARIO = REGIONS["ontario"]
TZ = {"timezone": "America/Toronto", "utc_std_offset_hours": -5, "dst_rule": "north_america"}
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


def place_info_by_lat(cut, south="CA", north="US"):
    """reverse_place_info stand-in: country by latitude (north of `cut` is `north`), no names."""
    return lambda lat, lon, warnings: {"names": set(), "country": north if lat >= cut else south, "state": None}


def catalog_row(fid, provider, url, box):
    """One MobilityDatabase catalog row (the CSV columns discover_feeds reads)."""
    return {"id": fid, "data_type": "gtfs", "status": "active", "provider": provider, "name": "",
            "urls.latest": url, "urls.direct_download": "", "urls.authentication_type": "0", "is_official": "True",
            "location.bounding_box.minimum_latitude": str(box[0]),
            "location.bounding_box.maximum_latitude": str(box[1]),
            "location.bounding_box.minimum_longitude": str(box[2]),
            "location.bounding_box.maximum_longitude": str(box[3])}


# ---------------------------------------------------------------------------------------- hubs-pricing-io-1
class RegionCountry(unittest.TestCase):
    """A region file never claims a point that reverse geocoding places in another country."""

    def test_detroit_trip_is_not_claimed_by_an_ontario_box(self):
        region = dict(TZ, id="on", country="CA",
                      bbox={"min_lat": 41.6, "max_lat": 46.7, "min_lon": -83.2, "max_lon": -74.3},
                      ride_pricing={"label": "Windsor card", "base": 2.5, "per_km": 0.81, "per_min": 0.18,
                                    "tax": 0.13, "currency": "CAD"}, feeds=[])
        ddot = feed([("S1", "Downtown", 42.3320, -83.0460), ("S2", "Zoo", 42.4760, -83.1490)],
                    [("4", "WK", "w1", "Zoo")],
                    [("w1", "10:00:00", "10:00:00", "S1", 1, 0, 0), ("w1", "10:40:00", "10:40:00", "S2", 2, 0, 0)])
        add_files(ddot, {"agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                                       "T,DDOT,http://x,America/Detroit\n"})
        row = catalog_row("mdb-ddot", "DDOT", "https://files.example/ddot.zip", (42.2, 42.6, -83.3, -82.9))
        with mock.patch.object(th, "load_regions", return_value=[region]), \
                mock.patch.object(th, "check_network", side_effect=ok_network), \
                mock.patch.object(th, "reverse_place_info", side_effect=place_info_by_lat(0.0)), \
                mock.patch.object(th, "load_catalog", return_value=[row]), \
                mock.patch.object(th, "fetch_url", side_effect=lambda fid, url, *a, **k: ddot):
            res = run(["--origin", "42.3314,-83.0458", "--dest", "42.4765,-83.1497", "--depart", "2026-10-15 09:30",
                       "--routing", "estimate"])
        self.assertIsNone(res["region"])
        self.assertIn("DDOT", [f["name"] for f in res["feeds"]])
        self.assertEqual(res["pricing"]["label"], "generic ride-hail estimate")
        self.assertEqual(res["pricing"]["currency"], "USD")
        self.assertNotIn("tax", res["pricing"])

    def test_region_never_claims_a_point_in_another_country(self):
        regions = list(REGIONS.values())
        self.assertEqual(th.regions_for_trip(regions, "auto", (42.8864, -78.8784), (42.9405, -78.7322), 13000,
                                             countries=("US", "US")), (None, []))
        plain = th.regions_for_trip(regions, "auto", (43.4988, -79.8560), (43.5812, -79.6475), 20000)
        same = th.regions_for_trip(regions, "auto", (43.4988, -79.8560), (43.5812, -79.6475), 20000,
                                   countries=("CA", "CA"))
        self.assertEqual(plain[0]["id"], "gtha")
        self.assertEqual((same[0]["id"], [r["id"] for r in same[1]]), (plain[0]["id"], [r["id"] for r in plain[1]]))
        self.assertIsNone(th.pick_region(regions, "auto", 42.3314, -83.0458, country="US"))
        self.assertEqual(th.pick_region(regions, "auto", 42.3149, -83.0364, country="CA")["id"], "ontario")
        self.assertEqual(th.pricing_at(ONTARIO, 42.3314, -83.0458, {"label": "fb"}, country="US")["label"], "fb")
        windsor = th.pricing_at(ONTARIO, 42.3149, -83.0364, {"label": "fb"}, country="CA")
        self.assertIn("Windsor", windsor["label"])
        self.assertEqual(th.pricing_regions(regions, 42.3314, -83.0458, country="US"), [])

    def test_cross_border_destination_discovers_feeds_and_drops_drop_points_across_the_border(self):
        box = {"min_lat": 43.3, "max_lat": 43.9, "min_lon": -80.0, "max_lon": -79.6}
        local = feed([("A", "Home stop", 43.5000, -79.8000), ("Z", "Far stop", 43.7880, -79.8000)],
                     [("1", "WK", "t1", "Z")],
                     [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:50:00", "10:50:00", "Z", 2, 0, 0)])
        there = feed([("D1", "Dest stop", 43.8110, -79.8000), ("D2", "Dest centre", 43.8200, -79.8000)],
                     [("2", "WK", "d1", "D2")],
                     [("d1", "11:00:00", "11:00:00", "D1", 1, 0, 0), ("d1", "11:10:00", "11:10:00", "D2", 2, 0, 0)])
        region = dict(TZ, id="home", country="CA", bbox=box,
                      feeds=[{"id": "local", "name": "Home Bus", "urls": [local], "bbox": box}])
        row = catalog_row("mdb-7", "Dest Transit", "https://files.example/dest.zip", (43.75, 43.85, -79.85, -79.75))
        urls = {"https://files.example/dest.zip": there, local: local}
        with mock.patch.object(th, "load_regions", return_value=[region]), \
                mock.patch.object(th, "check_network", side_effect=ok_network), \
                mock.patch.object(th, "reverse_place_info", side_effect=place_info_by_lat(43.80)), \
                mock.patch.object(th, "load_catalog", return_value=[row]), \
                mock.patch.object(th, "fetch_url", side_effect=lambda fid, url, *a, **k: urls[url]):
            res = run(["--origin", "43.5000,-79.8000", "--dest", "43.8100,-79.8000", "--depart", "2026-10-15 09:30",
                       "--routing", "estimate", "--final-ride", "on", "--min-saving", "0"])
        self.assertIn("Dest Transit", [f["name"] for f in res["feeds"]])
        self.assertTrue(any("the destination is in US, outside the region files' country (CA)" in w
                            for w in res["warnings"]), res["warnings"])
        self.assertNotIn("Far stop", [p["name"] for p in res["finish_info"]["drop_points"]])
        self.assertTrue(any("drop points across the border" in w for w in res["warnings"]), res["warnings"])
        self.assertTrue(any("this trip crosses the CA-US border" in w for w in res["warnings"]), res["warnings"])

    def test_country_lookup_is_skipped_when_preflight_found_nominatim_blocked(self):
        region = dict(TZ, id="home", country="CA", feeds=[],
                      bbox={"min_lat": 43.3, "max_lat": 43.9, "min_lon": -80.0, "max_lon": -79.6})
        p = feed([("A", "Hub A", 43.5000, -79.8000), ("C", "Dest C", 43.5500, -79.8000)], [("1", "WK", "t1", "C")],
                 [("t1", "10:00:00", "10:00:00", "A", 1, 0, 0), ("t1", "10:20:00", "10:20:00", "C", 2, 0, 0)])

        def half_blocked(hosts, timeout=6):
            return {h: ("blocked: refused" if h == "nominatim.openstreetmap.org" else "ok") for h in hosts}
        with mock.patch.object(th, "load_regions", return_value=[region]), \
                mock.patch.object(th, "check_network", side_effect=half_blocked), \
                mock.patch.object(th, "reverse_place_info", side_effect=AssertionError("no lookup")):
            res = run(["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 09:30",
                       "--routing", "estimate", "--no-discover", "--feed", "t=" + p])
        self.assertEqual(res["region"], "home")   # the box decides, as before

    def test_trip_domains_names_nominatim_as_optional_near_a_border(self):
        ns = th.argparse.Namespace(origin="42.3314,-83.0458", dest="42.4765,-83.1497", region="auto",
                                   routing="estimate", crosscheck=False, feed=None, no_discover=False,
                                   radius_km=None, dest_walk_m=1500)
        hosts, note = th.trip_domains(ns, list(REGIONS.values()))
        self.assertNotIn("nominatim.openstreetmap.org", hosts)
        self.assertIn("nominatim.openstreetmap.org", note)

    def test_shipped_regions_name_their_country(self):
        self.assertEqual(REGIONS["gtha"]["country"], "CA")
        self.assertEqual(REGIONS["ontario"]["country"], "CA")



# ---------------------------------------------------------------------------------- hubs-pricing-io-2 (+ ap-7)
class OriginRideArea(unittest.TestCase):
    """The first ride starts where the region file may say there is no ride-hail (the trip home from a getaway)."""

    def trip(self, area_name, origin):
        area = next(a for a in ONTARIO["ride_areas"] if a["name"] == area_name)
        region = dict(TZ, id="sb", name="sb", pickup_wait_min=8, feeds=[], ride_areas=[area],
                      bbox={"min_lat": 44.0, "max_lat": 46.0, "min_lon": -82.5, "max_lon": -80.5},
                      taxi_pricing_default=ONTARIO["taxi_pricing_default"])
        lat, lon = origin
        p = feed([("H", "Hub stop", lat + 0.0801, lon), ("D", "Dest stop", 45.0000, lon)], [("1", "WK", "t1", "D")],
                 [("t1", "10:00:00", "10:00:00", "H", 1, 0, 0), ("t1", "11:00:00", "11:00:00", "D", 2, 0, 0)])
        args = ["--origin", "%.4f,%.4f" % origin, "--dest", "45.0010,%.4f" % lon, "--depart", "2026-10-15 09:00",
                "--region", "sb", "--feed", "t=" + p, "--routing", "estimate", "--no-discover", "--min-saving", "0",
                "--final-ride", "off"]
        with mock.patch.object(th, "load_regions", return_value=[region]), \
                mock.patch.object(th, "check_network", side_effect=ok_network):
            return run(args), run(args, as_json=False), region, area

    def test_no_ride_hail_origin_prices_a_taxi_with_its_wait_and_no_uber_link(self):
        res, txt, region, area = self.trip("Sauble Beach", (44.6299, -81.2649))
        self.assertEqual(res["pickup_wait_min"], 30.0)
        self.assertTrue(res["pricing"]["label"].startswith("rural Ontario taxi estimate"), res["pricing"])
        self.assertEqual(res["pricing"]["currency"], "CAD")
        o = res["pareto"][0]
        self.assertIsNone(o["uber"])
        self.assertNotIn("m.uber.com", txt)
        self.assertNotIn("uber quote", txt)
        warn = next((w for w in res["warnings"]
                     if w.startswith("there is no ride-hail near Sauble Beach (the origin)")), None)
        self.assertIsNotNone(warn, res["warnings"])
        self.assertIn("Bayshore Taxi (Owen Sound)", warn)
        self.assertIn("30 min", warn)
        taxi = th.taxi_pricing(region, area)
        ra = res["ride_all"]
        self.assertAlmostEqual(ra["cost"], th.ride_cost(taxi, ra["ride_km"], ra["ride_s"]), places=6)
        # the option's km and minutes are rounded to 0.1
        self.assertAlmostEqual(o["ride_cost"], th.ride_cost(taxi, o["ride_km"], o["ride_min"] * 60), delta=0.1)
        # book the taxi 30 min ahead, not the region's 8 min: ride, platform and the 3 min margin on top
        self.assertLessEqual(abs(o["book_by"] - (o["board"] - 30 * 60 - o["ride_min"] * 60 - th.PLATFORM_S - 3 * 60)),
                             60)

    def test_sparse_origin_warns_to_book_ahead(self):
        res, txt, _region, _area = self.trip("Owen Sound", (44.5687, -80.9415))
        self.assertTrue(any(w.startswith("ride-hail near Owen Sound (the origin) is sparse") for w in res["warnings"]),
                        res["warnings"])
        self.assertIsNotNone(res["pareto"][0]["uber"])
        self.assertIn("uber quote", txt)
        self.assertEqual(res["pickup_wait_min"], 8.0)

    def test_origin_outside_every_area_keeps_the_ride_hail_plan(self):
        res, txt, _region, _area = self.trip("Sauble Beach", (44.4000, -81.2649))   # 25 km south of Sauble
        self.assertEqual(res["pickup_wait_min"], 8.0)
        self.assertIsNotNone(res["pareto"][0]["uber"])
        self.assertFalse(any("(the origin)" in w for w in res["warnings"]), res["warnings"])


# ------------------------------------------------------------------------------ hubs-pricing-io-6 (+ hubs-engine-6)
def transitous_trip(walk_from, board, arrive):
    """One Transitous itinerary (UTC clock strings, EDT = local + 4 h): walk to Hub A, bus 1 to Dest C."""
    return {"legs": [
        {"mode": "WALK", "startTime": "2026-10-15T%sZ" % walk_from, "endTime": "2026-10-15T%sZ" % board,
         "from": {"name": "Origin"}, "to": {"name": "Hub A"}},
        {"mode": "BUS", "agencyName": "Test Transit", "routeShortName": "1", "startTime": "2026-10-15T%sZ" % board,
         "endTime": "2026-10-15T%sZ" % arrive, "from": {"name": "Hub A"}, "to": {"name": "Dest C"}}]}


class CrossCheckMargin(unittest.TestCase):
    """The second opinion is asked with the plan's own safety margin, so the departure the margin excluded is not
    reported as a better connection."""

    def crosscheck(self, trips, replies, when):
        stops = [("A", "Hub A", 43.5000, -79.8000), ("C", "Dest C", 43.5500, -79.8000)]
        times = []
        for tid, (dep, arr) in trips.items():
            times += [(tid, dep, dep, "A", 1, 0, 0), (tid, arr, arr, "C", 2, 0, 0)]
        p = feed(stops, [("1", "WK", tid, "C") for tid in trips], times)
        asked = []

        def http_get(url, *a, **k):   # Transitous answers with the trips leaving at or after the asked time
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            asked.append(q)
            its = replies if q["arriveBy"] == ["true"] else [
                i for i in replies if i["legs"][0]["startTime"] >= q["time"][0]]
            return json.dumps({"itineraries": its}).encode()
        with mock.patch.object(th, "http_get", side_effect=http_get),                 mock.patch.object(th, "check_network", side_effect=ok_network):
            res = run(["--origin", "43.4955,-79.8000", "--dest", "43.5500,-79.8000", "--feed", "t=" + p,
                       "--crosscheck"] + when + BASE)
        return res, asked

    def test_arrive_by_bus_inside_the_margin_is_not_a_better_connection(self):
        res, asked = self.crosscheck({"a1": ("08:20:00", "08:40:00"), "a2": ("08:37:00", "08:58:00")},
                                     [transitous_trip("12:11:00", "12:20:00", "12:40:00"),
                                      transitous_trip("12:28:00", "12:37:00", "12:58:00")],
                                     ["--arrive-by", "2026-10-15 09:00"])
        self.assertEqual(asked[0]["time"], ["2026-10-15T12:57:00Z"])   # 09:00 less the 3 min margin
        self.assertEqual(res["crosscheck"]["transit_only"]["verdict"], "agrees")
        self.assertFalse(any("Transitous finds a better connection" in w for w in res["warnings"]), res["warnings"])

    def test_depart_bus_inside_the_margin_is_not_a_better_connection(self):
        res, asked = self.crosscheck({"a1": ("08:10:00", "08:30:00"), "a2": ("08:20:00", "08:40:00")},
                                     [transitous_trip("12:00:30", "12:10:00", "12:30:00"),
                                      transitous_trip("12:10:30", "12:20:00", "12:40:00")],
                                     ["--depart", "2026-10-15 08:00"])
        self.assertEqual(asked[0]["time"], ["2026-10-15T12:03:00Z"])   # 08:00 plus the 3 min margin
        self.assertEqual(res["transit_only"]["arrive"] // 60, 8 * 60 + 40)
        self.assertEqual(res["crosscheck"]["transit_only"]["verdict"], "agrees")
        self.assertFalse(any("Transitous finds a better connection" in w for w in res["warnings"]), res["warnings"])


if __name__ == "__main__":
    unittest.main()
