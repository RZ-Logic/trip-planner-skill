"""Regression tests for the review fixes to the driving route core: tolls the routing engine reports that the
planner does not price (US toll roads, crossings missing from tolls.json) and the time zone of an origin outside
the region files.

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
import datetime as dt
import json
import os
import sys
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import test_car_profile as tcp  # noqa: E402,F401  (keeps the run away from the user's saved car)
import test_drive_plan as tdp  # noqa: E402
import test_drive_route as tdr  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
