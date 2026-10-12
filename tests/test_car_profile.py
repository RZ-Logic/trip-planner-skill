"""Offline tests for the car in drive_plan.py: --car (NRCan lookup), the saved car (--save-car, --forget-car,
--no-saved-car), the precedence of the car flags, EV via --car, premium and diesel prices, the CAR line and the
JSON car block. vehicles.py is faked here (its own tests cover the NRCan files); routing and parking reuse the
fakes in test_drive_plan.

Run: python -m unittest tests.test_car_profile -v
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
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import test_drive_plan as tdp  # noqa: E402

dp, dc = tdp.dp, tdp.dc
SCRIPTS = os.path.join(os.path.dirname(HERE), "plugins", "trip-planner", "skills", "trip-planner", "scripts")

FUEL_CSV = tdp.FUEL_CSV + (
    "2026-10-05,210.1,215.2,214.0,1,1,1,1,1,1,1,1,1,1,1,214.5,214.9,212.0,Premium Gasoline,Super,,,,,,\n")

CARS = {
    "2022 toyota corolla": {
        "query": "2022 Toyota Corolla", "label": "2022 Toyota Corolla", "year": 2022, "make": "Toyota",
        "model": "Corolla", "class": "Compact", "kind": "gas", "fuel": "regular", "unit": "L/100 km",
        "city": 7.8, "hwy": 5.9, "comb": 6.9, "variants": 4, "range": [6.5, 7.3], "picked": "median of 4 variants",
        "alternatives": ["2022 Toyota Corolla Hybrid"], "range_km": None,
        "source": "NRCan Fuel Consumption Ratings 2022", "source_url": "https://open.canada.ca/x.csv",
        "confidence": "high"},
    "2020 bmw 330i": {
        "query": "2020 BMW 330i", "label": "2020 BMW 330i", "year": 2020, "make": "BMW", "model": "330i",
        "class": "Compact", "kind": "gas", "fuel": "premium", "unit": "L/100 km", "city": 9.6, "hwy": 6.6,
        "comb": 8.3, "variants": 1, "picked": "one variant", "alternatives": [], "source": "NRCan 2020", "source_url": "u",
        "confidence": "high"},
    "2019 vw golf tdi": {
        "query": "2019 VW Golf TDI", "label": "2019 Volkswagen Golf TDI", "year": 2019, "make": "Volkswagen",
        "model": "Golf TDI", "kind": "diesel", "fuel": "diesel", "unit": "L/100 km", "city": 6.9, "hwy": 5.0,
        "comb": 6.0, "variants": 2, "alternatives": [], "source": "NRCan 2019", "source_url": "u"},
    "2024 tesla model y": {
        "query": "2024 Tesla Model Y", "label": "2024 Tesla Model Y Long Range AWD", "year": 2024, "make": "Tesla",
        "model": "Model Y Long Range AWD", "kind": "ev", "fuel": "electricity", "unit": "kWh/100 km",
        "city": 16.9, "hwy": 19.5, "comb": 18.1, "variants": 3, "alternatives": [], "range_km": 533,
        "source": "NRCan battery-electric ratings 2024", "source_url": "u", "confidence": "high"},
    "2023 polestar 2": {
        "query": "2023 Polestar 2", "label": "2023 Polestar 2 Long range Dual motor", "year": 2023,
        "make": "Polestar", "model": "2 Long range Dual motor", "kind": "ev", "fuel": "electricity",
        "unit": "kWh/100 km", "city": 20.4, "hwy": 23.1, "comb": 21.6, "variants": 1, "alternatives": [],
        "range_km": 434, "source": "NRCan battery-electric ratings 2023", "source_url": "u"},
}


def fake_vehicles(calls):
    """A stand-in for vehicles.py with the agreed API, keeping the profile under TRIP_PLANNER_CONFIG."""
    m = types.ModuleType("vehicles")

    def profile_path():
        base = os.environ["TRIP_PLANNER_CONFIG"]
        return base if base.endswith(".json") else os.path.join(base, "car.json")

    def lookup(text, warnings, allow_download=True):
        calls.append(("lookup", text))
        got = CARS.get(" ".join(text.lower().split()))
        if got is None:
            warnings.append("closest: 2022 Toyota Corolla, 2022 Toyota Camry")
            return None
        return json.loads(json.dumps(got))

    def load_profile(warnings=None):
        try:
            with open(profile_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save_profile(car):
        calls.append(("save", car))
        p = profile_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(car, f)
        return p

    def forget_profile():
        try:
            os.remove(profile_path())
            return True
        except OSError:
            return False

    def shown_path(path):   # vehicles.shown_path's rule: the home folder as '~'
        home = os.path.normpath(os.path.expanduser("~"))
        p = os.path.normpath(path)
        return "~" + p[len(home):] if os.path.normcase(p).startswith(os.path.normcase(home) + os.sep) else p

    m.lookup, m.profile_path, m.load_profile, m.save_profile, m.forget_profile, m.shown_path = (
        lookup, profile_path, load_profile, save_profile, forget_profile, shown_path)
    return m


def fake_ev_charging():
    """Real presets (ev_charging.car_profile), charging planned by a stub."""
    import ev_charging as real
    m = types.ModuleType("ev_charging")
    m.car_profile = real.car_profile
    m.seen = []

    def plan_charging(poly, route_km, depart, car, soc, temp_c, warnings, opts=None):
        m.seen.append(car)
        kwh = route_km * car["kwh_100km_hwy"] / 100.0
        return {"needed": False, "feasible": True, "stops": [], "arrive_soc": 0.7, "kwh_used": kwh,
                "kwh_100km": car["kwh_100km_hwy"], "public_cost": 0.0, "home_energy_cost": kwh * 0.14,
                "extra_s": 0.0, "source": "test chargers"}
    m.plan_charging = plan_charging
    return m


def fetch_stub(url, **kw):
    if "fueltypesall" in url:
        return FUEL_CSV.encode("utf-8")
    raise OSError("offline test: %s" % url)


BASE = tdp.BASE + ["--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-14 10:00", "--park-and-ride", "off"]


class CarCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tp-car-")
        self.calls = []
        self.vm = fake_vehicles(self.calls)
        self.evc = fake_ev_charging()
        self.patches = [mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": self.tmp}),
                        mock.patch.dict(sys.modules, {"vehicles": self.vm})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_plan(self, *extra, **kw):
        with tdp.Env():
            mods = {"ev_charging": self.evc} if kw.get("ev") else {}
            with mock.patch.dict(sys.modules, mods), mock.patch.object(dc, "fetch", side_effect=fetch_stub):
                res = dp.plan(tdp.args_for(*(BASE + list(extra))))
                return res, dp.render(res)

    @staticmethod
    def car_line(text):
        lines = text.splitlines()
        i = next(i for i, ln in enumerate(lines) if ln.startswith("CAR "))
        out = [lines[i]]
        for ln in lines[i + 1:]:
            if not ln.startswith(" "):
                break
            out.append(ln.strip())
        return out


class DefaultAndLookupTests(CarCase):
    def test_default_class_line(self):
        res, text = self.run_plan()
        self.assertEqual(self.car_line(text)[0],
                         'CAR        not given, so fuel assumes a small SUV (NRCan 2026 median: 10.0 city / 8.1 hwy '
                         'L/100 km). Pass --car "YEAR MAKE MODEL" for your car\'s figure, or --save-car to keep it')
        heads = [ln.split()[0] for ln in text.splitlines() if ln and not ln.startswith(" ")]
        self.assertEqual(heads[heads.index("ROUTING") + 1], "CAR")
        self.assertEqual(heads[heads.index("CAR") + 1], "ROUTES")
        self.assertEqual(res["car"]["how"], "class default")
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)

    def test_car_class_given(self):
        res, text = self.run_plan("--car-class", "compact")
        self.assertIn("CAR        compact car, as given (NRCan 2026 median: 9.9 city / 7.1 hwy L/100 km)", text)
        self.assertAlmostEqual(res["routes"][0]["energy"]["l100"], 9.9 * 0.2 + 7.1 * 0.8)

    def test_hybrid_default(self):
        res, text = self.run_plan("--vehicle", "hybrid")
        self.assertIn("fuel assumes a typical hybrid (NRCan 2026 typical: 5.2 city / 5.8 hwy L/100 km)", text)

    def test_lookup_line_and_fuel_line(self):
        res, text = self.run_plan("--car", "2022 Toyota Corolla")
        car = self.car_line(text)
        self.assertEqual(car[0], "CAR        2022 Toyota Corolla: 7.8 city / 5.9 hwy L/100 km, regular (NRCan 2022 "
                                 "rating, median of 4 variants)")
        self.assertIn("also matched: 2022 Toyota Corolla Hybrid; pass the exact name", car[1])
        en = res["routes"][0]["energy"]
        self.assertAlmostEqual(en["l100"], 7.8 * 0.2 + 5.9 * 0.8)
        self.assertRegex(en["text"], r"^fuel ~\$[\d.]+ \(\d+ km at 6\.3 L/100 km, \$1\.876/L Toronto West regular, "
                                     r"week of 2026-10-05\)$")
        self.assertIn("car: NRCan 2022 rating, median of 4 variants", res["sources"])
        self.assertEqual(res["car"]["how"], "lookup")

    def test_cold_shown_in_fuel_line(self):
        res, _ = self.run_plan("--car", "2022 Toyota Corolla", "--temp-c", "2")
        self.assertIn("incl. 8% for the cold", res["routes"][0]["energy"]["text"])

    def test_premium_price(self):
        res, text = self.run_plan("--car", "2020 BMW 330i")
        self.assertEqual(res["fuel_price"]["fuel"], "premium")
        self.assertAlmostEqual(res["fuel_price"]["per_l"], 2.152)
        self.assertIn("Toronto West premium", res["routes"][0]["energy"]["text"])
        self.assertIn("2020 BMW 330i: 9.6 city / 6.6 hwy L/100 km, premium (NRCan 2020 rating, one variant)", text)

    def test_diesel_price_without_class_note(self):
        res, text = self.run_plan("--car", "2019 VW Golf TDI")
        self.assertEqual(res["vehicle"], "diesel")
        self.assertAlmostEqual(res["fuel_price"]["per_l"], 2.5)
        self.assertNotIn(dp.costs()["consumption"]["diesel_note"], res["warnings"])
        self.assertIn("diesel (NRCan 2019 rating, median of 2 variants)", text)

    def test_lookup_failure_falls_back(self):
        res, text = self.run_plan("--car", "2022 Toyta Corola")
        w = [x for x in res["warnings"] if x.startswith("--car")]
        self.assertEqual(len(w), 1)
        self.assertIn("closest: 2022 Toyota Corolla, 2022 Toyota Camry", w[0])
        self.assertIn("the small SUV class median instead", w[0])
        self.assertIn("CAR        '2022 Toyta Corola' not found, so fuel assumes a small SUV", text)
        self.assertEqual(res["car"]["how"], "class default")

    def test_missing_vehicles_module(self):
        with mock.patch.dict(sys.modules, {"vehicles": None}):
            res, text = self.run_plan("--car", "2022 Toyota Corolla")
        self.assertTrue(any("vehicles.py is missing" in x for x in res["warnings"]))
        self.assertEqual(res["car"]["how"], "class default")

    def test_lookup_exception_never_crashes(self):
        self.vm.lookup = mock.Mock(side_effect=ValueError("bad csv"))
        res, _ = self.run_plan("--car", "2022 Toyota Corolla")
        self.assertTrue(any("bad csv" in x for x in res["warnings"]))


class PrecedenceTests(CarCase):
    def test_flag_beats_car(self):
        res, text = self.run_plan("--car", "2020 BMW 330i", "--l-per-100km", "7.5")
        self.assertEqual(res["car"]["how"], "flag")
        self.assertAlmostEqual(res["routes"][0]["energy"]["l100"], 7.5)
        self.assertEqual(res["fuel_price"]["fuel"], "premium")   # the fuel still comes from the car
        self.assertIn("CAR        2020 BMW 330i: 7.5 L/100 km (your figure), premium", text)

    def test_flag_alone(self):
        res, text = self.run_plan("--l-per-100km", "6.5")
        self.assertIn("CAR        your car: 6.5 L/100 km (your figure), regular", text)

    def test_car_beats_saved(self):
        self.run_plan("--car", "2020 BMW 330i", "--save-car")
        res, _ = self.run_plan("--car", "2022 Toyota Corolla")
        self.assertEqual((res["car"]["how"], res["car"]["label"]), ("lookup", "2022 Toyota Corolla"))
        self.assertEqual(res["fuel_price"]["fuel"], "regular")

    def test_saved_beats_class(self):
        self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        res, text = self.run_plan("--car-class", "suv")
        self.assertEqual(res["car"]["how"], "saved")
        self.assertAlmostEqual(res["routes"][0]["energy"]["l100"], 7.8 * 0.2 + 5.9 * 0.8)
        self.assertTrue(any("--car-class suv is ignored" in w for w in res["warnings"]))

    def test_failed_lookup_falls_to_saved(self):
        self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        res, text = self.run_plan("--car", "nonsense car")
        self.assertEqual(res["car"]["how"], "saved")
        self.assertTrue(any("using your saved car instead" in w for w in res["warnings"]))
        self.assertIn("--car 'nonsense car' was not found (see WARNINGS)", text)

    def test_saved_ignored_for_other_vehicle(self):
        self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        res, _ = self.run_plan("--vehicle", "hybrid")
        self.assertEqual(res["car"]["how"], "class default")
        self.assertTrue(any("not used for this hybrid run" in w for w in res["warnings"]))

    def test_vehicle_conflicts_with_car(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_plan("--car", "2024 Tesla Model Y", "--vehicle", "diesel")
        self.assertIn("electric", str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            self.run_plan("--car", "2022 Toyota Corolla", "--ev-model", "ioniq5")
        self.assertIn("EV options", str(cm.exception))


class SavedCarTests(CarCase):
    def test_save_then_reuse(self):
        res, text = self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        path = os.path.join(self.tmp, "car.json")
        shown = self.vm.shown_path(path)   # printed and in --json with the home folder as '~' (no account name)
        self.assertEqual(res["car"]["saved_to"], shown)
        self.assertIn("saved as your car in %s" % shown, text)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["label"], "2022 Toyota Corolla")
        n = len([c for c in self.calls if c[0] == "lookup"])
        res, text = self.run_plan()
        self.assertEqual(len([c for c in self.calls if c[0] == "lookup"]), n)   # no new lookup
        self.assertIn("CAR        2022 Toyota Corolla (your saved car): 7.8 city / 5.9 hwy L/100 km, regular "
                      "(NRCan 2022 rating, median of 4 variants)", text)
        self.assertEqual(res["car"]["profile_path"], shown)
        self.assertEqual(json.loads(dp.to_json(res))["car"]["profile_path"], shown)

    def test_never_written_without_save_car(self):
        self.run_plan("--car", "2022 Toyota Corolla")
        self.assertFalse([c for c in self.calls if c[0] == "save"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "car.json")))

    def test_save_flags_only(self):
        self.run_plan("--l-per-100km", "6.1", "--save-car")
        res, text = self.run_plan()
        self.assertEqual(res["car"]["how"], "saved")
        self.assertAlmostEqual(res["routes"][0]["energy"]["l100"], 6.1)
        self.assertIn("CAR        your car: 6.1 L/100 km (your saved figure), regular", text)

    def test_save_vehicle_only(self):
        self.run_plan("--vehicle", "hybrid", "--save-car")
        res, text = self.run_plan()
        self.assertEqual(res["vehicle"], "hybrid")
        self.assertIn("CAR        your saved car has no model, so fuel assumes a typical hybrid", text)

    def test_save_nothing_errors(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_plan("--save-car")
        self.assertIn("nothing to save", str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            self.run_plan("--save-car", "--car", "2022 Toyta Corola")
        self.assertIn("was not found", str(cm.exception))
        self.assertIn("closest: 2022 Toyota Corolla", str(cm.exception))

    def test_no_saved_car(self):
        self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        res, _ = self.run_plan("--no-saved-car")
        self.assertEqual(res["car"]["how"], "class default")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "car.json")))

    def test_forget_with_trip(self):
        self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        res, text = self.run_plan("--forget-car")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "car.json")))
        self.assertEqual(res["car"]["how"], "class default")
        self.assertIn("forgot your saved car", text)

    def test_forget_and_save_only_via_main(self):
        out = io.StringIO()
        with tdp.Env(), mock.patch.object(dc, "fetch", side_effect=fetch_stub), contextlib.redirect_stdout(out):
            self.assertEqual(dp.main(["--car", "2022 Toyota Corolla", "--save-car"]), 0)
        self.assertIn("CAR        2022 Toyota Corolla: 7.8 city", out.getvalue())
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "car.json")))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dp.main(["--forget-car"]), 0)
        self.assertIn("forgot your saved car", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dp.main(["--forget-car"]), 0)
        self.assertIn("no saved car to forget", out.getvalue())

    def test_save_and_forget_together(self):
        with self.assertRaises(SystemExit):
            self.run_plan("--save-car", "--forget-car", "--car", "2022 Toyota Corolla")


class EvTests(CarCase):
    def test_ev_via_car_matches_preset(self):
        res, text = self.run_plan("--car", "2024 Tesla Model Y", ev=True)
        self.assertEqual(res["vehicle"], "ev")
        car = self.evc.seen[0]
        self.assertEqual(car["key"], "model-y")
        self.assertEqual((car["kwh_100km_city"], car["kwh_100km_hwy"]), (16.9, 19.5))
        self.assertEqual(car["battery_kwh"], 79.0)
        self.assertIn("CAR        2024 Tesla Model Y Long Range AWD: 16.9 city / 19.5 hwy kWh/100 km (NRCan 2024 "
                      "rating, median of 3 variants); 79 kWh battery, 250 kW max DC from the model-y preset", text)
        self.assertNotIn("fuel_price", res)
        self.assertIn("CHARGING", text)

    def test_ev_preset_beats_car(self):
        res, _ = self.run_plan("--car", "2024 Tesla Model Y", "--ev-model", "ioniq5", ev=True)
        self.assertEqual(self.evc.seen[0]["key"], "ioniq5")
        self.assertEqual(self.evc.seen[0]["kwh_100km_hwy"], 22.3)
        self.assertEqual(res["car"]["how"], "flag")

    def test_ev_unmatched_uses_generic_with_warning(self):
        res, text = self.run_plan("--car", "2023 Polestar 2", ev=True)
        car = self.evc.seen[0]
        self.assertEqual((car["key"], car["battery_kwh"], car["kwh_100km_hwy"]), ("generic", 70.0, 23.1))
        self.assertTrue(any("no EV preset matches 2023 Polestar 2" in w and "--battery-kwh" in w
                            for w in res["warnings"]))
        res, _ = self.run_plan("--car", "2023 Polestar 2", "--battery-kwh", "75", "--max-dc-kw", "155", ev=True)
        self.assertEqual(self.evc.seen[-1]["battery_kwh"], 75.0)
        self.assertFalse(any("no EV preset" in w for w in res["warnings"]))

    def test_saved_ev(self):
        self.run_plan("--car", "2024 Tesla Model Y", "--save-car", ev=True)
        res, text = self.run_plan(ev=True)
        self.assertEqual(res["vehicle"], "ev")
        self.assertEqual(self.evc.seen[-1]["kwh_100km_hwy"], 19.5)
        self.assertIn("(your saved car)", text)

    def test_saved_ev_preset_flags(self):
        self.run_plan("--ev-model", "niro", "--battery-kwh", "60", "--save-car", ev=True)
        res, text = self.run_plan(ev=True)
        self.assertEqual((self.evc.seen[-1]["key"], self.evc.seen[-1]["battery_kwh"]), ("niro", 60.0))
        self.assertEqual(res["car"]["how"], "saved")
        res, _ = self.run_plan("--ev-model", "ioniq5", ev=True)   # an explicit preset wins
        self.assertEqual((self.evc.seen[-1]["key"], self.evc.seen[-1]["battery_kwh"]), ("ioniq5", 80.0))

    def test_default_ev_line(self):
        res, text = self.run_plan("--vehicle", "ev", ev=True)
        self.assertIn("CAR        not given, so charging assumes a generic EV (70 kWh battery, 150 kW max DC", text)

    def test_preset_names(self):
        f = dp.ev_preset_for
        self.assertEqual(f({"make": "Hyundai", "model": "IONIQ 5 Long Range AWD"}), "ioniq5")
        self.assertIsNone(f({"make": "Hyundai", "model": "IONIQ 6 Long Range AWD"}))
        self.assertEqual(f({"make": "Tesla", "model": "Model Y Long Range AWD"}), "model-y")
        self.assertEqual(f({"make": "Tesla", "model": "Model 3 Long Range AWD"}), "model-3")
        self.assertEqual(f({"make": "Nissan", "model": "LEAF SV PLUS"}), "leaf-plus")
        self.assertEqual(f({"make": "Nissan", "model": "LEAF S"}), "leaf")
        self.assertEqual(f({"make": "Ford", "model": "Mustang Mach-E AWD Extended Range"}), "mach-e")
        self.assertIsNone(f({"make": "Polestar", "model": "2"}))
        self.assertEqual(f({"make": "Tesla", "model": "Model", "variant_names": [
            "Model Y Long Range AWD", "Model Y Performance", "Model Y RWD"]}), "model-y")
        self.assertIsNone(f({"make": "Tesla", "model": "Model", "variant_names": ["Model S", "Model X"]}))


class JsonAndParkRideTests(CarCase):
    def test_json_block(self):
        res, _ = self.run_plan("--car", "2022 Toyota Corolla")
        car = json.loads(dp.to_json(res))["car"]
        for k in ("label", "how", "source", "city", "hwy", "fuel", "alternatives"):
            self.assertIn(k, car)
        self.assertEqual(car["how"], "lookup")
        self.assertNotIn("ev_args", car)
        res, _ = self.run_plan()
        car = json.loads(dp.to_json(res))["car"]
        self.assertEqual((car["how"], car["class"]), ("class default", "small-suv"))

    def test_json_ev_block(self):
        res, _ = self.run_plan("--car", "2024 Tesla Model Y", ev=True)
        car = json.loads(dp.to_json(res))["car"]
        self.assertEqual(car["ev"]["key"], "model-y")
        self.assertEqual(car["unit"], "kWh/100 km")

    def test_park_and_ride_cost_per_km_follows_car(self):
        seen = {}

        def fake_pr(o, d, when, arrive_by, cost_per_km, *a, **kw):
            seen["per_km"] = cost_per_km
            return {"ran": False}
        argv = BASE[:-2]   # without --park-and-ride off
        with tdp.Env(), mock.patch.object(dc, "fetch", side_effect=fetch_stub), \
                mock.patch.object(dp, "park_and_ride", side_effect=fake_pr):
            res = dp.plan(tdp.args_for(*(argv + ["--park-and-ride", "on", "--car", "2020 BMW 330i"])))
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        self.assertAlmostEqual(seen["per_km"], rec["energy"]["cost"] / rec["km"])
        self.assertAlmostEqual(seen["per_km"], rec["energy"]["l100"] / 100.0 * 2.152)


try:
    import vehicles as real_vehicles  # noqa: E402
except Exception:  # noqa: BLE001
    real_vehicles = None


@unittest.skipIf(real_vehicles is None, "vehicles.py not present")
class RealVehiclesTests(CarCase):
    """The real vehicles.py (bundled NRCan index, no download) and its profile file in a temp folder."""

    def setUp(self):
        super().setUp()
        sys.modules["vehicles"] = real_vehicles
        self.nodl = mock.patch.object(real_vehicles, "_download_year", return_value=False)
        self.nodl.start()

    def tearDown(self):
        self.nodl.stop()
        super().tearDown()

    def test_corolla_save_reuse_forget(self):
        res, text = self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        self.assertRegex(self.car_line(text)[0], r"^CAR        2022 Toyota Corolla: \d+\.\d city / \d+\.\d hwy "
                                                 r"L/100 km, regular \(NRCan 2022 rating, median of \d+ variants\)$")
        path = os.path.join(self.tmp, "car.json")
        self.assertEqual(res["car"]["saved_to"], real_vehicles.shown_path(path))
        res, text = self.run_plan()
        self.assertEqual(res["car"]["how"], "saved")
        self.assertIn("2022 Toyota Corolla (your saved car)", text)
        res, text = self.run_plan("--forget-car")
        self.assertFalse(os.path.exists(path))
        self.assertEqual(res["car"]["how"], "class default")

    def test_model_y_is_ev_with_preset(self):
        res, text = self.run_plan("--car", "2024 Tesla Model Y", ev=True)
        self.assertEqual(res["vehicle"], "ev")
        self.assertEqual(self.evc.seen[0]["key"], "model-y")

    def test_unknown_car_warns_with_suggestions(self):
        # a model no fuzzy match fixes ('Corrola' is read as Corolla), so the not-found branch always runs
        res, text = self.run_plan("--car", "2022 Toyota Zzzrolla")
        self.assertEqual(res["car"]["how"], "class default")
        self.assertTrue(any(w.startswith("--car '2022 Toyota Zzzrolla' was not found in the NRCan") and "closest:" in w
                            for w in res["warnings"]), res["warnings"])


if __name__ == "__main__":
    unittest.main()
