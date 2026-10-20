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


if __name__ == "__main__":
    unittest.main()
