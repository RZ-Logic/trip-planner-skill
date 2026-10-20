"""Regression tests for the review's fare findings in trip_hubs.py: the GTFS fare path (card discount, cheapest
and route-specific rules, transfers and transfer_duration), route-priced passes (WEGO), mixed currencies and
refunded transfers; ride-mode hub ranking on ride + fare.

Offline: synthetic fare tables, no network, a fixed clock.
Run: python -m unittest tests.test_review_fares -v
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
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trip_hubs as th  # noqa: E402
from test_engine import make_feed, pin_clock  # noqa: E402
from test_regressions import add_files  # noqa: E402

DAY = dt.date(2026, 10, 15)   # a Thursday
REGIONS = {r["id"]: r for r in th.load_regions()}
GTHA, ONTARIO = REGIONS["gtha"], REGIONS["ontario"]
BASE = ["--region", "none", "--no-discover", "--routing", "estimate", "--final-ride", "off"]
_CLOCK = pin_clock()


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


def hm(text):
    return th._hm(text) * 60


def fare_model(attributes, rules=""):
    """A GTFS fare table from CSV bodies (fare_attributes.txt with its header, fare_rules.txt likewise)."""
    p = os.path.join(tempfile.mkdtemp(dir=_TMP), "fares.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("fare_attributes.txt", attributes)
        if rules:
            z.writestr("fare_rules.txt", rules)
    return th.load_fares(th.GtfsZip(p))


def fake_net(feed, model, zones):
    class Net:
        fares, fare_urls, stop_zone = {feed: model}, {}, zones
    return Net


def leg(feed, agency, route, depart, frm=0, to=1, a="A", b="B"):
    return {"type": "ride", "feed": feed, "agency": agency, "route": route, "route_id": route, "from": a, "to": b,
            "from_idx": frm, "to_idx": to, "depart": hm(depart) if isinstance(depart, str) else depart}


ZONE_FARES = ("fare_id,price,currency_type,payment_method,transfers\n"
              "Z13_20,6.65,CAD,1,\nZ13_20HI,7.10,CAD,1,\nX13_20,5.00,CAD,1,\n")
ZONE_RULES = "fare_id,route_id,origin_id,destination_id\nZ13_20,,13,20\nZ13_20HI,,13,20\nX13_20,X,13,20\n"


# ---------------------------------------------------------------------------------------------- tests-ci-eng-2
class GtfsFarePath(unittest.TestCase):
    """GO Transit has no flat fare in gtha.json, so every GO price comes through load_fares/fare_lookup/journey_fare."""

    def test_presto_discount_applies_to_card_and_tap_not_cash(self):
        self.assertNotIn("GO Transit", GTHA["flat_fares"])
        net = fake_net("GO Transit", fare_model(ZONE_FARES, ZONE_RULES), ["13", "20"])
        legs = [leg("GO Transit", "GO Transit", "L", "08:00")]
        for payment, want in (("card", 5.61), ("contactless", 5.61), ("cash", 6.65)):
            total, cur, parts, complete = th.journey_fare(net, legs, GTHA["flat_fares"], payment,
                                                          GTHA.get("card_discount"), GTHA["transfer_rules"], DAY)
            self.assertEqual((total, cur, complete), (want, "CAD", True), payment)
        self.assertEqual(th.journey_fare(net, legs, None, "card", GTHA["card_discount"])[2][0]["source"],
                         "GTFS less 15.7% card discount")

    def test_cheapest_matching_rule_wins(self):
        net = fake_net("go", fare_model(ZONE_FARES, ZONE_RULES), ["13", "20"])
        self.assertEqual(th.journey_fare(net, [leg("go", "GO Transit", "L", "08:00")], payment="cash")[0], 6.65)

    def test_route_specific_rule_prices_only_its_route(self):
        net = fake_net("go", fare_model(ZONE_FARES, ZONE_RULES), ["13", "20"])
        self.assertEqual(th.journey_fare(net, [leg("go", "GO Transit", "X", "08:00")], payment="cash")[0], 5.00)
        self.assertEqual(th.journey_fare(net, [leg("go", "GO Transit", "L", "08:00")], payment="cash")[0], 6.65)

    def test_feed_without_transfers_pays_every_leg(self):
        attrs = "fare_id,price,currency_type,payment_method,transfers\nZ13_20,6.65,CAD,1,0\n"
        net = fake_net("go", fare_model(attrs, "fare_id,origin_id,destination_id\nZ13_20,13,20\n"), ["13", "20"])
        legs = [leg("go", "GO Transit", "L", "08:00"), leg("go", "GO Transit", "L", "08:30")]
        self.assertEqual(th.journey_fare(net, legs, payment="cash")[0], 13.30)


# ------------------------------------------------------------------------------------------- hubs-pricing-io-3
class GtfsTransferAllowance(unittest.TestCase):
    def fare(self, transfers, departs, duration=""):
        attrs = ("fare_id,price,currency_type,payment_method,transfers,transfer_duration\n"
                 "F,3.00,USD,0,%s,%s\n" % (transfers, duration))
        net = fake_net("metro", fare_model(attrs), ["", ""])
        return th.journey_fare(net, [leg("metro", "Metro", str(k), d) for k, d in enumerate(departs)])

    def test_one_transfer_covers_two_legs(self):
        self.assertEqual(self.fare("1", ["10:00", "10:30"], "7200")[0], 3.00)               # was 6.00
        self.assertEqual(self.fare("1", ["10:00", "10:30", "11:00"], "7200")[0], 6.00)      # was 9.00

    def test_two_transfers_cover_three_legs(self):
        self.assertEqual(self.fare("2", ["10:00", "10:30", "11:00"])[0], 3.00)              # was 9.00

    def test_no_transfers_still_pays_each_leg(self):
        self.assertEqual(self.fare("0", ["10:00", "10:30"])[0], 6.00)

    def test_transfer_duration_ends_the_fare(self):
        self.assertEqual(self.fare("", ["10:00", "11:00"], "7200")[0], 3.00)
        self.assertEqual(self.fare("", ["10:00", "14:00"], "7200")[0], 6.00)                # was 3.00

    def test_duration_in_minutes_is_not_read_as_seconds(self):
        # Niagara Region Transit's feed publishes transfer_duration 120 (minutes); 120 s would charge every change
        self.assertEqual(self.fare("", ["10:00", "10:30"], "120")[0], 3.00)

    def test_unreadable_transfers_pays_each_leg_and_says_so(self):
        total, cur, parts, complete = self.fare("x", ["10:00", "10:30"])
        self.assertEqual(total, 6.00)
        o = {"transit_fare": total, "fare_currency": cur, "fare_parts": parts, "fare_complete": complete}
        self.assertIn("(before transfer discounts)", th.fare_text(o, "USD", False, rules_on=True))   # was absent


# ------------------------------------------------------------------------ review follow-up (hubs-pricing-io-3)
# mdb-620 (Valparaiso) shape: V-Line 1.00 with one transfer within 3600 s, ChicaGO Dash 9.00 with none, each
# fare_rules row limited to its own route
ROUTE_FARES = ("fare_id,price,currency_type,payment_method,transfers,transfer_duration\n"
               "V,1.00,USD,0,1,3600\nD,9.00,USD,0,0,\n")
ROUTE_RULES = "fare_id,route_id\nV,VL\nD,DASH\n"


class GtfsTransferOnlyOntoCoveredRoutes(unittest.TestCase):
    def fare(self, attrs, rules, *routes):
        net = fake_net("vl", fare_model(attrs, rules), [""])
        legs = [leg("vl", "V-Line", r, 36000 + 1800 * k, 0, 0) for k, r in enumerate(routes)]
        return th.journey_fare(net, legs)[0]

    def test_change_onto_a_route_the_fare_does_not_cover_pays_that_route(self):
        self.assertEqual(self.fare(ROUTE_FARES, ROUTE_RULES, "VL", "DASH"), 10.0)          # was 1.00
        self.assertEqual(self.fare(ROUTE_FARES, ROUTE_RULES, "VL", "DASH", "VL"), 11.0)    # was 2.00

    def test_change_onto_a_route_the_fare_covers_is_free(self):
        self.assertEqual(self.fare(ROUTE_FARES, ROUTE_RULES, "VL", "VL"), 1.0)

    def test_fare_rule_for_any_route_covers_every_route(self):
        attrs = "fare_id,price,currency_type,payment_method,transfers,transfer_duration\nF,3.00,USD,0,1,3600\n"
        self.assertEqual(self.fare(attrs, "fare_id,route_id\nF,\n", "1", "2"), 3.0)


# ---------------------------------------------------------------------------------------------------- ext-4
NIAGARA = "Niagara Region Transit (incl. WEGO)"
# the Niagara feed's own fare table (cached 2026-10): fare 1 local 3.50, fare 2 WEGO 17.00, both unlimited transfers
NIAGARA_FARES = ("fare_id,price,currency_type,payment_method,transfers,transfer_duration\n"
                 "2,17.00,CAD,0,,1440\n1,3.50,CAD,0,,120\n")
NIAGARA_RULES = "fare_id,route_id,origin_id,destination_id,contains_id\n1,104,,,\n2,602,,,\n2,603,,,\n"


class WegoPassBesideLocalBus(unittest.TestCase):
    def fare(self, *routes):
        net = fake_net(NIAGARA, fare_model(NIAGARA_FARES, NIAGARA_RULES), [""])
        legs = [leg(NIAGARA, "Niagara Region Transit", r, 36000 + 1800 * k, 0, 0) for k, r in enumerate(routes)]
        return th.journey_fare(net, legs, ONTARIO["flat_fares"], "card", ONTARIO.get("card_discount"),
                               ONTARIO["transfer_rules"], DAY)

    def test_local_bus_then_wego_pays_both(self):
        total, _cur, parts, complete = self.fare("104", "603")
        self.assertEqual((total, complete), (19.5, True))                                   # was 3.50
        self.assertEqual([(p["fare"], p["source"]) for p in parts], [(3.5, "GTFS"), (16.0, "region table, card")])

    def test_wego_then_local_bus_pays_both(self):
        self.assertEqual(self.fare("603", "104")[0], 19.5)                                  # was 17.00 (GTFS)

    def test_two_wego_lines_are_one_pass(self):
        self.assertEqual(self.fare("602", "603")[0], 16.0)


# ---------------------------------------------------------------------------------------------------- ext-12
class MixedCurrencies(unittest.TestCase):
    def test_fares_in_two_currencies_are_not_added(self):
        class Net:
            fares, fare_urls, stop_zone = {}, {}, [""]
        flat = {"AgencyCA": {"card": 5.0, "currency": "CAD"}, "AgencyUS": {"card": 3.0, "currency": "USD"}}
        legs = [leg("ca", "AgencyCA", "1", "10:00", 0, 0), leg("us", "AgencyUS", "2", "10:40", 0, 0)]
        total, cur, parts, complete = th.journey_fare(Net, legs, flat)
        self.assertEqual((total, cur, complete), (None, None, False))                     # was (8.0, 'CAD', True)
        self.assertEqual([(p["fare"], p["currency"]) for p in parts], [(5.0, "CAD"), (3.0, "USD")])
        txt = th.fare_text({"transit_fare": total, "fare_currency": cur, "fare_parts": parts, "fare_complete": complete,
                            "ride_cost": 20.0}, "CAD", True)
        self.assertIn("5.00 CAD + 3.00 USD", txt)
        self.assertIn("not added up: different currencies", txt)
        self.assertNotIn("total ~", txt)


# ---------------------------------------------------------------------------------------------------- gem-G2B
class RefundedFareGivesNoWindow(unittest.TestCase):
    def group(self, agency, depart, fare, first="A", last="B", routes=("1",)):
        return {"agency": agency, "feed": agency.lower(), "depart": hm(depart), "first_stop": first, "last_stop": last,
                "routes": set(routes), "fare": fare}

    def test_later_legs_run_from_the_go_fare_not_the_refunded_local_fare(self):
        g = [self.group("MiWay", "08:00", 3.5), self.group("GO Transit", "08:30", 6.0),
             self.group("Brampton Transit", "09:30", 3.55, last="Argentia Rd"),
             self.group("Milton Transit", "10:15", 4.25, first="Argentia Rd at Second Line", routes=("21",))]
        th.apply_transfers(g, GTHA["transfer_rules"], "card", dt.date(2026, 10, 13))
        self.assertEqual([x["charged"] for x in g], [0.0, 6.0, 0.0, 0.0])                   # Milton was 4.25
        self.assertIn("free from GO Transit", g[2]["note"])                                # was "free from MiWay"

    def test_one_go_tap_still_credits_every_local_fare_before_it(self):
        # unchanged (how many fares one GO tap credits is an open owner decision): MiWay and YRT both credited back
        g = [self.group("MiWay", "08:00", 3.5), self.group("York Region Transit", "08:30", 4.25),
             self.group("GO Transit", "09:00", 9.05)]
        th.apply_transfers(g, GTHA["transfer_rules"], "card", dt.date(2026, 10, 13))
        self.assertEqual([x["charged"] for x in g], [0.0, 0.0, 9.05])


# ---------------------------------------------------------------------------------------- hubs-pricing-io-11
class ContactlessNull(unittest.TestCase):
    class Net:
        fares, fare_urls, stop_zone = {}, {}, [""]

    def test_agency_that_refuses_a_bank_card_tap_costs_cash(self):
        # Route 2 (Barrie to Wasaga Beach): not route 4, which has its own "Simcoe County Linx 4" entry
        legs = [leg("linx", "Simcoe County Linx", "2", "10:00", 0, 0)]
        self.assertIsNone(ONTARIO["flat_fares"]["Simcoe County Linx"]["contactless"])
        self.assertEqual(th.journey_fare(self.Net, legs, ONTARIO["flat_fares"], "contactless")[0], 6.0)   # was 5.40
        self.assertEqual(th.journey_fare(self.Net, legs, ONTARIO["flat_fares"], "card")[0], 5.4)
        four = [leg("linx", "Simcoe County Linx", "4", "10:00", 0, 0)]
        self.assertEqual(th.journey_fare(self.Net, four, ONTARIO["flat_fares"], "contactless")[0], 2.0)   # was 1.80

    def test_agency_with_no_contactless_key_still_costs_the_card_price(self):
        self.assertNotIn("contactless", GTHA["flat_fares"]["TTC"])
        legs = [leg("ttc", "TTC", "1", "10:00", 0, 0)]
        self.assertEqual(th.journey_fare(self.Net, legs, GTHA["flat_fares"], "contactless")[0], 3.3)


# ------------------------------------------------------------------------------------- architecture-product-2
def run(args, as_json=True):
    out = io.StringIO()
    ok = mock.patch.object(th, "check_network", side_effect=lambda hosts, timeout=6: {h: "ok" for h in hosts})
    with ok, redirect_stdout(out), redirect_stderr(io.StringIO()):
        th.main(args + (["--json"] if as_json else []))
    return json.loads(out.getvalue()) if as_json else out.getvalue()


def priced_rides(costs):
    """Patch trip_hubs.ride_cost: a ride ending within 150 m of a point in `costs` costs that much."""
    real = th.ride_cost

    def fake(pricing, km, seconds, ends=None):
        for (lat, lon), c in costs.items():
            if ends and th.haversine_m(ends[1][0], ends[1][1], lat, lon) < 150:
                return c
        return real(pricing, km, seconds, ends)
    return mock.patch.object(th, "ride_cost", side_effect=fake)


ORIGIN, DEST = (43.4000, -79.8000), (43.6000, -79.8000)
HUB_1, HUB_2, HUB_3 = (43.4300, -79.8000), (43.4600, -79.8000), (43.4900, -79.8000)
FARE_HEADER = "fare_id,price,currency_type,payment_method,transfers\n"


def plan_args(p, *extra):
    return ["--origin", "%.4f,%.4f" % ORIGIN, "--dest", "%.4f,%.4f" % DEST, "--depart", "2026-10-15 10:00",
            "--feed", "t=" + p, "--radius-km", "15", "--origin-walk-m", "300"] + list(extra) + BASE


class RankingCountsTheFare(unittest.TestCase):
    """Ride mode ranks hubs on ride + fare, as car mode ranks on car cost + fare."""

    def feed(self, stops, trips, stop_times, zones=None, fares=None):
        p = os.path.join(tempfile.mkdtemp(dir=_TMP), "f.zip")
        make_feed(p, stops, trips, stop_times)
        if zones:
            add_files(p, {"stops.txt": "stop_id,stop_name,stop_lat,stop_lon,zone_id\n" + "".join(
                "%s,%s,%s,%s,%s\n" % (s[0], s[1], s[2], s[3], zones[s[0]]) for s in stops)})
        if fares:
            add_files(p, fares)
        return p

    def three_routes(self, fares):
        """Hub K (route R) arrives 11:05, Hub U (route S) 11:30, Hub Q (route T) 11:40."""
        stops = [("K", "Hub K", HUB_1[0], HUB_1[1]), ("U", "Hub U", HUB_2[0], HUB_2[1]),
                 ("Q", "Hub Q", HUB_3[0], HUB_3[1]), ("C", "Dest C", DEST[0], DEST[1])]
        return self.feed(stops, [("R", "WK", "k1", "C"), ("S", "WK", "u1", "C"), ("T", "WK", "q1", "C")],
                         [("k1", "10:40:00", "10:40:00", "K", 1, 0, 0), ("k1", "11:05:00", "11:05:00", "C", 2, 0, 0),
                          ("u1", "10:50:00", "10:50:00", "U", 1, 0, 0), ("u1", "11:30:00", "11:30:00", "C", 2, 0, 0),
                          ("q1", "10:55:00", "10:55:00", "Q", 1, 0, 0), ("q1", "11:40:00", "11:40:00", "C", 2, 0, 0)],
                         fares=fares)

    def test_cheaper_total_on_the_same_coach_is_listed(self):
        # one coach: Hub Y (dearer ride, 4.00 fare) then Hub X (cheaper ride, 12.00 fare), same arrival
        stops = [("Y", "Hub Y", HUB_1[0], HUB_1[1]), ("X", "Hub X", HUB_2[0], HUB_2[1]),
                 ("C", "Dest C", DEST[0], DEST[1])]
        p = self.feed(stops, [("R", "WK", "c1", "C")],
                      [("c1", "10:40:00", "10:40:00", "Y", 1, 0, 0), ("c1", "10:50:00", "10:50:00", "X", 2, 0, 0),
                       ("c1", "11:30:00", "11:30:00", "C", 3, 0, 0)],
                      zones={"Y": "1", "X": "2", "C": "3"},
                      fares={"fare_attributes.txt": FARE_HEADER + "a,4.00,CAD,0,\nb,12.00,CAD,0,\n",
                             "fare_rules.txt": "fare_id,origin_id,destination_id\na,1,3\nb,2,3\n"})
        with priced_rides({DEST: 60.0, HUB_1: 24.0, HUB_2: 20.0}):   # Y totals 28, X 32
            res = run(plan_args(p, "--min-saving", "0"))
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub Y"])                     # was ["Hub X"]
        self.assertEqual(res["pareto"][0]["transit_fare"], 4.0)

    def test_option_that_riding_all_the_way_beats_on_cost_and_time_is_not_a_trade_off(self):
        stops = [("H", "Hub H", HUB_1[0], HUB_1[1]), ("C", "Dest C", DEST[0], DEST[1])]
        p = self.feed(stops, [("R", "WK", "h1", "C")],
                      [("h1", "10:40:00", "10:40:00", "H", 1, 0, 0), ("h1", "11:30:00", "11:30:00", "C", 2, 0, 0)],
                      fares={"fare_attributes.txt": FARE_HEADER + "a,12.00,CAD,0,\n"})
        with priced_rides({DEST: 25.0, HUB_1: 15.0}):   # the ride saves 10 (over 20%), ride + fare costs 2 more
            res = run(plan_args(p))
        self.assertEqual(res["pareto"] + res["others"], [])                                # was listed
        self.assertTrue(any("ride + fare saves less than" in w for w in res["warnings"]), res["warnings"])

    def test_all_lists_the_fare(self):
        stops = [("H", "Hub H", HUB_1[0], HUB_1[1]), ("C", "Dest C", DEST[0], DEST[1])]
        p = self.feed(stops, [("R", "WK", "h1", "C")],
                      [("h1", "10:40:00", "10:40:00", "H", 1, 0, 0), ("h1", "11:30:00", "11:30:00", "C", 2, 0, 0)],
                      fares={"fare_attributes.txt": FARE_HEADER + "a,4.00,CAD,0,\n"})
        with priced_rides({DEST: 60.0, HUB_1: 20.0}):
            txt = run(plan_args(p, "--min-saving", "0", "--all"), as_json=False)
        line = next(ln for ln in txt.splitlines() if ln.startswith("  Hub H  book by"))
        self.assertIn("transit fare 4.00", line)                                           # was absent
        self.assertIn("BEST TRADE-OFFS (no other hub is both cheaper, fare included, and earlier)", txt)

    def test_unknown_fare_counts_as_the_dearest_known_fare(self):
        # route S has no fare rule: its fare is unknown, so Hub U ranks as if it cost route T's 20.00, the dearest
        # known fare; ranked on its ride alone (15 against Hub K's 20) it was a trade-off
        p = self.three_routes({"fare_attributes.txt": FARE_HEADER + "a,10.00,CAD,0,\nc,20.00,CAD,0,\n",
                               "fare_rules.txt": "fare_id,route_id\na,R\nc,T\n"})
        with priced_rides({DEST: 60.0, HUB_1: 20.0, HUB_2: 15.0, HUB_3: 30.0}):   # K 30; U 15 + unknown; Q 50
            res = run(plan_args(p, "--min-saving", "0"))
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub K"])                     # was ["Hub K", "Hub U"]
        self.assertIsNone(next(o for o in res["others"] if o["hub"] == "Hub U")["transit_fare"])

    def test_approximate_fare_is_not_ranked_as_exact(self):
        # Hub U's 5.00 is one point on a distance scale (a region file's "approx"): it ranks at no less than the
        # dearest exact fare (route T's 20.00), so the later Hub U is no trade-off against Hub K (exact: 15 + 5)
        p = self.three_routes({"fare_attributes.txt": FARE_HEADER + "a,10.00,CAD,0,\nb,5.00,CAD,0,\nc,20.00,CAD,0,\n",
                               "fare_rules.txt": "fare_id,route_id\na,R\nb,S\nc,T\n"})
        real = th.journey_fare

        def scale_fare(net, legs, *a, **k):
            total, cur, parts, complete = real(net, legs, *a, **k)
            if legs and legs[0]["route"] == "S":
                for part in parts:
                    part["approx"] = "distance-based: $5 to $20"
            return total, cur, parts, complete
        with priced_rides({DEST: 60.0, HUB_1: 20.0, HUB_2: 15.0, HUB_3: 30.0}), \
                mock.patch.object(th, "journey_fare", side_effect=scale_fare):
            res = run(plan_args(p, "--min-saving", "0"))
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub K"])                     # was ["Hub K", "Hub U"]
        u = next(o for o in res["others"] if o["hub"] == "Hub U")
        self.assertEqual((u["transit_fare"], u["fare_complete"]), (5.0, True))

    def test_fare_bound_ignores_a_hub_dearer_than_riding_all_the_way(self):
        # review follow-up: Hub Q (ride 30 + exact 60) costs more than riding all the way (60), so it is never
        # listed and its fare does not raise Hub U's approximate 5.00: U ranks at 15 + 10 (Hub K's exact fare)
        p = self.three_routes({"fare_attributes.txt": FARE_HEADER + "a,10.00,CAD,0,\nb,5.00,CAD,0,\nc,60.00,CAD,0,\n",
                               "fare_rules.txt": "fare_id,route_id\na,R\nb,S\nc,T\n"})
        real = th.journey_fare

        def scale_fare(net, legs, *a, **k):
            total, cur, parts, complete = real(net, legs, *a, **k)
            if legs and legs[0]["route"] == "S":
                for part in parts:
                    part["approx"] = "distance-based: $5 to $20"
            return total, cur, parts, complete
        with priced_rides({DEST: 60.0, HUB_1: 20.0, HUB_2: 15.0, HUB_3: 30.0}), \
                mock.patch.object(th, "journey_fare", side_effect=scale_fare):
            res = run(plan_args(p))   # the default --min-saving: 20% of riding all the way, 12
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub K", "Hub U"])            # was ["Hub K"]
        self.assertNotIn("Hub Q", [o["hub"] for o in res["pareto"] + res["others"]])

    # ------------------------------------------------------------------------------------------- hubs-engine-3
    def test_near_equal_rides_across_a_bucket_edge_are_one_trade_off(self):
        # ride all the way 83.06 (step 4.153), so the old bucket edge sat at 4 x 4.153 = 16.61: Hub A 16.66 arrives
        # 11:05, Hub B 16.56 arrives 11:45; 0.10 cheaper for 40 minutes later is no trade-off
        stops = [("A", "Hub A", HUB_1[0], HUB_1[1]), ("B", "Hub B", HUB_2[0], HUB_2[1]),
                 ("C", "Dest C", DEST[0], DEST[1])]
        p = self.feed(stops, [("R", "WK", "a1", "C"), ("S", "WK", "b1", "C")],
                      [("a1", "10:40:00", "10:40:00", "A", 1, 0, 0), ("a1", "11:05:00", "11:05:00", "C", 2, 0, 0),
                       ("b1", "11:10:00", "11:10:00", "B", 1, 0, 0), ("b1", "11:45:00", "11:45:00", "C", 2, 0, 0)])
        with priced_rides({DEST: 83.06, HUB_1: 16.66, HUB_2: 16.56}):
            res = run(plan_args(p, "--min-saving", "0"))
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub A"])                     # was ["Hub A", "Hub B"]

    def test_tolerant_front_has_no_bucket_edges(self):
        # (time, cost): equal costs within the step, wherever a multiple of the step falls between them
        pts = [(1, 16.66), (2, 16.56), (3, 12.0), (3, 11.0), (4, 10.5)]
        for step in (4.153, 1.0, 0.5):
            front = th.tolerant_front(pts, lambda x: x[0], lambda x: x[1], step)
            self.assertNotIn((2, 16.56), front, step)
            self.assertNotIn((3, 12.0), front, step)          # same time, dearer: never the faster pick
            self.assertEqual(front[0], (1, 16.66))
        self.assertEqual(th.tolerant_front(pts, lambda x: x[0], lambda x: x[1], 1.0), [(1, 16.66), (3, 11.0)])
        self.assertEqual(th.tolerant_front(pts, lambda x: x[0], lambda x: x[1], 0.4),
                         [(1, 16.66), (3, 11.0), (4, 10.5)])


# ------------------------------------------------------------------------------------ architecture-product-10
class RankingNoFinerThanTheRateCard(unittest.TestCase):
    """Ride prices are rate-card estimates, so no list splits hubs on less than the BEST TRADE-OFFS step."""

    def feed(self):
        # one bus: Hub A 10:40, M 10:50, Hub B 10:55, Dest C 11:45; a train from M at 10:58 reaches C at 11:05
        stops = [("A", "Hub A", HUB_1[0], HUB_1[1]), ("M", "Change M", HUB_2[0], HUB_2[1]),
                 ("B", "Hub B", HUB_3[0], HUB_3[1]), ("C", "Dest C", DEST[0], DEST[1])]
        p = os.path.join(tempfile.mkdtemp(dir=_TMP), "f.zip")
        make_feed(p, stops, [("BUS", "WK", "b1", "C"), ("TRAIN", "WK", "t1", "C")],
                  [("b1", "10:40:00", "10:40:00", "A", 1, 0, 0), ("b1", "10:50:00", "10:50:00", "M", 2, 0, 0),
                   ("b1", "10:55:00", "10:55:00", "B", 3, 0, 0), ("b1", "11:45:00", "11:45:00", "C", 4, 0, 0),
                   ("t1", "10:58:00", "10:58:00", "M", 1, 0, 0), ("t1", "11:05:00", "11:05:00", "C", 2, 0, 0)])
        return p

    def test_same_bus_a_few_cents_cheaper_is_not_listed(self):
        # ride all the way 60 (step 3): Hub B boards Hub A's bus, arrives 40 minutes later, saves 0.50
        with priced_rides({DEST: 60.0, HUB_1: 20.0, HUB_2: 40.0, HUB_3: 19.5}):
            res = run(plan_args(self.feed(), "--min-saving", "0"))
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub A"])
        self.assertNotIn("Hub B", [o["hub"] for o in res["others"]])                       # was listed
        self.assertEqual(res["hidden_hubs"]["same_vehicle"], 2)   # with Change M, dearer on Hub A's train; was 1

    def test_same_bus_more_than_a_step_cheaper_is_listed(self):
        with priced_rides({DEST: 60.0, HUB_1: 20.0, HUB_2: 40.0, HUB_3: 16.0}):   # saves 4, more than the step
            res = run(plan_args(self.feed(), "--min-saving", "0"))
        self.assertEqual([o["hub"] for o in res["pareto"]], ["Hub A", "Hub B"])


if __name__ == "__main__":
    unittest.main()
