"""Regression tests for the v0.9.8 driving fixes: every route re-solved when the lot's walk changes, the TomTom
return queried at its own time, live TomTom times without a weather factor, the CAR line for a combined-only saved
car, leave-later minutes from the printed clocks, the 407 fee text, coarse country lookups, the LEAF presets by year
and trim, saved-car paths without the account name, fix_json_escapes on valid JSON, control characters in the text
output and the Ontario 511 failure message. Offline: every fetch is faked.

Run: python -m unittest tests.test_v098_drive -v
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
import datetime as dt
import io
import json
import os
import sys
import types
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import test_car_profile as tcp  # noqa: E402  (keeps the run away from the user's saved car)
import test_drive_plan as tdp  # noqa: E402
import test_drive_route as tdr  # noqa: E402

dp, dc = tdp.dp, tdp.dc
th = dc.th
import drive_route as drt  # noqa: E402


def walk_parking(calls):
    """Parking whose first search finds a lot 4 min from the door and every later search one 12 min away."""
    m = types.ModuleType("parking")

    def find_parking(lat, lon, arrive, stay_min, opts, warnings):
        calls.append((arrive, stay_min))
        walk = 4 if len(calls) == 1 else 12
        lot = {"name": "Lot %d" % len(calls), "lat": lat + 0.002, "lon": lon, "walk_m": 80 * walk, "walk_min": walk,
               "search_min": 2, "cost": 0.0, "cost_text": "free", "free": True, "fits_stay": True,
               "max_stay_min": None, "confidence": "osm", "source": "t", "notes": ""}
        return {"options": [lot], "place_rules": [], "reservations": [], "sources": [], "radius_m": 800}
    m.find_parking = find_parking
    return m


class WalkResolveTests(unittest.TestCase):   # item 1
    def plan(self, *extra):
        calls = []
        with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.dict(sys.modules, {"parking": walk_parking(calls)}):
            res = dp.plan(tdp.args_for(*tdp.BASE, "--dest", "%f,%f" % tdp.UNION, "--arrive-by", "2026-10-14 09:00",
                                       "--stay-hours", "3", "--park-and-ride", "off", *extra))
            text = dp.render(res)
        self.assertGreater(len(calls), 1)   # the walk did change
        self.assertEqual(res["arrival"]["walk_min"], 12)
        return res, text

    def test_every_route_leaves_on_the_final_walk(self):
        res, text = self.plan()
        a, b = res["routes"][0], res["routes"][1]
        for r in (a, b):   # leave-by + allow time + parking 2 + walk 12 + spare fits the deadline
            end = r["leave_by"] + dt.timedelta(seconds=r["plan_s"] + (2 + 12 + 5) * 60)
            self.assertLessEqual((end - res["deadline"]).total_seconds(), 60, r["letter"])
            self.assertGreater((end - res["deadline"]).total_seconds(), -2 * 60, r["letter"])
        rows = round((a["leave_by"] - b["leave_by"]).total_seconds() / 60.0)
        self.assertEqual(res["trade_off"]["saved_min"], rows)   # TRADE-OFF and the rows agree
        self.assertIn("lets you leave %d min later" % rows, text)

    def test_round_trip_returns_on_the_final_walk(self):
        res, _ = self.plan("--round-trip")
        want = dt.datetime(2026, 10, 14, 12, 12)   # door 09:00 + 3 h stay + 12 min walk back
        self.assertEqual([r["return"]["depart"] for r in res["routes"]], [want, want])


TOLL_BACK = "calculateRoute/43.856100,-79.337000"   # the return query starts at the Markham end


def back_reply(with_toll, secs):
    r = tdr.tomtom_reply(with_toll, "2026-10-14T17:53:00-04:00")
    r["routes"][0]["summary"]["travelTimeInSeconds"] = secs
    return r


class TomTomReturnTests(unittest.TestCase):   # item 2
    def plan(self, back_table, *extra, now=tdp.NOW):
        fake = tdr.FakeFetch(back_table + [(("avoid=tollRoads",), tdr.tomtom_reply(False)),
                                           (("api.tomtom.com",), tdr.tomtom_reply(True))])

        def fetch(url, **kw):
            if "fueltypesall" in url:
                return tdp.FUEL_CSV.encode("utf-8")
            return fake(url, **kw)
        with tdp.Env(), tdp.at_now(now), mock.patch.dict(sys.modules, {"drive_route": drt}), \
                mock.patch.object(dc, "fetch", side_effect=fetch), mock.patch.dict(os.environ, {"TOMTOM_API_KEY": "K3Y"}):
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % tdr.MILTON, "--dest", "%f,%f" % tdr.MARKHAM,
                                       "--incidents", "off", "--park-and-ride", "off", "--depart", "2026-10-14 07:30",
                                       *extra))
            text = dp.render(res)
        return res, text, fake

    def test_return_queried_at_its_own_time(self):
        res, text, fake = self.plan([((TOLL_BACK, "avoid=tollRoads"), back_reply(False, 5100)),
                                     ((TOLL_BACK,), back_reply(True, 5400))],
                                    "--weather", "clear", "--stay-hours", "9", "--round-trip")
        back_urls = [u for u in fake.urls if TOLL_BACK in u]
        self.assertTrue(back_urls)
        self.assertTrue(all("departAt=2026-10-14T1" in u for u in back_urls))   # the return time, not 07:30
        a = next(r for r in res["routes"] if r["etr"])
        self.assertEqual(a["return"]["tomtom_return"], "queried")
        self.assertAlmostEqual(a["return"]["typical_s"], 5400, delta=1)
        self.assertIn("TomTom traffic time for the return at", a["return"]["factor_note"])
        self.assertEqual(a["return"]["etr"][0]["band_start"], "15:30")
        self.assertNotIn("time reused", text)

    def test_failed_return_query_says_so_in_text(self):
        res, text, _ = self.plan([((TOLL_BACK,), OSError("HTTP Error 503 for key=K3Y"))],
                                 "--weather", "clear", "--stay-hours", "9", "--round-trip")
        a = res["routes"][0]
        self.assertEqual(a["return"]["tomtom_return"], "reused")
        self.assertEqual(a["return"]["typical_s"], a["typical_s"])
        self.assertIn("time reused from the drive there, not TomTom traffic at the return time", text)
        self.assertNotIn("K3Y", text)
        self.assertNotIn("K3Y", dp.to_json(res))

    def test_live_departure_has_no_weather_factor(self):   # item 6
        res, text, _ = self.plan([], "--weather", "rain", now=dt.datetime(2026, 10, 14, 7, 28))
        a = res["routes"][0]
        self.assertEqual(a["typical_s"], 4380)
        self.assertIn("live traffic already reflects the weather", a["factor_note"])
        self.assertFalse(any(w.endswith("applied to the drive time") for w in res["warnings"]))
        self.assertIn("not added to the time: TomTom live traffic already reflects the weather", text)


class LiveWeatherRouteTests(unittest.TestCase):   # item 6, in drive_route
    def route(self, now):
        fake = tdr.FakeFetch([(("avoid=tollRoads",), tdr.tomtom_reply(False)),
                              (("api.tomtom.com",), tdr.tomtom_reply(True))])
        with mock.patch.object(drt.th, "local_now", return_value=(now, "zoneinfo")), \
                mock.patch.object(dc, "fetch", side_effect=fake):
            return drt.get_routes(tdr.MILTON, tdr.MARKHAM, tdr.WED_0730, dict(tdr.OPTS, tomtom_key="K"), [])[0], fake

    def test_departat_now_keeps_the_live_time(self):
        f, fake = self.route(dt.datetime(2026, 10, 14, 7, 28))
        self.assertIn("departAt=now", fake.urls[0])
        drt.retime(f, tdr.WED_0730, dict(tdr.OPTS, weather=1.15))
        self.assertEqual(f["typical_s"], 4380)
        self.assertIn("no x1.15 for rain", f["factor_note"])
        back = drt.reverse_for_return(f, dt.datetime(2026, 10, 14, 17, 0), dict(tdr.OPTS, weather=1.15))
        self.assertAlmostEqual(back["typical_s"], 4380 * 1.15, delta=1)   # the return is not live

    def test_future_departure_keeps_the_weather_factor(self):
        f, fake = self.route(dt.datetime(2026, 10, 10, 12, 0))
        self.assertNotIn("departAt=now", fake.urls[0])
        drt.retime(f, tdr.WED_0730, dict(tdr.OPTS, weather=1.15))
        self.assertAlmostEqual(f["typical_s"], 4380 * 1.15, delta=1)
        self.assertIn("x1.15 rain", f["factor_note"])

    def test_return_on_other_roads_is_not_used(self):
        f, _ = self.route(dt.datetime(2026, 10, 10, 12, 0))
        other = tdr.FakeFetch([((TOLL_BACK,), back_reply(False, 3000))])   # no 407 for a 407 route
        with mock.patch.object(drt.th, "local_now", return_value=(dt.datetime(2026, 10, 10, 12, 0), "zoneinfo")), \
                mock.patch.object(dc, "fetch", side_effect=other):
            back = drt.reverse_for_return(f, dt.datetime(2026, 10, 14, 17, 0), dict(tdr.OPTS, tomtom_key="K"))
        self.assertEqual(back["tomtom_return"], "reused")
        self.assertIn("other roads", back["tomtom_return_why"])
        self.assertEqual(back["base_s"], f["base_s"])
        self.assertIn("not re-queried for the return", back["factor_note"])


class CombinedOnlyCarTests(tcp.CarCase):   # item 3
    def save(self):
        with open(os.path.join(self.tmp, "car.json"), "w", encoding="utf-8") as f:
            json.dump({"label": "2020 Test Car", "year": 2020, "make": "Test", "model": "Car", "kind": "gas",
                       "comb": 8.0, "fuel": "regular"}, f)

    def test_render_and_main(self):
        self.save()
        res, text = self.run_plan()
        self.assertEqual(res["car"]["how"], "saved")
        self.assertIn("CAR        2020 Test Car (your saved car): 8.0 L/100 km combined, regular (NRCan 2020 rating)",
                      text)
        out = io.StringIO()
        with tdp.Env(), mock.patch.object(dc, "fetch", side_effect=tcp.fetch_stub), contextlib.redirect_stdout(out):
            self.assertEqual(dp.main(tcp.BASE), 0)
        self.assertIn("8.0 L/100 km combined", out.getvalue())


class LeaveLaterTests(unittest.TestCase):   # item 4
    def test_minutes_follow_the_printed_clocks(self):
        hub = tdp.hub("Milton GO", 4.9, tdp.H(7, 17) + 40, tdp.H(8, 45), 10.33, 0.79)
        res = {"access": "car", "warnings": [], "pareto": [hub], "others": []}
        ctx = {"drive_rt": 40.0, "day": dt.date(2026, 10, 14), "drive_leave": dt.datetime(2026, 10, 14, 7, 3)}
        with mock.patch.object(th, "plan", return_value=res):
            pr = dp.park_and_ride(tdp.MILTON, tdp.UNION, dt.datetime(2026, 10, 14, 9), True, 0.16, 9, 9.4, 18.0, [],
                                  30, ctx=ctx)
        op = pr["options"][0]
        self.assertEqual(op["leave_by"], "07:17")
        self.assertEqual(op["leave_later_min"], 14)   # 07:17 against 07:03, not 15 from the raw seconds
        ctx = {"drive_rt": 40.0, "day": dt.date(2026, 10, 14), "door": dt.datetime(2026, 10, 14, 8, 30, 50)}
        hub = tdp.hub("Milton GO", 4.9, tdp.H(7, 17), tdp.H(8, 45) + 10, 10.33, 0.79)
        res = {"access": "car", "warnings": [], "pareto": [hub], "others": []}
        with mock.patch.object(th, "plan", return_value=res):
            pr = dp.park_and_ride(tdp.MILTON, tdp.UNION, dt.datetime(2026, 10, 14, 7), False, 0.16, 9, 9.4, 1.0, [],
                                  30, ctx=ctx)
        self.assertEqual(pr["options"][0]["later_min"], 15)   # 08:45 against 08:30


class FeeTextTests(unittest.TestCase):   # item 5
    def test_fee_text_only_when_counted(self):
        args = (*tdp.BASE, "--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-14 08:00", "--park-and-ride", "off")
        _, text = tdp.run(*args)
        self.assertIn("(+$5 account fee that month)", text)
        res, text = tdp.run(*args, "--407-this-month")
        self.assertEqual(res["routes"][0]["account_fee"], 0.0)
        self.assertNotIn("account fee that month", text)
        _, text = tdp.run(*args, "--transponder")
        self.assertNotIn("account fee that month", text)


class GeocodeTests(unittest.TestCase):   # items 7 and 8
    def plan_urls(self, home):
        urls = []

        def spy(url, **kw):
            urls.append(url)
            if "fueltypesall" in url:
                return tdp.FUEL_CSV.encode("utf-8")
            raise OSError("offline")
        with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.object(dc, "fetch", side_effect=spy), \
                mock.patch.dict(os.environ, {"TRIP_HOME": home}):
            res = dp.plan(tdp.args_for(*tdp.BASE, "--dest", "%f,%f" % tdp.UNION, "--depart", "2026-10-14 08:00",
                                       "--park-and-ride", "off"))
        return res, [urllib.parse.unquote(u) for u in urls if "nominatim" in u]

    def test_home_destination_is_not_looked_up(self):
        res, urls = self.plan_urls("%f,%f;home" % tdp.UNION)
        self.assertEqual(res["destination"]["short"], "home")
        self.assertFalse([u for u in urls if "lat=43.645" in u or "lat=43.65" in u], urls)
        self.assertEqual(res["destination"]["country"], "CA")   # from the region file, not a lookup

    def test_country_lookup_is_coarse(self):
        res, urls = self.plan_urls("")
        country = [u for u in urls if "zoom=3" in u]
        self.assertEqual(len(country), 1)
        self.assertIn("lat=43.65&lon=-79.38", country[0])
        for u in urls:   # no call sends more than 3 decimals
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
            self.assertLessEqual(len(q["lat"][0].split(".")[1]), 3, u)

    def test_label_lookup_sends_three_decimals(self):
        reply = {"address": {"house_number": "805", "road": "Santa Maria Boulevard", "town": "Milton",
                             "country_code": "ca"}}
        with mock.patch.object(dc, "fetch_json", return_value=reply) as f, mock.patch.dict(os.environ, {"TRIP_HOME": ""}):
            dp.place_label("43.498757,-79.855987", *tdp.MILTON)
        url = f.call_args[0][0]
        self.assertIn("lat=43.499&lon=-79.856", url)
        self.assertNotIn("43.498757", url)
        self.assertIn("zoom=16", url)   # street level: no house number is asked for (security-tos-8)

    def test_reverse_country(self):
        with mock.patch.object(dc, "fetch_json", return_value={"address": {"country_code": "us"}}) as f:
            self.assertEqual(dp.reverse_country(42.886, -78.878), "US")
        self.assertIn("lat=42.89&lon=-78.88", f.call_args[0][0])
        with mock.patch.object(dc, "fetch_json", side_effect=OSError("down")):
            self.assertIsNone(dp.reverse_country(42.886, -78.878))


class LeafPresetTests(tcp.CarCase):   # item 9
    def presets(self):
        return dc.load_data("ev")["cars"]

    def test_by_year_and_trim(self):
        f = dp.ev_preset_for
        self.assertEqual(f({"make": "Nissan", "model": "LEAF", "year": 2026,
                            "variant_names": ["LEAF Platinum PLUS", "LEAF SV PLUS"]}), "leaf")
        self.assertEqual(f({"make": "Nissan", "model": "LEAF", "year": 2022, "label": "2022 Nissan LEAF PLUS",
                            "variant_names": ["LEAF SV/SL PLUS"]}), "leaf-plus")
        self.assertEqual(f({"make": "Nissan", "model": "LEAF", "year": 2020, "query": "2020 Nissan LEAF e+",
                            "variant_names": ["LEAF (40 kWh)", "LEAF SV/SL PLUS"]}), "leaf-plus")
        self.assertEqual(f({"make": "Nissan", "model": "LEAF S"}), "leaf")   # no year: unchanged
        old = {"make": "Nissan", "model": "LEAF", "year": 2021, "variant_names": ["LEAF (40 kWh)", "LEAF SV/SL PLUS"]}
        if "leaf-40" in self.presets():
            self.assertEqual((f(old), dp.ev_battery_for(old)), ("leaf-40", None))
        else:   # the CHAdeMO LEAF PLUS preset stands in, with the 40 kWh car's battery
            self.assertEqual((f(old), dp.ev_battery_for(old)), ("leaf-plus", 39.0))
        self.assertIsNone(dp.ev_battery_for({"make": "Nissan", "model": "LEAF", "year": 2022,
                                             "label": "2022 Nissan LEAF PLUS"}))

    def test_old_leaf_through_car(self):
        tcp.CARS["2021 nissan leaf"] = dict(tcp.CARS["2024 tesla model y"], query="2021 Nissan LEAF",
                                            label="2021 Nissan LEAF", year=2021, make="Nissan", model="LEAF",
                                            variant_names=["LEAF (40 kWh)", "LEAF SV/SL PLUS"])
        try:
            res, text = self.run_plan("--car", "2021 Nissan LEAF", ev=True)
        finally:
            del tcp.CARS["2021 nissan leaf"]
        car = self.evc.seen[-1]
        if "leaf-40" in self.presets():
            self.assertEqual(car["key"], "leaf-40")
        else:
            self.assertEqual((car["key"], car["battery_kwh"]), ("leaf-plus", 39.0))
            self.assertTrue(any("uses the leaf-plus preset" in w for w in res["warnings"]))
        self.assertNotIn("NACS", json.dumps(car.get("name")))

    def test_older_leafs_get_their_own_battery(self):   # diff review: 2011 to 2017 LEAFs are 24 or 30 kWh
        f, b = dp.ev_preset_for, dp.ev_battery_for
        for car, kwh in (({"make": "Nissan", "model": "LEAF", "year": 2013, "variant_names": ["LEAF"]}, 21.0),
                         ({"make": "Nissan", "model": "LEAF", "year": 2016, "label": "2016 Nissan LEAF (30 kWh)",
                           "variant_names": ["LEAF (30 kWh)"]}, 27.0),
                         ({"make": "Nissan", "model": "LEAF", "year": 2016,
                           "variant_names": ["LEAF (24 kWh)", "LEAF (30 kWh)"]}, 21.0),
                         ({"make": "Nissan", "model": "LEAF", "year": 2017, "variant_names": ["LEAF"]}, 27.0)):
            self.assertEqual((f(car), b(car)), ("leaf-40", kwh), car)

    def test_old_leaf_warning_names_the_pack(self):
        tcp.CARS["2013 nissan leaf"] = dict(tcp.CARS["2024 tesla model y"], query="2013 Nissan LEAF",
                                            label="2013 Nissan LEAF", year=2013, make="Nissan", model="LEAF",
                                            variant_names=["LEAF"])
        try:
            res, text = self.run_plan("--car", "2013 Nissan LEAF", ev=True)
        finally:
            del tcp.CARS["2013 nissan leaf"]
        self.assertEqual((self.evc.seen[-1]["key"], self.evc.seen[-1]["battery_kwh"]), ("leaf-40", 21.0))
        self.assertTrue(any("21 kWh usable battery (its 24 kWh pack" in w and "CHAdeMO" in w
                            for w in res["warnings"]), res["warnings"])

    def test_help_lists_leaf_plus(self):
        h = next(a for a in dp.build_parser()._actions if a.dest == "ev_model").help
        self.assertIn("leaf-plus", h)


class ShownPathTests(tcp.CarCase):   # item 10
    def setUp(self):
        super().setUp()
        self.vm.shown_path = lambda p: p.replace(self.tmp, "~" + os.sep + "cfg") if p else p

    def test_no_full_path_in_output(self):
        res, text = self.run_plan("--car", "2022 Toyota Corolla", "--save-car")
        self.assertTrue(res["car"]["saved_to"].startswith("~"))
        self.assertNotIn(self.tmp, text)
        res, text = self.run_plan()
        self.assertTrue(res["car"]["profile_path"].startswith("~"))
        self.assertNotIn(self.tmp, dp.to_json(res))
        out = io.StringIO()
        with tdp.Env(), contextlib.redirect_stdout(out):
            dp.main(["--forget-car"])
        self.assertIn("forgot your saved car (~", out.getvalue())
        self.assertNotIn(self.tmp, out.getvalue())


class JsonEscapeTests(unittest.TestCase):   # item 11
    def test_valid_json_unchanged(self):
        for obj in ({"path": "C:\\x"}, {"t": "a\\b", "q": "say \"hi\""}, {"u": "caf\u00e9 \\\\ \\u"}, {"n": "\n\t"}):
            text = json.dumps(obj)
            self.assertEqual(dp.fix_json_escapes(text), text)
            self.assertEqual(json.loads(dp.fix_json_escapes(text)), obj)

    def test_stray_backslash_repaired(self):
        self.assertEqual(json.loads(dp.fix_json_escapes('{"d": "Water \\ Sewer"}')), {"d": "Water \\ Sewer"})
        self.assertEqual(json.loads(dp.fix_json_escapes('{"a": "C:\\x", "b": "\\\\\\y"}')),
                         {"a": "C:\\x", "b": "\\\\y"})


class ControlCharTests(unittest.TestCase):   # item 12
    def test_feed_text_is_printed_safely(self):
        bad = "Evil\x1b[2J Lot\u202e\x07"
        with tdp.Env(), mock.patch.dict(sys.modules, {"parking": tdp.fake_parking(0.0, name=bad)}):
            res = dp.plan(tdp.args_for(*tdp.BASE, "--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-14 08:00",
                                       "--park-and-ride", "off"))
            res["incidents"] = {"items": [{"source": "Ontario 511", "name": "Hwy 401\x1b]0;x\x07", "road": "",
                                           "type": "closures", "description": "lane\x1b[31m closed\u2066",
                                           "legs": ["out"]}], "route": "A"}
            text = dp.render(res)
        self.assertIn("Evil [2J Lot", text)
        for ch in ("\x1b", "\x07", "\u202e", "\u2066"):
            self.assertNotIn(ch, text)
        self.assertEqual(len(text.splitlines()), len(dp.render(res).splitlines()))
        self.assertEqual(json.loads(dp.to_json(res))["parking"]["options"][0]["name"], bad)   # --json keeps the data

    def test_review_cli_prints_safely(self):
        import place_reviews as pr
        got = {"places": [{"name": "Lot\x1b[31m A", "dist_m": 120, "summary": "$9\u202e weekends",
                           "mentions": [{"text": "paid $9\x07", "author": "Jane\x1b D.", "date": "2026-08"}],
                           "maps_url": ""}], "attribution": "Google Maps"}
        out = io.StringIO()
        with mock.patch.object(pr, "review_prices", return_value=got), contextlib.redirect_stdout(out):
            pr.main(["43.64", "-79.37"])
        self.assertIn("Lot [31m A, 120 m", out.getvalue())
        for ch in ("\x1b", "\x07", "\u202e"):
            self.assertNotIn(ch, out.getvalue())


class On511FailureTests(unittest.TestCase):   # item 16
    def test_key_never_in_the_warning(self):
        key = "S3CR/ET+KEY"
        w = []
        err = OSError("HTTP Error 403 for %s and %s" % (key, urllib.parse.quote(key)))
        with mock.patch.object(dc, "fetch_json", side_effect=err):
            out = dp.on511_incidents(key, [([(43.65, -79.40), (43.75, -79.40)], dt.datetime(2026, 10, 14, 12),
                                            dt.datetime(2026, 10, 14, 13))], "America/Toronto", None, w)
        self.assertEqual(out, [])
        self.assertEqual(len(w), 1)
        self.assertTrue(w[0].startswith("Ontario 511 events unavailable (HTTP Error 403 for KEY and KEY"), w[0])
        self.assertNotIn("S3CR", w[0])

    def test_plan_with_failing_511(self):
        def fetch(url, **kw):
            if "fueltypesall" in url:
                return tdp.FUEL_CSV.encode("utf-8")
            raise OSError("offline test: %s" % url)   # the 511 URL carries the key
        with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.object(dc, "fetch", side_effect=fetch), \
                mock.patch.dict(os.environ, {"ON511_API_KEY": "S3CRETKEY"}):
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % tdp.MILTON, "--dest", "%f,%f" % tdp.UNION,
                                       "--weather", "clear", "--depart", "2026-10-14 08:00", "--park-and-ride", "off"))
            text = dp.render(res)
        self.assertTrue(any(w.startswith("Ontario 511 events unavailable") for w in res["warnings"]))
        self.assertNotIn("S3CRETKEY", text)
        self.assertNotIn("S3CRETKEY", dp.to_json(res))


if __name__ == "__main__":
    unittest.main()
