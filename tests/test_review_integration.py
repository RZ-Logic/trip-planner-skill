"""drive_plan + place_reviews: rates from recent Google reviews (official Places API, mocked here) on the lot list.

Run: python -m unittest tests.test_review_integration -v
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
import sys
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
import test_drive_plan as tdp  # noqa: E402

FERRY = (43.6406, -79.3756)
KEY = "test-places-key-123"
CHURCH = (FERRY[0] + 0.003, FERRY[1])


def lot(name, cost, dlat, flagged=False, conf="stale", why="2019 price", kind="garage"):
    return {"name": name, "lat": FERRY[0] + dlat, "lon": FERRY[1], "walk_m": 200 + int(dlat * 1e5), "walk_min": 3,
            "search_min": 2, "cost": cost, "cost_text": ("$%.2f" % cost) if cost is not None else "price unknown",
            "free": False, "fits_stay": True, "max_stay_min": None, "confidence": conf, "kind": kind,
            "source": "Green P 2019 open data" if conf == "stale" else "OpenStreetMap", "notes": "",
            "address": "", "maybe_closed": flagged,
            "check": {"why": "may have closed" if flagged else why, "url": None,
                      "search": "%s parking rates" % name}}


def fake_parking():
    m = types.ModuleType("parking")

    def find_parking(lat, lon, arrive, stay_min, opts, warnings):
        options = [lot("Green P 2 Church Street (St. Lawrence Garage)", 15.0, 0.003),
                   lot("Public lot NE of the destination", None, 0.001, conf="osm", why="price unknown", kind="lot"),
                   lot("Green P 9 Gone Street", 5.0, 0.0005, flagged=True)]
        return {"options": options, "place_rules": [], "reservations": [], "sources": ["Green P 2019 open data"],
                "radius_m": 1500, "check": pk.check_list(options),
                "caveat": "Posted signs override this data: time limits, rush-hour no-stopping, permit zones."}
    m.find_parking = find_parking
    m.check_list = pk.check_list
    return m


def review(text, when, author, rating=4, lang="en", orig=None):
    rv = {"name": "places/x/reviews/%s" % author, "rating": rating, "publishTime": when,
          "text": {"text": text, "languageCode": lang},
          "authorAttribution": {"displayName": author, "uri": "https://www.google.com/maps/contrib/1"},
          "googleMapsUri": "https://www.google.com/maps/reviews/data=%s" % author}
    if orig:
        rv["originalText"] = orig
    return rv


PLACES = {"places": [
    {"displayName": {"text": "Green P St. Lawrence Garage"}, "formattedAddress": "2 Church St, Toronto, ON",
     "location": {"latitude": CHURCH[0], "longitude": CHURCH[1] - 0.0002},
     "googleMapsUri": "https://maps.google.com/?cid=111", "types": ["parking_garage", "parking"],
     "reviews": [review("Weekend daily max is $9, great value for downtown.", "2026-08-16T14:00:00Z", "Jane D."),
                 review("Paid $30 here in 2022 for a game.", "2023-01-02T10:00:00Z", "Old Timer")]},
    {"displayName": {"text": "Impark – 1 Yonge St"}, "formattedAddress": "1 Yonge St, Toronto, ON",
     "location": {"latitude": 43.6421, "longitude": -79.3745},
     "googleMapsUri": "https://maps.google.com/?cid=222", "types": ["parking_lot", "parking"],
     "reviews": [review("Paid $25 flat for the day on a Sunday, attendant was friendly.", "2026-09-20T12:00:00Z",
                        "Sam K.")]},
    {"displayName": {"text": "Harbour lot"}, "formattedAddress": "10 Bay St, Toronto, ON",
     "location": {"latitude": 43.6398, "longitude": -79.3770},
     "googleMapsUri": "https://maps.google.com/?cid=333", "types": ["parking"],
     "reviews": [review("Clean and well lit.", "2026-07-01T12:00:00Z", "Pat")]}]}


class Recorder:
    def __init__(self, reply=None, error=None):
        self.calls, self.reply, self.error = [], reply, error

    def __call__(self, url, data=None, headers=None, **kw):
        if "places.googleapis.com" in url:
            self.calls.append({"url": url, "data": data, "headers": dict(headers or {}), "kw": kw})
            if self.error is not None:
                raise self.error
            return json.dumps(self.reply).encode("utf-8")
        return tdp.fetch_stub(url, **kw)


def run_plan(key, rec):
    m = fake_parking()
    with tdp.Env(), tdp.at_now(tdp.NOW), mock.patch.dict(sys.modules, {"parking": m}), \
            mock.patch.object(dc, "fetch", side_effect=rec), \
            mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": key}):
        res = dp.plan(tdp.args_for(*tdp.BASE, "--dest", "%f,%f" % FERRY, "--depart", "2026-10-11 10:00",
                                   "--stay-hours", "5", "--round-trip", "--park-and-ride", "off"))
        text = dp.render(res)
        data = dp.to_json(res)
    return res, text, data


class WithKey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = Recorder(PLACES)
        cls.res, cls.text, cls.data = run_plan(KEY, cls.rec)

    def test_one_official_call_key_in_header_only(self):
        self.assertEqual(len(self.rec.calls), 1)
        c = self.rec.calls[0]
        self.assertEqual(c["url"], pr.ENDPOINT)
        self.assertEqual(c["headers"]["X-Goog-Api-Key"], KEY)
        self.assertEqual(c["kw"].get("max_age_h"), 0)                  # never cached on disk
        body = json.loads(c["data"])
        self.assertEqual(body["locationRestriction"]["circle"]["radius"], 1500.0)   # the parking radius
        self.assertAlmostEqual(body["locationRestriction"]["circle"]["center"]["latitude"], FERRY[0])
        self.assertNotIn(KEY, self.text)
        self.assertNotIn(KEY, self.data)

    def test_target_unchanged_and_review_lot_after_priced(self):
        names = [o["name"] for o in self.res["parking"]["options"]]
        self.assertEqual(names[0], "Green P 2 Church Street (St. Lawrence Garage)")
        self.assertEqual(names[1], "Impark - 1 Yonge St")              # after the priced lot, dash cleaned
        self.assertEqual(names[-1], "Green P 9 Gone Street")
        self.assertEqual(self.res["arrival"]["lot"], names[0])
        self.assertEqual(self.res["links"]["target_name"], names[0])

    def test_matched_lot_sub_line_with_author_and_link(self):
        church = self.res["parking"]["options"][0]
        self.assertEqual(church["review_url"], "https://maps.google.com/?cid=111")
        self.assertEqual([m["amount"] for m in church["review_mentions"]], [9.0])   # the 2023 review is dropped
        self.assertIn("Google reviews of Green P St. Lawrence Garage mention $9", self.text)
        # each rate carries its author, month and the review's own Google Maps link
        self.assertIn("$9 weekends (Aug 2026, Jane D., https://www.google.com/maps/reviews/data=Jane D.), unverified",
                      self.text)
        lines = self.text.splitlines()
        i = next(n for n, x in enumerate(lines) if x.strip().startswith("1. Green P 2 Church"))
        self.assertTrue(lines[i + 1].strip().startswith("Google reviews of Green P St. Lawrence Garage mention $9"))
        m = church["review_mentions"][0]
        self.assertEqual((m["place"], m["maps_url"]), ("Green P St. Lawrence Garage", "https://maps.google.com/?cid=111"))
        self.assertNotIn("Old Timer", self.text)

    def test_review_only_lot_line(self):
        lines = self.text.splitlines()
        i = next(n for n, x in enumerate(lines) if x.strip().startswith("2. Impark - 1 Yonge St"))
        self.assertIn("price unknown", lines[i])
        self.assertIn("Google reviews mention $25", lines[i + 1])
        self.assertIn("Sam K.", lines[i + 1])
        self.assertIn("https://www.google.com/maps/reviews/data=Sam K.", lines[i + 1])
        self.assertNotIn("Harbour lot", self.text)                     # no rate in its reviews

    def test_check_hints_say_reviews(self):
        chk = self.res["parking"]["check"]
        self.assertEqual(chk[0]["n"], 1)
        self.assertEqual(chk[0]["why"], "2019 price; Google reviews mention a rate (see its review line)")
        self.assertNotIn("$9", chk[0]["why"])                          # no rate quoted without its author
        self.assertEqual(chk[1]["why"], "price from Google reviews only")
        self.assertEqual(chk[1]["url"], "https://maps.google.com/?cid=222")
        self.assertNotIn(dp.REVIEW_HINT, self.text)                    # the generic hint only without a key

    def test_sources_attribution_and_json(self):
        src = next(x for x in self.res["sources"] if x.startswith("parking review rates"))
        self.assertIn("Google Maps (Places API)", src)
        self.assertIn(pr.ATTRIBUTION.rstrip("."), src)
        self.assertEqual(self.text.count(pr.ATTRIBUTION.rstrip(".")), 1)
        data = json.loads(self.data)["parking"]
        rv = data["reviews"]
        self.assertEqual(rv["source"], pr.SOURCE)
        self.assertEqual(rv["with_rates"], 2)
        self.assertEqual(rv["matched"], ["Green P 2 Church Street (St. Lawrence Garage)"])
        self.assertEqual(rv["added"], ["Impark - 1 Yonge St"])
        self.assertEqual(len(rv["places"]), 3)
        extra = data["options"][1]
        self.assertEqual((extra["confidence"], extra["cost"], extra["unverified"]), ("review", None, True))
        self.assertFalse([k for k in extra if k.startswith("_")])
        self.assertTrue(data["options"][0]["review_text"].endswith("unverified"))

    def test_no_dashes(self):
        for ch in ("–", "—"):
            self.assertNotIn(ch, self.text)


class WithoutKey(unittest.TestCase):
    def test_silent_no_call_hint_only(self):
        rec = Recorder(PLACES)
        res, text, data = run_plan("", rec)
        self.assertEqual(rec.calls, [])
        self.assertIsNone(res["parking"]["reviews"])
        self.assertNotIn("Google reviews mention", text)
        self.assertNotIn("parking review rates", text)
        self.assertIn("check before you go", text)
        self.assertIn("; " + dp.REVIEW_HINT, text)
        self.assertEqual([o["name"] for o in res["parking"]["options"]][:2],
                         ["Green P 2 Church Street (St. Lawrence Garage)", "Public lot NE of the destination"])

    def test_http_error_warns_and_plans(self):
        err = urllib.error.HTTPError(pr.ENDPOINT, 403, "Forbidden", {}, None)
        rec = Recorder(error=err)
        res, text, _ = run_plan(KEY, rec)
        self.assertEqual(len(rec.calls), 1)
        self.assertTrue(any(w.startswith("Google Places reviews skipped: HTTP 403") for w in res["warnings"]))
        self.assertIsNone(res["parking"]["reviews"])
        self.assertIn(dp.REVIEW_HINT, text)
        self.assertNotIn(KEY, text)


class AttachUnit(unittest.TestCase):
    def test_review_lots_never_ahead_of_the_arrival_lot(self):
        unknown = lot("Unknown lot", None, 0.001, conf="osm", why="price unknown", kind="lot")
        parking = {"options": [unknown], "radius_m": 800, "check": []}
        got = {"places": [], "source": pr.SOURCE, "attribution": pr.ATTRIBUTION}
        extra = {"name": "Review lot", "lat": 1.0, "lon": 1.0, "walk_m": 900, "cost": None, "confidence": "review",
                 "review_mentions": [{"amount": 12.0, "unit": "day", "date": "2026-09", "author": "A"}],
                 "review_url": "https://maps.google.com/?cid=9", "_dist": 1.0}
        fake = types.SimpleNamespace(review_prices=lambda *a, **k: got, match_lots=lambda *a, **k: [extra],
                                     mention_text=pr.mention_text)
        with mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": KEY}), \
                mock.patch.object(dp, "_mod", side_effect=lambda n: fake if n == "place_reviews" else pk):
            out = dp.attach_reviews(parking, FERRY, unknown, [])
        self.assertEqual([o["name"] for o in out["options"]], ["Unknown lot", "Review lot"])
        self.assertTrue(out["options"][1]["beyond_walk"])
        self.assertEqual(out["options"][1]["review_text"],
                         "Google reviews mention $12 per day (Sep 2026, A, https://maps.google.com/?cid=9), unverified")

    def test_no_key_never_imports_or_calls(self):
        parking = {"options": [], "radius_m": 800}
        with mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": ""}), mock.patch.object(dp, "_mod") as m:
            out = dp.attach_reviews(parking, FERRY, None, [])
        m.assert_not_called()
        self.assertIsNone(out["reviews"])
        self.assertIsNone(dp.review_source(out))


if __name__ == "__main__":
    unittest.main()
