"""Regression tests for the v0.9.8 EV and car lookup fixes: destination DC chargers never crowded out by Level 2
posts, the stop planner's 'adds nothing' rule in _pick and _onward, per-network site rows, US Level 2 prices,
3-decimal coordinates to NLR and Overpass, the Overpass retry and error replies, and the NRCan lookup (no-year
trims, makes with spaces, no silent make switch, BOM profiles, a changed CSV layout, download hosts).

Offline: every fetch is mocked. Run: python -m unittest tests.test_v098_ev_car -v
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
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import ev_charging as ev  # noqa: E402
import vehicles as V  # noqa: E402
import test_ev as tev  # noqa: E402
import test_vehicles as tveh  # noqa: E402

SKILL = os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner")
DEST = (43.6453, -79.3806)


def l2(sid, name, lat, lon, network="FLO", pricing="", country="CA", kw=7.2):
    return {"id": sid, "station_name": name, "latitude": lat, "longitude": lon, "status_code": "E",
            "ev_network": network, "ev_connector_types": ["J1772"], "ev_level2_evse_num": 2, "ev_pricing": pricing,
            "country": country, "ev_charging_units": [{"charging_level": "level2", "connectors": {
                "J1772": {"power_kw": kw, "port_count": 2}}}]}


def dcrec(sid, name, lat, lon, kw, ports=2, network="FLO", code="J1772COMBO", pricing="$0.48 per kWh", access=""):
    r = tev.rec(sid, name, 0, {code: (kw, ports)}, network=network, pricing=pricing, access=access)
    r["latitude"], r["longitude"] = lat, lon
    return r


class FakeNearest:
    """NLR nearest.json as the API behaves: sorted by distance, cut at `limit`, total_results for the whole
    match; ev_charging_level=dc_fast keeps DC fast stations only."""

    def __init__(self, stations):
        self.stations, self.calls = stations, []

    def __call__(self, url, data=None, headers=None, **kw):
        if "nearest.json" not in url:
            raise AssertionError("unexpected URL " + url)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.calls.append(q)
        lat, lon = float(q["latitude"]), float(q["longitude"])
        rs = [r for r in self.stations if q.get("ev_charging_level") != "dc_fast"
              or any(u.get("charging_level") == "dc_fast" for u in r["ev_charging_units"])]
        rs.sort(key=lambda r: (r["latitude"] - lat) ** 2 + (r["longitude"] - lon) ** 2)
        return json.dumps({"total_results": len(rs), "fuel_stations": rs[:int(q.get("limit", 20))]}).encode()


class DestinationDcNotCrowdedOut(unittest.TestCase):   # item 1
    def dest(self, stations, car=None):
        net = FakeNearest(stations)
        w = []
        with mock.patch.object(dc, "fetch", side_effect=net), mock.patch.object(ev, "log"):
            out = ev._dest_chargers(DEST, car or ev.car_profile("ioniq5"), w, {})
        return out, net.calls, w

    def test_dense_downtown_still_lists_the_dc_sites(self):
        near = [l2(1000 + i, "L2 post %d" % i, DEST[0] + 0.0001 * (i % 20), DEST[1] + 0.0001 * (i // 20))
                for i in range(260)]
        far = [dcrec(2000, "Garage 50 kW", DEST[0] + 0.03, DEST[1], 50.0),
               dcrec(2001, "Ivy 180 kW", DEST[0] + 0.031, DEST[1] + 0.003, 180.0, ports=4, network="IVY")]
        out, calls, w = self.dest(near + far)
        self.assertEqual(w, [])
        self.assertEqual([c.get("ev_charging_level") for c in calls], [None, "dc_fast"])
        self.assertEqual(calls[0]["limit"], str(ev.NEAR_LIMIT))
        fast = [(x["name"], x["max_kw"]) for x in out if x["level"] == "DC fast"]
        self.assertEqual(fast, [("Garage 50 kW", 50.0), ("Ivy 180 kW", 180.0)])
        txt = ev._dest_time_text(30.0, ev._show_best_dc(out), ev.car_profile("ioniq5"), 15, ev._ev()["model"])
        self.assertIn("on the 180 kW DC at Ivy 180 kW", txt)

    def test_one_call_when_the_reply_is_complete(self):
        out, calls, _ = self.dest([l2(1, "Library L2", DEST[0] + 0.001, DEST[1]),
                                   dcrec(2, "Mall DC", DEST[0] + 0.01, DEST[1], 100.0)])
        self.assertEqual(len(calls), 1)   # the DEMO_KEY allows about 10 calls an hour
        self.assertEqual([x["name"] for x in out], ["Library L2", "Mall DC"])


def run(route_km, recs, car, soc, temp=15.0):
    st = [ev.norm_nlr(r) for r in recs]
    w = []
    with mock.patch.object(ev, "stations_near", return_value=[]), mock.patch.object(ev, "log"):
        r = ev.plan_charging(tev.line(route_km, n=400), route_km, tev.DEPART, car, soc, temp, w,
                             {"hwy_share": 0.8, "stations": st, "stations_source": "NLR synthetic"})
    return r, w


def dc_at(sid, name, km, kw, ports, off_deg=0.001):
    return tev.rec(sid, name, km, {"J1772COMBO": (kw, ports)}, pricing="$0.48 per kWh", off_deg=off_deg)


class StopThatAddsNothing(unittest.TestCase):   # item 2 (review items 15 and 16)
    def test_charger_reached_near_max_soc_is_not_the_pick(self):
        car = ev.car_profile("ioniq5")
        recs = [dc_at(1, "Early 150", 20, 150.0, 4), dc_at(2, "Mid 50", 200, 50.0, 2)]
        for soc in (0.85, 0.90, 1.00):
            r, w = run(350, recs, car, soc)
            self.assertTrue(r["feasible"], (soc, w))
            self.assertEqual([s["name"] for s in r["stops"]], ["Mid 50"], soc)
            self.assertFalse([x for x in w if "far enough ahead" in x])

    def test_faster_charger_just_before_the_critical_one(self):
        car = ev.car_profile("ioniq5")
        m = ev._ev()["model"]
        per_km = ev.consumption(car, 15.0, m["default_speed_kmh"], 0.8) / 100.0 / car["battery_kwh"]
        reach = (m["max_soc"] - m["reserve"]["default"]) / per_km
        for gap in (3, 6, 9):
            recs = [dc_at(1, "Fast 250", 250 - gap, 250.0, 6), dc_at(2, "Critical 150", 250, 150.0, 4),
                    dc_at(3, "Far 150", 250 + reach - 1.0, 150.0, 4)]
            r, w = run(600, recs, car, 0.9)
            self.assertTrue(r["feasible"], (gap, w))
            self.assertEqual([s["name"] for s in r["stops"]], ["Critical 150", "Far 150"])

    def test_a_short_top_up_when_it_is_the_only_way_on(self):
        # every charger before km 300 is reached above 77% from a full battery; the stop at km 80.8 adds less than
        # 3 points but is the only way to reach the km 312.7 charger, so the trip is feasible
        recs = [dc_at(1, "A", 11.8, 180.0, 6, 0.003), dc_at(2, "B", 33.6, 50.0, 6, 0.01),
                dc_at(3, "C", 80.8, 100.0, 6, 0.0005), dc_at(4, "D", 312.7, 250.0, 6)]
        r, w = run(350, recs, ev.car_profile("ioniq5"), 1.0)
        self.assertTrue(r["feasible"], w)
        self.assertEqual([s["name"] for s in r["stops"]][:2], ["C", "D"])
        self.assertGreater(r["stops"][0]["depart_soc"], r["stops"][0]["arrive_soc"])
        self.assertGreaterEqual(r["arrive_soc"], 0.15 - 0.005)

    def test_a_real_gap_is_still_infeasible(self):
        r, w = run(450, [dc_at(1, "A", 20, 150.0, 4), dc_at(2, "B", 420, 150.0, 4)], ev.car_profile("ioniq5"), 0.9)
        self.assertFalse(r["feasible"])
        self.assertTrue(w)


class SiteRowsKeepTheirOwnNetwork(unittest.TestCase):   # item 3
    def rows(self, near, car):
        with mock.patch.object(ev, "stations_near", return_value=[ev.norm_nlr(r) for r in near]):
            return ev._dest_chargers(DEST, car, [], {})

    def test_neighbours_on_other_networks_stay_apart(self):
        car = ev.car_profile("ioniq5", nacs_adapter=True)
        near = [dcrec(1, "Tesla - 17953 - Cobourg ON", DEST[0] + 0.0010, DEST[1], 250.0, ports=8, network="Tesla",
                      code="TESLA", pricing="$0.55 per kWh", access="24 hours daily. NACS partner enabled."),
                dcrec(2, "Cobourg FLO", DEST[0] + 0.0013, DEST[1], 100.0, ports=2, network="FLO")]
        out = self.rows(near, car)
        self.assertEqual(sorted((x["name"], x["network"], x["max_kw"]) for x in out),
                         [("Cobourg FLO", "FLO", 100.0), ("Tesla - 17953 - Cobourg ON", "Tesla Supercharger", 250.0)])
        txt = ev._dest_time_text(30.0, ev._show_best_dc(out), car, 15, ev._ev()["model"])
        self.assertIn("on the 250 kW DC at Tesla - 17953 - Cobourg ON", txt)
        near = [dcrec(3, "Durham Mercedes-Benz", DEST[0] + 0.0010, DEST[1], 50.0, ports=1, network="FLO"),
                dcrec(4, "Durham ChargePoint", DEST[0] + 0.0014, DEST[1], 163.0, ports=2,
                      network="ChargePoint Network")]
        out = self.rows(near, ev.car_profile("ioniq5"))
        self.assertEqual([(x["name"], x["network"], x["max_kw"], x["ports"]) for x in out],
                         [("Durham Mercedes-Benz", "FLO", 50.0, 1), ("Durham ChargePoint", "ChargePoint", 163.0, 2)])

    def test_posts_of_one_site_merge_under_the_faster_post(self):
        near = [dcrec(1, "Plaza post 1", DEST[0] + 0.0010, DEST[1], 50.0, ports=1),
                dcrec(2, "Plaza post 2", DEST[0] + 0.0012, DEST[1], 100.0, ports=2)]
        out = self.rows(near, ev.car_profile("ioniq5"))
        self.assertEqual([(x["name"], x["max_kw"], x["ports"]) for x in out], [("Plaza post 2", 100.0, 3)])


class UsLevel2Price(unittest.TestCase):   # item 4
    def test_us_dollars_named_and_converted(self):
        near = [l2(1, "Buffalo garage", 42.8874, -78.8784, network="ChargePoint Network", pricing="$2.00 per hour",
                   country="US"),
                l2(2, "Niagara garage", 43.0901, -79.0700, pricing="$2.00 per hour", country="CA")]
        fx, _ = ev.usd_cad()
        with mock.patch.object(ev, "stations_near", return_value=[ev.norm_nlr(r) for r in near[:1]]):
            us = ev._dest_chargers((42.8864, -78.8784), ev.car_profile("ioniq5"), [], {})
        self.assertEqual(us[0]["price_text"], "listed: US$2.00 per hour; about $%.2f/h in CAD at %.2f"
                         % (2.0 * fx, fx))
        self.assertEqual(ev._l2_price_text("$2.00 per hour", "CA"), "listed: $2.00 per hour")
        self.assertEqual(ev._l2_price_text("Free", "US"), "listed free")
        self.assertEqual(ev._l2_price_text("call the host", "US"), "listed: call the host")


class CoordinatesRounded(unittest.TestCase):   # item 5
    def test_nlr_and_overpass_get_three_decimals(self):
        net = tev.FakeNet(route=[], near=[])
        poly = [(43.498757, -79.855987), (43.6453, -79.3806)]
        with mock.patch.object(dc, "fetch", side_effect=net), mock.patch.object(ev, "log"):
            ev.stations_along(poly, [])
            ev.stations_near(43.498757, -79.855987, [])
        wkt = net.calls[0]["data"]["route"]
        self.assertTrue(wkt.startswith("LINESTRING(-79.856 43.499,"), wkt)
        self.assertFalse(re.search(r"\d\.\d{4}", wkt))
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(net.calls[1]["url"]).query))
        self.assertEqual((q["latitude"], q["longitude"]), ("43.499", "-79.856"))
        net = tev.FakeNet(fail_nlr=429, overpass=[])
        with mock.patch.object(dc, "fetch", side_effect=net), mock.patch.object(ev, "log"):
            ev.stations_along(poly, [])
            ev.stations_near(43.498757, -79.855987, [])
        for c in net.calls:
            if "overpass" in c["url"]:
                self.assertFalse(re.search(r"\d\.\d{4}", c["data"]["data"]), c["data"]["data"])


class OverpassRetryAndErrors(unittest.TestCase):   # item 15
    BODY = json.dumps({"elements": [{"type": "node", "id": 1, "lat": 44.0, "lon": -79.0, "tags": {}}]}).encode()
    REMARK = json.dumps({"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 1 after "
                                                   "91 seconds."}).encode()

    def test_429_waits_and_retries_once(self):
        err = urllib.error.HTTPError(ev.OVERPASS_URL, 429, "busy", {}, None)
        w = []
        with mock.patch.object(dc, "fetch", side_effect=[err, self.BODY]) as f, \
                mock.patch.object(ev.time, "sleep") as sl, mock.patch.object(ev, "log"):
            els = ev._overpass("q", w)
        self.assertEqual(len(els), 1)
        self.assertEqual(f.call_count, 2)
        sl.assert_called_once_with(30)
        self.assertEqual(w, [])
        self.assertIs(f.call_args[1]["validate"], ev._overpass_ok)

    def test_second_429_is_a_warning(self):
        errs = [urllib.error.HTTPError(ev.OVERPASS_URL, 429, "busy", {}, None) for _ in range(2)]
        w = []
        with mock.patch.object(dc, "fetch", side_effect=errs) as f, mock.patch.object(ev.time, "sleep"), \
                mock.patch.object(ev, "log"):
            self.assertIsNone(ev._overpass("q", w))
        self.assertEqual(f.call_count, 2)
        self.assertEqual(w, ["OpenStreetMap charger fallback failed (HTTP 429)"])

    def test_validate(self):
        self.assertIsNone(ev._overpass_ok(self.BODY))
        self.assertIn("timed out", ev._overpass_ok(self.REMARK))
        self.assertTrue(ev._overpass_ok(b'{"remark": "x"}'))

    def test_error_reply_is_not_cached(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        replies = [self.REMARK, self.BODY]

        class Resp(object):
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):   # as a real reply: the body, then nothing
                body, self.body = self.body, b""
                return body

        def opener(req, timeout=None):
            return Resp(replies.pop(0))
        w = []
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": tmp}), \
                mock.patch.object(dc._OPENER, "open", side_effect=opener), \
                mock.patch.object(dc.time, "sleep"), mock.patch.object(ev, "log"):
            self.assertIsNone(ev._overpass("[out:json];node(1);out;", w))
            self.assertEqual(os.listdir(os.path.join(tmp, "driving")), [])
            self.assertTrue(w and "Overpass: runtime error: Query timed out" in w[0], w)
            self.assertEqual(len(ev._overpass("[out:json];node(1);out;", [])), 1)
            self.assertEqual(len(os.listdir(os.path.join(tmp, "driving"))), 1)
        dc.stale_notes()


@unittest.skipUnless(os.path.exists(V.INDEX_PATH), "bundled vehicles index missing")
class BundledLookups(unittest.TestCase):   # items 6, 7, 8
    def setUp(self):
        V.set_index(None)
        self.net = mock.patch.object(dc, "fetch", side_effect=AssertionError("network used"))
        self.net.start()

    def tearDown(self):
        self.net.stop()

    def look(self, text):
        w = []
        return V.lookup(text, w, allow_download=False), w

    def test_no_year_uses_the_newest_name_of_a_trim(self):
        r, w = self.look("Honda Civic Si")
        self.assertEqual((r["year"], r["comb"]), (2026, 7.7), w)
        self.assertTrue(all("Si" in n for n in r["variant_names"]))
        r, w = self.look("Toyota Corolla LE")
        self.assertEqual(r["year"], max(V._search()["years"]))
        self.assertEqual(r["model"], "Corolla")

    def test_no_year_keeps_other_models_and_versions(self):
        for text, part in (("Toyota Prius c", "Prius c"), ("Ford F-150 Lightning", "Lightning"),
                           ("Jeep Grand Cherokee SRT", "SRT"), ("Alfa Romeo Giulia Quadrifoglio", "Quadrifoglio"),
                           ("Lexus ES 300h", "300h")):
            r, w = self.look(text)
            self.assertIsNotNone(r, (text, w))
            self.assertIn(part, r["label"], (text, w))
        r, _ = self.look("Toyota Camry TRD")
        self.assertEqual(r["kind"], "gas")   # the 2026 Camry is hybrid only: keep the TRD's own powertrain

    def test_no_silent_make_switch_on_a_letter(self):
        r, w = self.look("2022 Honda e")
        self.assertIsNone(r)
        self.assertIn("Honda", w[-1])
        self.assertNotIn("Mercedes", " ".join(w))

    def test_distinctive_name_under_another_make_is_never_high(self):
        r, w = self.look("2021 GMC Silverado")
        self.assertEqual(r["make"], "Chevrolet")
        self.assertNotEqual(r["confidence"], "high")
        self.assertTrue(any("no GMC 'silverado'" in x for x in w))

    def test_makes_with_spaces(self):
        for text, model in (("2021 Alfa Romeo", "Giulia"), ("2020 smart EQ", "fortwo"),
                            ("2021 Aston Martin", "Vantage")):
            r, w = self.look(text)
            self.assertIsNone(r)
            self.assertIn(model, w[-1])
        r, w = self.look("2021 Alfa Romeo Stelvo")
        self.assertEqual((r["make"], r["model"]), ("Alfa Romeo", "Stelvio"))
        r, w = self.look("2021 Land Rover Velar")
        self.assertEqual((r["make"], r["label"]), ("Land Rover", "2021 Land Rover Range Rover Velar"))
        self.assertFalse([x for x in w if "in NRCan ratings; matched" in x])


class SavedProfileFile(unittest.TestCase):   # items 9 and 12
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "car.json")
        self.env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": self.path})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, True)

    def test_bom_file_loads(self):
        with open(self.path, "w", encoding="utf-8-sig") as f:
            json.dump({"label": "2022 Toyota Corolla", "l_per_100km": 6.8}, f)
        w = []
        self.assertEqual(V.load_profile(w)["l_per_100km"], 6.8)
        self.assertEqual(w, [])

    def test_warning_shows_the_home_folder_as_tilde(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        real = os.path.expanduser
        with mock.patch.object(V.os.path, "expanduser", side_effect=lambda p: self.tmp if p == "~" else real(p)):
            w = []
            V.load_profile(w)
        self.assertIn("saved car file %s is unreadable" % os.path.join("~", "car.json"), w[0])
        self.assertNotIn(self.tmp, w[0])


class ChangedCsvLayout(unittest.TestCase):   # item 10
    ROW = "\n2026,Honda,Civic,Compact,1.5,4,AV7,X,7.7,6.3,7.1,40,166,6,7\n"

    def test_renamed_columns_raise_a_layout_error(self):
        for old, new in (("Model year", "Year"), ("Make", "Manufacturer"), ("City (L/100 km)", "Urban"),
                         ("Highway (L/100 km)", "Hwy"), ("Combined (L/100 km)", "Comb")):
            raw = (tveh.GAS_HEAD.replace(old, new) + self.ROW).encode("utf-8")
            with self.assertRaises(V.LayoutError, msg=old):
                V.parse_csv(raw)
        self.assertEqual(len(V.parse_csv((tveh.GAS_HEAD + self.ROW).encode("utf-8"))), 1)

    def test_live_file_with_a_new_layout_falls_back_to_the_index(self):
        V.set_index(tveh.fixture_index())
        self.addCleanup(V.set_index, None)
        url = "https://open.canada.ca/x/download/my2026-fuel-consumption-ratings.csv"
        pkg = {"result": {"resources": [{"language": ["en"], "url": url}]}}

        def fake(u, **kw):
            if u == V.PACKAGE_API:
                return json.dumps(pkg).encode("utf-8")
            if u == url:
                return (tveh.GAS_HEAD.replace("Model year", "Year") + self.ROW).encode("utf-8")
            raise AssertionError(u)
        w = []
        with mock.patch.object(dc, "fetch", side_effect=fake):
            r = V.lookup("2026 Honda Civic", w)
        self.assertEqual(r["year"], 2024)
        self.assertTrue(any("layout changed" in x and "Model year" in x and "bundled ratings index" in x
                            for x in w), w)


class DownloadHosts(unittest.TestCase):   # item 11
    def test_url_check(self):
        ok = ["https://open.canada.ca/data/dataset/x/my2026-fuel-consumption-ratings.csv",
              "https://opencanada.blob.core.windows.net/opengovprod/resources/x/my2026.csv"]
        bad = ["http://open.canada.ca/x.csv", "https://evil.example/my2026.csv",
               "https://open.canada.ca.evil.example/x.csv", "https://user@evil.example/open.canada.ca/x.csv",
               "file:///etc/passwd", "", None]
        for u in ok:
            self.assertTrue(V.download_url_ok(u), u)
        for u in bad:
            self.assertFalse(V.download_url_ok(u), u)

    def test_other_host_never_fetched(self):
        V.set_index(tveh.fixture_index())
        self.addCleanup(V.set_index, None)
        url = "https://evil.example/my2009-fuel-consumption-ratings.csv"
        pkg = {"result": {"resources": [{"language": ["en"], "url": url}]}}
        seen = []

        def fake(u, **kw):
            seen.append(u)
            if u == V.PACKAGE_API:
                return json.dumps(pkg).encode("utf-8")
            raise AssertionError(u)
        w = []
        with mock.patch.object(dc, "fetch", side_effect=fake):
            self.assertFalse(V._download_year(2009, w))
        self.assertEqual(seen, [V.PACKAGE_API])
        self.assertTrue(any("skipped NRCan ratings file my2009-fuel-consumption-ratings.csv" in x for x in w), w)


class ShippedFiles(unittest.TestCase):   # items 13 and 14
    def test_index_names_the_repo_build_tool(self):
        with open(V.INDEX_PATH, "rb") as f:
            raw = f.read()
        self.assertEqual(json.loads(raw.decode("utf-8"))["built_by"], "tools/build_vehicles_index.py")
        self.assertNotIn(b"Users/", raw)
        tool = os.path.join(ROOT, "tools", "build_vehicles_index.py")
        with open(tool, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("C:/", src)
        self.assertNotIn("AppData", src)
        self.assertNotIn("Users/", src)
        self.assertIn('BUILT_BY = "tools/build_vehicles_index.py"', src)

    def test_no_unshipped_note_cited(self):
        for rel in (("references", "driving", "ev.json"), ("scripts", "ev_charging.py")):
            with open(os.path.join(SKILL, *rel), encoding="utf-8") as f:
                self.assertNotIn("ev.md", f.read(), rel)


if __name__ == "__main__":
    unittest.main()
