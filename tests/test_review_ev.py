"""Regression tests for the EV and car lookup review findings (ev-vehicles-*, ext-1, ext-5, ext-13, gem-G1S,
gem-G6A) and the review follow-ups. Offline: every fetch is mocked. Run: python -m unittest tests.test_review_ev -v
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


class PriceClauseTest(unittest.TestCase):
    """ev-vehicles-7: a listed price with Level 2 and DC rates in one comma or 'and' list gives the DC rate, and an
    idle or overstay fee is never read as the charging rate."""

    def test_dc_clause_and_idle_fees(self):
        for text, want in (("$0.25 per kWh for Level 2, $0.45 per kWh for DC fast", ("per_kwh", 0.45)),
                           ("DC Fast $15/hr, Level 2 $1/hr", ("per_hour", 15.0)),
                           ("$20.00/hr; $0.40/min idle fee after 10 minutes", ("per_hour", 20.0)),
                           ("$0.45 per kWh for DC fast and $0.25 per kWh for Level 2", ("per_kwh", 0.45)),
                           ("$0.45/kWh DCFC, $1.50/hr Level 2", ("per_kwh", 0.45)),
                           # guards: both levels in one clause, and a later rate that is the only one listed
                           ("$0.25/kWh Level 2 DC fast $0.45/kWh", ("per_kwh", 0.45)),
                           ("$0.45 per kWh for DC fast, $0.40/min idle fee after 10 min", ("per_kwh", 0.45)),
                           ("$2/hr after 15 min grace period", ("per_hour", 2.0))):
            with self.subTest(text=text):
                p = ev.parse_price(text)
                self.assertEqual((p["model"], p["rate"]), want)
        self.assertAlmostEqual(ev._price_cost(ev.parse_price("DC Fast $15/hr, Level 2 $1/hr"), 30, 25, 100), 6.25)

    def test_rates_after_the_first_hour_are_kept(self):
        p = ev.parse_price("DC Fast: $5 per hour for first hour, $24 per hour after")
        self.assertEqual((p["model"], p["rate"], p["first"]), ("per_hour", 24.0, 5.0))


# --------------------------------------------------------------------------- Batch 7

class ReturnTemperatureTest(unittest.TestCase):
    """ext-13: with --weather auto the drive back's fuel, and an EV's home-rate energy when the drive back needs no
    charging, are priced at the drive back's own forecast temperature (15 C out, -10 C back here)."""

    def plan(self, dest, *extra, ev_mod=None):
        temps = []

        def weather_points(points, tzname, warnings, today=None):
            temps.append(15.0 if not temps else -10.0)
            return {"mult": 1.0, "temp_c": temps[-1], "text": "t", "kind": "clear", "source": "test",
                    "forecast": True, "days_out": 0}
        mods = {"ev_charging": ev_mod} if ev_mod is not None else {}
        with tdp.Env(), mock.patch.dict(sys.modules, mods), mock.patch.object(dp, "weather_points", weather_points):
            res = dp.plan(tdp.args_for(
                "--origin", "%f,%f" % tdp.MILTON, "--dest", "%f,%f" % dest, "--weather", "auto", "--incidents",
                "off", "--depart", "2026-10-16 08:00", "--park-and-ride", "off", "--round-trip", "--stay-hours", "3",
                "--transponder", *extra))
        self.assertGreaterEqual(len(temps), 2, temps)
        return res

    def test_fuel_back_uses_back_temperature(self):
        res = self.plan(tdp.GRAND_BEND)
        f = dp.cold_factor(-10) / dp.cold_factor(15)
        self.assertGreater(f, 1.0)
        for row in res["routes"]:
            with self.subTest(route=row["letter"]):
                self.assertEqual(row["return"]["weather"]["temp_c"], -10.0)
                self.assertAlmostEqual(row["return"]["energy_cost"], row["energy"]["cost"] * f, places=2)

    def test_ev_home_energy_back_uses_back_temperature(self):
        res = self.plan(tdp.MARKHAM, "--vehicle", "ev", "--ev-model", "ioniq5", "--soc", "90",
                        ev_mod=real_ev(lambda km: []))
        home_rate = dp.costs()["electricity"]["home_rate"]
        f = ev.temp_factor(-10) / ev.temp_factor(15)
        self.assertGreater(f, 1.0)
        rec = [row for row in res["routes"] if row["letter"] == res["recommended"]][0]
        self.assertIsNone(rec["return"]["charging"])
        self.assertAlmostEqual(rec["return"]["energy_cost"], res["charging"]["kwh_used"] * home_rate * f, places=2)


class DriveAndHybridWordTest(unittest.TestCase):
    """ev-vehicles-4: 'RWD' keeps the variants named RWD, and electric Performance, GT and Rally trims (dual
    motor) count as all-wheel drive. ev-vehicles-8: 'PowerBoost' and 'i-FORCE MAX' pick the hybrid, and a word
    that matches no variant (not a trim grade) lowers the confidence. Real bundled index, no download."""

    def setUp(self):
        self.nodl = mock.patch.object(V, "_download_year", return_value=False)
        self.nodl.start()

    def tearDown(self):
        self.nodl.stop()

    def look(self, text):
        w = []
        return V.lookup(text, w), w

    def test_rwd_drops_performance_rows(self):
        got, w = self.look("2023 Tesla Model 3 RWD")
        self.assertEqual(got["variant_names"], ["Model 3 RWD"])
        self.assertEqual((got["city"], got["hwy"]), (15.2, 16.6))
        got, w = self.look("2024 Tesla Model Y RWD")
        self.assertTrue(all("RWD" in n for n in got["variant_names"]), got["variant_names"])
        got, w = self.look("2023 Ford Mustang Mach-E RWD")
        self.assertFalse(any("GT" in n or "Rally" in n for n in got["variant_names"]), got["variant_names"])

    def test_two_wd_word_still_falls_back_to_unmarked_rows(self):
        got, w = self.look("2021 Chevrolet Silverado 2WD")
        self.assertTrue(got["variant_names"])
        self.assertFalse(any("4WD" in n for n in got["variant_names"]), got["variant_names"])

    def test_hybrid_trade_names(self):
        for text, want in (("2025 Toyota Tacoma i-FORCE MAX", (10.5, 9.8)),
                           ("2023 Ford F-150 PowerBoost", (10.3, 10.2)),
                           ("2024 Toyota Tundra i-FORCE MAX", None)):
            with self.subTest(text=text):
                got, w = self.look(text)
                self.assertEqual(got["kind"], "hybrid")
                if want:
                    self.assertEqual((got["city"], got["hwy"]), want)
                self.assertFalse(any(x.startswith("ignored") for x in w), w)

    def test_ignored_word_lowers_confidence(self):
        got, w = self.look("2024 Toyota Tundra i-FORCE")
        self.assertEqual(got["kind"], "gas")
        self.assertTrue(any("ignored 'iforce'" in x for x in w), w)
        self.assertEqual(got["confidence"], "medium")


class AddsNothingTest(unittest.TestCase):
    """ev-vehicles-9: a good charger near the start where the stop adds nothing (reached near the 80% limit) is
    skipped when adding that charge at the next charger is no slower; from a lower start it saves time and is
    kept (the StopThatAddsNothing fixture of test_v098_ev_car, from 0.75 to 0.82)."""

    def stops(self, soc):
        recs = [t98.dc_at(1, "Early 150", 20, 150.0, 4), t98.dc_at(2, "Mid 50", 200, 50.0, 2)]
        r, w = t98.run(350, recs, ev.car_profile("ioniq5"), soc)
        self.assertTrue(r["feasible"], (soc, w))
        return [s["name"] for s in r["stops"]]

    def test_skip_early_fast_stop_when_it_saves_nothing(self):
        for soc in (0.80, 0.82):
            with self.subTest(soc=soc):
                self.assertEqual(self.stops(soc), ["Mid 50"])
        self.assertEqual(self.stops(0.75), ["Early 150", "Mid 50"])


class ParkRideRateTest(tcp.CarCase):
    """gem-G6A: an EV's park-and-ride per-km rate (the short drive to the station) is its energy at the home
    charging rate, not the long drive's public fast charging; the full drive's cost keeps the public charging."""

    def test_ev_rate_excludes_public_charging(self):
        seen = {}

        def fake_pr(o, d, when, arrive_by, cost_per_km, hub_stay_h, one_way, *a, **kw):
            seen["per_km"], seen["one_way"] = cost_per_km, one_way
            return {"ran": False}

        def pc(poly, km, dep, car, soc, t, w, opts=None):
            return {"needed": True, "feasible": True, "stops": [], "arrive_soc": 0.3, "kwh_used": 98.9,
                    "kwh_100km": 22.0, "public_cost": 34.43, "home_energy_cost": 6.86, "extra_s": 1800.0,
                    "source": "test"}
        self.evc.plan_charging = pc
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": self.evc}), \
                mock.patch.object(dc, "fetch", side_effect=tcp.fetch_stub), \
                mock.patch.object(dp, "park_and_ride", side_effect=fake_pr):
            res = dp.plan(tdp.args_for(*(tcp.BASE[:-2] + ["--park-and-ride", "on", "--vehicle", "ev"])))
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        home_rate = dp.costs()["electricity"]["home_rate"]
        self.assertAlmostEqual(seen["per_km"], 98.9 / rec["km"] * home_rate)
        self.assertGreaterEqual(seen["one_way"], 34.43 + 6.86 - 1e-9)


# --------------------------------------------------------------------------- Review follow-up

class ReturnStretchNoteTest(unittest.TestCase):
    """Follow-up to ev-vehicles-5 and ev-vehicles-3: a drive back that can be planned only by charging above 80%
    says so in the Return trip note and under the CHARGING 'drive back from' line (generic EV, 420 km, chargers
    at km 160, 180 and 280, -5 C: the way there stays within 80%, the way back needs about 93% at one stop)."""
    KMS = (160, 180, 280)

    def test_return_note_names_the_charge_above_80(self):
        r, w = tev.plan_st([tev.nst(k, off_deg=0.0) for k in self.KMS], 420, ev.car_profile("generic"), 0.9,
                           temp=-5, near=[], return_trip=True)
        rp = r["return_plan"]
        self.assertTrue(r["feasible"] and rp["feasible"], w)
        self.assertTrue(all(s["depart_soc"] <= 0.8 + 1e-9 for s in r["stops"]), r["stops"])
        hi = [s for s in rp["stops"] if s["depart_soc"] > 0.8 + 1e-9]
        self.assertTrue(hi, rp["stops"])
        note = [n for n in r["notes"] if n.startswith("Return trip")][0]
        self.assertIn("charge limit", note)
        self.assertIn("slower", note)
        for s in hi:
            self.assertIn("%.0f%% at %s" % (s["depart_soc"] * 100, s["name"]), note)
        self.assertIn("charge limit", rp["stretch_note"])

    def test_drive_back_line_names_the_charge_above_80(self):
        o, d = InfeasibleRouteTest.O, InfeasibleRouteTest.D

        def get_routes(o_, d_, depart, opts, warnings, arrive_at=None):
            return [tdp.fake_retime(tdp.make_route("fastest", o, d, 420.0, 4.5 * 3600), depart, opts)]
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": real_ev(lambda km: [k / 420. for k in self.KMS])}):
            sys.modules["drive_route"].get_routes = get_routes
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % o, "--dest", "%f,%f" % d, "--weather", "clear",
                                       "--temp-c", "-5", "--incidents", "off", "--depart", "2026-10-16 08:00",
                                       "--vehicle", "ev", "--ev-model", "generic", "--soc", "90",
                                       "--park-and-ride", "off", "--round-trip", "--stay-hours", "3",
                                       "--transponder"))
            text = dp.render(res)
        cr = res["charging_return"]
        self.assertTrue(cr and cr["feasible"], cr)
        hi = [s for s in cr["stops"] if s["depart_soc"] > 0.8 + 1e-9]
        self.assertTrue(hi, cr["stops"])
        lines = text.splitlines()
        i = [k for k, x in enumerate(lines) if x.strip().startswith("drive back from")][0]
        self.assertIn("charge limit", lines[i + 1])
        for s in hi:
            self.assertIn("%.0f%% at %s" % (s["depart_soc"] * 100, s["name"]), lines[i + 1])


class ReturnNeedTemperatureTest(unittest.TestCase):
    """Follow-up to ext-13: whether a round trip's drive back needs charging is decided at the drive back's own
    forecast temperature (IONIQ 5, 110 km, --soc 90, 3 C out and -7 C back: the 54% on arrival covers the drive
    back at 3 C, not at -7 C with the 20% reserve below 0 C)."""
    O, D = InfeasibleRouteTest.O, InfeasibleRouteTest.D

    def plan(self, t_back, fracs=(0.5,)):
        temps = []

        def weather_points(points, tzname, warnings, today=None):
            temps.append(3.0 if not temps else t_back)
            return {"mult": 1.0, "temp_c": temps[-1], "text": "t", "kind": "clear", "source": "test",
                    "forecast": True, "days_out": 0}

        def get_routes(o_, d_, depart, opts, warnings, arrive_at=None):
            return [tdp.fake_retime(tdp.make_route("fastest", self.O, self.D, 110.0, 3600), depart, opts)]
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": real_ev(lambda km: list(fracs))}),                 mock.patch.object(dp, "weather_points", weather_points):
            sys.modules["drive_route"].get_routes = get_routes
            res = dp.plan(tdp.args_for(
                "--origin", "%f,%f" % self.O, "--dest", "%f,%f" % self.D, "--weather", "auto", "--incidents", "off",
                "--depart", "2026-10-16 08:00", "--park-and-ride", "off", "--round-trip", "--stay-hours", "3",
                "--transponder", "--vehicle", "ev", "--ev-model", "ioniq5", "--soc", "90"))
            text = dp.render(res)
        self.assertGreaterEqual(len(temps), 2, temps)
        return res, text

    def test_colder_drive_back_gets_its_charging(self):
        res, text = self.plan(-7.0)
        ch, cr = res["charging"], res["charging_return"]
        self.assertTrue(cr and cr["feasible"] and cr["stops"], cr)
        self.assertGreaterEqual(cr["arrive_soc"], 0.20 - 0.005)
        self.assertGreater(ch["return_soc_needed"], ch["arrive_soc"])
        self.assertFalse(any("no charging is needed for the return" in n for n in ch["notes"]), ch["notes"])
        self.assertTrue(any(n.startswith("Return trip") and "-7 C" in n for n in ch["notes"]), ch["notes"])
        self.assertIn("leave the destination with %d%%" % round(ch["return_soc_needed"] * 100), text)

    def test_same_temperature_needs_no_charging(self):
        res, text = self.plan(3.0)
        self.assertIsNone(res["charging_return"])
        self.assertTrue(any("no charging is needed for the return" in n for n in res["charging"]["notes"]))


class AwdWordTest(unittest.TestCase):
    """Follow-up to ev-vehicles-4: 'AWD' keeps the variants whose name carries a drive word; only when none does,
    the electric Performance, GT, Rally and Plaid trims count as AWD, together with the family's plain-named rows
    (NRCan's plain 'Model S' is the dual-motor Long Range) but never a row naming a single motor. Real bundled
    index, no download."""

    def setUp(self):
        self.nodl = mock.patch.object(V, "_download_year", return_value=False)
        self.nodl.start()

    def tearDown(self):
        self.nodl.stop()

    def look(self, text):
        w = []
        return V.lookup(text, w), w

    def test_rows_named_awd_win_over_performance_trims(self):
        got, w = self.look("2025 Tesla Model 3 AWD")
        self.assertEqual(got["variant_names"], ["Model 3 Long Range AWD (Import)"])
        self.assertEqual((got["city"], got["hwy"]), (15.3, 16.9))
        got, w = self.look("2024 Tesla Model Y AWD")
        self.assertFalse(any("Performance" in n for n in got["variant_names"]), got["variant_names"])
        self.assertEqual((got["city"], got["hwy"]), (17.2, 18.5))
        got, w = self.look("2025 Ford Mustang Mach-E AWD")
        self.assertFalse(any("GT" in n or "Rally" in n for n in got["variant_names"]), got["variant_names"])
        self.assertEqual((got["city"], got["hwy"]), (20.2, 22.2))

    def test_plain_rows_join_the_dual_motor_trims(self):
        got, w = self.look("2022 Tesla Model S Long Range AWD")
        self.assertIn("Model S", got["variant_names"])
        self.assertEqual(len(got["variant_names"]), 3)
        self.assertEqual((got["city"], got["hwy"], got["range_km"]), (17.6, 18.7, 637))
        self.assertFalse(any("no AWD variant" in x for x in w), w)
        got, w = self.look("2023 Tesla Model X Long Range AWD")
        self.assertIn("Model X", got["variant_names"])
        got, w = self.look("2021 Tesla Model S AWD")
        self.assertIn("Model S Long Range", got["variant_names"])

    def test_single_motor_rows_stay_out(self):
        got, w = self.look("2025 Volvo EX30 AWD")
        self.assertEqual(got["variant_names"], ["EX30 Twin Performance"])
        got, w = self.look("2023 Audi e-tron GT quattro")
        self.assertEqual(got["variant_names"], ["e-tron GT"])
        self.assertFalse(any("no AWD variant" in x for x in w), w)


class SkipNextStopTest(unittest.TestCase):
    """Follow-up to ev-vehicles-9: a good charger's no-gain stop is skipped only for the stop the plan makes next
    anyway, when that stop leaves with the same charge either way, so the skip never makes the plan slower (two
    layouts at 15 C where it did: a single-stall site taken instead, and a later stop no longer reaching the
    destination). The plans are the ones made without the skip."""

    def plan(self, car, km, sts, soc):
        r, w = tev.plan_st([tev.nst(k, kw=kw, ports=p, name="S%d" % k) for k, kw, p in sts], km,
                           ev.car_profile(car), soc, near=[])
        self.assertTrue(r["feasible"], w)
        return [s["name"] for s in r["stops"]], r["extra_s"] / 60.0

    def test_skip_never_makes_the_plan_slower(self):
        names, mins = self.plan("equinox", 700, [(140, 350.0, 1), (281, 62.5, 2), (495, 180.0, 2), (79, 150.0, 2),
                                                 (88, 100.0, 2)], 0.8)
        self.assertEqual(names, ["S79", "S281", "S495"])
        self.assertLess(mins, 93.0)
        names, mins = self.plan("niro", 400, [(144, 100.0, 4), (210, 62.5, 2), (347, 150.0, 8), (48, 350.0, 8)], 0.9)
        self.assertEqual(names, ["S144", "S210"])
        self.assertLess(mins, 48.0)


class PriceParkingClauseTest(unittest.TestCase):
    """Follow-up to ev-vehicles-7: a per-minute or per-hour price whose only clause mentions parking is still the
    charging rate; a separate parking or idle fee never is."""

    def test_time_rate_with_parking_in_its_clause(self):
        for text, want in (("$0.25/min (parking included)", ("per_minute", 0.25)),
                           ("$0.30/min plus parking", ("per_minute", 0.30)),
                           ("$16.00/hr + parking fees", ("per_hour", 16.0)),
                           # guards: a separate parking clause, and an idle fee alone, are not the rate
                           ("$20.00/hr, $2/hr parking", ("per_hour", 20.0)),
                           ("$0.30/min plus parking; $0.40/min idle fee after 10 min", ("per_minute", 0.30))):
            with self.subTest(text=text):
                p = ev.parse_price(text)
                self.assertEqual((p["model"], p["rate"]), want)
        self.assertIsNone(ev.parse_price("$0.40/min idle fee after 10 minutes"))
        self.assertIsNone(ev.parse_price("$2.00 per hour for parking, charging free"))
        st = {"name": "X", "network_code": "FLO", "pricing": "$0.25/min (parking included)",
              "units": {"J1772COMBO": (100.0, 2)}, "country": "CA"}
        cost, text, src = ev.stop_price(st, ev.car_profile("ioniq5"), 30.0, 25.0, 100.0)
        self.assertNotEqual(src, "network estimate")
        self.assertAlmostEqual(cost, 6.25)


if __name__ == "__main__":
    unittest.main()
