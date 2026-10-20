"""Shared helpers for the driving planner (drive_plan.py, tolls.py, parking.py, ev_charging.py).

Standard library only. Python 3.8+. Reuses trip_hubs.py for geocoding, time zones, regions and the
skill's User-Agent, so both planners behave the same way.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:   # `python -I` leaves the script's folder off sys.path
    sys.path.insert(0, HERE)

import trip_hubs as th  # noqa: E402

DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "references", "driving"))
_DATA = {}
_LAST_CALL = th._LAST_CALL   # one per-host pacing clock for both planners (Nominatim's 1 request/second)
_STALE = {}          # host -> (age in hours, error name) for replies served from an old cached copy
_CACHE_OFF = []      # set once the cache folder turned out unusable this run


class FetchError(Exception):
    """A reply came back but `validate` rejected it (an error body, a timed-out query)."""


def load_data(name):
    """references/driving/<name>.json, parsed once per process."""
    if name not in _DATA:
        with open(os.path.join(DATA_DIR, name + ".json"), encoding="utf-8") as f:
            _DATA[name] = json.load(f)
    return _DATA[name]


def env_key(*names):
    """The first non-empty environment variable among `names`, else None (API keys are optional)."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return None


def _cache_path(url, data):
    """Cache file for this request, or None when the cache folder cannot be created (caching is then off
    for the run; requests still work)."""
    h = hashlib.sha1((url + "\n" + (data.decode("utf-8", "replace") if data else "")).encode("utf-8")).hexdigest()
    try:
        d = os.path.join(th.cache_dir(), "driving")
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        if not _CACHE_OFF:
            _CACHE_OFF.append(True)
            th.log("cache folder unusable (%s); replies are not cached this run" % type(e).__name__)
        return None
    return os.path.join(d, h[:24])


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _valid(body, validate):
    """None when `body` passes `validate` (or there is no check), else the reason it failed."""
    if validate is None or body is None:
        return None
    try:
        why = validate(body)
    except Exception as e:  # noqa: BLE001
        why = "unreadable reply (%s)" % type(e).__name__
    return why or None


def _store(path, body):
    """Write the cache file atomically under a unique temporary name; a failure (folder gone, file locked
    by another run) only means this reply is not cached."""
    fd, tmp = None, None
    try:
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(body)
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


_KEEP_ON_REDIRECT = {"user-agent", "accept", "accept-language"}


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib's redirect handling, except that a redirect to another host, port or scheme drops every
    header but User-Agent and Accept: API keys sent in headers never reach a server the request was not
    addressed to."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed = getattr(req, "_allowed_hosts", None)
        if allowed and (urllib.parse.urlsplit(newurl).hostname or "").lower() not in allowed:
            raise urllib.error.HTTPError(newurl, code, "redirect to a host that is not allowed", headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and allowed:
            new._allowed_hosts = allowed
        if new is not None:
            a, b = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(new.full_url)
            if (a.scheme.lower(), a.netloc.lower()) != (b.scheme.lower(), b.netloc.lower()):
                new.headers = {k: v for k, v in new.headers.items() if k.lower() in _KEEP_ON_REDIRECT}
                new.unredirected_hdrs = {}
        return new


_OPENER = urllib.request.build_opener(_RedirectHandler)


def fetch(url, data=None, headers=None, timeout=30, min_interval=0.0, max_age_h=0.0, cache_key=None,
          stale_max_h=None, validate=None, allowed_hosts=None):
    """GET, or POST when `data` is given (bytes, str, or a dict that is form-encoded), with the skill's
    User-Agent. `min_interval` paces calls per host (free-service policies). With `max_age_h` a reply is
    cached on disk and reused while fresh; `cache_key` replaces the URL in the cache key (use it when the
    URL carries an API key).

    `validate(body)` returns None for a usable reply or a short reason for one that is not (an error body
    sent with HTTP 200, a timed-out query); a rejected reply is never cached and counts as a failure.

    On a failure a cached copy older than `max_age_h` stands in when it is at most `stale_max_h` hours old
    (None: any age; 0: never), and stale_notes() reports it for the run's warnings. Otherwise the error is
    raised.

    `allowed_hosts` (host names) refuses the URL, and any redirect, on another host with a ValueError or an
    HTTPError."""
    allowed = set(h.lower() for h in allowed_hosts) if allowed_hosts else None
    if allowed and (urllib.parse.urlsplit(url).hostname or "").lower() not in allowed:
        raise ValueError("refusing %s: not one of the allowed hosts" % urllib.parse.urlsplit(url).netloc)
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode("utf-8")
    elif isinstance(data, str):
        data = data.encode("utf-8")
    path = _cache_path(cache_key or url, data) if max_age_h else None
    if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        body = _read(path)
        if body is not None and not _valid(body, validate):
            return body
    host = urllib.parse.urlsplit(url).netloc
    if min_interval:
        wait = _LAST_CALL.get(host, 0.0) + min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
    hdrs = {"User-Agent": th.USER_AGENT, "Accept": "application/json, text/csv, */*"}
    hdrs.update(headers or {})
    if data is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs)
        if allowed:
            req._allowed_hosts = allowed
        with _OPENER.open(req, timeout=timeout) as r:
            body = r.read()
        why = _valid(body, validate)
        if why:
            raise FetchError(why)
    except Exception as e:
        old = _stale_copy(path, stale_max_h, validate)
        if old is not None:
            if isinstance(e, urllib.error.HTTPError):
                th.close_quietly(e)   # its body is not read; the cached copy answers instead
            _STALE[host] = (old[1], type(e).__name__)
            return old[0]
        raise
    finally:
        _LAST_CALL[host] = time.time()
    if path:
        _store(path, body)
    return body


def _stale_copy(path, stale_max_h, validate):
    """(body, age in hours) of the cached copy when it may stand in for a failed request, else None."""
    if not path or stale_max_h == 0 or not os.path.exists(path):
        return None
    try:
        age_h = (time.time() - os.path.getmtime(path)) / 3600.0
    except OSError:
        return None
    if stale_max_h is not None and age_h > stale_max_h:
        return None
    body = _read(path)
    if body is None or _valid(body, validate):
        return None
    return body, age_h


def stale_notes(reset=True):
    """One warning per host whose request failed and was answered from an older cached copy."""
    out = []
    for host, (age_h, err) in sorted(_STALE.items()):
        if age_h < 1:
            age = "%.0f min" % (age_h * 60)
        elif age_h < 48:
            age = "%.0f h" % age_h
        else:
            age = "%.0f days" % (age_h / 24)
        out.append("%s did not answer (%s); used its cached reply from %s ago, which may be out of date"
                   % (host, err, age))
    if reset:
        _STALE.clear()
    return out


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


# --------------------------------------------------------------------------- geometry
# Polylines are lists of (lat, lon) tuples.

def cum_dist(poly):
    """Cumulative distance in metres at each vertex."""
    out, tot = [0.0], 0.0
    for (a, b), (c, d) in zip(poly, poly[1:]):
        tot += th.haversine_m(a, b, c, d)
        out.append(tot)
    return out


def project(poly, cum, lat, lon):
    """Nearest point of the polyline to (lat, lon): (metres along the line, metres off the line, segment
    index). Uses a local flat-earth projection per segment, good to a few metres at these scales."""
    best = (0.0, float("inf"), 0)
    kx = 111320.0 * math.cos(math.radians(lat))
    ky = 110540.0
    for i in range(len(poly) - 1):
        (a_lat, a_lon), (b_lat, b_lon) = poly[i], poly[i + 1]
        ax, ay = (a_lon - lon) * kx, (a_lat - lat) * ky
        bx, by = (b_lon - lon) * kx, (b_lat - lat) * ky
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        px, py = ax + t * dx, ay + t * dy
        off = math.hypot(px, py)
        if off < best[1]:
            best = (cum[i] + t * (cum[i + 1] - cum[i]), off, i)
    if len(poly) == 1:
        best = (0.0, th.haversine_m(lat, lon, poly[0][0], poly[0][1]), 0)
    return best


def thin(poly, max_points):
    """Every k-th vertex (always keeping both ends) so the line has at most `max_points` vertices."""
    if len(poly) <= max_points:
        return list(poly)
    step = (len(poly) - 1) / float(max_points - 1)
    return [poly[int(round(i * step))] for i in range(max_points)]


def decode_polyline(s, precision=5):
    """Google encoded polyline (precision 5) or Valhalla's (precision 6) -> [(lat, lon)]."""
    out, i, lat, lon, f = [], 0, 0, 0, 10.0 ** precision
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = res = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(res >> 1) if res & 1 else res >> 1)
        lat += vals[0]
        lon += vals[1]
        out.append((lat / f, lon / f))
    return out


def encode_polyline(poly, precision=5):
    f, out, plat, plon = 10.0 ** precision, [], 0, 0
    for lat, lon in poly:
        ilat, ilon = int(round(lat * f)), int(round(lon * f))
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def bbox_of(poly, pad_m=0.0):
    lats, lons = [p[0] for p in poly], [p[1] for p in poly]
    mid = (min(lats) + max(lats)) / 2.0
    dlat = pad_m / 110540.0
    dlon = pad_m / (111320.0 * max(0.2, math.cos(math.radians(mid))))
    return {"min_lat": min(lats) - dlat, "max_lat": max(lats) + dlat,
            "min_lon": min(lons) - dlon, "max_lon": max(lons) + dlon}


def in_any(lat, lon, boxes):
    return any(th.in_bbox(lat, lon, b) for b in boxes or [])


# --------------------------------------------------------------------------- calendar

def holidays_for(regions=None):
    """Statutory holiday dates ('YYYY-MM-DD') from the region files (entries with "stat": false are
    operator-only service days and are left out), plus the Ontario ESA public holidays computed for last
    year through ten years ahead: the region lists stop at the end of next year. One rule with trip_hubs
    (trip_hubs.stat_holidays)."""
    this = dt.date.today().year
    return th.stat_holidays(regions if regions is not None else th.load_regions(), range(this - 1, this + 11))


def is_weekend_or_holiday(day, holidays):
    return day.weekday() >= 5 or day.isoformat() in holidays


def fmt_clock(when, ref=None):
    """datetime -> 'HH:MM', plus ' (Thu 15 Oct)' when `ref` (a datetime or date) is given and the day differs."""
    s = when.strftime("%H:%M")
    if ref is not None:
        ref_day = ref.date() if isinstance(ref, dt.datetime) else ref
        if when.date() != ref_day:
            s += " (%s)" % when.strftime("%a %d %b")
    return s


def fmt_dur(seconds):
    m = int(round(seconds / 60.0))
    if m < 60:
        return "%d min" % m
    return "%d h %02d" % divmod(m, 60)


def money(x, cur="CAD"):
    return "$%.2f" % x if cur == "CAD" else "%.2f %s" % (x, cur)


def local_dt(text, tzname, region, warnings):
    """--depart/--arrive-by text -> naive local datetime (same rules as trip_hubs)."""
    return th.parse_when(text, tzname, region, warnings)


def minutes_between(a, b):
    return (b - a).total_seconds() / 60.0


def add_s(when, seconds, tz=None, region=None):
    """Naive local wall clock `when` plus `seconds` of elapsed time (negative goes back). With a time zone the
    sum is worked out in UTC, so the clock jumps at a daylight-saving change. A wall time inside the repeated
    hour of the fall-back night is read as its first occurrence."""
    if not tz:
        return when + dt.timedelta(seconds=seconds)
    utc = when - th.offset_at_local(when, tz, region)[0] + dt.timedelta(seconds=seconds)
    return utc + th.offset_at_utc(utc, tz, region)[0]


def elapsed_s(a, b, tz=None, region=None):
    """Seconds of elapsed time from wall clock a to wall clock b (across a daylight-saving change with a tz)."""
    if not tz:
        return (b - a).total_seconds()
    return ((b - th.offset_at_local(b, tz, region)[0]) - (a - th.offset_at_local(a, tz, region)[0])).total_seconds()
