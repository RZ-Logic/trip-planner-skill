"""Offline regression tests for the review fixes in the network, fetch and cache layer (trip_hubs network
classifier, --check-network, NETWORK BLOCKED, offline mode, the MobilityDatabase catalog, safe_text,
drive_common.fetch caching and pacing, the CI network guard). Every HTTP call is mocked.

Run: PYTHONPATH=tools/ci TRIP_PLANNER_NO_NETWORK=1 python -m unittest tests.test_review_network -v
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
import datetime
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402
import drive_plan as dp  # noqa: E402
th = dc.th
from test_engine import make_feed  # noqa: E402  (same pattern as test_regressions.py)

CERT = urllib.error.URLError(ssl.SSLCertVerificationError(
    1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"))
PROXY_403 = urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))


def fresh_cache(test):
    """A cache folder of the test's own, removed afterwards."""
    d = tempfile.mkdtemp()
    test.addCleanup(_shutil.rmtree, d, True)
    p = mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": d})
    p.start()
    test.addCleanup(p.stop)
    return d


def run_main(argv, main=None):
    """th.main (or another main) -> (exit code or SystemExit text, stdout)."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = (main or th.main)(argv)
    except SystemExit as e:
        return str(e.code), buf.getvalue()
    return rc, buf.getvalue()


def ok_network(hosts, timeout=6):
    return {h: "ok" for h in hosts}


def dt_(hour):
    return datetime.datetime(2026, 10, 21, hour, 0)


TRIP = ["--dest", "43.5812,-79.6475", "--depart", "2026-10-21 08:00"]


class TlsAndCauses(unittest.TestCase):
    """hubs-pricing-io-4: a failed certificate check is its own case (not 'reachable', not an address problem), and
    NETWORK BLOCKED names the cause it saw instead of always saying DNS failure or connection refused."""

    def setUp(self):
        fresh_cache(self)

    def test_certificate_failure_is_not_reported_reachable(self):
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=CERT):
            status = th.check_network(["nominatim.openstreetmap.org"])
        self.assertTrue(status["nominatim.openstreetmap.org"].startswith("tls failed"), status)
        slow = urllib.error.URLError(ssl.SSLError("_ssl.c:989: The handshake operation timed out"))
        self.assertFalse(th._tls_failed(slow))   # a slow or blocked path, not a certificate problem
        self.assertTrue(th._unreachable(slow))
        self.assertTrue(th._net_cause(slow).startswith("timed out"))

    def test_address_run_with_broken_certificates_stops_with_tls_failed(self):
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=CERT):
            code, _out = run_main(["--origin", "Union Station, Toronto"] + TRIP)
        self.assertTrue(code.startswith("TLS FAILED"), code)
        self.assertIn("certificate", code)
        self.assertNotIn("could not find", code)
        self.assertNotIn("NETWORK BLOCKED", code)

    def test_check_network_cli_counts_tls_failures(self):
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=CERT):
            rc, out = run_main(["--check-network"])
        self.assertEqual(rc, 1)
        self.assertIn("tls failed", out)
        self.assertIn("0 of ", out)
        self.assertIn("cannot verify HTTPS certificates", out)

    def test_timeout_is_named_not_called_dns(self):
        with mock.patch.object(th.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError(socket.timeout("timed out"))):
            code, _out = run_main(["--origin", "43.4988,-79.8560"] + TRIP)
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertIn("timed out", code)
        self.assertNotIn("DNS failure or connection refused", code)

    def test_dns_failure_is_named(self):
        dns = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=dns):
            code, _out = run_main(["--origin", "43.4988,-79.8560"] + TRIP)
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertIn("(DNS lookup failed)", code)

    def test_geocoders_failing_tls_give_tls_failed(self):
        with mock.patch.object(th, "http_get", side_effect=CERT):
            with self.assertRaises(SystemExit) as ctx:
                th._geocode("Union Station, Toronto", "origin", [])
        self.assertTrue(str(ctx.exception.code).startswith("TLS FAILED"), ctx.exception.code)

    def test_one_probe_failing_tls_is_a_warning_with_the_fix(self):
        def status(hosts, timeout=6):
            return {h: ("tls failed: x" if h == "router.project-osrm.org" else "ok") for h in hosts}
        ns = th.argparse.Namespace(origin="43.4988,-79.8560", dest="43.5812,-79.6475", region="auto",
                                   routing="osrm", crosscheck=False, feed=None, no_discover=False, access="ride")
        warnings = []
        with mock.patch.object(th, "check_network", side_effect=status):
            th.preflight(ns, th.load_regions(), warnings)
        self.assertTrue(any("router.project-osrm.org" in w and "cannot verify HTTPS certificates" in w
                            for w in warnings), warnings)

    def test_drive_planner_names_tls_not_blocked(self):
        def status(hosts, timeout=6):
            return {h: "tls failed: x" for h in hosts}
        with mock.patch.object(dp.th, "check_network", side_effect=status):
            note = dp.blocked_note([])
            rc, out = run_main(["--check-network"], dp.main)
        self.assertTrue(note.startswith("TLS FAILED"), note)
        self.assertEqual(rc, 1)
        self.assertIn("cannot verify HTTPS certificates", out)

BOX = {"min_lat": 43.3, "max_lat": 43.8, "min_lon": -80.1, "max_lon": -79.5}


def region(urls):
    return {"id": "t", "timezone": "America/Toronto", "bbox": BOX,
            "feeds": [{"id": "bus", "name": "Test Bus", "bbox": BOX, "urls": urls}]}


ARGV = ["--origin", "43.5000,-79.8000", "--dest", "43.5500,-79.8000", "--depart", "2026-10-21 08:00",
        "--routing", "estimate", "--region", "auto"]


class FeedHostsBlocked(unittest.TestCase):
    """hubs-pricing-io-5: the probe hosts answer but the feed hosts are blocked. The run says NETWORK BLOCKED with
    the feed hosts, not 'no transit departures' with a Python list of warnings."""

    def setUp(self):
        fresh_cache(self)
        for p in (mock.patch.object(th, "check_network", side_effect=ok_network),
                  mock.patch.object(th, "reverse_place_info",
                                    side_effect=AssertionError("no reverse geocoding expected"))):
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, urls, fetch_error=None):
        with mock.patch.object(th, "load_regions", return_value=[region(urls)]):
            if fetch_error is None:
                return run_main(ARGV)
            with mock.patch.object(th, "fetch_url", side_effect=fetch_error):
                return run_main(ARGV)

    def test_blocked_feed_hosts_give_network_blocked(self):
        code, _out = self.run_with(["https://feeds.example.org/gtfs.zip"], PROXY_403)
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertIn("feeds.example.org", code)
        self.assertIn("a proxy refused", code)
        self.assertNotIn("Warnings: [", code)
        self.assertNotIn("no transit departures", code)

    def test_feed_tls_failure_gives_tls_failed(self):
        code, _out = self.run_with(["https://feeds.example.org/gtfs.zip"], CERT)
        self.assertTrue(code.startswith("TLS FAILED"), code)
        self.assertIn("feeds.example.org", code)

    def test_feed_that_answered_with_junk_is_not_blocked(self):
        code, _out = self.run_with(["https://feeds.example.org/gtfs.zip"], ValueError("not a zip file"))
        self.assertTrue(code.startswith("no transit departures found in the time window."), code)
        self.assertIn("\nWARNING feed Test Bus failed to load", code)

    def test_no_departures_message_has_no_list_repr(self):
        path = os.path.join(tempfile.mkdtemp(), "evening.zip")
        self.addCleanup(_shutil.rmtree, os.path.dirname(path), True)
        make_feed(path, [("A", "Stop A", 43.5000, -79.8000), ("C", "Stop C", 43.5500, -79.8000)],
                  [("R1", "WK", "T1", "C")],
                  [("T1", "18:00:00", "18:00:00", "A", 1, 0, 0), ("T1", "18:30:00", "18:30:00", "C", 2, 0, 0)])
        code, _out = self.run_with([path])
        self.assertTrue(code.startswith("no transit departures found in the time window."), code)
        self.assertNotIn("['", code)
        self.assertNotIn("Warnings: [", code)

HSAT = {"id": "hsat", "name": "Huron Shores", "manual": {   # as test_getaways.HandEnteredTimetables.FEED
    "agency": "Huron Shores Area Transit", "url": "https://example.org", "timezone": "America/Toronto",
    "services": {"daily": {"days": "1111111", "start": "2026-01-01", "end": "2026-12-31", "except": ["2026-12-25"]}},
    "routes": [{"route": "2", "name": "London - Grand Bend", "trips": [
        {"service": "daily", "headsign": "Grand Bend", "stops": [
            ["London #705", 43.02824, -81.27757, "10:41"], ["Exeter", 43.34748, -81.48149, "11:20"],
            ["Grand Bend Sobeys", 43.31306, -81.75406, "11:48"]]}]}]}}
HSAT_BOX = {"min_lat": 42.8, "max_lat": 43.6, "min_lon": -82.0, "max_lon": -81.0}
NET_URL = "https://feeds.example.org/net.zip"
OFFLINE_ARGV = ["--origin", "43.0290,-81.2780", "--dest", "43.3125,-81.7569", "--depart", "2026-10-21 10:00",
                "--routing", "estimate"]


def blocked_network(hosts, timeout=6):
    return {h: "blocked: connection refused (x)" for h in hosts}


class OfflinePlan(unittest.TestCase):
    """docs-contract-4: with every probe host blocked, a trip whose timetables are all hand-entered or cached still
    plans (labelled OFFLINE) when both ends are 'lat,lon' and ride times are estimated; anything that needs a
    download still stops with NETWORK BLOCKED, and that message never asks to allow an empty list."""

    def setUp(self):
        fresh_cache(self)
        refused = urllib.error.URLError(ConnectionRefusedError())
        for p in (mock.patch.object(th, "check_network", side_effect=blocked_network),
                  mock.patch.object(th, "http_get", side_effect=refused),
                  mock.patch.object(th.urllib.request, "urlopen", side_effect=refused)):
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, argv, extra_feeds=()):
        reg = {"id": "t", "timezone": "America/Toronto", "bbox": HSAT_BOX,
               "feeds": [dict(HSAT, bbox=HSAT_BOX)] + list(extra_feeds)}
        with mock.patch.object(th, "load_regions", return_value=[reg]):
            return run_main(argv)

    def test_bundled_trip_plans_offline_with_a_warning(self):
        rc, out = self.run_with(OFFLINE_ARGV)
        self.assertEqual(rc, 0, rc)
        self.assertIn("BASELINE transit only", out)
        self.assertIn("London #705 -> Grand Bend Sobeys (10:41-11:48", out)
        self.assertTrue(any(ln.startswith("WARNING") and "OFFLINE" in ln for ln in out.splitlines()), out)

    def test_routing_auto_still_stops_and_names_the_router(self):
        code, _out = self.run_with(OFFLINE_ARGV[:-1] + ["auto"])
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertIn("router.project-osrm.org", code)

    def test_uncached_network_feed_still_stops(self):
        code, _out = self.run_with(OFFLINE_ARGV, [{"id": "net", "bbox": HSAT_BOX, "urls": [NET_URL]}])
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertIn("feeds.example.org", code)

    def test_cached_network_feed_counts_as_available(self):
        path = th._feed_cache_path("net", NET_URL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        make_feed(path, [("A", "Stop A", 43.03, -81.28), ("C", "Stop C", 43.31, -81.75)],
                  [("R1", "WK", "T1", "C")],
                  [("T1", "18:00:00", "18:00:00", "A", 1, 0, 0), ("T1", "18:30:00", "18:30:00", "C", 2, 0, 0)])
        old = time.time() - 10 * 86400
        os.utime(path, (old, old))
        rc, out = self.run_with(OFFLINE_ARGV, [{"id": "net", "bbox": HSAT_BOX, "urls": [NET_URL]}])
        self.assertEqual(rc, 0, rc)
        lines = [ln for ln in out.splitlines() if ln.startswith("WARNING")]
        self.assertTrue(any("OFFLINE" in ln for ln in lines), out)
        self.assertTrue(any("could not be refreshed" in ln for ln in lines), out)

    def test_blocked_message_never_prints_an_empty_list(self):
        msg = th.network_blocked_message(["a"], [])
        self.assertNotIn(":\n  \n", msg)
        self.assertNotIn("allow these domains", msg)
        self.assertIn(":\n  b\n", th.network_blocked_message(["a"], ["b"]))


class GuardProxy(unittest.TestCase):
    """tests-ci-eng-8: under the CI network guard, a proxy on the machine itself must not carry a request out. The
    guard allows loopback connections, and urllib sends every request to HTTP(S)_PROXY when one is set."""

    CHILD = ("import urllib.request, urllib.error\n"
             "try:\n"
             "    urllib.request.urlopen('http://example.invalid/', timeout=5); print('REACHED')\n"
             "except urllib.error.URLError as e:\n"
             "    print('refused', 'TRIP_PLANNER_NO_NETWORK' in str(e.reason))\n")

    def test_loopback_proxy_does_not_carry_requests_out(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(srv.close)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(15)
        seen = []

        def proxy():   # a permissive proxy: answers whatever it is asked
            try:
                conn, _addr = srv.accept()
            except (socket.timeout, OSError):
                return
            with conn:
                seen.append(conn.recv(200))
                conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok")

        t = threading.Thread(target=proxy, daemon=True)
        t.start()
        env = {k: v for k, v in os.environ.items()
               if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
        env.update({"PYTHONPATH": os.path.join(ROOT, "tools", "ci"), "TRIP_PLANNER_NO_NETWORK": "1",
                    "HTTP_PROXY": "http://127.0.0.1:%d" % srv.getsockname()[1]})
        r = subprocess.run([sys.executable, "-c", self.CHILD], env=env, capture_output=True, text=True, timeout=60)
        srv.close()
        t.join(5)
        self.assertEqual(r.stdout.strip(), "refused True", (r.stdout, r.stderr))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("example.invalid:80", r.stderr)
        self.assertEqual(seen, [])


class FakeResp(io.BytesIO):
    """What urlopen and dc._OPENER.open return."""
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


NOMINATIM = "https://nominatim.openstreetmap.org"


class SharedPacing(unittest.TestCase):
    """tests-ci-eng-9: trip_hubs.http_get and drive_common.fetch pace each host on one clock, so a Nominatim search
    from one module followed by a reverse lookup from the other still waits out the 1 request per second."""

    def pace(self, first, second):
        clock, slept = [1000.0], []
        p = [mock.patch.dict(th._LAST_CALL, clear=True), mock.patch.dict(dc._LAST_CALL, clear=True),
             mock.patch("time.time", lambda: clock[0]), mock.patch("time.sleep", slept.append),
             mock.patch.object(th.urllib.request, "urlopen", side_effect=lambda *a, **k: FakeResp(b"[]")),
             mock.patch.object(dc._OPENER, "open", side_effect=lambda *a, **k: FakeResp(b"{}"))]
        for x in p:
            x.start()
        try:
            first()
            second()
        finally:
            for x in reversed(p):
                x.stop()
        return slept

    def th_search(self):
        th.http_get(NOMINATIM + "/search?q=a", min_interval=1.1)

    def dc_reverse(self):
        dc.fetch(NOMINATIM + "/reverse?lat=1", min_interval=1.1)

    def test_dc_waits_after_th(self):
        slept = self.pace(self.th_search, self.dc_reverse)
        self.assertEqual(len(slept), 1, slept)
        self.assertAlmostEqual(slept[0], 1.1)

    def test_th_waits_after_dc(self):
        slept = self.pace(self.dc_reverse, self.th_search)
        self.assertEqual(len(slept), 1, slept)
        self.assertAlmostEqual(slept[0], 1.1)

    def test_other_host_not_held(self):
        slept = self.pace(self.th_search, lambda: dc.fetch("https://photon.komoot.io/api?q=a", min_interval=1.1))
        self.assertEqual(slept, [])


SKILL_DIR = os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner")


class ManualMethodPointer(unittest.TestCase):
    """docs-contract-8: NETWORK BLOCKED and the SKILL.md compatibility line point to references/manual-method.md,
    where 0.9.8 moved the manual method, not to SKILL.md."""

    def test_blocked_message_points_to_manual_method_file(self):
        for hosts in (["b"], []):
            msg = th.network_blocked_message(["a"], hosts)
            self.assertIn("references/manual-method.md", msg)
            self.assertIn("references/driving.md", msg)
            self.assertNotIn("in SKILL.md", msg)
        self.assertTrue(os.path.exists(os.path.join(SKILL_DIR, "references", "manual-method.md")))

    def test_skill_frontmatter_points_to_manual_method_file(self):
        with open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8") as fh:
            line = next(x for x in fh if x.startswith("compatibility:"))
        self.assertIn("references/manual-method.md", line)
        self.assertNotIn("in this file", line)


CATALOG_CSV = ("id,data_type,status,provider,name,location.country_code,location.subdivision_name,"
               "location.municipality,location.bounding_box.minimum_latitude,location.bounding_box.maximum_latitude,"
               "location.bounding_box.minimum_longitude,location.bounding_box.maximum_longitude,urls.latest,"
               "urls.direct_download,urls.authentication_type,is_official\n"
               "mdb-1,gtfs,active,Town Bus,,CA,ontario,Town,43.4,43.6,-79.9,-79.7,https://files.example/mdb-1.zip,,0,"
               "True\n")   # the one-row catalog of test_v098_hubs.CatalogAndDiscovery
CATALOG_CSV2 = CATALOG_CSV + ("mdb-2,gtfs,active,City Bus,,CA,ontario,City,43.4,43.6,-79.9,-79.7,"
                              "https://files.example/mdb-2.zip,,0,True\n")


class CatalogValidation(unittest.TestCase):
    """hubs-pricing-io-9: a MobilityDatabase catalog download that is not the CSV (a proxy or login page sent with
    HTTP 200) is never cached, the fallback URL is tried, a bad cached file counts as missing, the last good copy
    is kept, and --refresh re-downloads the catalog."""

    def setUp(self):
        fresh_cache(self)
        self.path = os.path.join(th.cache_dir(), "mobilitydatabase_feeds.csv")

    def write(self, data, age_days=0):
        with open(self.path, "wb") as fh:
            fh.write(data)
        t = time.time() - age_days * 86400
        os.utime(self.path, (t, t))

    def test_html_catalog_not_cached_and_fallback_tried(self):
        first = th.MOBILITYDB_CSV_URLS[0]
        get = lambda url, **k: b"<html>login</html>" if url == first else CATALOG_CSV.encode()  # noqa: E731
        with mock.patch.object(th, "http_get", side_effect=get) as hg:
            rows = th.load_catalog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(hg.call_count, 2)
        with open(self.path, "rb") as fh:
            self.assertTrue(fh.read().startswith(b"id,"))

    def test_bad_cached_catalog_is_replaced(self):
        self.write(b"<html>portal</html>")
        with mock.patch.object(th, "http_get", return_value=CATALOG_CSV.encode()) as hg:
            self.assertEqual(len(th.load_catalog()), 1)
        self.assertEqual(hg.call_count, 1)

    def test_header_only_catalog_is_rejected(self):
        header = CATALOG_CSV.split("\n")[0] + "\n"
        with mock.patch.object(th, "http_get", return_value=header.encode()):
            with self.assertRaises(SystemExit):
                th.load_catalog()
        self.assertFalse(os.path.exists(self.path))

    def test_refresh_redownloads(self):
        self.write(CATALOG_CSV.encode())
        with mock.patch.object(th, "http_get", return_value=CATALOG_CSV2.encode()) as hg:
            self.assertEqual(len(th.load_catalog(refresh=True)), 2)
        self.assertEqual(hg.call_count, 1)

    def test_all_bad_keeps_previous_good_copy(self):
        self.write(CATALOG_CSV.encode(), age_days=30)
        with mock.patch.object(th, "http_get", return_value=b"<html>proxy</html>"):
            self.assertEqual(len(th.load_catalog()), 1)
        os.remove(self.path)
        with mock.patch.object(th, "http_get", return_value=b"<html>proxy</html>"):
            with self.assertRaises(SystemExit) as cm:
                th.load_catalog()
        for url in th.MOBILITYDB_CSV_URLS:
            self.assertIn(url, str(cm.exception))

    def test_plan_passes_refresh_to_catalog(self):
        box = th.box_around(43.5, -79.8, 3000)
        with mock.patch.object(th, "load_catalog", return_value=[]) as lc:
            th.discover_feeds(box, refresh=True)
        self.assertEqual(lc.call_args[1].get("refresh"), True)

    def test_discover_command_passes_refresh(self):
        info = {"names": set(), "country": "CA", "state": None}
        with mock.patch.object(th, "load_catalog", return_value=[]) as lc,                 mock.patch.object(th, "reverse_place_info", return_value=info):
            run_main(["--discover", "--origin", "43.5000,-79.8000", "--refresh"])
        self.assertEqual(lc.call_args[1].get("refresh"), True)


class JsonCache(unittest.TestCase):
    """security-tos-5: drive_common.fetch_json never caches a reply that is not JSON (a proxy or login page sent
    with HTTP 200), a file poisoned before the fix is ignored, and the fuel CSV is checked the same way."""
    URL = "https://api.example.org/thing?x=1"

    def setUp(self):
        fresh_cache(self)
        for p in (mock.patch("time.sleep"), mock.patch.dict(dc._LAST_CALL, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def opener(self, *bodies):
        return mock.patch.object(dc._OPENER, "open", side_effect=[FakeResp(b) for b in bodies])

    def test_fetch_json_never_caches_html(self):
        with self.opener(b"<html>proxy</html>", b'{"a": 1}') as op:
            with self.assertRaises(dc.FetchError):
                dc.fetch_json(self.URL, max_age_h=720)
            self.assertEqual(dc.fetch_json(self.URL, max_age_h=720), {"a": 1})
        self.assertEqual(op.call_count, 2)

    def test_poisoned_cache_file_is_ignored(self):
        with open(dc._cache_path(self.URL, None), "wb") as fh:
            fh.write(b"<html>")
        with self.opener(b'{"a": 1}') as op:
            self.assertEqual(dc.fetch_json(self.URL, max_age_h=720), {"a": 1})
        self.assertEqual(op.call_count, 1)

    def test_own_validator_still_wins(self):
        with self.opener(b'{"remark": "runtime error"}'):
            with self.assertRaises(dc.FetchError):
                dc.fetch_json(self.URL, max_age_h=720, validate=lambda b: "remark" if b"remark" in b else None)

    def test_reverse_country_recovers_after_proxy_page(self):
        with self.opener(b"<html>proxy</html>", b'{"address": {"country_code": "ca"}}'):
            self.assertIsNone(dp.reverse_country(43.5, -79.8))
            self.assertEqual(dp.reverse_country(43.5, -79.8), "CA")

    def test_fuel_csv_html_not_cached(self):
        from test_drive_plan import FUEL_CSV
        with self.opener(b"<html>proxy</html>", FUEL_CSV.encode()):
            self.assertTrue(dp.fuel_price(43.65, -79.4, "regular", [])["fallback"])
            self.assertFalse(dp.fuel_price(43.65, -79.4, "regular", [])["fallback"])

    def test_toronto_feed_html_not_cached(self):
        legs = [("out", [(43.65, -79.40), (43.66, -79.39)], dt_(8), dt_(9))]
        good = json.dumps({"Closure": []}).encode()
        with self.opener(b"<html>proxy</html>", good) as op:
            w = []
            dp.toronto_incidents(legs, "America/Toronto", None, w)
            self.assertTrue(w)
            w = []
            dp.toronto_incidents(legs, "America/Toronto", None, w)
            self.assertEqual(w, [])
        self.assertEqual(op.call_count, 2)

    def test_nlr_html_not_cached(self):
        import ev_charging as evc
        with self.opener(b"<html>proxy</html>", b'{"fuel_stations": []}'):
            self.assertIsNone(evc._nlr_call("nearest.json", {"latitude": 43.5}, "DEMO_KEY", []))
            self.assertEqual(evc._nlr_call("nearest.json", {"latitude": 43.5}, "DEMO_KEY", []), [])

    def test_nrcan_package_html_not_cached(self):
        import vehicles
        with mock.patch.object(vehicles, "_TRIED_DOWNLOAD", set()), self.opener(b"<html>proxy</html>"):
            self.assertFalse(vehicles._download_year(2031, []))
        self.assertFalse(os.path.exists(dc._cache_path(vehicles.PACKAGE_API, None)))


class SafeTextInvisible(unittest.TestCase):
    """security-tos-3: safe_text removes invisible characters (tag characters, zero-width marks, LRM/RLM/ALM, BOM,
    soft hyphen, private use, variation selectors 17-256), so text an agent reads matches what a person sees;
    line and paragraph separators become spaces like the controls."""

    def test_invisible_characters_removed(self):
        s = "Main​ Lot\U000e0041\U000e0042 X﻿‎‏؜\xad\U000e0101"
        self.assertEqual(th.safe_text(s), "Main Lot X")
        self.assertEqual(th.safe_text("a b"), "a b")

    def test_visible_text_unchanged(self):
        for s in ("Grand Bend Sobeys", "Montréal Trudeau", "東京駅", "café ☕️",
                  "Ελληνικά", "Café à l'Étoile"):
            self.assertEqual(th.safe_text(s), s)

    def test_controls_and_bidi_still_spaced(self):
        self.assertEqual(th.safe_text("a\x1bb‮c"), "a b c")
        self.assertIsNone(th.safe_text(None))
        self.assertEqual(th.safe_text(12), "12")

    def test_drive_output_strips_tag_characters(self):
        self.assertEqual(dp.plain_text(["Lot\U000e0041 A"]), "Lot A")


BUILDING = [{"lat": "43.6", "lon": "-79.4", "addresstype": "building", "display_name": "1 Front St W, Toronto"}]


class GeocodeCache(unittest.TestCase):
    """security-tos-7: forward and reverse geocoding are cached on disk for 30 days (Nominatim's usage policy asks
    clients to cache), keyed on the whitespace-normalised query; errors and empty results are asked again."""

    def setUp(self):
        fresh_cache(self)
        for p in (mock.patch("time.sleep"), mock.patch.dict(th._LAST_CALL, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def replies(self, **by_host):
        """th.http_get answering from a queue per host ('nominatim', 'photon'); an exception in a queue is raised."""
        calls = {k: 0 for k in by_host}

        def get(url, **k):
            host = next(h for h in by_host if h in url)
            calls[host] += 1
            r = by_host[host].pop(0)
            if isinstance(r, Exception):
                raise r
            return json.dumps(r).encode()
        return mock.patch.object(th, "http_get", side_effect=get), calls

    def test_same_address_geocoded_once(self):
        with mock.patch.object(th, "http_get", return_value=json.dumps(BUILDING).encode()) as hg:
            for _ in range(3):
                lat, lon, _name = th.resolve_place("1 Front St W, Toronto", "origin", [])
                self.assertEqual((lat, lon), (43.6, -79.4))
        self.assertEqual(hg.call_count, 1)
        self.assertTrue(os.listdir(os.path.join(th.cache_dir(), "geocode")))

    def test_whitespace_variants_share_the_cache(self):
        with mock.patch.object(th, "http_get", return_value=json.dumps(BUILDING).encode()) as hg:
            th.resolve_place("1 Front St W, Toronto", "origin", [])
            th.resolve_place("1 Front St W,  Toronto ", "origin", [])
        self.assertEqual(hg.call_count, 1)

    def test_failures_and_empty_results_not_cached(self):
        refused = urllib.error.URLError(ConnectionRefusedError())
        patch, calls = self.replies(nominatim=[refused, [], BUILDING], photon=[refused, {"features": []}])
        with patch:
            for _ in range(2):
                with self.assertRaises(SystemExit):
                    th.resolve_place("1 Front St W, Toronto", "origin", [])
            self.assertEqual(th.resolve_place("1 Front St W, Toronto", "origin", [])[:2], (43.6, -79.4))
        self.assertEqual(calls, {"nominatim": 3, "photon": 2})

    def test_photon_reply_cached(self):
        street = [{"lat": "43.6", "lon": "-79.4", "addresstype": "road", "display_name": "Front St W, Toronto"}]
        house = {"features": [{"geometry": {"coordinates": [-79.401, 43.601]},
                               "properties": {"type": "house", "housenumber": "1", "street": "Front St W",
                                              "city": "Toronto"}}]}
        patch, calls = self.replies(nominatim=[street], photon=[house])
        with patch:
            for _ in range(2):
                self.assertEqual(th.resolve_place("1 Front St W, Toronto", "origin", [])[:2], (43.601, -79.401))
        self.assertEqual(calls, {"nominatim": 1, "photon": 1})

    def test_reverse_place_info_cached(self):
        reply = {"address": {"town": "Milton", "country_code": "ca", "state": "Ontario"}}
        with mock.patch.object(th, "http_get", return_value=json.dumps(reply).encode()) as hg:
            for _ in range(2):
                self.assertEqual(th.reverse_place_info(43.5, -79.8, [])["country"], "CA")
        self.assertEqual(hg.call_count, 1)


def busy():
    """Overpass's HTTP 429 with a body, as urllib raises it."""
    return urllib.error.HTTPError(th.OVERPASS_URL, 429, "Too Many Requests", {}, io.BytesIO(b"busy"))


class HttpErrorsClosed(unittest.TestCase):
    """gem-G4: the Overpass retry loops close the HTTPError they drop (its body is never read); dc.fetch still
    re-raises an HTTPError unread, because place_reviews reads the error body."""

    def setUp(self):
        fresh_cache(self)
        for p in (mock.patch("time.sleep"), mock.patch.dict(th._LAST_CALL, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_parking_overpass_retry_closes_busy_reply(self):
        import parking as pk
        err = busy()
        with mock.patch.object(dc, "fetch_json", side_effect=[err, {"elements": []}]):
            self.assertEqual(pk._overpass("q", []), {"elements": []})
        self.assertTrue(err.fp.closed)

    def test_parking_overpass_final_failure_closes(self):
        import parking as pk
        errs, w = [busy(), busy()], []
        with mock.patch.object(dc, "fetch_json", side_effect=list(errs)):
            self.assertIsNone(pk._overpass("q", w))
        self.assertTrue(any("HTTP 429" in x for x in w), w)
        self.assertTrue(all(e.fp.closed for e in errs))

    def test_hubs_overpass_retry_closes_busy_reply(self):
        err = busy()
        with mock.patch.object(th, "http_get", side_effect=[err, json.dumps({"elements": []}).encode()]):
            self.assertEqual(th.overpass_parking(th.box_around(43.5, -79.8, 3000), []), [])
        self.assertTrue(err.fp.closed)

    def test_hubs_overpass_final_failure_closes(self):
        errs, w = [busy(), busy()], []
        with mock.patch.object(th, "http_get", side_effect=list(errs)):
            self.assertIsNone(th.overpass_parking(th.box_around(43.5, -79.8, 3000), w))
        self.assertTrue(any("HTTP Error 429" in x for x in w), w)
        self.assertTrue(all(e.fp.closed for e in errs))

    def test_place_reviews_can_still_read_error_body(self):
        import place_reviews
        body = io.BytesIO(b'{"error": {"status": "PERMISSION_DENIED"}}')
        err = urllib.error.HTTPError("https://places.example/v1", 403, "Forbidden", {}, body)
        with mock.patch.object(dc._OPENER, "open", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                dc.fetch("https://places.example/v1", data=b"{}")
        self.assertIn("PERMISSION_DENIED", place_reviews._http_error(cm.exception))


class GeocodeCacheFolder(unittest.TestCase):
    """Follow-up to security-tos-7: when the cache folder cannot be created, geocoder replies are simply not cached
    (as driving.md and drive_common.fetch say); the lookup itself still runs and answers."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(_shutil.rmtree, d, True)
        blocker = os.path.join(d, "a-file")
        open(blocker, "w").close()
        for p in (mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": os.path.join(blocker, "sub")}),
                  mock.patch("time.sleep"), mock.patch.dict(th._LAST_CALL, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_address_geocodes_without_a_cache_folder(self):
        with mock.patch.object(th, "http_get", return_value=json.dumps(BUILDING).encode()) as hg:
            for _ in range(2):
                self.assertEqual(th.resolve_place("1 Front St W, Toronto", "origin", [])[:2], (43.6, -79.4))
        self.assertEqual(hg.call_count, 2)   # nothing cached, both asked

    def test_reverse_place_info_without_a_cache_folder(self):
        reply = {"address": {"town": "Milton", "country_code": "ca", "state": "Ontario"}}
        warnings = []
        with mock.patch.object(th, "http_get", return_value=json.dumps(reply).encode()) as hg:
            self.assertEqual(th.reverse_place_info(43.5, -79.8, warnings)["country"], "CA")
        self.assertEqual(hg.call_count, 1)
        self.assertEqual(warnings, [])


def tls_network(hosts, timeout=6):
    return {h: "tls failed: x" for h in hosts}


class PreflightTlsAndOffline(unittest.TestCase):
    """Follow-ups to hubs-pricing-io-4 and docs-contract-4: a certificate failure does not stop a trip that plans
    offline; only a failed certificate check is TLS FAILED (a handshake cut or refused by a filter or proxy is a
    blocked network); a mix of TLS and blocked probes is NETWORK BLOCKED with the certificate fix in the note; and
    with nothing to allow, NETWORK BLOCKED names what keeps the trip from planning offline."""

    def setUp(self):
        fresh_cache(self)
        refused = urllib.error.URLError(ConnectionRefusedError())
        for p in (mock.patch.object(th, "http_get", side_effect=refused),
                  mock.patch.object(th, "reverse_place_info",
                                    side_effect=AssertionError("no reverse geocoding expected"))):
            p.start()
            self.addCleanup(p.stop)

    def test_bundled_trip_plans_offline_when_certificates_fail(self):
        reg = {"id": "t", "timezone": "America/Toronto", "bbox": HSAT_BOX, "feeds": [dict(HSAT, bbox=HSAT_BOX)]}
        with mock.patch.object(th, "check_network", side_effect=tls_network), \
                mock.patch.object(th, "load_regions", return_value=[reg]):
            rc, out = run_main(OFFLINE_ARGV)
        self.assertEqual(rc, 0, rc)
        self.assertIn("BASELINE transit only", out)
        lines = [ln for ln in out.splitlines() if ln.startswith("WARNING")]
        self.assertTrue(any("OFFLINE" in ln for ln in lines), out)
        self.assertTrue(any("cannot verify HTTPS certificates" in ln for ln in lines), out)

    def test_handshake_cut_by_a_filter_is_not_a_certificate_problem(self):
        for err in (ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:1131)"),
                    ssl.SSLError(1, "[SSL: TLSV1_ALERT_ACCESS_DENIED] tlsv1 alert access denied (_ssl.c:1131)"),
                    ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1131)")):
            e = urllib.error.URLError(err)
            self.assertFalse(th._tls_failed(e), err)
            self.assertTrue(th._unreachable(e), err)
            self.assertEqual(th._net_cause(e), "TLS handshake refused")
        self.assertTrue(th._tls_failed(CERT))
        self.assertTrue(th._tls_failed(urllib.error.URLError(ssl.SSLError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:1131)"))))
        eof = urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:1131)"))
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=eof):
            code, _out = run_main(["--origin", "43.4988,-79.8560"] + TRIP)
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertIn("TLS handshake refused", code)
        self.assertNotIn("cannot verify HTTPS certificates", code)

    def test_one_tls_and_two_blocked_probes_give_network_blocked(self):
        def status(hosts, timeout=6):
            return {h: ("tls failed: x" if h == "nominatim.openstreetmap.org" else "blocked: a proxy refused (x)")
                    for h in hosts}
        ns = th.argparse.Namespace(origin="43.4988,-79.8560", dest="43.5812,-79.6475", region="auto",
                                   routing="osrm", crosscheck=False, feed=None, no_discover=False, access="ride",
                                   radius_km=None, dest_walk_m=1500)
        with mock.patch.object(th, "check_network", side_effect=status):
            with self.assertRaises(SystemExit) as ctx:
                th.preflight(ns, th.load_regions(), [])
        code = str(ctx.exception.code)
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertIn("router.project-osrm.org", code)
        self.assertIn("a proxy refused", code)
        self.assertIn("The certificate check failed for nominatim.openstreetmap.org", code)

    def test_nothing_to_allow_names_why_the_trip_cannot_plan_offline(self):
        with mock.patch.object(th, "check_network", side_effect=blocked_network):
            code, _out = run_main(["--origin", "45.55,-78.45", "--dest", "45.62,-78.30",
                                   "--depart", "2026-10-21 10:00", "--routing", "estimate"])
        self.assertTrue(code.startswith("NETWORK BLOCKED"), code)
        self.assertNotIn("plans offline", code)
        self.assertIn("no feed in the region files is near this trip", code)
        self.assertIn("--discover", code)


if __name__ == "__main__":
    unittest.main()
