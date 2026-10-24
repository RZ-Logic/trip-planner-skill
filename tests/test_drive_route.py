"""Offline tests for tolls.py and drive_route.py (407 ETR pricing, bridges, engines, traffic model).

All HTTP is mocked through drive_common.fetch. Run: python -m unittest discover -s tests -v
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
import math
import os
import sys
import unittest
import urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import drive_route as drt  # noqa: E402
import tolls  # noqa: E402

MILTON = (43.5183, -79.8829)
MARKHAM = (43.8561, -79.3370)
WED_0730 = dt.datetime(2026, 10, 14, 7, 30)
OPTS = {"provider": "auto", "tomtom_key": None, "weather": 1.0, "holidays": set(), "tz": "America/Toronto",
        "region": None}


def densify(waypoints, step_m=250.0):
    """Straight lines between waypoints with a vertex every step_m."""
    out = [tuple(waypoints[0])]
    for (a, b), (c, d) in zip(waypoints, waypoints[1:]):
        n = max(1, int(math.ceil(dc.th.haversine_m(a, b, c, d) / step_m)))
        for k in range(1, n + 1):
            out.append((a + (c - a) * k / n, b + (d - b) * k / n))
    return out


def ic(name):
    return [i for i in tolls.data()["etr407"]["interchanges"] if i["name"] == name][0]


def etr_points(km_from, km_to):
    ics = sorted(tolls.data()["etr407"]["interchanges"], key=lambda i: i["km"])
    sel = [(i["lat"], i["lon"]) for i in ics if km_from <= i["km"] <= km_to]
    return sel if km_from <= km_to else list(reversed([(i["lat"], i["lon"]) for i in ics if km_to <= i["km"] <= km_from]))


def length_m(pts):
    return dc.cum_dist(pts)[-1]


# --------------------------------------------------------------------------- synthetic engine replies

def _valhalla_trip(pieces):
    """pieces: [(points, names, toll, kmh)] -> Valhalla trip with one leg."""
    shape, mans, total_s, total_km = [], [], 0.0, 0.0
    for pts, names, toll, kmh in pieces:
        begin = max(0, len(shape) - 1)
        shape.extend(pts if not shape else pts[1:])
        km = length_m(pts) / 1000.0
        t = km / kmh * 3600.0
        mans.append({"type": 8, "street_names": names, "time": t, "length": km, "toll": toll or None,
                     "begin_shape_index": begin, "end_shape_index": len(shape) - 1,
                     "highway": True if kmh >= 90 else None})
        total_s += t
        total_km += km
    mans.append({"type": 4, "time": 0.0, "length": 0.0, "begin_shape_index": len(shape) - 1,
                 "end_shape_index": len(shape) - 1})
    summ = {"time": total_s, "length": total_km, "has_toll": any(p[2] for p in pieces)}
    return {"trip": {"legs": [{"maneuvers": mans, "shape": dc.encode_polyline(shape, 6), "summary": summ}],
                     "summary": summ, "status": 0, "units": "kilometers"}}


def milton_markham_valhalla():
    b401 = (ic("Hwy 401")["lat"], ic("Hwy 401")["lon"])
    wood = (ic("Woodbine Ave")["lat"], ic("Woodbine Ave")["lon"])
    ramp_end = (wood[0] + 0.0025, wood[1] + 0.0009)
    return _valhalla_trip([
        (densify([MILTON, b401]), ["401", "Highway 401"], False, 100.0),
        (densify(etr_points(34.8, 84.4)), ["407 ETR", "Highway 407", "407 Express Toll Route"], True, 105.0),
        (densify([wood, ramp_end]), None, True, 40.0),
        (densify([ramp_end, MARKHAM]), ["Woodbine Avenue"], False, 50.0)])


def milton_markham_tollfree():
    return _valhalla_trip([
        (densify([MILTON, (43.66, -79.60), (43.76, -79.41)]), ["401", "Highway 401"], False, 100.0),
        (densify([(43.76, -79.41), (43.84, -79.38), MARKHAM]), ["404", "Highway 404"], False, 95.0)])


def _osrm_step(pts, name, ref, kmh):
    m = length_m(pts)
    return {"name": name, "ref": ref, "distance": m, "duration": m / (kmh / 3.6), "mode": "driving",
            "maneuver": {"type": "turn", "location": [pts[0][1], pts[0][0]]},
            "geometry": {"type": "LineString", "coordinates": [[p[1], p[0]] for p in pts]}}


def _osrm_route(steps):
    return {"distance": sum(s["distance"] for s in steps), "duration": sum(s["duration"] for s in steps),
            "legs": [{"steps": steps}], "weight_name": "routability"}


def milton_markham_osrm():
    b401 = (ic("Hwy 401")["lat"], ic("Hwy 401")["lon"])
    near401 = (b401[0] - 0.004, b401[1] - 0.003)
    wood = (ic("Woodbine Ave")["lat"], ic("Woodbine Ave")["lon"])
    gore = (wood[0] - 0.0005, wood[1] - 0.004)
    r0 = _osrm_route([
        _osrm_step(densify([MILTON, near401]), "Highway 401 Express", "401", 100),
        _osrm_step(densify([near401, b401]), "", None, 60),
        _osrm_step(densify(etr_points(34.8, 83.4) + [gore]), "Highway 407", "407 ETR", 105),
        _osrm_step(densify([gore, wood]), "", None, 50),
        _osrm_step(densify([wood, MARKHAM]), "Woodbine Avenue", "8", 50)])
    r1 = _osrm_route([
        _osrm_step(densify([MILTON, (43.66, -79.60), (43.76, -79.41)]), "Highway 401", "401", 95),
        _osrm_step(densify([(43.76, -79.41), MARKHAM]), "Highway 404", "404", 95)])
    return {"code": "Ok", "routes": [r0, r1], "waypoints": []}


def tomtom_reply(with_toll=True, depart="2026-10-14T07:30:00-04:00"):
    """Shaped like the TomTom calculateRoute docs (routes[].summary, legs[].points, sections)."""
    if with_toll:
        pts = densify([MILTON, (ic("Hwy 401")["lat"], ic("Hwy 401")["lon"])])
        a = len(pts) - 1
        pts += densify(etr_points(34.8, 84.4))[1:]
        z = len(pts) - 1
        pts += densify([pts[-1], MARKHAM])[1:]
    else:
        pts = densify([MILTON, (43.66, -79.60), (43.76, -79.41), MARKHAM])
        a = z = None
    total_m = round(length_m(pts))
    travel = 4380 if with_toll else 4920
    summary = {"lengthInMeters": total_m, "travelTimeInSeconds": travel, "trafficDelayInSeconds": 420,
               "trafficLengthInMeters": 5200, "departureTime": depart,
               "arrivalTime": "2026-10-14T08:43:00-04:00", "noTrafficTravelTimeInSeconds": 3150,
               "historicTrafficTravelTimeInSeconds": 4200, "liveTrafficIncidentsTravelTimeInSeconds": travel}
    sections = [{"startPointIndex": 0, "endPointIndex": len(pts) - 1, "sectionType": "TRAVEL_MODE",
                 "travelMode": "car"},
                {"startPointIndex": 10, "endPointIndex": 30, "sectionType": "TRAFFIC", "simpleCategory": "JAM",
                 "effectiveSpeedInKmh": 22, "delayInSeconds": 420, "magnitudeOfDelay": 2,
                 "tec": {"causes": [{"mainCauseCode": 1}], "effectCode": 4}}]
    if with_toll:
        sections.append({"startPointIndex": a, "endPointIndex": z, "sectionType": "TOLL",
                         "tollPaymentTypes": ["ETC_TRANSPONDER"]})
        sections.append({"startPointIndex": 2, "endPointIndex": z, "sectionType": "MOTORWAY"})
    return {"formatVersion": "0.0.12", "routes": [{
        "summary": summary,
        "legs": [{"summary": summary, "points": [{"latitude": p[0], "longitude": p[1]} for p in pts]}],
        "sections": sections}]}


class FakeFetch(object):
    """Routes mocked URLs to canned replies; records every URL and cache key."""

    def __init__(self, table):
        self.table = table
        self.urls = []
        self.keys = []

    def __call__(self, url, data=None, headers=None, timeout=30, min_interval=0.0, max_age_h=0.0, cache_key=None,
                 stale_max_h=None, validate=None):
        self.urls.append(url)
        self.keys.append(cache_key)
        for match, reply in self.table:
            if all(m in url for m in match):
                if isinstance(reply, Exception):
                    raise reply
                return json.dumps(reply).encode("utf-8")
        raise OSError("unmocked URL %s" % url[:80])


# --------------------------------------------------------------------------- tests

class TollData(unittest.TestCase):
    def test_shape_and_sources(self):
        d = tolls.data()
        e = d["etr407"]
        self.assertEqual(d["verified"], "2026-10-10")
        self.assertEqual(len(e["interchanges"]), 41)
        self.assertEqual(len(e["zones"]), 12)
        for direction in ("W", "E"):
            self.assertEqual([len(r) for r in e["rates"][direction]["weekday"]], [12] * 8)
            self.assertEqual([len(r) for r in e["rates"][direction]["weekend"]], [12] * 4)
        self.assertEqual(len(e["bands"]["weekday"]), 8)
        self.assertEqual(len(e["bands"]["weekend"]), 4)
        for h in ("2026-10-12", "2027-02-15", "2027-03-26", "2027-12-26"):
            self.assertIn(h, e["holidays_priced_as_weekend"])
        self.assertTrue(e["source"].startswith("https://"))
        for b in d["bridges"]:
            self.assertTrue(b["source"].startswith("https://"), b["name"])
            self.assertEqual(len(b["canada_side"]), 2)
            self.assertGreater(b["radius_m"], 0)
        for f in d["free_roads"]:
            self.assertTrue(f["source"].startswith("https://"))
        self.assertEqual(drt.traffic_data()["verified"], "2026-10-10")

    def test_chart_cells(self):
        r = tolls.data()["etr407"]["rates"]
        self.assertEqual(r["W"]["weekday"][5][7], 108.79)   # zone 8 westbound weekday 15:30 (insauga check)
        self.assertEqual(r["E"]["weekday"][1][5], 106.58)   # zone 6 eastbound weekday 07:00
        self.assertEqual(r["E"]["weekend"][1][11], 84.04)   # zone 12 eastbound weekend 10:00

    def test_no_dashes_in_data_text(self):
        for name in ("tolls", "traffic"):
            with open(os.path.join(dc.DATA_DIR, name + ".json"), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("—", text)
            self.assertNotIn("–", text)


class Price407(unittest.TestCase):
    SEG = {"entry_km": 34.8, "exit_km": 84.4, "direction": "E"}

    def test_research_milton_markham(self):
        p = tolls.price_407(self.SEG, dt.datetime(2026, 10, 14, 7, 45), True)
        self.assertEqual(p["toll"], 50.62)
        self.assertEqual(p["band_start"], "07:00")
        self.assertEqual(p["day_type"], "weekday")
        self.assertEqual(p["km"], 49.6)
        self.assertEqual(tolls.price_407(self.SEG, dt.datetime(2026, 10, 14, 7, 45), False)["toll"], 55.92)

    def test_research_markham_oshawa(self):
        seg = {"entry_km": 86.5, "exit_km": 108.0, "direction": "E"}
        p = tolls.price_407(seg, dt.datetime(2026, 10, 17, 11, 5), True)
        self.assertEqual(p["toll"], 15.18)
        self.assertEqual(p["day_type"], "weekend")
        self.assertEqual(tolls.price_407(seg, dt.datetime(2026, 10, 17, 11, 5), False)["toll"], 20.48)

    def test_holiday_priced_as_weekend(self):
        p = tolls.price_407(self.SEG, dt.datetime(2026, 10, 12, 9, 0), True)   # Thanksgiving Monday
        self.assertEqual(p["day_type"], "weekend")
        self.assertEqual(p["band_start"], "08:30")
        p2 = tolls.price_407(self.SEG, dt.datetime(2026, 10, 12, 9, 0), True, holidays=set())
        self.assertEqual(p2["day_type"], "weekday")

    def test_overnight_wrap_and_notes(self):
        p = tolls.price_407(self.SEG, dt.datetime(2026, 10, 14, 3, 0), True)
        self.assertEqual(p["band_start"], "21:00")
        p = tolls.price_407(self.SEG, dt.datetime(2026, 10, 17, 7, 0), True)
        self.assertEqual(p["band_start"], "21:00")
        self.assertEqual(p["day_type"], "weekend")
        p = tolls.price_407(self.SEG, dt.datetime(2027, 3, 3, 12, 0), False)
        self.assertIn("2027 rates not published yet", p["note"])
        self.assertIn("$5 account fee", p["note"])
        self.assertEqual(p["rates_year"], 2026)

    def test_account_fee_note_keeps_cents(self):
        # found while fixing Muse item 11: "$%.0f" would print a $5.50 fee as $6
        data = dict(tolls.data())
        data["etr407"] = dict(data["etr407"], account_fee_monthly=5.5)
        with mock.patch.object(tolls, "data", return_value=data):
            p = tolls.price_407(self.SEG, dt.datetime(2026, 10, 14, 12, 0), False)
        self.assertIn("plus $5.50 account fee", p["note"])

    def test_westbound_zone_split(self):
        seg = {"entry_km": 84.4, "exit_km": 34.8, "direction": "W"}
        p = tolls.price_407(seg, dt.datetime(2026, 10, 14, 16, 0), True)
        self.assertEqual(p["direction"], "W")
        self.assertEqual([z["zone"] for z in p["zones"]], [5, 6, 7, 8, 9, 10])
        self.assertAlmostEqual(sum(z["km"] for z in p["zones"]), 49.6, places=1)
        cents = sum(z["km"] * z["cents_per_km"] for z in p["zones"])
        self.assertAlmostEqual(p["toll"], round(cents / 100 + 1.0, 2), places=2)


class Segments407(unittest.TestCase):
    def test_snap_entry_exit(self):
        segs = tolls.segments_407([{"points": densify(etr_points(34.8, 84.4)), "offset_s": 300.0}])
        self.assertEqual(len(segs), 1)
        s = segs[0]
        self.assertEqual((s["entry"], s["exit"], s["direction"], s["km"]), ("Hwy 401", "Woodbine Ave", "E", 49.6))
        self.assertEqual((s["entry_no"], s["exit_no"]), (34, 84))
        self.assertEqual(s["offset_s"], 300.0)

    def test_east_clip(self):
        pts = densify(etr_points(86.5, 108.0) + [(43.93, -79.0), (43.95, -78.90)])
        segs = tolls.segments_407([{"points": pts, "offset_s": 0.0}])
        self.assertEqual(segs[0]["exit"], "Brock Rd")
        self.assertEqual(segs[0]["km"], 21.5)
        self.assertEqual(tolls.segments_407([{"points": [(43.93, -79.0), (43.95, -78.9)], "offset_s": 0}]), [])

    def test_merge_small_gap(self):
        a = densify(etr_points(34.8, 58.9))
        b = densify(etr_points(58.9, 84.4))
        segs = tolls.segments_407([{"points": a, "offset_s": 10.0, "end_s": 900.0},
                                   {"points": b, "offset_s": 905.0, "end_s": 1800.0}])
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["km"], 49.6)
        self.assertEqual(segs[0]["end_s"], 1800.0)

    def test_same_interchange_dropped(self):
        b = ic("Hwy 401")
        self.assertEqual(tolls.segments_407([{"points": [(b["lat"], b["lon"]), (b["lat"] + 0.001, b["lon"])],
                                              "offset_s": 0}]), [])


class Bridges(unittest.TestCase):
    def peace(self):
        return [b for b in tolls.data()["bridges"] if b["name"] == "Peace Bridge"][0]

    def test_into_us_and_back(self):
        b = self.peace()
        line = densify([(42.93, -78.95), tuple(b["canada_side"]), (b["lat"], b["lon"]), tuple(b["us_side"]),
                        (42.89, -78.87)], 100)
        out = tolls.bridges_on(line)
        self.assertEqual([(x["name"], x["direction"], x["cad"]) for x in out], [("Peace Bridge", "into_us", 0.0)])
        back = tolls.bridges_on(list(reversed(line)))
        self.assertEqual(back[0]["direction"], "into_canada")
        self.assertEqual(back[0]["cad"], 11.0)
        self.assertIn("C$11.00", back[0]["text"])

    def test_usd_only_direction(self):
        t = [b for b in tolls.data()["bridges"] if b["name"] == "Detroit-Windsor Tunnel"][0]
        line = densify([(42.300, -83.030), tuple(t["canada_side"]), (t["lat"], t["lon"]), tuple(t["us_side"]),
                        (42.335, -83.045)], 100)
        self.assertEqual(tolls.bridges_on(line)[0]["cad"], 8.25)
        back = tolls.bridges_on(list(reversed(line)))[0]
        self.assertEqual(back["direction"], "into_canada")
        # US$9.00 only: converted at the stored Bank of Canada rate so it counts in the trade-off
        fx = tolls.data()["usd_cad"]
        self.assertEqual(back["cad"], round(9.0 * fx["rate"], 2))
        self.assertTrue(back["cad_estimated"])
        self.assertIn("US$9.00", back["text"])
        self.assertIn("about C$%.2f at the Bank of Canada rate of %s" % (back["cad"], fx["date"]), back["text"])
        r = {"etr": [], "bridges": [back], "notes": []}
        drt._bridge_totals(r)
        self.assertEqual(r["toll_other_cad"], back["cad"])
        self.assertTrue(r["has_tolls"])
        self.assertTrue(any(n.startswith("border toll: Detroit-Windsor Tunnel") for n in r["notes"]))
        self.assertIn(drt.BORDER_NOTE, r["notes"])

    def test_parallel_street_near_tunnel_is_not_crossing(self):
        # F3: Ouellette Ave runs parallel to the tunnel axis to the riverfront; it never enters the tunnel.
        line = densify([(42.3007, -83.0213), (42.3105, -83.0335), (42.3209, -83.0412)], 50)
        self.assertEqual(tolls.bridges_on(line), [])
        # reaching the Canadian portal but not the US one is not a crossing either
        t = [b for b in tolls.data()["bridges"] if b["name"] == "Detroit-Windsor Tunnel"][0]
        stub = densify([(42.300, -83.030), tuple(t["canada_side"]), (t["lat"], t["lon"])], 50)
        self.assertEqual(tolls.bridges_on(stub), [])
        self.assertLessEqual(t["radius_m"], 200)

    def test_flip_bridges_for_return(self):
        b = self.peace()
        line = densify([(42.93, -78.95), tuple(b["canada_side"]), tuple(b["us_side"]), (42.89, -78.87)], 100)
        out = tolls.bridges_on(line)
        back = tolls.flip_bridges(out, length_m(line))
        self.assertEqual([(x["direction"], x["cad"]) for x in back], [("into_canada", 11.0)])
        self.assertAlmostEqual(back[0]["along_m"], length_m(line) - out[0]["along_m"], delta=2)

    def test_passing_by_is_not_crossing(self):
        # Niagara Parkway runs along the Canadian bank under the Rainbow Bridge plaza.
        line = densify([(43.0800, -79.0760), (43.0905, -79.0700), (43.1000, -79.0650)], 50)
        self.assertEqual(tolls.bridges_on(line), [])
        self.assertEqual(tolls.bridges_on(densify([MILTON, MARKHAM])), [])


class TrafficModel(unittest.TestCase):
    URB = {"urban": True, "s": 60.0}
    RUR = {"urban": False, "s": 60.0}

    def f(self, chunk, when, getaway=None, hol=()):
        return drt.factors(chunk, when, set(hol) | tolls.holidays_default(), getaway)[:2]

    def test_congested_bands(self):
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 14, 8, 0)), (1.45, 2.1))
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 14, 17, 0)), (1.45, 2.1))
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 14, 6, 30)), (1.3, 1.7))
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 14, 19, 0)), (1.3, 1.7))
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 14, 12, 0)), (1.2, 1.45))
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 17, 8, 0)), (1.2, 1.45))   # Saturday
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 12, 8, 0)), (1.2, 1.45))   # Thanksgiving
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 14, 22, 0)), (1.0, 1.15))
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 14, 5, 0)), (1.0, 1.15))

    def test_elsewhere_and_getaway(self):
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 14, 8, 0)), (1.03, 1.15))
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 14, 23, 0)), (1.0, 1.1))
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 16, 15, 0), "leaving"), (1.15, 1.4))   # Friday
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 16, 12, 0), "leaving"), (1.03, 1.15))
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 16, 15, 0), "returning"), (1.03, 1.15))
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 18, 13, 0), "returning"), (1.15, 1.4))  # Sunday
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 12, 9, 0), "leaving"), (1.03, 1.15))   # holiday AM
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 10, 12, 14, 0), "returning"), (1.15, 1.4))
        self.assertEqual(self.f(self.RUR, dt.datetime(2026, 4, 2, 16, 0), "leaving"), (1.15, 1.4))  # Good Friday eve
        self.assertEqual(self.f(self.URB, dt.datetime(2026, 10, 16, 15, 0), "leaving"), (1.3, 1.7))

    def test_areas(self):
        self.assertEqual(drt.area_of(*MILTON), "GTHA")
        self.assertEqual(drt.area_of(*MARKHAM), "GTHA")
        self.assertEqual(drt.area_of(43.8971, -78.8658), "GTHA")       # Oshawa
        self.assertEqual(drt.area_of(43.3125, -81.7569), "")           # Grand Bend
        self.assertEqual(drt.area_of(43.62, -80.02), "")               # Halton Hills farmland
        self.assertEqual(drt.area_of(43.95, -79.05), "")               # Durham north of Pickering
        self.assertEqual(drt.area_of(45.42, -75.70), "Ottawa")
        self.assertEqual(drt.area_of(42.98, -81.25), "London")
        self.assertEqual(drt.area_of(43.45, -80.49), "Kitchener-Waterloo-Cambridge")

    def test_road_names(self):
        self.assertEqual(drt.road_name(["401", "Macdonald-Cartier Freeway"]), "Hwy 401")
        self.assertEqual(drt.road_name(["407 ETR", "Highway 407"]), "407 ETR")
        self.assertEqual(drt.road_name(["Highway 407", "407"]), "Hwy 407")
        self.assertEqual(drt.road_name(["Warden Avenue", "65"]), "Warden Avenue")
        self.assertEqual(drt.road_name(["Queen Elizabeth Way", "QEW", "403"]), "QEW")
        self.assertEqual(drt.road_name(["", "I 190"]), "I-190")
        self.assertEqual(drt.road_name([]), "")


class Engines(unittest.TestCase):
    def run_routes(self, table, opts=None, depart=WED_0730, arrive_at=None):
        fake = FakeFetch(table)
        warnings = []
        o = dict(OPTS)
        o.update(opts or {})
        with mock.patch.object(dc, "fetch", side_effect=fake):
            routes = drt.get_routes(MILTON, MARKHAM, depart, o, warnings, arrive_at=arrive_at)
        return routes, warnings, fake

    def check_route_keys(self, r):
        for k in ("label", "engine", "traffic", "km", "base_s", "chunks", "typical_s", "plan_s", "depart", "arrive",
                  "arrive_plan", "poly", "hwy_share", "urban_share", "roads", "etr", "toll_407_transponder",
                  "toll_407_camera", "bridges", "toll_other_cad", "has_tolls", "delays", "notes", "factor_note"):
            self.assertIn(k, r)
        for c in r["chunks"]:
            for k in ("s", "m", "lat", "lon", "urban", "etr"):
                self.assertIn(k, c)
        self.assertLessEqual(len(r["poly"]), 2000)
        self.assertAlmostEqual((r["arrive"] - r["depart"]).total_seconds(), r["typical_s"], delta=1)
        self.assertGreaterEqual(r["plan_s"], r["typical_s"])
        for text in [r["factor_note"]] + r["notes"] + [s["note"] for s in r["etr"]]:
            self.assertNotIn("—", text)
            self.assertNotIn("–", text)

    def test_valhalla_fastest_and_tollfree(self):
        routes, warnings, fake = self.run_routes([
            (("valhalla", "exclude_tolls"), milton_markham_tollfree()),
            (("valhalla",), milton_markham_valhalla())])
        self.assertEqual([r["label"] for r in routes], ["fastest", "toll-free"])
        f, t = routes
        for r in routes:
            self.check_route_keys(r)
        self.assertEqual(f["engine"], "valhalla")
        self.assertEqual(f["traffic"], "free-flow")
        self.assertEqual(len(f["etr"]), 1)
        s = f["etr"][0]
        self.assertEqual((s["entry"], s["exit"], s["direction"], s["km"]), ("Hwy 401", "Woodbine Ave", "E", 49.6))
        self.assertEqual(s["band_start"], "07:00")
        self.assertEqual(s["toll_transponder"], 50.62)
        self.assertEqual(s["toll_camera"], 55.92)
        self.assertEqual(f["toll_407_transponder"], 50.62)
        self.assertTrue(f["has_tolls"])
        self.assertFalse(t["has_tolls"])
        self.assertEqual(t["etr"], [])
        self.assertIn("407 ETR", f["roads"])
        self.assertIn("weekday peak", f["factor_note"])
        self.assertGreater(f["urban_share"], 0.9)
        self.assertGreater(f["hwy_share"], 0.8)
        # entry time = depart + typical (traffic-scaled) seconds before the 407
        self.assertGreater(s["entry_time"], WED_0730 + dt.timedelta(minutes=5))
        self.assertEqual(warnings, [])
        q = json.loads(urllib.parse.unquote(fake.urls[1].split("json=", 1)[1]))
        self.assertTrue(q["costing_options"]["auto"]["exclude_tolls"])

    def test_osrm_fallback_when_valhalla_fails(self):
        routes, warnings, fake = self.run_routes([
            (("valhalla",), OSError("mock outage")),
            (("router.project-osrm.org",), milton_markham_osrm())])
        self.assertEqual([r["engine"] for r in routes], ["osrm", "osrm"])
        self.assertEqual([r["label"] for r in routes], ["fastest", "toll-free"])
        s = routes[0]["etr"][0]
        self.assertEqual((s["entry"], s["exit"], s["km"]), ("Hwy 401", "Woodbine Ave", 49.6))
        self.assertEqual(s["toll_transponder"], 50.62)
        self.assertTrue(any("Valhalla routing failed" in w and "using OSRM" in w for w in warnings))
        self.assertIn("steps=true", fake.urls[-1])
        self.assertIn("alternatives=3", fake.urls[-1])

    def test_osrm_provider_and_mirror(self):
        routes, warnings, fake = self.run_routes([
            (("router.project-osrm.org",), OSError("down")),
            (("routing.openstreetmap.de/routed-car",), milton_markham_osrm())], {"provider": "osrm"})
        self.assertEqual(routes[0]["engine"], "osrm")
        self.assertEqual(warnings, [])
        self.assertFalse(any("valhalla" in u for u in fake.urls))

    def test_osrm_without_tollfree_alternative(self):
        reply = milton_markham_osrm()
        reply["routes"] = reply["routes"][:1]
        routes, warnings, _ = self.run_routes([(("project-osrm",), reply)], {"provider": "osrm"})
        self.assertEqual([r["label"] for r in routes], ["fastest"])
        self.assertTrue(any("no alternative without the 407" in w for w in warnings))

    def test_all_engines_fail(self):
        routes, warnings, _ = self.run_routes([(("http",), OSError("offline"))])
        self.assertEqual(routes, [])
        self.assertTrue(any("every routing engine failed" in w for w in warnings))

    def test_tomtom_without_key_falls_back(self):
        routes, warnings, _ = self.run_routes([
            (("valhalla", "exclude_tolls"), milton_markham_tollfree()),
            (("valhalla",), milton_markham_valhalla())], {"provider": "tomtom"})
        self.assertEqual(routes[0]["engine"], "valhalla")
        self.assertTrue(any("TOMTOM_API_KEY" in w for w in warnings))

    def tomtom_run(self, arrive_at=None):
        now = (dt.datetime(2026, 10, 10, 12, 0), "zoneinfo")
        with mock.patch.object(drt.th, "local_now", return_value=now):
            return self.run_routes([
                (("api.tomtom.com", "avoid=tollRoads"), tomtom_reply(False)),
                (("api.tomtom.com",), tomtom_reply(True))],
                {"tomtom_key": "SECRETKEY123"}, arrive_at=arrive_at)

    def test_tomtom_live(self):
        routes, warnings, fake = self.tomtom_run()
        self.assertEqual([r["label"] for r in routes], ["fastest", "toll-free"])
        f = routes[0]
        self.check_route_keys(f)
        self.assertEqual(f["engine"], "tomtom")
        self.assertEqual(f["traffic"], "live")
        self.assertEqual(f["base_s"], 4380)
        self.assertEqual(f["typical_s"], 4380)
        self.assertGreater(f["plan_s"], 4380 * 1.1)
        self.assertEqual(len(f["etr"]), 1)
        self.assertEqual(f["etr"][0]["exit"], "Woodbine Ave")
        self.assertEqual(f["etr"][0]["toll_transponder"], 50.62)
        self.assertEqual(f["delays"][0]["delay_s"], 420)
        self.assertIn("traffic jam", f["delays"][0]["text"])
        self.assertIn("TomTom live traffic", f["factor_note"])
        self.assertGreater(f["hwy_share"], 0.8)
        self.assertTrue(any("no traffic" in n for n in f["notes"]))
        for u, k in zip(fake.urls, fake.keys):
            self.assertIn("key=SECRETKEY123", u)
            self.assertNotIn("SECRETKEY123", k)
            self.assertIn("departAt=2026-10-14T07%3A30%3A00-04%3A00", u)
            self.assertIn("computeTravelTimeFor=all", u)
            self.assertIn("sectionType=toll", u)
            self.assertIn("sectionType=traffic", u)
        self.assertIn("avoid=tollRoads", fake.urls[1])
        self.assertTrue(all("SECRETKEY123" not in w for w in warnings))

    def test_tomtom_now_and_arrive_at(self):
        now = (WED_0730 + dt.timedelta(minutes=5), "zoneinfo")
        fake = FakeFetch([(("api.tomtom.com",), tomtom_reply(True, "2026-10-14T07:21:00-04:00"))])
        o = dict(OPTS, tomtom_key="K")
        with mock.patch.object(drt.th, "local_now", return_value=now), mock.patch.object(dc, "fetch", side_effect=fake):
            drt.get_routes(MILTON, MARKHAM, WED_0730, o, [])
            self.assertIn("departAt=now", fake.urls[0])
            fake.urls[:] = []
            routes = drt.get_routes(MILTON, MARKHAM, WED_0730, o, [], arrive_at=dt.datetime(2026, 10, 14, 9, 0))
        self.assertIn("arriveAt=2026-10-14T09%3A00%3A00-04%3A00", fake.urls[0])
        self.assertNotIn("departAt", fake.urls[0])
        self.assertEqual(routes[0]["depart"], dt.datetime(2026, 10, 14, 7, 21))

    def test_tomtom_error_falls_back(self):
        routes, warnings, _ = self.run_routes([
            (("api.tomtom.com",), {"error": {"description": "Invalid key"}}),
            (("valhalla", "exclude_tolls"), milton_markham_tollfree()),
            (("valhalla",), milton_markham_valhalla())], {"tomtom_key": "K"})
        self.assertEqual(routes[0]["engine"], "valhalla")
        self.assertTrue(any("TomTom routing failed (Invalid key)" in w for w in warnings))


class RetimeAndReturn(unittest.TestCase):
    def routes(self):
        fake = FakeFetch([(("valhalla", "exclude_tolls"), milton_markham_tollfree()),
                          (("valhalla",), milton_markham_valhalla())])
        with mock.patch.object(dc, "fetch", side_effect=fake):
            return drt.get_routes(MILTON, MARKHAM, WED_0730, dict(OPTS), [])

    def test_retime_offpeak_and_weather(self):
        f = self.routes()[0]
        peak = f["typical_s"]
        drt.retime(f, dt.datetime(2026, 10, 14, 22, 0), OPTS)
        night = sum(c["s"] * drt.calibration(c, "valhalla") for c in f["chunks"])   # night factors are 1.0
        self.assertAlmostEqual(f["typical_s"], night, delta=1)
        self.assertLess(night, f["base_s"])   # Valhalla's 407 free-flow is slower than real free flow
        self.assertEqual(f["etr"][0]["band_start"], "21:00")
        self.assertLess(f["toll_407_transponder"], 50.62)
        self.assertLess(f["typical_s"], peak)
        drt.retime(f, dt.datetime(2026, 10, 14, 22, 0), dict(OPTS, weather=1.15))
        self.assertAlmostEqual(f["typical_s"], night * 1.15, delta=1)
        self.assertIn("x1.15 rain", f["factor_note"])
        drt.retime(f, WED_0730, OPTS)
        self.assertEqual(f["toll_407_transponder"], 50.62)
        self.assertEqual(f["arrive"], WED_0730 + dt.timedelta(seconds=f["typical_s"]))

    def test_reverse_for_return(self):
        f = self.routes()[0]
        back = drt.reverse_for_return(f, dt.datetime(2026, 10, 14, 17, 0), OPTS)
        self.assertIsNot(back, f)
        s = back["etr"][0]
        self.assertEqual((s["entry"], s["exit"], s["direction"]), ("Woodbine Ave", "Hwy 401", "W"))
        self.assertEqual(s["band_start"], "15:30")
        self.assertEqual(back["poly"][0], f["poly"][-1])
        self.assertEqual(back["chunks"][0]["lat"], round(f["poly"][-1][0], 6))
        self.assertEqual(len(back["chunks"]), len(f["chunks"]))
        self.assertAlmostEqual(back["base_s"], f["base_s"])
        # the return enters the 407 near the start (Woodbine is close to Markham)
        self.assertLess(s["offset_base_s"], f["etr"][0]["offset_base_s"])
        self.assertEqual(f["etr"][0]["direction"], "E")   # original untouched
        self.assertIn("return on the same roads", back["notes"])

    def test_reverse_tomtom_note(self):
        now = (dt.datetime(2026, 10, 10, 12, 0), "zoneinfo")
        fake = FakeFetch([(("avoid=tollRoads",), tomtom_reply(False)), (("api.tomtom.com",), tomtom_reply(True))])
        with mock.patch.object(drt.th, "local_now", return_value=now), mock.patch.object(dc, "fetch", side_effect=fake):
            f = drt.get_routes(MILTON, MARKHAM, WED_0730, dict(OPTS, tomtom_key="K"), [])[0]
        back = drt.reverse_for_return(f, dt.datetime(2026, 10, 14, 17, 0), OPTS)
        self.assertEqual(back["base_s"], f["base_s"])
        self.assertIn("not re-queried for the return", back["factor_note"])
        self.assertEqual(back["delays"], [])
        drt.retime(f, dt.datetime(2026, 10, 14, 12, 0), OPTS)
        self.assertIn("not re-queried for 12:00", f["factor_note"])

    def test_reverse_flips_bridge(self):
        b = [x for x in tolls.data()["bridges"] if x["name"] == "Rainbow Bridge"][0]
        line = densify([(43.08, -79.09), tuple(b["canada_side"]), (b["lat"], b["lon"]), tuple(b["us_side"]),
                        (43.085, -79.05)], 100)
        r = {"engine": "osrm", "chunks": [{"s": 300.0, "m": 5000.0, "lat": line[0][0], "lon": line[0][1],
                                           "urban": False, "etr": False}],
             "poly": line, "etr": [], "bridges": tolls.bridges_on(line), "roads": [], "notes": [], "delays": []}
        drt._bridge_totals(r)
        self.assertEqual(r["toll_other_cad"], 0.0)
        back = drt.reverse_for_return(r, dt.datetime(2026, 10, 14, 17, 0), OPTS)
        self.assertEqual(back["bridges"][0]["direction"], "into_canada")
        self.assertEqual(back["toll_other_cad"], 8.5)
        self.assertTrue(back["has_tolls"])


# --------------------------------------------------------------------------- review fixes (route fixer)

HOME = (43.498757, -79.855987)   # Milton Community Park (805 Santa Maria Blvd): a public stand-in origin
KINGSTON = (44.229868, -76.480430)
DETROIT = (42.3314, -83.0458)
EAST = tolls.data()["etr407"]["east_end_lon"]

# Compact Valhalla routes from south Milton (cached 2026-10-10; first point moved to Milton Community Park): runs of chunks as
# [base s, lat, lon, urban, etr, hwy, area]; built by scratchpad gen_fixtures.py from real replies.
CALIB_ROUTES = {
    'markham_407': {"km": 70.0, "end": [43.8564, -79.3377], "runs": [
        [588.3,43.4988,-79.8560,1,0,0,"GTHA"], [125.6,43.538,-79.7783,1,0,0,"GTHA"], [535.1,43.5521,-79.7629,1,1,1,"GTHA"],
        [535.1,43.6362,-79.7427,1,1,1,"GTHA"], [535.1,43.7389,-79.6561,1,1,1,"GTHA"], [535.1,43.791,-79.5024,1,1,1,"GTHA"],
        [76.4,43.8399,-79.3615,1,1,1,"GTHA"], [65.0,43.8436,-79.3374,1,1,0,"GTHA"], [302.2,43.8435,-79.3303,1,0,0,"GTHA"],
    ]},
    'markham_free': {"km": 72.7, "end": [43.8564, -79.3377], "runs": [
        [564.3,43.4988,-79.8560,1,0,0,"GTHA"], [341.8,43.5355,-79.781,1,0,0,"GTHA"], [560.6,43.5697,-79.8276,1,0,1,"GTHA"],
        [533.5,43.6293,-79.6853,1,0,1,"GTHA"], [590.1,43.7013,-79.5608,1,0,1,"GTHA"], [164.1,43.7571,-79.4024,1,0,1,"GTHA"],
        [51.6,43.7666,-79.3537,1,0,0,"GTHA"], [371.9,43.7664,-79.3417,1,0,1,"GTHA"], [450.5,43.8403,-79.3685,1,0,0,"GTHA"],
    ]},
    'wonderland_407': {"km": 55.3, "end": [43.8406, -79.5444], "runs": [
        [588.3,43.4988,-79.8560,1,0,0,"GTHA"], [125.6,43.538,-79.7783,1,0,0,"GTHA"], [534.1,43.5521,-79.7629,1,1,1,"GTHA"],
        [534.1,43.6357,-79.7432,1,1,1,"GTHA"], [410.9,43.7381,-79.6574,1,1,1,"GTHA"], [235.7,43.781,-79.5441,1,0,1,"GTHA"],
        [478.9,43.8228,-79.5457,1,0,0,"GTHA"], [241.3,43.8365,-79.5447,1,0,0,"GTHA"],
    ]},
    'wonderland_free': {"km": 58.4, "end": [43.8406, -79.5444], "runs": [
        [564.3,43.4988,-79.8560,1,0,0,"GTHA"], [341.8,43.5355,-79.781,1,0,0,"GTHA"], [560.6,43.5697,-79.8276,1,0,1,"GTHA"],
        [596.3,43.6293,-79.6853,1,0,1,"GTHA"], [529.1,43.7105,-79.5469,1,0,1,"GTHA"], [73.4,43.807,-79.5429,1,0,1,"GTHA"],
        [478.9,43.8228,-79.5457,1,0,0,"GTHA"], [241.3,43.8365,-79.5447,1,0,0,"GTHA"],
    ]},
    'tobermory_free': {"km": 290.1, "end": [45.2373, -81.5218], "runs": [
        [564.3,43.4988,-79.8560,1,0,0,"GTHA"], [341.8,43.5355,-79.781,1,0,0,"GTHA"], [534.6,43.5697,-79.8276,1,0,1,"GTHA"],
        [69.6,43.6269,-79.6911,1,0,1,"GTHA"], [23.8,43.6344,-79.6719,1,0,0,"GTHA"], [588.0,43.6362,-79.6667,1,0,1,"GTHA"],
        [91.1,43.7352,-79.7742,1,0,1,"GTHA"], [545.3,43.7526,-79.7918,0,0,1,""], [545.3,43.8036,-79.9193,0,0,1,""],
        [529.6,43.8932,-80.0407,0,0,1,""], [524.5,43.9924,-80.1157,0,0,1,""], [577.7,44.0865,-80.1609,0,0,1,""],
        [596.3,44.1269,-80.2971,0,0,1,""], [596.3,44.2045,-80.4304,0,0,1,""], [596.3,44.2742,-80.5693,0,0,1,""],
        [596.3,44.352,-80.7031,0,0,1,""], [551.2,44.422,-80.8425,0,0,1,""], [98.6,44.5065,-80.9141,0,0,1,""],
        [528.9,44.5236,-80.9178,0,0,0,""], [520.3,44.5511,-80.9891,0,0,0,""], [312.2,44.6081,-81.0706,0,0,0,""],
        [503.9,44.6338,-81.1328,0,0,1,""], [503.9,44.7226,-81.1395,0,0,1,""], [503.9,44.7815,-81.1911,0,0,1,""],
        [503.9,44.8652,-81.2342,0,0,1,""], [503.9,44.9491,-81.2763,0,0,1,""], [503.9,45.0317,-81.3214,0,0,1,""],
        [503.9,45.0803,-81.4249,0,0,1,""], [403.1,45.152,-81.4781,0,0,1,""], [562.9,45.1945,-81.5593,0,0,0,""],
        [562.9,45.206,-81.5498,0,0,0,""], [562.9,45.2147,-81.536,0,0,0,""], [95.7,45.2249,-81.525,0,0,0,""],
        [581.6,45.226,-81.5251,0,0,0,""],
    ]},
    'bluemtn_407': {"km": 152.5, "end": [44.5015, -80.3125], "runs": [
        [588.3,43.4988,-79.8560,1,0,0,"GTHA"], [125.6,43.538,-79.7783,1,0,0,"GTHA"], [551.2,43.5521,-79.7629,1,1,1,"GTHA"],
        [176.8,43.6366,-79.7423,1,1,1,"GTHA"], [495.0,43.6642,-79.7033,1,0,1,"GTHA"], [545.3,43.7526,-79.7918,0,0,1,""],
        [545.3,43.8036,-79.9193,0,0,1,""], [529.6,43.8932,-80.0407,0,0,1,""], [561.5,43.9924,-80.1157,0,0,1,""],
        [56.4,44.0849,-80.1718,0,0,1,""], [586.7,44.0817,-80.1881,0,0,0,""], [586.7,44.1633,-80.2095,0,0,0,""],
        [586.7,44.248,-80.2278,0,0,0,""], [519.2,44.3329,-80.2471,0,0,0,""], [579.2,44.391,-80.2259,0,0,0,""],
        [580.6,44.4562,-80.241,0,0,0,""], [306.5,44.498,-80.2854,0,0,0,""],
    ]},
    'kingston_401': {"km": 306.4, "end": [44.2298, -76.4802], "runs": [
        [564.3,43.4988,-79.8560,1,0,0,"GTHA"], [341.8,43.5355,-79.781,1,0,0,"GTHA"], [560.6,43.5697,-79.8276,1,0,1,"GTHA"],
        [533.5,43.6293,-79.6853,1,0,1,"GTHA"], [590.1,43.7013,-79.5608,1,0,1,"GTHA"], [556.6,43.7571,-79.4024,1,0,1,"GTHA"],
        [551.8,43.7933,-79.2164,1,0,1,"GTHA"], [551.8,43.8481,-79.0448,1,0,1,"GTHA"], [69.0,43.8817,-78.8548,1,0,1,"GTHA"],
        [551.8,43.8869,-78.8311,0,0,1,""], [551.8,43.9015,-78.6441,0,0,1,""], [551.8,43.939,-78.4534,0,0,1,""],
        [551.8,43.9752,-78.2624,0,0,1,""], [551.8,44.0004,-78.0769,0,0,1,""], [551.8,44.0274,-77.8845,0,0,1,""],
        [551.8,44.0891,-77.7111,0,0,1,""], [551.8,44.1537,-77.5387,0,0,1,""], [551.8,44.2065,-77.3547,0,0,1,""],
        [551.8,44.2308,-77.162,0,0,1,""], [551.8,44.2632,-76.9691,0,0,1,""], [551.8,44.2796,-76.7733,0,0,1,""],
        [206.9,44.2884,-76.5756,0,0,1,""], [556.0,44.2696,-76.5067,0,0,0,""],
    ]},
    'kingston_407': {"km": 322.7, "end": [44.2298, -76.4802], "runs": [
        [588.3,43.4988,-79.8560,1,0,0,"GTHA"], [125.6,43.538,-79.7783,1,0,0,"GTHA"], [583.9,43.5521,-79.7629,1,1,1,"GTHA"],
        [583.9,43.6482,-79.724,1,1,1,"GTHA"], [583.9,43.7584,-79.6152,1,1,1,"GTHA"], [583.9,43.8273,-79.4545,1,1,1,"GTHA"],
        [364.9,43.8603,-79.2646,1,1,1,"GTHA"], [146.0,43.9014,-79.1592,0,1,1,""], [73.0,43.9124,-79.1121,1,1,1,"GTHA"],
        [583.9,43.9229,-79.0931,0,0,1,""], [577.3,43.9661,-78.915,0,0,1,""], [209.2,43.9382,-78.7619,0,0,1,""],
        [86.0,43.8877,-78.7495,0,0,0,""], [535.8,43.8821,-78.7344,0,0,1,""], [535.8,43.9188,-78.5454,0,0,1,""],
        [535.8,43.9605,-78.3563,0,0,1,""], [535.8,43.9888,-78.1669,0,0,1,""], [535.8,44.0127,-77.9793,0,0,1,""],
        [535.8,44.0605,-77.7964,0,0,1,""], [535.8,44.1182,-77.6216,0,0,1,""], [535.8,44.1801,-77.4454,0,0,1,""],
        [535.8,44.2174,-77.2587,0,0,1,""], [535.8,44.2443,-77.0635,0,0,1,""], [535.8,44.2752,-76.8722,0,0,1,""],
        [468.8,44.2846,-76.6737,0,0,1,""], [556.0,44.2696,-76.5067,0,0,0,""],
    ]},
    'detroit_401': {"km": 348.4, "end": [42.3314, -83.0458], "runs": [
        [510.1,43.4988,-79.8560,1,0,0,"GTHA"], [487.2,43.4462,-79.7913,1,1,1,"GTHA"], [205.8,43.3439,-79.8313,1,0,1,"GTHA"],
        [68.6,43.3024,-79.874,0,0,1,""], [411.5,43.2925,-79.8939,1,0,1,"GTHA"], [548.7,43.217,-79.9641,0,0,1,""],
        [548.7,43.1787,-80.1422,0,0,1,""], [548.7,43.1687,-80.3327,0,0,1,""], [548.7,43.1336,-80.5206,0,0,1,""],
        [593.4,43.118,-80.7108,0,0,1,""], [593.4,43.0167,-80.8708,0,0,1,""], [263.7,42.9586,-81.0609,0,0,1,""],
        [197.8,42.9406,-81.1514,1,0,1,"London"], [533.3,42.9208,-81.2146,0,0,1,""], [534.1,42.8075,-81.329,0,0,1,""],
        [534.1,42.7089,-81.4691,0,0,1,""], [534.1,42.6216,-81.6182,0,0,1,""], [534.1,42.5504,-81.7759,0,0,1,""],
        [534.1,42.4697,-81.9306,0,0,1,""], [534.1,42.3851,-82.0819,0,0,1,""], [534.1,42.3224,-82.2528,0,0,1,""],
        [534.1,42.2712,-82.4267,0,0,1,""], [534.1,42.2322,-82.6062,0,0,1,""], [543.9,42.2386,-82.7983,0,0,1,""],
        [539.4,42.2355,-82.9837,0,0,1,""], [699.3,42.2973,-83.1046,0,0,1,""], [204.6,42.3061,-83.0994,0,0,1,""],
        [196.2,42.3354,-83.0639,0,0,0,""],
    ]},
}

# Google Maps typical ranges in minutes for the same trips (checked 2026-10-10), with the departure.
GOOGLE = {
    "markham_407": (dt.datetime(2026, 10, 14, 7, 30), 40, 60),
    "markham_free": (dt.datetime(2026, 10, 14, 7, 30), 60, 120),
    "wonderland_407": (dt.datetime(2026, 10, 17, 11, 0), 35, 50),
    "wonderland_free": (dt.datetime(2026, 10, 17, 11, 0), 45, 75),
    "tobermory_free": (dt.datetime(2026, 10, 17, 7, 0), 210, 250),
    "bluemtn_407": (dt.datetime(2026, 10, 17, 8, 0), 110, 140),
    "kingston_401": (dt.datetime(2026, 10, 16, 15, 0), 200, 300),
    "kingston_407": (dt.datetime(2026, 10, 16, 15, 0), 170, 230),
    "detroit_401": (dt.datetime(2026, 10, 20, 9, 0), 190, 250),
}


def calib_route(name, depart=None):
    f = CALIB_ROUTES[name]
    chunks = [{"s": r[0], "m": 0.0, "lat": r[1], "lon": r[2], "urban": bool(r[3]), "etr": bool(r[4]),
               "hwy": bool(r[5]), "area": r[6]} for r in f["runs"]]
    route = {"engine": "valhalla", "chunks": chunks, "poly": [(chunks[0]["lat"], chunks[0]["lon"]), tuple(f["end"])],
             "etr": [], "km": f["km"]}
    return drt.retime(route, depart or GOOGLE[name][0], OPTS)


class Calibration(unittest.TestCase):
    """UX1, UX2, F1: keyless times against Google's typical range (plus or minus 15%)."""

    def test_typical_inside_google_range(self):
        for name, (when, lo, hi) in GOOGLE.items():
            mins = calib_route(name)["typical_s"] / 60.0
            self.assertGreaterEqual(mins, lo * 0.85, "%s %.0f min" % (name, mins))
            self.assertLessEqual(mins, hi * 1.15, "%s %.0f min" % (name, mins))

    def test_407_saves_time_at_rush_hour(self):
        a, b = calib_route("markham_407"), calib_route("markham_free")
        self.assertGreaterEqual((b["typical_s"] - a["typical_s"]) / 60.0, 20)
        self.assertIn("on the 407 ETR (weekday peak)", a["factor_note"])
        self.assertTrue(a["factor_note"].startswith("calibrated free-flow time "))
        k, f = calib_route("kingston_401"), calib_route("kingston_407")
        self.assertLess(f["typical_s"], k["typical_s"])

    def test_407_factor_rows(self):
        hol = tolls.holidays_default()
        etr = {"etr": True, "urban": True, "hwy": True, "s": 60.0}
        self.assertEqual(drt.factors(etr, dt.datetime(2026, 10, 14, 8, 0), hol)[:2], (1.05, 1.25))
        self.assertEqual(drt.factors(etr, dt.datetime(2026, 10, 14, 12, 0), hol)[:2], (1.0, 1.15))
        self.assertEqual(drt.factors(etr, dt.datetime(2026, 10, 17, 8, 0), hol)[:2], (1.0, 1.15))
        typ, plan, _ = drt.factors(etr, dt.datetime(2026, 10, 14, 8, 0), hol, None, "valhalla")
        self.assertAlmostEqual(typ, 1.05 * 0.85)
        self.assertAlmostEqual(plan, 1.25 * 0.85)
        self.assertEqual(drt.calibration({"etr": False, "urban": True, "hwy": True}, "valhalla"), 1.0)
        self.assertEqual(drt.calibration({"etr": False, "urban": False, "hwy": True}, "valhalla"), 0.88)
        self.assertEqual(drt.calibration({"etr": True, "urban": True, "hwy": True}, "tomtom"), 1.0)
        self.assertEqual(drt.tomtom_plan_factor(etr, dt.datetime(2026, 10, 14, 8, 0), hol), (1.08, "407"))


class EastClipTiming(unittest.TestCase):
    """F2: a 407 stretch that starts or ends on the free 407 East is timed at Brock Rd."""

    def test_segments_use_point_times(self):
        pts = list(reversed(densify(etr_points(86.5, 108.0) + [(43.93, -79.0), (43.95, -78.90)])))
        times = [100.0 + 800.0 * i / (len(pts) - 1) for i in range(len(pts))]
        first = [i for i, p in enumerate(pts) if p[1] < EAST][0]
        seg = tolls.segments_407([{"points": pts, "times": times, "offset_s": 100.0, "end_s": 900.0}])[0]
        self.assertEqual((seg["entry"], seg["direction"]), ("Brock Rd", "W"))
        self.assertAlmostEqual(seg["offset_s"], times[first], delta=0.1)
        self.assertGreater(seg["offset_s"], 300.0)
        self.assertEqual(seg["end_s"], 900.0)
        # without times (synthetic callers) the stretch values stay
        self.assertEqual(tolls.segments_407([{"points": pts, "offset_s": 100.0}])[0]["offset_s"], 100.0)

    def test_eastbound_end_and_return_entry(self):
        wood = (ic("Woodbine Ave")["lat"], ic("Woodbine Ave")["lon"])
        far = (44.0187, -78.6232)
        trip = _valhalla_trip([
            (densify([MILTON, (43.66, -79.60), wood]), ["401", "Highway 401"], False, 100.0),
            (densify(etr_points(84.4, 108.0) + [(43.93, -79.0), far]), ["Highway 407"], True, 105.0),
            (densify([far, (44.20, -78.60)]), ["115", "Highway 115"], False, 100.0)])["trip"]
        b, extra = drt._valhalla_trip(trip)
        self.assertEqual(len(b.stretches[0]["times"]), len(b.stretches[0]["points"]))
        r = drt._finish(b, extra, "valhalla", dt.datetime(2026, 10, 16, 8, 0), OPTS)
        sg = r["etr"][0]
        self.assertEqual(sg["exit"], "Brock Rd")
        man = trip["legs"][0]["maneuvers"]
        before, on = man[0]["time"], man[1]["time"]
        pts = densify(etr_points(84.4, 108.0) + [(43.93, -79.0), far])
        cum = dc.cum_dist(pts)
        k = max(i for i, p in enumerate(pts) if p[1] < EAST)
        self.assertAlmostEqual(sg["end_base_s"], before + on * cum[k] / cum[-1], delta=2)
        self.assertLess(sg["end_base_s"], before + on - 60)
        back = drt.reverse_for_return(r, dt.datetime(2026, 10, 16, 11, 35), OPTS)
        self.assertAlmostEqual(back["etr"][0]["offset_base_s"], r["base_s"] - sg["end_base_s"], delta=1)
        self.assertEqual(back["etr"][0]["entry"], "Brock Rd")


class FreeEastNaming(unittest.TestCase):
    """F4 and K9: the free 407 East reads 'Hwy 407 East (free)' on a toll-free route."""

    def test_road_parts(self):
        east_pts = [(43.93, -79.0), (43.95, -78.9)]
        self.assertEqual(drt.road_parts(east_pts, 9000.0, "Hwy 407", EAST), [("Hwy 407 East (free)", 9000.0)])
        self.assertEqual(drt.road_parts(east_pts, 9000.0, "407 ETR", EAST), [("Hwy 407 East (free)", 9000.0)])
        west = densify(etr_points(99.4, 108.0))
        self.assertEqual(drt.road_parts(west, 8000.0, "407 ETR", EAST), [("407 ETR", 8000.0)])
        both = drt.road_parts(densify(etr_points(99.4, 108.0) + east_pts), 20000.0, "407 ETR", EAST)
        self.assertEqual([n for n, _ in both], ["407 ETR", "Hwy 407 East (free)"])
        self.assertAlmostEqual(sum(m for _, m in both), 20000.0)
        self.assertEqual(drt.road_parts(east_pts, 9000.0, "Hwy 401", EAST), [("Hwy 401", 9000.0)])

    def test_osrm_tollfree_on_407_east(self):
        pts = densify([(43.93, -79.0), (44.0187, -78.6232)])
        rt = _osrm_route([_osrm_step(densify([(43.80, -79.20), (43.93, -79.0)]), "Highway 401", "401", 100),
                          _osrm_step(pts, "Highway 407", "407 ETR", 105),
                          _osrm_step(densify([(44.0187, -78.6232), (44.20, -78.60)]), "Highway 115", "115", 100)])
        r = drt._finish(*drt._osrm_route(rt), engine="osrm", depart=WED_0730, opts=OPTS)
        self.assertEqual(r["etr"], [])
        self.assertFalse(r["has_tolls"])
        self.assertIn("Hwy 407 East (free)", r["roads"])
        self.assertNotIn("407 ETR", r["roads"])


class Ranking(unittest.TestCase):
    """F5: the toll-free route can be fastest; relabel re-ranks after a retime."""

    def r(self, typ, tolls_cad=0.0, etr=False, km=50.0):
        return {"typical_s": typ, "has_tolls": tolls_cad > 0, "etr": [1] if etr else [], "km": km,
                "toll_407_transponder": tolls_cad, "toll_other_cad": 0.0, "label": ""}

    def test_tollfree_can_be_fastest(self):
        f, tf = self.r(1000, 10.0, True), self.r(900, km=52)
        out = drt._pick(f, tf, [])
        self.assertEqual([(x["label"], x["typical_s"]) for x in out], [("fastest", 900)])
        f, tf = self.r(800, 10.0, True), self.r(900, km=52)
        self.assertEqual([x["label"] for x in drt._pick(f, tf, [])], ["fastest", "toll-free"])

    def test_relabel_after_retime(self):
        a, b, c = self.r(1000, 10.0, True), self.r(1100, km=52), self.r(1200, km=55)
        a["label"], b["label"], c["label"] = "fastest", "toll-free", "alternative"
        routes = [a, b, c]
        b["typical_s"] = 950   # e.g. weather or arrive-by retime
        drt.relabel(routes)
        self.assertEqual([(x["label"], x["typical_s"]) for x in routes],
                         [("fastest", 950), ("alternative", 1000), ("alternative", 1200)])
        b["typical_s"] = 1300
        drt.relabel(routes)
        self.assertEqual([(x["label"], x["typical_s"]) for x in routes],
                         [("fastest", 1000), ("toll-free", 1200), ("alternative", 1300)])
        self.assertEqual(drt.relabel([]), [])


class TomTomRoadNames(unittest.TestCase):
    """F6: TomTom routes name their roads from importantRoadStretch sections."""

    def test_roads_from_stretches(self):
        reply = tomtom_reply(False)
        rt = reply["routes"][0]
        n = len(rt["legs"][0]["points"])
        rt["sections"] += [
            {"startPointIndex": 0, "endPointIndex": n // 2, "sectionType": "IMPORTANT_ROAD_STRETCH",
             "importantRoadStretchIndex": 0, "streetName": {"text": "Macdonald-Cartier Freeway"},
             "roadNumbers": [{"text": "401"}]},
            {"startPointIndex": n // 2, "endPointIndex": n - 1, "sectionType": "IMPORTANT_ROAD_STRETCH",
             "importantRoadStretchIndex": 1, "roadNumbers": [{"text": "404"}]}]
        r = drt._finish(*drt._tomtom_route(rt), engine="tomtom", depart=WED_0730, opts=OPTS)
        self.assertEqual(r["roads"], ["Hwy 401", "Hwy 404"])

    def test_url_asks_for_stretches(self):
        now = (dt.datetime(2026, 10, 10, 12, 0), "zoneinfo")
        with mock.patch.object(drt.th, "local_now", return_value=now):
            url, key = drt.tomtom_url(MILTON, MARKHAM, WED_0730, dict(OPTS, tomtom_key="K"))
        self.assertIn("sectionType=importantRoadStretch", url)
        self.assertNotIn("K&", key)


class HolidaysAndData(unittest.TestCase):
    """F7, F8, F9, F10."""

    def test_civic_holiday_and_easter_monday_returns(self):
        rur = {"urban": False, "s": 60.0}
        hol = tolls.holidays_default()
        for day in (dt.datetime(2026, 8, 3, 14, 0), dt.datetime(2027, 3, 29, 14, 0), dt.datetime(2027, 8, 2, 14, 0)):
            self.assertEqual(drt.factors(rur, day, hol, "returning")[:2], (1.15, 1.4), day)
        self.assertEqual(drt.factors(rur, dt.datetime(2026, 8, 10, 14, 0), hol, "returning")[:2], (1.03, 1.15))
        # the 407 still prices those Mondays as weekdays (not ESA holidays)
        p = tolls.price_407({"entry_km": 34.8, "exit_km": 84.4, "direction": "E"}, dt.datetime(2026, 8, 3, 14, 0), True)
        self.assertEqual(p["day_type"], "weekday")

    def test_401_between_milton_and_407_is_urban(self):
        self.assertEqual(drt.area_of(43.570, -79.815), "GTHA")   # Hwy 401 near James Snow Pkwy
        self.assertEqual(drt.area_of(43.590, -79.800), "GTHA")   # 401/407 junction
        self.assertEqual(drt.area_of(43.475, -79.850), "GTHA")   # old gap between Milton and Burlington boxes
        self.assertEqual(drt.area_of(*HOME), "GTHA")

    def test_zone_1_from_km_0(self):
        q = ic("QEW / Hwy 403 (Burlington)")
        self.assertEqual(q["km"], 0.0)
        segs = tolls.segments_407([{"points": densify(etr_points(0.0, 6.0)), "offset_s": 0.0}])
        self.assertEqual((segs[0]["entry_km"], segs[0]["exit_km"], segs[0]["km"]), (0.0, 6.0, 6.0))
        p = tolls.price_407(segs[0], dt.datetime(2026, 10, 14, 12, 0), True)
        self.assertEqual(p["toll"], round(6.0 * 56.48 / 100 + 1.0, 2))

    def test_holidays_beyond_stored_years(self):
        stored = tolls.data()["etr407"]["holidays_priced_as_weekend"]
        for y in (2026, 2027):
            self.assertEqual(tolls.esa_holidays(y), set(d for d in stored if d.startswith(str(y))))
        seg = {"entry_km": 34.8, "exit_km": 84.4, "direction": "E"}
        p = tolls.price_407(seg, dt.datetime(2028, 10, 9, 12, 0), True)   # Thanksgiving 2028
        self.assertEqual(p["day_type"], "weekend")
        self.assertIn("2028 holidays computed from Ontario ESA rules", p["note"])
        self.assertIn("2028-04-14", tolls.holidays_default())   # Good Friday 2028
        self.assertEqual(tolls.price_407(seg, dt.datetime(2028, 10, 11, 12, 0), True)["day_type"], "weekday")


def _kingston_401():
    return _valhalla_trip([(densify([MILTON, (43.66, -79.60), (43.76, -79.30), (43.90, -78.85)], 500),
                            ["401", "Highway 401"], False, 90.0),
                           (densify([(43.90, -78.85), KINGSTON], 1000), ["401", "Highway 401"], False, 100.0)])


def _kingston_407():
    j401 = (ic("Hwy 401")["lat"], ic("Hwy 401")["lon"])
    return _valhalla_trip([(densify([MILTON, j401]), ["401", "Highway 401"], False, 90.0),
                           (densify(etr_points(34.8, 108.0), 500), ["Highway 407"], True, 110.0),
                           (densify([(ic("Brock Rd")["lat"], ic("Brock Rd")["lon"]), (43.95, -78.90), KINGSTON], 1000),
                            ["401", "Highway 401"], False, 105.0)])


class RushHour407Option(unittest.TestCase):
    """UX3: a 407 option is asked for when the free-flow fastest route skips it at a busy time."""

    def test_variant_points(self):
        thr = drt.variant_through(HOME, KINGSTON)
        self.assertEqual(len(thr), 2)
        # inset from both ends: a south Milton origin projects onto the chain before its real entry
        self.assertEqual([p["ic"] for p in thr], ["Mavis Rd", "Donald Cousens Pkwy"])
        for p in thr:
            self.assertTrue(20 <= p["heading"] <= 120, p)   # eastbound (the 407 runs NE in Mississauga)
            self.assertLess(tolls.chain_km(p["lat"], p["lon"])[1], 400)
        self.assertGreater(tolls.chain_km(thr[0]["lat"], thr[0]["lon"])[0], tolls.chain_km(*HOME)[0])
        back = drt.variant_through(KINGSTON, HOME)
        self.assertEqual([p["ic"] for p in back], ["Woodbine Ave", "Britannia Rd"])
        self.assertTrue(all(110 <= p["heading"] <= 300 for p in back), back)   # westbound (SE near Britannia)
        self.assertIsNone(drt.variant_through(MILTON, (43.53, -79.74)))      # under 15 km of 407
        self.assertIsNone(drt.variant_through((45.42, -75.70), (44.23, -76.48)))   # nowhere near the 407

    def test_when_it_is_wanted(self):
        r = calib_route("kingston_401")
        self.assertTrue(drt.wants_407_option(r, dt.datetime(2026, 10, 16, 15, 0), OPTS))
        self.assertTrue(drt.wants_407_option(r, dt.datetime(2026, 10, 14, 7, 30), OPTS))
        self.assertFalse(drt.wants_407_option(r, dt.datetime(2026, 10, 18, 6, 0), OPTS))
        self.assertFalse(drt.wants_407_option(calib_route("tobermory_free"), dt.datetime(2026, 10, 17, 7, 0), OPTS))

    def test_engine_adds_407_option(self):
        fake = FakeFetch([(("valhalla", "through"), _kingston_407()), (("valhalla",), _kingston_401())])
        w = []
        with mock.patch.object(dc, "fetch", side_effect=fake):
            routes = drt.get_routes(MILTON, KINGSTON, dt.datetime(2026, 10, 16, 15, 0), dict(OPTS), w)
        self.assertEqual(len(fake.urls), 2)
        q = json.loads(urllib.parse.unquote(fake.urls[1].split("json=", 1)[1]))
        self.assertEqual([l.get("type") for l in q["locations"]], [None, "through", "through", None])
        self.assertTrue(any(r["etr"] for r in routes))
        self.assertEqual([r["label"] for r in routes], ["fastest", "toll-free"])
        self.assertTrue(routes[0]["etr"])
        self.assertTrue(any("407 option added" in n for n in routes[0]["notes"]))
        back = drt.reverse_for_return(routes[0], dt.datetime(2026, 10, 16, 20, 0), OPTS)
        self.assertFalse(any("407 option added" in n for n in back["notes"]))
        # off-peak: no extra request
        fake = FakeFetch([(("valhalla",), _kingston_401())])
        with mock.patch.object(dc, "fetch", side_effect=fake):
            routes = drt.get_routes(MILTON, KINGSTON, dt.datetime(2026, 10, 18, 6, 0), dict(OPTS), [])
        self.assertEqual(len(fake.urls), 1)
        self.assertEqual([r["label"] for r in routes], ["fastest"])


def _gordie():
    return [b for b in tolls.data()["bridges"] if b["name"] == "Gordie Howe International Bridge"][0]


def _border_fast():
    g = _gordie()
    qew = (ic("QEW / Hwy 403 (Burlington)")["lat"], ic("QEW / Hwy 403 (Burlington)")["lon"])
    return _valhalla_trip([(densify([MILTON, (ic("Hwy 401")["lat"], ic("Hwy 401")["lon"])]), ["401"], False, 100.0),
                           (densify(etr_points(34.8, 0.0)), ["407 ETR", "Highway 407"], True, 110.0),
                           (densify([qew, (42.40, -82.90), tuple(g["canada_side"])], 1000), ["401"], False, 105.0),
                           (densify([tuple(g["canada_side"]), tuple(g["us_side"]), DETROIT], 50), ["I-75"], True, 60.0)])


def _border_no407(detour=False):
    g = _gordie()
    via = [(43.0, -81.2), (42.40, -82.90)] if not detour else [(43.10, -79.07), (42.90, -78.90), (41.50, -81.70)]
    return _valhalla_trip([(densify([MILTON] + via + [tuple(g["canada_side"])], 1000), ["401"], False, 95.0),
                           (densify([tuple(g["canada_side"]), tuple(g["us_side"]), DETROIT], 50), ["I-75"], True, 60.0)])


class BorderComparison(unittest.TestCase):
    """UX5: the route compared with the 407 keeps the border crossing."""

    def run_border(self, reply):
        fake = FakeFetch([(("valhalla", "use_tolls"), reply), (("valhalla",), _border_fast())])
        w = []
        with mock.patch.object(dc, "fetch", side_effect=fake):
            routes = drt.get_routes(MILTON, DETROIT, dt.datetime(2026, 10, 20, 9, 0), dict(OPTS), w)
        return routes, w, fake

    def test_without_407_keeps_bridge(self):
        routes, w, fake = self.run_border(_border_no407())
        q = json.loads(urllib.parse.unquote(fake.urls[1].split("json=", 1)[1]))
        self.assertEqual(q["costing_options"]["auto"], {"use_tolls": 0.0})
        self.assertEqual([r["label"] for r in routes], ["fastest", "without 407"])
        f, n = routes
        self.assertTrue(f["etr"])
        self.assertEqual(n["etr"], [])
        self.assertEqual([b["name"] for b in n["bridges"]], ["Gordie Howe International Bridge"])
        self.assertTrue(n["has_tolls"])
        self.assertIn(drt.BORDER_NOTE, f["notes"])
        self.assertEqual(w, [])

    def test_detour_rejected(self):
        routes, w, _ = self.run_border(_border_no407(detour=True))
        self.assertEqual([r["label"] for r in routes], ["fastest"])
        self.assertTrue(any("no route without the 407 that keeps the border crossing" in x for x in w))

    def test_keep_comparison_rules(self):
        fast = {"bridges": [{"name": "X"}], "km": 350.0, "etr": [1]}
        w = []
        self.assertIsNone(drt._keep_comparison(fast, {"bridges": [], "km": 360.0, "etr": []}, "OSRM", w))
        self.assertIsNone(drt._keep_comparison(fast, {"bridges": [{"name": "X"}], "km": 360.0, "etr": [1]}, "OSRM", w))
        ok = {"bridges": [{"name": "X"}], "km": 360.0, "etr": []}
        self.assertIs(drt._keep_comparison(fast, ok, "OSRM", w), ok)
        plain = {"bridges": [], "km": 70.0, "etr": []}
        self.assertIs(drt._keep_comparison({"bridges": [], "km": 60.0, "etr": [1]}, plain, "OSRM", w), plain)
        self.assertEqual(len(w), 2)


if __name__ == "__main__":
    unittest.main()
