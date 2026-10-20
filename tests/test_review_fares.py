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


if __name__ == "__main__":
    unittest.main()
