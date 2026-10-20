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
import io
import os
import socket
import ssl
import sys
import tempfile
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




if __name__ == "__main__":
    unittest.main()
