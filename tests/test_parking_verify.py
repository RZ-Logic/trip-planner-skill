"""Offline tests for closed Green P lots and the verify hints (parking.py, drive_plan.py).

Green P's 2019 carparks are joined to Toronto's TPA occupancy feed (2025 onward): a carpark missing from every
quarter of the feed may have closed (ranked after the unflagged priced lots, before unknown prices), a carpark
in parking.json toronto.closed is dropped, one in toronto.open counts as seen, and every option whose price is not
official and current carries a "check" hint.

Run: python -m unittest discover -s tests -v
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

SUN = dt.datetime(2026, 10, 11, 11, 0)
FERRY = (43.6406, -79.3756)


def cp(cid, address, lat, lng, half="2.00", ttc=False, kind="garage"):
    return {"id": cid, "address": address, "lat": str(lat), "lng": str(lng), "rate_half_hour": half,
            "carpark_type": kind, "is_ttc": ttc, "capacity": "100", "max_height": "",
            "slug": "https://parking.greenp.com/carpark/%s_x/" % cid, "rate_details": {"periods": []}}


GREENP = {"carparks": [
    cp(43, "2 Church Street (St. Lawrence Garage)", 43.6420, -79.3740),               # in the feed
    cp(999, "9 Gone Street ", 43.6410, -79.3750, half="0.25"),                       # missing: maybe closed
    cp(300, "185 Queens Quay East", 43.6405, -79.3745, half="0.25", kind="surface"),  # known closed: dropped
    cp(801, "Kipling South Lot - 400 Munster Avenue", 43.6415, -79.3760, half="1.00", ttc=True, kind="surface"),
    cp(194, "20 Ashbridges Bay Park Road (Parks)", 43.6400, -79.3765, half="1.00", kind="surface")]}


def occupancy(n=60, extra=("CP43",)):
    recs = [{"Car Park Number": "CP%d" % (1000 + i), "Average Daily Peak Occupancy %": "50%", "Year": "2025",
             "Quarter": "Q4"} for i in range(n)]
    recs += [{"Car Park Number": x, "Average Daily Peak Occupancy %": "16%", "Year": "2026", "Quarter": "Q1"}
             for x in extra]
    return {"result": {"records": recs}}


OVERPASS = {"elements": [
    {"type": "way", "id": 11, "center": {"lat": 43.6390, "lon": -79.3756},
     "tags": {"amenity": "parking", "fee": "yes", "name": "Harbour Square lot"}},
    {"type": "way", "id": 12, "center": {"lat": 43.6395, "lon": -79.3740}, "tags": {"amenity": "parking", "fee": "no"}}]}


def fake_fetch(routes):
    def fetch(url, data=None, **kw):
        for key, reply in routes:
            if key in url:
                return json.dumps(reply).encode("utf-8")
        raise urllib.error.URLError("offline test: %s" % url)
    return fetch


def find(occ=None, overpass=None, when=SUN, stay=300):
    routes = [("green-p-parking-2019", GREENP), ("datastore_search", occ if occ is not None else occupancy()),
              ("overpass-api.de", overpass if overpass is not None else {"elements": []})]
    warnings = []
    tmp = tempfile.mkdtemp()
    try:
        with mock.patch.object(pk, "PLACES_DIR", tmp), mock.patch.object(dc, "fetch", fake_fetch(routes)), \
                mock.patch.object(pk, "city_lots", return_value=([], [])), mock.patch.object(pk.time, "sleep"):
            res = pk.find_parking(FERRY[0], FERRY[1], when, stay, {}, warnings)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res, warnings


def names(res):
    return [o["name"] for o in res["options"]]


class ClosedRule(unittest.TestCase):
    def test_occupancy_index(self):
        occ, seen, years = pk.occupancy_index(occupancy(3)["result"]["records"])
        self.assertEqual(years, "2025-2026")
        self.assertIn("43", seen)
        self.assertEqual(occ, {"43": (16.0, "2026 Q1")})              # latest quarter only
        self.assertEqual(pk._cp_id("CP043"), "43")

    def test_maybe_closed_exempts_ttc_and_park_lots(self):
        seen = {"43"}
        by = dict((c["id"], c) for c in GREENP["carparks"])
        self.assertFalse(pk.maybe_closed(by[43], seen))
        self.assertTrue(pk.maybe_closed(by[999], seen))
        self.assertFalse(pk.maybe_closed(by[801], seen))              # TTC lots are never in the feed
        self.assertFalse(pk.maybe_closed(by[194], seen))              # nor waterfront park lots
        self.assertFalse(pk.maybe_closed(cp(702, "1 Brimley Road (Bluffer's Park)", 0, 0), seen))

    def test_known_closed_dropped_and_missing_flagged(self):
        res, _ = find()
        self.assertNotIn("Green P 185 Queens Quay East", names(res))
        by = dict((o["name"], o) for o in res["options"])
        gone = by["Green P 9 Gone Street"]                            # name is stripped
        self.assertTrue(gone["maybe_closed"])
        self.assertIn("may have closed", gone["notes"])
        self.assertIn("2025-2026", gone["notes"])
        self.assertFalse(by["Green P 2 Church Street (St. Lawrence Garage)"]["maybe_closed"])
        self.assertFalse(by["Green P Kipling South Lot - 400 Munster Avenue"]["maybe_closed"])
        closed = dc.load_data("parking")["toronto"]["closed"]
        self.assertEqual((closed[0]["id"], closed[0]["closed"]), ("300", "2021-03-31"))
        self.assertTrue(closed[0]["source"].startswith("https://parking.greenp.com/news/"))

    def test_small_or_missing_feed_flags_nothing(self):
        res, _ = find(occ=occupancy(5))
        self.assertFalse(any(o["maybe_closed"] for o in res["options"]))
        res, warnings = find(occ={"result": {"records": []}})
        self.assertFalse(any(o["maybe_closed"] for o in res["options"]))
        self.assertNotIn("Green P 185 Queens Quay East", names(res))   # the closed list needs no feed

    def test_flagged_after_priced_before_unknown_price(self):
        res, _ = find(overpass=OVERPASS)
        opts = res["options"]
        n = names(res)
        gone = n.index("Green P 9 Gone Street")
        self.assertTrue(opts[gone]["maybe_closed"])
        self.assertTrue(all(o["cost"] is not None and not o["maybe_closed"] for o in opts[:gone]))
        self.assertLess(gone, n.index("Harbour Square lot"))           # unknown price after the flagged lot
        self.assertIsNone(opts[n.index("Harbour Square lot")]["cost"])
        # cheapest by price, still after the unflagged priced lots
        self.assertLess(opts[gone]["cost"], min(o["cost"] for o in opts if o["cost"] and not o["maybe_closed"]))

    def test_rank_key_flag_inside_the_tier(self):
        a = pk.make_lot("A", 43.0, -79.0, "lot", {"hourly": 1.0}, "s", "", "stale", (43.0, -79.0), SUN, 60, set())
        b = pk.make_lot("B", 43.0, -79.0, "lot", None, "s", "", "osm", (43.0, -79.0), SUN, 60, set())
        c = pk.make_lot("C", 43.0, -79.0, "lot", {"hourly": 9.0}, "s", "", "stale", (43.0, -79.0), SUN, 60, set())
        d = pk.make_lot("D", 43.0, -79.0, "lot", None, "s", "", "stale", (43.0, -79.0), SUN, 60, set())
        f = pk.make_lot("F", 43.0, -79.0, "lot", {"free": True}, "OpenStreetMap", "", "osm", (43.0, -79.0), SUN, 60, set())
        f["_unverified_free"], f["unverified"] = "facility", True
        a["maybe_closed"] = d["maybe_closed"] = True
        self.assertEqual([x["name"] for x in pk.rank([d, b, f, a, c])], ["C", "A", "F", "B", "D"])


class Targets(unittest.TestCase):
    def test_flagged_target_after_priced_before_the_rest(self):
        a = {"name": "Gone", "cost": 5.0, "maybe_closed": True}
        b = {"name": "Open", "cost": 15.0}
        u = {"name": "Unknown", "cost": None}
        fac = {"name": "Clinic", "cost": 0.0, "unverified": True,
               "cost_text": "free per OpenStreetMap, unverified; may be for facility users"}
        g = {"name": "Gone unpriced", "cost": None, "maybe_closed": True}
        self.assertEqual(dp.targets([a, b]), [b, a])
        self.assertEqual(dp.targets([g, u, fac, a]), [a, u, fac, g])  # a flagged priced lot beats the rest
        self.assertEqual(dp.targets([a]), [a])                         # the only option stays
        self.assertEqual(dp.targets(None), [])
        nofit = {"name": "Short", "cost": 2.0, "fits_stay": False}
        self.assertEqual(dp.targets([nofit, a]), [a, nofit])

    def test_plan_arrival_and_navigation_skip_flagged_lot(self):
        import test_drive_plan as tdp
        m = types.ModuleType("parking")

        def lot(name, cost, flagged, dlat):
            return {"name": name, "lat": FERRY[0] + dlat, "lon": FERRY[1], "walk_m": 200, "walk_min": 3,
                    "search_min": 2, "cost": cost, "cost_text": "$%.2f" % cost, "free": False, "fits_stay": True,
                    "max_stay_min": None, "confidence": "stale", "source": "Green P 2019 open data", "notes": "",
                    "maybe_closed": flagged, "check": {"why": "may have closed" if flagged else "2019 price",
                                                       "url": None, "search": "Green P %s parking rates" % name}}

        def find_parking(lat, lon, arrive, stay_min, opts, warnings):
            options = [lot("Green P Gone", 5.0, True, 0.001), lot("Green P Open", 15.0, False, 0.003)]
            return {"options": options, "place_rules": [], "reservations": [], "sources": [], "radius_m": 800,
                    "check": pk.check_list(options)}
        m.find_parking = find_parking
        m.check_list = pk.check_list
        with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.dict(sys.modules, {"parking": m}):
            res = dp.plan(tdp.args_for(*tdp.BASE, "--dest", "%f,%f" % FERRY, "--depart", "2026-10-11 10:00",
                                       "--stay-hours", "5", "--round-trip", "--park-and-ride", "off"))
            text = dp.render(res)
        self.assertEqual(res["arrival"]["lot"], "Green P Open")
        self.assertEqual(res["links"]["target_name"], "Green P Open")
        self.assertAlmostEqual(res["links"]["target"][0], FERRY[0] + 0.003)
        self.assertEqual(res["parking"]["options"][0]["name"], "Green P Open")
        self.assertEqual([c["n"] for c in res["parking"]["check"]], [1, 2])   # recomputed after the reorder
        self.assertIn("MAY HAVE CLOSED", text)
        self.assertIn("check before you go", text)
        self.assertNotIn("—", text)
        self.assertNotIn("–", text)


class CheckHints(unittest.TestCase):
    def test_greenp_hints(self):
        res, _ = find(overpass=OVERPASS)
        by = dict((o["name"], o) for o in res["options"])
        church = by["Green P 2 Church Street (St. Lawrence Garage)"]["check"]
        self.assertEqual(church["why"], "2019 price")
        self.assertEqual(church["url"], "https://parking.greenp.com/carpark/43_x/")   # the carpark's own page
        self.assertEqual(church["search"], "Green P Carpark 043 2 Church Street St. Lawrence Garage")
        self.assertEqual(by["Green P 2 Church Street (St. Lawrence Garage)"]["greenp_id"], "43")
        gone = by["Green P 9 Gone Street"]["check"]
        self.assertEqual((gone["why"], gone["url"]), ("may have closed", "https://parking.greenp.com/carpark/999_x/"))
        self.assertEqual(by["Harbour Square lot"]["check"]["why"], "price unknown")
        self.assertEqual(by["Harbour Square lot"]["check"]["search"], "Harbour Square lot Toronto parking rates")

    def test_osm_free_and_generic(self):
        res, _ = find(overpass=OVERPASS)
        free = [o for o in res["options"] if o["unverified"]]
        self.assertTrue(free)
        c = free[0]["check"]
        self.assertEqual(c["why"], "free per OpenStreetMap, unverified")
        self.assertIsNone(c["search"])                                # unnamed: the map link instead
        self.assertTrue(c["url"].startswith("https://www.openstreetmap.org/way/"))

    def test_official_and_curated_need_no_check(self):
        for conf in ("official", "curated"):
            lot = pk.make_lot("City lot", 43.0, -79.0, "lot", {"hourly": 2.0}, "City", "https://x", conf,
                              (43.0, -79.0), SUN, 60, set())
            self.assertIsNone(pk.check_hint(lot))
        lot = pk.make_lot("Old lot", 43.0, -79.0, "lot", {"hourly": 2.0, "rate_year": 2025}, "Town", "https://town/p",
                          "curated", (43.0, -79.0), SUN, 60, set())
        self.assertEqual(pk.check_hint(lot), {"why": "2025 price", "url": "https://town/p", "search": "Old lot parking rates"})
        lot = pk.make_lot("Paid lot", 43.0, -79.0, "lot", {"hourly": 2.0}, "OpenStreetMap", "u", "osm",
                          (43.0, -79.0), SUN, 60, set())
        self.assertEqual(pk.check_hint(lot)["why"], "price per OpenStreetMap, unverified")

    def test_check_list_top_three_of_five(self):
        opts = [{"name": "L%d" % i, "check": None if i == 2 else {"why": "2019 price", "url": None, "search": "s%d" % i}}
                for i in range(1, 8)]
        got = pk.check_list(opts)
        self.assertEqual([c["n"] for c in got], [1, 3, 4])
        self.assertEqual(got[0], {"n": 1, "name": "L1", "why": "2019 price", "url": None, "search": "s1"})
        opts = [{"name": "x", "check": None}] * 5 + [{"name": "late", "check": {"why": "w", "url": None, "search": None}}]
        self.assertEqual(pk.check_list(opts), [])                     # only the five lots shown count

    def test_text_line(self):
        items = [{"n": 1, "name": "A", "why": "2019 price", "url": None, "search": "A parking rates"},
                 {"n": 3, "name": "B", "why": "price unknown", "url": "https://b", "search": None},
                 {"n": 4, "name": "C", "why": "may have closed", "url": "https://c", "search": "C rates"}]
        want = ('#1 A: 2019 price, search "A parking rates"; #3 B: price unknown, https://b; '
                '#4 C: may have closed, https://c or search "C rates"')
        self.assertEqual(dp.check_text(items), want)
        self.assertEqual(pk.check_text(items), want)

    def test_json_carries_checks(self):
        res, _ = find(overpass=OVERPASS)
        data = json.loads(dp.to_json({"parking": res}))
        p = data["parking"]
        self.assertTrue(all("check" in o and "maybe_closed" in o for o in p["options"]))
        self.assertTrue(p["check"])
        self.assertLessEqual(len(p["check"]), 5)                      # three, plus #1 and a flagged lot
        self.assertIn(1, [c["n"] for c in p["check"]])
        self.assertEqual(set(p["check"][0]), {"n", "name", "why", "url", "search"})
        first = p["options"][p["check"][0]["n"] - 1]
        self.assertEqual(first["check"]["why"], p["check"][0]["why"])

    def test_parking_lines_render_check(self):
        res, _ = find()   # Green P only: the flagged lot is #4 of 4
        L = dp.parking_lines({"parking": res, "parking_from": SUN, "parking_until": SUN + dt.timedelta(hours=5),
                              "stay_min": 300, "parking_stay_min": 300}, SUN)
        text = "\n".join(L)
        self.assertIn("     check before you go", text)
        self.assertIn('https://parking.greenp.com/carpark/43_x/ or search "Green P Carpark 043 2 Church Street '
                      'St. Lawrence Garage"', text)
        self.assertIn("MAY HAVE CLOSED", text)
        self.assertLess(text.index("MAY HAVE CLOSED"), text.index("check before you go"))
        self.assertNotIn("—", text)
        self.assertNotIn("–", text)


if __name__ == "__main__":
    unittest.main()
