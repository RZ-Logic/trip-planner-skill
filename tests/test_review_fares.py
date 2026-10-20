"""Regression tests for the review's fare findings in trip_hubs.py: the GTFS fare path (card discount, cheapest
and route-specific rules, transfers and transfer_duration), route-priced passes (WEGO), mixed currencies and
refunded transfers.

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
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trip_hubs as th  # noqa: E402
from test_engine import pin_clock  # noqa: E402

DAY = dt.date(2026, 10, 15)   # a Thursday
REGIONS = {r["id"]: r for r in th.load_regions()}
GTHA, ONTARIO = REGIONS["gtha"], REGIONS["ontario"]
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


if __name__ == "__main__":
    unittest.main()
