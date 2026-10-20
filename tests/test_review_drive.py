"""Regression tests for the review fixes to the driving route core: tolls the routing engine reports that the
planner does not price (US toll roads, crossings missing from tolls.json), the time zone of an origin outside
the region files, border waits and charging stops placed in the timeline, navigation links that follow the
recommended route, and rain or snow in the planning time behind a leave-by.

Every HTTP call is mocked: either drive_common.fetch with test_drive_route.FakeFetch, or test_drive_plan.Env.

Run: python -m unittest tests.test_review_drive -v
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
import copy
import datetime as dt
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
import test_car_profile as tcp  # noqa: E402,F401  (keeps the run away from the user's saved car)
import test_drive_plan as tdp  # noqa: E402
import test_drive_route as tdr  # noqa: E402
import test_final_integration as tfi  # noqa: E402

dp, dc = tdp.dp, tdp.dc
th = dc.th
import drive_route as drt  # noqa: E402
import tolls  # noqa: E402

BUF = (42.8864, -78.8784)
ALB = (42.6526, -73.7562)
SYR = (43.0481, -76.1474)
STC = (43.1594, -79.2469)
ROC = (43.1566, -77.6088)
TUE_0900 = dt.datetime(2026, 10, 20, 9, 0)


def _bridge(name):
    return [b for b in tolls.data()["bridges"] if b["name"] == name][0]


def _routes(table, o, d):
    fake = tdr.FakeFetch(table)
    w = []
    with mock.patch.object(dc, "fetch", side_effect=fake):
        routes = drt.get_routes(o, d, TUE_0900, dict(tdr.OPTS), w)
    return routes, w, fake


class UnpricedTolls(unittest.TestCase):
    """drive-core-1, architecture-product-6: a toll the engine reports that is neither the 407 nor a listed
    crossing is flagged as unpriced, and its toll-free comparison is kept."""

    def thruway(self):
        fast = tdr._valhalla_trip([(tdr.densify([BUF, SYR, ALB], 2000), ["I 90", "New York State Thruway"],
                                    True, 105.0)])
        free = tdr._valhalla_trip([(tdr.densify([BUF, (42.4, -76.5), ALB], 2000), ["US 20"], False, 80.0)])
        return _routes([(("valhalla", "exclude_tolls"), free), (("valhalla",), fast)], BUF, ALB)

    def test_non_407_toll_keeps_toll_free_route(self):
        routes, w, fake = self.thruway()
        self.assertEqual(len(fake.urls), 2)
        self.assertEqual([r["label"] for r in routes], ["fastest", "toll-free"])
        f, n = routes
        self.assertIs(f["has_tolls"], True)
        self.assertIs(f["toll_unpriced"], True)
        self.assertEqual(f["toll_other_cad"], 0.0)
        self.assertTrue(any(x.startswith("unpriced toll: ") for x in f["notes"]))
        self.assertIs(n["has_tolls"], False)
        self.assertIs(n["toll_unpriced"], False)
        self.assertFalse(any(x.startswith("unpriced toll: ") for x in n["notes"]))
        self.assertEqual(w, [])

    def test_return_keeps_the_unpriced_flag(self):
        routes, _w, _f = self.thruway()
        back = drt.reverse_for_return(routes[0], dt.datetime(2026, 10, 20, 17, 0), dict(tdr.OPTS))
        self.assertIs(back["toll_unpriced"], True)
        self.assertIs(back["has_tolls"], True)
        self.assertEqual(sum(1 for x in back["notes"] if x.startswith("unpriced toll: ")), 1)

    def test_border_bridge_then_us_toll_road(self):
        lq = _bridge("Lewiston-Queenston Bridge")
        ca, us = tuple(lq["canada_side"]), tuple(lq["us_side"])
        head = [(tdr.densify([STC, ca], 500), ["QEW"], False, 95.0),
                (tdr.densify([ca, us], 50), ["Lewiston-Queenston Bridge"], True, 40.0)]
        fast = tdr._valhalla_trip(head + [(tdr.densify([us, ROC], 2000), ["I 90", "New York State Thruway"],
                                           True, 105.0)])
        soft = tdr._valhalla_trip(head + [(tdr.densify([us, (43.25, -78.3), ROC], 2000), ["NY 104"], False, 80.0)])
        routes, w, fake = _routes([(("valhalla", "use_tolls"), soft), (("valhalla",), fast)], STC, ROC)
        self.assertEqual(len(fake.urls), 2)
        q = json.loads(urllib.parse.unquote(fake.urls[1].split("json=", 1)[1]))
        self.assertEqual(q["costing_options"]["auto"], {"use_tolls": 0.0})
        self.assertEqual([r["label"] for r in routes], ["fastest", "toll-free"])
        f, n = routes
        self.assertEqual([b["name"] for b in f["bridges"]], ["Lewiston-Queenston Bridge"])
        self.assertIs(f["toll_unpriced"], True)
        self.assertIs(f["has_tolls"], True)
        self.assertEqual([b["name"] for b in n["bridges"]], ["Lewiston-Queenston Bridge"])
        self.assertIs(n["has_tolls"], False)   # no toll into the US on this bridge
        self.assertIs(n["toll_unpriced"], False)
        self.assertEqual(w, [])

    def test_tomtom_toll_section_off_the_407_is_unpriced(self):
        rt = tdr.tomtom_reply(False)["routes"][0]
        rt["sections"].append({"startPointIndex": 10, "endPointIndex": 60, "sectionType": "TOLL"})
        r = drt._finish(*drt._tomtom_route(rt), engine="tomtom", depart=tdr.WED_0730, opts=tdr.OPTS)
        self.assertEqual(r["etr"], [])
        self.assertIs(r["toll_unpriced"], True)
        self.assertIs(r["has_tolls"], True)

    def test_trade_off_says_tolls_not_priced(self):
        a = {"label": "fastest", "has_tolls": True, "toll_unpriced": True, "etr": []}
        b = {"label": "toll-free", "has_tolls": False, "etr": []}
        items = [{"letter": "A", "route": a, "money": 30.0, "time_s": 200 * 60},
                 {"letter": "B", "route": b, "money": 32.0, "time_s": 230 * 60}]
        t = dp.trade_off(items, 30, False)
        self.assertTrue(t["text"].startswith("The toll route saves 30 min; its tolls are not priced here."), t["text"])
        self.assertNotIn("costs no more", t["text"])
        self.assertNotIn("No tolls", t["text"])
        self.assertTrue(t["text"].endswith("(fastest, tolls not priced)."), t["text"])
        items[0]["money"] = 40.0   # dearer in fuel: the per-hour price leaves the toll out
        t = dp.trade_off(items, 30, False, round_trip=True)
        self.assertIn("for $8.00 ($16/h of time saved), there and back, plus tolls not priced here.", t["text"])

    def test_407_route_unchanged(self):
        routes, w, _f = _routes([(("valhalla", "exclude_tolls"), tdr.milton_markham_tollfree()),
                                 (("valhalla",), tdr.milton_markham_valhalla())], tdr.MILTON, tdr.MARKHAM)
        self.assertEqual([r["label"] for r in routes], ["fastest", "toll-free"])
        self.assertEqual([r["toll_unpriced"] for r in routes], [False, False])
        self.assertTrue(routes[0]["etr"])
        self.assertFalse(any(x.startswith("unpriced toll: ") for r in routes for x in r["notes"]))
        routes, w, _f = _routes([(("valhalla", "use_tolls"), tdr._border_no407()),
                                 (("valhalla",), tdr._border_fast())], tdr.MILTON, tdr.DETROIT)
        self.assertEqual([r["label"] for r in routes], ["fastest", "without 407"])
        self.assertEqual([r["toll_unpriced"] for r in routes], [False, False])
        self.assertEqual(w, [])

    def test_decide_names_the_route_with_unpriced_tolls(self):
        m = tdp.fake_drive_route()
        inner = m.get_routes

        def get_routes(o, d, depart, opts, warnings, arrive_at=None):
            routes = inner(o, d, depart, opts, warnings, arrive_at)
            routes[0].update(toll_unpriced=True, etr=[], toll_407_transponder=0.0, toll_407_camera=0.0)
            return routes
        m.get_routes = get_routes
        with tdp.Env(), mock.patch.dict(sys.modules, {"drive_route": m}), tdp.at_now(tdp.NOW):
            res = dp.plan(tdp.args_for(*(tdp.BASE + ["--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-17 10:00",
                                                     "--park-and-ride", "off"])))
            text = dp.render(res)
        self.assertTrue(any(w.startswith("A has tolls this planner does not price") for w in res["warnings"]),
                        res["warnings"])
        self.assertIn("tolls are not priced here", text)

    def test_unlisted_border_crossing_gets_allowance(self):
        def rev(lat, lon):
            return {"label": "X", "country": "CA" if lat > 46.51 else "US", "state": None}
        with mock.patch.object(dp, "reverse_address", side_effect=rev), \
                mock.patch.object(dp, "reverse_country", side_effect=lambda lat, lon: rev(lat, lon)["country"]), \
                tdp.at_now(tdp.NOW):
            res, _text = tdp.run("--origin", "46.5219,-84.3461", "--dest", "46.4953,-84.3453", "--weather", "clear",
                                 "--incidents", "off", "--depart", "2026-10-17 10:00", "--park-and-ride", "off")
        allow = dp.costs()["arrival"]["border_wait_min"]
        rec = [r for r in res["routes"] if r["letter"] == res["recommended"]][0]
        self.assertEqual(rec["border_s"], allow * 60)
        self.assertEqual(res["arrival"]["border_min"], allow)
        self.assertTrue(any("crosses the Canada-US border at a crossing with no data here" in w
                            for w in res["warnings"]), res["warnings"])
        self.assertEqual(res["origin"]["country"], "CA")

    def test_same_country_trip_gets_no_allowance(self):
        with tdp.at_now(tdp.NOW):
            res, _text = tdp.run(*(tdp.BASE + ["--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-17 10:00",
                                               "--park-and-ride", "off"]))
        self.assertFalse(any("crossing with no data here" in w for w in res["warnings"]))
        self.assertFalse(res["arrival"].get("border_min"))


KENORA = "49.7670,-94.4894"
WINNIPEG = "49.8951,-97.1384"
UTC_1530 = dt.datetime(2026, 10, 14, 15, 30)   # 11:30 in Toronto, 10:30 in Kenora and Winnipeg, 08:30 in Vancouver


def _utc(when=UTC_1530):
    return mock.patch.object(th, "utc_now", return_value=when)


def _tz_warnings(res):
    return [w for w in res["warnings"] if "--tz" in w]


class TimeZoneTests(unittest.TestCase):
    """drive-core-2: an origin outside the region files was planned on Toronto time with no word about it."""

    def trip(self, *extra):
        return ["--origin", KENORA, "--dest", WINNIPEG, "--weather", "clear", "--incidents", "off",
                "--park-and-ride", "off"] + list(extra)

    def test_origin_outside_regions_warns(self):
        with _utc():
            res, _text = tdp.run(*self.trip("--depart", "2026-10-14 12:00"))
        self.assertIsNone(res["region"])
        self.assertEqual(res["timezone"], "America/Toronto")
        w = _tz_warnings(res)
        self.assertEqual(len(w), 1, res["warnings"])
        self.assertIn("America/Toronto", w[0])

    def test_tz_flag_reads_now_in_that_zone(self):
        with _utc():
            res, text = tdp.run(*self.trip("--tz", "America/Winnipeg", "--depart", "now"))
        self.assertEqual(res["depart"], dt.datetime(2026, 10, 14, 10, 30))
        self.assertEqual(res["timezone"], "America/Winnipeg")
        self.assertEqual(_tz_warnings(res), [])
        drive = [ln for ln in text.splitlines() if ln.startswith("DRIVE")][0]
        self.assertIn("times America/Winnipeg", drive)

    def test_tz_flag_hhmm_not_rolled_to_tomorrow(self):
        with _utc():
            res, _text = tdp.run("--origin", "49.2827,-123.1207", "--dest", "49.2488,-122.9805", "--weather", "clear",
                                 "--incidents", "off", "--park-and-ride", "off", "--tz", "America/Vancouver",
                                 "--depart", "09:00")
        self.assertEqual(res["depart"], dt.datetime(2026, 10, 14, 9, 0))
        self.assertFalse(any("already passed" in w for w in res["warnings"]), res["warnings"])

    def test_bad_tz_is_one_line(self):
        with tdp.Env():
            with self.assertRaises(SystemExit) as cm:
                dp.plan(tdp.args_for(*self.trip("--tz", "Mars/Base")))
        self.assertIn("--tz 'Mars/Base'", str(cm.exception.code))
        self.assertIn("America/Winnipeg", str(cm.exception.code))

    def test_region_origin_keeps_toronto_time_quietly(self):
        with _utc():
            res, text = tdp.run(*(tdp.BASE + ["--dest", "%f,%f" % tdp.MARKHAM, "--depart", "now",
                                              "--park-and-ride", "off"]))
        self.assertEqual(res["timezone"], "America/Toronto")
        self.assertEqual(res["depart"], dt.datetime(2026, 10, 14, 11, 30))
        self.assertEqual(_tz_warnings(res), [])
        self.assertNotIn("times America", text)



def _milton_markham():
    fake = tdr.FakeFetch([(("valhalla", "exclude_tolls"), tdr.milton_markham_tollfree()),
                          (("valhalla",), tdr.milton_markham_valhalla())])
    with mock.patch.object(dc, "fetch", side_effect=fake):
        return drt.get_routes(tdr.MILTON, tdr.MARKHAM, tdr.WED_0730, dict(tdr.OPTS), [])


class StopsInTimeline(unittest.TestCase):
    """drive-core-3: a border wait or a charging stop before the 407 moves the entry clock, its toll band and the
    time-of-day factors after it; the arrival keeps adding the stop once."""
    D0630 = dt.datetime(2026, 10, 14, 6, 30)

    def test_stop_before_407_moves_entry_band(self):
        f = _milton_markham()[0]
        ref = copy.deepcopy(f)
        drt.retime(ref, dt.datetime(2026, 10, 14, 7, 0), tdr.OPTS)
        f["_stops"] = [(0.0, 1800.0)]
        drt.retime(f, self.D0630, tdr.OPTS)
        self.assertEqual(f["etr"][0]["entry_time"], ref["etr"][0]["entry_time"])
        self.assertEqual(f["etr"][0]["band_start"], "07:00")
        self.assertEqual(f["etr"][0]["toll_transponder"], 50.62)
        self.assertAlmostEqual(f["typical_s"], ref["typical_s"], delta=1)
        self.assertEqual(f["arrive"], self.D0630 + dt.timedelta(seconds=f["typical_s"]))   # driving time only

    def test_stop_after_407_does_not_move_entry(self):
        f = _milton_markham()[0]
        ref = copy.deepcopy(f)
        drt.retime(ref, self.D0630, tdr.OPTS)
        f["_stops"] = [(f["base_s"], 1800.0)]
        drt.retime(f, self.D0630, tdr.OPTS)
        self.assertEqual(f["etr"][0]["entry_time"], ref["etr"][0]["entry_time"])
        self.assertEqual(f["etr"][0]["band_start"], ref["etr"][0]["band_start"])
        self.assertAlmostEqual(f["typical_s"], ref["typical_s"], delta=1)

    def test_stop_at_407_entry_is_before_it(self):
        f = _milton_markham()[0]
        ref = copy.deepcopy(f)
        drt.retime(ref, self.D0630, tdr.OPTS)
        f["_stops"] = [(f["etr"][0]["offset_base_s"] - 30.0, 1800.0)]   # inside a chunk, just before the ramp
        drt.retime(f, self.D0630, tdr.OPTS)
        self.assertAlmostEqual(f["etr"][0]["offset_s"], ref["etr"][0]["offset_s"] + 1800.0, delta=1)

    def test_set_stops_converts_metres(self):
        f = _milton_markham()[0]
        drt.set_stops(f, [(0.0, 60), (sum(c["m"] for c in f["chunks"]), 90), (5000.0, 0)])
        self.assertEqual(f["_stops"], [(0.0, 60.0), (round(f["base_s"], 1), 90.0)])   # a zero-length stop is dropped

    def test_return_drops_the_outbound_stops(self):
        f = _milton_markham()[0]
        drt.set_stops(f, [(0.0, 1800.0)])
        back = drt.reverse_for_return(f, dt.datetime(2026, 10, 14, 17, 0), tdr.OPTS)
        self.assertEqual(back["_stops"], [])

    def test_route_stops_list(self):
        got = dp.route_stops({"bridges": [{"along_m": 5000.0}]},
                             {"stops": [{"km_along": 12.5, "charge_min": 20, "detour_min": 1}]}, 30)
        self.assertEqual(got, [(5000.0, 1800.0), (12500.0, 1260.0)])

    def test_ev_charging_stop_moves_407_band_in_plan(self):
        evm = types.ModuleType("ev_charging")
        evm.car_profile = lambda *a, **k: {"name": "Test EV", "connectors": {"CCS"}}
        evm.plan_charging = lambda *a, **k: {
            "needed": True, "feasible": True, "arrive_soc": 0.4, "extra_s": 1800, "public_cost": 10.0,
            "kwh_used": 10.0, "kwh_100km": 18.0, "home_energy_cost": 0.0, "notes": [], "dest_chargers": [],
            "source": "NLR", "stops": [{"name": "Test DC", "network": "Ivy", "km_along": 1.0, "max_kw": 150,
                                        "ports": 4, "arrive_soc": 0.2, "depart_soc": 0.6, "charge_min": 30,
                                        "cost": 10.0, "price_text": "$0.69/kWh", "detour_min": 0}]}
        fake = tdr.FakeFetch([(("valhalla", "exclude_tolls"), tdr.milton_markham_tollfree()),
                              (("valhalla",), tdr.milton_markham_valhalla())])

        def fetch(url, **kw):
            if "fueltypesall" in url:
                return tdp.FUEL_CSV.encode("utf-8")
            return fake(url, **kw)
        with tdp.Env(), tdp.at_now(tdp.NOW),                 mock.patch.dict(sys.modules, {"drive_route": drt, "ev_charging": evm}),                 mock.patch.object(dc, "fetch", side_effect=fetch):
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % tdr.MILTON, "--dest", "%f,%f" % tdr.MARKHAM,
                                       "--vehicle", "ev", "--depart", "2026-10-14 06:30", "--weather", "clear",
                                       "--incidents", "off", "--park-and-ride", "off"))
        row = next(r for r in res["routes"] if r["label"] == "fastest")
        self.assertEqual(row["etr"][0]["band_start"], "07:00")
        self.assertNotIn("_stops", row)
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        self.assertEqual(res["arrival"]["at_lot"], rec["arrive"] + dt.timedelta(minutes=30))   # the stop added once


    def test_border_wait_placed_on_the_way_back(self):
        fake = tdr.FakeFetch([(("valhalla", "use_tolls"), tdr._border_no407()), (("valhalla",), tdr._border_fast())])

        def fetch(url, **kw):
            if "fueltypesall" in url:
                return tdp.FUEL_CSV.encode("utf-8")
            return fake(url, **kw)
        with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.dict(sys.modules, {"drive_route": drt}),                 mock.patch.object(dc, "fetch", side_effect=fetch),                 mock.patch.object(drt, "set_stops", wraps=drt.set_stops) as ss:
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % tdr.MILTON, "--dest", "%f,%f" % tdr.DETROIT,
                                       "--depart", "2026-10-20 06:00", "--weather", "clear", "--incidents", "off",
                                       "--park-and-ride", "off", "--round-trip", "--stay-hours", "3"))
        wait = dp.costs()["arrival"]["border_wait_min"] * 60.0
        calls = [c[0] for c in ss.call_args_list]
        out = [r for r, _st in calls if not r["_return"]]
        back = [r for r, _st in calls if r["_return"] and r["etr"]][0]
        self.assertEqual(len(out), 2)   # both routes cross the bridge near Detroit, at the far end
        self.assertTrue(all(st == [(r["bridges"][0]["along_m"], wait)] for r, st in calls))
        self.assertLess(back["_stops"][0][0], 0.1 * back["base_s"])   # the drive back crosses near its start
        plain = copy.deepcopy(back)
        plain["_stops"] = []
        drt.retime(plain, back["depart"], tdr.OPTS)
        # the 407 entry is at least the wait later (more, when the later clock meets heavier traffic before it)
        self.assertGreaterEqual(back["etr"][0]["offset_s"] - plain["etr"][0]["offset_s"], wait - 1)
        row = next(r for r in res["routes"] if r["etr"])["return"]
        self.assertEqual(row["etr"][0]["entry_time"], back["etr"][0]["entry_time"])
        self.assertEqual(row["arrive"], row["depart"] + dt.timedelta(seconds=row["typical_s"] + wait))   # added once



WINDSOR = "42.3149,-83.0364"


def _bent(o, d, dlat):
    mid = ((o[0] + d[0]) / 2.0 + dlat, (o[1] + d[1]) / 2.0)
    return tdp.line(o, mid, 30) + tdp.line(mid, d, 30)[1:]


def _router(m, edit):
    """the fake drive_route m with edit(a, b) applied to its two routes"""
    base = m.get_routes

    def get_routes(o, d, depart, opts, warnings, arrive_at=None):
        a, b = base(o, d, depart, opts, warnings, arrive_at)
        edit(a, b, o, d)
        return [a, b]
    m.get_routes = get_routes
    return m


def _run_links(m, *extra):
    with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.dict(sys.modules, {"drive_route": m}):
        res = dp.plan(tdp.args_for(*tdp.BASE, "--park-and-ride", "off", *extra))
        text = dp.render(res)
    return res, text


class LinksFollowRecommendedRoute(unittest.TestCase):
    """drive-core-5: the navigation links follow a recommended route the router would not pick by itself."""

    def test_without_407_border_route_gets_via(self):
        def edit(a, b, o, d):
            b["poly"] = _bent(o, d, -0.08)
        res, text = _run_links(_router(tfi.border_drive_route(), edit), "--dest", WINDSOR,
                               "--depart", "2026-10-17 10:00", "--value-of-time", "0")
        self.assertEqual(res["recommended"], "B")
        ln = res["links"]
        self.assertIsNotNone(ln["via"])
        self.assertIn("waypoints=", ln["google"])
        self.assertNotIn("avoid=tolls", ln["google"])   # avoiding tolls would also avoid the bridge
        self.assertIn("Google picks its own route", ln["timed_text"])
        self.assertIn("goes via a point on route B", text)

    def test_forced_407_fastest_gets_via(self):
        def edit(a, b, o, d):
            a["_forced_407"] = True
            a["poly"] = _bent(o, d, 0.08)
        res, _text = _run_links(_router(tdp.fake_drive_route(), edit), "--dest", "%f,%f" % tdp.MARKHAM,
                                "--depart", "2026-10-14 07:30", "--value-of-time", "300")
        self.assertEqual(res["recommended"], "A")
        self.assertIn("waypoints=", res["links"]["google"])
        self.assertNotIn("avoid=tolls", res["links"]["google"])

    def test_variant_route_is_marked(self):
        fake = tdr.FakeFetch([(("valhalla", "through"), tdr._kingston_407()), (("valhalla",), tdr._kingston_401())])
        with mock.patch.object(dc, "fetch", side_effect=fake):
            routes = drt.get_routes(tdr.MILTON, tdr.KINGSTON, dt.datetime(2026, 10, 16, 15, 0), dict(tdr.OPTS), [])
        var = [r for r in routes if any("407 option added" in n for n in r["notes"])]
        self.assertEqual(len(var), 1)
        self.assertIs(var[0].get("_forced_407"), True)
        self.assertFalse(any(r.get("_forced_407") for r in routes if r is not var[0]))

    def test_toll_free_route_that_keeps_a_bridge_uses_via_not_avoid(self):
        def edit(a, b, o, d):
            for r in (a, b):
                r["bridges"] = [{"name": "Ambassador Bridge", "cad": 0.0, "text": "free this way"}]
            a["toll_unpriced"] = True
            b["poly"] = _bent(o, d, -0.08)
        res, _text = _run_links(_router(tdp.fake_drive_route(), edit), "--dest", WINDSOR,
                                "--depart", "2026-10-17 10:00", "--value-of-time", "0")
        self.assertEqual(res["recommended"], "B")
        ln = res["links"]
        self.assertNotIn("avoid=tolls", ln["google"])
        self.assertIn("waypoints=", ln["google"])
        self.assertNotIn("avoid_tolls=t", ln["waze"])
        self.assertNotIn("avoid=tolls", ln["apple"])
        self.assertIn("avoid=tolls", ln["google_alt"])   # the second link still offers the toll-free way


class WeatherPlanTime(unittest.TestCase):
    """tests-ci-eng-4: rain or snow scales the planning time as well as the typical time, so a leave-by in snow is
    earlier by the weather allowance on the plan time; the park-and-ride ride factor carries the weather too."""

    def test_retime_peak_plan_time_includes_weather(self):
        f = _milton_markham()[0]
        drt.retime(f, tdr.WED_0730, dict(tdr.OPTS, weather=1.3))
        fac = [drt.factors(c, tdr.WED_0730, set(), None, "valhalla") for c in f["chunks"]]
        st = sum(c["s"] * x[0] for c, x in zip(f["chunks"], fac))
        sp = sum(c["s"] * x[1] for c, x in zip(f["chunks"], fac))
        self.assertGreater(sp, st)   # the peak plan rows are above the typical ones, so max() does not hide p
        # every chunk stays in the weekday peak, so the later clock of each chunk does not change its factors
        self.assertAlmostEqual(f["plan_s"], round(max(sp, st) * 1.3, 1), delta=1)
        self.assertAlmostEqual(f["typical_s"], round(st * 1.3, 1), delta=1)

    def test_arrive_by_leave_by_in_snow_uses_real_retime(self):
        fake = tdr.FakeFetch([(("valhalla", "exclude_tolls"), tdr.milton_markham_tollfree()),
                              (("valhalla",), tdr.milton_markham_valhalla())])

        def fetch(url, **kw):
            if "fueltypesall" in url:
                return tdp.FUEL_CSV.encode("utf-8")
            return fake(url, **kw)
        deadline = dt.datetime(2026, 10, 14, 9, 0)
        with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.dict(sys.modules, {"drive_route": drt}),                 mock.patch.object(dc, "fetch", side_effect=fetch):
            res = dp.plan(tdp.args_for("--origin", "%f,%f" % tdr.MILTON, "--dest", "%f,%f" % tdr.MARKHAM,
                                       "--incidents", "off", "--park-and-ride", "off",
                                       "--arrive-by", "2026-10-14 09:00", "--weather", "snow"))
        self.assertEqual(res["weather"]["mult"], dp.costs()["weather"]["snow_factor"])
        got = {r["letter"]: r["leave_by"] for r in res["routes"]}
        self.assertEqual(got, {"A": dt.datetime(2026, 10, 14, 7, 42), "B": dt.datetime(2026, 10, 14, 7, 2)})
        a = res["arrival"]
        extra = (a["search_min"] + a["walk_min"] + a["slack_min"]) * 60.0
        for r in res["routes"]:   # the latest whole minute: on time, and one minute later would be late
            spare = (deadline - r["leave_by"]).total_seconds() - r["plan_s"] - extra
            self.assertGreaterEqual(spare, 0, r["letter"])
            self.assertLess(spare, 60, r["letter"])

    def test_park_and_ride_traffic_factor_includes_weather(self):
        seen = {}

        def pnr(*a, **kw):
            seen.update(kw["ctx"])
            return {"options": [], "notes": [], "trigger": None}
        with tdp.Env(parking_cost=30.0), tdp.at_now(tdp.NOW), mock.patch.object(dp, "park_and_ride", side_effect=pnr):
            res = dp.plan(tdp.args_for(*tdp.BASE[:2], "--incidents", "off", "--weather", "snow",
                                       "--dest", "%f,%f" % tdp.UNION, "--depart", "2026-10-14 07:30",
                                       "--park-and-ride", "on"))
        self.assertIsNotNone(res["park_and_ride"])
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        snow = dp.costs()["weather"]["snow_factor"]
        self.assertGreater(snow, 1.0)
        want = th.plan_traffic_factor(rec["depart"], dc.holidays_for())[0] * snow
        self.assertAlmostEqual(seen["traffic_factor"], want, places=6)


def _night_route(etr=None):
    """One 3 h 06 TomTom chunk (no calibration, typical factor 1.0) for the DST clock tests."""
    return {"engine": "tomtom", "chunks": [{"s": 11160.0, "m": 300000.0, "lat": 43.6, "lon": -79.4, "urban": False,
                                             "area": None, "etr": False, "hwy": True}],
            "etr": list(etr or []), "poly": [(43.6, -79.4), (45.4, -75.7)], "_queried_depart": None,
            "_live_now": False, "_return": False}


def _via_utc(local, seconds, tz="America/Toronto"):
    """Wall clock `local` plus `seconds` of elapsed time, worked out through UTC (the reference for DstClock)."""
    utc = local - th.offset_at_local(local, tz, None)[0] + dt.timedelta(seconds=seconds)
    return utc + th.offset_at_utc(utc, tz, None)[0]


class DstClock(unittest.TestCase):
    """drive-core-7: arrivals, 407 entry times and leave-by times follow the wall clock across a daylight-saving
    change; the elapsed driving time is kept and the clock jumps."""

    def test_arrival_across_fall_back(self):
        r = _night_route()
        drt.retime(r, dt.datetime(2026, 11, 1, 0, 30), dict(tdr.OPTS))
        self.assertEqual(r["typical_s"], 11160.0)
        self.assertEqual(r["arrive"], dt.datetime(2026, 11, 1, 2, 36))   # 03:36 EDT does not happen: 02:36 EST
        self.assertEqual(r["arrive_plan"], _via_utc(dt.datetime(2026, 11, 1, 0, 30), r["plan_s"]))

    def test_407_entry_across_spring_forward(self):
        sg = {"entry": "Hwy 401", "entry_no": 1, "entry_km": 34.8, "exit": "Hwy 404", "exit_no": 2, "exit_km": 84.4,
              "km": 49.6, "direction": "E", "offset_base_s": 2700.0, "end_base_s": 4500.0, "offset_s": 2700.0}
        r = _night_route([sg])
        drt.retime(r, dt.datetime(2027, 3, 14, 1, 30), dict(tdr.OPTS))
        self.assertEqual(r["etr"][0]["entry_time"], dt.datetime(2027, 3, 14, 3, 15))   # 02:15 does not exist

    def test_leave_by_across_spring_forward(self):
        r = _night_route()
        deadline = dt.datetime(2027, 3, 14, 4, 30)
        dep = dp.plan_backwards(drt, r, deadline, 0, dict(tdr.OPTS))
        self.assertEqual(dep, dp.floor_min(_via_utc(deadline, -r["plan_s"])))
        self.assertLessEqual(_via_utc(dep, r["plan_s"]), deadline)
        self.assertGreater(_via_utc(dep + dt.timedelta(minutes=1), r["plan_s"]), deadline)   # the latest minute

    def test_route_line_border_wait_across_spring_forward(self):
        row = {"letter": "A", "label": "fastest", "arrive": dt.datetime(2027, 3, 14, 1, 30), "border_s": 2700.0,
               "typical_s": 3600.0, "plan_s": 3900.0, "km": 60.0, "energy": {"text": "fuel $6.00"}, "money": 6.0}
        res = {"transponder": True, "mode": "depart", "depart": dt.datetime(2027, 3, 14, 0, 30), "round_trip": False,
               "timezone": "America/Toronto"}
        self.assertIn("arrive 03:15 with 45 min at the border", dp.route_lines(row, res)[0])   # not 02:15

    def test_clock_helpers(self):
        tz = "America/Toronto"
        self.assertEqual(dc.add_s(dt.datetime(2026, 11, 1, 0, 30), 11160, tz, None), dt.datetime(2026, 11, 1, 2, 36))
        self.assertEqual(dc.add_s(dt.datetime(2027, 3, 14, 3, 15), -2700, tz, None), dt.datetime(2027, 3, 14, 1, 30))
        self.assertEqual(dc.add_s(dt.datetime(2026, 11, 1, 0, 30), 11160), dt.datetime(2026, 11, 1, 3, 36))   # no tz
        self.assertEqual(dc.elapsed_s(dt.datetime(2027, 3, 14, 1, 30), dt.datetime(2027, 3, 14, 3, 15), tz, None), 2700)
        self.assertEqual(dc.elapsed_s(dt.datetime(2026, 10, 14, 7, 0), dt.datetime(2026, 10, 14, 9, 0), tz, None), 7200)


class TomTomPointTimes(unittest.TestCase):
    """drive-core-8: a TomTom route times its pieces from TomTom's guidance instructions (cumulative seconds at a
    point), so a slow approach puts the 407 entry, and its toll band, at the right clock time; without usable
    instructions the time is spread evenly by distance as before."""
    D0645 = dt.datetime(2026, 10, 14, 6, 45)

    def _reply(self, instructions=None):
        rt = tdr.tomtom_reply(True)["routes"][0]
        toll = [x for x in rt["sections"] if x["sectionType"] == "TOLL"][0]
        a, z, n = toll["startPointIndex"], toll["endPointIndex"], len(rt["legs"][0]["points"])
        if instructions is not None:
            rt["guidance"] = {"instructions": instructions(a, z, n)}
        return rt, a, z

    def _finish(self, rt):
        return drt._finish(*drt._tomtom_route(rt), engine="tomtom", depart=self.D0645, opts=dict(tdr.OPTS))

    def test_407_entry_from_instruction_times(self):
        rt, a, z = self._reply(lambda a, z, n: [
            {"pointIndex": 0, "travelTimeInSeconds": 0, "routeOffsetInMeters": 0},
            {"pointIndex": a, "travelTimeInSeconds": 1200}, {"pointIndex": z, "travelTimeInSeconds": 4100},
            {"pointIndex": n - 1, "travelTimeInSeconds": 4380}])
        r = self._finish(rt)
        sg = r["etr"][0]
        self.assertAlmostEqual(sg["offset_base_s"], 1200.0, delta=1)   # even spread: 733.6
        self.assertEqual(sg["entry_time"], dt.datetime(2026, 10, 14, 7, 5))
        self.assertEqual(sg["band_start"], "07:00")
        self.assertEqual(sg["toll_transponder"], 50.62)
        self.assertAlmostEqual(r["base_s"], 4380.0, delta=1)

    def _even_offset(self, rt, a):
        pts = [(p["latitude"], p["longitude"]) for p in rt["legs"][0]["points"]]
        cum = dc.cum_dist(pts)
        return cum[a] / cum[-1] * rt["summary"]["travelTimeInSeconds"]

    def test_no_guidance_keeps_even_spread(self):
        rt, a, _z = self._reply()
        self.assertAlmostEqual(self._finish(rt)["etr"][0]["offset_base_s"], self._even_offset(rt, a), delta=1)

    def test_bad_guidance_falls_back(self):
        rt, a, _z = self._reply(lambda a, z, n: [{"pointIndex": a, "travelTimeInSeconds": 3000},
                                                 {"pointIndex": z, "travelTimeInSeconds": 2000}])   # time going back
        self.assertAlmostEqual(self._finish(rt)["etr"][0]["offset_base_s"], self._even_offset(rt, a), delta=1)

    def test_url_asks_for_instructions(self):
        now = (dt.datetime(2026, 10, 10, 12, 0), "zoneinfo")
        with mock.patch.object(drt.th, "local_now", return_value=now):
            url, key = drt.tomtom_url(tdr.MILTON, tdr.MARKHAM, tdr.WED_0730, dict(tdr.OPTS, tomtom_key="K"))
        self.assertIn("instructionsType=coded", url)
        self.assertIn("instructionsType=coded", key)   # a new cache key: replies cached without guidance are not reused


if __name__ == "__main__":
    unittest.main()
