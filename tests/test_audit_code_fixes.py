"""Code fixes from the v0.9.5 claim audit: US and Canada EV chargers (country=all), the NRCan download with the
skill's own User-Agent and a bundled-index fallback, the Transitous attribution link in SECOND OPINION, and the
driving planner's own NETWORK BLOCKED text when geocoding is blocked.

All network calls are mocked. Run: python -m unittest discover -s tests -v
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
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import drive_plan as dp  # noqa: E402
import ev_charging as ev  # noqa: E402
import trip_hubs as th  # noqa: E402
import vehicles as V  # noqa: E402

PARK = "43.498757,-79.855987"          # Milton Community Park
LAT = 42.95                            # a route along the New York Thruway latitude
KM_PER_DEG = 111.32 * 0.7318           # cos(42.95 deg)
DEPART = dt.datetime(2026, 10, 17, 9, 0)


def us_line(km, n=200):
    return [(LAT, -78.9 + (km / KM_PER_DEG) * i / (n - 1)) for i in range(n)]


def us_station(sid, name, km, code="J1772COMBO", kw=350.0, ports=4, network="Electrify America", state="NY",
               pricing=""):
    """An NLR record as the API returns it for a US station (same fields and connector codes as Canada)."""
    lon = -78.9 + km / KM_PER_DEG
    return {"id": sid, "station_name": name, "latitude": LAT + 0.0005, "longitude": lon,
            "street_address": "%d Thruway Service Area" % sid, "city": "Batavia", "state": state, "zip": "14020",
            "status_code": "E", "access_code": "public", "ev_network": network, "ev_connector_types": [code],
            "ev_dc_fast_num": ports, "ev_level2_evse_num": None, "ev_pricing": pricing,
            "access_days_time": "24 hours daily",
            "ev_charging_units": [{"network": network, "charging_level": "dc_fast", "port_count": ports,
                                   "connectors": {code: {"power_kw": kw, "port_count": ports}}}]}


class FakeNLR:
    def __init__(self, route=None, near=None):
        self.route, self.near, self.calls = route or [], near or [], []

    def __call__(self, url, data=None, headers=None, **kw):
        self.calls.append({"url": url, "data": data, "headers": headers or {}})
        if "developer.nlr.gov" not in url:
            raise AssertionError("unexpected URL " + url)
        st = self.route if "nearby-route" in url else self.near
        return json.dumps({"total_results": len(st), "fuel_stations": st}).encode()


class EvCountryAllTest(unittest.TestCase):
    def test_both_calls_ask_for_us_and_canada(self):
        self.assertEqual(ev.NLR_COUNTRY, "all")
        net = FakeNLR(route=[us_station(1, "Pembroke Service Area", 60)],
                      near=[us_station(2, "Buffalo Downtown", 0, network="EVGO", pricing="$0.48 per kWh")])
        w = []
        with mock.patch.object(dc, "fetch", side_effect=net), mock.patch.object(ev, "log"):
            along, src = ev.stations_along(us_line(120), w)
            near = ev.stations_near(42.8864, -78.8784, w)
        self.assertEqual(w, [])
        self.assertEqual(net.calls[0]["data"]["country"], "all")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(net.calls[1]["url"]).query)
        self.assertEqual(q["country"], ["all"])
        self.assertNotIn("(NRCan data)", src)
        self.assertIn("US DOE and NRCan", src)
        s = along[0]
        self.assertEqual((s["name"], s["source"], s["network_code"]), ("Pembroke Service Area", "NLR", "Electrify America"))
        self.assertEqual(s["units"], {"J1772COMBO": (350.0, 4)})
        self.assertEqual(near[0]["units"], {"J1772COMBO": (350.0, 4)})
        self.assertEqual(near[0]["pricing"], "$0.48 per kWh")

    def test_us_corridor_plans_stops_with_the_same_connector_filter(self):
        route = [us_station(1, "Pembroke 150", 150), us_station(2, "Chademo 200", 200, code="CHADEMO", kw=50.0, ports=1),
                 us_station(3, "Syracuse 300", 300, network="EVGO"),
                 us_station(4, "Tesla only 320", 320, code="TESLA", kw=250.0, ports=8, network="Tesla")]
        route[3]["access_days_time"] = "24 hours daily. Open to Teslas only."
        net = FakeNLR(route=route)
        w = []
        with mock.patch.object(dc, "fetch", side_effect=net), mock.patch.object(ev.time, "sleep"), \
                mock.patch.object(ev, "log"):
            r = ev.plan_charging(us_line(420), 420, DEPART, ev.car_profile("equinox"), 0.8, 10, w,
                                 {"hwy_share": 0.85})
        self.assertTrue(r["needed"] and r["feasible"])
        self.assertEqual([s["name"] for s in r["stops"]], ["Pembroke 150", "Syracuse 300"])
        for s in r["stops"]:
            self.assertEqual(s["connectors"], ["J1772COMBO"])
        self.assertEqual(net.calls[0]["data"]["country"], "all")


class NrcanDownloadTest(unittest.TestCase):
    PKG = {"result": {"resources": [
        {"language": ["en"], "url": "https://open.canada.ca/x/download/my1995-2014-fuel-consumption-ratings-5-cycle.csv"}]}}

    def setUp(self):
        V._TRIED_DOWNLOAD.clear()

    def tearDown(self):
        V._TRIED_DOWNLOAD.clear()

    def test_headers_are_the_skills_own(self):
        h = V.download_headers()
        self.assertEqual(h["User-Agent"], th.USER_AGENT)
        self.assertNotIn("Mozilla", h["User-Agent"])
        self.assertNotIn("User-Agent", V.DOWNLOAD_HEADERS)

    def test_every_download_call_sends_the_skill_user_agent(self):
        calls = []

        def fake(url, **kw):
            calls.append(kw.get("headers") or {})
            if url == V.PACKAGE_API:
                return json.dumps(self.PKG).encode("utf-8")
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        w = []
        with mock.patch.object(dc, "fetch", side_effect=fake):
            self.assertFalse(V._download_year(2009, w))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(h.get("User-Agent") == th.USER_AGENT for h in calls))
        self.assertTrue(any("refused" in x and "bundled ratings index" in x for x in w), w)

    def test_refusal_falls_back_to_the_bundled_index_with_a_warning(self):
        years = V._search()["years"]
        self.assertTrue(years)
        want = years[-1] + 1
        if want > dt.date.today().year + 1:
            self.skipTest("bundled index already covers next model year")

        def refuse(url, **kw):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        w = []
        with mock.patch.object(dc, "fetch", side_effect=refuse):
            r = V.lookup("%d Toyota Corolla" % want, w)
        self.assertIsNotNone(r)                       # nearest year from the bundled index
        self.assertLessEqual(abs(r["year"] - want), 2)
        msg = [x for x in w if "refused" in x]
        self.assertTrue(msg, w)
        self.assertIn("HTTP 403", msg[0])
        self.assertIn("using the bundled ratings index (model years %d to %d)" % (years[0], years[-1]), msg[0])
        for x in w:
            self.assertNotIn("\u2014", x)
            self.assertNotIn("\u2013", x)

    def test_unreachable_is_a_warning_too(self):
        w = []
        with mock.patch.object(dc, "fetch", side_effect=urllib.error.URLError("offline")):
            self.assertFalse(V._download_year(2009, w))
        self.assertTrue(any("could not reach" in x and "offline" in x and "bundled ratings index" in x for x in w), w)

    def test_nrcan_hosts_on_the_driving_allow_list(self):
        for h in V.DOWNLOAD_HOSTS:
            self.assertIn(h, dp.DRIVE_HOSTS)
            self.assertIn(h, dp.drive_hosts())
        self.assertEqual(urllib.parse.urlsplit(V.PACKAGE_API).netloc, "open.canada.ca")


class TransitousAttributionTest(unittest.TestCase):
    CC = {"source": "Transitous (transitous.org, non-commercial, best effort)", "timezone_from": "zoneinfo",
          "attribution": "https://transitous.org/sources/",
          "transit_only": {"transitous": {"depart": "08:01", "arrive": "09:10", "legs": ["GO 21"]}, "verdict": "agrees"},
          "hubs": [{"hub": "Milton GO", "ours": {"board": "08:20", "arrive": "09:05"},
                    "transitous": None, "verdict": "no itinerary found"}]}

    def test_section_carries_the_link(self):
        lines = th.second_opinion_lines(self.CC)
        self.assertTrue(lines[0].startswith("SECOND OPINION  Transitous"))
        self.assertIn("https://transitous.org/sources/", lines[1])
        self.assertIn("  transit only from the origin: 08:01-09:10 GO 21  [agrees]", lines)
        self.assertEqual(lines[-1], "")
        self.assertEqual(th.TRANSITOUS_SOURCES_URL, "https://transitous.org/sources/")

    def test_link_shows_even_for_an_older_result_without_the_key(self):
        cc = dict(self.CC)
        cc.pop("attribution")
        self.assertIn("https://transitous.org/sources/", th.second_opinion_lines(cc)[1])

    def test_no_crosscheck_no_section(self):
        self.assertEqual(th.second_opinion_lines(None), [])
        self.assertEqual(th.second_opinion_lines({}), [])


class DriveGeocodeBlockedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": self.tmp, "TRIP_PLANNER_CACHE": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dns_down(self, *a, **k):
        raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

    def assert_drive_message(self, msg):
        self.assertTrue(msg.startswith("NETWORK BLOCKED"), msg)
        for part in ("nominatim.openstreetmap.org", "photon.komoot.io", "'lat,lon'", "curated parking",
                     "references/driving.md", "valhalla1.openstreetmap.de", "open.canada.ca", "not an address problem"):
            self.assertIn(part, msg)
        self.assertNotIn("timetables", msg)
        self.assertNotIn("\u2014", msg)
        self.assertNotIn("\u2013", msg)

    def test_plan_with_an_address_gets_the_driving_message(self):
        args = dp.build_parser().parse_args(["--origin", "Milton Community Park, Milton ON", "--dest", "Grand Bend Beach",
                                             "--depart", "2026-10-17 09:00", "--stay-hours", "4", "--no-saved-car"])
        with mock.patch.object(th, "http_get", side_effect=self.dns_down), \
                mock.patch.object(dc, "fetch", side_effect=self.dns_down):
            with self.assertRaises(SystemExit) as ctx:
                dp.plan(args)
        self.assert_drive_message(str(ctx.exception.code))

    def test_lat_lon_needs_no_geocoder(self):
        with mock.patch.object(th, "http_get", side_effect=AssertionError("network used")):
            lat, lon, _ = dp.resolve_drive_place(PARK, "origin", [], True)
        self.assertAlmostEqual(lat, 43.498757, places=5)
        self.assertAlmostEqual(lon, -79.855987, places=5)

    def test_other_exits_pass_through(self):
        with mock.patch.object(th, "resolve_place", side_effect=SystemExit("could not find origin 'x'")):
            with self.assertRaises(SystemExit) as ctx:
                dp.resolve_drive_place("x", "origin", [], True)
        self.assertEqual(ctx.exception.code, "could not find origin 'x'")



class USDollarPrices(unittest.TestCase):
    """A price a US station lists is in US dollars: shown as US$ and converted to CAD for the cost."""

    def test_us_listed_price_is_converted(self):
        import ev_charging as ev
        car = ev.car_profile("ioniq5")
        st = {"name": "EA Buffalo", "network_code": "Electrify America", "pricing": "$0.48 per kWh", "country": "US"}
        rate, _ = ev.usd_cad()
        cost, txt, src = ev.stop_price(st, car, 20.0, 20.0, 150.0)
        self.assertEqual(src, "station")
        self.assertIn("US$0.48", txt)
        self.assertIn("in CAD at", txt)
        self.assertAlmostEqual(cost, round(20.0 * 0.48 * rate, 2), places=2)

    def test_canadian_listed_price_unchanged(self):
        import ev_charging as ev
        car = ev.car_profile("ioniq5")
        st = {"name": "Jule King City", "network_code": "JULE", "pricing": "$0.48 per kWh", "country": "CA"}
        cost, txt, _ = ev.stop_price(st, car, 20.0, 20.0, 150.0)
        self.assertNotIn("US$", txt)
        self.assertAlmostEqual(cost, 9.60, places=2)

    def test_norm_nlr_keeps_country(self):
        import ev_charging as ev
        r = {"id": 1, "station_name": "x", "latitude": 42.9, "longitude": -78.8, "country": "US",
             "ev_charging_units": [{"charging_level": "dc_fast", "connectors": {"J1772COMBO": {"power_kw": 150, "port_count": 2}}}]}
        self.assertEqual(ev.norm_nlr(r)["country"], "US")


class BorderCountry(unittest.TestCase):
    """Parking and destination chargers stay in the destination's country (Goat Island is not walkable from
    Table Rock)."""

    def test_overpass_query_limited_to_country(self):
        import parking as pk
        seen = []
        with mock.patch.object(pk, "_overpass", side_effect=lambda q, w, h: seen.append(q) or {"elements": []}):
            pk.osm_lots(43.0791, -79.0784, dt.datetime(2026, 10, 17, 20, 50), 390, set(), 1250, None, [], country="CA")
            pk.osm_lots(43.0791, -79.0784, dt.datetime(2026, 10, 17, 20, 50), 390, set(), 1250, None, [])
        self.assertIn('area["ISO3166-1"="CA"][admin_level=2]->.c', seen[0])
        self.assertIn("(area.c)", seen[0])
        self.assertNotIn("area", seen[1])

    def test_find_parking_passes_country_to_osm_only(self):
        import parking as pk
        calls = {}

        def fake_osm(*a, **k):
            calls["osm"] = k.get("country")
            return [], []
        with mock.patch.object(pk, "osm_lots", side_effect=fake_osm),                 mock.patch.object(pk, "city_lots", return_value=([], [])),                 mock.patch.object(pk, "toronto_lots", return_value=([], [])):
            pk.find_parking(43.0791, -79.0784, dt.datetime(2026, 10, 17, 20, 50), 390,
                            {"holidays": set(), "country": "CA"}, [])
        self.assertEqual(calls["osm"], "CA")

    def test_dest_chargers_drop_other_country(self):
        import ev_charging as ev
        car = ev.car_profile("ioniq5")
        mk = lambda name, cc: {"id": name, "name": name, "network_code": "ChargePoint Network", "lat": 43.08,
                               "lon": -79.07, "address": "", "units": {"J1772": (6.6, 2)}, "pricing": "",
                               "access_text": "", "dc_num": 0, "l2_num": 2, "status": "E", "source": "NLR", "country": cc}
        near = [mk("Goat Island NY", "US"), mk("Table Rock ON", "CA")]
        with mock.patch.object(ev, "stations_near", return_value=near):
            out = ev._dest_chargers((43.0791, -79.0784), car, [], {"dest_country": "CA"})
        names = [x["name"] for x in out or []]
        self.assertIn("Table Rock ON", names)
        self.assertNotIn("Goat Island NY", names)


class UnfitReason(unittest.TestCase):
    """A lot that does not fit says why: the no-parking window, not a derived 'max stay'."""

    def test_no_parking_window_named(self):
        import parking as pk
        rule = {"flat": 30.0, "max_stay_min": 1080,
                "no_parking": [{"days": list(range(7)), "hours": ["00:00", "06:00"], "all_days": True}]}
        lot = pk.make_lot("Lot A", 43.0764, -79.0800, "lot", rule, "x", "", "curated", (43.0791, -79.0784),
                          dt.datetime(2026, 10, 17, 20, 50), 390, set())
        self.assertFalse(lot["fits_stay"])
        self.assertEqual(lot["unfit_reason"], "no parking 00:00 to 06:00 during your stay")
        self.assertEqual(pk.no_parking_hit(rule, dt.datetime(2026, 10, 17, 10, 0), 240), None)

    def test_lot_line_uses_reason(self):
        import drive_plan as dp
        line = dp.lot_line(1, {"name": "Lot A", "cost_text": "$30.00 for 6 h 30", "walk_m": 418, "fits_stay": False,
                               "unfit_reason": "no parking 00:00 to 06:00 during your stay", "max_stay_min": 1080,
                               "confidence": "curated", "source": "x"})
        self.assertIn("no parking 00:00 to 06:00 during your stay", line)
        self.assertNotIn("max stay", line)


if __name__ == "__main__":
    unittest.main()
