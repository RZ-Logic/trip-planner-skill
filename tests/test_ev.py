"""Offline tests for ev_charging.py (car presets, energy and charge-time model, price parsing, greedy stops).

All network calls are mocked through drive_common.fetch. Run: python -m unittest discover -s tests -v
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
import re
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import ev_charging as ev  # noqa: E402

DEPART = dt.datetime(2026, 10, 17, 9, 0)
LAT = 44.0
KM_PER_DEG = 111.32 * 0.7193398   # cos(44 deg)


def line(km, n=200):
    """A straight west-to-east route of `km` along latitude 44."""
    return [(LAT, -80.0 + (km / KM_PER_DEG) * i / (n - 1)) for i in range(n)]


def at_km(km, off_deg=0.0):
    return LAT + off_deg, -80.0 + km / KM_PER_DEG


def rec(sid, name, km, units, network="FLO", pricing=None, access="24 hours daily", off_deg=0.001):
    lat, lon = at_km(km, off_deg)
    cu = [{"network": network, "charging_level": "dc_fast", "port_count": n,
           "connectors": {code: {"power_kw": kw, "port_count": n}}} for code, (kw, n) in units.items()]
    return {"id": sid, "station_name": name, "latitude": lat, "longitude": lon, "street_address": "1 Test Rd",
            "city": "Testville", "status_code": "E", "access_code": "public", "ev_network": network,
            "ev_connector_types": sorted(units), "ev_dc_fast_num": sum(n for _, n in units.values()),
            "ev_level2_evse_num": None, "ev_pricing": pricing, "access_days_time": access, "ev_charging_units": cu}


def l2rec(sid, name, lat, lon, ports=2, pricing="Free"):
    return {"id": sid, "station_name": name, "latitude": lat, "longitude": lon, "street_address": "", "city": "",
            "status_code": "E", "ev_network": "Non-Networked", "ev_connector_types": ["J1772"],
            "ev_dc_fast_num": None, "ev_level2_evse_num": ports, "ev_pricing": pricing, "access_days_time": "",
            "ev_charging_units": [{"network": "Non-Networked", "charging_level": "level2", "port_count": ports,
                                   "connectors": {"J1772": {"power_kw": 7.2, "port_count": ports}}}]}


def reply(stations):
    return json.dumps({"total_results": len(stations), "fuel_stations": stations}).encode()


class FakeNet:
    """Routes mocked fetch calls by URL; records each call."""

    def __init__(self, route=None, near=None, overpass=None, fail_nlr=None, fail_overpass=None):
        self.route, self.near, self.overpass = route or [], near or [], overpass
        self.fail_nlr, self.fail_overpass = fail_nlr, fail_overpass
        self.calls = []

    def __call__(self, url, data=None, headers=None, **kw):
        self.calls.append({"url": url, "data": data, "headers": headers or {}})
        if "developer.nlr.gov" in url:
            if self.fail_nlr:
                raise urllib.error.HTTPError(url, self.fail_nlr, "fail", {}, None)
            return reply(self.route if "nearby-route" in url else self.near)
        if "overpass" in url:
            if self.fail_overpass:
                raise urllib.error.URLError("down")
            return json.dumps({"elements": self.overpass or []}).encode()
        raise AssertionError("unexpected URL " + url)


def plan(route_km, car, soc, temp, net, **opts):
    w = []
    o = {"hwy_share": 0.85}
    o.update(opts)
    with mock.patch.object(dc, "fetch", side_effect=net), mock.patch.object(ev.time, "sleep"), \
            mock.patch.object(ev, "log"):
        r = ev.plan_charging(line(route_km), route_km, DEPART, car, soc, temp, w, o)
    return r, w


class CarProfileTest(unittest.TestCase):
    def test_every_preset_loads(self):
        for key in dc.load_data("ev")["cars"]:
            c = ev.car_profile(key)
            self.assertGreater(c["battery_kwh"], 20)
            self.assertGreater(c["max_dc_kw"], 20)
            self.assertTrue(0.3 < c["f_10_80"] <= 0.9)
            self.assertTrue(c["connectors"])
            self.assertIn("http", c["source"])
        self.assertEqual(ev.car_profile()["key"], "generic")

    def test_aliases_and_unknown(self):
        self.assertEqual(ev.car_profile("Model Y")["key"], "model-y")
        self.assertEqual(ev.car_profile("IONIQ 5")["key"], "ioniq5")
        self.assertEqual(ev.car_profile("mach e")["key"], "mach-e")
        with self.assertRaises(SystemExit) as cm:
            ev.car_profile("cybertruck")
        self.assertIn("equinox", str(cm.exception))

    def test_overrides(self):
        c = ev.car_profile("equinox", battery_kwh=70, max_dc_kw=100, kwh_per_100km=25)
        self.assertEqual((c["battery_kwh"], c["max_dc_kw"], c["kwh_100km_hwy"]), (70.0, 100.0, 25.0))
        self.assertAlmostEqual(c["kwh_100km_city"], round(25 * 17.9 / 21.0, 1))
        self.assertIn("your figures", c["name"])

    def test_connectors_and_adapter(self):
        self.assertEqual(ev.car_profile("equinox")["connectors"], {"J1772COMBO"})
        self.assertEqual(ev.car_profile("equinox", nacs_adapter=True)["connectors"], {"J1772COMBO", "TESLA"})
        t = ev.car_profile("model-3")
        self.assertTrue(t["tesla"])
        self.assertEqual(t["connectors"], {"TESLA"})
        self.assertEqual(ev.car_profile("leaf", nacs_adapter=True)["connectors"], {"TESLA", "J1772COMBO"})
        self.assertEqual(ev.car_profile("leaf-plus", nacs_adapter=True)["connectors"], {"CHADEMO"})


class ModelTest(unittest.TestCase):
    def test_factors(self):
        self.assertAlmostEqual(ev.temp_factor(20), 1.0)
        self.assertAlmostEqual(ev.temp_factor(10), 1.15)
        self.assertAlmostEqual(ev.temp_factor(-7), 1.43)
        self.assertAlmostEqual(ev.temp_factor(-15), 1.6)
        self.assertAlmostEqual(ev.speed_factor(110), 1.10)
        self.assertAlmostEqual(ev.speed_factor(100), 1.0)

    def test_consumption_blend(self):
        c = ev.car_profile("equinox")
        want = (0.85 * 21.0 * 1.05 + 0.15 * 17.9) * 1.15
        self.assertAlmostEqual(ev.consumption(c, 10, 105, 0.85), want, places=6)

    def test_charge_minutes(self):
        c = ev.car_profile("ioniq5")
        # 10 to 80% on a charger faster than the car: evkx says about 19 min, plus 5 min plug-in
        self.assertAlmostEqual(ev.charge_minutes(c, 0.7 * 80, 350), 0.7 * 80 / (260 * 0.675) * 60 + 5, places=6)
        # a 50 kW charger limits the session: f = 0.9
        self.assertAlmostEqual(ev.charge_minutes(c, 30, 50), 30 / (50 * 0.9) * 60 + 5, places=6)
        self.assertAlmostEqual(ev.charge_minutes(c, 30, 50, -5) - 5, (30 / 45.0 * 60) * 1.3, places=6)
        self.assertEqual(ev.charge_minutes(c, 0, 50), 0.0)

    def test_month_normal(self):
        self.assertEqual(ev.month_normal(1), -5.0)
        self.assertEqual(ev.month_normal(7), 22.1)


class PriceTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ev.parse_price("$0.48 per kWh")["rate"], 0.48)
        self.assertEqual(ev.parse_price("$0.22 per kWh for L2; $0.56 per kWh for DCFC")["rate"], 0.56)
        self.assertEqual(ev.parse_price("$0.0467 per minute for L2 ports; $0.56 per kWh for DC Fast ports")["rate"], 0.56)
        self.assertEqual(ev.parse_price("Free")["model"], "free")
        self.assertIsNone(ev.parse_price("See app"))
        p = ev.parse_price("$1.00 connection fee; price per minute based on vehicle demand; $0.32 per min at 50 kW; "
                           "$0.38 per min at 100 kW; $0.45 per min at 150 kW")
        self.assertEqual(p["model"], "per_minute")
        self.assertAlmostEqual(ev._price_cost(p, 30, 20, 100), 1.0 + 0.38 * 20)
        p = ev.parse_price("Level 2: $5 for first hour, $12 per hour after; DC Fast: $5 per hour for first hour, "
                           "$24 per hour after")
        self.assertAlmostEqual(ev._price_cost(p, 30, 90, 50), 5 + 24 * 0.5)

    def test_network_estimates(self):
        car = ev.car_profile("equinox")
        st = {"name": "ONroute Port Hope", "network_code": "IVY", "pricing": ""}
        cost, text, src = ev.stop_price(st, car, 20.0, 20.0, 100)
        self.assertEqual((cost, src), (13.8, "network estimate"))
        self.assertIn("check the app", text)
        st = {"name": "PETRO-CANADA 1", "network_code": "ChargePoint Network", "pricing": ""}
        self.assertAlmostEqual(ev.stop_price(st, car, 20.0, 30.0, 100)[0], 9.0)
        tesla = {"name": "X - Tesla Supercharger", "network_code": "Tesla", "pricing": ""}
        self.assertAlmostEqual(ev.stop_price(tesla, ev.car_profile("model-y"), 10, 10, 250)[0], 5.6)
        self.assertAlmostEqual(ev.stop_price(tesla, car, 10, 10, 250)[0], 7.2)
        self.assertEqual(ev.stop_price(dict(tesla, pricing="$0.67 per kWh"), car, 10, 10, 250)[2], "station")


class PlanTest(unittest.TestCase):
    def corridor(self):
        return [rec(1, "Good 150", 150, {"J1772COMBO": (150.0, 4)}),
                rec(2, "Slow 160", 160, {"J1772COMBO": (50.0, 1)}),
                rec(3, "Chademo 200", 200, {"CHADEMO": (50.0, 2)}),
                rec(4, "Good 300", 300, {"J1772COMBO": (180.0, 4)}, network="IVY"),
                rec(5, "Tesla only 320", 320, {"TESLA": (250.0, 8)}, network="Tesla",
                    access="24 hours daily. Open to Teslas only.")]

    def test_no_stop_makes_no_call(self):
        net = FakeNet()
        r, w = plan(100, ev.car_profile("ioniq5"), 0.8, 15, net)
        self.assertFalse(r["needed"])
        self.assertEqual(r["stops"], [])
        self.assertEqual(net.calls, [])
        self.assertGreater(r["arrive_soc"], 0.5)
        self.assertAlmostEqual(r["home_energy_cost"], r["kwh_used"] * 0.14, delta=0.02)

    def test_greedy_stops(self):
        car = ev.car_profile("equinox")
        net = FakeNet(route=self.corridor())
        r, w = plan(420, car, 0.8, 10, net)
        self.assertTrue(r["needed"] and r["feasible"])
        names = [s["name"] for s in r["stops"]]
        self.assertEqual(names, ["Good 150", "Good 300"])
        for s in r["stops"]:
            self.assertLessEqual(s["depart_soc"], 0.80 + 1e-9)
            self.assertGreaterEqual(s["arrive_soc"], 0.15 - 1e-9)
            self.assertEqual(s["connectors"], ["J1772COMBO"])
        # last stop charges only to the destination plus reserve and margin
        self.assertLess(r["stops"][-1]["depart_soc"], 0.8)
        self.assertAlmostEqual(r["arrive_soc"], 0.20, places=2)
        self.assertEqual(r["stops"][-1]["network"], "Ivy")
        self.assertGreater(r["extra_s"], 0)
        self.assertAlmostEqual(r["public_cost"], round(sum(s["cost"] for s in r["stops"]), 2))
        # key goes in a header, never in the URL or the posted body
        c = net.calls[0]
        self.assertNotIn("DEMO_KEY", c["url"])
        self.assertEqual(c["headers"].get("X-Api-Key"), "DEMO_KEY")
        self.assertIn("country", c["data"])
        self.assertIn("LINESTRING(", c["data"]["route"])
        self.assertTrue(c["data"]["route"].startswith("LINESTRING(-80.000 44.000,"))   # 3 decimals: no exact start

    def test_tesla_rules(self):
        st = self.corridor()[:3] + [rec(6, "Partner 300", 300, {"TESLA": (250.0, 8)}, network="Tesla",
                                        access="24 hours daily. NACS partner enabled.")]
        r, _ = plan(420, ev.car_profile("equinox"), 0.8, 10, FakeNet(route=st))
        # the partner site needs an adapter; the 260 km after Good 150 is bridged by charging above 80% there
        self.assertNotIn("Partner 300", [s["name"] for s in r["stops"]])
        self.assertGreater(r["stops"][0]["depart_soc"], 0.8)
        self.assertTrue(any("charge limit" in n for n in r["notes"]))
        r, _ = plan(420, ev.car_profile("equinox", nacs_adapter=True), 0.8, 10, FakeNet(route=st))
        self.assertTrue(r["feasible"])
        self.assertEqual(r["stops"][-1]["name"], "Partner 300")
        self.assertEqual(r["stops"][-1]["network"], "Tesla Supercharger")
        only = [rec(5, "Tesla only 200", 200, {"TESLA": (250.0, 8)}, network="Tesla",
                    access="24 hours daily. Open to Teslas only.")]
        r, _ = plan(420, ev.car_profile("leaf", nacs_adapter=True), 0.8, 10, FakeNet(route=only))
        self.assertFalse(r["feasible"])
        r, _ = plan(420, ev.car_profile("model-y"), 0.8, 10, FakeNet(route=only))
        self.assertEqual([s["name"] for s in r["stops"]], ["Tesla only 200"])

    def test_cold_reserve_and_more_stops(self):
        car = ev.car_profile("equinox")
        warm, _ = plan(420, car, 0.8, 10, FakeNet(route=self.corridor()))
        cold, w = plan(420, car, 0.8, -10, FakeNet(route=self.corridor()))
        self.assertEqual(cold["reserve"], 0.20)
        self.assertGreater(cold["kwh_100km"], warm["kwh_100km"] * 1.25)
        self.assertGreater(cold["extra_s"], warm["extra_s"])
        self.assertTrue(any("precondition" in n for n in cold["notes"]))

    def test_month_normal_when_no_temp(self):
        r, _ = plan(50, ev.car_profile("niro"), 0.9, None, FakeNet())
        self.assertEqual(r["temp_c"], 10.0)   # October normal at Pearson
        self.assertIn("month normal", r["notes"][0])

    def test_return_trip_destination_chargers(self):
        car = ev.car_profile("ioniq5")
        dest = at_km(100)
        near = [l2rec(10, "Motel L2", dest[0] + 0.001, dest[1]), l2rec(11, "Motel L2 post 2", dest[0] + 0.0012, dest[1]),
                rec(12, "Town DC", 101, {"J1772COMBO": (100.0, 2)}, network="JULE", pricing="$0.48 per kWh")]
        net = FakeNet(near=near)
        r, w = plan(100, car, 0.6, 10, net, return_trip=True, dest=dest)
        self.assertFalse(r["needed"])
        self.assertLess(r["arrive_soc"], r["return_soc_needed"])
        self.assertEqual([x["name"] for x in r["dest_chargers"]], ["Motel L2", "Town DC"])
        self.assertEqual(r["dest_chargers"][0]["ports"], 4)   # two posts of one site merged
        self.assertEqual(r["dest_chargers"][1]["price_text"], "listed $0.48/kWh")
        self.assertEqual(r["dest_chargers"][0]["price_text"], "listed free")
        # EV-7: the DC time is at the DC site near the destination (100 kW), named, not at the car's 260 kW peak
        note = [n for n in r["notes"] if n.startswith("Return trip")][0]
        self.assertIn("on the 100 kW DC at Town DC", note)
        kwh = (r["return_soc_needed"] - r["arrive_soc"]) * 80
        mins = int(re.search(r"or (\d+) min on the 100 kW DC", note).group(1))
        self.assertAlmostEqual(mins, ev.charge_minutes(car, kwh, 100, 10), delta=1.0)
        self.assertGreater(mins, ev.charge_minutes(car, kwh, 260, 10) + 3)
        # Level 2 at the site's 7.2 kW with 90% AC efficiency
        hours = float(re.search(r"about ([\d.]+) h on a 7.2 kW Level 2", note).group(1))
        self.assertAlmostEqual(hours, kwh / (7.2 * 0.9), delta=0.1)
        self.assertEqual(len(net.calls), 1)
        self.assertIn("nearest.json", net.calls[0]["url"])
        # enough charge: no destination call
        net = FakeNet(near=near)
        r, _ = plan(100, car, 0.95, 15, net, return_trip=True, dest=dest)
        self.assertGreaterEqual(r["arrive_soc"], r["return_soc_needed"])
        self.assertEqual(net.calls, [])

    def test_long_return_points_to_last_charger(self):
        st = [rec(1, "Owen Sound DC", 170, {"J1772COMBO": (100.0, 5)}, network="JULE", pricing="$0.48 per kWh")]
        r, _ = plan(273, ev.car_profile("niro"), 0.9, 10, FakeNet(route=st, near=[]), return_trip=True)
        self.assertEqual([s["name"] for s in r["stops"]], ["Owen Sound DC"])
        self.assertGreater(r["return_soc_one_charge"], 0.8)
        self.assertNotIn("return_soc_needed", r)   # the CLI would print "leave the destination with 116%"
        self.assertLessEqual(r["return_soc_to_charger"], 0.8)
        self.assertEqual([s["name"] for s in r["return_plan"]["stops"]], ["Owen Sound DC"])
        self.assertTrue(r["return_plan"]["charge_at_destination"])
        self.assertTrue(any("Owen Sound DC at km 103" in n and "charge at the destination to at least" in n
                            for n in r["notes"]))

    def test_nlr_limit_falls_back_to_overpass(self):
        lat, lon = at_km(200)
        osm = [{"type": "node", "id": 7, "lat": lat, "lon": lon,
                "tags": {"amenity": "charging_station", "name": "ONroute Test", "brand": "Ivy", "capacity": "4",
                         "socket:type1_combo": "4", "socket:type1_combo:output": "100 kW"}},
               {"type": "node", "id": 8, "lat": lat, "lon": lon + 0.01,
                "tags": {"amenity": "charging_station", "name": "Library L2", "socket:type1": "2"}}]
        net = FakeNet(fail_nlr=429, overpass=osm)
        r, w = plan(350, ev.car_profile("equinox"), 0.8, 15, net)
        self.assertTrue(any("NLR_API_KEY" in x for x in w))
        self.assertTrue(any("OpenStreetMap" in x for x in w))
        self.assertEqual([s["name"] for s in r["stops"]], ["ONroute Test"])
        self.assertEqual(r["stops"][0]["max_kw"], 100.0)
        self.assertIn("Overpass", r["source"])

    def test_all_sources_down_never_crashes(self):
        net = FakeNet(fail_nlr=500, fail_overpass=True)
        r, w = plan(420, ev.car_profile("equinox"), 0.8, 10, net, return_trip=True)
        # no charger list at all: the stops are an estimate (INTEGRATE), not a false "no charger exists"
        self.assertTrue(r["feasible"])
        self.assertTrue(r["needed"])
        self.assertTrue(r["estimated"])
        self.assertTrue(r["stops"] and all(s["source"] == "estimate" for s in r["stops"]))
        self.assertTrue(r["stops"][0]["name"].startswith("a fast charger near km"))
        self.assertGreaterEqual(r["arrive_soc"], 0.15)
        self.assertTrue(any("HTTP 500" in x for x in w))
        self.assertFalse(any("No compatible public DC" in x or "No reachable" in x for x in w))
        self.assertTrue(any("unavailable" in n for n in r["notes"]))
        self.assertEqual(r["dest_chargers"], [])

    def test_text_has_no_dashes(self):
        r, w = plan(420, ev.car_profile("equinox"), 0.8, -10, FakeNet(route=self.corridor()), return_trip=True)
        blob = json.dumps(r, ensure_ascii=False) + " ".join(w)
        self.assertNotIn("\u2014", blob)
        self.assertNotIn("\u2013", blob)


# --------------------------------------------------------------------------- review fixes (EV-1 to EV-15, K10, UX13)

def nst(km, kw=150.0, ports=4, code="J1772COMBO", net="IVY", name=None, address="", pricing="", off_deg=0.0005):
    """A normalized station (as norm_nlr returns) at `km` along line(); negative off_deg is south (right of
    eastbound travel)."""
    lat, lon = at_km(km, off_deg)
    return {"id": "t%s%s" % (km, name), "name": name or "S%d" % km, "network_code": net, "lat": lat, "lon": lon,
            "address": address, "units": {code: (kw, ports)}, "pricing": pricing, "access_text": "",
            "dc_num": ports, "l2_num": 0, "status": "E", "source": "NLR"}


def plan_st(stations, route_km, car, soc, temp=15.0, poly=None, near=None, **opts):
    """plan_charging with a given station list; the destination search returns `near` (None: lookup failed)."""
    w = []
    o = {"hwy_share": 0.85, "stations": stations, "stations_source": "NLR test"}
    o.update(opts)
    calls = []

    def fake_near(lat, lon, warnings, opts=None):
        calls.append((lat, lon))
        return [dict(x) for x in near] if near is not None else None
    with mock.patch.object(ev, "stations_near", side_effect=fake_near), mock.patch.object(ev, "log"), \
            mock.patch.object(dc, "fetch", side_effect=AssertionError("no network in this test")):
        r = ev.plan_charging(poly or line(route_km), route_km, DEPART, car, soc, temp, w, o)
    r["_near_calls"] = calls
    return r, w


def pcts(text):
    return [int(x) for x in re.findall(r"(\d+)%", text)]


class DirectionTest(unittest.TestCase):
    """EV-1: a service centre on the other carriageway of a divided highway is not a stop."""

    def cands(self, stations, eastbound=True, car=None):
        poly = line(300) if eastbound else list(reversed(line(300)))
        return ev._candidates(stations, poly, 300, car or ev.car_profile("ioniq5"), ev._ev()["model"])

    def test_side_of_divided_highway(self):
        right = nst(100, name="ONroute Port Hope", address="845 Hwy 401 Eastbound, Port Hope", off_deg=-0.001)
        left = nst(110, name="ONroute Trenton North", address="17278 ON-401 Westbound, Quinte West", off_deg=0.001)
        left_plain = nst(120, name="ONroute Napanee", address="1550 Simmons Rd, Odessa", off_deg=0.001)
        town = nst(130, name="Canadian Tire", address="2360 Parkedale Ave", off_deg=0.001, net="Electrify Canada")
        names = [c["st"]["name"] for c in self.cands([right, left, left_plain, town])]
        self.assertEqual(names, ["ONroute Port Hope", "Canadian Tire"])
        # driving the other way the twins swap; an in-town station on either side stays
        names = [c["st"]["name"] for c in self.cands([right, left, left_plain, town], eastbound=False)]
        self.assertEqual(sorted(names), ["Canadian Tire", "ONroute Napanee", "ONroute Trenton North"])

    def test_direction_word_beyond_side_range(self):
        # 'Westbound' in the record drops it for eastbound travel even when it lies 1 km off on the right
        far = nst(100, name="Gas bar", address="1 Hwy 401 Westbound", off_deg=-0.009)
        self.assertEqual(self.cands([far]), [])
        self.assertEqual(len(self.cands([far], eastbound=False)), 1)
        abbr = nst(100, name="Esso 401 WB", off_deg=-0.003)
        self.assertEqual(self.cands([abbr]), [])
        self.assertIsNone(ev.station_bound({"name": "Irving Moncton NB", "address": "1 Main St"}))
        self.assertEqual(ev.station_bound({"name": "x", "address": "Hwy 400 Southbound"}), 180.0)

    def test_plan_picks_own_carriageway(self):
        car = ev.car_profile("ioniq5")
        sts = [nst(150, name="ONroute A", address="Hwy 401 Westbound", off_deg=0.001),
               nst(140, name="ONroute B", address="Hwy 401 Eastbound", off_deg=-0.001)]
        r, _ = plan_st(sts, 300, car, 0.8)
        self.assertEqual([s["name"] for s in r["stops"]], ["ONroute B"])
        r, _ = plan_st(sts, 300, car, 0.8, poly=list(reversed(line(300))))
        self.assertEqual([s["name"] for s in r["stops"]], ["ONroute A"])


class ChargeModelTest(unittest.TestCase):
    """EV-6: a faster charger is never predicted slower."""

    def test_monotonic_in_charger_kw(self):
        for key in dc.load_data("ev")["cars"]:
            car = ev.car_profile(key)
            prev = None
            for kw in (25, 50, 100, 125, 150, 175, 200, 250, 350):
                t = ev.charge_minutes(car, 40.0, kw)
                if prev is not None:
                    self.assertLessEqual(t, prev + 1e-9, "%s at %d kW" % (key, kw))
                prev = t

    def test_low_f_car_on_mid_charger(self):
        y = ev.car_profile("model-y")
        # 150 kW x 0.9 = 135 kW available, the car averages 250 x 0.405 = 101 kW: the car limits
        self.assertAlmostEqual(ev.charge_minutes(y, 40, 150), 40 / (250 * 0.405) * 60 + 5, places=6)
        self.assertAlmostEqual(ev.charge_minutes(y, 40, 150), ev.charge_minutes(y, 40, 250), places=6)
        self.assertAlmostEqual(ev.charge_minutes(y, 40, 100), 40 / 90.0 * 60 + 5, places=6)
        self.assertIn("min(charger_kw * charger_limited_f", dc.load_data("ev")["model"]["charge_time"])

    def test_billed_minutes(self):
        self.assertAlmostEqual(ev.billed_minutes(12.8), 7.8)
        self.assertEqual(ev.billed_minutes(3.0), 0.0)


class PriceParseTest(unittest.TestCase):
    """EV-12: free prefixes, fee order, energy plus time, flat session prices."""

    def test_free_prefix_with_a_price(self):
        p = ev.parse_price("Free for the first hour, then $4.00 per hour")
        self.assertEqual((p["model"], p["rate"], p["first"]), ("per_hour", 4.0, 0.0))
        self.assertAlmostEqual(ev._price_cost(p, 20, 30, 50), 0.0)
        self.assertAlmostEqual(ev._price_cost(p, 40, 90, 50), 2.0)
        p = ev.parse_price("Free parking; $0.45 per kWh")
        self.assertEqual((p["model"], p["rate"]), ("per_kwh", 0.45))
        self.assertEqual(ev.parse_price("Free")["model"], "free")
        self.assertEqual(ev.parse_price("FREE charging")["model"], "free")

    def test_fee_after_and_time_plus_energy(self):
        self.assertEqual(ev.parse_price("$0.50 per kWh; session fee $2.00")["fee"], 2.0)
        self.assertEqual(ev.parse_price("$0.50 per kWh; $1.00 connection fee")["fee"], 1.0)
        p = ev.parse_price("$0.30 per kWh plus $0.10 per minute")
        self.assertAlmostEqual(ev._price_cost(p, 40, 40, 100), 16.0)
        # an idle fee is not a charging rate
        p = ev.parse_price("$0.69/kWh; idle fee $0.40/min after 10 min")
        self.assertNotIn("per_min", p)
        self.assertAlmostEqual(ev._price_cost(p, 40, 40, 100), 27.6)

    def test_flat(self):
        p = ev.parse_price("$25 flat rate")
        self.assertAlmostEqual(ev._price_cost(p, 40, 40, 100), 25.0)
        self.assertEqual(ev.price_short(p), "$25 per session")
        self.assertAlmostEqual(ev._price_cost(ev.parse_price("$10 per session"), 30, 30, 50), 10.0)


class PriceTextTest(unittest.TestCase):
    """K10 and EV-13: short price text with no parentheses; long network text once in the notes."""

    def test_short_texts(self):
        eq = ev.car_profile("equinox")
        cost, txt, src = ev.stop_price({"pricing": "", "network_code": "FLO", "name": "x"}, eq, 51, 42.6, 100)
        self.assertEqual(txt, "estimate; FLO sets prices per site, check the app")
        self.assertEqual(src, "network estimate")
        self.assertAlmostEqual(cost, 20.0 * 42.6 / 60, places=2)
        jule = ("$1.00 connection fee; Price per minute based on vehicle demand; $0.32 per min @ 50 kW; "
                "$0.38 per min @ 100 kW; $0.45 per min @ 150 kW")
        self.assertEqual(ev.stop_price({"pricing": jule, "network_code": "JULE", "name": "x"}, eq, 30, 20, 100)[1],
                         "listed $0.38/min at 100 kW + $1 fee")
        self.assertEqual(ev.stop_price({"pricing": "$0.48 per kWh", "network_code": "JULE", "name": "x"}, eq, 30, 20, 100)[1],
                         "listed $0.48/kWh")
        p = ev.parse_price("Level 2: $5 for first hour, $12 per hour after; DC Fast: $5 per hour for first hour, "
                           "$24 per hour after")
        self.assertEqual(ev.price_short(p), "$5 first hour, then $24/h")
        for n in dc.load_data("ev")["networks"]:
            st = {"pricing": "", "network_code": n["match"][0] if n["match"] else "", "name": ""}
            t = ev.stop_price(st, eq, 30, 30, 100)[1]
            self.assertNotIn("(", t)
            self.assertLessEqual(len(t), 75, t)
            self.assertNotIn("(", n["short"])

    def test_long_text_once_per_network(self):
        sts = [nst(150, net="FLO"), nst(300, net="FLO", kw=100.0)]
        r, _ = plan_st(sts, 420, ev.car_profile("equinox"), 0.8, temp=10)
        self.assertEqual(len(r["stops"]), 2)
        flo = [n for n in r["notes"] if n.startswith("FLO price estimate")]
        self.assertEqual(len(flo), 1)
        self.assertIn("$20/hour", flo[0])
        for s in r["stops"]:
            self.assertEqual(s["price_text"], "estimate; FLO sets prices per site, check the app")
            self.assertIsInstance(s["detour_min"], int)   # never '+0 min detour' from 0.4 min

    def test_l2_text(self):
        self.assertEqual(ev._l2_price_text("Free"), "listed free")
        self.assertEqual(ev._l2_price_text(""), "Level 2, price set by the host; check the app")
        long = ev._l2_price_text("$2.00 per hour for the first 4 hours (then $5.00 per hour), parking extra after 6 pm")
        self.assertNotIn("(", long)
        self.assertLessEqual(len(long), 70)


class TeslaAndPlugsTest(unittest.TestCase):
    def test_tesla_owner_rate_at_listed_supercharger(self):
        """EV-10: the NLR price at a Supercharger is the non-Tesla rate."""
        sc = {"pricing": "$0.72 per kWh", "network_code": "Tesla", "name": "X - Tesla Supercharger"}
        cost, txt, src = ev.stop_price(sc, ev.car_profile("model-y"), 40, 30, 250)
        self.assertEqual((cost, src), (22.4, "network estimate"))
        self.assertIn("Tesla about $0.56/kWh", txt)
        cost, txt, src = ev.stop_price(sc, ev.car_profile("equinox", nacs_adapter=True), 40, 30, 250)
        self.assertEqual((cost, txt, src), (28.8, "listed $0.72/kWh non-Tesla rate", "station"))

    def test_leaf_not_offered_tesla_destination(self):
        """EV-14: Tesla Destination (NACS AC) plugs only for Teslas."""
        dest = {"name": "Inn - Tesla Destination", "network_code": "Tesla", "units": {"TESLA_L2": (11.5, 2)},
                "pricing": "Free", "access_text": "", "status": "E"}
        self.assertIsNone(ev.compatible(dest, ev.car_profile("leaf"), dc_only=False))
        self.assertIsNotNone(ev.compatible(dest, ev.car_profile("model-y"), dc_only=False))

    def test_colocated_keeps_faster(self):
        """EV-9: a 50 kW post beside a 250 kW Supercharger does not hide the Supercharger."""
        a = nst(100, kw=50.0, ports=2, net="FLO", name="FLO plaza")
        b = nst(100, kw=250.0, ports=12, code="TESLA", net="Tesla", name="Plaza - Tesla Supercharger")
        b["lat"] += 0.0002
        b["pricing"] = "$0.72 per kWh"
        c = ev._candidates([a, b], line(300), 300, ev.car_profile("ioniq5", nacs_adapter=True), ev._ev()["model"])
        self.assertEqual([(x["st"]["name"], x["max_kw"]) for x in c], [("Plaza - Tesla Supercharger", 250.0)])
        c = ev._candidates([a, b], line(300), 300, ev.car_profile("ioniq5"), ev._ev()["model"])
        self.assertEqual([x["st"]["name"] for x in c], ["FLO plaza"])

    def test_per_minute_bills_without_plug_in(self):
        """EV-15: Petro-Canada per-minute price bills the session, not the 5 min plug-in allowance."""
        r, _ = plan_st([nst(200, net="PETROCAN", name="PETRO-CANADA 1", kw=100.0)], 330, ev.car_profile("equinox"), 0.8)
        s = r["stops"][0]
        self.assertAlmostEqual(s["cost"], round(0.30 * (s["charge_min"] - 5), 2), delta=0.02)


class EdgePlanTest(unittest.TestCase):
    def test_estimate_path_low_start(self):
        """EV-4: every source down and a start at 19%: a stop at once, never 'no stop needed' at -74%."""
        net = FakeNet(fail_nlr=500, fail_overpass=True)
        r, w = plan(300, ev.car_profile("generic"), 0.19, 15, net)
        self.assertTrue(r["needed"])
        self.assertTrue(r["feasible"])
        self.assertLessEqual(r["stops"][0]["km_along"], 2)
        self.assertGreaterEqual(r["arrive_soc"], 0.15 - 0.005)
        for s in r["stops"]:
            self.assertGreaterEqual(s["arrive_soc"], 0.0)

    def test_arrival_below_reserve_is_never_feasible(self):
        """EV-4 safety net: a plan that cannot charge (one-point route) is infeasible with a warning."""
        w = []
        with mock.patch.object(ev, "stations_near", return_value=[]), mock.patch.object(ev, "log"):
            r = ev.plan_charging([at_km(0)], 300, DEPART, ev.car_profile("generic"), 0.5, 15, w, {})
        self.assertFalse(r["feasible"])
        self.assertTrue(r["needed"])
        self.assertTrue(any("below the 15% reserve; charge before leaving" in x for x in w))

    def test_start_below_reserve_station_close(self):
        """EV-5: at 12% with a charger 5 km away, charge there first."""
        r, w = plan_st([nst(5), nst(200)], 400, ev.car_profile("generic"), 0.12)
        self.assertTrue(r["feasible"])
        self.assertEqual([s["km_along"] for s in r["stops"]][0], 5.0)
        self.assertTrue(any(n.startswith("You start below the 15% reserve: charge at the first stop (km 5)")
                            for n in r["notes"]))
        for s in r["stops"][1:]:
            self.assertGreaterEqual(s["arrive_soc"], 0.15 - 1e-6)
        # below 5% nothing is reachable: still infeasible
        r, w = plan_st([nst(30), nst(200)], 400, ev.car_profile("generic"), 0.04)
        self.assertFalse(r["feasible"])

    def test_skip_short_stop_hint(self):
        """EV-11: the hint shows when the previous stop left at 77%, not only at exactly 80%."""
        r, _ = plan_st([nst(200), nst(390)], 420, ev.car_profile("generic"), 0.8)
        self.assertEqual(len(r["stops"]), 2)
        self.assertLess(r["stops"][0]["depart_soc"], 0.795)
        self.assertLess(r["stops"][-1]["kwh"], 12)
        hint = [n for n in r["notes"] if "would skip the short stop" in n]
        self.assertEqual(len(hint), 1)
        self.assertIn("instead of %d%%" % round(r["stops"][0]["depart_soc"] * 100), hint[0])
        self.assertNotIn("%%", hint[0])

    def test_minus_zero_and_failed_lookup_source(self):
        """UX13: '-0 C' prints as '0 C'; a failed destination lookup shows in the source."""
        r, w = plan_st([], 50, ev.car_profile("generic"), 0.6, temp=-0.3, near=None)
        self.assertIn(", 0 C (", r["notes"][0])
        self.assertNotIn("-0 C", " ".join(r["notes"]))
        r, w = plan_st([], 50, ev.car_profile("generic"), 0.33, temp=15, near=None)   # low arrival, no stop
        self.assertFalse(r["stops"])
        self.assertEqual(len(r["_near_calls"]), 1)
        self.assertEqual(r["source"], "charger lookup failed (NLR limit or Overpass busy)")


class ReturnTripTest(unittest.TestCase):
    """EV-3: no percentage above max_soc, and a real plan on the reversed route for the drive back."""

    def assert_no_big_pct(self, r):
        for n in r["notes"]:
            if n.startswith("Return trip"):
                self.assertTrue(all(p <= 100 for p in pcts(n)), n)
        self.assertLessEqual(r.get("return_soc_needed", 0), 0.8 + 1e-9)
        self.assertLessEqual(r.get("return_soc_to_charger", 0), 0.8 + 1e-9)

    def test_stops_on_the_way_back_from_arrival(self):
        sts = [nst(100), nst(200), nst(300), nst(390)]
        r, w = plan_st(sts, 400, ev.car_profile("generic"), 0.9, return_trip=True, near=[])
        self.assert_no_big_pct(r)
        self.assertGreater(r["return_soc_one_charge"], 1.0)
        self.assertNotIn("return_soc_needed", r)
        rp = r["return_plan"]
        self.assertTrue(rp["feasible"])
        self.assertGreaterEqual(len(rp["stops"]), 2)
        # km of the drive back: the first return stop is the station nearest the destination
        self.assertFalse(rp["charge_at_destination"])
        self.assertEqual(rp["stops"][0]["name"], "S390")
        note = [n for n in r["notes"] if n.startswith("Return trip")][0]
        self.assertIn("charging stops on the way back", note)
        self.assertIn("S390 at km 10", note)
        self.assertIn("from the 20% you arrive with", note)

    def test_charge_at_destination_then_stop(self):
        # arrival 20%, first charger back is 150 km away: charge at the destination, then stop
        sts = [nst(100), nst(250)]
        car = ev.car_profile("generic")
        r, w = plan_st(sts, 400, car, 0.9, return_trip=True, near=[])
        self.assert_no_big_pct(r)
        rp = r["return_plan"]
        self.assertTrue(rp["feasible"] and rp["charge_at_destination"])
        self.assertEqual(rp["stops"][0]["name"], "S250")
        self.assertGreater(r["return_soc_to_charger"], r["arrive_soc"])
        self.assertEqual(len(r["_near_calls"]), 1)   # destination chargers searched once

    def test_no_plan_back(self):
        """Only charger is near the start: say no plan was found, never 'leave with 102%'."""
        r, w = plan_st([nst(20)], 300, ev.car_profile("generic"), 1.0, return_trip=True, near=[])
        self.assert_no_big_pct(r)
        self.assertFalse(r["return_plan"]["feasible"])
        self.assertNotIn("return_soc_to_charger", r)
        self.assertTrue(any("no fast charger plan was found" in n for n in r["notes"]))
        self.assertTrue(any(x.startswith("Return trip: ") for x in w))

    def test_direction_on_the_way_back(self):
        """The drive back uses the other carriageway's service centres."""
        sts = [nst(140, name="ONroute EB", address="Hwy 401 Eastbound", off_deg=-0.001),
               nst(150, name="ONroute WB", address="Hwy 401 Westbound", off_deg=0.001),
               nst(280, name="ONroute EB2", address="Hwy 401 Eastbound", off_deg=-0.001),
               nst(270, name="ONroute WB2", address="Hwy 401 Westbound", off_deg=0.001)]
        r, _ = plan_st(sts, 420, ev.car_profile("ioniq5"), 0.8, return_trip=True, near=[])
        self.assertTrue(all("EB" in s["name"] for s in r["stops"]))
        self.assertTrue(r["return_plan"]["stops"])
        self.assertTrue(all("WB" in s["name"] for s in r["return_plan"]["stops"]))

    def test_short_return_still_prints_leave_with(self):
        r, _ = plan_st([], 100, ev.car_profile("ioniq5"), 0.6, temp=10, return_trip=True, near=[])
        self.assertIn("return_soc_needed", r)
        self.assertNotIn("return_plan", r)


if __name__ == "__main__":
    unittest.main()
