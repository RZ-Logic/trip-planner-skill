"""Offline tests for drive_plan.py (the driving CLI). Routing, parking, charging and trip_hubs are faked.

Run: python -m unittest tests.test_drive_plan -v
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
import types
import urllib.parse
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import drive_plan as dp  # noqa: E402

th = dc.th
MILTON = (43.498757, -79.855987)
MARKHAM = (43.8561, -79.3370)
UNION = (43.6453, -79.3806)
GRAND_BEND = (43.3125, -81.7569)

FUEL_CSV = (
    "Date,Ottawa,Toronto West/Ouest,Toronto East/Est,Windsor,London,Peterborough,St. Catharine's,Sudbury,"
    "Sault Saint Marie,Thunder Bay,North Bay,Timmins,Kenora,Parry Sound,Ontario Average/Moyenne provinciale,"
    "Southern Average/Moyenne du sud de l'Ontario,Northern Average/Moyenne du nord de l'Ontario,Fuel Type,"
    "Type de carburant,,,,,,\n"
    "2026-09-28,175.9,181.8,181.0,1,1,1,1,1,1,1,1,1,1,1,179.8,179.9,179.4,Regular Unleaded Gasoline,Essence,,,,,,\n"
    "2026-10-05,181.6,187.6,0.0,1,1,1,1,1,1,1,1,1,1,1,184.3,185.3,176.2,Regular Unleaded Gasoline,Essence,,,,,,\n"
    "2026-10-05,253.8,250.0,247.0,1,1,1,1,1,1,1,1,1,1,1,250.0,249.4,255.1,Diesel,Diesel,,,,,,\n")


def line(a, b, n=40):
    return [(a[0] + (b[0] - a[0]) * i / (n - 1.0), a[1] + (b[1] - a[1]) * i / (n - 1.0)) for i in range(n)]


# ---------------------------------------------------------------------- fake drive_route

def peak(when):
    h = when.hour + when.minute / 60.0
    return 1.45 if when.weekday() < 5 and (7 <= h < 9.5 or 15.5 <= h < 18.5) else 1.1


def fake_retime(route, depart, opts):
    w = opts.get("weather", 1.0)
    f = peak(depart)
    route["typical_s"] = route["base_s"] * f * w
    route["plan_s"] = route["base_s"] * (f + 0.4) * w
    route["depart"] = depart
    route["arrive"] = depart + dt.timedelta(seconds=route["typical_s"])
    route["arrive_plan"] = depart + dt.timedelta(seconds=route["plan_s"])
    if route["etr"]:
        rate = 1.0 if f > 1.2 else 0.5   # peak band costs more
        for s in route["etr"]:
            s["entry_time"] = depart
            s["toll_transponder"] = round(s["km"] * rate + 1.0, 2)
            s["toll_camera"] = round(s["toll_transponder"] + 5.30, 2)
        route["toll_407_transponder"] = sum(s["toll_transponder"] for s in route["etr"])
        route["toll_407_camera"] = sum(s["toll_camera"] for s in route["etr"])
    return route


def make_route(label, o, d, km, base_s, etr_km=0.0):
    r = {"label": label, "engine": "valhalla", "traffic": "free-flow", "km": km, "base_s": base_s, "chunks": [],
         "poly": line(o, d), "hwy_share": 0.8, "urban_share": 0.4,
         "roads": ["Hwy 401", "Hwy 403"] if not etr_km else ["Hwy 407"], "etr": [], "bridges": [],
         "toll_407_transponder": 0.0, "toll_407_camera": 0.0, "toll_other_cad": 0.0, "has_tolls": bool(etr_km),
         "delays": [], "notes": [], "factor_note": "free-flow time x1.45 in GTHA weekday peak"}
    if etr_km:
        r["etr"] = [{"entry": "Hwy 401", "entry_no": 1, "exit": "Woodbine Ave", "exit_no": 2, "km": etr_km,
                     "direction": "E", "offset_s": 300, "band_start": "07:00", "day_type": "weekday", "zones": [],
                     "note": "2026 rates", "rates_year": 2026}]
    return r


def fake_drive_route(calls=None):
    calls = calls if calls is not None else []
    m = types.ModuleType("drive_route")

    def get_routes(o, d, depart, opts, warnings, arrive_at=None):
        calls.append(("get_routes", depart, arrive_at))
        km = th.haversine_m(o[0], o[1], d[0], d[1]) / 1000.0 * 1.3
        a = make_route("fastest", o, d, km, km / 95.0 * 3600, etr_km=40.0)
        b = make_route("toll-free", o, d, km * 1.05, km / 70.0 * 3600)
        return [fake_retime(a, depart, opts), fake_retime(b, depart, opts)]

    def retime(route, depart, opts):
        calls.append(("retime", route["label"], depart))
        return fake_retime(route, depart, opts)

    def reverse_for_return(route, depart, opts):
        calls.append(("reverse", route["label"], depart))
        r = dict(route, poly=list(reversed(route["poly"])),
                 etr=[dict(s, direction="W") for s in route["etr"]])
        return fake_retime(r, depart, opts)

    m.get_routes, m.retime, m.reverse_for_return = get_routes, retime, reverse_for_return
    return m


def fake_parking(cost=0.0, name="Main St lot"):
    m = types.ModuleType("parking")

    def find_parking(lat, lon, arrive, stay_min, opts, warnings):
        lot = {"name": name, "lat": lat + 0.002, "lon": lon, "walk_m": 290, "walk_min": 4, "search_min": 2,
               "cost": cost, "cost_text": "free" if not cost else "%s for the stay" % dc.money(cost),
               "basis": "", "kind": "lot", "source": "test data", "source_url": "", "confidence": "curated",
               "free": not cost, "fits_stay": True, "max_stay_min": None, "capacity": 100, "often_full": False,
               "reservation": None, "notes": ""}
        return {"options": [lot], "place_rules": ["Pay by app"], "reservations": [], "sources": ["test parking"],
                "radius_m": 800, "caveat": "Posted signs override this data: time limits, rush-hour no-stopping, "
                                           "permit zones."}
    m.find_parking = find_parking
    return m


def fetch_stub(url, **kw):
    if "fueltypesall" in url:
        return FUEL_CSV.encode("utf-8")
    raise OSError("offline test: %s" % url)


def args_for(*extra):
    return dp.build_parser().parse_args(list(extra))


class Env:
    """Patch sibling modules and the network for one test."""

    def __init__(self, parking_cost=0.0, calls=None, ev=False):
        mods = {"drive_route": fake_drive_route(calls), "parking": fake_parking(parking_cost),
                "ev_charging": None}
        self.patches = [mock.patch.dict(sys.modules, mods),
                        mock.patch.object(dc, "fetch", side_effect=fetch_stub),
                        mock.patch.dict(os.environ, {"ON511_API_KEY": "", "TOMTOM_API_KEY": "",
                                                     "GOOGLE_PLACES_API_KEY": ""})]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()


def run(*argv, **kw):
    with Env(**kw):
        args = args_for(*argv)
        res = dp.plan(args)
        return res, dp.render(res)


BASE = ["--origin", "%f,%f" % MILTON, "--weather", "clear", "--incidents", "off"]


# ---------------------------------------------------------------------- tests

class FuelTests(unittest.TestCase):
    def test_latest_week_and_market(self):
        got = dp.parse_fuel_csv(FUEL_CSV, "Toronto West/Ouest", "Regular Unleaded Gasoline")
        self.assertEqual(got, (187.6, "2026-10-05", "Toronto West/Ouest"))

    def test_zero_means_missing(self):
        got = dp.parse_fuel_csv(FUEL_CSV, "Toronto East/Est", "Regular Unleaded Gasoline")
        self.assertEqual(got[0], 185.3)   # Southern average of the same week
        self.assertIn("Southern", got[2])

    def test_diesel(self):
        self.assertEqual(dp.parse_fuel_csv(FUEL_CSV, "Toronto East/Est", "Diesel")[0], 247.0)

    def test_nearest_market_and_price(self):
        w = []
        with mock.patch.object(dc, "fetch", side_effect=fetch_stub):
            p = dp.fuel_price(MILTON[0], MILTON[1], "regular", w)
        self.assertAlmostEqual(p["per_l"], 1.876)
        self.assertEqual(p["market"], "Toronto West")
        self.assertIn("week of 2026-10-05", p["text"])
        self.assertEqual(w, [])

    def test_override_cents(self):
        self.assertAlmostEqual(dp.fuel_price(0, 0, "regular", [], 159.9)["per_l"], 1.599)
        self.assertAlmostEqual(dp.fuel_price(0, 0, "regular", [], 1.65)["per_l"], 1.65)

    def test_network_failure_falls_back(self):
        w = []
        with mock.patch.object(dc, "fetch", side_effect=OSError("down")):
            p = dp.fuel_price(MILTON[0], MILTON[1], "regular", w)
        self.assertAlmostEqual(p["per_l"], 1.843)
        self.assertTrue(any("could not be fetched" in x for x in w))

    def test_outside_ontario_warns(self):
        w = []
        with mock.patch.object(dc, "fetch", side_effect=fetch_stub):
            p = dp.fuel_price(45.50, -73.57, "regular", w)   # Montreal
        self.assertEqual(p["market"], "Ontario average")
        self.assertTrue(any("outside Ontario" in x for x in w))

    def test_consumption_blend_and_cold(self):
        v, basis = dp.consumption("gas", "midsize", 0.5, 20)
        self.assertAlmostEqual(v, (9.2 + 7.1) / 2)
        self.assertAlmostEqual(dp.consumption("gas", "midsize", 1.0, 2)[0], 7.1 * 1.08)
        self.assertAlmostEqual(dp.consumption("gas", "midsize", 1.0, -6)[0], 7.1 * 1.15)
        self.assertAlmostEqual(dp.consumption("hybrid", None, 0.0, 15)[0], 5.2)
        self.assertAlmostEqual(dp.consumption("gas", None, 0.0, 15, override=6.5)[0], 6.5)
        self.assertIn("small SUV", dp.consumption("gas", None, 0.3, None)[1])


class WeatherTests(unittest.TestCase):
    def reply(self, rain=0.0, snow=0.0, temp=9.0):
        times = ["2026-10-12T%02d:00" % h for h in range(24)]
        return {"hourly": {"time": times, "temperature_2m": [temp] * 24, "precipitation": [rain] * 24,
                           "snowfall": [snow] * 24}}

    def test_rain_and_snow(self):
        when = dt.datetime(2026, 10, 12, 10, 15)
        with mock.patch.object(dc, "fetch_json", return_value=self.reply(rain=1.2)):
            w = dp.weather_at(43.5, -80.5, when, "America/Toronto", [], today=dt.date(2026, 10, 10))
        self.assertEqual(w["mult"], 1.15)
        self.assertIn("9 C", w["text"])
        with mock.patch.object(dc, "fetch_json", return_value=self.reply(snow=0.5, temp=-3)):
            w = dp.weather_at(43.5, -80.5, when, "America/Toronto", [], today=dt.date(2026, 10, 10))
        self.assertEqual(w["mult"], 1.4)
        with mock.patch.object(dc, "fetch_json", return_value=self.reply(rain=0.2)):
            w = dp.weather_at(43.5, -80.5, when, "America/Toronto", [], today=dt.date(2026, 10, 10))
        self.assertEqual(w["mult"], 1.0)

    def test_beyond_forecast_uses_month_normal(self):
        with mock.patch.object(dc, "fetch_json") as f:
            w = dp.weather_at(43.6, -79.6, dt.datetime(2027, 1, 20, 9), "America/Toronto", [],
                              today=dt.date(2026, 10, 10))
        f.assert_not_called()
        self.assertEqual(w["temp_c"], -3.9)
        self.assertEqual(w["mult"], 1.0)

    def test_failure_warns(self):
        warnings = []
        with mock.patch.object(dc, "fetch_json", side_effect=OSError("x")):
            w = dp.weather_at(43.6, -79.6, dt.datetime(2026, 10, 12, 9), "America/Toronto", warnings,
                              today=dt.date(2026, 10, 10))
        self.assertEqual(w["mult"], 1.0)
        self.assertTrue(warnings)


class LinkTests(unittest.TestCase):
    def test_google_time_link(self):
        u = dp.google_time_link((43.5, -79.8), (43.8, -79.3), dt.datetime(2026, 10, 12, 9, 0), arrive_by=True)
        self.assertIn("data=!4m6!4m5!2m3!6e1!7e2!8j1791795600!3e0", u)
        u = dp.google_time_link((43.5, -79.8), (43.8, -79.3), dt.datetime(2026, 10, 12, 9, 0), avoid_tolls=True)
        self.assertIn("!4m7!4m6!2m4!2b1!6e0!7e2!8j1791795600!3e0", u)

    def test_official_links(self):
        self.assertIn("avoid=tolls", dp.google_link((1, 2), (3, 4), True))
        self.assertIn("api=1", dp.google_link((1, 2), (3, 4)))
        self.assertEqual(dp.waze_link((43.5, -79.8), True),
                         "https://waze.com/ul?ll=43.500000,-79.800000&navigate=yes&avoid_tolls=t")
        self.assertTrue(dp.apple_link((1, 2), (3, 4)).startswith("https://maps.apple.com/directions?"))


class TradeOffTests(unittest.TestCase):
    def items(self):
        a = {"label": "fastest", "has_tolls": True, "etr": [{}]}
        b = {"label": "toll-free", "has_tolls": False, "etr": []}
        return [{"letter": "A", "route": a, "money": 60.62, "time_s": 41 * 60},
                {"letter": "B", "route": b, "money": 10.0, "time_s": 55 * 60}]

    def test_break_even_text(self):
        t = dp.trade_off(self.items(), 30, False)
        self.assertEqual(t["rec"], "B")
        self.assertIn("The 407 saves 14 min for $50.62 ($217/h of time saved).", t["text"])
        self.assertIn("take B (toll-free)", t["text"])
        self.assertIn("$5 account fee", t["fee_note"])

    def test_high_value_of_time_takes_407(self):
        t = dp.trade_off(self.items(), 300, True)
        self.assertEqual(t["rec"], "A")
        self.assertNotIn("fee_note", t)

    def test_no_tolls(self):
        it = self.items()[1:]
        t = dp.trade_off(it, 30, False)
        self.assertIn("No tolls", t["text"])


class IncidentTests(unittest.TestCase):
    def feed(self, now_ms):
        def item(i, lat, lon, typ, impact, start=now_ms - 3600000, end=now_ms + 3600000, sched=None):
            return ('{"id":"T%d","road":"Road %d","name":"Road %d near X","latitude":"%f","longitude":"%f",'
                    '"type":"%s","maxImpact":"%s","startTime":"%d","endTime":"%d","scheduleEveryday":%s,'
                    '"description":"Toronto-TMC: Water \\ Sewer work","geoPolyline":"[%f,%f],[%f,%f]"}'
                    % (i, i, i, lat, lon, typ, impact, start, end, json.dumps(sched), lon, lat, lon, lat + 0.0005))
        rows = [item(1, 43.70, -79.40, "ROAD_CLOSED", "Low"),               # on the route, closed
                item(2, 43.70, -79.39, "CONSTRUCTION", "High"),             # 800 m off the route
                item(3, 43.70, -79.40, "CONSTRUCTION", "Low"),              # low impact, not a closure
                item(4, 43.70, -79.40, "CONSTRUCTION", "High", start=now_ms + 9e8, end=now_ms + 1e9),   # later
                item(5, 43.70, -79.40, "CONSTRUCTION", "Medium", sched="22:00-05:00"),   # night work only
                ('{"id":"T6","road":"Side St","name":"Side St","latitude":"43.71","longitude":"-79.40",'
                 '"type":"ROAD_CLOSED","maxImpact":"High","startTime":"%d","endTime":"%d","scheduleEveryday":null,'
                 '"description":"","geoPolyline":"[-79.40,43.71],[-79.399,43.71],[-79.398,43.71]"}'
                 % (now_ms - 3600000, now_ms + 3600000))]   # a side street that only touches the route
        return ('{"Closure": [%s]}' % ",".join(rows)).encode("utf-8")

    def test_filters(self):
        start = dt.datetime(2026, 10, 14, 12, 0)
        now_ms = dp._local_to_utc_ms(start, "America/Toronto", None)
        poly = [(43.65, -79.40), (43.75, -79.40)]
        with mock.patch.object(dc, "fetch", return_value=self.feed(now_ms)):
            out = dp.toronto_incidents([(poly, start, start + dt.timedelta(minutes=40))], "America/Toronto", None, [])
        self.assertEqual([x["road"] for x in out], ["Road 1"])
        self.assertEqual(out[0]["description"], "Water \\ Sewer work")

    def test_outside_toronto_skips_fetch(self):
        with mock.patch.object(dc, "fetch") as f:
            out = dp.toronto_incidents([(line(MILTON, GRAND_BEND), dt.datetime(2026, 10, 12, 9),
                                         dt.datetime(2026, 10, 12, 12))], "America/Toronto", None, [])
        f.assert_not_called()
        self.assertEqual(out, [])

    def test_511_with_key(self):
        start = dt.datetime(2026, 10, 14, 12, 0)
        t = dp._local_to_utc_ms(start, "America/Toronto", None) / 1000
        events = [{"ID": "a", "Latitude": 43.70, "Longitude": -79.40, "StartDate": t - 60, "PlannedEndDate": t + 7200,
                   "EventType": "closures", "IsFullClosure": True, "RoadwayName": "Hwy 401", "Description": "x",
                   "Severity": "Major", "DirectionOfTravel": "Eastbound"},
                  {"ID": "b", "Latitude": 44.5, "Longitude": -79.40, "StartDate": t - 60, "EventType": "roadwork"}]
        with mock.patch.object(dc, "fetch_json", return_value=events) as f:
            out = dp.on511_incidents("SECRET", [([(43.65, -79.40), (43.75, -79.40)], start,
                                                 start + dt.timedelta(hours=1))], "America/Toronto", None, [])
        self.assertEqual([x["road"] for x in out], ["Hwy 401"])
        self.assertNotIn("SECRET", f.call_args[1]["cache_key"])


class PlanTests(unittest.TestCase):
    def test_depart_sections_and_links(self):
        res, text = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30",
                        "--park-and-ride", "off")
        heads = [ln.split()[0] for ln in text.splitlines() if ln and not ln.startswith(" ")]
        order = ["DRIVE", "ROUTING", "ROUTES", "TRADE-OFF", "ARRIVAL", "PARKING", "LINKS", "SOURCES"]
        self.assertEqual([h for h in heads if h in order], order)
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)
        self.assertIn("A. Fastest via 407 ETR (Hwy 401 to Woodbine Ave, 40 km)", text)
        self.assertIn("407 ETR eastbound, weekday 07:00 to 09:30 band", text)   # UX9: the band as a window
        self.assertIn("Toronto West regular, week of 2026-10-05", text)
        lot = res["parking"]["options"][0]
        self.assertIn("%.6f,%.6f" % (lot["lat"], lot["lon"]), urllib.parse.unquote(res["links"]["google"]))
        self.assertIn("!6e0", res["links"]["google_timed"])
        self.assertIn("walk from the lot", text)
        json.loads(dp.to_json(res))   # serialisable

    def test_arrive_by_two_pass(self):
        calls = []
        deadline = dt.datetime(2026, 10, 14, 9, 0)
        with Env(calls=calls):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--arrive-by", "2026-10-14 09:00",
                                   "--park-and-ride", "off"))
            text = dp.render(res)
        for row in res["routes"]:
            extra = (2 + 4 + 5) * 60
            end = row["leave_by"] + dt.timedelta(seconds=row["plan_s"] + extra)
            self.assertLessEqual((end - deadline).total_seconds(), 60, row["label"])
            self.assertGreater((end - deadline).total_seconds(), -30 * 60, row["label"])
            self.assertEqual(row["depart"], row["leave_by"])
        self.assertGreaterEqual(sum(1 for c in calls if c[0] == "retime"), 4)   # at least two passes per route
        self.assertEqual(calls[0][2], deadline)
        self.assertIn("Leave by", text)
        self.assertIn("to be at the door by 09:00", text)
        self.assertIn("!6e1", res["links"]["google_timed"])

    def test_round_trip_prices_return(self):
        calls = []
        with Env(calls=calls):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % GRAND_BEND, "--depart", "2026-10-12 09:00",
                                   "--stay-hours", "5", "--round-trip"))
            text = dp.render(res)
        rev = [c for c in calls if c[0] == "reverse"]
        self.assertEqual(len(rev), 2)
        a = res["routes"][0]
        self.assertEqual(rev[0][2], a["arrive"] + dt.timedelta(minutes=2 + 4 + 300 + 4))
        self.assertIn("return", a)
        one_way = a["energy"]["cost"] + a["tolls"]
        # F15: no transponder, so the $5 monthly account fee is counted once for the 407 route
        self.assertAlmostEqual(a["money"], one_way + a["energy"]["cost"] + a["return"]["tolls"] + 5.0, places=2)
        self.assertEqual(a["account_fee"], 5.0)
        self.assertIn("round trip total", text)
        self.assertIn("Return: leave", text)
        self.assertIn("(Thanksgiving)", text)
        self.assertIsNone(res["park_and_ride"])   # Grand Bend is outside the GTHA

    def test_park_and_ride_downtown(self):
        hub_res = {"access": "car", "warnings": [], "pareto": [
            {"hub": "Milton GO", "ride_km": 2.1, "ride_min": 5, "book_by": 6 * 3600 + 40 * 60, "board": 7 * 3600,
             "arrive": 8 * 3600 + 5 * 60, "transit_fare": 10.52, "car_cost": 0.40,
             "parking": {"text": "free GO parking, fills by about 08:00 on weekdays", "cost": 0.0},
             "legs": [{"type": "ride", "agency": "GO Transit", "route": "MI", "headsign": "Union Station",
                       "depart": 7 * 3600, "arrive": 8 * 3600}]}], "others": []}
        seen = {}

        def fake_plan(ns):
            seen["ns"] = ns
            return hub_res
        with mock.patch.object(th, "plan", side_effect=fake_plan):   # $20 parking: the hub is cheaper there and back
            res, text = run(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00",
                            "--stay-hours", "9", parking_cost=20.0)
        self.assertEqual(res["park_and_ride"]["trigger"], "the destination is in downtown Toronto")
        self.assertEqual(seen["ns"].access, "car")
        self.assertEqual(seen["ns"].arrive_by, "2026-10-14 09:00")
        self.assertIn("PARK AND RIDE", text)
        self.assertIn('Drive 2.1 km to Milton GO (free GO parking, fills by about 08:00 on weekdays), leave by 06:40, '
                      'board GO Transit MI "Union Station" at 07:00, arrive 08:05', text)
        # INTEGRATE: park and ride is compared there and back (fares both ways, driving both ways + parking)
        self.assertIn("there and back: car $0.80 + fares $21.04 = $21.84", text)
        self.assertIn("The car stays at the hub", text)
        self.assertLess(text.index("PARKING"), text.index("PARK AND RIDE"))

    def test_park_and_ride_expensive_parking_trigger(self):
        with mock.patch.object(th, "plan", return_value={"access": "car", "pareto": [], "others": []}):
            res, text = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", parking_cost=22.0)
        self.assertIn("$22.00", res["park_and_ride"]["trigger"])

    def test_park_and_ride_failure_warns(self):
        with mock.patch.object(th, "plan", side_effect=SystemExit("no feeds")):
            res, text = run(*BASE, "--dest", "%f,%f" % UNION, "--depart", "2026-10-14 07:30")
        self.assertTrue(any("park and ride search failed (no feeds)" in w and "trip_hubs.py" in w
                            for w in res["warnings"]))
        self.assertNotIn("PARK AND RIDE", text)

    def test_ev_without_charging_module(self):
        res, text = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 11:00", "--vehicle", "ev",
                        "--park-and-ride", "off")
        en = res["routes"][0]["energy"]
        self.assertAlmostEqual(en["cost"], res["routes"][0]["km"] * 20.1 / 100 * 0.14, places=2)
        self.assertIn("home rate", text)
        self.assertTrue(any("ev_charging.py is missing" in w for w in res["warnings"]))

    def test_ev_with_charging(self):
        evm = types.ModuleType("ev_charging")
        evm.car_profile = lambda *a, **k: {"name": "Test EV", "connectors": {"CCS"}}
        seen = []

        def plan_charging(poly, km, dep, car, soc, t, w, opts=None):
            seen.append((soc, t, (opts or {}).get("return_trip")))
            return dict(charged)
        charged = {
            "needed": True, "arrive_soc": 0.22, "extra_s": 1500, "public_cost": 12.5, "kwh_used": 60.0,
            "kwh_100km": 19.0, "home_energy_cost": 3.0, "notes": [], "dest_chargers": [], "source": "NLR",
            "stops": [{"name": "ONroute Cambridge", "network": "Ivy", "km_along": 120, "max_kw": 350, "ports": 4,
                       "arrive_soc": 0.18, "depart_soc": 0.55, "charge_min": 20, "cost": 12.5,
                       "price_text": "$0.69/kWh", "detour_min": 1}]}
        evm.plan_charging = plan_charging
        with Env():
            with mock.patch.dict(sys.modules, {"ev_charging": evm}):
                res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % GRAND_BEND, "--depart", "2026-10-12 09:00",
                                       "--vehicle", "ev", "--soc", "75", "--temp-c", "4"))
                text = dp.render(res)
        # the outbound call gets the starting charge as a fraction and the temperature, in that order
        self.assertTrue(seen)
        self.assertEqual(seen[0], (0.75, 4.0, False))
        self.assertIn("CHARGING", text)
        self.assertIn("ONroute Cambridge", text)
        self.assertAlmostEqual(res["routes"][0]["energy"]["cost"], 15.5)
        self.assertEqual(res["arrival"]["charge_min"], 25)
        json.loads(dp.to_json(res))

    def test_no_routes(self):
        for status in ("ok", "blocked: test"):   # th.check_network faked: no live HEAD to the routing hosts
            with self.subTest(status=status), Env(), \
                    mock.patch.object(th, "check_network", side_effect=lambda hosts, timeout=6: {h: status for h in hosts}):
                sys.modules["drive_route"].get_routes = lambda *a, **k: []
                res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30"))
                text = dp.render(res)
            self.assertIn("ROUTES", text)
            self.assertIn("WARNINGS", text)
            self.assertEqual(res["network_blocked"], status != "ok")
            self.assertEqual("NETWORK BLOCKED" in text, status != "ok")

    def test_missing_parking_module(self):
        with Env():
            with mock.patch.dict(sys.modules, {"parking": None}):
                res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30",
                                       "--park-and-ride", "off"))
                text = dp.render(res)
        self.assertTrue(any("parking.py is missing" in w for w in res["warnings"]))
        self.assertIn("Navigate to the destination", text)

    def test_hub_args_fallback(self):
        w = []
        saved = th.__dict__.pop("build_parser", None)
        try:
            ns, argv = dp.hub_args(MILTON, UNION, dt.datetime(2026, 10, 14, 9), True, 0.15, 9, w)
        finally:
            if saved is not None:
                th.build_parser = saved
        self.assertEqual(ns.access, "car")
        self.assertEqual(ns.arrive_by, "2026-10-14 09:00")
        self.assertAlmostEqual(ns.car_cost_per_km, 0.15)
        self.assertIn("--access", argv)
        self.assertTrue(w)

    def test_hub_args_parser(self):
        if not hasattr(th, "build_parser"):
            self.skipTest("trip_hubs.build_parser not added yet")
        ns, argv = dp.hub_args(MILTON, UNION, dt.datetime(2026, 10, 14, 9), False, 0.15, 9, [])
        self.assertEqual(ns.access, "car")
        self.assertEqual(ns.depart, "2026-10-14 09:00")


NOW = dt.datetime(2026, 10, 10, 12, 0)


def at_now(when):
    return mock.patch.object(th, "local_now", return_value=(when, "test clock"))


def hub(name, km, leave, arrive, fare, car, park=None, complete=True, parts=None, backup=None, board=None):
    park = park or {"text": "free GO parking, first come, first served", "cost": 0.0}
    return {"hub": name, "ride_km": km, "ride_min": km, "book_by": leave, "board": board or leave + 1800,
            "arrive": arrive, "transit_fare": fare, "fare_complete": complete, "fare_parts": parts or [],
            "car_cost": car, "parking": park, "backup": backup,
            "legs": [{"type": "ride", "agency": "GO Transit", "route": "LW", "headsign": "Union Station",
                      "depart": board or leave + 1800, "arrive": arrive}]}


def H(h, m=0):
    return h * 3600 + m * 60


class ParkRideSelectTests(unittest.TestCase):
    """K2, F7, PR1, PR2, PR7, PR8, PR9, PR12."""
    DAY = dt.date(2026, 10, 14)

    def run_pr(self, hubs, drive_rt=36.84, arrive_by=True, extra=None, stay_h=9.0):
        res = {"access": "car", "warnings": [], "pareto": hubs, "others": [], "hubs_with_service": 12}
        ctx = {"drive_rt": drive_rt, "day": self.DAY, "traffic_factor": 2.07, "safety_min": 5,
               "drive_leave": dt.datetime(2026, 10, 14, 7, 9) if arrive_by else None,
               "door": None if arrive_by else dt.datetime(2026, 10, 14, 8, 30)}
        ctx.update(extra or {})
        w = []
        with mock.patch.object(th, "plan", return_value=res) as p:
            pr = dp.park_and_ride(MILTON, UNION, dt.datetime(2026, 10, 14, 9), arrive_by, 0.16, stay_h, 9.42, 18.0, w,
                                  30, ctx=ctx)
        return pr, w, p.call_args[0][0]

    def five(self):
        return [hub("Weston GO/UP", 43.9, H(7, 35), H(8, 56), None, 7.03, complete=False,
                    parts=[{"agency": "UP Express", "fare": None}]),
                hub("Clarkson GO", 26.2, H(7, 33), H(8, 47), 7.46, 4.19),
                hub("Oakville GO", 22.0, H(7, 31), H(8, 50), 8.18, 3.51),
                hub("Bronte GO", 15.6, H(7, 28), H(8, 52), 9.36, 2.49),
                hub("Milton GO", 4.9, H(7, 26), H(8, 45), 10.33, 0.79)]

    def test_cheapest_complete_hub_is_kept_and_unknown_fare_last(self):
        pr, w, ns = self.run_pr(self.five())
        names = [op["hub"] for op in pr["options"]]
        self.assertIn("Milton GO", names)                 # PR1: the cheapest round trip is not cut by leave-by
        self.assertEqual(names[0], "Clarkson GO")         # best value: $1.06 dearer but leaves 7 min later ($30/h)
        self.assertEqual(names[-1], "Weston GO/UP")       # unknown fare last
        self.assertLessEqual(len(names), 3)
        mil = next(op for op in pr["options"] if op["hub"] == "Milton GO")
        self.assertAlmostEqual(mil["rt_total"], 2 * 0.79 + 2 * 10.33)
        self.assertEqual(mil["leave_later_min"], 17)      # 07:26 vs driving 07:09
        self.assertEqual(ns.traffic_factor, 2.07)         # PR2/F3: same traffic allowance as the drive
        self.assertEqual(ns.safety_min, 5.0)

    def test_far_cheap_hub_that_leaves_much_earlier_ranks_below_a_near_one(self):
        hubs = [hub("Mimico GO", 42.0, H(6, 46), H(8, 34), 3.71, 5.24),
                hub("Oakville GO", 21.9, H(7, 14), H(8, 47), 8.18, 2.74),
                hub("Milton GO", 4.9, H(7, 19), H(8, 48), 10.33, 0.62)]
        pr, w, _ = self.run_pr(hubs, drive_rt=32.20)
        names = [op["hub"] for op in pr["options"]]
        self.assertEqual(names[0], "Milton GO")           # leaves last; Mimico saves $4 but leaves 33 min earlier
        self.assertIn("Mimico GO", names)                 # still shown: it is the cheapest there and back
        self.assertGreater(pr["options"][-1]["time_cost"], 30)

    def test_value_of_time_zero_ranks_by_money(self):
        pr, w, _ = self.run_pr(self.five(), extra={"vot": 0})
        self.assertEqual([op["hub"] for op in pr["options"]][0], "Milton GO")

    def test_partial_fare_is_unknown(self):
        pr, w, _ = self.run_pr(self.five())
        wes = next(op for op in pr["options"] if op["hub"] == "Weston GO/UP")
        self.assertIsNone(wes["fare"])                    # PR7
        self.assertIsNone(wes["rt_total"])
        self.assertEqual(wes["fare_missing"], ["UP Express"])
        hp = [{"hub": "Weston GO/UP", "ride_km": 43.9, "transit_fare": 3.10, "fare_complete": False,
               "fare_parts": [{"agency": "GO Transit", "fare": 3.10}, {"agency": "UP Express", "fare": None}]}]
        h = hub("Weston GO/UP", 43.9, H(7, 35), H(8, 56), 3.10, 7.03, complete=False,
                parts=hp[0]["fare_parts"])
        pr, w, _ = self.run_pr([h])
        self.assertIsNone(pr["options"][0]["fare"])       # a partial sum is not shown as the fare

    def test_json_fields(self):
        pr, w, _ = self.run_pr(self.five())
        op = pr["options"][0]
        self.assertIn("total_one_way", op)                # PR9: no ambiguous 'total' / 'vs_drive'
        self.assertNotIn("vs_drive", op)
        self.assertEqual(op["drive_rt"], 36.84)
        self.assertEqual(set(op["parking"]), {"text", "cost", "price_unknown", "basis"})

    def test_dominated_options_dropped_one_line(self):
        hubs = [hub("Aldershot GO Bus", 25.0, H(10, 4), H(12, 25), 28.35, 1.84),
                hub("Square One", 24.3, H(10, 23), H(12, 37), 42.99, 1.80)]
        pr, w, _ = self.run_pr(hubs, drive_rt=37.46, arrive_by=False,
                               extra={"door": dt.datetime(2026, 10, 14, 11, 30)})
        self.assertEqual(pr["options"], [])               # K2/PR8: both slower and dearer
        self.assertEqual(pr["dropped"], 2)
        text = "\n".join(dp.pr_lines(dict(pr, trigger="test"), {"mode": "depart"}))
        self.assertIn("checked 12 hubs: driving is cheaper and faster", text)
        self.assertNotIn("The car stays at the hub", text)
        self.assertNotIn("1. Drive", text)

    def test_slower_but_cheaper_is_kept(self):
        hubs = [hub("Milton GO", 4.9, H(7, 0), H(9, 10), 10.33, 0.79)]
        pr, w, _ = self.run_pr(hubs, arrive_by=False)
        self.assertEqual(len(pr["options"]), 1)
        self.assertEqual(pr["options"][0]["later_min"], 40)

    def test_backup_shown(self):
        b = {"board": H(8, 21), "arrive": H(8, 43), "route": "UP Express", "book_by": H(7, 20)}
        pr, w, _ = self.run_pr([hub("Weston GO/UP", 43.9, H(7, 35), H(8, 56), 6.0, 7.03, backup=b)])
        text = "\n".join(dp.pr_lines(dict(pr, trigger="test"), {"mode": "arrive_by"}))
        self.assertIn("backup: leave by 07:20 for UP Express at 08:21, arrive 08:43", text)
        self.assertIn("x2.07 traffic allowance, 5 min spare", text)
        self.assertIn("leave 26 min later than driving", text)

    def test_hub_limits(self):
        go = {"parking": {"text": "free GO parking, first come, first served"}}
        ttc = {"parking": {"text": "TTC commuter lot, $5 to $7 a day on weekdays"}}
        self.assertIn("48", dp.hub_limit(go, dt.datetime(2026, 10, 14, 7), 50))
        self.assertIsNone(dp.hub_limit(go, dt.datetime(2026, 10, 14, 7), 30))
        self.assertIn("02:00", dp.hub_limit(ttc, dt.datetime(2026, 10, 14, 20), 8))
        self.assertIsNone(dp.hub_limit(ttc, dt.datetime(2026, 10, 14, 7), 10))
        pr, w, _ = self.run_pr([hub("Milton GO", 4.9, H(7, 26), H(8, 45), 10.33, 0.79)], stay_h=60)
        self.assertEqual(pr["options"], [])
        self.assertTrue(any("Milton GO" in x and "48" in x for x in w))

    def test_hub_stay_includes_transit_and_traffic_factor(self):
        seen = {}

        def fake(ns):
            seen["ns"] = ns
            return {"access": "car", "pareto": [], "others": []}
        with mock.patch.object(th, "plan", side_effect=fake):
            res, text = run(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00", "--stay-hours", "9")
        ns = seen["ns"]
        self.assertGreater(ns.stay_hours, 9.9)            # PR12: the car waits at the hub for the transit legs too
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        # the hub drive uses the same planning-allowance rows as trip_hubs car mode (deadline minus 60 min), times the weather
        want = th.plan_traffic_factor(dt.datetime(2026, 10, 14, 8, 0))[0] * (res["weather"].get("mult") or 1.0)
        self.assertAlmostEqual(ns.traffic_factor, round(want, 2), places=2)
        self.assertEqual(ns.safety_min, 5.0)
        self.assertIn("no hub with parking and transit reaches the destination", text)

    def test_niagara_does_not_trigger(self):
        niagara = (43.0791, -79.0784)
        with mock.patch.object(th, "plan") as p:
            res, text = run(*BASE, "--dest", "%f,%f" % niagara, "--depart", "2026-10-17 10:00", parking_cost=30.0)
        p.assert_not_called()                             # F7/K3: outside the GTHA built-up boxes
        self.assertIsNone(res["park_and_ride"])


class TradeOffRoundingTests(unittest.TestCase):
    def test_same_minutes_cheaper_wins(self):   # K5/F6
        b = {"label": "toll-free", "has_tolls": False, "etr": []}
        c = {"label": "alternative", "has_tolls": False, "etr": []}
        a = {"label": "fastest", "has_tolls": True, "etr": [{}]}
        items = [{"letter": "A", "route": a, "money": 22.13, "time_s": 66 * 60},
                 {"letter": "B", "route": b, "money": 9.19, "time_s": 68.3 * 60},
                 {"letter": "C", "route": c, "money": 9.42, "time_s": 67.8 * 60}]
        t = dp.trade_off(items, 30, False)
        self.assertEqual(t["rec"], "B")
        self.assertEqual(t["saved_min"], 2)

    def test_fee_in_money_and_407_this_month(self):   # F15
        res, _ = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off")
        a = res["routes"][0]
        self.assertAlmostEqual(a["money"], a["energy"]["cost"] + a["tolls"] + 5.0, places=2)
        self.assertIn("counted once", res["trade_off"]["fee_note"])
        res, text = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off",
                        "--407-this-month")
        a = res["routes"][0]
        self.assertAlmostEqual(a["money"], a["energy"]["cost"] + a["tolls"], places=2)
        self.assertIn("not counted", res["trade_off"]["fee_note"])
        res, _ = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off",
                     "--transponder")
        self.assertEqual(res["routes"][0]["account_fee"], 0.0)


class LeaveAtTests(unittest.TestCase):   # F1
    def test_hhmm_is_on_the_trip_day(self):
        calls = []
        with Env(calls=calls), at_now(NOW):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30",
                                   "--leave-at", "17:00", "--round-trip", "--park-and-ride", "off"))
            text = dp.render(res)
        back = res["arrival"]["return"]["leave"]
        self.assertEqual(back.date(), dt.date(2026, 10, 14))
        self.assertEqual(back, dt.datetime(2026, 10, 14, 17, 4))   # 17:00 + 4 min walk to the lot
        self.assertFalse(any("already passed" in w for w in res["warnings"]))
        self.assertGreater(res["stay_min"], 7 * 60)
        self.assertIn("17:04", text)

    def test_hhmm_before_arrival_rolls_to_next_day(self):
        with Env(), at_now(NOW):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30",
                                   "--leave-at", "08:00", "--round-trip", "--park-and-ride", "off"))
            text = dp.render(res)
        self.assertEqual(res["arrival"]["return"]["leave"].date(), dt.date(2026, 10, 15))
        self.assertTrue(any("is before you get there" in w for w in res["warnings"]))
        self.assertIn("(Thu 15 Oct)", text)   # F11: the other day is named

    def test_full_date_before_arrival_errors(self):
        with Env(), at_now(NOW):
            with self.assertRaises(SystemExit) as cm:
                dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30",
                                 "--leave-at", "2026-10-14 08:00", "--park-and-ride", "off"))
        self.assertIn("before you get there", str(cm.exception))


class ArriveByTests(unittest.TestCase):
    def test_round_trip_anchored_at_deadline(self):   # F5, K4, F9
        deadline = dt.datetime(2026, 10, 14, 9, 0)
        with Env(), at_now(NOW):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00",
                                   "--stay-hours", "8", "--round-trip", "--park-and-ride", "off"))
            text = dp.render(res)
        a = res["arrival"]
        self.assertEqual(a["return"]["leave"], deadline + dt.timedelta(minutes=8 * 60 + 4))
        self.assertEqual(res["parking_from"], a["at_lot"])               # priced from the typical lot arrival
        self.assertEqual(res["parking_until"], a["return"]["leave"])
        self.assertAlmostEqual(res["parking_stay_min"],
                               (a["return"]["leave"] - a["at_lot"]).total_seconds() / 60.0, places=1)
        self.assertIn("8 h stay, parked %s to 17:04" % dc.fmt_clock(a["at_lot"]), text)

    def test_json_depart_is_leave_by_and_minutes(self):   # F12, K11
        with Env(), at_now(NOW):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00",
                                   "--park-and-ride", "off"))
        j = json.loads(dp.to_json(res))
        self.assertEqual(j["depart"], res["arrival"]["leave"].strftime("%Y-%m-%dT%H:%M"))
        self.assertIn("depart_guess", j)
        for k in ("at_lot", "at_lot_plan", "at_door"):
            self.assertRegex(j["arrival"][k], r"^\d{4}-\d\d-\d\dT\d\d:\d\d$")

    def test_passed_leave_by_not_recommended(self):   # F4
        with Env(), at_now(NOW):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00",
                                   "--park-and-ride", "off"))
        lb = {r["letter"]: r["leave_by"] for r in res["routes"]}
        self.assertEqual(res["recommended"], "B")     # toll-free leaves earliest
        now = lb["B"] + dt.timedelta(minutes=3)
        self.assertLess(now, lb["A"])
        with Env(), at_now(now):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00",
                                   "--park-and-ride", "off"))
            text = dp.render(res)
        self.assertEqual(res["recommended"], "A")
        self.assertTrue(any("B needed you to leave by" in w for w in res["warnings"]))
        if (lb["A"] - now).total_seconds() < 300:
            self.assertIn("leave now", text)

    def test_leave_now_flag(self):
        with Env(), at_now(NOW):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00",
                                   "--park-and-ride", "off"))
        lb = res["arrival"]["leave"]
        with Env(), at_now(lb - dt.timedelta(minutes=3)):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % UNION, "--arrive-by", "2026-10-14 09:00",
                                   "--park-and-ride", "off"))
            text = dp.render(res)
        self.assertIn("That is in 3 min: leave now.", text)

    def test_backsolve_latest_minute(self):   # F16
        class DR:
            def retime(self, route, dep, opts):
                route["depart"] = dep
                route["plan_s"] = 5400 if dep < dt.datetime(2026, 10, 14, 7, 0) else 7200
        deadline = dt.datetime(2026, 10, 14, 8, 40)
        route = {"plan_s": 5400}
        dep = dp.plan_backwards(DR(), route, deadline, 0, {})
        self.assertEqual(dep, dt.datetime(2026, 10, 14, 6, 59))   # 6.7 min early before; now the latest minute
        self.assertEqual(route["depart"], dep)
        route = {"plan_s": 3600}
        dep = dp.plan_backwards(DR(), route, dt.datetime(2026, 10, 14, 9, 30), 60, {})
        self.assertLessEqual(dep + dt.timedelta(seconds=route["plan_s"] + 60), dt.datetime(2026, 10, 14, 9, 30))


class ParkingAgainTests(unittest.TestCase):   # F8, UX8
    def test_re_search_refreshes_walk(self):
        m = types.ModuleType("parking")
        calls = []

        def find_parking(lat, lon, arrive, stay_min, opts, warnings):
            calls.append((arrive, stay_min))
            walk = 4 if len(calls) == 1 else 9
            lot = {"name": "Lot %d" % len(calls), "lat": lat + 0.002, "lon": lon, "walk_m": 100 * walk,
                   "walk_min": walk, "search_min": 2, "cost": 0.0, "cost_text": "free", "free": True,
                   "fits_stay": True, "max_stay_min": None, "confidence": "osm", "source": "t", "notes": ""}
            return {"options": [lot], "place_rules": [], "reservations": [], "sources": [], "radius_m": 800}
        m.find_parking = find_parking
        with Env(), at_now(NOW), mock.patch.dict(sys.modules, {"parking": m}):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % GRAND_BEND, "--depart", "2026-10-12 09:00",
                                   "--stay-hours", "5", "--round-trip"))
        # the second search (priced with the first lot's 4 min walk) picks a lot 9 min away, so a third search
        # prices that lot for the time it actually holds the car
        self.assertEqual(len(calls), 3)
        a = res["arrival"]
        self.assertEqual((a["lot"], a["walk_min"]), ("Lot 3", 9))
        self.assertEqual(a["return"]["leave"], a["at_lot"] + dt.timedelta(minutes=2 + 9 + 300 + 9))
        self.assertAlmostEqual(calls[1][1], 2 + 4 + 300 + 4)
        self.assertAlmostEqual(calls[2][1], 2 + 9 + 300 + 9)   # priced for the time parked

    def test_slot_and_max_stay_cap_return(self):
        m = types.ModuleType("parking")

        def find_parking(lat, lon, arrive, stay_min, opts, warnings):
            lot = {"name": "Cyprus Lake P1", "lat": lat, "lon": lon, "walk_m": 300, "walk_min": 4, "search_min": 2,
                   "cost": 21.0, "cost_text": "$21.00", "free": False, "fits_stay": stay_min <= 240,
                   "max_stay_min": 240, "reservation": "Parks Canada reservation, 4 h slot", "confidence": "curated",
                   "source": "t", "notes": ""}
            rules = ["Grotto: a confirmed parking reservation is required, 4 h slots 08:00-12:00, 09:00-13:00, "
                     "12:00-16:00, 13:00-17:00, 16:00-20:00, 17:00-21:00."]
            return {"options": [lot], "place_rules": rules, "reservations": [], "sources": [], "radius_m": 800}
        m.find_parking = find_parking
        with Env(), at_now(NOW), mock.patch.dict(sys.modules, {"parking": m}):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % GRAND_BEND, "--depart", "2026-10-17 07:00",
                                   "--stay-hours", "6", "--round-trip"))
            text = dp.render(res)
        a = res["arrival"]
        park_start = a["at_lot"] + dt.timedelta(minutes=2)
        slot = a["slot"]
        self.assertLessEqual(slot["start"], park_start)
        self.assertGreaterEqual((slot["end"] - park_start).total_seconds(), 7200)
        self.assertEqual(a["return"]["leave"], slot["end"])        # not after the 6 h stay
        self.assertTrue(any("slot ends" in w or "max stay" in w for w in res["warnings"]))
        self.assertIn("Book the", text)


class ValidationTests(unittest.TestCase):   # F10
    def bad(self, *extra):
        with mock.patch("sys.stderr", io_sink()):
            with self.assertRaises(SystemExit):
                args_for("--origin", "43.5,-79.8", "--dest", "43.6,-79.4", *extra)

    def test_ranges(self):
        for extra in (("--soc", "150"), ("--soc", "-1"), ("--stay-hours", "-3"), ("--stay-hours", "0"),
                      ("--value-of-time", "-50"), ("--l-per-100km", "0"), ("--max-walk-m", "0"),
                      ("--kwh-per-100km", "0"), ("--fuel-price", "0")):
            self.bad(*extra)
        self.assertEqual(args_for("--origin", "1,2", "--dest", "3,4", "--soc", "100").soc, 100.0)

    def test_bad_time_is_one_line(self):
        for t in ("14/10/2026", "25:00"):
            with Env():
                with self.assertRaises(SystemExit) as cm:
                    dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", t))
            self.assertIn("--depart", str(cm.exception))

    def test_vehicle_conflicts(self):
        with self.assertRaises(SystemExit) as cm:
            dp.validate(args_for("--origin", "1,2", "--dest", "3,4", "--vehicle", "hybrid", "--ev-model", "ioniq5"))
        self.assertIn("--ev-model", str(cm.exception))
        a = args_for("--origin", "1,2", "--dest", "3,4", "--kwh-per-100km", "18")
        dp.validate(a)
        self.assertEqual(a.vehicle, "ev")

    def test_fuel_price_20_is_cents(self):
        self.assertAlmostEqual(dp.fuel_price(0, 0, "regular", [], 20)["per_l"], 0.20)
        self.assertAlmostEqual(dp.fuel_price(0, 0, "regular", [], 19.9)["per_l"], 19.9)

    def test_description(self):   # UX12
        self.assertIn("time-of-day traffic model", dp.build_parser().description)
        self.assertNotIn("traffic-aware", dp.build_parser().description)


def io_sink():
    import io
    return io.StringIO()


class FuelTextTests(unittest.TestCase):   # F13, F14
    def test_basis_and_hint(self):
        res, text = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off")
        # the CAR line names the assumption; the fuel line keeps only the numbers
        self.assertIn("small SUV median", res["routes"][0]["energy"]["basis"])
        self.assertIn("CAR        not given, so fuel assumes a small SUV", text)
        self.assertIn('Pass --car "YEAR MAKE MODEL"', text)
        res, text = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off",
                        "--l-per-100km", "6.7")
        self.assertNotIn("Pass --car", text)
        self.assertIn("6.7 L/100 km (your figure)", text)

    def test_diesel_warns(self):
        res, _ = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off",
                     "--vehicle", "diesel")
        self.assertIn(dp.costs()["consumption"]["diesel_note"], res["warnings"])

    def test_market_choice(self):
        with mock.patch.object(dc, "fetch", side_effect=fetch_stub):
            w = []
            p = dp.fuel_price(44.2312, -76.4860, "regular", w)          # Kingston: 146 km from Peterborough
            self.assertEqual(p["market"], "Southern Ontario average")
            self.assertAlmostEqual(p["per_l"], 1.853)
            self.assertEqual(w, [])
            p = dp.fuel_price(49.4167, -82.4331, "regular", [])        # Kapuskasing, far north
            self.assertEqual(p["market"], "Northern Ontario average")
            self.assertAlmostEqual(p["per_l"], 1.762)
            w = []
            p = dp.fuel_price(42.8864, -78.8784, "regular", w, province="new york")   # Buffalo is in the bbox
            self.assertEqual(p["market"], "Ontario average")
            self.assertTrue(any("outside Ontario" in x for x in w))
            p = dp.fuel_price(43.4988, -79.8560, "regular", [], province="ontario")
            self.assertEqual(p["market"], "Toronto West")
        self.assertEqual(dc.load_data("costs")["fuel"]["max_market_km"], 100)
        self.assertEqual(dp.province_of({"country": "US", "state": "new york"}), "us")
        self.assertEqual(dp.place_label("Jackson Square, 2, King Street West, Hamilton, Ontario, Canada", 43, -79)[1],
                         {"state": "ontario"})


class LabelTests(unittest.TestCase):   # K6, F18, UX10
    def test_reverse_geocode_label(self):
        reply = {"name": "", "address": {"house_number": "805", "road": "Santa Maria Boulevard", "town": "Milton",
                                         "state": "Ontario", "country_code": "ca"}}
        with mock.patch.object(dc, "fetch_json", return_value=reply) as f, \
                mock.patch.dict(os.environ, {"TRIP_HOME": ""}):
            label, info = dp.place_label("43.498757,-79.855987", *MILTON)
        self.assertEqual(label, "805 Santa Maria Blvd, Milton")
        self.assertEqual(dp.province_of(info), "ontario")
        kw = f.call_args[1]
        self.assertEqual(kw["max_age_h"], 720)
        self.assertGreaterEqual(kw["min_interval"], 1.0)
        self.assertIn("lat=43.4988&", kw["cache_key"])
        self.assertIn("zoom=18", f.call_args[0][0])
        road_only = {"category": "highway", "type": "tertiary", "name": "Santa Maria Boulevard",
                     "address": {"road": "Santa Maria Boulevard", "town": "Milton"}}
        walkway = {"category": "highway", "type": "footway", "name": "PATH",
                   "address": {"road": "PATH", "city": "Toronto"}}
        with mock.patch.dict(os.environ, {"TRIP_HOME": ""}):
            with mock.patch.object(dc, "fetch_json", return_value=road_only):
                self.assertEqual(dp.place_label("43.4988,-79.8560", 43.4988, -79.8560)[0], "Santa Maria Blvd, Milton")
            with mock.patch.object(dc, "fetch_json", return_value=walkway):
                self.assertEqual(dp.place_label("43.6453,-79.3806", 43.6453, -79.3806)[0], "Toronto")

    def test_failure_keeps_coordinates_and_home(self):
        with mock.patch.object(dc, "fetch_json", side_effect=OSError("down")), \
                mock.patch.dict(os.environ, {"TRIP_HOME": ""}):
            self.assertEqual(dp.place_label("43.498757,-79.855987", *MILTON)[0], "43.498757,-79.855987")
        with mock.patch.object(dc, "fetch_json") as f, \
                mock.patch.dict(os.environ, {"TRIP_HOME": "43.49876,-79.85599;home (park)"}):
            self.assertEqual(dp.place_label("43.498757,-79.855987", *MILTON)[0], "home (park)")
        f.assert_not_called()

    def test_short_label_house_number(self):
        self.assertEqual(dp.short_label("CN Tower, 290, Bremner Boulevard, Toronto, Ontario"),
                         "CN Tower, 290 Bremner Boulevard")
        self.assertEqual(dp.short_label("805, Santa Maria Boulevard, Coates, Milton"), "805 Santa Maria Boulevard, Coates")
        self.assertEqual(dp.short_label("Grand Bend, Lambton Shores, Ontario"), "Grand Bend, Lambton Shores")
        self.assertEqual(dp.abbr_road("King Street West"), "King St W")

    def test_fmt_clock_day(self):   # F11
        self.assertEqual(dc.fmt_clock(dt.datetime(2026, 10, 15, 1, 10), dt.datetime(2026, 10, 14, 9)),
                         "01:10 (Thu 15 Oct)")
        self.assertEqual(dc.fmt_clock(dt.datetime(2026, 10, 14, 1, 10), dt.date(2026, 10, 14)), "01:10")
        self.assertEqual(dc.fmt_clock(dt.datetime(2026, 10, 15, 1, 10)), "01:10")


class LinkChoiceTests(unittest.TestCase):   # F2
    def test_primary_link_follows_toll_free(self):
        res, text = run(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2026-10-14 07:30", "--park-and-ride", "off")
        self.assertEqual(res["recommended"], "B")
        self.assertIn("avoid=tolls", res["links"]["google"])
        self.assertNotIn("avoid=tolls", res["links"]["google_alt"])
        self.assertIn("| with tolls https://", text)

    def test_alternative_waypoint(self):
        a = {"poly": line(MILTON, MARKHAM, 60)}
        mid = ((MILTON[0] + MARKHAM[0]) / 2 + 0.05, (MILTON[1] + MARKHAM[1]) / 2)
        c = {"poly": line(MILTON, mid, 30) + line(mid, MARKHAM, 30)[1:], "label": "alternative"}
        p = dp.alt_waypoint(c, [a, c])
        self.assertIsNotNone(p)
        self.assertLess(th.haversine_m(p[0], p[1], mid[0], mid[1]), 1500)
        self.assertIsNone(dp.alt_waypoint({"poly": a["poly"]}, [a, {"poly": a["poly"]}]))
        self.assertIn("waypoints=", dp.google_link(MILTON, MARKHAM, True, p))


class BandTests(unittest.TestCase):   # UX9
    def test_band_window(self):
        self.assertEqual(dp.band_text({"band_start": "21:00", "day_type": "weekend"}), "weekend 21:00 to 08:30 band")
        self.assertEqual(dp.band_text({"band_start": "07:00", "day_type": "weekday"}), "weekday 07:00 to 09:30 band")
        self.assertEqual(dp.band_text({"band_start": "21:00", "day_type": "weekday", "band_end": "05:00"}),
                         "weekday 21:00 to 05:00 band")


class WeatherAlongTests(unittest.TestCase):   # UX4, UX7
    def reply(self, temps, rains, snows, day="2026-10-12"):
        out = []
        for t, r, s in zip(temps, rains, snows):
            times = ["%sT%02d:00" % (day, h) for h in range(24)]
            out.append({"hourly": {"time": times, "temperature_2m": [t] * 24, "precipitation": [r] * 24,
                                   "snowfall": [s] * 24}})
        return out

    def pts(self, day=12):
        w = dt.datetime(2026, 10, day, 9, 0)
        return [(43.5, -80.0, w), (43.8, -80.5, w + dt.timedelta(hours=1)), (44.2, -81.0, w + dt.timedelta(hours=2))]

    def test_mean_over_points(self):
        w = []
        with mock.patch.object(dc, "fetch_json", return_value=self.reply([9, 9, 9], [1.2, 0, 0], [0, 0, 0])) as f:
            out = dp.weather_points(self.pts(), "America/Toronto", w, today=dt.date(2026, 10, 10))
        self.assertEqual(f.call_count, 1)                   # one request for every point
        self.assertAlmostEqual(out["mult"], (1.15 + 1 + 1) / 3, places=3)
        self.assertIn("on part of the route", out["text"])
        self.assertTrue(any("applied to the drive time" in x for x in w))

    def test_wet_snow_above_freezing_is_not_snow(self):
        with mock.patch.object(dc, "fetch_json", return_value=self.reply([3.5] * 3, [0.1] * 3, [0.21] * 3)):
            out = dp.weather_points(self.pts(), "America/Toronto", [], today=dt.date(2026, 10, 10))
        self.assertEqual(out["mult"], 1.0)
        self.assertNotEqual(out["kind"], "snow")

    def test_far_forecast_not_applied(self):
        w = []
        rep = self.reply([-1] * 3, [0.4] * 3, [0.3] * 3, day="2026-10-17")
        with mock.patch.object(dc, "fetch_json", return_value=rep):
            out = dp.weather_points(self.pts(17), "America/Toronto", w, today=dt.date(2026, 10, 10))
        self.assertEqual(out["mult"], 1.0)
        self.assertIn("not added; forecast is 7 days out", out["text"])
        self.assertTrue(any("7 days out" in x for x in w))

    def test_past_date(self):   # UX7
        out = dp.weather_points([(43.6, -79.6, dt.datetime(2025, 10, 1, 9))], "America/Toronto", [],
                                today=dt.date(2026, 10, 10))
        self.assertIn("date is in the past", out["text"])
        with Env(), at_now(NOW):
            res = dp.plan(args_for(*BASE, "--dest", "%f,%f" % MARKHAM, "--depart", "2025-10-01 09:00",
                                   "--park-and-ride", "off"))
        self.assertTrue(any("in the past (Wed 2025-10-01 09:00); did you mean Fri 2027-10-01 09:00?" in x
                            for x in res["warnings"]))

    def test_return_uses_its_own_weather(self):
        seen = []

        def wp(points, tz, warnings, today=None):
            seen.append(points[0][2])
            mult = 1.0 if len(seen) == 1 else 1.4
            return {"mult": mult, "temp_c": 0, "text": "w%d" % len(seen), "kind": "x", "forecast": False}
        with Env(), at_now(NOW), mock.patch.object(dp, "weather_points", side_effect=wp):
            res = dp.plan(args_for("--origin", "%f,%f" % MILTON, "--incidents", "off", "--dest", "%f,%f" % GRAND_BEND,
                                   "--depart", "2026-10-12 09:00", "--stay-hours", "5", "--round-trip"))
        a = res["routes"][0]
        self.assertGreaterEqual(len(seen), 2)
        self.assertGreater(seen[1], dt.datetime(2026, 10, 12, 15))   # looked up at the return hour
        back_dep = a["return"]["depart"]
        self.assertAlmostEqual(a["return"]["typical_s"], a["base_s"] * peak(back_dep) * 1.4, places=1)
        self.assertAlmostEqual(a["typical_s"], a["base_s"] * peak(a["depart"]), places=1)
        self.assertEqual(a["return"]["weather"]["text"], "w2")


class IncidentLegTests(unittest.TestCase):   # K13
    def test_route_and_leg_named(self):
        item = {"source": "City of Toronto", "road": "Gardiner", "name": "Gardiner W", "type": "ROAD_CLOSED",
                "what": "road closed", "impact": "High", "until": None, "legs": ["back"], "description": "x",
                "closed": True}
        with Env(), at_now(NOW), mock.patch.object(dp, "toronto_incidents", return_value=[item]) as ti:
            res = dp.plan(args_for("--origin", "%f,%f" % MILTON, "--weather", "clear", "--dest", "%f,%f" % UNION,
                                   "--depart", "2026-10-14 07:30", "--round-trip", "--park-and-ride", "off"))
            text = dp.render(res)
        legs = ti.call_args[0][0]
        self.assertEqual([lg[0] for lg in legs], ["out", "back"])
        self.assertIn("INCIDENTS  on route %s (the recommended route), there and back:" % res["recommended"], text)
        self.assertIn("(road closed, impact High, on the way back)", text)



class TradeOffWordingTests(unittest.TestCase):
    """Arrive-by minutes are a later leave-by (the allow time), not a shorter typical drive; say both."""

    def items(self):
        a = {"letter": "A", "route": {"has_tolls": True, "etr": [{"km": 11}], "label": "fastest"},
             "money": 25.05, "time_s": 104 * 60, "typical_s": 63 * 60}
        b = {"letter": "B", "route": {"has_tolls": False, "etr": [], "label": "toll-free"},
             "money": 7.10, "time_s": 115 * 60, "typical_s": 69 * 60}
        return [a, b]

    def test_arrive_by_says_leave_later_and_typical_difference(self):
        tr = dp.trade_off(self.items(), 30.0, False, arrive_by=True)
        self.assertIn("The 407 lets you leave 11 min later (typical drive about 6 min shorter) for $17.95 ($98/h).",
                      tr["text"])
        self.assertEqual(tr["saved_min"], 11)
        self.assertEqual(tr["typical_saved_min"], 6)
        self.assertNotIn("saves", tr["text"])

    def test_depart_mode_keeps_saves_wording(self):
        items = self.items()
        for it in items:
            it["time_s"] = it["typical_s"]
        tr = dp.trade_off(items, 30.0, False)
        self.assertIn("The 407 saves 6 min for $17.95", tr["text"])
        self.assertNotIn("typical_saved_min", tr)

    def test_arrive_by_round_trip_names_both_parts(self):
        tr = dp.trade_off(self.items(), 30.0, False, round_trip=True, arrive_by=True)
        self.assertIn("there and back (a later leave-by plus the drive back).", tr["text"])

    def test_same_typical_drive(self):
        items = self.items()
        items[1]["typical_s"] = 63 * 60 + 20
        tr = dp.trade_off(items, 30.0, False, arrive_by=True)
        self.assertIn("(typical drive about the same)", tr["text"])


if __name__ == "__main__":
    unittest.main()
