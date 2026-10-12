"""Offline tests for the seams between the driving fixers' changes (drive_route, parking, trip_hubs and
drive_plan): relabel after a retime, the 'without 407' comparison, the border wait, an unknown hub parking
price, a paid-hours time limit on a free day, and far curated lots.

Run: python -m unittest tests.test_final_integration -v
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
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_plan as dp  # noqa: E402
import parking as pk  # noqa: E402
import test_drive_plan as tdp  # noqa: E402

th = tdp.th


def border_drive_route():
    """fake drive_route whose two routes cross a border bridge (the 407 one and a 'without 407' one)."""
    m = tdp.fake_drive_route()
    base_get = m.get_routes

    def get_routes(o, d, depart, opts, warnings, arrive_at=None):
        a, b = base_get(o, d, depart, opts, warnings, arrive_at)
        for r in (a, b):
            r["bridges"] = [{"name": "Ambassador Bridge", "cad": 12.0, "text": "C$12.00"}]
            r["toll_other_cad"] = 12.0
            r["has_tolls"] = True
        b["label"] = "without 407"
        return [a, b]
    m.get_routes = get_routes
    return m


class TradeOffWithout407(unittest.TestCase):
    def test_border_trip_compares_with_the_route_that_leaves_the_407(self):
        fast = {"label": "fastest", "has_tolls": True, "etr": [{"km": 40}]}
        w407 = {"label": "without 407", "has_tolls": True, "etr": []}
        items = [{"letter": "A", "route": fast, "money": 60.0, "time_s": 3 * 3600},
                 {"letter": "B", "route": w407, "money": 20.0, "time_s": 3.5 * 3600}]
        tr = dp.trade_off(items, 30.0, False)
        self.assertNotIn("Every route found has tolls", tr["text"])
        self.assertIn("The 407 saves 30 min for $40.00", tr["text"])
        self.assertIn("(without 407)", tr["text"])
        self.assertEqual(dp.LABELS["without 407"], "Without 407")


class BorderWait(unittest.TestCase):
    def test_border_wait_in_arrival_and_trade_off(self):
        with mock.patch.dict(sys.modules, {"drive_route": border_drive_route()}):
            with tdp.Env():
                sys.modules["drive_route"] = border_drive_route()
                args = tdp.args_for(*tdp.BASE, "--dest", "42.3149,-83.0364", "--depart", "2026-10-17 10:00")
                res = dp.plan(args)
                text = dp.render(res)
        wait = dp.costs()["arrival"]["border_wait_min"]
        rec = next(r for r in res["routes"] if r["letter"] == res["recommended"])
        self.assertEqual(rec["border_s"], wait * 60)
        a = res["arrival"]
        self.assertEqual(a["border_min"], wait)
        # lot arrival = drive arrival + the border allowance
        self.assertEqual(a["at_lot"], rec["arrive"] + dt.timedelta(minutes=wait))
        self.assertIn("min at the border", text)
        self.assertIn("Includes %d min at the border" % wait, text)
        self.assertNotIn("Every route found has tolls", text)


class RelabelAfterWeather(unittest.TestCase):
    def test_relabel_is_called_after_a_weather_retime(self):
        m = tdp.fake_drive_route()
        seen = []

        def relabel(routes):
            seen.append([r["label"] for r in routes])
            return routes
        m.relabel = relabel
        with tdp.Env():
            sys.modules["drive_route"] = m
            args = tdp.args_for("--origin", "%f,%f" % tdp.MILTON, "--weather", "snow", "--incidents", "off",
                                "--dest", "%f,%f" % tdp.MARKHAM, "--depart", "2026-10-14 07:30")
            dp.plan(args)
        self.assertEqual(len(seen), 1)


class HubParkingUnknown(unittest.TestCase):
    def test_unknown_hub_parking_is_not_free(self):
        unknown = {"text": "paid parking, price unknown (no free parking at this station), check signs",
                   "cost": None, "price_unknown": True}
        hubs = [tdp.hub("Kipling", 30.0, tdp.H(7, 20), tdp.H(8, 40), 3.30, 4.80, park=unknown)]
        hubs[0]["parking_unknown"] = True
        pr, w, _ = tdp.ParkRideSelectTests.run_pr(tdp.ParkRideSelectTests("run"), hubs)
        op = pr["options"][0]
        self.assertIsNone(op["rt_total"])
        self.assertTrue(op["parking"]["price_unknown"])
        text = "\n".join(dp.pr_lines(dict(pr, trigger="test"), {"mode": "arrive_by"}))
        self.assertIn("hub parking price unknown", text)
        self.assertIn("total not known", text)

    def test_hub_traffic_factor_is_the_plan_row_times_weather(self):
        f, name = th.plan_traffic_factor(dt.datetime(2026, 10, 14, 8, 0))
        self.assertGreater(f, 1.5)
        self.assertIn("peak", name)


class PaidHoursLimit(unittest.TestCase):
    RULE = {"hourly": 2.0, "paid_days": [0, 1, 2, 3, 4], "paid_hours": ["09:00", "18:00"], "max_stay_min": 180}

    def option(self):
        lot = {"name": "City Hall Lot", "lat": 43.3255, "lon": -79.799, "cost": 0.0, "_rule": self.RULE,
               "max_stay_min": 180}
        return pk._public(lot)

    def test_limit_binds_on_a_paid_weekday_only(self):
        opt = self.option()
        sat = dt.datetime(2026, 10, 17, 11, 0)
        wed = dt.datetime(2026, 10, 14, 11, 0)
        self.assertTrue(pk.stay_fits(opt, sat, 186))
        self.assertFalse(pk.stay_fits(opt, wed, 186))
        self.assertTrue(pk.stay_fits(opt, wed, 170))

    def test_unknown_option(self):
        self.assertIsNone(pk.stay_fits({"name": "nowhere", "lat": 0, "lon": 0}, dt.datetime(2026, 1, 1), 60))


class OwnWalkParking(unittest.TestCase):
    """A cheap lot 10 min away fits only the time parked with a 1 min walk (Rideau St before the rush-hour
    no-parking): the plan must not price it for the shorter time and flip between the two lots."""

    def test_far_cheap_lot_is_not_priced_for_the_close_lots_time(self):
        import types
        calls = []

        def lot(name, walk, cost):
            return {"name": name, "lat": 45.43 + walk / 1000.0, "lon": -75.69, "walk_m": walk * 70,
                    "walk_min": walk, "search_min": 2, "cost": cost, "cost_text": "$%.2f" % cost, "free": False,
                    "fits_stay": True, "max_stay_min": None, "confidence": "official", "source": "t", "notes": ""}

        def find_parking(lat, lon, arrive, stay_min, opts, warnings):
            calls.append(stay_min)
            far, near = lot("Rideau machine", 10, 2.25), lot("Dalhousie machine", 1, 6.5)
            opts_ = [near] if stay_min >= 2 + 2 * 10 + 120 - 1 else [far, near]
            return {"options": opts_, "place_rules": [], "reservations": [], "sources": [], "radius_m": 800}
        m = types.ModuleType("parking")
        m.find_parking = find_parking
        with tdp.Env():
            sys.modules["parking"] = m
            res = dp.plan(tdp.args_for(*tdp.BASE, "--dest", "45.4289,-75.6919", "--depart", "2026-10-16 08:00"))
        self.assertLessEqual(len(calls), 4)
        self.assertEqual(res["arrival"]["lot"], "Dalhousie machine")
        self.assertEqual(res["arrival"]["walk_min"], 1)
        self.assertEqual(res["parking"]["options"][0]["name"], "Dalhousie machine")
        self.assertEqual(res["parking"]["options"][-1]["name"], "Rideau machine")
        self.assertAlmostEqual(res["parking_stay_min"], 2 + 2 * 1 + 120, places=0)
        self.assertTrue(any("Rideau machine" in w and "10 min walk" in w for w in res["warnings"]))


class FarCuratedLot(unittest.TestCase):
    def test_beyond_walk_label(self):
        opt = pk._public({"name": "Lot C", "lat": 43.1, "lon": -79.0, "_far": True, "_rule": None,
                          "cost_text": "$10.00 for 4 h", "walk_m": 2131})
        self.assertTrue(opt["beyond_walk"])
        self.assertIn("beyond the 800 m walk radius", dp.lot_line(5, opt, 800))
        self.assertNotIn("beyond", dp.lot_line(1, {"name": "x", "walk_m": 100, "cost_text": "free"}, 800))

    def test_motorways_when_no_road_names(self):
        row = {"letter": "A", "label": "fastest", "km": 100, "typical_s": 3600, "plan_s": 4000, "roads": [],
               "hwy_share": 0.9, "etr": [], "energy": {"text": "fuel ~$10 (x)"}, "money": 10.0, "account_fee": 0,
               "has_tolls": False, "notes": [], "delays": []}
        res = {"transponder": False, "mode": "depart", "round_trip": False, "depart": dt.datetime(2026, 10, 14, 9)}
        self.assertIn("via motorways", dp.route_lines(row, res)[0])
        row["hwy_share"] = 0.2
        self.assertIn("via local roads", dp.route_lines(row, res)[0])


if __name__ == "__main__":
    unittest.main()
