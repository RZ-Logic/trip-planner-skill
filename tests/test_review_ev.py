"""Regression tests for the EV and car lookup review findings (ev-vehicles-*, ext-1, ext-5, ext-13, gem-G1S,
gem-G6A). Offline: every fetch is mocked. Run: python -m unittest tests.test_review_ev -v
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
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import ev_charging as ev  # noqa: E402
import vehicles as V  # noqa: E402
import test_ev as tev  # noqa: E402            line(), nst(), plan_st(), rec()
import test_v098_ev_car as t98  # noqa: E402   dc_at(), run()
import test_drive_plan as tdp  # noqa: E402    Env, args_for, make_route, fake_retime, MILTON, MARKHAM
import test_car_profile as tcp  # noqa: E402   CarCase, fake_ev_charging
dp = tdp.dp


def st_at(poly, f, sid, kw=150.0, ports=4):
    """A normalized DC station at fraction f along poly (straight segments), 0.0005 deg off the line."""
    n = len(poly) - 1
    i = min(int(f * n), n - 1)
    t = f * n - i
    lat = poly[i][0] + (poly[i + 1][0] - poly[i][0]) * t + 0.0005
    lon = poly[i][1] + (poly[i + 1][1] - poly[i][1]) * t
    return {"id": sid, "name": sid, "network_code": "IVY", "lat": lat, "lon": lon, "address": "",
            "units": {"J1772COMBO": (kw, ports)}, "pricing": "", "access_text": "", "dc_num": ports,
            "l2_num": 0, "status": "E", "source": "NLR"}


def real_ev(fracs_for_km):
    """An ev_charging module whose plan_charging is the real one, with stations at fracs_for_km(route_km)."""
    m = types.ModuleType("ev_charging")
    m.car_profile = ev.car_profile
    m.temp_factor = ev.temp_factor

    def pc(poly, km, dep, car, soc, t, w, opts=None):
        o = dict(opts or {})
        o["stations"] = [st_at(poly, f, "S%d" % round(f * 100)) for f in fracs_for_km(km)]
        o["stations_source"] = "NLR test"
        with mock.patch.object(ev, "stations_near", return_value=[]):
            return ev.plan_charging(poly, km, dep, car, soc, t, w, o)
    m.plan_charging = pc
    return m


# --------------------------------------------------------------------------- Batch 1

class TrimBatteryTest(tcp.CarCase):
    """ev-vehicles-1 and -2: a small-battery trim, or an EV with no preset, gets a battery scaled from its own
    NRCan range and rating (the real vehicles.py, bundled index only)."""

    def setUp(self):
        super().setUp()
        sys.modules["vehicles"] = V
        self.nodl = mock.patch.object(V, "_download_year", return_value=False)
        self.nodl.start()

    def tearDown(self):
        self.nodl.stop()
        super().tearDown()

    def plan_car(self, query):
        self.evc.seen[:] = []
        res, text = self.run_plan("--car", query, ev=True)
        return res, text, self.evc.seen[0]

    def test_small_battery_trims_get_a_scaled_battery(self):
        presets = dp._ev_presets()
        for query, preset in (("2023 Tesla Model 3 RWD", "model-3"),
                              ("2020 Tesla Model 3 Standard Range Plus", "model-3"),
                              ("2023 Hyundai IONIQ 5 Standard Range", "ioniq5"),
                              ("2024 Kia EV9 Light", "ev9"),
                              ("2024 Ford Mustang Mach-E Standard Range", "mach-e")):
            with self.subTest(query=query):
                res, text, car = self.plan_car(query)
                self.assertEqual(car["key"], preset)
                self.assertLess(car["battery_kwh"], 0.9 * presets[preset]["battery_kwh"])
                self.assertGreater(car["battery_kwh"], 45)
                label = res["car"]["label"]
                self.assertTrue(any(label in w and "--battery-kwh" in w for w in res["warnings"]), res["warnings"])
                self.assertIn("scaled", self.car_line(text)[0])
                self.assertEqual(res["car"]["battery_scaled"]["preset"], preset)

    def test_long_range_keeps_the_preset_battery(self):
        for query, kwh in (("2023 Tesla Model 3 Long Range", 82.0), ("2024 Kia EV9 Wind", 96.0)):
            with self.subTest(query=query):
                res, text, car = self.plan_car(query)
                self.assertEqual(car["battery_kwh"], kwh)
                self.assertFalse(any("imply" in w for w in res["warnings"]), res["warnings"])
                self.assertNotIn("battery_scaled", res["car"])

    def test_battery_flag_wins_over_the_scaled_battery(self):
        self.evc.seen[:] = []
        res, text = self.run_plan("--car", "2023 Tesla Model 3 RWD", "--battery-kwh", "57.5", ev=True)
        self.assertEqual(self.evc.seen[0]["battery_kwh"], 57.5)
        self.assertNotIn("battery_scaled", res["car"])
        self.assertFalse(any("imply" in w for w in res["warnings"]), res["warnings"])

    def test_ioniq_electric_is_not_an_ioniq_5(self):
        self.assertIsNone(dp.ev_preset_for({"make": "Hyundai", "model": "IONIQ Electric", "year": 2020,
                                            "variant_names": ["IONIQ Electric"]}))
        self.assertEqual(dp.ev_preset_for({"make": "Hyundai", "model": "IONIQ 5 Long Range AWD"}), "ioniq5")
        res, text, car = self.plan_car("2020 Hyundai Ioniq Electric")
        self.assertEqual(car["key"], "generic")
        self.assertTrue(any("no EV preset matches 2020 Hyundai IONIQ Electric" in w for w in res["warnings"]),
                        res["warnings"])
        self.assertGreater(car["battery_kwh"], 30)
        self.assertLess(car["battery_kwh"], 45)


class InfeasibleRouteTest(unittest.TestCase):
    """ext-1: a route whose charging cannot be planned is not recommended when another route's can."""
    O, D = (43.65, -79.38), (45.42, -75.69)

    def plan(self, fracs):
        o, d = self.O, self.D

        def get_routes(o_, d_, depart, opts, warnings, arrive_at=None):
            return [tdp.fake_retime(tdp.make_route("fastest", o, d, 400.0, 4 * 3600), depart, opts),
                    tdp.fake_retime(tdp.make_route("toll-free", o, d, 420.0, 5 * 3600), depart, opts)]
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": real_ev(fracs)}):
            sys.modules["drive_route"].get_routes = get_routes
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % o, "--dest", "%f,%f" % d, "--weather", "clear",
                                       "--incidents", "off", "--depart", "2026-10-16 08:00", "--vehicle", "ev",
                                       "--ev-model", "generic", "--soc", "80", "--park-and-ride", "off"))
            return res, dp.render(res)

    def test_feasible_route_wins(self):
        res, text = self.plan(lambda km: [] if km < 410 else [100 / 420., 200 / 420., 300 / 420.])
        self.assertEqual(res["recommended"], "B")
        self.assertTrue(res["charging"]["feasible"])
        self.assertTrue(any("A cannot be driven on the fast chargers found" in w for w in res["warnings"]),
                        res["warnings"])
        # every route infeasible: keep them all, the fastest stays recommended, no such warning
        res, text = self.plan(lambda km: [])
        self.assertEqual(res["recommended"], "A")
        self.assertFalse(any("cannot be driven on the fast chargers" in w for w in res["warnings"]))


class ReturnPlanTest(unittest.TestCase):
    """ext-5, gem-G1S, ev-vehicles-3: when the drive back starts with a destination charge, the plan is made from
    the charge the note advises, that charge reaches the first stop with its own reserve, and the summary carries
    the energy for pricing."""

    def plan(self, ports=4):
        sts = [tev.nst(100, ports=ports), tev.nst(250, ports=ports)]
        r, w = tev.plan_st(sts, 400, ev.car_profile("generic"), 0.9, return_trip=True, near=[])
        return r, r["return_plan"]

    def test_plan_starts_from_the_advised_charge(self):
        r, rp = self.plan()
        self.assertTrue(rp["feasible"] and rp["charge_at_destination"])
        self.assertEqual(rp["start_soc"], r["return_soc_to_charger"])
        self.assertGreaterEqual(rp["stops"][0]["arrive_soc"], 0.15 - 1e-9)
        self.assertLess(rp["stops"][0]["arrive_soc"], 0.20)
        note = [n for n in r["notes"] if n.startswith("Return trip")][0]
        self.assertIn(dc.money(rp["public_cost"]), note)
        self.assertIn("at least %.0f%%" % (r["return_soc_to_charger"] * 100), note)

    def test_single_stall_first_charger_needs_its_reserve(self):
        r, rp = self.plan(ports=1)
        self.assertTrue(rp["feasible"])
        self.assertGreaterEqual(r["return_soc_to_charger"], 0.80 - 0.329 + 0.20 - 1e-9)
        self.assertEqual(rp["start_soc"], r["return_soc_to_charger"])
        self.assertGreaterEqual(rp["stops"][0]["arrive_soc"], 0.20 - 1e-9)

    def test_summary_carries_energy(self):
        r, rp = self.plan()
        for k in ("soc_start", "kwh_used", "home_energy_cost"):
            self.assertIn(k, rp)
        self.assertEqual(rp["soc_start"], rp["start_soc"])
        self.assertGreater(rp["kwh_used"], 0)
        self.assertGreater(rp["home_energy_cost"], 0)


class ReturnRenderTest(unittest.TestCase):
    """ev-vehicles-3: the CHARGING 'drive back from' line uses the return plan's own starting charge, and the
    drive back's money counts its battery energy (home rate), not only the public charging."""
    KINGSTON = (44.2312, -76.4860)

    def test_drive_back_line_and_money(self):
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": real_ev(lambda km: [0.3, 0.6])}):
            res = dp.plan(tdp.args_for(
                "--origin", "%f,%f" % tdp.MILTON, "--dest", "%f,%f" % self.KINGSTON, "--weather", "clear",
                "--incidents", "off", "--depart", "2026-10-16 08:00", "--vehicle", "ev", "--ev-model", "generic",
                "--soc", "90", "--park-and-ride", "off", "--round-trip", "--stay-hours", "3", "--transponder"))
            text = dp.render(res)
        cr = res["charging_return"]
        self.assertTrue(cr and cr["charge_at_destination"], cr)
        self.assertNotIn("drive back from 20%", text)
        self.assertIn("drive back from %d%% (charged at the destination)" % round(cr["start_soc"] * 100), text)
        checked = 0
        for row in res["routes"]:
            back = row.get("return") or {}
            rp = back.get("charging")
            if not rp:
                continue
            checked += 1
            self.assertGreater(rp["home_energy_cost"], 0)
            self.assertAlmostEqual(back["energy_cost"], rp["public_cost"] + rp["home_energy_cost"], places=2)
            self.assertGreater(back["energy_cost"], rp["public_cost"])
        self.assertGreater(checked, 0)


# --------------------------------------------------------------------------- Batch 3

class StretchChargeTest(unittest.TestCase):
    """ev-vehicles-5: a gap after a stop that 80% cannot bridge is planned with stops charging above 80% (up to
    ev.json max_soc_stretch) and a note; when even that fails the advice is Level 2, since more charge at departure
    would not help after a stop."""

    def plan(self, kms, temp=15.0, soc=0.9):
        return tev.plan_st([tev.nst(k) for k in kms], 400, ev.car_profile("generic"), soc, temp=temp, near=[])

    def test_gap_after_a_stop_is_bridged_above_80(self):
        r, w = self.plan([100, 240, 370], temp=-15)
        self.assertTrue(r["feasible"], w)
        self.assertGreater(r["stops"][0]["depart_soc"], 0.8)
        self.assertLessEqual(r["stops"][0]["depart_soc"], 0.95 + 1e-9)
        self.assertTrue(any("charge limit" in n and "slower" in n for n in r["notes"]), r["notes"])
        self.assertFalse(any("leave with more charge" in x for x in w), w)

    def test_mild_gap_needs_only_a_little_more(self):
        r, w = self.plan([100, 280], temp=5)
        self.assertTrue(r["feasible"], w)
        self.assertGreater(r["stops"][0]["depart_soc"], 0.80)
        self.assertLess(r["stops"][0]["depart_soc"], 0.85)

    def test_unbridgeable_gap_after_a_stop_does_not_say_leave_with_more(self):
        r, w = self.plan([100], temp=5)
        self.assertFalse(r["feasible"])
        self.assertTrue(any("Level 2" in x for x in w), w)
        self.assertFalse(any("leave with more charge" in x for x in w), w)

    def test_gap_at_the_origin_still_says_leave_with_more(self):
        r, w = self.plan([])
        self.assertFalse(r["feasible"])
        self.assertTrue(any("leave with more charge" in x for x in w), w)

    def test_feasible_80_plan_is_unchanged(self):
        r, w = self.plan([100, 200, 300])
        self.assertTrue(r["feasible"], w)
        self.assertTrue(all(s["depart_soc"] <= 0.8 + 1e-9 for s in r["stops"]), r["stops"])
        self.assertFalse(any("charge limit" in n for n in r["notes"]), r["notes"])

    def test_charging_header_after_a_stop_says_level_2(self):
        o, d = InfeasibleRouteTest.O, InfeasibleRouteTest.D

        def get_routes(o_, d_, depart, opts, warnings, arrive_at=None):
            return [tdp.fake_retime(tdp.make_route("fastest", o, d, 400.0, 4 * 3600), depart, opts)]

        def text_for(fracs):
            with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": real_ev(fracs)}):
                sys.modules["drive_route"].get_routes = get_routes
                res = dp.plan(tdp.args_for("--origin", "%f,%f" % o, "--dest", "%f,%f" % d, "--weather", "clear",
                                           "--incidents", "off", "--depart", "2026-10-16 08:00", "--vehicle", "ev",
                                           "--ev-model", "generic", "--soc", "90", "--park-and-ride", "off"))
                return res, dp.render(res)
        res, text = text_for(lambda km: [100 / 400.])
        head = [x for x in text.splitlines() if x.startswith("CHARGING")][0]
        self.assertFalse(res["charging"]["feasible"])
        self.assertIn("1 stop(s) planned", head)
        self.assertIn("plan Level 2 stops", head)
        self.assertNotIn("leave with more charge", head)
        res, text = text_for(lambda km: [])   # no stop: more charge at departure is still the first remedy
        head = [x for x in text.splitlines() if x.startswith("CHARGING")][0]
        self.assertIn("leave with more charge", head)


class StartChargeTest(tcp.CarCase):
    """ev-vehicles-6: the CHARGING header shows the starting charge, marked assumed (with a WARNING) when --soc is
    not given and the 90% default was used."""

    def head(self, text):
        return [x for x in text.splitlines() if x.startswith("CHARGING")][0]

    def soc_warnings(self, res):
        return [w for w in res["warnings"] if "--soc" in w]

    def test_default_start_is_marked_assumed(self):
        res, text = self.run_plan("--vehicle", "ev", ev=True)
        self.assertIn("from 90% (assumed; pass --soc)", self.head(text))
        self.assertTrue(any("90%" in w for w in self.soc_warnings(res)), res["warnings"])

    def test_given_start_is_shown_plain(self):
        res, text = self.run_plan("--vehicle", "ev", "--soc", "70", ev=True)
        self.assertIn("from 70%", self.head(text))
        self.assertNotIn("assumed", self.head(text))
        self.assertEqual(self.soc_warnings(res), [])

    def test_stop_and_infeasible_headers_show_the_start(self):
        stop = {"name": "S1", "network": "Ivy", "max_kw": 150.0, "ports": 4, "km_along": 20.0, "arrive_soc": 0.2,
                "depart_soc": 0.6, "charge_min": 20.0, "detour_min": 2, "cost": 10.0, "price_text": "Ivy"}

        def stub(feasible):
            def pc(poly, km, dep, car, soc, t, w, opts=None):
                return {"needed": True, "feasible": feasible, "stops": [dict(stop)], "arrive_soc": 0.3,
                        "kwh_used": 10.0, "kwh_100km": 18.0, "public_cost": 10.0, "home_energy_cost": 1.0,
                        "extra_s": 1320.0, "source": "test chargers"}
            return pc
        self.evc.plan_charging = stub(True)
        res, text = self.run_plan("--vehicle", "ev", ev=True)
        self.assertRegex(self.head(text), r"1 stop from 90% \(assumed; pass --soc\), .* arrive with 30%")
        self.evc.plan_charging = stub(False)
        res, text = self.run_plan("--vehicle", "ev", "--soc", "55", ev=True)
        self.assertIn("1 stop(s) planned from 55%, but no reachable", self.head(text))


if __name__ == "__main__":
    unittest.main()
