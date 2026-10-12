"""Offline tests for the INTEGRATE fixes across the driving modules (parking pricing and ranking, EV charger
reuse and estimates, incident direction, output helpers, EV options implying --vehicle ev).

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
import math
import os
import sys
import types
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import drive_plan as dp  # noqa: E402
import ev_charging as ev  # noqa: E402
import parking as pk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_drive_plan as tdp  # noqa: E402  (fakes for drive_route, parking and the network)

MONTHS = [36, 30, 25, 30, 40, 40, 40, 40, 40, 30, 30, 40]


def lot(name, cost, walk, far=False, fits=True, conf="curated", kind="lot"):
    return {"name": name, "cost": cost, "fits_stay": fits, "kind": kind, "confidence": conf, "walk_m": walk,
            "_far": far}


class PricingTest(unittest.TestCase):
    def test_flat_by_month(self):
        rule = {"flat": 40.0, "flat_by_month": MONTHS, "max_stay_min": 1080}
        cost, basis, fits = pk.price_rule(rule, dt.datetime(2026, 10, 17, 11, 0), 240)
        self.assertEqual(cost, 30.0)
        self.assertIn("October", basis)
        self.assertTrue(fits)
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 7, 4, 11, 0), 240)[0], 40.0)

    def test_evening_flat_with_flat(self):
        rule = {"flat": 18.0, "evening_flat": 15.0, "evening_from": "18:00"}
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 10, 14, 19, 0), 120)[0], 15.0)
        self.assertEqual(pk.price_rule(rule, dt.datetime(2026, 10, 14, 10, 0), 120)[0], 18.0)

    def test_niagara_lots_use_month_prices(self):
        places = {p["id"]: p for p in pk.load_places()}
        a = next(x for x in places["niagara-falls"]["lots"] if x["name"].startswith("Niagara Parks Lot A"))
        self.assertEqual(a["rule"]["flat_by_month"][9], 30)

    def test_lot_names_do_not_repeat_free(self):
        for p in pk.load_places():
            for x in p.get("lots") or []:
                self.assertNotIn("(free", x["name"], x["name"])


class RankTest(unittest.TestCase):
    def test_walkable_lots_rank_before_far_curated_ones(self):
        out = pk.rank([lot("Shuttle lot C", 10.0, 2131, far=True), lot("Lot A", 30.0, 418)])
        self.assertEqual([x["name"] for x in out], ["Lot A", "Shuttle lot C"])

    def test_far_only_still_ranked_by_price(self):
        out = pk.rank([lot("Trailhead", 21.0, 9000, far=True, kind="park"),
                       lot("Visitor Centre", 0.0, 5000, far=True, kind="park")])
        self.assertEqual(out[0]["name"], "Visitor Centre")


def line(km, n=200):
    k = 111.32 * 0.7193398
    return [(44.0, -80.0 + (km / k) * i / (n - 1)) for i in range(n)]


class EvTest(unittest.TestCase):
    def test_reused_station_list_makes_no_call(self):
        car = ev.car_profile("equinox")
        boom = mock.Mock(side_effect=AssertionError("no network expected"))
        w = []
        with mock.patch.object(dc, "fetch", boom), mock.patch.object(ev, "log"):
            r = ev.plan_charging(line(420), 420, dt.datetime(2026, 10, 17, 9), car, 0.8, 15, w,
                                 {"stations": [], "stations_source": "NLR test", "hwy_share": 0.85})
        urls = [c.args[0] for c in boom.call_args_list]
        self.assertFalse(any("nearby-route" in u for u in urls), urls)   # only the destination search may run
        self.assertEqual(r["source"], "NLR test")
        self.assertFalse(r["feasible"])   # a real (empty) list: no charger exists, not an estimate

    def test_estimate_when_every_source_fails(self):
        car = ev.car_profile("ioniq5")

        def down(url, *a, **kw):
            raise urllib.error.URLError("down")
        w = []
        with mock.patch.object(dc, "fetch", side_effect=down), mock.patch.object(ev, "log"), \
                mock.patch.object(ev.time, "sleep"):
            r = ev.plan_charging(line(450), 450, dt.datetime(2026, 10, 16, 8), car, 0.8, 11, w, {"hwy_share": 0.9})
        self.assertTrue(r["estimated"])
        self.assertTrue(r["feasible"])
        self.assertGreaterEqual(len(r["stops"]), 1)
        self.assertGreaterEqual(r["arrive_soc"], 0.15)
        self.assertTrue(all(s["max_kw"] == 100 for s in r["stops"]))
        self.assertGreater(r["extra_s"], 0)
        self.assertNotIn("_stations", r)


class IncidentTest(unittest.TestCase):
    def test_ramp_direction_letter(self):
        self.assertEqual(dp.closure_direction("Park Lawn Gardiner W Ramp 15 m Southwest of Park Lawn Rd",
                                              "ramp to the Gardiner Westbound; Eastbound off ramp"), "westbound")
        self.assertIsNone(dp.closure_direction("Eastern Av E Dvp N Ramp 34 m East", ""))
        self.assertEqual(dp.closure_direction("Lake Shore Blvd", "eastbound lanes closed"), "eastbound")
        self.assertIsNone(dp.closure_direction("Lake Shore Blvd", "eastbound and westbound lanes"))

    def test_heading(self):
        east = [(43.627, -79.50 + i * 0.001) for i in range(30)]
        cum = dc.cum_dist(east)
        pts = [(43.6271, -79.488), (43.6270, -79.489)]
        self.assertFalse(dp.heading_matches(east, cum, pts, "westbound"))
        self.assertTrue(dp.heading_matches(east, cum, pts, "eastbound"))
        self.assertTrue(dp.heading_matches(east, cum, pts, "northbound"))


class OutputTest(unittest.TestCase):
    def test_clip_at_word(self):
        self.assertEqual(dp.clip("one two three four", 12), "one two...")
        self.assertEqual(dp.clip("short", 12), "short")

    def test_group_rules(self):
        g = dp.group_rules(["Grand Bend: a", "Grand Bend: b", "Posted signs: c", "plain"])
        self.assertEqual(g, [("Grand Bend", ["a", "b"]), (None, ["Posted signs: c"]), (None, ["plain"])])

    def test_ev_options_imply_ev(self):
        args = dp.build_parser().parse_args(["--origin", "43.5,-79.8", "--dest", "43.0,-79.0", "--ev-model", "ioniq5"])
        captured = {}

        def stop(*a, **kw):
            captured["vehicle"] = args.vehicle
            raise RuntimeError("stop here")
        with mock.patch.object(dp, "_mod", side_effect=stop), \
                mock.patch.object(dp.th, "load_regions", return_value=[]):
            with self.assertRaises(RuntimeError):
                dp.plan(args)
        self.assertEqual(captured["vehicle"], "ev")


class RampAndCrossingTest(unittest.TestCase):   # F17, UX11
    def feed(self, recs):
        import json
        return json.dumps({"Closure": recs}).encode("utf-8")

    def rec(self, i, pts, name, typ="ROAD_CLOSED", impact="High", both=False):
        lat, lon = pts[len(pts) // 2]
        return {"id": "R%d" % i, "road": name, "name": name, "latitude": str(lat), "longitude": str(lon),
                "type": typ, "maxImpact": impact, "startTime": "0", "endTime": "0", "scheduleEveryday": None,
                "directionsAffected": "BOTH_DIRECTIONS" if both else "ONE_DIRECTION", "description": "",
                "geoPolyline": ",".join("[%f,%f]" % (p[1], p[0]) for p in pts)}

    def run_inc(self, poly, recs):
        start = dt.datetime(2026, 10, 14, 17, 0)
        with mock.patch.object(dc, "fetch", return_value=self.feed(recs)):
            return dp.toronto_incidents([("back", poly, start, start + dt.timedelta(minutes=40))],
                                        "America/Toronto", None, [])

    def west(self):
        return [(43.627, -79.47 - i * 0.001) for i in range(31)]   # westbound mainline

    def test_ramp_beside_route(self):
        ramp = [(43.6270 + k * 0.00015, -79.488 + k * 0.0003) for k in range(7)]   # 176 m, peels off the mainline
        out = self.run_inc(self.west(), [self.rec(1, ramp, "Park Lawn Gardiner W Ramp")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["what"], "ramp closed beside your route")
        self.assertFalse(out[0]["closed"])
        self.assertEqual(out[0]["legs"], ["back"])

    def test_ramp_on_route(self):
        ramp = [(43.627, -79.480 - k * 0.0003) for k in range(11)]   # the route drives it
        out = self.run_inc(self.west(), [self.rec(2, ramp, "Park Lawn Gardiner W Ramp")])
        self.assertTrue(out and out[0]["closed"])
        self.assertEqual(out[0]["what"], "road closed")

    def test_crossing_street_rejected(self):
        # a 200 m street at 60 degrees to the route: overlaps the 60 m buffer but heads across it
        k = 0.7224
        dlat, dlon = 0.0018 * math.sin(math.radians(60)), 0.0018 * math.cos(math.radians(60)) / k
        street = [(43.627 - dlat / 2 + dlat * i / 10.0, -79.485 - dlon / 2 + dlon * i / 10.0) for i in range(11)]
        out = self.run_inc(self.west(), [self.rec(3, street, "Leslie St", typ="CONSTRUCTION")])
        self.assertEqual(out, [])
        along = [(43.627, -79.485 + i * 0.0003) for i in range(8)]
        self.assertEqual(len(self.run_inc(self.west(), [self.rec(4, along, "Lake Shore Blvd", typ="CONSTRUCTION")])),
                         1)

    def test_both_directions_skips_heading_filter(self):
        east_only = [(43.627, -79.485 + i * 0.0003) for i in range(8)]
        r = self.rec(5, east_only, "Gardiner E Ramp")
        self.assertEqual(self.run_inc(self.west(), [r]), [])                 # an eastbound ramp, westbound route
        r = self.rec(6, east_only, "Lake Shore Blvd", both=True)
        r["description"] = "eastbound lanes and ramps"
        self.assertEqual(len(self.run_inc(self.west(), [r])), 1)


class EvCorridorTest(unittest.TestCase):   # EV-2, EV-8
    def fake_ev(self, calls, ret=None):
        m = types.ModuleType("ev_charging")
        m.car_profile = lambda *a, **k: {"name": "Test EV", "connectors": {"CCS"}}

        def plan_charging(poly, km, dep, car, soc, t, w, opts=None):
            opts = opts or {}
            calls.append({"poly": poly, "stations": opts.get("stations"), "soc": soc,
                          "return_trip": opts.get("return_trip")})
            st = opts.get("stations")
            if st is None:
                st = [{"id": "st%d" % len(calls)}]
            if not opts.get("return_trip") and ret is not None:
                return dict(ret, _stations=st, _stations_source="NLR")
            return {"needed": True, "feasible": True, "arrive_soc": 0.2, "extra_s": 1500, "public_cost": 10.91,
                    "kwh_used": 100.0, "kwh_100km": 24.9, "home_energy_cost": 9.0, "notes": [], "dest_chargers": [],
                    "source": "NLR", "stops": [], "return_soc_needed": 1.29, "soc_start": soc,
                    "_stations": st, "_stations_source": "NLR"}
        m.plan_charging = plan_charging
        return m

    def routes(self, o, d):
        mid = ((o[0] + d[0]) / 2 + 0.6, (o[1] + d[1]) / 2)   # about 65 km north of the straight line
        bent = tdp.line(o, mid, 20) + tdp.line(mid, d, 20)[1:]

        def get_routes(o_, d_, depart, opts, warnings, arrive_at=None):
            km = 300.0
            a = tdp.make_route("fastest", o, d, km, km / 95.0 * 3600)
            b = tdp.make_route("toll-free", o, d, km * 1.1, km / 90.0 * 3600)
            b["poly"] = bent
            c = tdp.make_route("alternative", o, d, km * 1.02, km / 92.0 * 3600)
            return [tdp.fake_retime(r, depart, opts) for r in (a, b, c)]
        return get_routes

    def test_divergent_route_gets_its_own_list(self):
        o, d = (43.65, -79.38), (45.42, -75.69)
        calls = []
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": self.fake_ev(calls)}):
            sys.modules["drive_route"].get_routes = self.routes(o, d)
            dp.plan(tdp.args_for("--origin", "%f,%f" % o, "--dest", "%f,%f" % d, "--weather", "clear",
                                 "--incidents", "off", "--depart", "2026-10-16 08:00", "--vehicle", "ev",
                                 "--park-and-ride", "off"))
        self.assertIsNone(calls[0]["stations"])                     # A: its own lookup
        self.assertIsNone(calls[1]["stations"])                     # B leaves A's corridor: its own lookup
        self.assertEqual({s["id"] for s in calls[2]["stations"]}, {"st1", "st2"})   # C reuses the merged list
        self.assertLess(dp._off_share(tdp.line(o, d), [tdp.line(o, d)]), 0.01)

    def test_round_trip_prices_return_charging(self):
        o, d = (43.65, -79.38), (45.42, -75.69)
        calls = []
        ret = {"needed": True, "feasible": True, "arrive_soc": 0.2, "extra_s": 1500, "public_cost": 10.91,
               "kwh_used": 100.0, "kwh_100km": 24.9, "home_energy_cost": 9.0, "notes": [], "dest_chargers": [],
               "source": "NLR", "stops": [], "return_soc_needed": 1.29}
        evm = self.fake_ev(calls, ret)
        orig = evm.plan_charging

        def pc(poly, km, dep, car, soc, t, w, opts=None):
            out = orig(poly, km, dep, car, soc, t, w, opts)
            if not (opts or {}).get("return_trip"):   # the drive back from 20%
                out.update(arrive_soc=0.15, extra_s=4800, public_cost=31.5, home_energy_cost=2.0, return_soc_needed=None,
                           stops=[{"name": "Stop 1", "km_along": 120, "charge_min": 40},
                                  {"name": "Stop 2", "km_along": 300, "charge_min": 35}])
            return out
        evm.plan_charging = pc
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": evm}):
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % o, "--dest", "%f,%f" % d, "--weather", "clear",
                                       "--incidents", "off", "--depart", "2026-10-16 08:00", "--vehicle", "ev",
                                       "--park-and-ride", "off", "--round-trip", "--stay-hours", "3",
                                       "--transponder"))
            text = dp.render(res)
        back_calls = [c for c in calls if not c["return_trip"]]
        self.assertTrue(back_calls)
        self.assertAlmostEqual(back_calls[0]["soc"], 0.2)          # starts from the arrival charge
        a = res["routes"][0]
        self.assertAlmostEqual(a["money"], 19.91 + a["tolls"] + 33.5 + a["return"]["tolls"], places=2)
        self.assertEqual(a["return"]["charge_s"], 4800)
        r = res["arrival"]["return"]
        self.assertEqual(r["charge_min"], 80)
        self.assertIn("drive back from 20%: 2 stops", text)
        self.assertIn("with 80 min of charging", text)


if __name__ == "__main__":
    unittest.main()
