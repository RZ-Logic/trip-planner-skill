"""Tests for the HTTP layer under every script: drive_common.fetch (disk cache, stale stand-ins, validate, key
headers on redirects, an unusable cache folder, two writers on one cache file), trip_hubs.http_get and
trip_hubs.fetch_url (GTFS downloads).

Every request goes to a small HTTP server on 127.0.0.1 started by this file; nothing leaves the machine.

Run: python -m unittest tests.test_fetch -v
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
import http.server
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner", "scripts"))
import drive_common as dc  # noqa: E402

th = dc.th


class Server:
    """What the local servers answer: path -> (status, headers, body), plus every request they saw."""
    routes = {}
    seen = []      # (port, path, headers dict, body bytes)
    lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        sent = self.rfile.read(n) if n else b""
        path = urllib.parse.urlsplit(self.path).path
        with Server.lock:
            Server.seen.append((self.server.server_address[1], path, dict(self.headers), sent))
            status, headers, body = Server.routes.get(path, (404, {}, b"not here"))
        if callable(body):
            body = body()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


def gtfs_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nA,Alpha,43.5,-79.8\n")
        z.writestr("trips.txt", "route_id,service_id,trip_id\nR,WK,t1\n")
        z.writestr("stop_times.txt", "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nt1,10:00:00,10:00:00,A,1\n")
    return buf.getvalue()


class LocalHttp(unittest.TestCase):
    """Two servers on 127.0.0.1 (different ports, so a redirect between them is a cross-host redirect), a fresh
    cache folder per test and the module's run state (stale notes, pacing, cache-off flag) reset."""

    @classmethod
    def setUpClass(cls):
        cls.servers = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
        cls.threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                       for s in cls.servers]
        for t in cls.threads:
            t.start()
        cls.a = "http://127.0.0.1:%d" % cls.servers[0].server_address[1]
        cls.b = "http://127.0.0.1:%d" % cls.servers[1].server_address[1]

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        for t in cls.threads:
            t.join(5)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(_shutil.rmtree, self.tmp, True)
        self.cache = os.path.join(self.tmp, "cache")
        Server.routes, Server.seen = {}, []
        patches = [mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": self.cache,
                                                "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}),
                   mock.patch.object(dc, "_CACHE_OFF", []), mock.patch.dict(dc._LAST_CALL, clear=True),
                   mock.patch.dict(th._LAST_CALL, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        dc.stale_notes()                     # nothing left over from another test
        self.addCleanup(dc.stale_notes)

    def route(self, path, body=b'{"ok": 1}', status=200, headers=None):
        Server.routes[path] = (status, headers or {}, body)

    def hits(self, path):
        return [s for s in Server.seen if s[1] == path]

    def cache_files(self):
        d = os.path.join(self.cache, "driving")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def age(self, hours):
        """Make every cached reply `hours` old."""
        for name in self.cache_files():
            p = os.path.join(self.cache, "driving", name)
            t = time.time() - hours * 3600
            os.utime(p, (t, t))

    def assertRaisesHttp(self, code, fn, *a, **kw):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            fn(*a, **kw)
        ctx.exception.close()               # the error holds the server's reply open
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception


class FetchCache(LocalHttp):
    def test_fresh_cache_answers_without_a_request(self):
        self.route("/x", b'{"n": 1}')
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"n": 1}')
        self.route("/x", b'{"n": 2}')
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"n": 1}')
        self.assertEqual(len(self.hits("/x")), 1)
        self.assertEqual(dc.stale_notes(), [])

    def test_expired_copy_is_fetched_again(self):
        self.route("/x", b'{"n": 1}')
        dc.fetch(self.a + "/x", max_age_h=1, timeout=10)
        self.age(2)
        self.route("/x", b'{"n": 2}')
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"n": 2}')
        self.assertEqual(len(self.hits("/x")), 2)

    def test_no_max_age_never_caches(self):
        self.route("/x")
        dc.fetch(self.a + "/x", timeout=10)
        dc.fetch(self.a + "/x", timeout=10)
        self.assertEqual(len(self.hits("/x")), 2)
        self.assertEqual(self.cache_files(), [])

    def test_cache_key_replaces_the_url(self):
        self.route("/x", b'{"n": 1}')
        dc.fetch(self.a + "/x?key=ONE", max_age_h=1, cache_key="x-route", timeout=10)
        self.assertEqual(dc.fetch(self.a + "/x?key=TWO", max_age_h=1, cache_key="x-route", timeout=10),
                         b'{"n": 1}')
        self.assertEqual(len(self.hits("/x")), 1)

    def test_post_form_body_and_headers(self):
        self.route("/q")
        dc.fetch(self.a + "/q", data={"data": "[out:json];"}, timeout=10)
        _, _, headers, sent = self.hits("/q")[0]
        self.assertEqual(sent, b"data=%5Bout%3Ajson%5D%3B")
        self.assertEqual(headers.get("Content-Type"), "application/x-www-form-urlencoded")
        self.assertEqual(headers.get("User-Agent"), th.USER_AGENT)

    def test_post_bodies_are_cached_apart(self):
        self.route("/q", lambda: str(len(Server.seen)).encode())
        one = dc.fetch(self.a + "/q", data="a", max_age_h=1, timeout=10)
        two = dc.fetch(self.a + "/q", data="b", max_age_h=1, timeout=10)
        self.assertNotEqual(one, two)
        self.assertEqual(dc.fetch(self.a + "/q", data="a", max_age_h=1, timeout=10), one)

    def test_min_interval_paces_the_host(self):
        self.route("/x")
        with mock.patch.object(dc.time, "sleep") as sl:
            dc.fetch(self.a + "/x", min_interval=30, timeout=10)
            dc.fetch(self.a + "/x", min_interval=30, timeout=10)
        self.assertEqual(sl.call_count, 1)
        self.assertGreater(sl.call_args[0][0], 25)


class FetchStale(LocalHttp):
    def cached_then_down(self, hours):
        self.route("/x", b'{"n": 1}')
        dc.fetch(self.a + "/x", max_age_h=1, timeout=10)
        self.age(hours)
        self.route("/x", b"broken", status=500)

    def test_any_age_stand_in_by_default(self):
        self.cached_then_down(100)
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"n": 1}')
        notes = dc.stale_notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("127.0.0.1:", notes[0])
        self.assertIn("HTTPError", notes[0])
        self.assertIn("4 days ago", notes[0])
        self.assertEqual(dc.stale_notes(), [])          # reported once

    def test_stale_max_zero_never_stands_in(self):
        self.cached_then_down(2)
        self.assertRaisesHttp(500, dc.fetch, self.a + "/x", max_age_h=1, stale_max_h=0, timeout=10)
        self.assertEqual(dc.stale_notes(), [])

    def test_stale_max_hours_limits_the_age(self):
        self.cached_then_down(3)
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, stale_max_h=6, timeout=10), b'{"n": 1}')
        self.assertEqual(len(dc.stale_notes()), 1)
        self.assertRaisesHttp(500, dc.fetch, self.a + "/x", max_age_h=1, stale_max_h=2, timeout=10)
        self.assertEqual(dc.stale_notes(), [])

    def test_unreachable_host_uses_the_copy(self):
        self.cached_then_down(2)
        with mock.patch.object(dc._OPENER, "open", side_effect=urllib.error.URLError("refused")):
            self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"n": 1}')
        self.assertIn("URLError", dc.stale_notes()[0])

    def test_no_copy_raises(self):
        self.route("/x", b"broken", status=503)
        self.assertRaisesHttp(503, dc.fetch, self.a + "/x", max_age_h=1, timeout=10)
        self.assertEqual(dc.stale_notes(), [])


def no_remark(body):
    return "the query timed out" if b"remark" in body else None


class FetchValidate(LocalHttp):
    def test_rejected_reply_raises_and_is_not_cached(self):
        self.route("/x", b'{"remark": "runtime error: timeout"}')
        with self.assertRaises(dc.FetchError) as ctx:
            dc.fetch(self.a + "/x", max_age_h=1, validate=no_remark, timeout=10)
        self.assertEqual(str(ctx.exception), "the query timed out")
        self.assertEqual(self.cache_files(), [])
        self.route("/x", b'{"elements": []}')
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, validate=no_remark, timeout=10), b'{"elements": []}')
        self.assertEqual(len(self.hits("/x")), 2)

    def test_rejected_reply_falls_back_to_a_good_copy(self):
        self.route("/x", b'{"elements": [1]}')
        dc.fetch(self.a + "/x", max_age_h=1, validate=no_remark, timeout=10)
        self.age(5)
        self.route("/x", b'{"remark": "timeout"}')
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, validate=no_remark, timeout=10), b'{"elements": [1]}')
        self.assertIn("FetchError", dc.stale_notes()[0])

    def test_fresh_copy_that_fails_the_check_is_asked_again(self):
        self.route("/x", b'{"remark": "old bad copy"}')
        dc.fetch(self.a + "/x", max_age_h=1, timeout=10)          # cached by a caller without a check
        self.route("/x", b'{"elements": []}')
        self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, validate=no_remark, timeout=10), b'{"elements": []}')
        self.assertEqual(len(self.hits("/x")), 2)

    def test_old_copy_that_fails_the_check_never_stands_in(self):
        self.route("/x", b'{"remark": "bad"}')
        dc.fetch(self.a + "/x", max_age_h=1, timeout=10)
        self.age(5)
        self.route("/x", b"down", status=500)
        self.assertRaisesHttp(500, dc.fetch, self.a + "/x", max_age_h=1, validate=no_remark, timeout=10)
        self.assertEqual(dc.stale_notes(), [])

    def test_validate_that_raises_counts_as_a_rejection(self):
        self.route("/x", b"not json")
        with self.assertRaises(dc.FetchError) as ctx:
            dc.fetch(self.a + "/x", max_age_h=1, validate=lambda body: __import__("json").loads(body), timeout=10)
        self.assertIn("unreadable reply", str(ctx.exception))


class FetchRedirects(LocalHttp):
    KEYS = {"X-Api-Key": "SECRET_1", "X-Goog-Api-Key": "SECRET_2", "Authorization": "Bearer SECRET_3"}

    def test_cross_host_redirect_drops_key_headers(self):
        self.route("/go", b"", status=302, headers={"Location": self.b + "/landed"})
        self.route("/landed", b'{"ok": 1}')
        self.assertEqual(dc.fetch(self.a + "/go", headers=self.KEYS, timeout=10), b'{"ok": 1}')
        first, landed = self.hits("/go")[0][2], self.hits("/landed")[0][2]
        self.assertEqual(first.get("X-Api-Key"), "SECRET_1")
        lower = {k.lower() for k in landed}
        self.assertFalse(lower & {"x-api-key", "x-goog-api-key", "authorization"})
        self.assertEqual(landed.get("User-Agent"), th.USER_AGENT)
        self.assertNotIn("SECRET", repr(landed))

    def test_same_host_redirect_keeps_them(self):
        self.route("/go", b"", status=302, headers={"Location": self.a + "/landed"})
        self.route("/landed", b'{"ok": 1}')
        dc.fetch(self.a + "/go", headers=self.KEYS, timeout=10)
        landed = self.hits("/landed")[0][2]
        self.assertEqual(landed.get("X-Api-Key"), "SECRET_1")
        self.assertEqual(landed.get("X-Goog-Api-Key"), "SECRET_2")

    def test_allowed_hosts_refuse_a_redirect_elsewhere(self):
        port = self.servers[1].server_address[1]
        self.route("/go", b"", status=302, headers={"Location": "http://localhost:%d/landed" % port})
        self.route("/landed", b'{"ok": 1}')
        self.assertRaisesHttp(302, dc.fetch, self.a + "/go", timeout=10, allowed_hosts=("127.0.0.1",))
        self.assertEqual(self.hits("/landed"), [])

    def test_allowed_hosts_follow_a_redirect_inside_the_list(self):
        self.route("/go", b"", status=302, headers={"Location": self.b + "/landed"})
        self.route("/landed", b'{"ok": 1}')
        self.assertEqual(dc.fetch(self.a + "/go", timeout=10, allowed_hosts=("127.0.0.1",)), b'{"ok": 1}')

    def test_allowed_hosts_refuse_the_url_itself(self):
        with self.assertRaises(ValueError):
            dc.fetch(self.a + "/go", timeout=10, allowed_hosts=("open.canada.ca",))
        self.assertEqual(Server.seen, [])


class FetchCacheFolder(LocalHttp):
    def test_unusable_cache_folder_still_fetches(self):
        blocker = os.path.join(self.tmp, "a-file")
        open(blocker, "w").close()
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": os.path.join(blocker, "sub")}), \
                mock.patch.object(th, "log") as log:
            self.route("/x")
            self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"ok": 1}')
            self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"ok": 1}')
        self.assertEqual(len(self.hits("/x")), 2)                  # nothing cached, both asked
        self.assertEqual(log.call_count, 1)                        # said once per run
        self.assertIn("not cached", log.call_args[0][0])

    def test_two_threads_writing_the_same_key(self):
        self.route("/x", b'{"same": true}')
        errors = []

        def worker():
            try:
                for _ in range(15):
                    self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1e-9, timeout=10), b'{"same": true}')
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        files = self.cache_files()
        self.assertEqual(len(files), 1, files)                     # no temporary files left behind
        with open(os.path.join(self.cache, "driving", files[0]), "rb") as f:
            self.assertEqual(f.read(), b'{"same": true}')

    def test_failed_cache_write_still_returns_the_reply(self):
        self.route("/x")
        with mock.patch.object(dc.tempfile, "mkstemp", side_effect=PermissionError("locked")):
            self.assertEqual(dc.fetch(self.a + "/x", max_age_h=1, timeout=10), b'{"ok": 1}')
        self.assertEqual(self.cache_files(), [])


class HttpGet(LocalHttp):
    def test_body_and_user_agent(self):
        self.route("/g", b"hello")
        self.assertEqual(th.http_get(self.a + "/g", timeout=10), b"hello")
        self.assertEqual(self.hits("/g")[0][2].get("User-Agent"), th.USER_AGENT)

    def test_http_error_raises(self):
        self.route("/g", b"nope", status=404)
        self.assertRaisesHttp(404, th.http_get, self.a + "/g", timeout=10)

    def test_min_interval_paces_the_host(self):
        self.route("/g", b"x")
        with mock.patch.object(th.time, "sleep") as sl:
            th.http_get(self.a + "/g", timeout=10, min_interval=20)
            th.http_get(self.a + "/g", timeout=10, min_interval=20)
            th.http_get(self.b + "/g", timeout=10, min_interval=20)      # another host is not held back
        self.assertEqual(sl.call_count, 1)
        self.assertGreater(sl.call_args[0][0], 15)


class FetchUrl(LocalHttp):
    def setUp(self):
        super().setUp()
        self.zip = gtfs_zip()

    def test_download_then_cache_then_refresh(self):
        self.route("/feed.zip", self.zip)
        url = self.a + "/feed.zip"
        path = th.fetch_url("f", url, 24, False)
        self.assertTrue(path.startswith(self.cache) and path.endswith(".zip"))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), self.zip)
        self.assertEqual(th.fetch_url("f", url, 24, False), path)
        self.assertEqual(len(self.hits("/feed.zip")), 1)
        self.assertEqual(th.fetch_url("f", url, 24, True), path)    # --refresh
        self.assertEqual(len(self.hits("/feed.zip")), 2)
        self.assertEqual([n for n in os.listdir(self.cache) if n.endswith(".part")], [])

    def test_local_path_is_used_as_is(self):
        p = os.path.join(self.tmp, "mine.zip")
        with open(p, "wb") as f:
            f.write(self.zip)
        self.assertEqual(th.fetch_url("f", p, 24, False), p)
        self.assertEqual(Server.seen, [])

    def test_error_page_is_refused_and_not_kept(self):
        self.route("/feed.zip", b"<html>maintenance</html>")
        with self.assertRaises(ValueError) as ctx:
            th.fetch_url("f", self.a + "/feed.zip", 24, False)
        self.assertIn("not a zip file", str(ctx.exception))
        self.assertFalse(os.path.isdir(self.cache) and os.listdir(self.cache))

    def test_zip_without_timetable_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hello")
        self.route("/feed.zip", buf.getvalue())
        with self.assertRaises(ValueError) as ctx:
            th.fetch_url("f", self.a + "/feed.zip", 24, False)
        self.assertIn("not a GTFS zip", str(ctx.exception))

    def test_failed_refresh_keeps_the_old_copy_with_a_warning(self):
        self.route("/feed.zip", self.zip)
        url = self.a + "/feed.zip?token=SECRET_KEY_9"
        path = th.fetch_url("f", url, 24, False)
        self.route("/feed.zip", b"down", status=500)
        warnings = []
        self.assertEqual(th.fetch_url("f", url, 24, True, warnings), path)
        self.assertEqual(len(warnings), 1)
        self.assertIn("used a cached copy", warnings[0])
        self.assertNotIn("SECRET_KEY_9", warnings[0])

    def test_failed_download_without_a_copy_raises(self):
        self.route("/feed.zip", b"down", status=500)
        self.assertRaisesHttp(500, th.fetch_url, "f", self.a + "/feed.zip", 24, False)


class QuietLog(unittest.TestCase):
    """TRIP_PLANNER_QUIET=1 silences the progress lines of every script (they all log through th.log)."""

    def logged(self, value):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_QUIET": value}), mock.patch.object(sys, "stderr", err):
            th.log("routing 1,2 -> 3,4")
            __import__("drive_plan").log("park and ride: running trip_hubs in car mode")
        return err.getvalue()

    def test_quiet(self):
        self.assertEqual(self.logged("1"), "")
        self.assertEqual(self.logged(" 1 "), "")

    def test_not_quiet(self):
        for value in ("0", "", "yes"):
            self.assertEqual(self.logged(value), "routing 1,2 -> 3,4\npark and ride: running trip_hubs in car mode\n")


GUARD = os.path.join(ROOT, "tools", "ci", "sitecustomize.py")


class NetworkGuard(unittest.TestCase):
    """tools/ci/sitecustomize.py, the CI network guard (run in a child Python so this process is not patched)."""

    def test_loopback_names(self):
        spec = importlib.util.spec_from_file_location("trip_planner_ci_guard", GUARD)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for host in ("127.0.0.1", "127.8.0.1", "::1", "[::1]", "localhost", "LOCALHOST", "api.localhost",
                     "::ffff:127.0.0.1", b"127.0.0.1"):
            self.assertTrue(guard._loopback(host), host)
        for host in ("8.8.8.8", "10.0.0.1", "example.com", "localhost.example.com", "", None, "::"):
            self.assertFalse(guard._loopback(host), host)

    def test_child_python_refuses_the_internet_and_allows_loopback(self):
        code = "\n".join([
            "import socket, sys, time",
            "sys.path.insert(0, %r)" % os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner",
                                                    "scripts"),
            "srv = socket.socket(); srv.bind(('127.0.0.1', 0)); srv.listen(1)",
            "c = socket.create_connection(srv.getsockname(), timeout=5); c.close(); srv.close()",
            "t = time.time()",
            "for target in (('example.invalid', 443), ('203.0.113.9', 80)):",
            "    try:",
            "        socket.create_connection(target, timeout=5)",
            "        print('CONNECTED', target)",
            "    except ConnectionRefusedError as e:",
            "        print('refused', 'TRIP_PLANNER_NO_NETWORK' in str(e))",
            "s = socket.socket()",
            "try:",
            "    s.connect(('203.0.113.9', 80))",
            "except ConnectionRefusedError:",
            "    print('refused raw')",
            "finally:",
            "    s.close()",
            "import trip_hubs as th",
            "print(th.check_network(['example.invalid'])['example.invalid'][:8])",
            "print('fast', time.time() - t < 3)",
        ])
        env = dict(os.environ, PYTHONPATH=os.path.dirname(GUARD), TRIP_PLANNER_NO_NETWORK="1",
                   TRIP_PLANNER_QUIET="1")
        out = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=60)
        self.assertEqual(out.stdout.split("\n")[:5], ["refused True", "refused True", "refused raw", "blocked:",
                                                     "fast True"])
        # a run that tried the network fails at exit and lists what it tried
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("connection attempt(s) refused: 203.0.113.9:80, example.invalid:443", out.stderr)

    def test_child_with_loopback_only_exits_cleanly(self):
        code = "\n".join([
            "import socket",
            "srv = socket.socket(); srv.bind(('127.0.0.1', 0)); srv.listen(1)",
            "c = socket.create_connection(srv.getsockname(), timeout=5); c.close(); srv.close()",
            "print('ok')",
        ])
        env = dict(os.environ, PYTHONPATH=os.path.dirname(GUARD), TRIP_PLANNER_NO_NETWORK="1")
        out = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=60)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "ok"), out.stderr)
        self.assertNotIn("network guard", out.stderr)


class Python38HttpErrorClose(unittest.TestCase):
    """On Python 3.8 closing an HTTPError made without a body raises KeyError('file') (CI caught it there).
    Every place that closes one must survive that; the newer Pythons here simulate it with a patch."""

    def setUp(self):
        p = mock.patch.object(urllib.error.HTTPError, "close", side_effect=KeyError("file"))
        p.start()
        self.addCleanup(p.stop)

    def err(self, code):
        return urllib.error.HTTPError("https://example.invalid/x", code, "x", {}, None)

    def test_close_quietly(self):
        th.close_quietly(self.err(500))

    def test_check_network(self):
        with mock.patch.object(th.urllib.request, "urlopen", side_effect=self.err(404)):
            self.assertEqual(th.check_network(["example.invalid"]), {"example.invalid": "ok"})

    def test_nrcan_download_warning(self):
        import vehicles as V
        self.assertIn("HTTP 403", V._download_warning("ratings", self.err(403)))

    def test_nlr_error(self):
        import ev_charging as ev
        warnings = []
        with mock.patch.object(dc, "fetch", side_effect=self.err(429)):
            self.assertIsNone(ev._nlr_call("/x.json", {"a": 1}, "DEMO_KEY", warnings))
        self.assertTrue(any("hourly limit" in w for w in warnings), warnings)

    def test_stale_stand_in(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(_shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"TRIP_PLANNER_CACHE": tmp}), mock.patch.object(dc, "_CACHE_OFF", []):
            path = dc._cache_path("https://example.invalid/y", None)
            with open(path, "wb") as f:
                f.write(b"old")
            old = time.time() - 7200
            os.utime(path, (old, old))
            with mock.patch.object(dc._OPENER, "open", side_effect=self.err(500)):
                self.assertEqual(dc.fetch("https://example.invalid/y", max_age_h=1), b"old")
        dc.stale_notes()


if __name__ == "__main__":
    unittest.main()
