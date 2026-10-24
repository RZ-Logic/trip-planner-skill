"""Offline tests for place_reviews.py (Google Places API (New) review rates, optional key).

No live call: every reply is mocked through drive_common.fetch, shaped like the documented
places:searchNearby response. Run: python -m unittest discover -s tests -v
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
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import place_reviews as pr  # noqa: E402

KEY = "AIzaTESTKEY-0123456789abcdefghijklmno"
TODAY = dt.date(2026, 10, 10)
DEST = (43.6406, -79.3756)

# (review sentence, expected [(amount, unit)] or [] when nothing should be extracted)
CASES = [
    ("Paid $15 flat to park here.", [(15.0, "flat")]),
    ("$20 for the day, cheapest lot near the lake.", [(20.0, "day")]),
    ("Rates are $4/hr and the daily max is $22.", [(4.0, "hour"), (22.0, "day")]),
    ("It's $2.50 per half hour, weekend day maximum $9.", [(2.5, "half hour"), (9.0, "weekend")]),
    ("On event nights they charge $30, so plan ahead.", [(30.0, "event")]),
    ("Leafs game night parking was $40 flat.", [(40.0, "event")]),
    ("Parking is $20-25 depending on the day.", [(20.0, "day")]),
    ("2 hours for $8, not bad for downtown parking.", [(8.0, "flat")]),
    ("Paid $8 for 2 hours of parking.", [(8.0, "flat")]),
    ("Free after 6pm, otherwise $3 an hour.", [(3.0, "hour")]),
    ("Free after 6 and on Sundays.", []),
    ("Stationnement 15 $ pour la journée, correct.", [(15.0, "day")]),
    ("Stationnement à 3,50 $ de l'heure.", [(3.5, "hour")]),
    ("Got a $75 ticket even though I paid for parking.", []),
    ("My car was towed and it cost $85 to get it back from the lot.", []),
    ("Valet guy expected a $5 tip after parking.", []),
    ("Food at the stand was $12 but parking was $10 for the evening.", [(10.0, "evening")]),
    ("Monthly parking is $90 a month here.", []),
    ("The 407 toll was $18 but parking was only $12.", [(12.0, "unknown")]),
    ("Evening rate is $8 after 6pm.", [(8.0, "evening")]),
    ("Paid $12 to park, took a ticket at the gate and paid at the machine.", [(12.0, "unknown")]),
    ("Charged me $35 for a concert parking spot.", [(35.0, "event")]),
    ("Their rate went from $3 to $4 per hour this year.", [(4.0, "hour")]),
    ("Great lot, clean, well lit.", []),
    ("Was about $6 for an hour of parking.", [(6.0, "hour")]),
    ("C$18 daily max at this garage.", [(18.0, "day")]),
    ("Parked for 3 hrs and paid $13.50.", [(13.5, "flat")]),
    ("The rate is $1,200 for the year permit.", []),
    ("Ended up paying 20 dollars for parking during the festival.", [(20.0, "event")]),
    ("Max $25 for 24 hours.", [(25.0, "day")]),
    ("Parking was fine, $14 for the day.", [(14.0, "day")]),
    ("We got fined $50 for parking in the wrong spot.", []),
    ("Parking cost $120 for the Taylor Swift concert.", []),
    ("Burgers were $15 each, great place.", []),
]


def review(text, when="2026-08-14T13:22:05.123456Z", rating=4, lang="en", orig=None, author="Pat K."):
    r = {"name": "places/ChIJabc/reviews/xyz", "relativePublishTimeDescription": "2 months ago", "rating": rating,
         "text": {"text": text, "languageCode": lang},
         "originalText": {"text": orig if orig is not None else text, "languageCode": "fr" if orig else lang},
         "authorAttribution": {"displayName": author, "uri": "https://www.google.com/maps/contrib/123",
                               "photoUri": "https://lh3.googleusercontent.com/a/x"},
         "publishTime": when, "flagContentUri": "https://www.google.com/local/review/rap/report?x",
         "googleMapsUri": "https://www.google.com/maps/reviews/data=xyz"}
    return r


def place(name, lat, lon, reviews=None, types=("parking", "establishment", "point_of_interest"), addr=None):
    p = {"displayName": {"text": name, "languageCode": "en"},
         "formattedAddress": addr or "1 Test St, Toronto, ON M5E 1A1, Canada",
         "location": {"latitude": lat, "longitude": lon},
         "googleMapsUri": "https://maps.google.com/?cid=1234567890",
         "types": list(types)}
    if reviews is not None:
        p["reviews"] = reviews
    return p


def reply(*places):
    return json.dumps({"places": list(places)}).encode("utf-8")


class Extractor(unittest.TestCase):
    def test_cases(self):
        self.assertGreaterEqual(len(CASES), 25)
        for text, want in CASES:
            got = [(m["amount"], m["unit"]) for m in pr.extract_mentions(text, "2026-08", 4)]
            self.assertEqual(got, want, text)

    def test_muse_item_10_skip_rules(self):
        # Muse items 10.a to 10.e: a hotel price, add-on fees named other ways and older past-price phrasings
        # were read as parking rates; a "$15 parking fee" was dropped as a fee on top
        cases = [
            ("Parking was easy, then I paid $90 for the hotel.", []),
            ("Parking was fine but the hotel room was $90.", []),
            ("Hotel parking was $25 a day.", [(25.0, "day")]),
            ("Parking is $12, plus a $3 credit card processing fee.", [(12.0, "unknown")]),
            ("Parking is $12 and $3 extra for the credit card.", [(12.0, "unknown")]),
            ("Parking $12 plus a $4 service charge.", [(12.0, "unknown")]),
            ("Parking: back in 2023 it was $12, now $15 a day.", [(15.0, "day")]),
            ("I recall paying $12 back then for parking, now $15 a day.", [(15.0, "day")]),
            ("Parking was $10 two years ago, now $14 a day.", [(14.0, "day")]),
            ("Paid the $15 parking fee.", [(15.0, "unknown")]),
            ("The $20 daily parking fee is steep.", [(20.0, "day")]),
            ("Parking was $10 plus a $2 booking fee.", [(10.0, "unknown")]),
        ]
        for text, want in cases:
            got = [(m["amount"], m["unit"]) for m in pr.extract_mentions(text, "2026-08", 4)]
            self.assertEqual(got, want, text)

    def test_range_and_duration_fields(self):
        m = pr.extract_mentions("Parking is $20-25 depending on the day.", "2026-08")[0]
        self.assertEqual(m["max"], 25.0)
        self.assertEqual(pr.mention_text(m), "$20-25 per day")
        m = pr.extract_mentions("2 hours for $8, fine for parking.", "2026-08")[0]
        self.assertEqual(m["duration_h"], 2.0)
        self.assertEqual(pr.mention_text(m), "$8 for 2 hours")

    def test_snippet_short_and_dash_free(self):
        long = ("We circled for ages looking for anything open near the arena and the waterfront — finally "
                "found this garage where parking was $18 for the day – which beat the $40 event lots by the "
                "stadium entrance and the side streets that were all permit only anyway.")
        ms = pr.extract_mentions(long, "2026-08")
        self.assertTrue(ms)
        for m in ms:
            self.assertLessEqual(len(m["text"]), 120)
            self.assertNotIn("–", m["text"])
            self.assertNotIn("—", m["text"])
        self.assertIn("$18", ms[0]["text"])

    def test_dedupe_within_review(self):
        ms = pr.extract_mentions("Parking was $10 for the day. Yes, $10 for the day!", "2026-08")
        self.assertEqual(len(ms), 1)

    def test_summary(self):
        ms = [{"amount": 20.0, "unit": "day", "date": "2026-03"}, {"amount": 15.0, "unit": "flat", "date": "2026-08"},
              {"amount": 15.0, "unit": "flat", "date": "2026-01"}]
        self.assertEqual(pr.summary_text(ms), "reviews mention $15 flat (Aug 2026), $20 per day (Mar 2026)")
        self.assertEqual(pr.summary_text([]), "")


class Api(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)

    def tearDown(self):
        self.env.stop()

    def test_no_key_returns_none_silently(self):
        w = []
        with mock.patch.object(dc, "fetch") as f:
            self.assertIsNone(pr.review_prices(43.64, -79.37, 800, w))
            f.assert_not_called()
        self.assertEqual(w, [])

    def test_request_shape_and_key_only_in_header(self):
        w = []
        body = reply(place("Green P Carpark 43", 43.6466, -79.3741,
                           [review("Paid $9 for the whole day on a Saturday, great garage.")]))
        with mock.patch.object(dc, "fetch", return_value=body) as f:
            res = pr.review_prices(43.6406, -79.3756, 800, w, key=KEY, today=TODAY)
        args, kw = f.call_args
        url = args[0] if args else kw["url"]
        self.assertEqual(url, "https://places.googleapis.com/v1/places:searchNearby")
        self.assertNotIn(KEY, url)
        self.assertEqual(kw["headers"]["X-Goog-Api-Key"], KEY)
        self.assertEqual(kw["max_age_h"], 0)
        self.assertNotIn("cache_key", kw)
        mask = kw["headers"]["X-Goog-FieldMask"]
        self.assertNotIn(" ", mask)
        self.assertIn("places.reviews", mask)
        self.assertIn("places.googleMapsUri", mask)
        sent = json.loads(kw["data"])
        self.assertEqual(sent["includedTypes"], ["parking", "parking_garage", "parking_lot"])
        self.assertEqual(sent["maxResultCount"], 20)
        self.assertEqual(sent["locationRestriction"]["circle"]["radius"], 800.0)
        self.assertEqual(sent["locationRestriction"]["circle"]["center"], {"latitude": 43.6406, "longitude": -79.3756})
        self.assertEqual(f.call_count, 1)
        self.assertNotIn(KEY, json.dumps(res))
        self.assertEqual(w, [])
        self.assertEqual(res["source"], "Google reviews via Places API")
        self.assertIn("Google Maps", res["attribution"])
        p = res["places"][0]
        self.assertEqual(p["name"], "Green P Carpark 43")
        self.assertEqual(p["maps_url"], "https://maps.google.com/?cid=1234567890")
        self.assertEqual(p["kind"], "garage")
        m = p["mentions"][0]
        self.assertEqual((m["amount"], m["unit"], m["date"], m["rating"], m["author"]), (9.0, "weekend", "2026-08", 4, "Pat K."))
        self.assertEqual(p["summary"], "reviews mention $9 weekends (Aug 2026)")

    def test_env_key_used(self):
        w = []
        with mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": KEY}):
            with mock.patch.object(dc, "fetch", return_value=reply()) as f:
                res = pr.review_prices(43.64, -79.37, 800, w, today=TODAY)
        self.assertEqual(f.call_args[1]["headers"]["X-Goog-Api-Key"], KEY)
        self.assertEqual(res["places"], [])

    def test_empty_reply(self):
        with mock.patch.object(dc, "fetch", return_value=b"{}"):
            res = pr.review_prices(43.64, -79.37, 800, [], key=KEY, today=TODAY)
        self.assertEqual(res["places"], [])

    def test_http_error_warns_without_key(self):
        err = urllib.error.HTTPError(pr.ENDPOINT, 403, "Forbidden", {}, io.BytesIO(json.dumps(
            {"error": {"code": 403, "message": "API key not valid. Key: %s" % KEY, "status": "PERMISSION_DENIED"}}).encode()))
        w = []
        with mock.patch.object(dc, "fetch", side_effect=err):
            self.assertIsNone(pr.review_prices(43.64, -79.37, 800, w, key=KEY, today=TODAY))
        self.assertEqual(len(w), 1)
        self.assertIn("HTTP 403", w[0])
        self.assertIn("PERMISSION_DENIED", w[0])
        self.assertNotIn(KEY, w[0])

    def test_network_error_and_bad_json(self):
        w = []
        with mock.patch.object(dc, "fetch", side_effect=urllib.error.URLError("timed out key=%s" % KEY)):
            self.assertIsNone(pr.review_prices(43.64, -79.37, 800, w, key=KEY, today=TODAY))
        with mock.patch.object(dc, "fetch", return_value=b"<html>"):
            self.assertIsNone(pr.review_prices(43.64, -79.37, 800, w, key=KEY, today=TODAY))
        self.assertEqual(len(w), 2)
        self.assertFalse(any(KEY in x for x in w))

    def test_old_reviews_dropped(self):
        revs = [review("Parking was $30 flat for the game.", when="2024-12-01T10:00:00Z"),
                review("Paid $12 for the day to park.", when="2025-05-02T10:00:00Z"),
                review("Paid $14 for the day to park.", when="2025-04-01T10:00:00Z"),
                review("No date here, parking $5 per hour.", when=None)]
        with mock.patch.object(dc, "fetch", return_value=reply(place("Lot A", 43.641, -79.376, revs))):
            res = pr.review_prices(43.64, -79.37, 800, [], key=KEY, today=TODAY)
        got = [(m["amount"], m["date"]) for m in res["places"][0]["mentions"]]
        self.assertEqual(got, [(12.0, "2025-05")])
        self.assertEqual(res["places"][0]["recent_reviews"], 1)

    def test_dedupe_across_reviews_and_original_text(self):
        revs = [review("Parking $10 for the day.", when="2026-07-01T10:00:00Z"),
                review("Parking $10 for the day, fair.", when="2026-07-20T10:00:00Z"),
                review("Parking for the day, paid ten.", orig="Stationnement 11 $ pour la journée.",
                       when="2026-06-03T10:00:00Z")]
        with mock.patch.object(dc, "fetch", return_value=reply(place("Lot B", 43.641, -79.376, revs))):
            res = pr.review_prices(43.64, -79.37, 800, [], key=KEY, today=TODAY)
        ms = res["places"][0]["mentions"]
        self.assertEqual([(m["amount"], m["date"]) for m in ms], [(10.0, "2026-07"), (11.0, "2026-06")])

    def test_places_without_reviews_kept_last(self):
        body = reply(place("Near no reviews", 43.6407, -79.3757),
                     place("Far with rate", 43.645, -79.38, [review("Paid $16 for the day parking here.")]))
        with mock.patch.object(dc, "fetch", return_value=body):
            res = pr.review_prices(43.6406, -79.3756, 800, [], key=KEY, today=TODAY)
        self.assertEqual([p["name"] for p in res["places"]], ["Far with rate", "Near no reviews"])
        self.assertEqual(res["places"][1]["summary"], "")

    def test_radius_clamped(self):
        with mock.patch.object(dc, "fetch", return_value=reply()) as f:
            pr.review_prices(43.64, -79.37, 90000, [], key=KEY, today=TODAY)
        self.assertEqual(json.loads(f.call_args[1]["data"])["locationRestriction"]["circle"]["radius"], 50000.0)

    def test_cli_without_key(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch.object(dc, "fetch") as f:
            pr.main(["43.64", "-79.37"])
        f.assert_not_called()
        self.assertIn("no GOOGLE_PLACES_API_KEY", out.getvalue())


class MatchLots(unittest.TestCase):
    def lot(self, name, lat, lon, kind="garage"):
        return {"name": name, "lat": lat, "lon": lon, "kind": kind, "cost": 9.0, "confidence": "stale",
                "source": "Green P (2019 data)", "walk_m": 300}

    def places(self):
        m1 = [{"amount": 9.0, "unit": "weekend", "text": "Paid $9 on Saturday", "date": "2026-08", "rating": 4}]
        m2 = [{"amount": 20.0, "unit": "day", "text": "$20 for the day", "date": "2026-03", "rating": 3}]
        return [
            {"name": "Green P St. Lawrence Garage", "lat": 43.64670, "lon": -79.37420, "address": "2 Church St, Toronto",
             "maps_url": "https://maps.google.com/?cid=1", "kind": "garage", "dist_m": 680, "mentions": m1,
             "summary": pr.summary_text(m1)},
            {"name": "Impark Lot 4021", "lat": 43.6420, "lon": -79.3790, "address": "50 Yonge St, Toronto",
             "maps_url": "https://maps.google.com/?cid=2", "kind": "lot", "dist_m": 350, "mentions": m2,
             "summary": pr.summary_text(m2)},
            {"name": "Quiet Lot", "lat": 43.6430, "lon": -79.3800, "address": "", "maps_url": "", "kind": "lot",
             "dist_m": 400, "mentions": [], "summary": ""},
        ]

    def test_attach_by_name_and_extras(self):
        lots = [self.lot("Green P 2 Church St", 43.64675, -79.37415),
                self.lot("Green P 15 Wellington St", 43.64690, -79.37450),   # nearer by name? no: other lot
                self.lot("Street parking Front St", 43.6420, -79.3790, kind="street")]
        extras = pr.match_lots(self.places(), lots, dest=DEST)
        self.assertEqual(lots[0]["review_summary"], "reviews mention $9 weekends (Aug 2026)")
        self.assertEqual(lots[0]["review_url"], "https://maps.google.com/?cid=1")
        self.assertNotIn("review_mentions", lots[1])
        self.assertNotIn("review_mentions", lots[2])   # street segments never take a garage's reviews
        self.assertEqual(len(extras), 1)
        x = extras[0]
        self.assertEqual((x["name"], x["kind"], x["cost"], x["confidence"], x["unverified"]),
                         ("Impark Lot 4021", "lot", None, "review", True))
        self.assertEqual(x["source"], "Google reviews via Places API")
        self.assertIn("$20 per day", x["cost_text"])
        self.assertGreater(x["walk_m"], 0)
        for k in ("walk_min", "fits_stay", "free", "_dist", "lat", "lon", "notes", "capacity", "often_full"):
            self.assertIn(k, x)

    def test_nearest_when_no_name_match(self):
        lots = [self.lot("Some Garage", 43.64672, -79.37425), self.lot("Other Garage", 43.64700, -79.37480)]
        extras = pr.match_lots(self.places()[:1], lots)
        self.assertEqual(lots[0]["review_place"], "Green P St. Lawrence Garage")
        self.assertNotIn("review_mentions", lots[1])
        self.assertEqual(extras, [])

    def test_too_far_becomes_extra(self):
        lots = [self.lot("Green P 2 Church St", 43.6480, -79.3742)]   # about 145 m away
        extras = pr.match_lots(self.places()[:1], lots, max_m=80)
        self.assertNotIn("review_mentions", lots[0])
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0]["walk_m"], int(round(680 * 1.3)))

    def test_merge_two_places_into_one_lot(self):
        p = self.places()[:1]
        q = dict(p[0], name="St Lawrence Garage", mentions=[{"amount": 9.0, "unit": "weekend", "text": "x",
                                                              "date": "2026-08", "rating": 5},
                                                             {"amount": 2.5, "unit": "half hour", "text": "y",
                                                              "date": "2026-09", "rating": 4}])
        lots = [self.lot("Green P 2 Church St", 43.64675, -79.37415)]
        pr.match_lots(p + [q], lots)
        self.assertEqual(len(lots[0]["review_mentions"]), 2)
        self.assertEqual(lots[0]["review_summary"], "reviews mention $2.50 per half hour (Sep 2026), $9 weekends (Aug 2026)")


class Hygiene(unittest.TestCase):
    def test_no_en_or_em_dash_in_module_strings(self):
        with open(pr.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("–", src)
        self.assertNotIn("—", src)
        self.assertNotIn("–", pr.ATTRIBUTION + pr.summary_text([{"amount": 1.0, "unit": "day", "date": "2026-01"}]))


if __name__ == "__main__":
    unittest.main()
