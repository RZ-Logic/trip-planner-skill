"""Offline tests for the 2026-10-10 parking review fixes (F1-F10: Green P open/closed lots, ranking, widening,
post-2019 carparks, check hints, Saturday special, often-full fallback; R1-R8: Google review rate extraction,
matching and attribution).

Run: python -m unittest tests.test_parking_fixes -v
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
import shutil
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_common as dc  # noqa: E402
import drive_plan as dp  # noqa: E402
import parking as pk  # noqa: E402
import place_reviews as pr  # noqa: E402

SAT = dt.datetime(2026, 10, 17, 10, 0)
SUN = dt.datetime(2026, 10, 18, 10, 0)
FERRY = (43.6406, -79.3756)
MARKET = (43.6487, -79.3715)          # St. Lawrence Market
TOR = pk.cfg()["toronto"]

CP43_DETAILS = {"periods": [
    {"title": "Monday - Sunday & Holidays", "rates": [{"when": "Day Maximum (7am - 6pm)", "rate": "$15.00"},
                                                     {"when": "Night Maximum (6pm - 7am)", "rate": "$7.00"}],
     "notes": []},
    {"title": "SATURDAY SPECIAL", "rates": [],
     "notes": ["$1.00 EACH HOUR FOR THE FIRST 2 HOURS - REGULAR", "RATES THEREAFTER"]}],
    "addenda": ["(ENTRY BETWEENÂ 5am - 6pm ONLY)Â "]}


def cp(cid, address, lat, lng, half="2.00", kind="garage", details=None):
    return {"id": cid, "address": address, "lat": str(lat), "lng": str(lng), "rate_half_hour": half,
            "carpark_type": kind, "is_ttc": False, "capacity": "100", "max_height": "",
            "slug": "https://parking.greenp.com/carpark/%s_x/" % cid,
            "rate_details": details or {"periods": []}}


def occupancy(extra=("CP43",), n=60):
    recs = [{"Car Park Number": "CP%d" % (1000 + i), "Average Daily Peak Occupancy %": "50%", "Year": "2025",
             "Quarter": "Q4"} for i in range(n)]
    recs += [{"Car Park Number": x, "Average Daily Peak Occupancy %": "24%", "Year": "2026", "Quarter": "Q1"}
             for x in extra]
    return {"result": {"records": recs}}


def fake_fetch(routes):
    def fetch(url, data=None, **kw):
        for key, reply in routes:
            if key in url:
                return json.dumps(reply).encode("utf-8")
        raise urllib.error.URLError("offline test: %s" % url)
    return fetch


def find(carparks, dest=FERRY, when=SUN, stay=300, occ=None, overpass=None):
    routes = [("green-p-parking-2019", {"carparks": carparks}),
              ("datastore_search", occ if occ is not None else occupancy()),
              ("overpass-api.de", overpass if overpass is not None else {"elements": []})]
    warnings = []
    tmp = tempfile.mkdtemp()
    try:
        with mock.patch.object(pk, "PLACES_DIR", tmp), mock.patch.object(dc, "fetch", fake_fetch(routes)), \
                mock.patch.object(pk, "city_lots", return_value=([], [])), mock.patch.object(pk.time, "sleep"):
            res = pk.find_parking(dest[0], dest[1], when, stay, {}, warnings)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res, warnings


def by_name(res):
    return dict((o["name"], o) for o in res["options"])


# --------------------------------------------------------------------------- F1, F6

class OpenAndClosedLists(unittest.TestCase):
    def test_open_list_counts_as_seen(self):
        ids = [x["id"] for x in TOR["open"]]
        self.assertEqual(sorted(ids, key=int), ["11", "181", "230", "267", "275", "283", "284", "285"])
        for x in TOR["open"]:
            self.assertEqual(x["verified"], "2026-10-10")
            self.assertTrue(x["url"].startswith("https://parking.greenp.com/carpark/%s_" % x["id"]))
        self.assertFalse(pk.maybe_closed(cp(283, "100 Cooperage Street", 0, 0), set()))
        self.assertTrue(pk.maybe_closed(cp(271, "271 Front Street East", 0, 0), set()))

    def test_open_lot_ranks_by_price_not_flagged(self):
        res, _ = find([cp(43, "2 Church Street (St. Lawrence Garage)", 43.6420, -79.3740),
                       cp(283, "100 Cooperage Street", 43.6410, -79.3750, half="0.50")])
        o = by_name(res)["Green P 100 Cooperage Street"]
        self.assertFalse(o["maybe_closed"])
        self.assertEqual(res["options"][0]["name"], "Green P 100 Cooperage Street")

    def test_263_closed_with_notice(self):
        closed = dict((x["id"], x) for x in TOR["closed"])
        self.assertEqual(closed["263"]["closed"], "2025-12-01")
        self.assertTrue(closed["263"]["source"].startswith("https://parking.greenp.com/news/permanent-closure"))
        res, _ = find([cp(263, "130 Elizabeth Street - Toronto Coach Terminal", 43.6410, -79.3750)])
        self.assertNotIn("Green P 130 Elizabeth Street - Toronto Coach Terminal", by_name(res))

    def test_flagged_priced_lot_beats_facility_and_unknown(self):
        overpass = {"elements": [
            {"type": "way", "id": 5, "center": {"lat": 43.6398, "lon": -79.3775},
             "tags": {"amenity": "parking", "fee": "no", "access": "permissive", "name": "Clinic lot"}},
            {"type": "way", "id": 6, "center": {"lat": 43.6396, "lon": -79.3782},
             "tags": {"amenity": "parking", "fee": "yes", "name": "Mystery lot"}}]}
        res, _ = find([cp(999, "9 Gone Street", 43.6410, -79.3750)], overpass=overpass)
        names = [o["name"] for o in res["options"]]
        self.assertEqual(names[0], "Green P 9 Gone Street")
        self.assertTrue(res["options"][0]["maybe_closed"])
        self.assertEqual(dp.targets(res["options"])[0]["name"], "Green P 9 Gone Street")
        self.assertLess(names.index("Green P 9 Gone Street"), names.index("Clinic lot"))
        self.assertLess(names.index("Clinic lot"), names.index("Mystery lot"))

    def test_note_asserts_no_direction(self):
        note = TOR["greenp_note"]
        self.assertNotIn("higher;", note)
        self.assertIn("current prices differ", note)
        self.assertIn("some weekend maximums lower", note)
        self.assertIn("flagged lot is ranked after the unflagged priced lots", TOR["occupancy_about"])


# --------------------------------------------------------------------------- F2

class Widening(unittest.TestCase):
    def test_widens_when_every_walkable_priced_lot_is_flagged(self):
        far = cp(43, "2 Church Street (St. Lawrence Garage)", FERRY[0] + 0.0088, FERRY[1])   # ~980 m, ~1270 m walk
        res, warnings = find([cp(999, "9 Gone Street", 43.6410, -79.3750), far])
        self.assertEqual(res["radius_m"], 1500)
        self.assertIn("priced lots within 800 m may have closed; widened the search to 1500 m", warnings)
        names = [o["name"] for o in res["options"]]
        self.assertIn("Green P 9 Gone Street", names)                     # kept in the pool
        self.assertIn("Green P 2 Church Street (St. Lawrence Garage)", names)
        self.assertFalse(res["options"][0]["maybe_closed"])

    def test_no_widen_when_an_open_priced_lot_is_walkable(self):
        res, warnings = find([cp(999, "9 Gone Street", 43.6410, -79.3750),
                              cp(43, "2 Church Street (St. Lawrence Garage)", 43.6420, -79.3740)])
        self.assertEqual(res["radius_m"], 800)
        self.assertFalse([w for w in warnings if "widened" in w])


# --------------------------------------------------------------------------- F3

class NewCarparks(unittest.TestCase):
    def test_data(self):
        ids = sorted((x["id"] for x in TOR["new_carparks"]), key=int)
        self.assertEqual(ids, ["72", "221", "262", "304", "426", "650", "664"])
        for x in TOR["new_carparks"]:
            self.assertTrue(x["url"].startswith("https://parking.greenp.com/carpark/"))
            self.assertEqual(x["rule"]["as_of"], "2026-10-10")
            self.assertTrue(43.58 < x["lat"] < 43.86 and -79.64 < x["lon"] < -79.11)

    def test_st_lawrence_market_north_listed_and_priced(self):
        res, _ = find([cp(43, "2 Church Street (St. Lawrence Garage)", 43.646440, -79.373066)],
                      dest=MARKET, when=SAT, stay=180, occ=occupancy(("CP43", "CP72")))
        o = by_name(res)["Green P 92 Front Street East (St. Lawrence Market North)"]
        self.assertEqual(o["cost"], 9.0)                                  # Sat: 3 h at $6/h capped at $9
        self.assertEqual(o["confidence"], "curated")
        self.assertIn("(Green P page, 2026-10-10)", o["cost_text"])
        self.assertIsNone(o["check"])                                     # current curated price
        self.assertEqual(o["greenp_id"], "72")
        self.assertIn("peak occupancy 24% (2026 Q1)", o["notes"])
        self.assertTrue(any(s.startswith("Green P carparks opened after 2019") for s in res["sources"]))
        # weekday: $18 day maximum
        cost = pk.price_rule(o and pk._RULES[(o["name"], o["lat"], o["lon"])], dt.datetime(2026, 10, 14, 9, 0),
                             540, set())[0]
        self.assertEqual(cost, 18.0)

    def test_unpriced_new_carpark(self):
        t = dict(TOR, new_carparks=[{"id": "999", "address": "1 New Street", "lat": FERRY[0], "lon": FERRY[1],
                                     "kind": "garage", "url": "https://parking.greenp.com/carpark/999_1-new-street/"}])
        lots = pk.new_carpark_lots(FERRY[0], FERRY[1], SUN, 120, set(), 1000, None, {}, set(), t)
        self.assertEqual(lots[0]["cost_text"], "price unknown, opened after 2019")
        hint = pk.check_hint(lots[0])
        self.assertEqual(hint["why"], "price unknown, opened after 2019")
        self.assertEqual(hint["url"], "https://parking.greenp.com/carpark/999_1-new-street/")
        self.assertEqual(pk.new_carpark_lots(FERRY[0], FERRY[1], SUN, 120, set(), 1000, None, {}, {"999"}, t), [])

    def test_kept_when_2019_file_fails(self):
        routes = [("datastore_search", occupancy())]
        warnings = []
        with mock.patch.object(dc, "fetch", fake_fetch(routes)):
            lots, srcs = pk.toronto_lots(MARKET[0], MARKET[1], SAT, 180, set(), 1000, None, warnings)
        self.assertIn("Green P 92 Front Street East (St. Lawrence Market North)", [x["name"] for x in lots])
        self.assertTrue(srcs)


# --------------------------------------------------------------------------- F4, F5, F9

class CheckHints(unittest.TestCase):
    def test_greenp_url_and_padded_search(self):
        res, _ = find([cp(43, "2 Church Street (St. Lawrence Garage)", 43.6420, -79.3740)])
        c = res["options"][0]["check"]
        self.assertEqual(c["url"], "https://parking.greenp.com/carpark/43_x/")
        self.assertEqual(c["search"], "Green P Carpark 043 2 Church Street St. Lawrence Garage")
        self.assertEqual(pk.greenp_search({"id": "A1", "address": "x"}), "Green P Carpark A1 x")

    def test_check_list_keeps_first_flagged_lot_past_three(self):
        opts = [{"name": "L%d" % i, "maybe_closed": i == 5, "check": {"why": "w", "url": None, "search": None}}
                for i in range(1, 7)]
        self.assertEqual([c["n"] for c in pk.check_list(opts)], [1, 2, 3, 5])
        opts[0]["check"] = None
        self.assertEqual([c["n"] for c in pk.check_list(opts)], [2, 3, 4, 5])

    def osm(self, tags, lat=43.6410, lon=-79.3750, when=SUN):
        data = {"elements": [{"type": "way", "id": 7, "center": {"lat": lat, "lon": lon},
                              "tags": dict({"amenity": "parking"}, **tags)}]}
        with mock.patch.object(pk, "_overpass", return_value=data):
            lots, _ = pk.osm_lots(FERRY[0], FERRY[1], when, 120, set(), 800, None, [])
        return lots[0], pk.check_hint(lots[0])

    def test_osm_search_has_street_and_city(self):
        _l, h = self.osm({"fee": "yes", "name": "Harbour lot", "addr:housenumber": "10", "addr:street": "Bay Street"})
        self.assertEqual(h["search"], "Harbour lot 10 Bay Street Toronto parking rates")

    def test_brand_only_needs_address(self):
        _l, h = self.osm({"fee": "yes", "name": "Impark"})
        self.assertIsNone(h["search"])
        self.assertTrue(h["url"].startswith("https://www.openstreetmap.org/way/7"))
        _l, h = self.osm({"fee": "yes", "name": "Impark", "addr:housenumber": "25", "addr:street": "York Street"})
        self.assertEqual(h["search"], "Impark 25 York Street Toronto parking rates")
        self.assertFalse(pk._brand_only("Bay Cat Hospital Parking"))
        self.assertTrue(pk._brand_only("SP+ parking garage"))

    def test_generic_lot_search_by_street(self):
        _l, h = self.osm({"fee": "yes", "addr:street": "Queens Quay West"})
        self.assertEqual(h["search"], "parking near Queens Quay West Toronto rates")
        self.assertTrue(h["url"].startswith("https://www.openstreetmap.org/"))

    def test_osm_stale_text_is_dated(self):
        _l, h = self.osm({"fee": "yes", "name": "Old lot", "charge": "2019: 3.00 CAD/hour"})
        self.assertEqual(h["why"], "old price (2019)")
        lot, h = self.osm({"fee": "yes", "name": "Checked lot", "charge": "3.00 CAD/hour", "check_date:charge": "2020-05"})
        self.assertEqual((lot["confidence"], h["why"]), ("stale", "old price (2020)"))


# --------------------------------------------------------------------------- F7

class SaturdaySpecial(unittest.TestCase):
    def rule(self):
        return pk._greenp_rule(cp(43, "2 Church Street", 0, 0, half="2.50", details=CP43_DETAILS))

    def test_parsed(self):
        r = self.rule()
        self.assertEqual(r["intro"], {"days": [5], "hourly": 1.0, "minutes": 120, "from": "05:00", "to": "18:00"})
        self.assertTrue(all(p.get("days") in (None, list(range(7))) for p in r["periods"]))  # Saturday stays priced
        self.assertIn("Sat special $1.00/h for the first 2 h (entry 05:00-18:00)", pk.describe_rule(r))

    def test_priced(self):
        r = self.rule()
        cost, basis, _ = pk.price_rule(r, SAT, 180, set())
        self.assertEqual(cost, 7.0)                                       # $2 + 2 x $2.50, not $15
        self.assertTrue(basis.startswith("Sat special: $1.00/h for the first 2 h"))
        self.assertEqual(pk.price_rule(r, SAT, 60, set())[0], 1.0)
        self.assertEqual(pk.price_rule(r, SUN, 180, set())[0], 15.0)       # Sunday: regular
        self.assertEqual(pk.price_rule(r, SAT.replace(hour=19), 180, set())[0], 7.0)   # after entry window: night max
        self.assertEqual(pk.price_rule(r, SAT.replace(hour=9), 600, set())[0], 20.0)   # day max beats the special

    def test_curated_weekend_special(self):
        r = next(x["rule"] for x in TOR["new_carparks"] if x["id"] == "304")
        self.assertEqual(pk.price_rule(r, SAT, 120, set())[0], 6.0)
        self.assertEqual(pk.price_rule(r, SAT - dt.timedelta(days=1), 120, set())[0], 14.0)


# --------------------------------------------------------------------------- F8, F10 (plan)

def plan_with(options):
    import test_drive_plan as tdp
    m = types.ModuleType("parking")

    def find_parking(lat, lon, arrive, stay_min, opts, warnings):
        opts_ = [dict(o) for o in options]
        return {"options": opts_, "place_rules": [], "reservations": [], "sources": [], "radius_m": 800,
                "check": pk.check_list(opts_)}
    m.find_parking = find_parking
    m.check_list = pk.check_list
    with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.dict(sys.modules, {"parking": m}):
        res = dp.plan(tdp.args_for(*tdp.BASE, "--dest", "%f,%f" % FERRY, "--depart", "2026-10-11 10:00",
                                   "--stay-hours", "4", "--park-and-ride", "off"))
        return res, dp.render(res), json.loads(dp.to_json(res))


def opt(name, cost, dlat, full=False, flagged=False):
    return {"name": name, "lat": FERRY[0] + dlat, "lon": FERRY[1], "walk_m": 300 + int(dlat * 1e5), "walk_min": 4,
            "search_min": 2, "cost": cost, "cost_text": "$%.2f for 4 h" % cost, "free": False, "fits_stay": True,
            "max_stay_min": None, "confidence": "stale", "source": "Green P 2019 open data", "notes": "",
            "often_full": full, "maybe_closed": flagged,
            "check": {"why": "may have closed" if flagged else "2019 price",
                      "url": "https://parking.greenp.com/carpark/1_x/", "search": "x"}}


class ArrivalNotes(unittest.TestCase):
    def test_often_full_names_a_fallback(self):
        res, text, data = plan_with([opt("Green P 10 Portland Street", 16.0, 0.001, full=True),
                                     opt("Green P 40 York Street", 20.0, 0.002),
                                     opt("Green P Other", 21.0, 0.003, full=True)])
        self.assertEqual(res["arrival"]["lot"], "Green P 10 Portland Street")   # still the cheapest
        self.assertEqual(data["arrival"]["fallback"]["name"], "Green P 40 York Street")
        self.assertIn("Green P 10 Portland Street is often full; fallback Green P 40 York Street ($20.00 for 4 h, "
                      "500 m walk).", text)

    def test_all_flagged_arrival_warns(self):
        res, text, data = plan_with([opt("Green P 9 Gone Street", 5.0, 0.001, flagged=True)])
        self.assertEqual(res["arrival"]["lot"], "Green P 9 Gone Street")
        self.assertTrue(data["arrival"]["maybe_closed"])
        self.assertIn("arrive at Green P 9 Gone Street (may have closed, check first) about", text)
        self.assertTrue(any(w.startswith("Green P 9 Gone Street may have closed; check its Green P page before "
                                         "leaving (https://parking.greenp.com/carpark/1_x/)") for w in res["warnings"]))

    def test_open_lot_preferred_and_no_note(self):
        res, text, data = plan_with([opt("Green P 9 Gone Street", 5.0, 0.001, flagged=True),
                                     opt("Green P Open", 15.0, 0.002)])
        self.assertEqual(res["arrival"]["lot"], "Green P Open")
        self.assertNotIn("maybe_closed", data["arrival"])
        self.assertNotIn("may have closed, check first", text)
        for ch in ("–", "—"):
            self.assertNotIn(ch, text)


# --------------------------------------------------------------------------- R1, R3, R4, R5, R6

def got(text):
    return [pr.mention_text(m) for m in pr.extract_mentions(text)]


class Extraction(unittest.TestCase):
    def test_r1_lot_numbers_are_not_prices(self):
        self.assertEqual(got("Parked at Lot 5 $10 for the day."), ["$10 per day"])
        self.assertEqual(got("Level P2 $8 parking after 6pm."), ["$8 evenings"])
        self.assertEqual(got("Gate 3 $12 parking all day."), ["$12 per day"])
        self.assertEqual(got("Stationnement 20 $ par jour."), ["$20 per day"])
        self.assertEqual(got("Stationnement 15-20 $ par jour."), ["$15-20 per day"])
        self.assertEqual([m["amount"] for m in pr.extract_mentions("Parking was 12 dollars.")], [12.0])
        self.assertEqual(got("Parking 10$ for 2 hours."), ["$10 for 2 hours"])

    def test_r3_fees_differences_and_other_services(self):
        self.assertEqual([m["amount"] for m in pr.extract_mentions("Parking $15 plus a $0.50 credit card fee.")], [15.0])
        self.assertEqual([m["amount"] for m in
                          pr.extract_mentions("Parking has a $1.25 service fee on top of the $9 rate.")], [9.0])
        self.assertEqual([m["amount"] for m in
                          pr.extract_mentions("$20 for parking, $5 cheaper than the lot next door.")], [20.0])
        self.assertEqual(got("EV charging at this parking garage cost $15."), [])
        self.assertEqual(got("Car wash in the parking garage was $20."), [])
        self.assertEqual(got("Parking was more than $10."), [])
        self.assertEqual(got("Parking $5 off with validation."), [])
        self.assertEqual(got("Got a refund of $10 for parking."), [])
        self.assertEqual([m["amount"] for m in pr.extract_mentions("They were charging $20 per day for parking.")], [20.0])

    def test_r4_nearest_context(self):
        self.assertEqual(got("Parking is $6 on weekends and $18 weekdays."), ["$6 weekends", "$18 weekdays"])
        self.assertEqual(got("Hotel guests pay $45 per night for parking."), ["$45 overnight"])
        self.assertEqual(got("On event nights they charge $30, so plan ahead."), ["$30 for events"])

    def test_r5_minutes_first_hour_and_wide_duration(self):
        self.assertEqual(got("Parking $2 per 20 minutes, max $24."), ["$2 per 20 min", "$24 per day"])
        self.assertEqual(got("Parking $1 per 15 min."), ["$1 per 15 min"])
        self.assertEqual(got("$3 for first hour then $4 every additional hour of parking."),
                         ["$3 per hour", "$4 per hour"])
        self.assertEqual(got("Parked here for 2 hours on Saturday, the machine charged me $10."), ["$10 for 2 hours"])
        self.assertEqual(got("Parking cost $12, which is a lot."), ["$12 (unit not stated)"])

    def test_r6_past_rates(self):
        self.assertEqual(got("Weekend max is $9.00 now, used to be $7."), ["$9 weekends"])
        self.assertEqual(got("Parking was previously $5 but now it is $8 per hour."), ["$8 per hour"])


# --------------------------------------------------------------------------- R2

class Matching(unittest.TestCase):
    def test_other_operator_and_valet_not_attached(self):
        green = {"name": "Green P 40 York Street", "lat": 43.6420, "lon": -79.3820, "kind": "garage", "cost": 25.0,
                 "address": ""}
        dlat = 70 / 111195.0
        impark = {"name": "Impark | 25 York St Garage", "lat": 43.6420 + dlat, "lon": -79.3820,
                  "address": "25 York St, Toronto, ON", "maps_url": "cid=A", "kind": "garage", "dist_m": 70,
                  "mentions": [{"amount": 45.0, "unit": "event", "date": "2026-08", "author": "A", "text": "x"}],
                  "summary": "reviews mention $45 for events (Aug 2026)"}
        valet = {"name": "Union Station Valet", "lat": 43.6420 - 45 / 111195.0, "lon": -79.3820,
                 "address": "65 Front St W, Toronto, ON", "maps_url": "cid=B", "kind": "lot", "dist_m": 45,
                 "mentions": [{"amount": 60.0, "unit": "day", "date": "2026-08", "author": "B", "text": "y"}],
                 "summary": "reviews mention $60 per day (Aug 2026)"}
        self.assertFalse(pr._similar(impark, green))
        extras = pr.match_lots([impark, valet], [green], dest=FERRY)
        self.assertNotIn("review_mentions", green)
        self.assertEqual(sorted(x["name"] for x in extras), ["Impark | 25 York St Garage", "Union Station Valet"])
        self.assertEqual(extras[0]["review_mentions"][0]["maps_url"], extras[0]["review_url"])

    def test_same_address_and_close_uncontested_still_match(self):
        lot = {"name": "Green P 2 Church Street (St. Lawrence Garage)", "lat": 43.6464, "lon": -79.3731,
               "kind": "garage", "cost": 15.0}
        p = {"name": "St Lawrence Garage", "lat": 43.6467, "lon": -79.3731, "address": "2 Church St, Toronto",
             "maps_url": "cid=1", "kind": "garage", "dist_m": 10,
             "mentions": [{"amount": 9.0, "unit": "weekend", "date": "2026-08", "author": "J", "text": "z"}],
             "summary": "s"}
        self.assertTrue(pr._similar(p, lot))
        self.assertEqual(pr.match_lots([p], [lot]), [])
        self.assertEqual(lot["review_mentions"][0]["place"], "St Lawrence Garage")
        # two unnamed places both 10 m from one lot: neither is attached by distance alone
        lot2 = {"name": "Some Garage", "lat": 43.6500, "lon": -79.3800, "kind": "garage"}
        q1 = dict(p, name="Alpha", address="", lat=43.65009, lon=-79.3800)
        q2 = dict(p, name="Beta", address="", lat=43.64991, lon=-79.3800)
        self.assertEqual(len(pr.match_lots([q1, q2], [lot2])), 2)
        self.assertNotIn("review_mentions", lot2)


# --------------------------------------------------------------------------- R7, R8

class ReviewDisplay(unittest.TestCase):
    def attach(self, options, extras, best):
        gotr = {"places": [{"name": "x", "mentions": [{"amount": 1.0}]}], "source": pr.SOURCE,
                "attribution": pr.ATTRIBUTION}
        fake = types.SimpleNamespace(review_prices=lambda *a, **k: gotr, match_lots=lambda *a, **k: extras,
                                     mention_text=pr.mention_text)
        parking = {"options": options, "radius_m": 800, "check": []}
        with mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "k"}), \
                mock.patch.object(dp, "_mod", side_effect=lambda n: fake if n == "place_reviews" else pk):
            return dp.attach_reviews(parking, FERRY, best, [])

    def test_review_lot_shown_with_many_priced_lots(self):
        opts = [{"name": "Lot %d" % i, "cost": 5.0 + i, "lat": 1, "lon": 1, "walk_m": 100} for i in range(6)]
        opts.append({"name": "Lot 9", "cost": None, "lat": 1, "lon": 1, "walk_m": 100})
        extra = {"name": "Far Review Garage", "lat": 1, "lon": 1, "walk_m": 700, "cost": None,
                 "review_mentions": [{"amount": 12.0, "unit": "day", "date": "2026-09", "author": "A",
                                      "review_url": "https://www.google.com/maps/reviews/r1", "place": "Far Review Garage"}],
                 "review_url": "https://maps.google.com/?cid=9"}
        out = self.attach(opts, [extra], opts[0])
        names = [o["name"] for o in out["options"]]
        self.assertEqual(names.index("Far Review Garage"), 4)
        self.assertTrue(dp.review_source(out).startswith("parking review rates: Google Maps (Places API). Rates"))
        L = dp.parking_lines({"parking": out, "parking_from": SUN, "parking_until": SUN, "stay_min": 60,
                              "parking_stay_min": 60}, SUN)
        text = "\n".join(L)
        self.assertIn("Google reviews mention $12 per day (Sep 2026, A, https://www.google.com/maps/reviews/r1), "
                      "unverified", text)

    def test_source_says_none_shown_when_matched_lot_is_past_five(self):
        opts = [{"name": "Lot %d" % i, "cost": 5.0 + i, "lat": 1, "lon": 1, "walk_m": 100} for i in range(7)]
        opts[6]["review_mentions"] = [{"amount": 9.0, "unit": "day", "date": "2026-09", "author": "A"}]
        opts[6]["check"] = {"why": "2019 price", "url": None, "search": None}
        out = self.attach(opts, [], opts[0])
        self.assertIn("none shown", dp.review_source(out))
        self.assertEqual(out["options"][6]["check"]["why"], "2019 price; Google reviews mention a rate (see its review line)")

    def test_review_text_groups_by_place(self):
        lot = {"name": "Green P 2 Church Street", "review_place": "P1", "review_url": "https://maps/p1",
               "review_mentions": [
                   {"amount": 9.0, "unit": "weekend", "date": "2026-08", "author": "Jane", "place": "P1",
                    "review_url": "https://maps/r1"},
                   {"amount": 20.0, "unit": "day", "date": "2026-07", "author": "Sam", "place": "P2",
                    "maps_url": "https://maps/p2"}]}
        self.assertEqual(dp.review_text(pr, lot),
                         "Google reviews of P1 mention $9 weekends (Aug 2026, Jane, https://maps/r1); "
                         "Google reviews of P2 mention $20 per day (Jul 2026, Sam, https://maps/p2), unverified")

    def test_review_text_counts_reviews_of_the_same_rate(self):
        # Muse item 10.h: three reviewers quoting $15 a day read the same as one
        def mk(who, ym):
            return {"amount": 15.0, "unit": "day", "date": ym, "author": who, "place": "P1",
                    "review_url": "https://maps/" + who}
        lot = {"name": "P1", "review_place": "P1", "review_mentions": [mk("A", "2026-09"), mk("B", "2026-09"),
                                                                       mk("C", "2026-05")]}
        self.assertEqual(dp.review_text(pr, lot),
                         "Google reviews mention $15 per day (Sep 2026, A and 2 more reviews, https://maps/A), unverified")
        lot["review_mentions"] = lot["review_mentions"][:2]
        self.assertIn("(Sep 2026, A and 1 more review, ", dp.review_text(pr, lot))
        lot["review_mentions"] = lot["review_mentions"][:1]
        self.assertEqual(dp.review_text(pr, lot), "Google reviews mention $15 per day (Sep 2026, A, https://maps/A), unverified")


if __name__ == "__main__":
    unittest.main()
