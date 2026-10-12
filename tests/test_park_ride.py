"""Tests for trip_hubs.py --access car (drive your own car to a hub with parking, then transit).

Offline: synthetic feeds from test_engine, a mocked Overpass reply, no network.
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
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import DAY, STOPS, hms, make_feed, pin_clock, th  # noqa: E402
from test_regressions import add_files  # noqa: E402

_CLOCK = pin_clock()   # plan() runs see a fixed "now" before their hard-coded trip dates (see test_engine)


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


BOX = {"min_lat": 43.3, "max_lat": 43.8, "min_lon": -80.0, "max_lon": -79.6}
BLOCK = {"verified": "2026-10-10", "agencies": [
    {"agency": "Test Transit", "label": "T rule", "route_types": [2], "price": {"free": True},
     "text": "free T parking", "no_free_parking": ["Transfer B"],
     "paid_only": [{"name": "Far E", "text": "paid only, price unknown"}], "source": "https://example.org/t"}],
    "stop_name_words": ["park & ride"], "stop_name_text": "free carpool lot"}
REGION = {"id": "testreg", "bbox": BOX, "timezone": "America/Toronto", "feeds": [], "park_and_ride": BLOCK,
          "holidays": [{"date": "2026-10-12", "name": "Thanksgiving"}]}


def overpass_reply(*elements):
    return json.dumps({"version": 0.6, "elements": list(elements)}).encode("utf-8")


def lot(oid, lat, lon, **tags):
    tags.setdefault("amenity", "parking")
    return {"type": "way", "id": oid, "center": {"lat": lat, "lon": lon}, "tags": tags}


# a park_ride lot ~110 m north of Hub D (bus only), a free public lot ~150 m from Transfer B (rail, but listed
# as no free parking), and a private lot beside Dest C (ignored)
LOTS = [lot(1, 43.5060, -79.8000, park_ride="yes", fee="no", name="D lot"),
        lot(2, 43.5213, -79.8000, fee="no", name="B public lot"),
        lot(3, 43.5502, -79.8000, access="private", fee="no")]


def rail_feed(path, trips, stop_times, rail_routes):
    """make_feed, with the listed routes as trains (route_type 2) and the rest as buses."""
    make_feed(path, STOPS, trips, stop_times)
    rows = sorted({t[0] for t in trips})
    add_files(path, {"routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n" + "".join(
        "%s,T,%s,Route %s,%d\n" % (r, r, r, 2 if r in rail_routes else 3) for r in rows)})


class Helpers(unittest.TestCase):
    def test_station_key(self):
        self.assertEqual(th.station_key("Union Station GO"), "union")
        self.assertEqual(th.station_key("Union Station Bus Terminal"), "union bus terminal")
        self.assertEqual(th.station_key("Kipling Station - Eastbound Platform"), "kipling")
        self.assertEqual(th.station_key("Downsview Park GO"), "downsview park")
        self.assertEqual(th.station_key("Milton GO"), "milton")

    def test_charge_cost(self):
        self.assertEqual(th.charge_cost("$2/hr;$5 max", 240), 5.0)
        self.assertEqual(th.charge_cost("1.75 CAD/30 minutes", 240), 14.0)
        self.assertEqual(th.charge_cost("$10/day", 240), 10.0)
        self.assertEqual(th.charge_cost("$3/h, $12/day", 600), 12.0)
        self.assertIsNone(th.charge_cost("ask the attendant", 240))
        self.assertIsNone(th.charge_cost("", 240))

    def test_osm_lots_keep_public_parking_only(self):
        els = LOTS + [{"type": "node", "id": 9, "lat": 43.6, "lon": -79.7, "tags": {"amenity": "parking",
                                                                                   "access": "customers"}},
                      lot(11, 43.6, -79.7, name="Pick-up Drop-off"), lot(12, 43.6, -79.7, name="Kiss & Ride"),
                      {"type": "node", "id": 10, "lat": 43.6, "lon": -79.7,
                       "tags": {"amenity": "parking", "park_ride": "no", "maxstay": "2 hours"}}]
        lots = th.osm_lots(els)
        self.assertEqual([x["id"] for x in lots], ["way/1", "way/2", "node/10"])
        self.assertTrue(lots[0]["park_ride"])
        self.assertFalse(lots[2]["park_ride"])
        self.assertEqual(lots[2]["maxstay_min"], 120)
        self.assertEqual((lots[0]["lat"], lots[0]["lon"]), (43.5060, -79.8000))

    def test_agency_price_weekday_weekend_holiday(self):
        ttc = {"price": {"weekday_daily": 7.0, "weekend_holiday_free": True}, "text": "paid", "text_free_day": "free"}
        hol = {"2026-10-12"}
        self.assertEqual(th.agency_price(ttc, DAY, hol), (7.0, "paid"))                       # Thursday
        self.assertEqual(th.agency_price(ttc, DAY.replace(day=17), hol), (0.0, "free"))      # Saturday
        self.assertEqual(th.agency_price(ttc, DAY.replace(day=12), hol), (0.0, "free"))      # holiday Monday
        self.assertEqual(th.agency_price({"price": {"free": True}, "text": "free GO"}, DAY, hol), (0.0, "free GO"))

    def test_build_parser_defaults(self):
        ns = th.build_parser().parse_args(["--origin", "1,2", "--dest", "3,4"])
        self.assertEqual((ns.access, ns.car_cost_per_km, ns.park_min, ns.stay_hours), ("ride", 0.17, 4.0, 4.0))
        ns = th.build_parser().parse_args(["--origin", "1,2", "--dest", "3,4", "--access", "car",
                                           "--car-cost-per-km", "0.2", "--stay-hours", "9"])
        self.assertEqual((ns.access, ns.car_cost_per_km, ns.stay_hours), ("car", 0.2, 9.0))


class HubParking(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.path = os.path.join(tmp, "f.zip")
        # R: train A -> B -> E; L: bus D -> C; P: bus from a "Park & Ride" stop
        stops = STOPS + [("P", "Hwy 9 Park & Ride", 43.5300, -79.7000)]
        trips = [("R", "WK", "r1", "C"), ("L", "WK", "l1", "C"), ("P", "WK", "p1", "C")]
        st = [("r1", "10:20:00", "10:20:00", "A", 1, 0, 0), ("r1", "10:25:00", "10:25:00", "B", 2, 0, 0),
              ("r1", "10:30:00", "10:30:00", "E", 3, 0, 0),
              ("l1", "10:15:00", "10:15:00", "D", 1, 0, 0), ("l1", "10:40:00", "10:40:00", "C", 2, 0, 0),
              ("p1", "10:15:00", "10:15:00", "P", 1, 0, 0), ("p1", "10:45:00", "10:45:00", "C", 2, 0, 0)]
        make_feed(self.path, stops, trips, st)
        add_files(self.path, {"routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                                            "L,T,L,Route L,3\nP,T,P,Route P,3\nR,T,R,Route R,2\n"})
        self.net = th.Network()
        self.net.warnings = []
        th.load_feed(self.net, "t", "test", self.path, DAY, hms(9, 0), hms(13, 0))
        used = sorted({c[2] for c in self.net.conns} | {c[3] for c in self.net.conns})
        pairs = sorted((th.haversine_m(43.499, -79.8, self.net.stop_lat[s], self.net.stop_lon[s]), s) for s in used)
        self.clusters = th.cluster_stops(self.net, pairs)

    def names(self, kept):
        return {self.net.stop_name[c["stops"][0]]: c["parking"] for c in kept}

    def test_rules_osm_and_exclusions(self):
        kept, summary = th.hub_parking(self.net, self.clusters, [BLOCK], th.osm_lots(LOTS), 240, DAY, set())
        got = self.names(kept)
        self.assertEqual(got["Hub A"]["basis"], "T rule")                 # (a) rail station of a listed agency
        self.assertEqual(got["Hub A"]["cost"], 0.0)
        self.assertEqual(got["Hub D"]["basis"], "OSM park_ride")          # (b) bus stop beside a park_ride lot
        self.assertTrue(got["Hub D"]["free"])
        self.assertEqual(got["Transfer B"]["basis"], "OSM parking near a station")   # (c), not the no-free rule
        self.assertEqual(got["Far E"]["basis"], "T rule")                 # paid_only: kept, price unknown
        self.assertIsNone(got["Far E"]["cost"])
        self.assertTrue(got["Far E"]["price_unknown"])
        self.assertEqual(got["Hwy 9 Park & Ride"]["basis"], "stop name")
        self.assertNotIn("Dest C", got)                                   # private lot only
        self.assertEqual(summary["excluded"], 1)
        self.assertEqual(summary["with_parking"], len(kept))
        self.assertEqual(summary["osm_lots"], 2)

    def test_without_osm_only_rules_qualify(self):
        kept, summary = th.hub_parking(self.net, self.clusters, [BLOCK], None, 240, DAY, set())
        self.assertEqual(set(self.names(kept)), {"Hub A", "Far E", "Hwy 9 Park & Ride"})
        self.assertIsNone(summary["osm_lots"])

    def test_bus_stop_is_not_a_station_for_nearby_parking(self):
        free_by_d = [lot(5, 43.5055, -79.8000, fee="no")]   # public lot (not park_ride) beside the bus-only stop
        kept, _ = th.hub_parking(self.net, self.clusters, [], th.osm_lots(free_by_d), 240, DAY, set())
        self.assertNotIn("Hub D", self.names(kept))

    def test_maxstay_shorter_than_the_stay_is_skipped(self):
        short = [lot(6, 43.5060, -79.8000, park_ride="yes", maxstay="2 hours")]
        kept, _ = th.hub_parking(self.net, self.clusters, [], th.osm_lots(short), 240, DAY, set())
        self.assertNotIn("Hub D", self.names(kept))
        kept, _ = th.hub_parking(self.net, self.clusters, [], th.osm_lots(short), 90, DAY, set())
        self.assertIn("Hub D", self.names(kept))


class OverpassFetch(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": self.cache})
        self.env.start()
        self.box = th.box_around(43.5, -79.8, 5000)

    def tearDown(self):
        self.env.stop()

    def test_429_waits_once_then_retries_and_caches(self):
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b""))
        self.addCleanup(err.close)   # recent Pythons warn about an HTTPError left open
        with mock.patch.object(th, "http_get", side_effect=[err, overpass_reply(*LOTS)]) as hg, \
                mock.patch.object(th.time, "sleep") as sl:
            w = []
            els = th.overpass_parking(self.box, w)
        self.assertEqual(len(els), 3)
        self.assertEqual(hg.call_count, 2)
        sl.assert_called_once_with(30)
        self.assertEqual(w, [])
        self.assertIn("overpass-api.de/api/interpreter?data=", hg.call_args[0][0])
        with mock.patch.object(th, "http_get", side_effect=AssertionError("cached")):
            self.assertEqual(len(th.overpass_parking(self.box, [])), 3)

    def test_failure_warns_and_returns_none(self):
        with mock.patch.object(th, "http_get", side_effect=OSError("offline")), mock.patch.object(th.time, "sleep"):
            w = []
            self.assertIsNone(th.overpass_parking(self.box, w))
        self.assertTrue(any("OpenStreetMap parking lookup failed" in x for x in w))


class CarModePlan(unittest.TestCase):
    """End to end: plan() in car mode with a region that has a park_and_ride block."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": self.tmp})
        self.env.start()
        self.path = os.path.join(self.tmp, "f.zip")
        trips = [("R", "WK", "r1", "C"), ("L", "WK", "l1", "C"), ("M", "WK", "m1", "C")]
        st = [("r1", "10:20:00", "10:20:00", "A", 1, 0, 0), ("r1", "10:35:00", "10:35:00", "C", 2, 0, 0),
              ("l1", "10:15:00", "10:15:00", "D", 1, 0, 0), ("l1", "10:40:00", "10:40:00", "C", 2, 0, 0),
              ("m1", "10:30:00", "10:30:00", "B", 1, 0, 0), ("m1", "10:38:00", "10:38:00", "C", 2, 0, 0)]
        rail_feed(self.path, trips, st, {"R"})

    def tearDown(self):
        self.env.stop()

    def run_plan(self, region, extra_args, osm=(LOTS[0],)):
        args = ["--origin", "43.4990,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-15 10:00",
                "--no-discover", "--feed", "test=" + self.path, "--routing", "estimate", "--radius-km", "4",
                "--origin-walk-m", "50"] + extra_args
        buf = io.StringIO()
        with mock.patch.object(th, "load_regions", return_value=[region]), \
                mock.patch.object(th, "check_network", side_effect=lambda hosts, timeout=6: {h: "ok" for h in hosts}), \
                mock.patch.object(th, "http_get", return_value=overpass_reply(*osm)) as hg, redirect_stdout(buf):
            th.main(args)
        return buf.getvalue(), hg

    def test_car_mode_json(self):
        out, hg = self.run_plan(REGION, ["--access", "car", "--json"])
        res = json.loads(out)
        self.assertEqual(res["access"], "car")
        self.assertEqual(hg.call_count, 1)                               # one Overpass query per run
        opts = res["pareto"] + res["others"]
        hubs = {o["hub"]: o for o in opts}
        self.assertIn("Hub A", hubs)
        self.assertNotIn("Transfer B", hubs)                             # no parking: left out
        self.assertNotIn("Transfer B across street", hubs)
        ps = res["parking_summary"]
        self.assertEqual((ps["with_parking"], ps["excluded"]), (2, 1))
        self.assertEqual(ps["by_basis"], {"T rule": 1, "OSM park_ride": 1})
        a = hubs["Hub A"]
        self.assertEqual(a["parking"]["basis"], "T rule")
        self.assertEqual(a["car_cost"], round(a["ride_km"] * 0.17, 2))
        self.assertIsNone(a["uber"])
        ra = res["ride_all"]
        self.assertAlmostEqual(ra["cost"], ra["ride_km"] * 0.17)
        self.assertAlmostEqual(ra["arrive"], hms(10, 0) + ra["ride_s"])  # no pickup wait

    def test_car_mode_text(self):
        out, _ = self.run_plan(REGION, ["--access", "car"])
        self.assertIn("CAR COST MODEL  $0.17/km", out)
        self.assertIn("BASELINE drive all the way:", out)
        self.assertIn("run drive_plan.py for tolls and parking", out)
        self.assertIn("parking: free T parking [T rule]", out)
        self.assertIn("leave by", out)
        self.assertNotIn("book by", out)
        self.assertNotIn("uber quote", out)
        self.assertIn("drive radius", out)
        self.assertNotIn("—", out)
        self.assertNotIn("–", out)

    def test_paid_hub_parking_is_priced_for_the_stay(self):
        block = dict(BLOCK, agencies=[dict(BLOCK["agencies"][0], price={"weekday_daily": 7.0,
                                                                         "weekend_holiday_free": True},
                                           text="paid T lot")])
        out, _ = self.run_plan(dict(REGION, park_and_ride=block), ["--access", "car", "--json"])
        a = {o["hub"]: o for o in json.loads(out)["pareto"] + json.loads(out)["others"]}["Hub A"]
        self.assertEqual(a["parking"]["cost"], 7.0)
        self.assertAlmostEqual(a["car_cost"], round(a["ride_km"] * 0.17 + 7.0, 2))

    def test_ride_mode_ignores_the_block_and_skips_overpass(self):
        plain = dict(REGION)
        plain.pop("park_and_ride")
        with_block, hg1 = self.run_plan(REGION, ["--min-saving", "0"])
        without, hg2 = self.run_plan(plain, ["--min-saving", "0"])
        self.assertEqual(with_block, without)
        self.assertEqual(hg1.call_count + hg2.call_count, 0)
        self.assertNotIn("access", json.loads(self.run_plan(REGION, ["--json"])[0]))
        self.assertIn("uber quote", with_block)


class Fixes(unittest.TestCase):
    """Offline checks for the park-and-ride review fixes (PR3, PR4, PR5, PR6/K1, PR11, UX6, K12)."""

    @classmethod
    def setUpClass(cls):
        cls.gtha = next(r for r in th.load_regions() if r["id"] == "gtha")

    # ---- PR5 / K12: UP Express fares by station pair
    def test_up_express_pair_table_is_dated_and_sourced(self):
        up = self.gtha["flat_fares"]["UP Express"]
        self.assertEqual(up["verified"], "2026-10-10")
        self.assertIn("upexpress.com", up["source"])
        self.assertEqual(len(up["pairs"]), 10)
        self.assertEqual(up["pairs"]["Union|Weston"], {"card": 5.02, "cash": 5.65, "contactless": 5.65})
        self.assertEqual(up["pairs"]["Union|Pearson"]["cash"], 12.35)
        go = next(a for a in self.gtha["park_and_ride"]["agencies"] if a["agency"] == "GO Transit")
        self.assertIn("Pearson", [p["name"] for p in go["paid_only"]])   # airport parking is never free
        self.assertIn("Bloor", go["no_free_parking"])
        self.assertIn("Mount Dennis", go["no_free_parking"])

    def test_fare_station_names(self):
        self.assertEqual(th.fare_station("Weston GO/UP"), "weston")
        self.assertEqual(th.fare_station("UP Express Union Station"), "union")
        self.assertEqual(th.fare_station("UP Express Pearson Airport"), "pearson")
        self.assertEqual(th.fare_station("Mount Dennis GO/UP"), "mount dennis")
        self.assertEqual(th.fare_station("Bloor GO/UP"), "bloor")

    def up_fare(self, a, b, payment="card"):
        class Net:
            fares, fare_urls, stop_zone = {}, {}, [""]
        leg = [{"type": "ride", "feed": "upx", "agency": "UP Express", "route": "UP", "route_id": "UP",
                "from": a, "to": b, "from_idx": 0, "to_idx": 0, "depart": 30000}]
        return th.journey_fare(Net, leg, self.gtha["flat_fares"], payment)

    def test_up_express_fare_by_pair_either_direction(self):
        self.assertEqual(self.up_fare("Weston GO/UP", "UP Express Union Station")[0], 5.02)          # PRESTO
        self.assertEqual(self.up_fare("UP Express Union Station", "Weston GO/UP", "cash")[0], 5.65)  # ticket
        self.assertEqual(self.up_fare("Weston GO/UP", "UP Express Union Station", "contactless")[0], 5.65)
        self.assertEqual(self.up_fare("Bloor GO/UP", "UP Express Union Station", "cash")[0], 5.30)   # not GO 4.40
        self.assertEqual(self.up_fare("Mount Dennis GO/UP", "UP Express Union Station", "cash")[0], 5.50)
        self.assertEqual(self.up_fare("UP Express Pearson Airport", "UP Express Union Station", "cash")[0], 12.35)
        total, cur, parts, complete = self.up_fare("Weston GO/UP", "UP Express Union Station")
        self.assertEqual((cur, complete, parts[0]["currency"]), ("CAD", True, "CAD"))

    def test_unlisted_up_pair_stays_unknown(self):
        total, _cur, parts, complete = self.up_fare("Weston GO/UP", "Somewhere Else")
        self.assertIsNone(total)
        self.assertFalse(complete)
        self.assertIsNone(parts[0]["fare"])

    # ---- PR4: airport garages are not park and ride
    def test_airport_garages_left_out(self):
        els = [lot(21, 43.683, -79.612, fee="yes", name="Terminal 1 Parking Garage", parking="multi-storey"),
               lot(22, 43.689, -79.616, fee="yes", name="Value Park Lot", operator="GTAA"),
               lot(23, 43.684, -79.596, fee="yes", name="Park 'N Fly Self Park A", park_ride="yes", access="yes"),
               lot(24, 43.700, -79.513, fee="no", name="Weston GO parking")]
        self.assertEqual([x["id"] for x in th.osm_lots(els)], ["way/24"])

    # ---- UX6: car mode plans the drive on the planning-allowance factor with the drive_plan spare
    def test_plan_traffic_factor_rows(self):
        with open(os.path.join(th.DRIVING_DIR, "traffic.json"), encoding="utf-8") as fh:
            plan = {r["name"]: r["plan"] for r in json.load(fh)["congested"]}
        self.assertEqual(th.plan_traffic_factor(th.dt.datetime(2026, 10, 14, 8, 0)),
                         (plan["weekday peak"], "weekday peak"))
        self.assertEqual(th.plan_traffic_factor(th.dt.datetime(2026, 10, 14, 6, 30))[0], plan["weekday shoulder"])
        self.assertEqual(th.plan_traffic_factor(th.dt.datetime(2026, 10, 17, 8, 0))[0], plan["daytime"])  # Sat
        self.assertEqual(th.plan_traffic_factor(th.dt.datetime(2026, 10, 12, 8, 0), {"2026-10-12"})[0],
                         plan["daytime"])                                                        # holiday
        self.assertEqual(th.plan_traffic_factor(th.dt.datetime(2026, 10, 14, 22, 0))[0], plan["night"])
        self.assertEqual(th.plan_traffic_factor(th.dt.datetime(2026, 10, 14, 3, 0))[0], plan["night"])
        self.assertEqual(th.car_plan_defaults()["slack_min"], 5.0)

    def test_plan_traffic_factor_without_traffic_json(self):
        with mock.patch.object(th, "DRIVING_DIR", tempfile.mkdtemp()):
            self.assertEqual(th.plan_traffic_factor(th.dt.datetime(2026, 10, 14, 8, 0))[0], 2.1)
            self.assertEqual(th.car_plan_defaults()["slack_min"], 5.0)

    def test_safety_default_is_unset_on_the_parser(self):
        self.assertIsNone(th.build_parser().parse_args(["--origin", "1,2", "--dest", "3,4"]).safety_min)


class FixParking(HubParking):
    """PR4 (no-free and paid-only stations) and PR11 (a station's lot is not a bus-stop hub)."""

    def test_no_free_station_osm_lot_is_paid_not_free(self):
        kept, _ = th.hub_parking(self.net, self.clusters, [BLOCK], th.osm_lots(LOTS), 240, DAY, set())
        b = self.names(kept)["Transfer B"]                        # GO-style no_free_parking + an OSM fee=no lot
        self.assertIsNone(b["cost"])
        self.assertTrue(b["price_unknown"])
        self.assertFalse(b["free"])
        self.assertIn("paid parking, price unknown", b["text"])

    def test_paid_only_station_osm_lot_is_not_free(self):
        near_e = [lot(7, 43.6010, -79.8000, fee="no", name="E lot")]
        kept, _ = th.hub_parking(self.net, self.clusters, [BLOCK], th.osm_lots(near_e), 240, DAY, set())
        self.assertIsNone(self.names(kept)["Far E"]["cost"])
        # an OSM charge tag still prices it
        priced = [lot(8, 43.6010, -79.8000, fee="yes", charge="$5/day")]
        kept, _ = th.hub_parking(self.net, self.clusters, [BLOCK], th.osm_lots(priced), 240, DAY, set())
        self.assertEqual(self.names(kept)["Far E"]["cost"], 5.0)

    def test_station_lot_is_not_a_bus_stop_hub(self):
        # a park_ride lot 334 m from rail Hub A and 222 m from bus-only Hub D belongs to Hub A
        shared = [lot(9, 43.5030, -79.8000, park_ride="yes", fee="no", name="A GO lot")]
        kept, _ = th.hub_parking(self.net, self.clusters, [], th.osm_lots(shared), 240, DAY, set())
        got = self.names(kept)
        self.assertNotIn("Hub D", got)
        self.assertEqual(got["Hub A"]["basis"], "OSM parking near a station")
        # a lot only the bus stop reaches still makes it a hub
        kept, _ = th.hub_parking(self.net, self.clusters, [], th.osm_lots(LOTS[:1]), 240, DAY, set())
        self.assertIn("Hub D", self.names(kept))


class FixCarRanking(CarModePlan):
    """PR3 (rank on car + fare), PR4 (unknown hub parking is not $0), UX6 (plan factor, 5 min spare)."""

    def fares(self, r, l_):
        return {"Test Transit R": {"card": r, "currency": "CAD"}, "Test Transit L": {"card": l_, "currency": "CAD"}}

    def opts(self, region, extra):
        res = json.loads(self.run_plan(region, ["--access", "car", "--json", "--traffic-factor", "1.2",
                                                "--safety-min", "3"] + extra)[0])
        return res, [o["hub"] for o in res["pareto"]], {o["hub"]: o for o in res["pareto"] + res["others"]}

    def test_pareto_counts_the_fare(self):
        # Hub A: 0.1 km drive, $9 train, arrives 10:35. Hub D: 1 km drive, $2 bus, arrives 10:40.
        # On car cost alone D is dominated by A; on car + fare D is the cheap option and joins the front.
        res, pareto, hubs = self.opts(dict(REGION, flat_fares=self.fares(9.0, 2.0)), [])
        self.assertEqual(pareto, ["Hub A", "Hub D"])
        self.assertEqual(hubs["Hub D"]["transit_fare"], 2.0)
        # with equal fares A wins on both counts again
        res, pareto, _ = self.opts(dict(REGION, flat_fares=self.fares(2.0, 2.0)), [])
        self.assertEqual(pareto, ["Hub A"])

    def test_unknown_hub_parking_is_not_free(self):
        block = dict(BLOCK, agencies=[dict(BLOCK["agencies"][0], paid_only=[{"name": "Hub A", "text": "paid only"}])])
        region = dict(REGION, park_and_ride=block, flat_fares=self.fares(3.0, 3.0))
        res, pareto, hubs = self.opts(region, [])
        a = hubs["Hub A"]
        self.assertTrue(a["parking_unknown"])
        self.assertEqual(a["car_cost"], a["drive_cost"])                 # known part only, no made-up price
        self.assertEqual(pareto, ["Hub A", "Hub D"])                     # D (priced) is no longer dominated
        out = self.run_plan(region, ["--access", "car", "--traffic-factor", "1.2", "--safety-min", "3"])[0]
        self.assertIn(" + hub parking price unknown", out)
        self.assertIn("(car + fare) + hub parking price unknown", out)

    def test_car_mode_uses_plan_factor_and_5_min_spare(self):
        res = json.loads(self.run_plan(REGION, ["--access", "car", "--json"])[0])
        self.assertEqual(res["safety_min"], 5.0)
        self.assertEqual(res["car"]["traffic_factor"], 1.7)              # Thursday 10:00: weekday shoulder plan
        self.assertIn("weekday shoulder", res["car"]["traffic_basis"])
        _km, sec = th.heuristic_ride(th.haversine_m(43.4990, -79.8000, 43.5500, -79.8000))
        self.assertAlmostEqual(res["ride_all"]["ride_s"], sec * 1.7)
        res = json.loads(self.run_plan(REGION, ["--access", "car", "--json", "--traffic-factor", "1.3"])[0])
        self.assertEqual((res["car"]["traffic_factor"], res["car"]["traffic_basis"]), (1.3, "--traffic-factor"))
        out = self.run_plan(REGION, ["--access", "car"])[0]
        self.assertIn("DRIVE TIMES  free-flow x1.70 (planning allowance, weekday shoulder), plus 5 min spare",
                      out)
        ride = json.loads(self.run_plan(REGION, ["--json"])[0])
        self.assertEqual(ride["safety_min"], 3.0)
        self.assertNotIn("car", ride)


class FixSameTrain(unittest.TestCase):
    """K1 / PR6: in car mode a hub on the same train as transit only (the car replaces the local bus) is kept;
    --min-gain-min applies to paid rides only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": self.tmp})
        self.env.start()
        self.path = os.path.join(self.tmp, "f.zip")
        stops = STOPS + [("O", "Origin Stop", 43.4900, -79.8000)]
        trips = [("O", "WK", "o1", "A"), ("R", "WK", "r1", "C"), ("L", "WK", "l1", "C")]
        st = [("o1", "10:05:00", "10:05:00", "O", 1, 0, 0), ("o1", "10:12:00", "10:12:00", "A", 2, 0, 0),
              ("r1", "10:20:00", "10:20:00", "A", 1, 0, 0), ("r1", "10:35:00", "10:35:00", "C", 2, 0, 0),
              ("l1", "10:25:00", "10:25:00", "D", 1, 0, 0), ("l1", "10:50:00", "10:50:00", "C", 2, 0, 0)]
        make_feed(self.path, stops, trips, st)
        add_files(self.path, {"routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                                            "L,T,L,Route L,3\nO,T,O,Route O,3\nR,T,R,Route R,2\n"})

    def tearDown(self):
        self.env.stop()

    def run_plan(self, when, extra, region=REGION):
        args = ["--origin", "43.4900,-79.8000", "--dest", "43.5500,-79.8000", "--no-discover", "--feed",
                "test=" + self.path, "--routing", "estimate", "--radius-km", "4", "--origin-walk-m", "200",
                "--json"] + when + extra
        buf = io.StringIO()
        with mock.patch.object(th, "load_regions", return_value=[region]), \
                mock.patch.object(th, "check_network", side_effect=lambda hosts, timeout=6: {h: "ok" for h in hosts}), \
                mock.patch.object(th, "http_get", return_value=overpass_reply(LOTS[0])), redirect_stdout(buf):
            th.main(args)
        res = json.loads(buf.getvalue())
        return res, [o["hub"] for o in res["pareto"] + res["others"]]

    def test_depart_same_train_is_kept_in_car_mode(self):
        res, hubs = self.run_plan(["--depart", "2026-10-15 10:00"], ["--access", "car"])
        to = res["transit_only"]
        self.assertEqual(th.fmt_t(to["arrive"]), "10:35")
        self.assertIn("Hub A", hubs)                                     # same 10:20 train, leaves home later
        a = next(o for o in res["pareto"] + res["others"] if o["hub"] == "Hub A")
        self.assertEqual(a["arrive"], to["arrive"])
        self.assertGreater(a["book_by"], to["leave_by"])
        self.assertNotIn("Hub D", hubs)                                  # later bus, no cheaper: transit only wins
        self.assertTrue(any("transit only is as fast and costs no more" in w for w in res["warnings"]))
        self.assertFalse(any("--min-gain-min" in w for w in res["warnings"]))

    def test_arrive_by_small_gain_is_kept_in_car_mode(self):
        res, hubs = self.run_plan(["--arrive-by", "2026-10-15 10:45"], ["--access", "car"])
        a = next(o for o in res["pareto"] + res["others"] if o["hub"] == "Hub A")
        gain = (a["book_by"] - res["transit_only"]["leave_by"]) / 60.0
        self.assertTrue(0 < gain < 10, gain)                             # under --min-gain-min, still listed

    def test_slower_hub_shows_only_when_clearly_cheaper(self):
        # transit only: $8 (one feed, so the first route's flat fare covers bus and train), arrives 10:35.
        # Hub D arrives 10:50 by a bus of its own.
        def region(l_fare):
            return dict(REGION, flat_fares={"Test Transit O": {"card": 8.0}, "Test Transit R": {"card": 5.0},
                                            "Test Transit L": {"card": l_fare}})
        res, hubs = self.run_plan(["--depart", "2026-10-15 10:00"], ["--access", "car"], region(1.0))
        self.assertEqual(res["transit_only"]["transit_fare"], 8.0)
        self.assertIn("Hub D", hubs)                                     # about $1.40 in all vs $8: a real saving
        res, hubs = self.run_plan(["--depart", "2026-10-15 10:00"], ["--access", "car"], region(7.5))
        self.assertNotIn("Hub D", hubs)                                  # about $7.90 vs $8: within a $1 bucket

    def test_ride_mode_still_applies_min_gain(self):
        res, hubs = self.run_plan(["--depart", "2026-10-15 10:00"], ["--min-saving", "0"])
        self.assertNotIn("Hub A", hubs)
        self.assertTrue(any("--min-gain-min" in w for w in res["warnings"]))


if __name__ == "__main__":
    unittest.main()
