"""Tests for vehicles.py: NRCan Fuel Consumption Ratings lookup and the saved car profile.

The lookup tests run on fixture CSV rows (parsed with the same parser the index build uses), never the
network; download fallbacks are exercised through a mocked drive_common.fetch. A last class checks the
bundled index for 20 popular Canadian cars against NRCan's published figures.
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
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import vehicles as V  # noqa: E402

GAS_HEAD = ("Model year,Make,Model,Vehicle class,Engine size (L),Cylinders,Transmission,Fuel type,"
            "City (L/100 km),Highway (L/100 km),Combined (L/100 km),Combined (mpg),CO2 emissions (g/km),"
            "CO2 rating,Smog rating")
GAS_ROWS = """
2022,Toyota,Corolla,Compact,1.8,4,AV,X,7.6,5.9,6.8,42,158,7,7
2022,Toyota,Corolla,Compact,1.8,4,AV7,X,7.6,5.9,6.8,42,158,7,7
2022,Toyota,Corolla (3-mode),Compact,2.0,4,AV10,X,7.7,6.0,6.9,41,161,7,7
2022,Toyota,Corolla Hatchback,Compact,2.0,4,AV10,X,7.5,5.9,6.8,42,159,7,7
2022,Toyota,Corolla Hybrid,Compact,1.8,4,AV,X,4.4,5.1,4.7,60,110,8,7
2022,Toyota,Corolla Cross,Sport utility vehicle: Small,2.0,4,AV10,X,7.6,7.2,7.4,38,174,6,7
2022,Toyota,Corolla Cross AWD,Sport utility vehicle: Small,2.0,4,AV10,X,8.1,7.5,7.8,36,184,6,7
2024,Toyota,Corolla,Compact,2.0,4,AV10,X,7.4,5.7,6.7,42,158,7,7
2024,Toyota,Corolla Hybrid,Compact,1.8,4,AV,X,4.4,5.1,4.7,60,110,8,7
2022,Toyota,RAV4,Sport utility vehicle: Small,2.5,4,AS8,X,8.8,6.8,7.9,36,184,6,7
2022,Toyota,RAV4 AWD,Sport utility vehicle: Small,2.5,4,AS8,X,9.5,7.2,8.5,33,199,6,6
2022,Toyota,RAV4 Hybrid AWD,Sport utility vehicle: Small,2.5,4,AV,X,5.8,6.3,6.0,47,141,7,7
2023,Toyota,RAV4,Sport utility vehicle: Small,2.5,4,AS8,X,8.8,6.8,7.9,36,184,6,7
2023,Toyota,RAV4 Hybrid AWD,Sport utility vehicle: Small,2.5,4,AV,X,5.8,6.3,6.0,47,141,7,7
2023,Toyota,Sienna,Minivan,2.5,4,AV,X,6.6,6.5,6.6,43,154,7,7
2023,Toyota,Sienna AWD,Minivan,2.5,4,AV,X,6.8,6.6,6.7,42,158,7,7
2023,Toyota,Prius,Mid-size,2.0,4,AV,X,4.4,4.6,4.5,63,105,8,7
2019,Ford,F-150,Pickup truck: Standard,2.7,6,AS10,X,12.8,9.6,11.4,25,267,4,5
2019,Ford,F-150,Pickup truck: Standard,3.5,6,AS10,X,14.1,10.0,12.3,23,287,4,5
2019,Ford,F-150,Pickup truck: Standard,5.0,8,AS10,X,14.6,10.0,12.5,23,293,4,5
2019,Ford,F-150 4X4,Pickup truck: Standard,2.7,6,AS10,X,13.3,10.4,12.0,24,281,4,5
2019,Ford,F-150 4X4,Pickup truck: Standard,3.5,6,AS10,X,14.7,10.8,12.9,22,303,4,5
2019,Ford,F-150 4X4,Pickup truck: Standard,3.0,6,AS10,D,11.3,8.6,10.1,28,272,4,3
2019,Ford,F-150 Raptor 4X4,Pickup truck: Standard,3.5,6,AS10,Z,16.9,13.8,15.5,18,363,3,5
2022,Ford,F-150,Pickup truck: Standard,2.7,6,AS10,X,12.7,9.6,11.3,25,265,4,5
2022,Ford,F-150 Hybrid 4X4,Pickup truck: Standard,3.5,6,AS10,X,11.6,10.5,11.1,25,260,4,5
2023,Honda,Civic Sedan,Mid-size,2.0,4,AV,X,7.4,5.8,6.7,42,157,7,7
2023,Honda,Civic Hatchback,Full-size,1.5,4,AV7,X,7.7,6.3,7.1,40,166,6,7
2023,Honda,Civic Sedan Si,Mid-size,1.5,4,M6,Z,8.7,6.4,7.7,37,180,6,6
2023,Honda,Civic Type R,Full-size,2.0,4,M6,Z,10.8,8.3,9.7,29,227,5,6
2024,Honda,Civic Sedan,Mid-size,2.0,4,AV,X,7.4,5.8,6.7,42,157,7,7
2024,Honda,Civic Hatchback,Full-size,1.5,4,AV7,X,7.7,6.3,7.1,40,166,6,7
2023,Honda,CR-V,Sport utility vehicle: Small,1.5,4,AV,X,8.4,7.1,7.8,36,181,6,7
2023,Honda,CR-V AWD,Sport utility vehicle: Small,1.5,4,AV,X,9.1,7.6,8.4,34,197,6,6
2023,Honda,CR-V Hybrid AWD,Sport utility vehicle: Small,2.0,4,AV,X,6.0,6.9,6.4,44,151,7,7
2021,Chevrolet,Silverado,Pickup truck: Standard,2.7,4,A8,X,13.0,10.8,12.0,24,280,4,5
2021,Chevrolet,Silverado 4WD,Pickup truck: Standard,5.3,8,A10,X,15.8,12.3,14.2,20,332,3,5
2021,Chevrolet,Silverado 4WD,Pickup truck: Standard,3.0,6,A10,D,10.9,9.4,10.2,28,275,4,3
2021,Chevrolet,Silverado FFV,Pickup truck: Standard,5.3,8,A8,E,19.6,15.3,17.7,16,288,4,5
2020,Volkswagen,Jetta,Compact,1.4,4,A8,X,7.9,5.9,7.0,40,165,7,7
2020,Volkswagen,Jetta GLI,Compact,2.0,4,M6,Z,9.8,7.0,8.5,33,199,5,5
2022,Mercedes-Benz,C 300 4MATIC Sedan,Compact,2.0,4,AS9,Z,10.0,7.0,8.6,33,202,5,5
2024,Mazda,Mazda3 4-Door,Compact,2.5,4,AS6,X,8.4,6.3,7.5,38,176,6,7
2024,Mazda,Mazda3 5-Door 4WD,Mid-size,2.5,4,AS6,X,9.0,7.1,8.1,35,190,5,7
2024,Mazda,CX-5 4WD,Sport utility vehicle: Small,2.5,4,AS6,X,9.8,7.9,9.0,31,209,5,6
2024,Mazda,CX-50 4WD,Sport utility vehicle: Small,2.5,4,AS6,X,9.7,7.9,8.9,32,209,5,7
2024,Hyundai,Kona,Sport utility vehicle: Small,2.0,4,AV,X,8.0,6.6,7.4,38,174,6,7
2024,Hyundai,Kona N,Sport utility vehicle: Small,2.0,4,AM8,Z,11.8,8.7,10.4,27,246,5,3
2024,Kia,Niro,Sport utility vehicle: Small,1.6,4,AM6,X,4.5,5.2,4.8,59,112,8,7
2024,Lexus,RX 350 AWD,Sport utility vehicle: Small,2.4,4,AS8,Z,11.2,8.4,9.9,29,232,5,5
2024,Lexus,RX 350h AWD,Sport utility vehicle: Small,2.5,4,AV6,Z,6.3,6.8,6.5,43,152,7,7
* Ratings are estimates; see the guide.
"""
BEV = """Model year,Make,Model,Vehicle class,Motor (kW),Transmission,Fuel type,City (kWh/100 km),Highway (kWh/100 km),Combined (kWh/100 km),City (Le/100 km),Highway (Le/100 km),Combined (Le/100 km),Range (km),CO2 emissions (g/km),CO2 rating ,Smog rating,Recharge time (h)
2024,Tesla ,Model Y Long Range AWD,Sport utility vehicle: Small,291,A1,B,16.5,17.9,17.2,1.9,2.0,1.9,531,0,10,10,10
2024,Tesla,Model Y RWD,Sport utility vehicle: Small,211,A1,B,16.6,18.1,17.3,1.9,2.0,1.9,394,0,10,10,8
2024,Tesla,Model Y Long Range-I,Sport utility vehicle: Small,291,A1,B,17.4,18.2,17.8,2.0,2.0,2.0,497,0,10,10,10
2024,Tesla,Model 3 RWD,Mid-size,208,A1,B,15.0,16.6,15.8,1.7,1.9,1.8,438,0,10,10,8
2024,Hyundai,Kona Electric,Sport utility vehicle: Small,150,A1,B,16.3,20.3,18.1,1.8,2.3,2.0,420,0,10,10,9
2024,Kia,Niro EV,Sport utility vehicle: Small,150,A1,B,16.6,20.1,18.2,1.9,2.3,2.0,407,0,10,10,9
2022,Ford,F-150 Lightning Extended Range,Pickup truck: Standard,433,A1,B,28.0,35.0,31.0,3.1,3.9,3.5,515,0,10,10,13
"""
PHEV = """Model year,Make,Model,Vehicle class,Motor (kW),Engine size (L),Cylinders,Transmission,Fuel type 1,Combined Le/100 km,Range 1 (km),Recharge time (h),Fuel type 2,City (L/100 km),Highway (L/100 km),Combined (L/100 km),Range 2 (km),CO2 emissions (g/km),CO2 rating,Smog rating
2022,Toyota,RAV4 Prime,Sport utility vehicle: Small,134,2.5,4,AV,B/X*,2.5 ([22.3 kWh + 0.0 L]/100 km),68,4.5,X,5.7,6.4,6.0,911,44,10,7
2016,Chevrolet,Volt,Compact,111,1.5,4,AV,B/Z*,2.0 (18.0 kWh/100 km),85,4.5,X,5.5,5.6,5.6,676,32,10,7
2024,Kia,Niro Plug-in Hybrid,Sport utility vehicle: Small,62,1.6,4,AM6,B/X,2.1 ([18.5 kWh + 0.1 L]/100 km),55,2.5,X,4.9,5.4,5.1,893,40,10,7
"""
# MY2025 is published in Windows-1252: 'Coup\xe9'
CP1252 = (GAS_HEAD + "\r\n2025,Audi,A5 Coup\xe9 45 TFSI quattro,Subcompact,2.0,4,AM7,Z,10.1,7.3,8.8,32,207,5,4\r\n"
          ).encode("cp1252")


def fixture_index():
    classes, rows = [], []
    rows += V.parse_csv((GAS_HEAD + GAS_ROWS).encode("utf-8"))
    rows += V.parse_csv(BEV.encode("utf-8"))
    rows += V.parse_csv(("﻿" + PHEV).encode("utf-8"))
    rows += V.parse_csv(CP1252)
    comp = V.compact_rows(rows, classes)
    years = sorted(int(y) for y in comp)
    return {"years": [years[0], years[-1]], "classes": classes, "rows": comp,
            "files": [{"type": "gas", "years": [2015, 2024], "url": "https://example.test/my2015-2024.csv"},
                      {"type": "bev", "years": [2012, 2026], "url": "https://example.test/bev.csv"},
                      {"type": "phev", "years": [2012, 2026], "url": "https://example.test/phev.csv"}]}


class FixtureCase(unittest.TestCase):
    def setUp(self):
        V.set_index(fixture_index())
        # lookups here must never touch the network
        self.net = mock.patch.object(dc, "fetch", side_effect=AssertionError("network used"))
        self.net.start()

    def tearDown(self):
        self.net.stop()
        V.set_index(None)

    def look(self, text, **kw):
        w = []
        kw.setdefault("allow_download", False)
        return V.lookup(text, w, **kw), w


class TestParsing(FixtureCase):
    def test_cp1252_file_decodes(self):
        rows = V.parse_csv(CP1252)
        self.assertEqual(rows[0]["model"], "A5 Coup\xe9 45 TFSI quattro")
        self.assertEqual(rows[0]["fuel"], "Z")

    def test_e85_rows_dropped_and_footnote_skipped(self):
        rows = V.parse_csv((GAS_HEAD + GAS_ROWS).encode("utf-8"))
        self.assertFalse([r for r in rows if r["fuel"] == "E"])
        self.assertTrue(all(isinstance(r["year"], int) for r in rows))

    def test_bev_and_phev_layouts(self):
        bev = V.parse_csv(BEV.encode("utf-8"))
        y = [r for r in bev if r["model"].startswith("Model Y Long Range AWD")][0]
        self.assertEqual((y["kind"], y["fuel"], y["comb"], y["range_km"]), ("e", "B", 17.2, 531))
        self.assertEqual(bev[0]["make"], "Tesla")   # trailing space in the CSV
        self.assertTrue(any(r["model"] == "Model Y Long Range (Import)" for r in bev))
        ph = V.parse_csv(PHEV.encode("utf-8"))
        self.assertEqual((ph[0]["kind"], ph[0]["fuel"], ph[0]["comb"], ph[0]["e_kwh"], ph[0]["e_range"]),
                         ("p", "X", 6.0, 22.3, 68))
        self.assertEqual(ph[1]["e_kwh"], 18.0)

    def test_identical_rows_fold(self):
        idx = fixture_index()
        cor = [r for r in idx["rows"]["2022"]["Toyota"] if r[0] == "Corolla"]
        self.assertEqual(len(cor), 1)
        self.assertEqual(cor[0][8], 2)

    def test_detect_kind(self):
        self.assertEqual(V.detect_kind("Lexus", "RX 350h AWD", "Z", "AV6", 6.3, 6.8, 2024), "h")
        self.assertEqual(V.detect_kind("Ford", "F-150", "D", "AS10", 11, 8, 2019), "d")
        self.assertEqual(V.detect_kind("Toyota", "Sienna", "X", "AV", 6.6, 6.5, 2023), "h")
        self.assertEqual(V.detect_kind("Toyota", "Sienna", "X", "A8", 12.0, 8.5, 2019), "g")
        self.assertEqual(V.detect_kind("Honda", "Prelude", "X", "AV", 5.0, 5.7, 2026), "h")
        self.assertEqual(V.detect_kind("Chevrolet", "Trailblazer", "X", "AV", 7.8, 7.7, 2024), "g")

    def test_tokens_and_family(self):
        self.assertEqual(V.tokens("F-150 4X4"), ["f150", "4x4"])
        self.assertEqual(V.family_tokens("RAV4 Hybrid AWD"), ["rav4"])
        self.assertEqual(V.family_tokens("Corolla Cross AWD"), ["corolla", "cross"])
        self.assertEqual(V.family_display("IONIQ 5 Long Range AWD"), "IONIQ 5")
        self.assertEqual(V.car_class("Sport utility vehicle: Small"), "small-suv")
        self.assertEqual(V.car_class("Pickup truck: Standard"), "pickup")


class TestLookup(FixtureCase):
    KEYS = ("query", "label", "year", "make", "model", "class", "kind", "fuel", "unit", "city", "hwy", "comb",
            "variants", "range", "picked", "alternatives", "range_km", "source", "source_url", "confidence")

    def test_basic_and_shape(self):
        r, w = self.look("2022 Toyota Corolla")
        for k in self.KEYS:
            self.assertIn(k, r)
        self.assertEqual(r["label"], "2022 Toyota Corolla")
        self.assertEqual((r["kind"], r["fuel"], r["unit"]), ("gas", "regular", "L/100 km"))
        self.assertEqual(r["variants"], 4)        # 1.8 x2 (folded), 3-mode, hatchback; no Cross, no Hybrid
        self.assertEqual(r["comb"], 6.8)
        self.assertEqual(r["range"], [6.8, 6.9])
        self.assertEqual(r["picked"], "median of 4 variants")
        self.assertEqual(r["confidence"], "high")
        self.assertIn("2022 Toyota Corolla Hybrid", r["alternatives"])
        self.assertEqual(r["source_url"], "https://example.test/my2015-2024.csv")
        self.assertEqual(w, [])

    def test_year_after_model(self):
        r, _ = self.look("corolla 2022")
        self.assertEqual(r["label"], "2022 Toyota Corolla")

    def test_case_and_punctuation(self):
        r, _ = self.look("  2022 TOYOTA, COROLLA!! ")
        self.assertEqual(r["label"], "2022 Toyota Corolla")

    def test_hybrid_word_picks_hybrid(self):
        r, _ = self.look("2022 Corolla Hybrid")
        self.assertEqual((r["kind"], r["comb"], r["label"]), ("hybrid", 4.7, "2022 Toyota Corolla Hybrid"))
        self.assertIn("2022 Toyota Corolla", r["alternatives"])

    def test_sub_model_is_separate(self):
        r, _ = self.look("2022 Corolla Cross")
        self.assertEqual(r["label"], "2022 Toyota Corolla Cross")
        self.assertEqual(r["comb"], 7.6)

    def test_rav4_hybrid(self):
        r, _ = self.look("2023 RAV4 Hybrid")
        self.assertEqual((r["kind"], r["city"], r["hwy"], r["comb"]), ("hybrid", 5.8, 6.3, 6.0))
        self.assertEqual(r["picked"], "one variant")

    def test_spaced_alias(self):
        r, _ = self.look("2023 rav 4 hybrid")
        self.assertEqual(r["label"], "2023 Toyota RAV4 Hybrid")

    def test_non_hybrid_preferred_with_hybrid_alternative(self):
        r, _ = self.look("2023 Toyota RAV4")
        self.assertEqual(r["kind"], "gas")
        self.assertIn("2023 Toyota RAV4 Hybrid", r["alternatives"])

    def test_phev_gasoline_mode(self):
        r, _ = self.look("2022 RAV4 Prime")
        self.assertEqual((r["kind"], r["unit"], r["comb"]), ("phev", "L/100 km", 6.0))
        self.assertIn("gasoline mode", r["picked"])
        self.assertEqual((r["electric_kwh_100km"], r["electric_range_km"]), (22.3, 68))

    def test_plug_in_words(self):
        r, w = self.look("2023 Toyota RAV4 plug-in hybrid")
        self.assertEqual((r["kind"], r["year"]), ("phev", 2022))
        # the 2023 RAV4 is rated, only not as a plug-in: the warning names the powertrain (V6)
        self.assertIn("no 2023 Toyota RAV4 plug-in hybrid in NRCan; used the 2022 plug-in hybrid", w)

    def test_ev(self):
        r, _ = self.look("Tesla Model Y 2024")
        self.assertEqual((r["kind"], r["fuel"], r["unit"]), ("ev", "electricity", "kWh/100 km"))
        self.assertEqual(r["label"], "2024 Tesla Model Y")
        self.assertEqual(r["variants"], 3)
        self.assertEqual(r["comb"], 17.3)
        self.assertEqual(r["range_km"], 497)

    def test_ev_glued_name(self):
        r, _ = self.look("2024 model3")
        self.assertEqual((r["label"], r["comb"]), ("2024 Tesla Model 3", 15.8))

    def test_engine_narrows(self):
        r, _ = self.look("2019 F-150 3.5L")
        self.assertEqual(r["label"], "2019 Ford F-150 3.5L")
        self.assertEqual(r["variants"], 2)        # 2WD and 4X4 3.5; Raptor is another model
        self.assertEqual(r["engines"], [3.5])

    def test_f150_alias_excludes_raptor_and_diesel(self):
        r, _ = self.look("2019 Ford F150")
        self.assertEqual(r["variants"], 5)
        self.assertLess(r["range"][1], 15.5)
        self.assertIn("2019 Ford F-150 Diesel", r["alternatives"])

    def test_drive_narrows(self):
        r, _ = self.look("2019 F150 4x4")
        self.assertEqual(r["label"], "2019 Ford F-150 4WD")
        self.assertEqual(r["variants"], 2)

    def test_diesel(self):
        r, _ = self.look("2019 F-150 diesel")
        self.assertEqual((r["kind"], r["fuel"], r["comb"]), ("diesel", "diesel", 10.1))

    def test_named_sub_model(self):
        r, _ = self.look("2019 F-150 Raptor")
        self.assertEqual((r["label"], r["fuel"], r["comb"]), ("2019 Ford F-150 Raptor", "premium", 15.5))

    def test_electric_word_finds_sibling_model(self):
        r, _ = self.look("2022 F-150 electric")
        self.assertEqual(r["kind"], "ev")
        self.assertIn("Lightning", r["label"])

    def test_no_year_uses_newest(self):
        r, w = self.look("Honda Civic")
        self.assertEqual(r["year"], 2024)
        self.assertTrue(any("no model year" in x for x in w))
        self.assertEqual(r["confidence"], "medium")

    def test_type_r_and_si(self):
        r, _ = self.look("2023 Civic Type R")
        self.assertEqual((r["comb"], r["fuel"]), (9.7, "premium"))
        r, _ = self.look("2023 Honda Civic Si")
        self.assertEqual((r["comb"], r["label"]), (7.7, "2023 Honda Civic Si"))
        r, _ = self.look("2023 Honda Civic")
        self.assertLess(r["range"][1], 9.7)       # Type R left out of plain Civic

    def test_crv_spellings(self):
        for t in ("2023 Honda CRV", "2023 cr-v", "2023 Honda CR V"):
            r, _ = self.look(t)
            self.assertEqual(r["label"], "2023 Honda CR-V", t)
            self.assertEqual(r["kind"], "gas")
        r, _ = self.look("2023 crv hybrid")
        self.assertEqual(r["comb"], 6.4)

    def test_make_aliases(self):
        r, _ = self.look("2021 Chevy Silverado")
        self.assertEqual((r["make"], r["kind"]), ("Chevrolet", "gas"))
        self.assertEqual(r["variants"], 2)        # the E85 row of the FFV is not counted
        r, _ = self.look("2020 VW Jetta")
        self.assertEqual(r["make"], "Volkswagen")
        r, _ = self.look("2022 Mercedes C300")
        self.assertEqual((r["make"], r["fuel"], r["comb"]), ("Mercedes-Benz", "premium", 8.6))

    def test_make_glued_to_model(self):
        r, _ = self.look("2024 Mazda 3")
        self.assertEqual(r["model"], "Mazda3")
        self.assertEqual(r["variants"], 2)
        r, _ = self.look("2024 Mazda CX-5")
        self.assertEqual(r["comb"], 9.0)          # not CX-50

    def test_ev_alternative_listed(self):
        r, _ = self.look("2024 Hyundai Kona")
        self.assertEqual((r["kind"], r["comb"]), ("gas", 7.4))   # Kona N is another model
        self.assertIn("2024 Hyundai Kona Electric", r["alternatives"])
        r, _ = self.look("2024 Kona Electric")
        self.assertEqual((r["kind"], r["comb"], r["range_km"]), ("ev", 18.1, 420))

    def test_hybrid_only_line(self):
        r, _ = self.look("2024 Kia Niro")
        self.assertEqual(r["kind"], "hybrid")
        self.assertTrue(any("EV" in a for a in r["alternatives"]))
        self.assertTrue(any("Plug-in" in a for a in r["alternatives"]))
        r, _ = self.look("2023 Toyota Sienna")
        self.assertEqual((r["kind"], r["comb"]), ("hybrid", 6.7))

    def test_phev_only_line(self):
        r, _ = self.look("2016 Chevy Volt")
        self.assertEqual((r["kind"], r["comb"], r["electric_range_km"]), ("phev", 5.6, 85))

    def test_nearest_year(self):
        r, w = self.look("2023 Toyota Corolla")
        self.assertIn(r["year"], (2022, 2024))
        self.assertTrue(any("no 2023" in x for x in w))
        self.assertNotEqual(r["confidence"], "high")

    def test_year_too_far(self):
        r, w = self.look("2015 Toyota Corolla")
        self.assertIsNone(r)
        self.assertTrue(any("2022 to 2024" in x for x in w))

    def test_nonsense(self):
        r, w = self.look("purple banana")
        self.assertIsNone(r)
        self.assertTrue(w and "no NRCan rating matched" in w[0])

    def test_misspelling(self):
        r, w = self.look("Toyota Corola 2022")
        self.assertEqual(r["label"], "2022 Toyota Corolla")
        self.assertEqual(r["confidence"], "low")
        self.assertTrue(any("Corolla" in x for x in w))

    def test_make_only_and_empty(self):
        r, w = self.look("Toyota")
        self.assertIsNone(r)
        self.assertIn("Corolla", w[0])
        r, w = self.look("")
        self.assertIsNone(r)

    def test_unknown_engine_warns(self):
        r, w = self.look("2022 Corolla 9.9L")
        self.assertEqual(r["variants"], 4)
        self.assertTrue(any("9.9L" in x for x in w))

    def test_lexus_numbers(self):
        r, _ = self.look("2024 Lexus RX")
        self.assertEqual((r["kind"], r["comb"]), ("gas", 9.9))
        r, _ = self.look("2024 Lexus RX 350h")
        self.assertEqual((r["kind"], r["comb"]), ("hybrid", 6.5))

    def test_unknown_trim_word_ignored(self):
        r, w = self.look("2023 Honda CR-V Lariat")
        self.assertEqual(r["label"], "2023 Honda CR-V")
        self.assertTrue(any("ignored 'lariat'" in x for x in w))

    def test_no_text_crash(self):
        self.assertIsNone(V.lookup(None, []))


class TestDownloadFallback(unittest.TestCase):
    PKG = {"result": {"resources": [
        {"language": ["en"], "url": "https://open.canada.ca/x/download/my1995-2014-fuel-consumption-ratings-5-cycle.csv"},
        {"language": ["fr"], "url": "https://open.canada.ca/x/download/am1995-2014-cotes.csv"},
        {"language": ["en"], "url": "https://open.canada.ca/x/download/original-my1995-2014-fuel-consumption-ratings-2-cycle.csv"},
    ]}}
    OLD = (GAS_HEAD + "\n2009,Toyota,Corolla,Compact,1.8,4,A4,X,9.3,6.9,8.2,34,189,n/a,n/a\n"
           "2009,Toyota,Corolla,Compact,1.8,4,M5,X,8.9,6.6,7.9,36,182,n/a,n/a\n").encode("utf-8")

    def setUp(self):
        V.set_index(fixture_index())

    def tearDown(self):
        V.set_index(None)

    def test_missing_year_downloaded(self):
        calls = []

        def fake(url, **kw):
            calls.append((url, kw))
            if url == V.PACKAGE_API:
                return json.dumps(self.PKG).encode("utf-8")
            if url.endswith("my1995-2014-fuel-consumption-ratings-5-cycle.csv"):
                return self.OLD
            raise AssertionError(url)
        with mock.patch.object(dc, "fetch", side_effect=fake):
            w = []
            r = V.lookup("2009 Toyota Corolla", w)
        self.assertEqual((r["year"], r["comb"], r["variants"]), (2009, 8.1, 2))
        self.assertTrue(r["source_url"].endswith("my1995-2014-fuel-consumption-ratings-5-cycle.csv"))
        self.assertEqual(calls[0][1]["headers"]["Accept-Language"], "en-CA,en;q=0.9")
        self.assertEqual(calls[0][1]["headers"]["User-Agent"], dc.th.USER_AGENT)   # the skill's own, never a browser
        self.assertTrue(all(c[1]["headers"]["User-Agent"] == dc.th.USER_AGENT for c in calls))
        self.assertEqual(calls[1][1]["max_age_h"], 720.0)
        self.assertEqual(len(calls), 2)          # French and 2-cycle files skipped

    def test_download_failure_is_a_warning(self):
        with mock.patch.object(dc, "fetch", side_effect=urllib.error.URLError("offline")):
            w = []
            r = V.lookup("2009 Toyota Corolla", w)
        self.assertIsNone(r)
        self.assertTrue(any("could not reach" in x for x in w))

    def test_offline_never_calls_network(self):
        with mock.patch.object(dc, "fetch", side_effect=AssertionError("network used")):
            r = V.lookup("2009 Toyota Corolla", [], allow_download=False)
        self.assertIsNone(r)


class TestProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "sub", "car.json")
        self.env = mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": self.path})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_path_from_env(self):
        self.assertEqual(V.profile_path(), self.path)
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_CONFIG": self.tmp}):
            self.assertEqual(V.profile_path(), os.path.join(self.tmp, "car.json"))

    def test_default_path(self):
        env = dict(os.environ)
        env.pop("TRIP_PLANNER_CONFIG", None)
        env["APPDATA"] = self.tmp
        with mock.patch.dict(os.environ, env, clear=True):
            p = V.profile_path()
        self.assertTrue(p.endswith(os.path.join("trip-planner", "car.json")))
        if os.name == "nt":
            self.assertTrue(p.startswith(self.tmp))
        else:
            self.assertIn(".config", p)

    def test_missing_is_none(self):
        self.assertIsNone(V.load_profile())

    def test_save_load_forget(self):
        car = {"label": "2022 Toyota Corolla", "comb": 6.8, "kind": "gas", "l_per_100km": 7.2}
        p = V.save_profile(car)
        self.assertEqual(p, self.path)
        self.assertFalse([f for f in os.listdir(os.path.dirname(p)) if f.endswith(".tmp")])
        got = V.load_profile()
        self.assertEqual(got["label"], "2022 Toyota Corolla")
        self.assertEqual(got["l_per_100km"], 7.2)
        self.assertRegex(got["saved"], r"^\d{4}-\d\d-\d\d$")
        car2 = dict(car, battery_kwh=77.4, ev_model="ioniq5")
        V.save_profile(car2)
        self.assertEqual(V.load_profile()["battery_kwh"], 77.4)
        self.assertTrue(V.forget_profile())
        self.assertFalse(V.forget_profile())
        self.assertIsNone(V.load_profile())

    def test_lookup_result_round_trips(self):
        V.set_index(fixture_index())
        try:
            r = V.lookup("2023 RAV4 Hybrid", [], allow_download=False)
        finally:
            V.set_index(None)
        V.save_profile(r)
        got = V.load_profile()
        self.assertEqual((got["label"], got["comb"], got["range"]), (r["label"], r["comb"], r["range"]))

    def test_corrupt_file(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        w = []
        self.assertIsNone(V.load_profile(w))
        self.assertTrue(w and "unreadable" in w[0])
        with mock.patch("sys.stderr") as err:
            self.assertIsNone(V.load_profile())
            self.assertTrue(err.write.called)
        with open(self.path, "wb") as f:
            f.write(b"\xff\xfe\x00garbage")
        self.assertIsNone(V.load_profile([]))
        with open(self.path, "w") as f:
            f.write("[1, 2]")
        w = []
        self.assertIsNone(V.load_profile(w))
        self.assertTrue(w)
        self.assertTrue(V.forget_profile())


@unittest.skipUnless(os.path.exists(V.INDEX_PATH), "bundled vehicles index missing")
class TestBundledIndex(unittest.TestCase):
    """The shipped index, offline, for popular Canadian cars; exact figures are NRCan's published rows."""

    def setUp(self):
        V.set_index(None)
        self.net = mock.patch.object(dc, "fetch", side_effect=AssertionError("network used"))
        self.net.start()

    def tearDown(self):
        self.net.stop()

    def look(self, text):
        w = []
        r = V.lookup(text, w, allow_download=False)
        self.assertIsNotNone(r, "%s: %s" % (text, w))
        return r

    def test_meta(self):
        idx = V.load_index()
        self.assertLessEqual(idx["years"][0], 2010)
        self.assertGreaterEqual(idx["years"][1], 2026)
        self.assertIn("open.canada.ca", idx["dataset"])
        self.assertTrue(idx["built"] and idx["built_by"])
        self.assertLess(os.path.getsize(V.INDEX_PATH), 1000000)

    def test_popular_cars(self):
        cases = [("2023 Toyota Corolla", "gas", 6.0, 8.0), ("2023 Honda Civic", "gas", 6.5, 8.0),
                 ("2023 Honda CR-V", "gas", 7.5, 8.6), ("2023 Toyota RAV4", "gas", 7.5, 8.6),
                 ("2023 Nissan Rogue", "gas", 7.0, 8.5), ("2023 Ford Escape", "gas", 7.5, 9.5),
                 ("2023 Ford F-150", "gas", 10.5, 14.0), ("2023 Chevrolet Silverado", "gas", 11.5, 15.0),
                 ("2023 Toyota Tacoma", "gas", 11.0, 13.5), ("2023 Hyundai Elantra", "gas", 6.0, 8.0),
                 ("2023 Hyundai Tucson", "gas", 8.0, 9.5), ("2023 Tesla Model 3", "ev", 14.0, 19.0),
                 ("2023 Hyundai Ioniq 5", "ev", 17.0, 22.0), ("2025 Chevrolet Equinox EV", "ev", 18.0, 22.0),
                 ("2023 Mazda3", "gas", 7.0, 8.5), ("2023 Mazda CX-5", "gas", 8.5, 10.0),
                 ("2023 Subaru Outback", "gas", 8.0, 10.5), ("2023 Toyota Sienna", "hybrid", 6.0, 7.0)]
        for text, kind, lo, hi in cases:
            r = self.look(text)
            self.assertEqual(r["kind"], kind, text)
            self.assertTrue(lo <= r["comb"] <= hi, "%s comb %s" % (text, r["comb"]))

    def test_nrcan_published_values(self):
        # my2015-2024-fuel-consumption-ratings.csv: 2023,Toyota,RAV4 Hybrid AWD,...,5.8,6.3,6.0
        r = self.look("2023 Toyota RAV4 Hybrid")
        self.assertEqual((r["city"], r["hwy"], r["comb"], r["picked"]), (5.8, 6.3, 6.0, "one variant"))
        # my2012-2026-battery-electric-vehicles.csv: 2023,Hyundai,Kona Electric,...,15.5,19.9,17.4,...,415
        r = self.look("2023 Hyundai Kona Electric")
        self.assertEqual((r["city"], r["hwy"], r["comb"], r["range_km"]), (15.5, 19.9, 17.4, 415))
        # 2023,Honda,CR-V Hybrid AWD,...,6.0,6.9,6.4
        r = self.look("2023 Honda CR-V Hybrid")
        self.assertEqual((r["city"], r["hwy"], r["comb"]), (6.0, 6.9, 6.4))
        # 2023 Tesla Model Y: six rows, combined 17.1 to 19.2 kWh/100 km
        r = self.look("2023 Tesla Model Y")
        self.assertEqual((r["kind"], r["variants"], r["range"]), ("ev", 6, [17.1, 19.2]))
        # 2022 RAV4 Prime gasoline mode 5.7 / 6.4 / 6.0, 68 km electric
        r = self.look("2022 Toyota RAV4 Prime")
        self.assertEqual((r["kind"], r["comb"], r["electric_range_km"]), ("phev", 6.0, 68))


if __name__ == "__main__":
    unittest.main()
