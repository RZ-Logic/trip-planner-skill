"""Regression tests for the review's region data findings: the Ontario region's curated fares and timetables
(distance- and zone-based rural fares shown as approximate, route-level prices; hand-entered and projected
timetables flagged at run time, region files past their recheck age), the region notes against the data, and
the docs, manifests and zip against the code (flags, labels, versions, paths, zip contents).

Offline: the bundled region files and docs, synthetic legs, plan runs with every network call mocked, a fixed clock.
Run: python -m unittest tests.test_review_region_data -v
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
import copy
import datetime as dt
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import pin_clock, th  # noqa: E402  (test_engine inserts the scripts path)
import drive_plan  # noqa: E402

REGIONS = {r["id"]: r for r in th.load_regions()}
ONTARIO = REGIONS["ontario"]
_CLOCK = pin_clock()   # utc_now() = 2026-10-12 16:00


def setUpModule():
    _CLOCK.start()


def tearDownModule():
    _CLOCK.stop()


def read(rel):
    """A repo-relative text file."""
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def ok_network(hosts, timeout=6):
    return {h: "ok" for h in hosts}


def one_feed_region(fid):
    """A test region holding a deep copy of one ontario.json feed (time zone, holidays, fares and verified date
    from ontario.json), box over all of Ontario."""
    f = copy.deepcopy(next(x for x in ONTARIO["feeds"] if x["id"] == fid))
    r = {k: ONTARIO[k] for k in ("timezone", "utc_std_offset_hours", "dst_rule", "holidays", "flat_fares", "verified")}
    r.update(id="t", name="T", bbox={"min_lat": 41.0, "max_lat": 47.0, "min_lon": -84.0, "max_lon": -74.0}, feeds=[f])
    return r


def run_plan(region, args, as_json=True):
    """th.main on one test region, network mocked: the parsed --json result or the text output."""
    out = io.StringIO()
    with mock.patch.object(th, "load_regions", return_value=[region]),             mock.patch.object(th, "check_network", side_effect=ok_network),             redirect_stdout(out), redirect_stderr(io.StringIO()):
        th.main(args + ["--region", "t", "--routing", "estimate", "--final-ride", "off", "--no-discover"]
                + (["--json"] if as_json else []))
    return json.loads(out.getvalue()) if as_json else out.getvalue()


class NoFares:   # journey_fare's net: flat fares only
    fares, fare_urls, stop_zone = {}, {}, [""]


def ride_leg(feed, agency, route, frm="A", to="B", depart=36000):
    return {"type": "ride", "feed": feed, "agency": agency, "route": route, "route_id": route, "from": frm,
            "to": to, "depart": depart, "arrive": depart + 1200, "from_idx": 0, "to_idx": 0}


HSAT = ("Huron Shores Area Transit (Routes 1-3 and Route 2 Beach Bus)", "Huron Shores Area Transit")
LINX = ("Simcoe County LINX", "Simcoe County Linx")


class RuralFareTests(unittest.TestCase):
    """data-integrity-6: a distance- or zone-based rural fare is one common price on a scale. It must print as
    'about X (scale)', not as a firm figure, and LINX Route 4 has its own $2 fare."""

    def test_distance_based_fare_prints_as_approximate_with_its_scale(self):
        total, cur, parts, complete = th.journey_fare(NoFares, [ride_leg(HSAT[0], HSAT[1], "1")],
                                                      ONTARIO["flat_fares"], "card")
        self.assertEqual(parts[0]["fare"], 12.0)
        self.assertIn("approx", parts[0])
        self.assertIn("$6", parts[0]["approx"])
        self.assertIn("$20", parts[0]["approx"])
        o = {"transit_fare": total, "fare_parts": parts, "fare_currency": cur, "fare_complete": complete}
        txt = th.fare_text(o, "CAD", True, rules_on=True)
        self.assertIn("transit fare about 12.00 CAD", txt)
        self.assertIn("Huron Shores Area Transit about 12.00 (distance-based", txt)

    def test_every_distance_or_zone_fare_entry_is_flagged(self):
        for key in ("Huron Shores Area Transit", "PC Connect", "GOST (Guelph Owen Sound Transportation)",
                    "Simcoe County Linx", "Corridor 11 Bus", "Middlesex County Connect (Sarnia-London)"):
            self.assertTrue(ONTARIO["flat_fares"][key].get("approx"), key)

    def test_linx_route_4_uses_its_own_two_dollar_fare(self):
        for payment, want in (("cash", 2.0), ("card", 1.8)):
            _t, _c, parts, _ok = th.journey_fare(NoFares, [ride_leg(LINX[0], LINX[1], "4")],
                                                 ONTARIO["flat_fares"], payment)
            self.assertEqual(parts[0]["fare"], want, payment)
            self.assertNotIn("approx", parts[0], payment)
        _t, _c, parts, _ok = th.journey_fare(NoFares, [ride_leg(LINX[0], LINX[1], "2")],
                                             ONTARIO["flat_fares"], "cash")
        self.assertEqual(parts[0]["fare"], 6.0)
        self.assertTrue(parts[0].get("approx"))

    def test_free_part_carries_no_approx(self):
        flat = dict(ONTARIO["flat_fares"])
        flat["Huron Shores Area Transit"] = dict(flat["Huron Shores Area Transit"], card=0.0)
        _t, _c, parts, _ok = th.journey_fare(NoFares, [ride_leg(HSAT[0], HSAT[1], "1")], flat, "card")
        self.assertEqual(parts[0]["fare"], 0)
        self.assertNotIn("approx", parts[0])

    def test_approx_is_kept_apart_from_the_transfer_caveat(self):
        # an approximate fare is not a fare before transfer discounts: the two caveats stay separate
        total, cur, parts, complete = th.journey_fare(NoFares, [ride_leg(HSAT[0], HSAT[1], "1")],
                                                      ONTARIO["flat_fares"], "card")
        self.assertNotIn("transfers_unknown", parts[0])
        o = {"transit_fare": total, "fare_parts": parts, "fare_currency": cur, "fare_complete": complete}
        self.assertNotIn("before transfer discounts", th.fare_text(o, "CAD", True, rules_on=True))


WEGO = ["--origin", "43.14917,-79.04799", "--dest", "43.2494,-79.06157"]   # Floral Clock to Fort George
MEGABUS = ["--origin", "43.64415,-79.37747", "--dest", "44.2595,-76.50604"]   # Toronto Union to Kingston terminal


def assumed_warnings(res):
    return [w for w in res["warnings"] if "assumed" in w]


class AssumedTimetableTests(unittest.TestCase):
    """architecture-product-4: a hand-entered timetable whose notes say its times are projected (WEGO 604's 2027
    seasons, the HSAT 2027 Beach Bus, Megabus east after its announced 2027-01-06 change) is planned as fact.
    The leg must carry a mark, the run a WARNING, the FEEDS line a 'hand-entered' tag; and a region file whose
    verified date is over six months old must warn."""

    def test_projected_wego_604_times_carry_a_warning_and_a_leg_mark(self):
        region = one_feed_region("wego-604-notl")
        res = run_plan(region, WEGO + ["--depart", "2027-06-15 10:00"])
        ws = [w for w in res["warnings"] if "WEGO 604" in w and "assumed" in w and "2027-06-15" in w]
        self.assertEqual(len(ws), 1, res["warnings"])
        self.assertIn("projected from the 2026 schedule", ws[0])
        self.assertIs(res["transit_only"]["legs"][0].get("assumed"), True)
        self.assertIs(res["feeds"][0]["hand_entered"], True)
        self.assertIs(res["feeds"][0]["assumed_on_date"], True)
        txt = run_plan(region, WEGO + ["--depart", "2027-06-15 10:00"], as_json=False)
        self.assertIn("timetable assumed", txt)
        self.assertIn("(2026-10-04..2027-10-31, hand-entered)", txt)

    def test_published_fall_2026_wego_times_are_not_marked_assumed(self):
        res = run_plan(one_feed_region("wego-604-notl"), WEGO + ["--depart", "2026-10-15 10:00"])
        self.assertEqual(assumed_warnings(res), [])
        self.assertTrue(res["transit_only"]["legs"])
        legs = res["transit_only"]["legs"] + [lg for o in res["pareto"] + res["others"] for lg in o["legs"]]
        self.assertFalse(any(lg.get("assumed") for lg in legs))
        self.assertIs(res["feeds"][0]["assumed_on_date"], False)
        self.assertIs(res["feeds"][0]["hand_entered"], True)

    def test_megabus_east_after_its_announced_change_is_marked_assumed(self):
        region = one_feed_region("megabus-on-east")
        res = run_plan(region, MEGABUS + ["--depart", "2027-03-10 09:00"])
        self.assertTrue([w for w in assumed_warnings(res) if "Megabus" in w and "2027-01-06" in w], res["warnings"])
        res = run_plan(region, MEGABUS + ["--depart", "2026-10-20 09:00"])
        self.assertEqual(assumed_warnings(res), [])
        self.assertTrue(res["transit_only"]["legs"])

    def test_load_feed_marks_only_trips_on_assumed_service_days(self):
        f = {"id": "x", "name": "X", "observed_until": "2027-01-05", "assumed_dates": ["2026-12-24"],
             "manual": {"agency": "X", "timezone": "America/Toronto",
                        "services": {"s": {"days": "1111111", "start": "2026-10-01", "end": "2027-09-30"},
                                     "p": {"days": "1111111", "start": "2027-06-01", "end": "2027-06-30",
                                           "assumed": True}},
                        "routes": [{"route": "1", "trips": [
                            {"service": "s", "stops": [["A", 43.0, -80.0, "10:00"], ["B", 43.05, -80.0, "10:10"]]},
                            {"service": "p", "stops": [["A", 43.0, -80.0, "11:00"], ["B", 43.05, -80.0, "11:10"]]}]}]}}
        path = th.manual_feed_zip(f)
        for day, expect in ((dt.date(2027, 1, 5), 0), (dt.date(2027, 1, 6), 1), (dt.date(2026, 12, 24), 1),
                            (dt.date(2026, 12, 23), 0), (dt.date(2027, 6, 10), 2)):
            net = th.Network()
            net.warnings = []
            info = th.load_feed(net, "x", "X", path, day, 0, 30 * 3600, provenance=th.feed_provenance(f))
            self.assertEqual(len(net.assumed_trips), expect, day)
            self.assertIs(info["assumed_on_date"], bool(expect), day)
        self.assertIsNone(th.feed_provenance({"id": "g", "urls": ["https://example.org/gtfs.zip"]}))

    def test_region_file_older_than_six_months_warns(self):
        region = one_feed_region("wego-604-notl")
        with mock.patch.object(th, "utc_now", return_value=dt.datetime(2027, 5, 1, 12, 0)):
            res = run_plan(region, WEGO + ["--depart", "2027-05-20 10:00"])
        self.assertTrue([w for w in res["warnings"] if "last checked 2026-10-10" in w], res["warnings"])
        res = run_plan(region, WEGO + ["--depart", "2026-10-15 10:00"])
        self.assertFalse([w for w in res["warnings"] if "last checked" in w], res["warnings"])


MAX_STRAIGHT_KMH = 110   # about 125-130 km/h by road; 85 would flag dozens of plausible Hwy 401 coach legs


def _minutes(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def hand_entered_trips():
    """(region id, feed, route, trip) for every hand-entered trip in the bundled region files."""
    for r in th.load_regions():
        for f in r.get("feeds", []):
            for route in (f.get("manual") or {}).get("routes", []):
                for t in route["trips"]:
                    yield r["id"], f, route, t


class HandEnteredTimetableData(unittest.TestCase):
    """data-integrity-8: hand-entered timetables held legs faster than any road allows (Onex Milton to Kitchener
    in 20 min on two weekend trips, GOST Williamsford to Chatsworth 9.6 km in 5 min). The operators' own
    published times say so, so the times stay; such a trip carries a 'check' note that warns on any shown leg."""

    def test_no_hand_entered_leg_is_faster_than_the_road_allows(self):
        bad = []
        for _rid, f, _route, t in hand_entered_trips():
            if t.get("check"):
                continue   # the operator publishes these times; the trip's 'check' note warns on every shown leg
            for a, b in zip(t["stops"], t["stops"][1:]):
                dep = a[4] if len(a) > 4 else a[3]
                minutes = _minutes(b[3]) - _minutes(dep)
                km = th.haversine_m(a[1], a[2], b[1], b[2]) / 1000.0
                if minutes <= 0 or km / (minutes / 60.0) > MAX_STRAIGHT_KMH:
                    bad.append((f["id"], t["service"], a[0], dep, b[0], b[3],
                                round(km / (minutes / 60.0)) if minutes > 0 else None))
        self.assertEqual(bad, [], bad)

    def test_check_notes_are_kept_to_the_known_doubtful_trips(self):
        # a 'check' note is not a way to skip the speed test: only the three trips the review found carry one
        checked = sorted((f["id"], t["service"], t["stops"][0][3]) for _r, f, _ro, t in hand_entered_trips()
                         if t.get("check"))
        self.assertEqual(checked, [("gost-guelph-owen-sound", "daily", "10:38"), ("onex", "ox_2050_we", "20:50"),
                                   ("onex", "ox_2155_we", "21:55")])

    def test_gost_doubtful_leg_warns_and_keeps_the_published_times(self):
        res = run_plan(one_feed_region("gost-guelph-owen-sound"),
                       ["--origin", "44.17457,-80.81917", "--dest", "44.45658,-80.89835",   # Durham to Chatsworth
                        "--depart", "2026-10-15 12:00"])
        leg = res["transit_only"]["legs"][0]
        self.assertEqual((th.fmt_t(leg["depart"]), th.fmt_t(leg["arrive"])), ("12:15", "12:40"))
        self.assertIn("misprint", leg.get("check", ""))
        ws = [w for w in res["warnings"] if w.startswith("GOST") and "12:35" in w and "check with the operator" in w]
        self.assertEqual(len(ws), 1, res["warnings"])
        txt = run_plan(one_feed_region("gost-guelph-owen-sound"),
                       ["--origin", "44.17457,-80.81917", "--dest", "44.45658,-80.89835",
                        "--depart", "2026-10-15 12:00"], as_json=False)
        self.assertIn("times doubtful", txt)

    def test_onex_weekend_milton_to_kitchener_warns(self):
        res = run_plan(one_feed_region("onex"), ["--origin", "43.52803,-79.9038",   # Milton GO carpool lot
                                                 "--dest", "43.40955,-80.39267",   # Kitchener Sportsworld
                                                 "--depart", "2026-10-17 22:30"])   # a Saturday
        leg = res["transit_only"]["legs"][0]
        self.assertEqual(th.fmt_t(leg["depart"]), "22:55")
        self.assertTrue(leg.get("check"))
        self.assertTrue([w for w in res["warnings"] if w.startswith("Onex") and "20 min" in w], res["warnings"])

    def test_published_gost_run_without_a_check_note_does_not_warn(self):
        res = run_plan(one_feed_region("gost-guelph-owen-sound"),
                       ["--origin", "44.17457,-80.81917", "--dest", "44.45658,-80.89835",
                        "--depart", "2026-10-15 18:30"])   # the 17:30 Guelph run: Durham 19:07, Chatsworth 19:32
        self.assertNotIn("check", res["transit_only"]["legs"][0])
        self.assertFalse([w for w in res["warnings"] if "check with the operator" in w], res["warnings"])


PORT_HOPE = ["--origin", "43.94915,-78.29352", "--dest", "43.96958,-78.19966"]   # Town Hall to Northumberland Mall

# manual feeds whose notes say "assumed" but carry no projected-times flag, each with the reason
NO_PROJECTED_TIMES = {
    "collingwood-blink": "published times; only the season end is assumed (the region age warning covers it)",
    "grey-transit-route-5": "the assumed holiday is a no-service day",
    "huntsville-transit": "the assumed items drop trips or add no-service days",
    "corridor-11": "the assumed holiday is a no-service day",
    "bracebridge-transit": "the assumed holiday is a no-service day",
}


class ResidualAssumedTimetables(unittest.TestCase):
    """data-integrity-5: hand-entered timetables whose notes say a part is assumed (Megabus west after its
    2027-01-05 booking horizon, Port Hope's 2027 reduced-service holidays, Ride Norfolk on Remembrance Day,
    Dec 24 and Sep 30) loaded with no flag, so their projected times were planned as fact with no WARNING."""

    def test_residual_assumed_service_days_are_flagged(self):
        feeds = {f["id"]: f for f in ONTARIO["feeds"]}
        for fid, day, expect in (("megabus-on-west", dt.date(2027, 1, 5), False),
                                 ("megabus-on-west", dt.date(2027, 2, 2), True),
                                 ("port-hope-transit", dt.date(2027, 8, 2), True),
                                 ("port-hope-transit", dt.date(2027, 8, 3), False),
                                 ("ride-norfolk-brantford", dt.date(2026, 11, 11), True),
                                 ("ride-norfolk-brantford", dt.date(2026, 11, 12), False)):
            f = feeds[fid]
            net = th.Network()
            net.warnings = []
            # 06:00-22:00 keeps the next service day's early trips (Megabus 2027-01-06 06:00) out of the window
            th.load_feed(net, fid, f["name"], th.manual_feed_zip(f), day, 6 * 3600, 22 * 3600,
                         provenance=th.feed_provenance(f))
            self.assertTrue(net.trips, (fid, day))
            self.assertEqual(bool(net.assumed_trips), expect, (fid, day))

    def test_port_hope_civic_holiday_run_warns(self):
        region = one_feed_region("port-hope-transit")
        res = run_plan(region, PORT_HOPE + ["--depart", "2027-08-02 09:00"])
        self.assertTrue(res["transit_only"]["legs"])
        ws = [w for w in assumed_warnings(res) if w.startswith("Port Hope") and "2027-08-02" in w]
        self.assertEqual(len(ws), 1, res["warnings"])
        res = run_plan(region, PORT_HOPE + ["--depart", "2027-08-03 09:00"])
        self.assertTrue(res["transit_only"]["legs"])
        self.assertEqual(assumed_warnings(res), [])

    def test_every_manual_feed_whose_notes_say_assumed_is_flagged_or_explained(self):
        missing = []
        for f in ONTARIO["feeds"]:
            if not f.get("manual") or not re.search(r"assum", f.get("notes", ""), re.I):
                continue
            prov = th.feed_provenance(f)
            flagged = prov["assumed_services"] or prov["assumed_dates"] or prov["observed_until"]
            if flagged:
                self.assertTrue(prov["note"], f["id"])   # the WARNING says what is assumed
            elif f["id"] not in NO_PROJECTED_TIMES:
                missing.append(f["id"])
        self.assertEqual(missing, [])


ONTARIO_MD = "plugins/trip-planner/skills/trip-planner/references/regions/ontario.md"


class NotesMatchData(unittest.TestCase):
    """data-integrity-13: ontario.md said Simcoe County LINX runs nothing on holidays, while its file runs Route 4
    on its Sunday trips and only Christmas is added as no service; and the rural taxi default was labelled 'from
    published flat rates' though every listed rural taxi publishes none."""

    def test_holiday_section_names_only_feeds_that_stop_on_every_holiday(self):
        md = read(ONTARIO_MD)
        sec = md[md.index("## 4. Holidays"):]
        sec = sec[:sec.index("\n## ")]
        m = re.search(r";\s*([^;.]+?)\s+run nothing", sec)
        named = [x.strip() for x in re.split(r",| and ", m.group(1)) if x.strip()]
        stop_all = {f["id"] for f in ONTARIO["feeds"] if not f.get("manual") and f.get("holiday_service") == "none"
                    and len(f.get("holiday_dates") or []) >= 5}   # every statutory holiday, not only Christmas
        mapped = set()
        for item in named:
            ids = [f["id"] for f in ONTARIO["feeds"] if f["name"].lower().startswith(item.lower())]
            self.assertEqual(len(ids), 1, (item, ids))
            self.assertIn(ids[0], stop_all, item)
            mapped.add(ids[0])
        self.assertEqual(mapped, stop_all)

    def test_rural_taxi_default_is_labelled_as_an_assumption(self):
        label = ONTARIO["taxi_pricing_default"]["label"].lower()
        self.assertNotIn("published", label.replace("publish no", ""))
        self.assertIn("assumed", label)
        self.assertIn("assumed local taxi rate", read(ONTARIO_MD))


def doc_files():
    """The published docs (never review/): SKILL.md, README.md, references/**/*.md and docs/*.md."""
    paths = [os.path.join(SKILL_DIR, "SKILL.md"), os.path.join(ROOT, "README.md")]
    paths += sorted(glob.glob(os.path.join(SKILL_DIR, "references", "**", "*.md"), recursive=True))
    return paths + sorted(glob.glob(os.path.join(ROOT, "docs", "*.md")))


def source_files(*dirs):
    return [p for d in dirs for p in sorted(glob.glob(os.path.join(d, "*.py")))]


SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
TOOLS_DIR = os.path.join(ROOT, "tools")
FLAG_RE = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]+")


def read_abs(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parser_actions():
    """Every argparse action of the two planners (trip_hubs and drive_plan)."""
    return [a for ap in (th.build_parser(), drive_plan.build_parser()) for a in ap._actions]


def load_build_zip():
    """tools/build_zip.py as a module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("build_zip", os.path.join(TOOLS_DIR, "build_zip.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DocContract(unittest.TestCase):
    """docs-contract-9: nothing checked the docs, manifests or zip against the code, so a renamed flag or label,
    a version bump in one file, or a stray file in the zip went unnoticed."""

    def test_every_flag_in_the_docs_exists(self):
        known = set()
        for path in source_files(SCRIPTS_DIR, TOOLS_DIR):
            known.update(re.findall(r"[\"'](--[a-z][a-z0-9-]+)[\"']", read_abs(path)))
        known.update(o for a in parser_actions() for o in a.option_strings)
        unknown = sorted((os.path.relpath(p, ROOT), f) for p in doc_files() for f in set(FLAG_RE.findall(read_abs(p)))
                         if f not in known)
        self.assertEqual(unknown, [])

    def test_flag_choices_named_in_the_docs_exist(self):
        actions = {o: a for a in parser_actions() for o in a.option_strings}
        named = 0
        for path in doc_files():
            for flag, values in re.findall(r"`(--[a-z0-9-]+) ([a-z0-9]+(?:\|[a-z0-9]+)+)`", read_abs(path)):
                named += 1
                self.assertIn(flag, actions, path)
                self.assertTrue(actions[flag].choices, flag)
                self.assertLessEqual(set(values.split("|")), set(actions[flag].choices), (path, flag))
        self.assertGreaterEqual(named, 2)   # --payment and --final-ride at least

    def test_backticked_labels_in_skill_exist_in_the_scripts(self):
        # every script's source, not only render(): TLS FAILED and OFFLINE live in SystemExit and WARNING text
        src = "\n".join(read_abs(p) for p in source_files(SCRIPTS_DIR))
        labels = set(re.findall(r"`([A-Z][A-Z0-9 &/+-]{2,})`", read_abs(os.path.join(SKILL_DIR, "SKILL.md"))))
        self.assertIn("NOT SHOWN", labels)
        self.assertEqual(sorted(x for x in labels if x not in src), [])

    def test_version_strings_agree(self):
        skill = read_abs(os.path.join(SKILL_DIR, "SKILL.md"))
        versions = {"trip_hubs.VERSION": th.VERSION,
                    "SKILL.md": re.search(r'^\s+version:\s*"([^"]+)"', skill, re.M).group(1),
                    "README badge": re.search(r"Status-v(\d+\.\d+\.\d+)", read("README.md")).group(1),
                    # the first numbered release heading: an "## Unreleased" heading above it is skipped
                    "CHANGELOG": re.search(r"^## (\d+\.\d+\.\d+)", read("CHANGELOG.md"), re.M).group(1)}
        for rel in ("plugins/trip-planner/plugin.json", "plugins/trip-planner/.claude-plugin/plugin.json"):
            versions[rel] = json.loads(read(rel))["version"]
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_named_reference_paths_exist(self):
        files = doc_files() + source_files(SCRIPTS_DIR)
        missing = sorted({(os.path.relpath(p, ROOT), ref) for p in files
                          for ref in re.findall(r"(?<![\w/])(references/[\w/.-]+\.(?:md|json))", read_abs(p))
                          if not os.path.exists(os.path.join(SKILL_DIR, ref))})
        links = re.findall(r"\]\(((?:plugins|docs|tools|tests)/[^)#\s]+)", read("README.md"))
        self.assertTrue(links)
        missing += [("README.md", ln) for ln in links if not os.path.exists(os.path.join(ROOT, ln))]
        self.assertEqual(missing, [])

    def test_zip_holds_only_the_skill_folder(self):
        mod = load_build_zip()
        arcs = [arc for _full, arc in mod.zip_entries()]
        self.assertTrue(all(a.startswith("trip-planner/") for a in arcs))
        for need in ("SKILL.md", "LICENSE", "agents/openai.yaml", "scripts/trip_hubs.py",
                     "references/regions/ontario.json"):
            self.assertIn("trip-planner/" + need, arcs)
        for a in arcs:
            self.assertFalse(re.search(r"__pycache__|\.pyc$|(^|/)\.env|/tests/", a), a)
            # a NOTICE file is allowed beside LICENSE (third-party data credits)
            self.assertIn(a.split("/")[1], {"SKILL.md", "LICENSE", "NOTICE", "agents", "references", "scripts"}, a)

    def test_zip_skips_caches_and_stray_files(self):
        mod = load_build_zip()
        tmp = tempfile.mkdtemp(prefix="trip-planner-zip-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for rel in ("SKILL.md", ".DS_Store", "scripts/a.py", "scripts/b.pyc", "scripts/__pycache__/a.cpython-38.pyc",
                    "scripts/.pytest_cache/README.md"):
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            open(os.path.join(tmp, rel), "w").close()
        with mock.patch.object(mod, "SKILL", tmp):
            arcs = [arc for _full, arc in mod.zip_entries()]
        self.assertEqual(arcs, ["trip-planner/SKILL.md", "trip-planner/scripts/a.py"])


if __name__ == "__main__":
    unittest.main()
