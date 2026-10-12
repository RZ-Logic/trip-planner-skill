"""Regression tests for the --car review findings: the NRCan lookup on the bundled index (V1 to V13) and the CAR
line, saved car and EV handling in drive_plan.py (V14, F1 to F9). Offline: lookups never download, routing and
parking are the fakes from test_drive_plan, the profile lives in a temp folder.

Run: python -m unittest tests.test_car_review_fixes -v
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
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import test_car_profile as tcp  # noqa: E402
import test_drive_plan as tdp  # noqa: E402

dp, dc = tdp.dp, tdp.dc
import vehicles as V  # noqa: E402


@unittest.skipUnless(os.path.exists(V.INDEX_PATH), "bundled vehicles index missing")
class BundledCase(unittest.TestCase):
    def setUp(self):
        V.set_index(None)
        self.net = mock.patch.object(dc, "fetch", side_effect=AssertionError("network used"))
        self.net.start()

    def tearDown(self):
        self.net.stop()

    def look(self, text):
        w = []
        return V.lookup(text, w, allow_download=False), w


class TwoDigitYearTests(BundledCase):   # V1
    def test_two_digit_year_is_the_year(self):
        r, w = self.look("19 civic")
        self.assertEqual((r["make"], r["model"], r["year"], r["kind"]), ("Honda", "Civic", 2019, "gas"))
        r, _ = self.look("21 rav4")
        self.assertEqual((r["make"], r["year"], r["kind"]), ("Toyota", 2021, "gas"))

    def test_model_number_is_not_a_year(self):
        r, w = self.look("Mazda CX 30")
        self.assertEqual(r["model"], "CX-30")
        self.assertEqual(r["year"], max(V._search()["years"]))
        r, _ = self.look("2021 Mazda CX 30")
        self.assertEqual((r["model"], r["year"]), ("CX-30", 2021))

    def test_number_never_matches_a_wheel_size(self):
        r, w = self.look("20 inch model y 2024")
        self.assertEqual((r["make"], r["kind"]), ("Tesla", "ev"))
        self.assertNotIn("Rivian", " ".join(w))

    def test_bracket_only_match_refused(self):
        # a loose word must not match '(19" Wheels)' in a BMW name
        V._search()
        q = {"year": None, "make": None, "model": ["19", "zzqx"], "text": "19 zzqx"}
        ids, how, left = V._match_model_in(q, [])
        self.assertEqual(ids, [])

    def test_all_words_ignored_after_loose_match_is_none(self):
        # 'long' only appears inside 'Model Y Long Range'; with every other word unmatched it is no match
        r, w = self.look("2024 Tesla long zzqx")
        self.assertIsNone(r, w)
        self.assertTrue(any("no NRCan rating matched" in x for x in w))


class MiniTests(BundledCase):   # V2
    def test_mini_cooper_is_the_hatch(self):
        for text in ("2017 Mini Cooper", "2017 MINI Cooper"):
            r, w = self.look(text)
            self.assertIsNotNone(r, w)
            self.assertEqual(r["label"], "2017 MINI Cooper")
            self.assertTrue(8.5 <= r["city"] <= 8.8 and 6.2 <= r["hwy"] <= 6.8, r)
            self.assertFalse([n for n in r["variant_names"] if "Clubman" in n or "Countryman" in n])

    def test_countryman_is_its_own_model(self):
        r, w = self.look("2017 MINI Cooper Countryman")
        self.assertEqual((r["year"], r["label"]), (2017, "2017 MINI Cooper Countryman"))
        self.assertTrue(all("Countryman" in n for n in r["variant_names"]))

    def test_cooper_se_is_the_electric_hatch(self):
        r, w = self.look("2020 Mini Cooper SE")
        self.assertEqual((r["kind"], r["city"], r["hwy"]), ("ev", 18.2, 20.9))

    def test_suggestions_not_repeated(self):
        q = V.parse_query("2020 MINI Coopr")
        sug = V._suggest(q)
        names = sug.split(": ", 1)[-1].split(", ")
        self.assertEqual(len(names), len(set(names)), sug)


class KindPreferenceTests(BundledCase):   # V3
    def test_i3_is_the_electric(self):
        r, _ = self.look("2016 BMW i3")
        self.assertEqual((r["kind"], r["label"]), ("ev", "2016 BMW i3 Electric"))
        self.assertIn("2016 BMW i3 REx Plug-in Hybrid", r["alternatives"])
        r, _ = self.look("2016 BMW i3 REx Plug-in Hybrid")
        self.assertEqual(r["kind"], "phev")

    def test_no_doubled_suffix(self):
        r, _ = self.look("2023 Volvo XC60 T8 Recharge")
        self.assertEqual(r["label"], "2023 Volvo XC60 T8 Recharge")

    def test_alternatives_round_trip(self):
        queries = ["2016 BMW i3", "2022 Toyota Corolla", "2023 Volvo XC60", "2022 Lexus UX", "2023 Toyota RAV4",
                   "2018 Jeep Grand Cherokee", "2022 Jeep Grand Cherokee", "2019 Honda Accord", "2020 Mini Cooper",
                   "2018 Ford F-150", "2021 Ram 1500", "2019 Ram 1500 diesel", "2018 Jeep Wrangler Unlimited",
                   "2024 Kia Niro", "2023 Honda CR-V", "2017 Chevrolet Cruze", "2018 f150 ecoboost",
                   "2021 Toyota RAV4", "2022 Chrysler Pacifica"]
        n = 0
        for text in queries:
            r, w = self.look(text)
            self.assertIsNotNone(r, "%s: %s" % (text, w))
            for alt in r["alternatives"]:
                n += 1
                r2, w2 = self.look(alt)
                self.assertIsNotNone(r2, "%s -> %s: %s" % (text, alt, w2))
                self.assertEqual(r2["label"], alt, "%s -> %s: %s" % (text, alt, w2))
        self.assertGreater(n, 30)


class PerformanceTrimTests(BundledCase):   # V4
    def test_grand_cherokee_without_srt_or_trackhawk(self):
        r, _ = self.look("2018 Jeep Grand Cherokee")
        self.assertFalse([n for n in r["variant_names"] if "SRT" in n or "Trackhawk" in n])
        self.assertLess(r["city"], 16.0)
        r, _ = self.look("2018 Jeep Grand Cherokee SRT")
        self.assertTrue(r["variant_names"] and all("SRT" in n for n in r["variant_names"]), r["variant_names"])

    def test_durango_and_ram(self):
        r, _ = self.look("2019 Dodge Durango")
        self.assertFalse([n for n in r["variant_names"] if "SRT" in n])
        r, _ = self.look("2021 Ram 1500")
        self.assertFalse([n for n in r["variant_names"] if "TRX" in n])

    def test_real_trims_stay(self):
        r, _ = self.look("2019 Honda Civic")
        self.assertIn("Civic Hatchback Sport", r["variant_names"] + V.lookup("2019 Honda Civic Hatchback", [],
                                                                             allow_download=False)["variant_names"])


class ExactYearTests(BundledCase):   # V5
    def test_corolla_le_2020(self):
        r, w = self.look("my 2020 Corolla LE")
        self.assertEqual(r["year"], 2020)
        self.assertFalse(any("NRCan has no 2020" in x for x in w))


class PowertrainYearTests(BundledCase):   # V6, V11
    def test_ram_1500_classic_ecodiesel(self):
        for text in ("2019 Ram 1500 diesel", "2019 Ram 1500 ecodiesel"):
            r, w = self.look(text)
            self.assertEqual((r["year"], r["kind"]), (2019, "diesel"), w)
            self.assertTrue(all("Classic" in n for n in r["variant_names"]))
            self.assertIn("no 2019 Ram 1500 diesel in NRCan; used the 2019 1500 Classic", w)
            self.assertIn("Diesel", r["label"])

    def test_powertrain_wording(self):
        r, w = self.look("2016 Chevrolet Cruze diesel")
        self.assertIn("no 2016 Chevrolet Cruze diesel in NRCan; used the 2017 diesel", w)
        self.assertEqual(r["label"], "2017 Chevrolet Cruze Diesel")
        r, w = self.look("2021 jeep grand cherokee 4xe")
        self.assertIn("no 2021 Jeep Grand Cherokee plug-in hybrid in NRCan; used the 2022 plug-in hybrid", w)
        r, w = self.look("2023 Toyota Tacoma hybrid")
        self.assertIn("no 2023 Toyota Tacoma hybrid in NRCan; used the 2024 hybrid", w)


class MisspelledMakeTests(BundledCase):   # V7
    def test_make_typos(self):
        for text, make, model in (("Toyta Corola 2020", "Toyota", "Corolla"), ("2020 Toyta Corolla", "Toyota", "Corolla"),
                                  ("2020 Hond Civic", "Honda", "Civic")):
            r, w = self.look(text)
            self.assertIsNotNone(r, "%s: %s" % (text, w))
            self.assertEqual((r["make"], r["model"], r["year"], r["confidence"]), (make, model, 2020, "low"))
            self.assertTrue(any(x.startswith("read '") for x in w))

    def test_fit_stays_honda(self):
        r, _ = self.look("2015 fit")
        self.assertEqual((r["make"], r["model"]), ("Honda", "Fit"))

    def test_no_download_for_indexed_year(self):
        with mock.patch.object(V, "_download_year", return_value=False) as dl:
            V.lookup("2020 Qwzx Plokm", [], allow_download=True)
        self.assertFalse(dl.called)


class LeadingWordTests(BundledCase):   # V8
    def test_leading_words_dropped(self):
        for text, make in (("white 2019 civic", "Honda"), ("my wife's 2019 rav4", "Toyota"),
                           ("leased 2023 model y", "Tesla")):
            r, w = self.look(text)
            self.assertIsNotNone(r, "%s: %s" % (text, w))
            self.assertEqual(r["make"], make)
            self.assertTrue(any(x.startswith("ignored '") for x in w))


class ElectrifiedWordTests(BundledCase):   # V9
    def test_recharge_is_electrified(self):
        r, _ = self.look("2023 Volvo XC60 Recharge")
        self.assertEqual(r["kind"], "phev")
        r, _ = self.look("2022 Volvo XC40 Recharge")
        self.assertEqual((r["kind"], r["city"], r["hwy"]), ("ev", 22.8, 26.6))

    def test_unknown_designator_is_none(self):
        r, w = self.look("2022 Lexus UX 300e")
        self.assertIsNone(r)
        self.assertTrue(any("300e" in x for x in w))


class GrandCherokeeLTests(BundledCase):   # V10
    def test_l_is_its_own_model(self):
        r, _ = self.look("2022 Jeep Grand Cherokee L")
        self.assertTrue(r["variant_names"] and all("Grand Cherokee L" in n for n in r["variant_names"]))

    def test_split_engine_litre(self):
        q = V.parse_query("2019 F-150 3.5 L")
        self.assertEqual((q["engine"], q["model"]), (3.5, ["f150"]))


class AliasAndGenerationTests(BundledCase):   # V12
    def test_rogue_sport_is_qashqai(self):
        r, w = self.look("2019 Nissan Rogue Sport")
        self.assertEqual(r["model"], "Qashqai")
        self.assertTrue(any("Qashqai" in x for x in w))

    def test_wrangler_jk_and_jl(self):
        r, w = self.look("Jeep Wrangler Unlimited 2018")
        self.assertEqual((r["year"], r["label"]), (2018, "2018 Jeep Wrangler JK Unlimited"))
        self.assertIn("2018 Jeep New Wrangler Unlimited", r["alternatives"])
        r2, _ = self.look("2018 Jeep New Wrangler Unlimited")
        self.assertTrue(all(n.startswith("New Wrangler Unlimited") for n in r2["variant_names"]))
        r3, _ = self.look("2019 Jeep Wrangler Unlimited")
        self.assertEqual(r3["year"], 2019)


class EngineWordTests(BundledCase):   # V13
    def test_ecoboost(self):
        r, w = self.look("2018 f150 ecoboost")
        self.assertTrue(set(r["engines"]) <= {2.7, 3.5}, r["engines"])
        self.assertIn("EcoBoost", r["label"])
        self.assertFalse(any("ignored 'ecoboost'" in x for x in w))

    def test_turbo_engine_size(self):
        r, w = self.look("2019 accord 2.0t")
        self.assertEqual(r["engines"], [2.0])
        self.assertFalse(any("ignored" in x for x in w))


# --------------------------------------------------------------------------- drive_plan


class LowConfidenceLineTests(tcp.CarCase):   # V14
    def test_rough_match_marked(self):
        tcp.CARS["2018 f150 ecoboost"] = dict(tcp.CARS["2022 toyota corolla"], label="2018 Ford F-150", confidence="low",
                                              alternatives=["2018 Ford F-150 2.7L"])
        try:
            res, text = self.run_plan("--car", "2018 f150 ecoboost")
        finally:
            del tcp.CARS["2018 f150 ecoboost"]
        self.assertTrue(self.car_line(text)[0].endswith("; rough match, check WARNINGS and the alternatives"))
        res, text = self.run_plan("--car", "2022 Toyota Corolla")
        self.assertNotIn("rough match", text)


class EvLineTests(tcp.CarCase):   # F1, F7
    def no_routes(self, *extra, status="ok"):
        # th.check_network is faked: the no-routes plan asks it about the routing hosts (a live HEAD each)
        with tdp.Env(), mock.patch.object(dc.th, "check_network",
                                          side_effect=lambda hosts, timeout=6: {h: status for h in hosts}):
            sys.modules["drive_route"].get_routes = lambda *a, **k: []
            with mock.patch.dict(sys.modules, {"ev_charging": self.evc}), \
                    mock.patch.object(dc, "fetch", side_effect=tcp.fetch_stub):
                res = dp.plan(tdp.args_for(*(tcp.BASE + list(extra))))
                return res, dp.render(res)

    def test_no_routes_has_ev_figures(self):
        for status in ("ok", "blocked: test"):   # the routing hosts reachable, then all blocked (NETWORK BLOCKED)
            with self.subTest(status=status):
                res, text = self.no_routes("--car", "2024 Tesla Model Y", status=status)
                self.assertEqual(res["network_blocked"], status != "ok")
                line = self.car_line(text)[0]
                self.assertIn("16.9 city / 19.5 hwy kWh/100 km", line)
                self.assertIn("79 kWh battery", line)
                res, text = self.no_routes("--ev-model", "model-y", status=status)
                line = self.car_line(text)[0]
                self.assertNotIn("0.0 kWh", line)
                self.assertRegex(line, r"\d+\.\d city / \d+\.\d hwy kWh/100 km")

    def test_car_only_save_has_ev_figures(self):
        out = io.StringIO()
        with tdp.Env(), mock.patch.dict(sys.modules, {"ev_charging": self.evc}), contextlib.redirect_stdout(out):
            self.assertEqual(dp.main(["--car", "2024 Tesla Model Y", "--save-car"]), 0)
        first = out.getvalue().splitlines()[0]
        self.assertIn("16.9 city / 19.5 hwy kWh/100 km", first)
        self.assertNotIn(":  ", first)

    def test_missing_ev_module_backstop(self):
        car = {"how": "flag", "vehicle": "ev", "fuel": "electricity", "label": "2024 Tesla Model Y",
               "ev_args": {"kwh_per_100km": 20.0}}
        first, _ = dp.car_lines({"car": car})
        self.assertIn("20.0 kWh/100 km (your figure)", first)
        car = {"how": "lookup", "vehicle": "ev", "fuel": "electricity", "label": "2024 Tesla Model Y", "year": 2024,
               "ev_args": {"nrcan": {"label": "x", "city": 16.9, "hwy": 19.5}}}
        first, _ = dp.car_lines({"car": car})
        self.assertIn("16.9 city / 19.5 hwy", first)
        car = {"how": "flag", "vehicle": "ev", "fuel": "electricity", "ev_args": {}}
        first, _ = dp.car_lines({"car": car})
        self.assertNotIn("0.0 kWh", first)

    def test_car_with_kwh_flag_line(self):
        res, text = self.run_plan("--car", "2024 Tesla Model Y", "--kwh-per-100km", "20", ev=True)
        line = self.car_line(text)[0]
        self.assertEqual(line, "CAR        2024 Tesla Model Y Long Range AWD: 20.0 kWh/100 km (your figure); 79 kWh "
                               "battery, 250 kW max DC from the model-y preset")
        self.assertNotIn("((", line)


class FuelFlagWithEvTests(tcp.CarCase):   # F2
    def test_l_per_100km_with_ev_car_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_plan("--car", "2024 Tesla Model Y", "--l-per-100km", "7", ev=True)
        self.assertIn("--kwh-per-100km", str(cm.exception))
        with self.assertRaises(SystemExit):
            self.run_plan("--vehicle", "ev", "--l-per-100km", "7", ev=True)

    def test_saved_ev_skipped_for_fuel_figure(self):
        self.run_plan("--car", "2024 Tesla Model Y", "--save-car", ev=True)
        res, text = self.run_plan("--l-per-100km", "7", ev=True)
        self.assertEqual((res["vehicle"], res["car"]["how"]), ("gas", "flag"))
        self.assertTrue(any("not used for this gas run" in w for w in res["warnings"]))

    def test_saved_hybrid_keeps_fuel_figure(self):
        self.run_plan("--vehicle", "hybrid", "--save-car")
        res, _ = self.run_plan("--l-per-100km", "5")
        self.assertEqual((res["vehicle"], res["car"]["how"]), ("hybrid", "flag"))


class BrokenProfileTests(tcp.CarCase):   # F3
    def write(self, data):
        with open(os.path.join(self.tmp, "car.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_bad_values_never_crash(self):
        for data in ({"label": "Old car", "city": "abc", "hwy": 5}, {"label": "Old car", "city": 7.5},
                     {"l_per_100km": "seven"}, {"label": "Old car", "comb": "x"}, {"vehicle": "truck"},
                     {"flags": [1, 2]}, {"label": "Old car", "city": 7, "hwy": 6, "flags": "x"}):
            self.write(data)
            res, text = self.run_plan()
            self.assertTrue(self.car_line(text)[0].startswith("CAR "), data)
            self.assertTrue(any("saved car file" in w for w in res["warnings"]), (data, res["warnings"]))
        self.write({"label": "Old car", "city": 7.5})
        res, _ = self.run_plan()
        self.assertEqual(res["car"]["how"], "class default")
        self.write({"label": "Old car", "city": 7, "hwy": 6, "flags": "x"})
        res, _ = self.run_plan()
        self.assertEqual(res["car"]["how"], "saved")

    def test_parts_direct(self):
        w = []
        lk, flags = dp._saved_parts({"l_per_100km": "6.5", "battery_kwh": -3, "vehicle": "truck"}, w, "p")
        self.assertEqual((lk, flags), (None, {"l_per_100km": 6.5}))
        self.assertEqual(len(w), 2)


class SaveMergeTests(tcp.CarCase):   # F4, F5
    def test_override_keeps_saved_car(self):
        self.run_plan("--car", "2024 Tesla Model Y", "--save-car", ev=True)
        res, text = self.run_plan("--battery-kwh", "75", "--save-car", ev=True)
        with open(os.path.join(self.tmp, "car.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["label"], saved["battery_kwh"]), ("2024 Tesla Model Y Long Range AWD", 75.0))
        res, _ = self.run_plan(ev=True)
        self.assertEqual((res["car"]["how"], res["car"]["ev"]["battery_kwh"]), ("saved", 75.0))

    def test_replacing_is_noted(self):
        self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        res, text = self.run_plan("--no-saved-car", "--l-per-100km", "6", "--save-car")
        self.assertIn("replaced your saved car (2022 Toyota Corolla)", text)

    def test_kwh_figure_saved(self):
        res, text = self.run_plan("--kwh-per-100km", "18", "--save-car", "--no-saved-car", ev=True)
        with open(os.path.join(self.tmp, "car.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["kwh_per_100km"], 18.0)
        res, text = self.run_plan(ev=True)
        self.assertEqual((res["vehicle"], res["car"]["how"]), ("ev", "saved"))
        self.assertIn("18.0 kWh/100 km (your saved figure)", self.car_line(text)[0])
        self.assertEqual(self.evc.seen[-1]["kwh_100km_hwy"], 18.0)

    def test_nothing_to_save_mentions_kwh(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_plan("--save-car", "--no-saved-car")
        self.assertIn("--kwh-per-100km", str(cm.exception))


class ConfigPathTests(unittest.TestCase):   # F6
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tp-cfg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_folder_paths(self):
        for env in (os.path.join(self.tmp, "newdir") + os.sep, os.path.join(self.tmp, "newdir2"),
                    os.path.join(self.tmp, "newdir3") + "/"):
            with mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": env}):
                path = V.save_profile({"l_per_100km": 6.0})
                self.assertEqual(os.path.basename(path), "car.json")
                self.assertTrue(os.path.isfile(path))
                self.assertEqual(V.load_profile()["l_per_100km"], 6.0)
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": os.path.join(self.tmp, "mine.json")}):
            self.assertEqual(V.profile_path(), os.path.join(self.tmp, "mine.json"))

    def test_write_failure_is_a_clean_exit(self):
        blocker = os.path.join(self.tmp, "file")
        with open(blocker, "w") as f:
            f.write("x")
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": os.path.join(blocker, "car.json")}), \
                mock.patch.dict(sys.modules, {"vehicles": V}), tdp.Env():
            with self.assertRaises(SystemExit) as cm:
                dp.car_only(tdp.args_for("--l-per-100km", "6", "--save-car", "--no-saved-car"))
        self.assertIn("--save-car: could not write", str(cm.exception))


class CarClassTests(tcp.CarCase):   # F8, F9
    def test_car_class_with_car_warns(self):
        res, _ = self.run_plan("--car", "2022 Toyota Corolla", "--car-class", "pickup")
        self.assertTrue(any(w.startswith("--car-class pickup is ignored: --car 2022 Toyota Corolla")
                            for w in res["warnings"]))

    def test_class_is_a_costs_key(self):
        car = dict(tcp.CARS["2022 toyota corolla"], car_class="compact")
        tcp.CARS["2022 corolla cc"] = car
        try:
            res, _ = self.run_plan("--car", "2022 Corolla cc")
        finally:
            del tcp.CARS["2022 corolla cc"]
        j = json.loads(dp.to_json(res))["car"]
        self.assertEqual((j["class"], j["nrcan_class"]), ("compact", "Compact"))
        res, _ = self.run_plan("--car", "2020 BMW 330i")
        j = json.loads(dp.to_json(res))["car"]
        self.assertNotIn("class", j)
        self.assertEqual(j["nrcan_class"], "Compact")


if __name__ == "__main__":
    unittest.main()
