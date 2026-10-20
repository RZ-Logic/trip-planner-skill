"""Offline tests for the v0.9.8 parking fixes: Green P day-and-time ranges and rolling maximums, checked
replies from Overpass, ArcGIS, Green P and the TPA feed, the rate-text parser (24 h windows, free days, clock
times before a price, OpenStreetMap day forms, "max $X", block prices), the Overpass retry budget, overnight
opening hours, the rank quotas and the Overpass disk-cache fallback.

Every HTTP call is mocked: either drive_common.fetch, or the opener under it with a temporary cache folder.

Run: python -m unittest tests.test_v098_parking -v
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
import parking as pk  # noqa: E402

MON = dt.datetime(2026, 10, 19, 9, 0)
WED = dt.datetime(2026, 10, 14, 9, 0)
SAT = dt.datetime(2026, 10, 17, 10, 0)
SUN = dt.datetime(2026, 10, 18, 10, 0)
DEST = (43.6453, -79.3806)


def carpark(cid, half, periods):
    return {"id": cid, "address": "%s Test Street" % cid, "lat": "43.6455", "lng": "-79.3810", "rate_half_hour": half,
            "carpark_type": "garage", "is_ttc": False, "capacity": "100", "max_height": "", "slug": "",
            "rate_details": {"periods": periods, "addenda": []}}


# the 2019 rows of carparks 52 (40 York St), 289 (373 Front St E) and 133 (20 Prescott Ave)
CP52 = carpark("52", "4.00", [
    {"title": "Monday - Friday", "rates": [{"when": "Day Maximum (7am - 7pm)", "rate": "$25.00"},
                                           {"when": "Night Maximum (5pm - 7am)", "rate": "$9.00"}], "notes": []},
    {"title": "Saturday - Sunday", "rates": [{"when": "Maximum per 12 hour period (Sat 7am - Mon 7am)", "rate": "$9.00"},
                                             {"when": "PLEASE BE ADVISED", "rate": ""}], "notes": []}])
CP289 = carpark("289", "2.00", [{"title": "Monday - Sunday & Holidays ",
                                 "rates": [{"when": "Maximum (Any 4 hour Period)", "rate": "$12.00"}], "notes": []}])
CP133 = carpark("133", "1.00", [{"title": "Monday - Sunday",
                                 "rates": [{"when": "Maximum (Any 24 hr Period)", "rate": "$5.00"}], "notes": []}])


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TempCache(object):
    """A temporary TRIP_PLANNER_CACHE and a fake HTTP opener under the real drive_common.fetch."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def open(self, req, timeout=None):
        self.calls.append(req.full_url)
        for key, body in self.replies:
            if key in req.full_url:
                return Reply(json.dumps(body).encode("utf-8"))
        raise urllib.error.URLError("offline test")

    def __enter__(self):
        self.tmp = tempfile.mkdtemp()
        self.patches = [mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": self.tmp}),
                        mock.patch.object(dc._OPENER, "open", side_effect=self.open),
                        mock.patch.object(pk.time, "sleep")]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()
        dc.stale_notes()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cached(self):
        d = os.path.join(self.tmp, "driving")
        return [f for f in os.listdir(d) if not f.endswith(".tmp")] if os.path.isdir(d) else []


def overpass_calls(calls):
    return len([c for c in calls if "overpass" in c])


# --------------------------------------------------------------------------- 1, 2: Green P

class GreenPDayRanges(unittest.TestCase):
    def test_span_reads_days_and_times_as_one_window(self):
        self.assertEqual(pk._gp_span("Maximum per 12 hour period (Sat 7am - Mon 7am)"), [([5, 6], ["07:00", "07:00"])])
        self.assertEqual(pk._gp_span("Fri 6pm - Sat 2am"), [([4], ["18:00", "02:00"])])
        self.assertIsNone(pk._gp_span("Day Maximum (7am - 7pm)"))
        self.assertIsNone(pk._gp_span("Saturday - Sunday & Holidays"))

    def test_carpark_52_weekday_and_weekend_maximums(self):
        rule = pk._greenp_rule(CP52)
        self.assertEqual(pk.price_rule(rule, MON, 9 * 60, set())[0], 25.0)    # was $9: Monday read as a weekend day
        self.assertEqual(pk.price_rule(rule, WED, 9 * 60, set())[0], 25.0)
        self.assertEqual(pk.price_rule(rule, SAT, 5 * 60, set())[0], 9.0)
        self.assertEqual(pk.price_rule(rule, SUN, 5 * 60, set())[0], 9.0)     # was $25
        # Sunday 20:00 to Monday 10:00: the weekend window to Mon 07:00 ($9), then 3 h of the weekday day rate
        self.assertEqual(pk.price_rule(rule, SUN.replace(hour=20), 14 * 60, set())[0], 33.0)
        self.assertIn("max $9.00 per 12 h", pk.describe_rule(rule))

    def test_any_n_hour_maximum_is_rolling(self):
        rule = pk._greenp_rule(CP289)
        self.assertEqual(rule["periods"][0]["roll_min"], 240)
        wed8 = WED.replace(hour=8)
        self.assertEqual([pk.price_rule(rule, wed8, h * 60, set())[0] for h in (3, 9, 14)], [12.0, 28.0, 44.0])
        self.assertIn("max $12.00 per 4 h", pk.describe_rule(rule))

    def test_rolling_24_hours_does_not_double_overnight(self):
        rule = pk._greenp_rule(CP133)
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=20), 12 * 60, set())[0], 5.0)   # was $10
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=20), 30 * 60, set())[0], 10.0)  # two 24 h blocks
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=8), 120, set())[0], 4.0)

    def test_rolling_rule_in_rate_schema(self):
        rule = {"hourly": 3.0, "per_min": 30, "periods": [{"days": list(range(7)), "hours": ["00:00", "24:00"],
                                                           "max": 9.0, "roll_min": 480}]}
        self.assertEqual(pk.price_rule(rule, WED.replace(hour=20), 10 * 60, set())[0], 15.0)   # $9 + 2 h at $3

    def test_rolling_blocks_count_from_entry_with_free_hours_inside(self):
        # gem-G3, kept as is by the owner on 2026-10-20: the blocks run on the clock from the first charged minute,
        # free overnight hours included, as a garage clock bills (errs high against a daily reset)
        rule = {"hourly": 4.0, "per_min": 60, "periods": [
            {"days": [5, 6], "hours": ["08:00", "18:00"], "max": 9.0, "roll_min": 720},
            {"days": [5, 6], "hours": ["18:00", "08:00"], "free": True}]}
        sat14 = dt.datetime(2026, 10, 17, 14, 0)
        # Sat 14-18 $9, Sun 08-14 $9 (block Sun 02:00-14:00), Sun 14-16 $8; a reset each day would give $18
        self.assertEqual(pk.price_rule(rule, sat14, 26 * 60, set())[0], 26.0)
        self.assertEqual(pk.price_rule(rule, sat14.replace(hour=8), 34 * 60, set())[0], 18.0)


# --------------------------------------------------------------------------- 3, 13: checked replies and the cache

def osm_search(warnings):
    return pk.osm_lots(DEST[0], DEST[1], MON, 120, set(), 900, None, warnings)


class CheckedReplies(unittest.TestCase):
    REMARK = {"version": 0.6, "elements": [],
              "remark": "runtime error: Query timed out in \"query\" at line 1 after 26 seconds."}
    GOOD = {"elements": [{"type": "way", "id": 1, "center": {"lat": 43.6455, "lon": -79.3810},
                          "tags": {"amenity": "parking", "fee": "yes", "charge": "$3/hr", "name": "Good Lot"}}]}

    def test_overpass_check(self):
        self.assertIsNone(pk._overpass_check(json.dumps(self.GOOD).encode()))
        self.assertIn("timed out", pk._overpass_check(json.dumps(self.REMARK).encode()))
        self.assertIn("error", pk._overpass_check(json.dumps({"elements": [], "remark": "runtime error: out of memory"}).encode()))
        self.assertEqual(pk._overpass_check(b"[]"), "bad reply")
        self.assertIsNone(pk._arcgis_check(b'{"features": []}'))
        self.assertEqual(pk._arcgis_check(b'{"error": {"code": 400, "message": "Invalid query"}}'), "Invalid query")
        self.assertIsNone(pk._ckan_check(b'{"success": true, "result": {"records": []}}'))
        self.assertIn("Not found", pk._ckan_check(b'{"success": false, "error": {"message": "Not found"}}'))
        self.assertIsNone(pk._greenp_check(b'{"carparks": []}'))
        self.assertTrue(pk._greenp_check(b'{"error": "x"}'))

    def test_overpass_remark_is_a_warned_failure_and_not_cached(self):
        with TempCache([("overpass", self.REMARK)]) as net:
            w = []
            lots, srcs = osm_search(w)
            self.assertEqual((lots, srcs), ([], []))                      # no source listed for an error reply
            self.assertTrue(any(x.startswith(pk.OVERPASS_TIMEOUT_TEXT) for x in w), w)
            self.assertEqual(net.cached(), [])
            osm_search([])                                                # a new run asks the network again
            self.assertEqual(overpass_calls(net.calls), 2)

    def test_overpass_runtime_error_without_timeout(self):
        bad = {"elements": [], "remark": "runtime error: Query run out of memory using about 2048 MB of RAM."}
        with TempCache([("overpass", bad)]):
            w = []
            self.assertEqual(osm_search(w), ([], []))
            self.assertTrue(any("out of memory" in x and "results may miss free lots" in x for x in w), w)

    def test_overpass_good_reply_is_cached(self):
        with TempCache([("overpass", self.GOOD)]) as net:
            lots, srcs = osm_search([])
            self.assertEqual([x["name"] for x in lots], ["Good Lot"])
            self.assertTrue(srcs and srcs[0].startswith("OpenStreetMap via Overpass"))
            osm_search([])
            self.assertEqual(overpass_calls(net.calls), 1)

    def test_arcgis_error_reply_warned_and_not_cached(self):
        layer = pk.cfg()["city_layers"][0]
        err = {"error": {"code": 400, "message": "Invalid query"}}
        with TempCache([("query?", err)]) as net:
            for _ in range(2):
                w = []
                self.assertEqual(pk._arcgis_query(layer, 43.33, -79.80, 500, w), [])
                self.assertEqual(w, ["%s parking layer returned an error (Invalid query)" % layer["city"]])
            self.assertEqual(len(net.calls), 2)
            self.assertEqual(net.cached(), [])

    def test_cached_reply(self):
        q = "[out:json];node(1);out;"
        url = pk.cfg()["overpass"]["url"]
        with TempCache([]):
            self.assertIsNone(pk._cached_reply(url, q))                   # nothing cached
            path = dc._cache_path(url, urllib.parse.urlencode({"data": q}).encode("utf-8"))
            with open(path, "wb") as f:
                f.write(json.dumps(self.GOOD).encode("utf-8"))
            self.assertEqual(pk._cached_reply(url, q), self.GOOD)
            with open(path, "wb") as f:                                    # an error reply cached by an old version
                f.write(json.dumps(self.REMARK).encode("utf-8"))
            self.assertIsNone(pk._cached_reply(url, q))
            with open(path, "wb") as f:
                f.write(b"not json")
            self.assertIsNone(pk._cached_reply(url, q))
        with mock.patch.object(dc, "_cache_path", return_value=None):    # cache folder unusable
            self.assertIsNone(pk._cached_reply(url, q))

    def test_busy_run_uses_the_disk_cache(self):
        q = '[out:json][timeout:25];nwr["amenity"="parking"](around:900,%.5f,%.5f);out center tags;' % DEST
        url = pk.cfg()["overpass"]["url"]
        with TempCache([]) as net:
            path = dc._cache_path(url, urllib.parse.urlencode({"data": q}).encode("utf-8"))
            with open(path, "wb") as f:
                f.write(json.dumps(self.GOOD).encode("utf-8"))
            os.utime(path, (1, 1))                                         # long expired
            w = [pk.OVERPASS_TIMEOUT_TEXT + "); results may miss free lots"]
            lots, _ = osm_search(w)
            self.assertEqual([x["name"] for x in lots], ["Good Lot"])
            self.assertEqual(net.calls, [])


# --------------------------------------------------------------------------- 4 to 9: rate text

class RateText(unittest.TestCase):
    def test_24_hour_window_without_days(self):          # 4
        for text, rate in (("$2/hr 00:00-24:00", 2.0), ("$2.50/hr, 0:00-24:00", 2.5), ("$3/h 00:00-24:00, 3 hr limit", 3.0)):
            r, _ = pk.parse_rate_text(text)
            self.assertTrue(pk.describe_rule(r).startswith("%s/h, daily" % dc.money(rate)), text)
            self.assertEqual(pk.price_rule(r, MON, 120)[0], rate * 2, text)

    def test_one_bad_lot_never_drops_the_source(self):   # 4
        ov = {"elements": [
            {"type": "way", "id": 1, "center": {"lat": 43.6455, "lon": -79.3810},
             "tags": {"amenity": "parking", "fee": "yes", "charge": "$2/hr 00:00-24:00", "name": "Day Lot"}},
            {"type": "way", "id": 2, "center": {"lat": 43.6450, "lon": -79.3800},
             "tags": {"amenity": "parking", "fee": "yes", "charge": "$3/hr", "name": "Bad Lot"}},
            {"type": "way", "id": 3, "center": {"lat": 43.6458, "lon": -79.3802},
             "tags": {"amenity": "parking", "fee": "no", "name": "Free Lot"}}]}
        real = pk.osm_charge_rule

        def charge_rule(tags):
            if tags.get("name") == "Bad Lot":
                raise KeyError("paid_days")
            return real(tags)
        w = []
        with mock.patch.object(pk, "_overpass", return_value=ov), mock.patch.object(pk, "osm_charge_rule", charge_rule):
            lots, srcs = osm_search(w)
        self.assertEqual(sorted(x["name"] for x in lots), ["Day Lot", "Free Lot"])
        self.assertEqual(lots[0]["cost"], 4.0)
        self.assertTrue(srcs)
        self.assertIn("1 OpenStreetMap parking lot left out: unreadable data (KeyError)", w)

    def test_free_days(self):                            # 5
        for text in ("$2/hr, free Sat-Sun", "$2/hr Mon-Fri, free Sat-Sun", "$2/hr, Sat-Sun free",
                     "$2/hr, free on weekends"):
            r, _ = pk.parse_rate_text(text)
            self.assertEqual(r.get("paid_days"), [0, 1, 2, 3, 4], text)
            self.assertEqual(pk.price_rule(r, MON, 120)[0], 4.0, text)
            self.assertEqual(pk.price_rule(r, SUN, 120)[0], 0.0, text)
        r, _ = pk.parse_rate_text("$2/hr, Sun and holidays free")
        self.assertEqual((r["paid_days"], r.get("holidays_free")), ([0, 1, 2, 3, 4, 5], True))
        r, _ = pk.parse_rate_text("$2/hr, free after 6pm")     # a free window is still a window
        self.assertEqual(r["paid_hours"], ["00:00", "18:00"])

    def test_free_day_lot_ranks_after_a_cheaper_paid_one(self):   # 5
        lots = [pk.make_lot(name, DEST[0] + 0.001, DEST[1], "lot", pk.parse_rate_text(text)[0], "OpenStreetMap", "u",
                            "osm", DEST, MON.replace(hour=10), 120, set())
                for name, text in (("Weekend Free Lot", "$2/hr, free Sat-Sun"), ("Cheap Lot", "$1/hr"))]
        self.assertEqual([x["name"] for x in pk.rank(lots)], ["Cheap Lot", "Weekend Free Lot"])

    def test_clock_time_before_a_price(self):            # 6
        for text, want in (("Mon-Sat 9:00-18:00 $2/hr", (2.0, ["09:00", "18:00"])),
                           ("8:00-18:00 $2.50/hr", (2.5, ["08:00", "18:00"])),
                           ("Mon-Fri 08:00 - 18:00 $3.00/h", (3.0, ["08:00", "18:00"]))):
            r, _ = pk.parse_rate_text(text)
            self.assertNotIn("flat", r, text)
            self.assertEqual((r["hourly"], r["paid_hours"]), want, text)
            self.assertEqual(pk.price_rule(r, MON.replace(hour=10), 120)[0], want[0] * 2, text)
        self.assertEqual(pk.parse_rate_text("4 CAD/h")[0]["hourly"], 4.0)        # the suffix form still works
        self.assertEqual(pk.parse_rate_text("5$/h")[0]["hourly"], 5.0)

    def test_osm_day_forms(self):                        # 7
        r, _ = pk.parse_rate_text("Mo-Fr 08:00-18:00 $2/hr")
        self.assertEqual((r["paid_days"], r["paid_hours"], r["hourly"]), ([0, 1, 2, 3, 4], ["08:00", "18:00"], 2.0))
        r, _ = pk.parse_rate_text("Mo-Su 00:00-24:00 $2/hr")
        self.assertEqual((r["paid_days"], r["hourly"]), (list(range(7)), 2.0))
        r, _ = pk.parse_rate_text("Sa,Su 10:00-16:00 $3/hr")
        self.assertEqual(r["paid_days"], [5, 6])
        r, _ = pk.parse_rate_text("Mo,We,Fr 09:00-17:00 $1/hr")
        self.assertEqual(r["paid_days"], [0, 2, 4])
        # ordinary words are not days
        for text in ("We charge $2/hr", "To the lot: $2/hr", "The $2/hr rate", "Su casa $2/hr", "Fr. Smith lot $2/hr", "Mo money $2/hr", "Sam's lot $2/hr"):
            r, _ = pk.parse_rate_text(text)
            self.assertNotIn("paid_days", r, text)
            self.assertEqual(r["hourly"], 2.0, text)

    def test_max_before_the_amount(self):                # 8
        for text in ("$2/hr, max $10", "$2/hr; maximum $10", "$2/hr, $10 max", "$2/hr, daily max $10"):
            r, notes = pk.parse_rate_text(text)
            self.assertEqual((r.get("daily_max"), r.get("flat")), (10.0, None), text)
            self.assertNotIn("extra flat amount ignored", notes)
            self.assertEqual(pk.price_rule(r, MON.replace(hour=8), 600)[0], 10.0, text)

    def test_block_price(self):                          # 9
        for text in ("$7 for 3 hours", "$7 per 3 hours"):
            r, _ = pk.parse_rate_text(text)
            self.assertNotIn("daily_max", r)
            self.assertEqual([pk.price_rule(r, MON.replace(hour=8), h * 60)[0] for h in (2, 3, 4, 10)],
                             [7.0, 7.0, 14.0, 28.0], text)
            self.assertIn("$7.00 per 3 h", pk.describe_rule(r))
        r, _ = pk.parse_rate_text("$7 for 3 hours, $20 max")
        self.assertEqual(pk.price_rule(r, MON.replace(hour=8), 600)[0], 20.0)     # capped by the stated max
        r, _ = pk.parse_rate_text("2.00 CAD/30min, 8.00 CAD/24 hour max")         # "max" after it: still a cap
        self.assertEqual((r["hourly"], r["daily_max"]), (4.0, 8.0))


# --------------------------------------------------------------------------- 10: Overpass retry budget

class OverpassBudget(unittest.TestCase):
    def run_searches(self, fail, n=3):
        clock, calls, made = {"t": 1000.0}, [], []

        def fetch(url, data=None, timeout=None, **kw):
            calls.append(timeout)
            clock["t"] += timeout          # every attempt uses its whole timeout
            made.append(fail(url))
            raise made[-1]

        def sleep(s):
            clock["t"] += s
        w, took = [], []
        with mock.patch.object(dc, "fetch", side_effect=fetch), mock.patch.object(pk.time, "time", lambda: clock["t"]), \
                mock.patch.object(pk.time, "sleep", side_effect=sleep), mock.patch.object(pk, "_cached_reply", lambda u, q: None):
            for i in range(n):
                t0 = clock["t"]
                self.assertIsNone(pk._overpass("q%d" % i, w))
                took.append(clock["t"] - t0)
        for e in made:
            if hasattr(e, "close"):
                e.close()
        return w, took, calls

    def test_two_busy_replies_stay_within_the_budget(self):
        w, took, calls = self.run_searches(lambda u: urllib.error.HTTPError(u, 504, "Gateway Timeout", {}, io.BytesIO(b"")))
        self.assertLessEqual(took[0], pk.OVERPASS_MAX_WAIT_S)      # was 2 x 40 s + 30 s = 110 s
        self.assertEqual(len(calls), 2)                             # later searches in the run skip the network
        self.assertEqual(took[1:], [0, 0])
        self.assertTrue(w[0].startswith(pk.OVERPASS_BUSY_TEXT))

    def test_socket_timeout_counts_as_busy(self):
        w, took, calls = self.run_searches(lambda u: urllib.error.URLError(socket.timeout("timed out")))
        self.assertEqual(len(calls), 1)                             # was 3 of 3
        self.assertEqual(w, ["OpenStreetMap parking search failed (timed out); results may miss free lots"])
        w, took, calls = self.run_searches(lambda u: socket.timeout("timed out"))
        self.assertEqual(len(calls), 1)

    def test_fast_busy_reply_still_waits_30_s_once(self):
        w, err = [], urllib.error.HTTPError("u", 429, "busy", {}, io.BytesIO(b""))
        self.addCleanup(err.close)
        with mock.patch.object(dc, "fetch", side_effect=err), mock.patch.object(pk.time, "sleep") as sl:
            pk._overpass("q", w)
        sl.assert_called_once_with(30.0)


# --------------------------------------------------------------------------- 11: opening hours past midnight

class OpeningHours(unittest.TestCase):
    def test_open_for_stay(self):
        night = ("20:00", "06:00")
        self.assertTrue(pk.open_for_stay(night, MON.replace(hour=21), 120))
        self.assertTrue(pk.open_for_stay(night, MON.replace(hour=1), 120))        # in the window that began Sunday
        self.assertTrue(pk.open_for_stay(night, MON.replace(hour=20), 600))
        self.assertFalse(pk.open_for_stay(night, MON.replace(hour=12), 120))
        self.assertFalse(pk.open_for_stay(night, MON.replace(hour=5), 120))        # runs past 06:00
        day = ("06:00", "23:00")
        self.assertTrue(pk.open_for_stay(day, MON.replace(hour=10), 120))
        self.assertFalse(pk.open_for_stay(day, MON.replace(hour=22), 120))

    def test_osm_lot_reason(self):
        ov = {"elements": [{"type": "way", "id": 9, "center": {"lat": 43.6455, "lon": -79.3810},
                            "tags": {"amenity": "parking", "fee": "yes", "charge": "$3/hr", "name": "Night Lot",
                                     "opening_hours": "20:00-06:00", "maxstay": "12 hours"}}]}
        for hour, fits in ((21, True), (1, True), (12, False)):
            with mock.patch.object(pk, "_overpass", return_value=ov):
                lot = pk.osm_lots(DEST[0], DEST[1], MON.replace(hour=hour), 120, set(), 900, None, [])[0][0]
            self.assertEqual(lot["fits_stay"], fits, hour)
            self.assertEqual(lot["unfit_reason"], None if fits else "open 20:00 to 06:00 only", hour)


# --------------------------------------------------------------------------- 12: rank quotas

class RankQuotas(unittest.TestCase):
    def lot(self, name, kind, rule, dlat, conf="official", fits=True):
        x = pk.make_lot(name, DEST[0] + dlat, DEST[1], kind, rule, "City", "u", conf, DEST, MON, 120, set())
        x["fits_stay"] = fits
        return x

    def test_street_cap_does_not_spend_the_unknown_quota(self):
        lots = [self.lot("Paid street %d" % i, "street", {"hourly": 2.0}, 0.0003 * (i + 1)) for i in range(3)]
        lots += [self.lot("Unknown street %d" % i, "street", None, -0.0003 * (i + 1)) for i in range(2)]
        lots.append(self.lot("Big Garage", "garage", None, 0.004, conf="osm"))
        self.assertEqual([x["name"] for x in pk.rank(lots)], ["Paid street 0", "Paid street 1", "Paid street 2", "Big Garage"])

    def test_an_off_street_option_is_always_kept(self):
        lots = [self.lot("Paid street", "street", {"hourly": 2.0}, 0.0003)]
        lots += [self.lot("Unknown street %d" % i, "street", None, -0.0003 * (i + 1)) for i in range(2)]
        lots.append(self.lot("Far Garage", "garage", None, 0.006, conf="osm"))
        names = [x["name"] for x in pk.rank(lots)]
        self.assertEqual(names[-1], "Far Garage")
        self.assertEqual(len(names), 4)
        # a full list gives up its last street option for the garage
        full = [self.lot("Street %d" % i, "street", {"hourly": 1.0 + i}, 0.0003 * (i + 1)) for i in range(5)]
        full.append(self.lot("Garage", "garage", None, 0.006, conf="osm"))
        names = [x["name"] for x in pk.rank(full, dict(pk.cfg(), max_options=3, max_street=5))]
        self.assertEqual(names, ["Street 0", "Street 1", "Garage"])
        # only street options: nothing is added
        self.assertTrue(all(x["kind"] == "street" for x in pk.rank(full[:5])))


class ReviewFollowUps(unittest.TestCase):
    """The diff review's parking findings: an N-hour amount next to an hourly rate stays a maximum, free days
    with a clock window are free windows, and a block price reads as one."""

    def price(self, text, when, mins):
        rule, _ = pk.parse_rate_text(text)
        return pk.price_rule(rule, when, mins, set())[0]

    def test_n_hour_amount_with_an_hourly_rate_is_a_maximum(self):
        m8 = dt.datetime(2026, 10, 19, 8, 0)
        self.assertAlmostEqual(self.price("3.50 CAD/hour, 20 CAD/24 hours", m8, 600), 20.0)
        self.assertAlmostEqual(self.price("$2/hr, $15/24 hours", m8, 600), 15.0)
        self.assertAlmostEqual(self.price("$2/hr, $15/24 hours", m8, 1800), 30.0)
        self.assertAlmostEqual(self.price("$3.00/hr; $12 per 12 hours", m8, 600), 12.0)
        self.assertAlmostEqual(self.price("$4/hr, $18/12hrs", m8, 900), 30.0)     # 12 h capped at $18, then 3 h at $4

    def test_block_price_alone_is_still_a_block_price(self):
        self.assertAlmostEqual(self.price("$7 for 3 hours", MON, 600), 28.0)
        self.assertAlmostEqual(self.price("$6.00/12 hours", MON, 180), 6.0)
        rule, _ = pk.parse_rate_text("$7 for 3 hours")
        self.assertEqual(pk.describe_rule(rule), "$7.00 per 3 h")

    def test_free_days_with_a_window(self):
        self.assertAlmostEqual(self.price("$2/hr, free Sat-Sun 9am-5pm", dt.datetime(2026, 10, 19, 10, 0), 120), 4.0)
        self.assertAlmostEqual(self.price("$2/hr, free Sat-Sun 9am-5pm", SAT, 120), 0.0)
        self.assertAlmostEqual(self.price("$10 flat; free after 6pm and weekends", dt.datetime(2026, 10, 19, 10, 0), 120), 10.0)
        self.assertAlmostEqual(self.price("$10 flat; free after 6pm and weekends", SAT, 120), 0.0)
        self.assertAlmostEqual(self.price("$10 flat; free after 6pm and weekends", dt.datetime(2026, 10, 19, 19, 0), 60), 0.0)


if __name__ == "__main__":
    unittest.main()
