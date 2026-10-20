#!/usr/bin/env python3
"""Find every transit hub worth taking a ride (Uber, Lyft, taxi) to.

Reads GTFS timetables, scans the network once backwards from the destination
(profile connection scan), then checks EVERY stop within ride range of the
origin. Prints the options that trade ride cost against arrival time, plus
ride-only and transit-only baselines, fares where known, and an optional second
opinion from the Transitous router.

Standard library only. Python 3.8+.

Examples:
  python3 trip_hubs.py --origin "43.4988,-79.8560" --dest "43.5812,-79.6475" --depart "2026-10-15 21:00"
  python3 trip_hubs.py --origin "805 Santa Maria Blvd, Milton, ON" --dest "Square One, Mississauga" --depart now
  python3 trip_hubs.py --origin "..." --dest "..." --arrive-by "2026-10-16 09:00" --crosscheck
  python3 trip_hubs.py --discover --origin "52.5200,13.4050" --dest "52.3906,13.0645"
"""
from __future__ import annotations

import argparse
import bisect
import calendar
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

VERSION = "0.9.8"
USER_AGENT = "trip-planner-skill/%s (open-source agent skill; github.com/RZ-Logic)" % VERSION
INF = 10 ** 9
HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS_DIR = os.path.normpath(os.path.join(HERE, "..", "references", "regions"))

WALK_MPS = 1.25        # 4.5 km/h
WALK_DETOUR = 1.25     # street network vs straight line
PLATFORM_S = 60        # drop-off to stop/platform
CLUSTER_M = 200        # stops closer than this are one hub
OSRM_BATCH = 200       # destinations per OSRM table request (URL stays under ~5 KB)
OSRM_MAX_REQUESTS = 5
OSRM_FALLBACK = "https://routing.openstreetmap.de/routed-car"
DISCOVER_MAX_FEEDS = 40   # feeds with stops near the trip; feeds without nearby stops do not count
DISCOVER_MAX_AREA = 30.0   # square degrees; bigger bboxes are national/intercity feeds
DISCOVER_FAR_KM = 300.0    # bbox centred farther than this from the trip is probably a bad bbox
DISCOVER_DUP_IOU = 0.8     # same provider and this much bbox overlap = the same feed listed twice
DISCOVER_DEST_MAX = 8      # feeds discovered around a destination no region file covers
DEST_DISCOVER_MIN_M = 30000.0   # trips at least this long look for feeds there
INTERCITY_MIN_M = 50000.0  # trips at least this long also load intercity region files
LONG_TRIP_M = 60000.0      # trips at least this long get a search window sized to the drive
MAX_WINDOW_H = 9.0         # longest automatic search window
FINAL_RIDE_MIN_M = 60000.0   # trips at least this long also try finishing with a ride (auto mode)
FINAL_RIDE_SHARE = 0.6       # a final ride may start this share of the trip away from the destination
FINAL_RIDE_RADIUS_M = (8000.0, 80000.0)   # final-ride reach, clamped to this range
FINAL_RIDE_DROP_POINTS = 5   # stops near the destination tried as the place to switch to a ride
FINAL_RIDE_WALK_M = 600      # a final ride can be picked up this far from where you get off
LONG_HOP_M = 10000           # a vehicle covering this much between two stops is a coach or train
STATION_RADIUS_FACTOR = 1.3  # rail stations count as hubs this much past the ride radius

MOBILITYDB_CSV_URLS = [
    "https://files.mobilitydatabase.org/feeds_v2.csv",
    "https://share.mobilitydata.org/catalogs-csv",
]
TRANSITOUS_URL = "https://api.transitous.org/api/v6/plan"
TRANSITOUS_SOURCES_URL = "https://transitous.org/sources/"   # attribution its usage policy asks to show

DEFAULT_PRICING = {
    "label": "generic ride-hail estimate",
    "currency": "USD",
    "base": 2.50,
    "per_km": 1.00,
    "per_min": 0.30,
    "booking_fee": 2.50,
    "min_fare": 8.00,
    "source": "generic North American default, not a quote",
}

ROUTE_TYPES = {0: "tram", 1: "subway", 2: "train", 3: "bus", 4: "ferry", 5: "cable car",
               6: "gondola", 7: "funicular", 11: "trolleybus", 12: "monorail"}

PRECISE_GEOCODE = {"building", "house", "amenity", "shop", "office", "tourism", "leisure", "aeroway",
                   "railway", "historic", "man_made", "craft", "emergency", "healthcare", "club", "place"}
STREET_WORDS = {"street", "avenue", "road", "drive", "boulevard", "blvd", "north", "south", "east", "west",
                "and", "the", "lane", "way", "court", "crescent", "place", "highway", "hwy", "parkway", "pkwy",
                "terrace", "trail", "circle", "square", "near", "corner", "of", "at"}

# currency for the generic ride estimate when no region file gives a local rate card
TZ_CURRENCY = {"America/Toronto": "CAD", "America/Montreal": "CAD", "America/Halifax": "CAD",
               "America/Moncton": "CAD", "America/St_Johns": "CAD", "America/Winnipeg": "CAD",
               "America/Regina": "CAD", "America/Edmonton": "CAD", "America/Vancouver": "CAD",
               "America/Nipigon": "CAD", "America/Thunder_Bay": "CAD", "Europe/London": "GBP",
               "Europe/Zurich": "CHF", "Australia/Sydney": "AUD", "Australia/Melbourne": "AUD",
               "Australia/Brisbane": "AUD", "Australia/Perth": "AUD", "Australia/Adelaide": "AUD",
               "Australia/Canberra": "AUD", "Australia/Hobart": "AUD", "Australia/Darwin": "AUD",
               "Pacific/Auckland": "NZD"}
EURO_ZONES = {"Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Rome", "Europe/Amsterdam",
              "Europe/Brussels", "Europe/Vienna", "Europe/Dublin", "Europe/Lisbon", "Europe/Helsinki",
              "Europe/Athens", "Europe/Luxembourg", "Europe/Ljubljana", "Europe/Bratislava", "Europe/Monaco",
              "Europe/Malta", "Europe/Riga", "Europe/Tallinn", "Europe/Vilnius", "Europe/Zagreb", "Atlantic/Canary"}


def local_currency(tzname):
    if tzname in TZ_CURRENCY:
        return TZ_CURRENCY[tzname]
    if (tzname or "").startswith("Canada/"):
        return "CAD"
    if tzname in EURO_ZONES:
        return "EUR"
    if tzname in US_ZONES or (tzname or "").startswith("US/"):
        return "USD"
    return None


US_ZONES = {"America/New_York", "America/Detroit", "America/Indiana/Indianapolis", "America/Kentucky/Louisville",
            "America/Chicago", "America/Denver", "America/Boise", "America/Phoenix", "America/Los_Angeles",
            "America/Anchorage", "America/Puerto_Rico", "Pacific/Honolulu", "EST5EDT", "CST6CDT", "MST7MDT",
            "PST8PDT"}


# --------------------------------------------------------------------------- utils

def close_quietly(resp):
    """Close a response or an HTTPError. On Python 3.8 an HTTPError made without a body raises KeyError when
    closed, so any error here is ignored: there is nothing left to free."""
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass


def log(msg):
    """Progress line on stderr (every script's log). TRIP_PLANNER_QUIET=1 silences it; the WARNINGS in the
    output are not affected."""
    if os.environ.get("TRIP_PLANNER_QUIET", "").strip() == "1":
        return
    print(msg, file=sys.stderr, flush=True)


# C0 and C1 control characters (terminal escape sequences, bell) and the bidirectional override, embedding
# and isolate marks that can reorder how a line reads.
_UNSAFE_CHARS = re.compile("[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def safe_text(s):
    """Text from a feed, map, API or review, safe to print: control and bidi-override characters become
    spaces, so the data cannot move the cursor, recolour the terminal or reorder a line. None stays None."""
    if s is None:
        return None
    return _UNSAFE_CHARS.sub(" ", s if isinstance(s, str) else str(s))


def safe_url(url):
    """A feed URL fit to print: a user:password@ part is dropped and query values are hidden, since agency feeds
    often carry an API key there. Local paths come back unchanged. For display only: downloads use the real URL."""
    try:
        p = urllib.parse.urlsplit(url or "")
    except ValueError:
        return "(unreadable URL)"
    if not p.scheme or not p.netloc:
        return url
    query = "&".join(k.partition("=")[0] + ("=REDACTED" if "=" in k else "") for k in p.query.split("&") if k)
    return urllib.parse.urlunsplit((p.scheme, p.netloc.rpartition("@")[2], p.path, query, ""))


def _scrub(text, url):
    """An error message with the URL in it replaced by its printable form, and any password or long query value
    from that URL that the message quotes on its own (urllib names the host with its user:password@) hidden."""
    text = str(text)
    if not url:
        return text
    text = text.replace(url, safe_url(url))
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return text
    if "@" in p.netloc:
        text = text.replace(p.netloc.rpartition("@")[0] + "@", "")
    for part in p.query.split("&"):
        value = part.partition("=")[2]
        if len(value) >= 6:   # short values (v=2) are not secrets and would match ordinary text
            text = text.replace(value, "REDACTED")
    return text


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def walk_s(meters):
    return int(math.ceil(meters * WALK_DETOUR / WALK_MPS))


def fmt_t(sec):
    """Seconds since midnight of the travel date -> 'HH:MM' (+1d when past midnight)."""
    sec = int(sec)
    day, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    s = "%02d:%02d" % (h, rem // 60)
    if day:
        s += " (%+dd)" % day
    return s


def fmt_min(sec):
    return "%d min" % int(round(sec / 60.0))


def parse_time(s):
    s = s.strip()
    if not s:
        return None
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(x)


def parse_date(s):
    s = s.strip()
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


_LAST_CALL = {}


def http_get(url, timeout=60, min_interval=0.0):
    """GET with an identifying User-Agent. min_interval paces calls per host (free-service policies)."""
    host = urllib.parse.urlsplit(url).netloc
    if min_interval:
        wait = _LAST_CALL.get(host, 0.0) + min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    finally:
        _LAST_CALL[host] = time.time()


def cache_dir():
    d = os.environ.get("TRIP_PLANNER_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "trip-planner")
    os.makedirs(d, exist_ok=True)
    return d


def gmaps_link(a, b, mode):
    return "https://www.google.com/maps/dir/?api=1&" + urllib.parse.urlencode(
        {"origin": "%.6f,%.6f" % a, "destination": "%.6f,%.6f" % b, "travelmode": mode})


def uber_link(a, b, name):
    """Prefilled Uber web link (prices need the rider's login)."""
    pick = json.dumps({"latitude": a[0], "longitude": a[1], "addressLine1": "Pickup"})
    drop = json.dumps({"latitude": b[0], "longitude": b[1], "addressLine1": name})
    return "https://m.uber.com/looking?" + urllib.parse.urlencode({"pickup": pick, "drop[0]": drop})


# --------------------------------------------------------------------------- time zones
# zoneinfo needs a tz database, which Windows Python lacks unless `tzdata` is installed.
# The table below covers common transit cities so offsets stay right without it.

TZ_TABLE = {
    "America/St_Johns": (-3.5, "na"), "America/Halifax": (-4, "na"), "America/Moncton": (-4, "na"),
    "America/Toronto": (-5, "na"), "America/New_York": (-5, "na"), "America/Montreal": (-5, "na"),
    "America/Detroit": (-5, "na"), "America/Indiana/Indianapolis": (-5, "na"),
    "America/Kentucky/Louisville": (-5, "na"), "America/Nipigon": (-5, "na"), "America/Thunder_Bay": (-5, "na"),
    "US/Eastern": (-5, "na"), "EST5EDT": (-5, "na"),
    "America/Chicago": (-6, "na"), "America/Winnipeg": (-6, "na"), "US/Central": (-6, "na"), "CST6CDT": (-6, "na"),
    "America/Regina": (-6, None), "America/Mexico_City": (-6, None), "America/Guatemala": (-6, None),
    "America/Denver": (-7, "na"), "America/Edmonton": (-7, "na"), "America/Boise": (-7, "na"),
    "US/Mountain": (-7, "na"), "MST7MDT": (-7, "na"), "America/Phoenix": (-7, None),
    "America/Los_Angeles": (-8, "na"), "America/Vancouver": (-8, "na"), "US/Pacific": (-8, "na"),
    "PST8PDT": (-8, "na"), "America/Anchorage": (-9, "na"), "Pacific/Honolulu": (-10, None),
    "America/Puerto_Rico": (-4, None), "America/Bogota": (-5, None), "America/Lima": (-5, None),
    "America/Panama": (-5, None), "America/Sao_Paulo": (-3, None), "America/Argentina/Buenos_Aires": (-3, None),
    "America/Montevideo": (-3, None),
    "Europe/London": (0, "eu"), "Europe/Dublin": (0, "eu"), "Europe/Lisbon": (0, "eu"), "Atlantic/Canary": (0, "eu"),
    "Europe/Paris": (1, "eu"), "Europe/Berlin": (1, "eu"), "Europe/Madrid": (1, "eu"), "Europe/Rome": (1, "eu"),
    "Europe/Amsterdam": (1, "eu"), "Europe/Brussels": (1, "eu"), "Europe/Vienna": (1, "eu"),
    "Europe/Zurich": (1, "eu"), "Europe/Stockholm": (1, "eu"), "Europe/Oslo": (1, "eu"),
    "Europe/Copenhagen": (1, "eu"), "Europe/Prague": (1, "eu"), "Europe/Warsaw": (1, "eu"),
    "Europe/Budapest": (1, "eu"), "Europe/Luxembourg": (1, "eu"), "Europe/Belgrade": (1, "eu"),
    "Europe/Zagreb": (1, "eu"), "Europe/Ljubljana": (1, "eu"), "Europe/Bratislava": (1, "eu"),
    "Europe/Monaco": (1, "eu"), "Europe/Malta": (1, "eu"),
    "Europe/Helsinki": (2, "eu"), "Europe/Athens": (2, "eu"), "Europe/Bucharest": (2, "eu"),
    "Europe/Sofia": (2, "eu"), "Europe/Kiev": (2, "eu"), "Europe/Kyiv": (2, "eu"), "Europe/Riga": (2, "eu"),
    "Europe/Tallinn": (2, "eu"), "Europe/Vilnius": (2, "eu"),
    "Europe/Istanbul": (3, None), "Europe/Moscow": (3, None),
    "Africa/Johannesburg": (2, None), "Africa/Lagos": (1, None), "Africa/Nairobi": (3, None),
    "Asia/Dubai": (4, None), "Asia/Kolkata": (5.5, None), "Asia/Bangkok": (7, None), "Asia/Jakarta": (7, None),
    "Asia/Ho_Chi_Minh": (7, None), "Asia/Shanghai": (8, None), "Asia/Hong_Kong": (8, None),
    "Asia/Taipei": (8, None), "Asia/Singapore": (8, None), "Asia/Manila": (8, None),
    "Asia/Kuala_Lumpur": (8, None), "Asia/Tokyo": (9, None), "Asia/Seoul": (9, None),
    "Australia/Perth": (8, None), "Australia/Darwin": (9.5, None), "Australia/Brisbane": (10, None),
    "Australia/Adelaide": (9.5, "au"), "Australia/Sydney": (10, "au"), "Australia/Melbourne": (10, "au"),
    "Australia/Canberra": (10, "au"), "Australia/Hobart": (10, "au"), "Pacific/Auckland": (12, "nz"),
    "UTC": (0, None), "Etc/UTC": (0, None), "GMT": (0, None),
    # old link names some feeds still declare (OC Transpo: Canada/Eastern)
    "Canada/Newfoundland": (-3.5, "na"), "Canada/Atlantic": (-4, "na"), "Canada/Eastern": (-5, "na"),
    "Canada/Central": (-6, "na"), "Canada/Saskatchewan": (-6, None), "Canada/Mountain": (-7, "na"),
    "Canada/Yukon": (-7, None), "Canada/Pacific": (-8, "na"), "America/Whitehorse": (-7, None),
    "America/Yellowknife": (-7, "na"), "US/Michigan": (-5, "na"), "US/East-Indiana": (-5, "na"),
    "US/Arizona": (-7, None), "US/Alaska": (-9, "na"), "US/Hawaii": (-10, None),
}
RULE_ALIASES = {"north_america": "na", "na": "na", "eu": "eu", "au": "au", "nz": "nz"}


def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_sunday(year, month):
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - 6) % 7)


def _dst_active_utc(utc, std, rule):
    """Is daylight saving in force at this UTC instant? Transitions are computed in UTC."""
    y, h = utc.year, dt.timedelta(hours=1)
    std_td = dt.timedelta(hours=std)
    if rule == "na":   # 2nd Sun Mar 02:00 standard -> 1st Sun Nov 02:00 daylight
        s = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(2)) - std_td
        e = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(2)) - std_td - h
        return s <= utc < e
    if rule == "eu":   # last Sun Mar 01:00 UTC -> last Sun Oct 01:00 UTC
        s = dt.datetime.combine(_last_sunday(y, 3), dt.time(1))
        e = dt.datetime.combine(_last_sunday(y, 10), dt.time(1))
        return s <= utc < e
    if rule == "au":   # southern: 1st Sun Oct 02:00 standard -> 1st Sun Apr 03:00 daylight
        e = dt.datetime.combine(_nth_sunday(y, 4, 1), dt.time(3)) - std_td - h
        s = dt.datetime.combine(_nth_sunday(y, 10, 1), dt.time(2)) - std_td
        return utc < e or utc >= s
    if rule == "nz":   # last Sun Sep 02:00 standard -> 1st Sun Apr 03:00 daylight
        e = dt.datetime.combine(_nth_sunday(y, 4, 1), dt.time(3)) - std_td - h
        s = dt.datetime.combine(_last_sunday(y, 9), dt.time(2)) - std_td
        return utc < e or utc >= s
    return False


def _tz_rule(tzname, region):
    reg = region or {}
    if reg.get("utc_std_offset_hours") is not None and (not tzname or tzname == reg.get("timezone")):
        return float(reg["utc_std_offset_hours"]), RULE_ALIASES.get(reg.get("dst_rule") or "", None)
    if tzname in TZ_TABLE:
        return TZ_TABLE[tzname]
    return None


def _zoneinfo(tzname):
    if not tzname:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tzname)
    except Exception:  # noqa: BLE001
        return None


def offset_at_utc(utc, tzname, region):
    """UTC offset at a naive UTC instant -> (timedelta, how)."""
    z = _zoneinfo(tzname)
    if z is not None:
        return utc.replace(tzinfo=dt.timezone.utc).astimezone(z).utcoffset(), "zoneinfo"
    r = _tz_rule(tzname, region)
    if r:
        std, rule = r
        return dt.timedelta(hours=std + (1 if rule and _dst_active_utc(utc, std, rule) else 0)), "built-in rule"
    return dt.timedelta(seconds=time.localtime(calendar.timegm(utc.timetuple())).tm_gmtoff), "machine clock"


def offset_at_local(local, tzname, region):
    """UTC offset of a naive local wall time -> (timedelta, how)."""
    z = _zoneinfo(tzname)
    if z is not None:
        return local.replace(tzinfo=z).utcoffset(), "zoneinfo"
    r = _tz_rule(tzname, region)
    if r:
        std, rule = r
        dst = bool(rule) and _dst_active_utc(local - dt.timedelta(hours=std + 1), std, rule)
        return dt.timedelta(hours=std + (1 if dst else 0)), "built-in rule"
    return dt.timedelta(seconds=time.localtime(time.mktime(local.timetuple())).tm_gmtoff), "machine clock"


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def local_now(tzname, region):
    utc = utc_now()
    off, how = offset_at_utc(utc, tzname, region)
    return (utc + off).replace(second=0, microsecond=0), how


def parse_when(text, tzname, region, warnings):
    """'now', 'HH:MM' (next occurrence, local) or 'YYYY-MM-DD HH:MM' -> naive local datetime."""
    if text in (None, "", "now"):
        now, how = local_now(tzname, region)
        if how == "machine clock" and tzname:
            warnings.append("time zone %s is unknown to this Python (no tz database), so 'now' uses the computer's "
                            "clock offset. Pass an explicit 'YYYY-MM-DD HH:MM' or `pip install tzdata`." % tzname)
        return now
    txt = text.strip().replace("T", " ")
    if re.match(r"^\d{1,2}:\d{2}$", txt):
        now, _ = local_now(tzname, region)
        hh, mm = txt.split(":")
        when = dt.datetime.combine(now.date(), dt.time(int(hh), int(mm)))
        if when < now - dt.timedelta(minutes=30):
            when += dt.timedelta(days=1)
            warnings.append("%s has already passed today, so the plan is for tomorrow (%s). Pass a full "
                            "'YYYY-MM-DD HH:MM' to choose the day." % (txt, when.strftime("%a %Y-%m-%d")))
        return when
    return dt.datetime.strptime(txt[:16], "%Y-%m-%d %H:%M")


def same_clock(tz_a, tz_b):
    """True when two IANA zones keep the same clock (e.g. Toronto and New York)."""
    if tz_a == tz_b:
        return True
    a, b = TZ_TABLE.get(tz_a), TZ_TABLE.get(tz_b)
    return bool(a and b and a == b)


# --------------------------------------------------------------------------- geocoding

LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def check_geocode(query, found, label, warnings):
    """Warn when the result does not mention the street or place words of the request."""
    first = query.split(",")[0].lower()
    if "&" in first or " and " in first or "/" in first:
        warnings.append("%s %r is an intersection; geocoders often land on the wrong corner or street. Confirm "
                        "%r or pass 'lat,lon'." % (label, query, found))
        return
    words = [w for w in re.findall(r"[a-z]{3,}", first) if w not in STREET_WORDS]
    missing = [w for w in words if w not in (found or "").lower()]
    if missing:
        warnings.append("%s %r was geocoded to %r, which does not mention %s. Confirm the spot or pass 'lat,lon'."
                        % (label, query, found, ", ".join(missing)))


def reverse_place_info(lat, lon, warnings):
    """Town names, country code and state around a point. Town names match catalog feeds that have no
    bounding box; country and state rank large feeds."""
    info = {"names": set(), "country": None, "state": None}
    try:
        # town level (zoom 10) needs no more than ~100 m of precision: do not send the exact door
        url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
            {"lat": "%.3f" % lat, "lon": "%.3f" % lon, "format": "jsonv2", "zoom": 10})
        addr = json.loads(http_get(url, timeout=20, min_interval=1.1)).get("address") or {}
    except Exception as e:  # noqa: BLE001
        warnings.append("reverse geocoding failed (%s)" % e)
        return info
    info["names"] = {addr[k].lower() for k in ("city", "town", "village", "municipality", "hamlet", "county")
                     if addr.get(k)}
    info["country"] = (addr.get("country_code") or "").upper() or None
    info["state"] = (addr.get("state") or addr.get("region") or "").lower() or None
    return info


def resolve_place(text, label, warnings, strict=True):
    """'lat,lon' or a free-text address. Nominatim first; Photon when Nominatim only finds the street."""
    m = LATLON_RE.match(text)
    if m:
        return float(m.group(1)), float(m.group(2)), text.strip()
    lat, lon, name = _geocode(text, label, warnings, strict)
    check_geocode(text, name, label, warnings)
    return lat, lon, name


def _unreachable(e):
    """True when the network path is blocked (DNS failure, connection refused, timeout, a proxy refusing),
    False when the host answered in any way (an HTTP error, or even dropping the connection)."""
    import socket
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 407 or (e.code == 403 and "proxy" in str(e.headers.get("Server", "")).lower())
    reason = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(reason, (socket.gaierror, ConnectionRefusedError, socket.timeout, TimeoutError)):
        return True
    text = str(reason).lower()
    return any(k in text for k in ("name or service not known", "getaddrinfo", "nodename", "timed out",
                                   "tunnel connection failed", "proxy", "refused", "no route to host",
                                   "network is unreachable", "temporary failure in name resolution"))


def _geocode(text, label, warnings, strict=True):
    nom, blocked = None, []
    try:
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": text, "format": "jsonv2", "limit": 1})
        data = json.loads(http_get(url, timeout=20, min_interval=1.1))  # policy: max 1 request/second
        if data:
            nom = data[0]
    except Exception as e:  # noqa: BLE001
        if _unreachable(e):
            blocked.append("nominatim.openstreetmap.org")
        warnings.append("Nominatim geocoding failed for %s (%s)" % (label, e))
    if nom and (nom.get("addresstype") in PRECISE_GEOCODE or nom.get("category") in PRECISE_GEOCODE):
        return float(nom["lat"]), float(nom["lon"]), nom.get("display_name", text)
    try:
        url = "https://photon.komoot.io/api/?" + urllib.parse.urlencode({"q": text, "limit": 1})
        feats = json.loads(http_get(url, timeout=20, min_interval=1.0)).get("features") or []
        if feats and feats[0]["properties"].get("type") in ("house", "building", "amenity"):
            lon, lat = feats[0]["geometry"]["coordinates"]
            p = feats[0]["properties"]
            name = ", ".join(str(p[k]) for k in ("housenumber", "street", "city") if p.get(k)) or text
            return float(lat), float(lon), name
    except Exception as e:  # noqa: BLE001
        if _unreachable(e):
            blocked.append("photon.komoot.io")
        warnings.append("Photon geocoding failed for %s (%s)" % (label, e))
    if nom:
        if strict and HOUSE_NUMBER_RE.match(text.split(",")[0]):
            # a house number was given but only the street matched: the point can be kilometres off along it
            raise SystemExit("ADDRESS NOT PRECISE: %s %r matched only the street (%s), not the building, so the point "
                             "could be kilometres from the real address and the hubs in range would be wrong. Get "
                             "the exact spot from a map or places tool and pass it as 'lat,lon', or rerun with "
                             "--allow-street-match to plan from the street anyway."
                             % (label, text, nom.get("display_name", "")))
        warnings.append("%s %r was only found at street level (%s), so the point may be off by a kilometre or more. "
                        "Hubs in range can change; ask for a pin, a nearby landmark or 'lat,lon'."
                        % (label, text, nom.get("addresstype")))
        return float(nom["lat"]), float(nom["lon"]), nom.get("display_name", text)
    if len(blocked) == 2:
        raise SystemExit(network_blocked_message(blocked, ["nominatim.openstreetmap.org", "photon.komoot.io"],
                                                 "The feed hosts depend on where the trip is: rerun with 'lat,lon' "
                                                 "for the full list."))
    raise SystemExit("could not find %s %r with the geocoders. Pass coordinates as 'lat,lon'." % (label, text))


HOUSE_NUMBER_RE = re.compile(r"^\s*\d+[A-Za-z]?(\s*-\s*\d+[A-Za-z]?)?\s+\S")
SERVICE_DOMAINS = ["nominatim.openstreetmap.org", "photon.komoot.io", "router.project-osrm.org",
                   "routing.openstreetmap.de", "api.transitous.org", "files.mobilitydatabase.org"]


def needed_domains(regions):
    """Every host the script may contact: the free services plus each region feed host."""
    hosts = list(SERVICE_DOMAINS)
    for reg in regions:
        for f in reg.get("feeds", []):
            if f.get("skip"):
                continue
            fh = [urllib.parse.urlsplit(u).netloc for u in f.get("urls", [])]
            own = [h for h in fh if h not in SHARED_HOSTS and not re.match(r"^\d+(\.\d+){3}(:\d+)?$", h)]
            for h in own or fh:
                if h and h not in hosts:
                    hosts.append(h)
    return hosts


def trip_geometry(o_lat, o_lon, d_lat, d_lon, args):
    """Ride radius, destination zone, the trip's bounding box and the 'near' list used to pick feeds."""
    od_m = haversine_m(o_lat, o_lon, d_lat, d_lon)
    # hubs much past 70% of the way rarely beat riding all the way, and flood the driving-time budget
    radius_m = args.radius_km * 1000 if args.radius_km else min(40000.0, max(3000.0, 0.7 * od_m))
    dest_zone_m = max(3000.0, args.dest_walk_m * 2)   # feeds within twice the walking distance of the destination
    # a final ride can start well short of the destination, so feeds that reach that far count too
    reach_m = max(dest_zone_m, final_ride_radius(od_m, args))
    query_box = box_union(box_around(o_lat, o_lon, radius_m), box_around(d_lat, d_lon, reach_m))
    near = [(o_lat, o_lon, radius_m), (d_lat, d_lon, reach_m)]
    return od_m, radius_m, dest_zone_m, query_box, near


def final_ride_radius(od_m, args, force=False):
    """How far from the destination a final ride may start (0 when the final ride is off). Auto mode turns it
    on for long trips, or (force) when no stop is within walking distance of the destination."""
    mode = getattr(args, "final_ride", "auto")
    if mode == "off" or (mode == "auto" and od_m < FINAL_RIDE_MIN_M and not force):
        return 0.0
    lo, hi = FINAL_RIDE_RADIUS_M
    return min(hi, max(lo, FINAL_RIDE_SHARE * od_m))


# Platforms that host many unrelated sites. Admins resist allowlisting them, so when a feed has another
# source they are listed as optional.
SHARED_HOSTS = {"www.arcgis.com", "drive.google.com", "drive.usercontent.google.com", "github.com",
                "raw.githubusercontent.com", "dropbox.com", "www.dropbox.com"}


def trip_domains(args, regions):
    """The hosts this particular trip needs, and a note when the list cannot be complete yet."""
    coords = LATLON_RE.match(args.origin or "") and LATLON_RE.match(args.dest or "")
    hosts, note, optional, border = [], "", [], False
    if not coords:
        hosts += ["nominatim.openstreetmap.org", "photon.komoot.io"]
    if args.routing != "estimate":
        hosts += ["router.project-osrm.org", "routing.openstreetmap.de"]
    if args.crosscheck:
        hosts.append("api.transitous.org")
    if getattr(args, "access", "ride") == "car":
        hosts.append("overpass-api.de")
    for spec in args.feed or []:
        src = FEED_ID_RE.sub(r"\2", spec) if FEED_ID_RE.match(spec) else spec
        if not os.path.exists(src) and urllib.parse.urlsplit(src).netloc:
            hosts.append(urllib.parse.urlsplit(src).netloc.rpartition("@")[2])   # never a user:password
    if not coords:
        note = "The feed hosts depend on where the trip is: rerun with 'lat,lon' for the full list."
    else:
        o_lat, o_lon = (float(x) for x in LATLON_RE.match(args.origin).groups())
        d_lat, d_lon = (float(x) for x in LATLON_RE.match(args.dest).groups())
        od, _r, _z, box, near = trip_geometry(o_lat, o_lon, d_lat, d_lon, args)
        home, extra = regions_for_trip(regions, args.region, (o_lat, o_lon), (d_lat, d_lon), od)
        region = merge_regions(home, extra, d_lat, d_lon, pricing_regions(regions, o_lat, o_lon))
        if region:
            for f in region.get("feeds", []):
                if f.get("skip") or (f.get("bbox") and not bbox_overlap(f["bbox"], box)):
                    continue
                if f.get("coverage") and not coverage_near(f["coverage"], near):
                    continue
                fh = [urllib.parse.urlsplit(u).netloc for u in f.get("urls", [])]

                def shared(h):   # shared platforms and bare IP addresses: allowlists work by domain
                    return h in SHARED_HOSTS or bool(re.match(r"^\d+(\.\d+){3}(:\d+)?$", h))
                own = [h for h in fh if not shared(h)]
                hosts += own or fh
                optional += [h for h in fh if own and shared(h)]
        elif not args.no_discover:
            hosts.append("files.mobilitydatabase.org")
            note = ("With no region file, feeds come from the MobilityDatabase mirror (files.mobilitydatabase.org); "
                    "a few agencies may also need their own host.")
        # plan() asks Nominatim which country each end is in when a region file that names one holds it
        border = args.region == "auto" and any(r.get("country") and r.get("bbox") and in_bbox(lat, lon, r["bbox"])
                                               for r in regions for lat, lon in ((o_lat, o_lon), (d_lat, d_lon)))
    out = []
    for h in hosts:
        if h and h not in out:
            out.append(h)
    optional = [h for h in dict.fromkeys(optional) if h not in out]
    if optional:
        note = (note + " " if note else "") + ("Optional (shared platforms; the mirror is used without them): %s."
                                               % ", ".join(optional))
    if border and "nominatim.openstreetmap.org" not in out:
        note = (note + " " if note else "") + ("Optional: nominatim.openstreetmap.org (confirms which country each "
                                               "end is in; without it the region boxes decide).")
    return out, note


def network_blocked_message(blocked, hosts, note=""):
    return ("NETWORK BLOCKED: this environment cannot reach %s (DNS failure or connection refused), so the live "
            "planner cannot run here. This is not an address problem. Either allow these domains in this "
            "environment's network settings (on claude.ai, the code execution network access setting):\n  %s\n%s"
            "or follow the manual method in SKILL.md: use a tool or connector that can request any URL (an "
            "HTTP, fetch or scraping connector; built-in web-fetch tools often refuse URLs you build), and "
            "plan from the region notes and the hand-entered timetables in the region files meanwhile."
            % (", ".join(blocked), "\n  ".join(hosts), (note + "\n") if note else ""))


def check_network(hosts, timeout=6):
    """{host: 'ok' | 'blocked: reason'} using one light request per host."""
    out = {}
    for h in hosts:
        try:
            req = urllib.request.Request("https://%s/" % h, method="HEAD", headers={"User-Agent": USER_AGENT})
            urllib.request.urlopen(req, timeout=timeout).close()
            out[h] = "ok"
        except Exception as e:  # noqa: BLE001
            out[h] = ("blocked: %s" % e) if _unreachable(e) else "ok"
            if isinstance(e, urllib.error.HTTPError):
                close_quietly(e)
        finally:
            _LAST_CALL[h] = time.time()   # counts toward the per-host pacing of the free services
    return out


def preflight(args, regions, warnings):
    """Tell 'network blocked' apart from 'address not found' before anything else fails. Returns
    {host: 'ok' | 'blocked: reason'} for the hosts it checked ({} when the trip needs no network)."""
    local_only = (args.region == "none" and args.routing == "estimate" and not args.crosscheck
                  and all(os.path.exists(FEED_ID_RE.sub(r"\2", s)) or os.path.exists(s) for s in (args.feed or []))
                  and LATLON_RE.match(args.origin) and LATLON_RE.match(args.dest or ""))
    if local_only:
        return {}
    status = check_network(["files.mobilitydatabase.org", "nominatim.openstreetmap.org", "router.project-osrm.org"])
    blocked = [h for h, s in status.items() if s != "ok"]
    if len(blocked) == len(status):
        hosts, note = trip_domains(args, regions)
        raise SystemExit(network_blocked_message(blocked, hosts, note))
    if blocked:
        warnings.append("some services are unreachable from here: %s" % ", ".join(blocked))
    return status


# --------------------------------------------------------------------------- regions and feeds

def load_regions():
    regions = []
    if os.path.isdir(REGIONS_DIR):
        for name in sorted(os.listdir(REGIONS_DIR)):
            if name.endswith(".json"):
                with open(os.path.join(REGIONS_DIR, name), encoding="utf-8") as fh:
                    reg = json.load(fh)
                reg.setdefault("id", name[:-5])
                regions.append(reg)
    return regions


def in_bbox(lat, lon, b, pad_deg=0.0):
    return (b["min_lat"] - pad_deg <= lat <= b["max_lat"] + pad_deg
            and b["min_lon"] - pad_deg <= lon <= b["max_lon"] + pad_deg)


def bbox_overlap(a, b):
    return not (a["max_lat"] < b["min_lat"] or b["max_lat"] < a["min_lat"]
                or a["max_lon"] < b["min_lon"] or b["max_lon"] < a["min_lon"])


def box_around(lat, lon, meters):
    dlat = meters / 111320.0
    dlon = meters / (111320.0 * max(0.2, math.cos(math.radians(lat))))
    return {"min_lat": lat - dlat, "max_lat": lat + dlat, "min_lon": lon - dlon, "max_lon": lon + dlon}


def box_union(a, b):
    return {"min_lat": min(a["min_lat"], b["min_lat"]), "max_lat": max(a["max_lat"], b["max_lat"]),
            "min_lon": min(a["min_lon"], b["min_lon"]), "max_lon": max(a["max_lon"], b["max_lon"])}


def box_center(b):
    return (b["min_lat"] + b["max_lat"]) / 2.0, (b["min_lon"] + b["max_lon"]) / 2.0


def bbox_dist_m(lat, lon, b):
    """Metres from a point to a bounding box (0 inside it, or when the box is unknown)."""
    if not b:
        return 0.0
    return haversine_m(lat, lon, min(max(lat, b["min_lat"]), b["max_lat"]), min(max(lon, b["min_lon"]), b["max_lon"]))


def region_claims(r, lat, lon, cc=None):
    """r's box holds the point, and the point is not known to be in another country than r's (the Ontario
    boxes hold Detroit and Buffalo)."""
    return (bool(r.get("bbox")) and in_bbox(lat, lon, r["bbox"])
            and not (cc and r.get("country") and r["country"] != cc))


def pick_region(regions, region_arg, lat, lon, country=None):
    if region_arg in ("none", ""):
        return None
    if region_arg != "auto":
        for r in regions:
            if r["id"] == region_arg:
                return r
        raise SystemExit("unknown region %r. Known: %s" % (region_arg, ", ".join(r["id"] for r in regions) or "none"))
    for r in regions:
        if region_claims(r, lat, lon, country):
            return r
    return None


def _bbox_area(b):
    return (b["max_lat"] - b["min_lat"]) * (b["max_lon"] - b["min_lon"])


def regions_for_trip(regions, region_arg, o, d, od_m, countries=(None, None)):
    """(home, extra). home: the most specific region file holding the origin (its rate card, time zone
    and fares lead). extra: the most specific one holding the destination, plus intercity region files
    holding either end when the trip is long or when no local region file holds the destination.
    '--region ID' pins home; 'none' uses no region file. `countries` (origin, destination country codes,
    None when unknown) keeps a region file from holding an end in another country."""
    if region_arg in ("none", ""):
        return None, []
    o_cc, d_cc = countries

    def holding(lat, lon, intercity, cc):
        rs = [r for r in regions if region_claims(r, lat, lon, cc) and bool(r.get("intercity")) == intercity]
        return sorted(rs, key=lambda r: _bbox_area(r["bbox"]))

    if region_arg != "auto":
        home = pick_region(regions, region_arg, o[0], o[1])
    else:
        home = (holding(o[0], o[1], False, o_cc) or holding(o[0], o[1], True, o_cc) or [None])[0]
    extra = []
    dest_local = holding(d[0], d[1], False, d_cc)

    def serves(r, pt):   # some feed of region r has stops within 3 km of pt (unknown without stop maps: yes)
        maps = [f["coverage"] for f in r.get("feeds", []) if f.get("coverage") and not f.get("skip")]
        return not maps or any(coverage_near(m, [(pt[0], pt[1], 3000.0)]) for m in maps)

    # long trips, and ends that no local region file actually serves (Niagara-on-the-Lake sits inside the
    # GTHA box, but only the intercity file has its buses)
    want_intercity = (od_m >= INTERCITY_MIN_M or not dest_local or not serves(dest_local[0], d)
                      or (home is not None and not home.get("intercity") and not serves(home, o)))
    for r in dest_local[:1] + (holding(o[0], o[1], True, o_cc) + holding(d[0], d[1], True, d_cc)
                               if want_intercity else []):
        if r is not home and r not in extra:
            extra.append(r)
    if home is None and extra:
        home, extra = extra[0], extra[1:]
    return home, extra


def pricing_regions(regions, lat, lon, country=None):
    """Region files that hold a point and carry ride-hail rate areas (used for pricing even on trips that
    do not load their feeds)."""
    return [r for r in regions if r.get("ride_pricing_areas") and region_claims(r, lat, lon, country)]


def merge_regions(home, extra, d_lat=None, d_lon=None, pricing_from=()):
    """One region view for the trip: home's time zone, rate card and pickup wait; feeds, fares and
    transfer rules from all of them (home first); the destination's rate card for a final ride. Rate
    areas also come from `pricing_from` (region files holding the origin) when none of them has any."""
    if home is None:
        return None
    if not extra:
        view = dict(home)
        view["notes_files"] = [home["notes_file"]] if home.get("notes_file") else []
        view.setdefault("final_ride_pricing", home.get("ride_pricing"))
        for key in ("ride_pricing_areas", "ride_pricing_outside", "taxi_pricing_default"):
            if not view.get(key):
                view[key] = next((r[key] for r in pricing_from if r.get(key)), None)
        return view
    view = dict(home)
    view["id"] = "+".join(r["id"] for r in [home] + extra)
    view["notes_files"] = [r["notes_file"] for r in [home] + extra if r.get("notes_file")]
    seen = {f["id"] for f in home.get("feeds", [])}
    feeds = list(home.get("feeds", []))
    for r in extra:
        for f in r.get("feeds", []):
            if f["id"] not in seen:
                seen.add(f["id"])
                feeds.append(f)
    view["feeds"] = feeds
    for key in ("flat_fares", "card_discount"):
        merged = {}
        for r in reversed([home] + extra):
            merged.update(r.get(key) or {})
        view[key] = merged or None
    rules = [r.get("transfer_rules") for r in [home] + extra if r.get("transfer_rules")]
    if rules:
        same = {}
        for tr in reversed(rules):
            same.update(tr.get("same_agency") or {})
        view["transfer_rules"] = {"verified": rules[0].get("verified"), "same_agency": same,
                                  "pairs": [p for tr in rules for p in tr.get("pairs") or []],
                                  "paper_only": sorted({a for tr in rules for a in tr.get("paper_only") or []})}
    hol = {}
    for r in [home] + extra:
        for h in r.get("holidays") or []:
            h = h if isinstance(h, dict) else {"date": h}
            hol.setdefault(h["date"], h)
    view["holidays"] = [hol[k] for k in sorted(hol)]
    view["ride_areas"] = [a for r in [home] + extra for a in r.get("ride_areas") or []]
    for key in ("ride_pricing_areas", "ride_pricing_outside", "taxi_pricing_default"):
        view[key] = next((r[key] for r in [home] + extra + list(pricing_from) if r.get(key)), None)
    dest_regions = [r for r in [home] + extra
                    if d_lat is not None and r.get("bbox") and in_bbox(d_lat, d_lon, r["bbox"])]
    dest_regions.sort(key=lambda r: (bool(r.get("intercity")), _bbox_area(r["bbox"])))
    view["final_ride_pricing"] = next((r["ride_pricing"] for r in dest_regions if r.get("ride_pricing")),
                                      home.get("ride_pricing"))
    return view


def holiday_modes(region, feed_cfg):
    """{date: 'none' | 'sunday' | 'saturday'} for a feed whose file does not encode holidays: the feed's own
    `holiday_dates` when given (agencies differ), else the region's statutory holidays."""
    mode = (feed_cfg or {}).get("holiday_service") or "as_feed"
    if mode == "as_feed":
        return {}
    dates = (feed_cfg or {}).get("holiday_dates")
    if dates is None:
        dates = [h["date"] if isinstance(h, dict) else h for h in (region or {}).get("holidays") or []
                 if not isinstance(h, dict) or h.get("stat", True)]
    out = {}
    for x in dates:
        try:
            out[parse_date(x.replace("-", ""))] = mode
        except (ValueError, AttributeError):
            continue
    return out


def manual_feed_zip(feed):
    """Write a region file's hand-entered timetable (an operator with no public GTFS) as a small GTFS zip in
    the cache, so it loads like any other feed. feed['manual'] holds agency, services and routes."""
    m = feed["manual"]
    blob = json.dumps(m, sort_keys=True).encode("utf-8")
    path = os.path.join(cache_dir(), "manual-%s-%s.zip" % (re.sub(r"[^A-Za-z0-9_.-]", "_", feed["id"]),
                                                          hashlib.sha1(blob).hexdigest()[:10]))
    if os.path.exists(path):
        return path

    def csv_text(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue()

    stop_ids, stops, routes, trips, times, cal, cal_dates = {}, [], [], [], [], [], []
    for sid, sv in sorted(m["services"].items()):
        days = sv.get("days", "1111111")
        cal.append([sid] + list(days) + [sv["start"].replace("-", ""), sv["end"].replace("-", "")])
        cal_dates += [[sid, x.replace("-", ""), "2"] for x in sv.get("except", [])]
        cal_dates += [[sid, x.replace("-", ""), "1"] for x in sv.get("extra", [])]
    for ri, r in enumerate(m["routes"]):
        rid = "r%d" % ri
        routes.append([rid, "a", r.get("route", ""), r.get("name", ""), r.get("type", 3)])
        for ti, t in enumerate(r["trips"]):
            tid = "%s_t%d" % (rid, ti)
            trips.append([rid, t["service"], tid, t.get("headsign", "")])
            for seq, st in enumerate(t["stops"]):
                name, lat, lon, arr = st[0], st[1], st[2], st[3]
                dep = st[4] if len(st) > 4 else arr
                key = (name, round(float(lat), 5), round(float(lon), 5))
                if key not in stop_ids:
                    stop_ids[key] = "s%d" % len(stop_ids)
                    stops.append([stop_ids[key], name, key[1], key[2]])
                times.append([tid, arr + ":00", dep + ":00", stop_ids[key], seq])
    files = {
        "agency.txt": csv_text(["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_phone"],
                               [["a", m["agency"], m.get("url", ""), m.get("timezone", "America/Toronto"),
                                 m.get("phone", "")]]),
        "stops.txt": csv_text(["stop_id", "stop_name", "stop_lat", "stop_lon"], stops),
        "routes.txt": csv_text(["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
                               routes),
        "trips.txt": csv_text(["route_id", "service_id", "trip_id", "trip_headsign"], trips),
        "stop_times.txt": csv_text(["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
                                   times),
        "calendar.txt": csv_text(["service_id"] + WEEKDAYS + ["start_date", "end_date"], cal),
        "calendar_dates.txt": csv_text(["service_id", "date", "exception_type"], cal_dates),
    }
    tmp = "%s.%d.part" % (path, os.getpid())
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    os.replace(tmp, path)
    return path


FEED_ID_RE = re.compile(r"^([A-Za-z0-9_.-]+)=(.+)$")


def parse_feed_spec(spec, used_ids):
    """'id=URL', a bare URL or a local path -> (unique id, source)."""
    m = FEED_ID_RE.match(spec)
    if m and not os.path.exists(spec):
        fid, src = m.group(1), m.group(2)
    else:
        src = spec
        parts = urllib.parse.urlsplit(src)
        base = os.path.basename(parts.path) or parts.netloc.rpartition("@")[2]
        fid = re.sub(r"\W+", "_", base)[:40].strip("_") or "feed"
    unique, n = fid, 2
    while unique in used_ids:
        unique, n = "%s_%d" % (fid, n), n + 1
    used_ids.add(unique)
    return unique, src


def fetch_url(fid, url, max_age_h, refresh, warnings=None):
    """Download a feed (or use a local path) with a per-URL cache file."""
    if os.path.exists(url):
        return url
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    path = os.path.join(cache_dir(), "%s-%s.zip" % (re.sub(r"[^A-Za-z0-9_.-]", "_", fid), digest))
    if os.path.exists(path) and not refresh and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        return path
    tmp = "%s.%d.part" % (path, os.getpid())
    try:
        log("downloading %s: %s" % (fid, safe_url(url)))
        sp = urllib.parse.urlsplit(url)
        if sp.scheme.lower() == "file" and (sp.query or sp.fragment):
            # a local file has no query: open the path alone (Python 3.12 and older read '?v=2' as part of the
            # file name); the full URL still names the cache file
            url_open = urllib.parse.urlunsplit(sp._replace(query="", fragment=""))
        else:
            url_open = url
        req = urllib.request.Request(url_open, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as out:
            size = int((getattr(r, "headers", None) or {}).get("Content-Length") or 0)
            if size > FEED_MAX_BYTES:
                raise ValueError("the server offers %.0f MB, over the %d MB feed limit"
                                 % (size / 1e6, FEED_MAX_BYTES // 10 ** 6))
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                if not got and not chunk.startswith(b"PK"):   # an error page or junk: stop before the rest arrives
                    raise ValueError("not a zip file (the server sent something else)")
                got += len(chunk)
                if got > FEED_MAX_BYTES:
                    raise ValueError("download passed the %d MB feed limit" % (FEED_MAX_BYTES // 10 ** 6))
                out.write(chunk)
        with zipfile.ZipFile(tmp) as z:
            check_zip_sizes(z)
            names = {os.path.basename(n) for n in z.namelist()}
        if not {"stops.txt", "stop_times.txt", "trips.txt"} <= names:
            if any(n.endswith(".zip") for n in names):
                raise ValueError("a zip of GTFS zips (one per mode), which is not supported; unpack it and pass "
                                 "each inner zip with --feed")
            raise ValueError("not a GTFS zip (missing stops/trips/stop_times)")
        try:
            os.replace(tmp, path)
        except OSError:
            if not os.path.exists(path):   # another run is replacing it right now
                raise
        return path
    except Exception as e:  # noqa: BLE001
        if os.path.exists(path):
            age_d = (time.time() - os.path.getmtime(path)) / 86400.0
            log("warning: could not refresh %s (%s); using cached copy" % (fid, _scrub(e, url)))
            if warnings is not None:
                warnings.append("%s could not be refreshed (%s); used a cached copy %.1f days old"
                                % (fid, _scrub(e, url), age_d))
            if isinstance(e, urllib.error.HTTPError):
                close_quietly(e)
            return path
        raise
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


FEED_MAX_BYTES = 1000 * 10 ** 6       # a feed download larger than this is refused (the TTC feed is about 80 MB)
MEMBER_MAX_BYTES = 4000 * 10 ** 6     # largest file inside a feed zip, unpacked (TTC: about 412 MB for all files)
MEMBER_MAX_RATIO = 100                # unpacked / packed size above which a large member is a zip bomb, not text
MEMBER_RATIO_FROM = 50 * 10 ** 6      # the ratio check starts at this unpacked size (small files pack very well)


def check_zip_sizes(z):
    """Refuse a zip whose members would unpack to absurd sizes before anything reads them."""
    for info in z.infolist():
        if info.file_size > MEMBER_MAX_BYTES or (info.file_size > MEMBER_RATIO_FROM and
                                                 info.file_size > MEMBER_MAX_RATIO * max(1, info.compress_size)):
            raise ValueError("%s would unpack to %.0f MB from %.1f MB packed, which is not a timetable file; the "
                             "feed was refused" % (os.path.basename(info.filename), info.file_size / 1e6,
                                                   info.compress_size / 1e6))


class GtfsZip:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        check_zip_sizes(self.z)
        self.names = {}
        for n in self.z.namelist():
            if not n.endswith("/"):
                self.names.setdefault(os.path.basename(n), n)

    def has(self, name):
        return name in self.names

    def table(self, name):
        # GTFS must be UTF-8; replace bad bytes instead of dropping a whole agency over one accent
        fh = io.TextIOWrapper(self.z.open(self.names[name]), encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(fh)
        header = [h.strip().strip('"') for h in next(reader, [])]
        return {h: i for i, h in enumerate(header)}, reader


def col(row, idx, name, default=""):
    i = idx.get(name)
    if i is None or i >= len(row):
        return default
    return row[i].strip()


def feed_timezone(path):
    gz = GtfsZip(path)
    if gz.has("agency.txt"):
        idx, rows = gz.table("agency.txt")
        for r in rows:
            if r and col(r, idx, "agency_timezone"):
                return col(r, idx, "agency_timezone")
    return None


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def active_services(gz, dates):
    out = {d: set() for d in dates}
    span = [None, None]

    def note(d):
        if span[0] is None or d < span[0]:
            span[0] = d
        if span[1] is None or d > span[1]:
            span[1] = d

    if gz.has("calendar.txt"):
        idx, rows = gz.table("calendar.txt")
        for r in rows:
            if not r:
                continue
            sid = col(r, idx, "service_id")
            try:
                start, end = parse_date(col(r, idx, "start_date")), parse_date(col(r, idx, "end_date"))
            except (ValueError, IndexError):
                continue
            note(start)
            note(end)
            for d in dates:
                if start <= d <= end and col(r, idx, WEEKDAYS[d.weekday()]) == "1":
                    out[d].add(sid)
    if gz.has("calendar_dates.txt"):
        idx, rows = gz.table("calendar_dates.txt")
        for r in rows:
            if not r:
                continue
            try:
                d = parse_date(col(r, idx, "date"))
            except (ValueError, IndexError):
                continue
            et = col(r, idx, "exception_type")
            if et == "1":
                note(d)
            if d in out:
                if et == "1":
                    out[d].add(col(r, idx, "service_id"))
                elif et == "2":
                    out[d].discard(col(r, idx, "service_id"))
    return out, span


def stop_cells(path, cell_deg):
    """The grid cells (cell_deg degrees square) that contain at least one stop: a small map of where a feed
    has service, stored in region files so far-away feeds can be skipped without downloading them."""
    gz = GtfsZip(path)
    idx, rows = gz.table("stops.txt")
    cells = set()
    for r in rows:
        try:
            lat, lon = float(col(r, idx, "stop_lat")), float(col(r, idx, "stop_lon"))
        except (ValueError, IndexError):
            continue
        if lat or lon:
            cells.add((int(math.floor(lat / cell_deg)), int(math.floor(lon / cell_deg))))
    return cells


def coverage_near(cov, near):
    """True when any cell of a stored stop map lies within reach of any (lat, lon, meters) in `near`."""
    c = float(cov["cell_deg"])
    for part in cov["cells"].split(";"):
        if not part:
            continue
        i, j = part.split(",")
        lo_lat, lo_lon = int(i) * c, int(j) * c
        for lat, lon, meters in near:
            clat = min(max(lat, lo_lat), lo_lat + c)   # nearest point of the cell
            clon = min(max(lon, lo_lon), lo_lon + c)
            if haversine_m(lat, lon, clat, clon) <= meters:
                return True
    return False


def feed_check(path, day, near):
    """(has service on day, (span_from, span_to), has a stop near any of `near` [(lat, lon, meters)])."""
    gz = GtfsZip(path)
    act, span = active_services(gz, [day])
    close = not near
    if near:
        idx, rows = gz.table("stops.txt")
        for r in rows:
            try:
                lat, lon = float(col(r, idx, "stop_lat")), float(col(r, idx, "stop_lon"))
            except (ValueError, IndexError):
                continue
            if any(haversine_m(lat, lon, a, b) <= m for a, b, m in near):
                close = True
                break
    return bool(act[day]), span, close


def choose_feed_file(fid, label, urls, day, near, args, warnings, bad_urls=None):
    """First URL whose file has service on the trip date. Returns path, or None when the feed has no
    stop near the origin or destination. URLs that failed earlier in this run (`bad_urls`, url -> error)
    are not downloaded again."""
    first, notes = None, []
    bad_urls = {} if bad_urls is None else bad_urls
    for i, url in enumerate(urls):   # messages show safe_url(url): a feed URL can carry an API key
        if url in bad_urls:
            notes.append("%s failed (%s)" % (safe_url(url), _scrub(bad_urls[url], url)))
            continue
        try:
            path = fetch_url(fid, url, args.max_age_hours, args.refresh, warnings)
            covers, span, close = feed_check(path, day, near)
        except Exception as e:  # noqa: BLE001
            bad_urls[url] = e
            notes.append("%s failed (%s)" % (safe_url(url), _scrub(e, url)))
            continue
        if not close:
            return None
        if covers:
            if i > 0:
                plain = urllib.parse.urlsplit(url).scheme == "http"
                warnings.append("%s: used fallback copy #%d (%s) because: %s%s" % (
                    label, i, safe_url(url), "; ".join(notes),
                    (". This copy came over unencrypted http and could not be checked against the agency's own "
                     "site, so confirm the times with the operator" if plain else "")))
            return path
        notes.append("%s covers %s..%s, not %s" % (safe_url(url), span[0], span[1], day.isoformat()))
        first = first or path
    if first:
        return first  # load_feed reports the missing service day
    raise RuntimeError("; ".join(notes) or "no URL")


def fill_missing_times(rows):
    """rows: sorted [seq, arr, dep, stop, pk, dk]; interpolate stops with no times."""
    known = [i for i, r in enumerate(rows) if r[1] is not None]
    if len(known) == len(rows):
        return rows
    if not known:
        return []
    out = []
    for a, b in zip(known, known[1:]):
        out.append(rows[a])
        t0, t1 = rows[a][2], rows[b][1]
        for k in range(a + 1, b):
            t = int(t0 + (t1 - t0) * (k - a) / (b - a))
            r = list(rows[k])
            r[1] = r[2] = t
            out.append(r)
    out.append(rows[known[-1]])
    return out


class Network:
    def __init__(self):
        self.stop_name, self.stop_lat, self.stop_lon, self.stop_feed, self.stop_zone = [], [], [], [], []
        self.trips = []   # (feed_label, agency_name, route_short, route_long, route_type, headsign, trip_id, route_id)
        self.conns = []   # (dep, arr, s_from, s_to, trip_idx, pickup_ok, dropoff_ok, row_k)
        self.feeds = []   # dicts for reporting
        self.fares = {}   # feed_label -> GTFS fare model
        self.fare_urls = {}  # (feed_label, agency_name) -> agency_fare_url
        self.xfer = {}    # (stop, stop) -> min seconds, or -1 when transfers.txt forbids it
        self.stop_heads = {}  # (trip_idx, row_k) -> stop_headsign where it differs from the trip headsign
        self.trip_buffer = []  # per trip: seconds a rider must be at the stop before it leaves (coaches, trains)
        self.request = {}  # (trip_idx, row_k) -> "b" (boarding) / "a" (alighting) on request only
        self.idle_stops = {}  # feed label -> stop coordinates, for feeds with no service on the date
        self.feed_stops = {}  # feed label -> coordinates of every stop in the file, served in the window or not
        self.warnings = []
        self.timezones = set()

    def add_stop(self, name, lat, lon, feed, zone):
        self.stop_name.append(name)
        self.stop_lat.append(lat)
        self.stop_lon.append(lon)
        self.stop_feed.append(feed)
        self.stop_zone.append(zone)
        return len(self.stop_name) - 1


def load_fares(gz):
    if not gz.has("fare_attributes.txt"):
        return None
    attrs = {}
    idx, rows = gz.table("fare_attributes.txt")
    for r in rows:
        if not r:
            continue
        try:
            attrs[col(r, idx, "fare_id")] = (float(col(r, idx, "price")), col(r, idx, "currency_type"),
                                             col(r, idx, "transfers"))
        except ValueError:
            continue
    rules = {}
    if gz.has("fare_rules.txt"):
        idx, rows = gz.table("fare_rules.txt")
        for r in rows:
            if not r or col(r, idx, "contains_id"):
                continue
            key = (col(r, idx, "origin_id"), col(r, idx, "destination_id"))
            rules.setdefault(key, []).append((col(r, idx, "fare_id"), col(r, idx, "route_id")))
    if not attrs:
        return None
    return {"attrs": attrs, "rules": rules}


def fare_lookup(model, route_id, oz, dz):
    if not model["rules"]:
        if len(model["attrs"]) == 1:
            return next(iter(model["attrs"].values()))
        return None
    best = None
    for key in ((oz, dz), (oz, ""), ("", dz), ("", "")):
        for fid, rid in model["rules"].get(key, ()):
            if rid and rid != route_id:
                continue
            a = model["attrs"].get(fid)
            if a and (best is None or a[0] < best[0]):
                best = a
    return best


def holiday_substitute(gz, d, mode):
    """Services to run on holiday d for a feed that does not encode it: none, or those of the nearest
    Sunday (or Saturday) that the file covers, looking back a week first, then ahead."""
    if mode == "none":
        return set()
    target = 6 if mode == "sunday" else 5
    back = (d.weekday() - target) % 7 or 7
    ahead = (target - d.weekday()) % 7 or 7
    for ref in (d - dt.timedelta(days=back), d + dt.timedelta(days=ahead)):
        act, _ = active_services(gz, [ref])
        if act[ref]:
            return act[ref]
    return None   # the file covers neither: nothing to substitute


def load_feed(net, fid, label, path, day, t0, t_end, holidays=None, local_tz=None, region=None,
              exclude_routes=None, board_buffer_s=0):
    """Add a feed's connections between t0 and t_end (seconds after midnight of `day`, local time).
    holidays: {date: mode} for holidays the file does not mark. local_tz: the trip's zone; a feed that keeps
    another clock (FlixBus publishes UTC) is shifted to it. exclude_routes: route ids or names to drop."""
    gz = GtfsZip(path)
    tz, agencies, first_agency = None, {}, None
    if gz.has("agency.txt"):
        idx, rows = gz.table("agency.txt")
        for r in rows:
            if not r:
                continue
            name = safe_text(col(r, idx, "agency_name"))
            agencies[col(r, idx, "agency_id")] = name
            first_agency = first_agency or name
            tz = tz or col(r, idx, "agency_timezone")
            if col(r, idx, "agency_fare_url"):
                net.fare_urls.setdefault((label, name), col(r, idx, "agency_fare_url"))
            elif col(r, idx, "agency_url"):
                net.fare_urls.setdefault((label, name), col(r, idx, "agency_url"))
    days = [day - dt.timedelta(days=1), day, day + dt.timedelta(days=1)]
    shifts = [0, 0, 0]   # per service day, so a window across a clock change stays right
    if tz and local_tz and not same_clock(tz, local_tz):
        noon = dt.datetime.combine(day, dt.time(12))
        unknown = [z for z, reg in ((local_tz, region), (tz, None))
                   if offset_at_local(noon, z, reg)[1] == "machine clock"]
        if unknown:   # the computer's own clock says nothing about either zone: keep the feed's times as they are
            net.warnings.append("%s keeps %s time, but this Python cannot convert %s (no tz database and not in the "
                                "built-in table), so its times are used unchanged as %s time. Check them, or "
                                "`pip install tzdata`." % (label, tz, " or ".join(unknown), local_tz))
        else:
            def zone_gap(d):
                noon = dt.datetime.combine(d, dt.time(12))
                return int((offset_at_local(noon, local_tz, region)[0]
                            - offset_at_local(noon, tz, None)[0]).total_seconds())
            shifts = [zone_gap(d) for d in days]
            log("%s keeps %s time: shifted %+.1f h to %s" % (label, tz, shifts[1] / 3600.0, local_tz))
        tz = local_tz
    if tz:
        net.timezones.add(tz)

    routes, skip_routes = {}, set()
    idx, rows = gz.table("routes.txt")
    for r in rows:
        if not r:
            continue
        short = safe_text(col(r, idx, "route_short_name"))
        long_ = safe_text(col(r, idx, "route_long_name"))
        try:
            rtype = int(col(r, idx, "route_type", "3") or 3)
        except ValueError:
            rtype = 3
        agency = agencies.get(col(r, idx, "agency_id")) or first_agency or label
        routes[col(r, idx, "route_id")] = (short or long_, long_, rtype, agency)
        rid = col(r, idx, "route_id")
        if exclude_routes and {rid, short, long_} & set(exclude_routes):
            skip_routes.add(rid)

    offsets = [-86400 + shifts[0], shifts[1], 86400 + shifts[2]]
    act, span = active_services(gz, days)
    substituted = False
    for d in days:
        mode = (holidays or {}).get(d)
        if mode:
            sub = holiday_substitute(gz, d, mode)
            act[d] = sub or set()
            if d == day and sub is not None:
                substituted = True
                net.warnings.append("%s: %s is a holiday this file does not mark, so %s (region notes)."
                                    % (label, d.isoformat(), "it has no service" if mode == "none"
                                       else "its %s schedule is used" % mode.capitalize()))
    info = {"id": fid, "name": label, "path": os.path.basename(path),   # the file name only, not the home folder
            "service_from": span[0].isoformat() if span[0] else None,
            "service_to": span[1].isoformat() if span[1] else None,
            "active_services_on_date": len(act[day])}
    net.feeds.append(info)
    if not act[day] and not substituted:
        net.warnings.append("%s has NO service on %s (file covers %s to %s). Its trips are missing from this answer."
                            % (label, day.isoformat(), info["service_from"], info["service_to"]))
    fares = load_fares(gz)
    if fares:
        net.fares[label] = fares

    trip_off, trip_meta = {}, {}
    idx, rows = gz.table("trips.txt")
    for r in rows:
        if not r:
            continue
        sid = col(r, idx, "service_id")
        offs = tuple(o for o, d in zip(offsets, days) if sid in act[d])
        if offs and skip_routes and col(r, idx, "route_id") in skip_routes:
            continue
        if offs:
            tid = col(r, idx, "trip_id")
            trip_off[tid] = offs
            trip_meta[tid] = (col(r, idx, "route_id"), safe_text(col(r, idx, "trip_headsign")))

    # headway-based trips: stop_times holds one template run, repeated every headway_secs
    freq = {}
    if gz.has("frequencies.txt"):
        idx, rows = gz.table("frequencies.txt")
        for r in rows:
            tid = col(r, idx, "trip_id") if r else ""
            if tid in trip_off:
                try:
                    start, end = parse_time(col(r, idx, "start_time")), parse_time(col(r, idx, "end_time"))
                    head = int(float(col(r, idx, "headway_secs")))
                except (ValueError, TypeError):
                    continue
                if start is not None and end is not None and head > 0:
                    freq.setdefault(tid, []).append((start, end, head))

    stops_raw = {}
    idx, rows = gz.table("stops.txt")
    for r in rows:
        if not r:
            continue
        try:
            lat, lon = float(col(r, idx, "stop_lat")), float(col(r, idx, "stop_lon"))
        except ValueError:
            continue
        stops_raw[col(r, idx, "stop_id")] = (safe_text(col(r, idx, "stop_name")), lat, lon, col(r, idx, "zone_id"))
    net.feed_stops[label] = [(v[1], v[2]) for v in stops_raw.values()]
    if not act[day]:
        net.idle_stops[label] = net.feed_stops[label]

    lo, hi = t0 - 3600, t_end + 3 * 3600

    def stream_stop_times(only=None):
        """Rows for active trips. Timed rows far outside the window are skipped unless the trip is
        headway-based or listed in `only` (trips that need every timepoint for interpolation)."""
        found, untimed = {}, set()
        idx, rows = gz.table("stop_times.txt")
        i_trip, i_arr, i_dep = idx["trip_id"], idx["arrival_time"], idx["departure_time"]
        i_stop, i_seq = idx["stop_id"], idx["stop_sequence"]
        i_pk, i_dk, i_sh = idx.get("pickup_type"), idx.get("drop_off_type"), idx.get("stop_headsign")
        for r in rows:
            if not r:
                continue
            tid = r[i_trip].strip()
            if only is not None and tid not in only:
                continue
            offs = trip_off.get(tid)
            if offs is None:
                continue
            a, d = parse_time(r[i_arr]), parse_time(r[i_dep])
            if a is None:
                a = d
            if d is None:
                d = a
            if a is None:
                untimed.add(tid)
            elif only is None and tid not in freq and not any(d + o >= lo and a + o <= hi for o in offs):
                continue
            pk = i_pk is None or i_pk >= len(r) or r[i_pk].strip() != "1"
            dk = i_dk is None or i_dk >= len(r) or r[i_dk].strip() != "1"
            try:
                seq = int(float(r[i_seq]))
            except ValueError:
                continue
            sh = r[i_sh].strip() if i_sh is not None and i_sh < len(r) else ""
            pkc = r[i_pk].strip() if i_pk is not None and i_pk < len(r) else ""
            dkc = r[i_dk].strip() if i_dk is not None and i_dk < len(r) else ""
            found.setdefault(tid, []).append([seq, a, d, r[i_stop].strip(), pk, dk, sh, pkc, dkc])
        return found, untimed

    per_trip, untimed = stream_stop_times()
    if untimed:  # interpolation needs the timed stops on both sides, even outside the window
        per_trip.update(stream_stop_times(only=untimed)[0])

    local = {}

    def gstop(sid):
        g = local.get(sid)
        if g is None:
            raw = stops_raw.get(sid)
            if raw is None:
                return None
            g = local[sid] = net.add_stop(raw[0], raw[1], raw[2], label, raw[3])
        return g

    for tid, trows in per_trip.items():
        trows.sort(key=lambda x: x[0])
        trows = fill_missing_times(trows)
        if len(trows) < 2:
            continue
        rid, head = trip_meta[tid]
        short, long_, rtype, agency = routes.get(rid, (rid, "", 3, label))
        shifts = [0]
        if tid in freq:
            first_dep, shifts = trows[0][2], []
            for start, end, step in freq[tid]:
                t = start
                while t < end:
                    shifts.append(t - first_dep)
                    t += step
        for shift in shifts:
            for o in trip_off[tid]:
                ti = None
                for k in range(len(trows) - 1):
                    r0, r1 = trows[k], trows[k + 1]
                    dep = r0[2] + o + shift
                    if dep < t0 or dep > t_end:
                        continue
                    s0, s1 = gstop(r0[3]), gstop(r1[3])
                    if s0 is None or s1 is None:
                        continue
                    if ti is None:
                        ti = len(net.trips)
                        net.trips.append((label, agency, short, long_, rtype, head, tid, rid))
                        net.trip_buffer.append(int(board_buffer_s))
                    if r0[6] and r0[6] != head:   # stop_headsign: what the sign says at this stop
                        net.stop_heads[(ti, k)] = safe_text(r0[6])
                    flag = ("b" if r0[7] in ("2", "3") else "") + ("a" if r1[8] in ("2", "3") else "")
                    if flag:   # phone ahead or tell the driver
                        net.request[(ti, k)] = flag
                    net.conns.append((dep, r1[1] + o + shift, s0, s1, ti, r0[4], r1[5], k))

    # transfers.txt: forbidden transfers and minimum transfer times
    if gz.has("transfers.txt"):
        idx, rows = gz.table("transfers.txt")
        for r in rows:
            if not r:
                continue
            if any(col(r, idx, k) for k in ("from_route_id", "to_route_id", "from_trip_id", "to_trip_id")):
                continue  # route- or trip-specific rules are not modelled; never apply them stop-wide
            a, b = local.get(col(r, idx, "from_stop_id")), local.get(col(r, idx, "to_stop_id"))
            if a is None or b is None:
                continue
            ttype = col(r, idx, "transfer_type")
            if ttype == "3":
                net.xfer[(a, b)] = -1
            elif ttype == "2":
                try:
                    net.xfer[(a, b)] = max(net.xfer.get((a, b), 0), int(float(col(r, idx, "min_transfer_time"))))
                except ValueError:
                    pass
    return info


# --------------------------------------------------------------------------- discovery (any city)

def load_catalog(max_age_h=168.0):
    path = os.path.join(cache_dir(), "mobilitydatabase_feeds.csv")
    if not (os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600):
        err = None
        for url in MOBILITYDB_CSV_URLS:
            try:
                log("downloading MobilityDatabase catalog: %s" % url)
                data = http_get(url, timeout=120)
                tmp = "%s.%d.part" % (path, os.getpid())
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
                break
            except Exception as e:  # noqa: BLE001
                err = e
        else:
            if not os.path.exists(path):
                raise SystemExit("could not download the MobilityDatabase catalog: %s" % err)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _norm(s):
    """Letters and digits only, any script (so Japanese or Greek names do not collapse to '')."""
    return "".join(ch for ch in (s or "").casefold() if ch.isalnum())


def _provider_key(s):
    return _norm(re.sub(r"\s*\([^)]*\)\s*$", "", s or ""))   # 'Public Transport Victoria (PTV)' == 'Public Transport Victoria'


FILLER_WORDS = {"supplemented", "supplement", "gtfs", "static", "schedule", "schedules", "feed", "data", "full",
                "complete", "official", "current", "latest", "new"}


def _name_key(s):
    return _norm(" ".join(w for w in re.split(r"\W+", (s or "").lower()) if w and w not in FILLER_WORDS))


def bbox_iou(a, b):
    w = min(a["max_lon"], b["max_lon"]) - max(a["min_lon"], b["min_lon"])
    h = min(a["max_lat"], b["max_lat"]) - max(a["min_lat"], b["min_lat"])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    area = lambda x: (x["max_lat"] - x["min_lat"]) * (x["max_lon"] - x["min_lon"])  # noqa: E731
    return inter / (area(a) + area(b) - inter)


def discover_feeds(box, rows=None, places=None):
    """GTFS feeds whose bounding box intersects `box`, smallest area first. Feeds with no bounding box
    in the catalog are matched by town name (`places`, lowercase). Returns (feeds, too_big, far, duplicates)."""
    rows = rows if rows is not None else load_catalog()
    qc = box_center(box)
    places = {p for p in (places or ()) if len(p) >= 3}
    hits, too_big, far = [], [], []
    for r in rows:
        if (r.get("data_type") or "").strip() != "gtfs":
            continue
        if (r.get("status") or "").strip().lower() in ("deprecated", "inactive", "development"):
            continue
        fid = r.get("id") or r.get("mdb_source_id") or ""
        latest = (r.get("urls.latest") or "").strip()
        direct = (r.get("urls.direct_download") or "").strip()
        auth = (r.get("urls.authentication_type") or "0").strip() not in ("", "0")
        # web downloads only: a catalog row must not point the script at a local file or another protocol
        urls = [u for u in ([latest] + ([] if auth else [direct]))
                if u and urllib.parse.urlsplit(u).scheme.lower() in ("http", "https")]
        if not urls:
            continue
        item = {"id": fid, "provider": (r.get("provider") or "").strip(), "name": (r.get("name") or "").strip(),
                "municipality": r.get("location.municipality"), "status": r.get("status"),
                "country": (r.get("location.country_code") or "").upper() or None,
                "subdivision": (r.get("location.subdivision_name") or "").lower() or None,
                "urls": urls, "needs_key": auth, "official": (r.get("is_official") or "").lower() == "true",
                "license": r.get("urls.license")}
        try:
            b = {"min_lat": float(r["location.bounding_box.minimum_latitude"]),
                 "max_lat": float(r["location.bounding_box.maximum_latitude"]),
                 "min_lon": float(r["location.bounding_box.minimum_longitude"]),
                 "max_lon": float(r["location.bounding_box.maximum_longitude"])}
        except (KeyError, ValueError, TypeError):
            # no bounding box in the catalog: keep it only when it names a town at either end of the trip
            text = " ".join([(r.get("location.municipality") or ""), item["provider"], item["name"]]).lower()
            if places and any(re.search(r"\b%s\b" % re.escape(p), text) for p in places):
                item.update(area=0.0, bbox=None, center_km=0, no_bbox=True)
                hits.append(item)
            continue
        if not bbox_overlap(b, box):
            continue
        area = (b["max_lat"] - b["min_lat"]) * (b["max_lon"] - b["min_lon"])
        bc = box_center(b)
        item.update(area=area, bbox=b, center_km=round(haversine_m(qc[0], qc[1], bc[0], bc[1]) / 1000.0))
        if area > DISCOVER_MAX_AREA:
            too_big.append(item)
        elif item["center_km"] > DISCOVER_FAR_KM:
            far.append(item)
        else:
            hits.append(item)
    # the same feed is often listed by several catalog sources (mdb-, tld-, ntd-...) or twice by one
    # agency (e.g. VBB): drop those copies, but keep separate feeds of one agency ("Bus" and "Rail")
    # among copies prefer: smaller area, MobilityDatabase's own id, official, "Supplemented" (planned changes)
    hits.sort(key=lambda h: (h["area"], not h["id"].startswith("mdb-"), not h["official"],
                             "supplement" not in h["name"].lower()))
    out, dups = [], []
    for h in hits:
        dup = None
        for k in out:
            if _provider_key(k["provider"]) != _provider_key(h["provider"]):
                continue
            same_name = _name_key(k["name"]) == _name_key(h["name"]) or not k["name"] or not h["name"]
            if k.get("bbox") is None or h.get("bbox") is None:
                if same_name:
                    dup = k
                    break
                continue
            if same_name and bbox_iou(k["bbox"], h["bbox"]) >= DISCOVER_DUP_IOU:
                dup = k
                break
        (dups if dup else out).append(h)
    too_big.sort(key=lambda h: (not h["official"], h["area"]))
    return out, too_big, far, dups


# --------------------------------------------------------------------------- geometry helpers

class Grid:
    def __init__(self, lats, lons, ids, cell_m, ref_lat):
        self.dlat = cell_m / 111320.0
        self.dlon = cell_m / (111320.0 * max(0.2, math.cos(math.radians(ref_lat))))
        self.cells = {}
        for i in ids:
            self.cells.setdefault(self.key(lats[i], lons[i]), []).append(i)

    def key(self, lat, lon):
        return int(math.floor(lat / self.dlat)), int(math.floor(lon / self.dlon))

    def near(self, lat, lon, rings=1):
        ky, kx = self.key(lat, lon)
        for dy in range(-rings, rings + 1):
            for dx in range(-rings, rings + 1):
                for i in self.cells.get((ky + dy, kx + dx), ()):
                    yield i


def build_footpaths(net, used, radius_m, min_change_s):
    """Walking transfers between different stops. A transfer never takes less than the minimum change
    time; transfers.txt can forbid a pair or demand more time."""
    ref = sum(net.stop_lat[i] for i in used) / max(1, len(used))
    grid = Grid(net.stop_lat, net.stop_lon, used, radius_m, ref)
    used_set = set(used)
    foot = {}
    for i in used:
        lst = {}
        for j in grid.near(net.stop_lat[i], net.stop_lon[i]):
            if j != i:
                d = haversine_m(net.stop_lat[i], net.stop_lon[i], net.stop_lat[j], net.stop_lon[j])
                if d <= radius_m:
                    lst[j] = max(walk_s(d) + 60, int(min_change_s))
        if lst:
            foot[i] = lst
    for (a, b), need in net.xfer.items():
        if a == b or a not in used_set or b not in used_set:
            continue
        if need < 0:
            foot.get(a, {}).pop(b, None)
        else:
            d = haversine_m(net.stop_lat[a], net.stop_lon[a], net.stop_lat[b], net.stop_lon[b])
            foot.setdefault(a, {})[b] = max(foot.get(a, {}).get(b, 0), need, walk_s(d) + 60, int(min_change_s))
    return {i: list(v.items()) for i, v in foot.items() if v}


def same_stop_change(net, min_change_s):
    out = {}
    for (a, b), need in net.xfer.items():
        if a == b:
            out[a] = -1 if need < 0 else max(int(min_change_s), need)
    return out


def ride_area(region, lat, lon):
    """The region file's note on ride-hail near a point (nearest area whose radius covers it):
    {name, uber: yes|sparse|no, service, taxis: [{name, phone, note}], note} or None."""
    best = None
    for a in (region or {}).get("ride_areas") or []:
        d = haversine_m(lat, lon, a["lat"], a["lon"]) / 1000.0
        if d <= a.get("radius_km", 10.0) and (best is None or d < best[0]):
            best = (d, a)
    if best is None:
        return None
    a = dict(best[1])
    a.setdefault("service", {"yes": "Uber/Lyft", "sparse": "Uber/Lyft (sparse: book ahead) or taxi",
                             "no": "taxi (no Uber/Lyft)"}.get(a.get("uber", "yes"), "Uber/Lyft"))
    return a


def taxis_text(area):
    """The taxis a ride area lists, with their own caveats: 'Name phone (note); ...'."""
    return "; ".join("%s %s%s" % (t.get("name", ""), t.get("phone", ""), " (%s)" % t["note"] if t.get("note") else "")
                     for t in (area or {}).get("taxis") or [])


def cluster_stops(net, pairs):
    """[(distance, stop)] sorted by distance -> hubs {lat, lon, stops, straight_m}: a stop within CLUSTER_M
    of a hub's first stop joins that hub."""
    clusters, cgrid = [], {}
    cell = CLUSTER_M / 111320.0
    # a degree of longitude is shorter away from the equator: size the cell for the most poleward stop, so the
    # 3 x 3 neighbourhood always covers CLUSTER_M east-west
    top = max([abs(net.stop_lat[s]) for _d, s in pairs] or [0.0])
    cell_lon = CLUSTER_M / (111320.0 * max(0.05, math.cos(math.radians(top))))
    for d, s in pairs:
        lat, lon = net.stop_lat[s], net.stop_lon[s]
        ky, kx = int(math.floor(lat / cell)), int(math.floor(lon / cell_lon))
        home = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for ci in cgrid.get((ky + dy, kx + dx), ()):
                    c = clusters[ci]
                    if home is None and haversine_m(lat, lon, c["lat"], c["lon"]) <= CLUSTER_M:
                        home = ci
        if home is None:
            clusters.append({"lat": lat, "lon": lon, "stops": [s], "straight_m": d})
            cgrid.setdefault((ky, kx), []).append(len(clusters) - 1)
        else:
            clusters[home]["stops"].append(s)
    return clusters


# --------------------------------------------------------------------------- profile connection scan

class ProfileScan:
    """Backward scan: for every stop, earliest arrival at the destination as a
    function of when you are ready to leave that stop (unlimited transfers)."""

    def __init__(self, net, egress, foot, min_change_s, change_at=None):
        n = len(net.stop_name)
        self.net = net
        self.pdep = [[] for _ in range(n)]   # negated departure times, ascending
        self.parr = [[] for _ in range(n)]   # arrival times, strictly decreasing
        self.pcon = [[] for _ in range(n)]
        self.pleg = [[] for _ in range(n)]   # vehicles used from that departure to the destination
        change_at = change_at or {}
        conns = net.conns
        self.resp = [-1] * len(conns)
        self.alight = {}
        trip_best = [(INF, INF)] * len(net.trips)   # (arrival, vehicles): ties go to fewer vehicles
        trip_resp = [-1] * len(net.trips)
        buf = net.trip_buffer if len(net.trip_buffer) == len(net.trips) else [0] * len(net.trips)
        order = getattr(net, "scan_order", None)
        if order is None or len(order) != len(conns):   # every scan of this network shares one order
            order = sorted(range(len(conns)), key=lambda i: (conns[i][0] - buf[conns[i][4]], conns[i][1],
                                                            conns[i][7]), reverse=True)
            net.scan_order = order
        pdep, parr, pcon, pleg = self.pdep, self.parr, self.pcon, self.pleg
        bis = bisect.bisect_right
        for ci in order:
            dep, arr, s0, s1, ti, pk, dk, _k = conns[ci]
            best, info = (INF, INF), None
            if dk:
                e = egress.get(s1)
                if e is not None:
                    best, info = (arr + e, 1), ("walk", s1, e)
                mc = change_at.get(s1, min_change_s)
                nd = pdep[s1]
                if nd and mc >= 0:
                    j = bis(nd, -(arr + mc)) - 1
                    if j >= 0 and (parr[s1][j], pleg[s1][j] + 1) < best:
                        best, info = (parr[s1][j], pleg[s1][j] + 1), ("xfer", s1, pcon[s1][j], 0)
                for y, w in foot.get(s1, ()):
                    nd = pdep[y]
                    if nd:
                        j = bis(nd, -(arr + w)) - 1
                        if j >= 0 and (parr[y][j], pleg[y][j] + 1) < best:
                            best, info = (parr[y][j], pleg[y][j] + 1), ("xfer", y, pcon[y][j], w)
            if best < trip_best[ti]:
                trip_best[ti], trip_resp[ti] = best, ci
                self.alight[ci] = info
                (tau, legs), self.resp[ci] = best, ci
            else:
                (tau, legs), self.resp[ci] = trip_best[ti], trip_resp[ti]
            if tau < INF and pk:
                bdep = dep - buf[ti]   # be at the stop this early for coaches and trains
                nd = pdep[s0]
                if not nd or tau < parr[s0][-1]:
                    if nd and nd[-1] == -bdep:
                        parr[s0][-1], pcon[s0][-1], pleg[s0][-1] = tau, ci, legs
                    else:
                        nd.append(-bdep)
                        parr[s0].append(tau)
                        pcon[s0].append(ci)
                        pleg[s0].append(legs)
                elif nd[-1] == -bdep and tau == parr[s0][-1] and legs < pleg[s0][-1]:
                    pcon[s0][-1], pleg[s0][-1] = ci, legs

    def has_service(self, s):
        return bool(self.pdep[s])

    def evaluate(self, s, t):
        nd = self.pdep[s]
        if not nd:
            return INF, -1
        j = bisect.bisect_right(nd, -t) - 1
        if j < 0:
            return INF, -1
        return self.parr[s][j], self.pcon[s][j]

    def latest_departure(self, s, deadline):
        """Latest departure from stop s that still reaches the destination by `deadline`."""
        arr = self.parr[s]
        lo, hi = 0, len(arr)
        while lo < hi:
            mid = (lo + hi) // 2
            if arr[mid] <= deadline:
                hi = mid
            else:
                lo = mid + 1
        if lo == len(arr):
            return None
        return -self.pdep[s][lo], arr[lo]

    def journey(self, s, t):
        """Legs of the earliest journey from stop s when ready at time t."""
        net, conns = self.net, self.net.conns
        arrival, c = self.evaluate(s, t)
        legs, guard = [], 0
        while c >= 0 and guard < 20:
            guard += 1
            r = self.resp[c]
            dep, s0 = conns[c][0], conns[c][2]
            arr, s1, ti = conns[r][1], conns[r][3], conns[r][4]
            feed, agency, short, long_, rtype, head, tid, rid = net.trips[ti]
            head = net.stop_heads.get((ti, conns[c][7]), head)
            legs.append({"type": "ride", "feed": feed, "agency": agency, "route": short, "route_name": long_,
                         "route_id": rid, "mode": ROUTE_TYPES.get(rtype, "transit"), "headsign": head,
                         "trip_id": tid, "trip_key": ti,
                         "from": net.stop_name[s0], "from_idx": s0, "depart": dep,
                         "to": net.stop_name[s1], "to_idx": s1, "arrive": arr,
                         "stops": conns[r][7] - conns[c][7] + 1})
            buf = net.trip_buffer[ti] if ti < len(net.trip_buffer) else 0
            if buf:
                legs[-1]["be_there_by"] = dep - buf
            if "b" in net.request.get((ti, conns[c][7]), ""):
                legs[-1]["request_board"] = True
            if "a" in net.request.get((ti, conns[r][7]), ""):
                legs[-1]["request_alight"] = True
            info = self.alight.get(r)
            if info is None:
                break
            if info[0] == "walk":
                legs.append({"type": "walk", "to": "destination", "seconds": info[2]})
                break
            _x, y, nc, w = info
            if w:
                legs.append({"type": "walk", "to": net.stop_name[y], "seconds": w})
            c = nc
        return arrival, legs


def _hm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _rule_applies(rule, payment, gap_min, day, t, boundary, routes):
    """Whether one transfer rule from the region file covers a transfer made at t (seconds since midnight
    of `day`), gap_min minutes after the earlier fare started, at the stops named in `boundary`."""
    if payment not in rule.get("payment", ["card"]):
        return False
    if rule.get("window_min") is not None and gap_min > rule["window_min"]:
        return False
    if rule.get("days") or rule.get("hours"):
        when = day + dt.timedelta(days=int(t // 86400)) if day else None
        if when and rule.get("days") and when.weekday() not in rule["days"]:
            return False
        mins = (t % 86400) / 60.0
        if rule.get("hours") and not any(_hm(a) <= mins < _hm(b) for a, b in rule["hours"]):
            return False
    if rule.get("at_stops") and not any(k.lower() in boundary.lower() for k in rule["at_stops"]):
        return False
    if rule.get("routes") and not set(rule["routes"]) & routes:
        return False
    return True


def apply_transfers(groups, rules, payment, day=None):
    """Apply the region's transfer rules to fare groups in travel order. Each group is a dict with
    agency, feed, depart, first_stop, last_stop, routes and fare (full price or None); this sets
    charged (None when unknown) and note. Rules: same_agency {name: {window_min, payment}} and
    pairs [{from, to, window_min, payment, action 'free' | 'refund_from', days, hours, at_stops, routes,
    label}]. Windows run from the boarding of the fare that was paid: a leg that rides free keeps the
    window of the fare that covered it, so a free transfer never restarts the clock. Payment
    'contactless' (a bank card tap) gets the same transfers as 'card'. Paying cash, the rider holds the
    paper transfer of the agency they paid, so pair rules need a direct change and match that agency.
    rules['paper_only'] lists agencies with no card readers on board (Milton Transit): with a card the rider
    still pays them in cash, and a leg boarded on such a paper transfer is a paper leg too, so the next
    change from it follows the cash rules."""
    same = rules.get("same_agency") or {}
    pairs = rules.get("pairs") or []
    paper_only = set(rules.get("paper_only") or [])
    pay = "card" if payment == "contactless" else payment
    for g in groups:
        g["charged"], g["note"], g["window_from"] = g["fare"], None, g["depart"]
        g["issuer"], g["issuer_agency"] = {g["agency"], g["feed"]}, g["agency"]
        g["paper"] = pay == "cash" or bool({g["agency"], g["feed"]} & paper_only)
    for i, g in enumerate(groups):
        names = {g["agency"], g["feed"]}
        boundary = "%s | %s" % (groups[i - 1]["last_stop"], g["first_stop"]) if i else ""
        for p in groups[:i]:
            # what the rider shows: a paper transfer names the agency that issued it; a card tap names the leg
            ppay = "cash" if p["paper"] else pay
            pnames = p["issuer"] if p["paper"] else {p["agency"], p["feed"]}
            gap = (g["depart"] - p["window_from"]) / 60.0
            s = next((same[n] for n in names if n in same), None)
            if s and pnames & names and ppay in s.get("payment", ["card", "cash"]) and gap <= s["window_min"]:
                g["charged"], g["note"] = 0.0, "%s transfer within %d min" % (g["agency"], s["window_min"])
                g["window_from"], g["issuer"], g["issuer_agency"] = p["window_from"], p["issuer"], p["issuer_agency"]
                g["paper"] = g["paper"] or p["paper"]
                break
            direct = p is groups[i - 1]
            hit = None
            for r in pairs:
                if (r.get("at_stops") or ppay == "cash") and not direct:
                    continue   # stop rules and paper transfers cover a direct change only
                t = p["depart"] if r.get("action") == "refund_from" else g["depart"]   # the leg made free
                if (pnames & set(r["from"]) and names & set(r["to"])
                        and _rule_applies(r, ppay, gap, day, t, boundary, p["routes"] | g["routes"])):
                    hit = r
                    break
            if not hit:
                continue
            label = hit.get("label") or "transfer"
            if hit.get("action") == "refund_from":
                if p["charged"] or p["charged"] is None:
                    p["charged"], p["note"] = 0.0, "free (%s)" % label
            else:
                src = p["issuer_agency"] if p["paper"] else p["agency"]   # paper: whose transfer the rider holds
                g["charged"], g["note"] = 0.0, "free from %s (%s)" % (src, label)
                g["window_from"], g["issuer"], g["issuer_agency"] = p["window_from"], p["issuer"], p["issuer_agency"]
                g["paper"] = g["paper"] or p["paper"]
                break
    return groups


FARE_STATION_DROP = {"go", "up", "express", "station", "stn", "airport", "terminal", "1", "3"}


def fare_station(name):
    """'UP Express Pearson Airport', 'Bloor GO/UP', 'Mount Dennis' -> 'pearson', 'bloor', 'mount dennis'."""
    base = re.split(r"\s+-\s+", name or "")[0].lower().replace("&", " and ")
    return " ".join(w for w in re.findall(r"[a-z0-9']+", base) if w not in FARE_STATION_DROP)


def pair_fare(pairs, a, b):
    """The price entry for a ride between stops a and b in a {'Union|Weston': {...}} table, either direction."""
    want = {fare_station(a), fare_station(b)}
    for key, entry in (pairs or {}).items():
        x, _, y = key.partition("|")
        if {fare_station(x), fare_station(y)} == want and len(want) == 2:
            return entry
    return None


def journey_fare(net, legs, flat=None, payment="card", card_discount=None, rules=None, day=None):
    """Sum fares by feed group. The region's curated flat fare wins when present (charged once per
    consecutive group); otherwise the feed's GTFS fare table (one fare per group when the feed allows
    unlimited transfers), less the region's card discount for that feed when paying by card. With the
    region's transfer rules, free and refunded transfers are taken off (see apply_transfers).
    Returns (total or None, currency, parts, complete)."""
    groups = []
    same = (rules or {}).get("same_agency") or {}
    for lg in legs:
        if lg["type"] != "ride":
            continue
        # one fare covers a run of one feed only within that agency's transfer window: a bus caught long after
        # the first one is a new fare (apply_transfers then decides whether a transfer still covers it)
        win = next((same[n].get("window_min") for n in (lg["agency"], lg["feed"]) if n in same), None)
        if groups and groups[-1][0] == lg["feed"] and (win is None or lg["depart"] - groups[-1][1][0]["depart"]
                                                         <= win * 60):
            groups[-1][1].append(lg)
        else:
            groups.append((lg["feed"], [lg]))
    total, cur, parts, complete = 0.0, None, [], True
    for feed, glegs in groups:
        price, source = None, None
        by_route = [(flat or {}).get("%s %s" % (lg["agency"], lg["route"])) for lg in glegs]
        # a route-level price ("Niagara Region Transit 602") wins when every leg of the group has one
        f = by_route[0] if all(by_route) else ((flat or {}).get(feed) or (flat or {}).get(glegs[0]["agency"]))
        if f:
            # the payment's own price; a bank-card tap costs the card price unless listed; an agency that
            # does not take this payment (card: null) costs its fare or cash price
            tries = [payment] + (["card"] if payment == "contactless" else []) + ["fare", "cash", "card"]
            if f.get("pairs"):   # fares by station pair (UP Express), either direction; unlisted pair = unknown
                f = dict(pair_fare(f["pairs"], glegs[0]["from"], glegs[-1]["to"]) or {}, currency=f.get("currency", ""))
            amount = next((f[k] for k in tries if f.get(k) is not None), None)
            if amount is not None:
                price, source = (float(amount), f.get("currency", ""), ""), "region table, %s" % payment
        model = net.fares.get(feed)
        if price is None and model:
            unlimited = all(a[2] == "" for a in model["attrs"].values())
            if unlimited:
                price = fare_lookup(model, glegs[0]["route_id"], net.stop_zone[glegs[0]["from_idx"]],
                                    net.stop_zone[glegs[-1]["to_idx"]])
            else:
                acc = None
                for lg in glegs:
                    a = fare_lookup(model, lg["route_id"], net.stop_zone[lg["from_idx"]], net.stop_zone[lg["to_idx"]])
                    if a is None:
                        acc = None
                        break
                    acc = (acc[0] + a[0], a[1], a[2]) if acc else a
                price = acc
            if price is not None:
                source = "GTFS"
                disc = (card_discount or {}).get(feed)
                if payment in ("card", "contactless") and disc:
                    price = (price[0] * (1.0 - disc), price[1], price[2])
                    source = "GTFS less %.1f%% card discount" % (disc * 100)
        parts.append({"agency": glegs[0]["agency"], "feed": feed, "fare": None if price is None else price[0],
                      "currency": None if price is None else price[1], "source": source,
                      "depart": glegs[0]["depart"], "first_stop": glegs[0]["from"], "last_stop": glegs[-1]["to"],
                      "routes": {lg["route"] for lg in glegs}})
    if rules:
        apply_transfers(parts, rules, payment, day)
    for p in parts:
        full = p.pop("fare")
        charged = p.pop("charged", full)
        for k in ("depart", "first_stop", "last_stop", "routes", "window_from", "issuer", "issuer_agency", "paper"):
            p.pop(k, None)
        if charged is None:
            complete = False
            p.update(fare=None, fare_url=net.fare_urls.get((p["feed"], p["agency"])))
            p.pop("currency", None)
            p.pop("source", None)
            continue
        total += charged
        cur = cur or p.get("currency")
        p["fare"] = round(charged, 2)
        if full is None:
            p["source"] = "transfer rule"
            p.pop("currency", None)
        if full is not None and charged != full:
            p["full_fare"] = round(full, 2)
        if p.get("note") is None:
            p.pop("note", None)
    if not parts:
        return None, None, parts, True
    return (round(total, 2) if total or complete else None), cur, parts, complete


# --------------------------------------------------------------------------- ride model

def traffic_factor_for(when):
    """OSRM times are free-flow. Inflate them by time of day (weekday peaks worst)."""
    h = when.hour + when.minute / 60.0
    weekday = when.weekday() < 5
    if weekday and (7.0 <= h < 9.5 or 15.5 <= h < 18.5):
        return 1.45
    if h >= 20.0 or h < 6.0:
        return 1.0
    return 1.2


DRIVING_DIR = os.path.normpath(os.path.join(HERE, "..", "references", "driving"))
# used when references/driving/traffic.json is missing: weekday peak, shoulder, daytime, night (plan = the conservative planning allowance)
PLAN_ROWS_FALLBACK = [
    {"name": "weekday peak", "days": "weekday", "windows": [["07:00", "09:30"], ["15:30", "18:30"]], "plan": 2.1},
    {"name": "weekday shoulder", "days": "weekday",
     "windows": [["06:00", "07:00"], ["09:30", "10:30"], ["14:30", "15:30"], ["18:30", "19:30"]], "plan": 1.7},
    {"name": "daytime", "days": "any", "windows": [["06:00", "20:00"]], "plan": 1.45},
    {"name": "night", "days": "any", "windows": [["20:00", "06:00"]], "plan": 1.15}]


def _driving_json(name):
    try:
        with open(os.path.join(DRIVING_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def car_plan_defaults():
    """Car-mode defaults shared with drive_plan.py: the spare minutes planned before a deadline."""
    slack = (_driving_json("costs.json").get("arrival") or {}).get("slack_min")
    return {"slack_min": float(slack) if isinstance(slack, (int, float)) else 5.0}


def plan_traffic_factor(when, holidays=()):
    """Car mode: the planning-allowance multiplier on free-flow time for a drive at `when` (the congested-area
    rows of references/driving/traffic.json, as drive_plan.py plans its own drive). Returns (factor, row name)."""
    rows = _driving_json("traffic.json").get("congested") or PLAN_ROWS_FALLBACK
    hm = when.hour * 60 + when.minute
    weekday = when.weekday() < 5 and when.date().isoformat() not in set(holidays)
    for row in rows:
        if row.get("days") == "weekday" and not weekday:
            continue
        for a, b in row.get("windows") or []:
            lo, hi = _hm(a), _hm(b)
            if (lo <= hm < hi) if lo <= hi else (hm >= lo or hm < hi):
                return float(row["plan"]), row.get("name", "")
    return float(rows[-1]["plan"]), rows[-1].get("name", "")


def heuristic_ride(straight_m):
    road_km = straight_m / 1000.0 * 1.35
    speed = min(75.0, 24.0 + 3.0 * road_km)   # highway trips average about 75 km/h door to door
    return road_km, road_km / speed * 3600 + 120


def osrm_table(base, origin, points):
    out = []
    for start in range(0, len(points), OSRM_BATCH):
        if start // OSRM_BATCH >= OSRM_MAX_REQUESTS:
            out.extend([None] * (len(points) - start))
            break
        chunk = points[start:start + OSRM_BATCH]
        coords = ";".join("%.5f,%.5f" % (lon, lat) for lat, lon in [origin] + chunk)
        url = "%s/table/v1/driving/%s?sources=0&annotations=duration,distance" % (base.rstrip("/"), coords)
        data = json.loads(http_get(url, timeout=30, min_interval=1.0))  # demo servers: max 1 request/second
        if data.get("code") != "Ok":
            raise RuntimeError("OSRM error: %s" % (data.get("message") or data.get("code")))
        durs = data["durations"][0][1:]
        dists = (data.get("distances") or [[None] * (len(chunk) + 1)])[0][1:]
        for du, di in zip(durs, dists):
            out.append(None if du is None or di is None else (du, di))
    return out


def ride_cost(pricing, km, seconds, ends=None):
    """Rate card -> rider estimate: (base + km + min + booking fee + trip fee) x (1 + tax) x upfront uplift.
    `booking_fee_tiers` is a list of [max_km, fee] pairs (last one wins beyond the table). With `ends`
    ((lat, lon) pickup, drop-off) and service-area boxes (`service_areas`, or one `service_area`), a ride
    that starts inside them and ends outside adds `out_of_area_per_km` for the whole distance (Uber's
    out-of-region surcharge in Ontario)."""
    p = pricing
    fee = p.get("booking_fee", 0.0)
    for max_km, tier_fee in p.get("booking_fee_tiers") or []:
        fee = tier_fee
        if km < max_km:
            break
    c = max(p.get("min_fare", 0.0),
            p["base"] + p["per_km"] * km + p["per_min"] * seconds / 60.0 + fee + p.get("trip_fee", 0.0))
    areas = p.get("service_areas") or ([p["service_area"]] if p.get("service_area") else [])
    if ends and areas and p.get("out_of_area_per_km"):
        inside = [any(in_bbox(pt[0], pt[1], b) for b in areas) for pt in ends]
        if inside[0] and not inside[1]:
            c += p["out_of_area_per_km"] * km
    return c * (1.0 + p.get("tax", 0.0)) * p.get("uplift", 1.0)


def pricing_at(region, lat, lon, fallback, country=None):
    """The rate card for a ride picked up at (lat, lon): the first of the region's ride_pricing_areas whose
    boxes hold the point (its boxes become the service area for the out-of-area surcharge), else the
    region's card for places with no published rate (ride_pricing_outside), else `fallback`. A point known
    to be in another country than the region file's gets `fallback` (Windsor's rate box holds Detroit)."""
    if country and (region or {}).get("country") and country != region["country"]:
        return fallback
    for a in (region or {}).get("ride_pricing_areas") or []:
        if any(in_bbox(lat, lon, b) for b in a["boxes"]):
            p = dict(fallback)
            p.update(a["pricing"])
            p["service_areas"] = a["boxes"]
            p["label"] = a["pricing"].get("label") or a["name"]
            p["source"] = a["pricing"].get("source") or (region.get("ride_pricing_areas_source") or p.get("source", ""))
            return p
    out = (region or {}).get("ride_pricing_outside")
    if out and (region or {}).get("ride_pricing_areas"):
        p = dict(fallback)
        p.update(out)
        p.pop("service_areas", None)
        p.pop("service_area", None)
        return p
    return fallback


def taxi_pricing(region, area):
    """Rate card for a taxi where ride-hail does not operate: the area's own rate, else the region's."""
    rate = (area or {}).get("taxi_rate") or (region or {}).get("taxi_pricing_default")
    if not rate:
        return None
    p = {"label": "local taxi estimate", "base": 0.0, "per_km": 0.0, "per_min": 0.0, "tax": 0.0}
    p.update(rate)
    return p


# --------------------------------------------------------------------------- park and ride (--access car)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PUBLIC_ACCESS = {"", "yes", "public", "permissive"}
NOT_PARKING_RE = re.compile(r"drop.?off|kiss|pick.?up|passenger|taxi|staff|employee|permit", re.I)
# airport garages and valet lots (Pearson Terminal 1 garage, GTAA Value Park, Park'N Fly) are not park and ride
AIRPORT_LOT_RE = re.compile(r"\b(terminal|airport|a(?:e|\u00e9)roport|pearson|gtaa|value park)\b|park\W*n\W*fly", re.I)
UNKNOWN_PARKING_PENALTY = 1e6   # car-mode ranking: an unknown hub parking price or fare sorts after known ones
PARK_RIDE_M = 300          # an OSM park_ride lot this close to one of a hub's stops serves it
STATION_PARKING_M = 400    # public OSM parking this close to a rail station serves it
STATION_CLAIM_M = 600      # a lot this close to a rail station is that station's (big GO lots reach ~450 m)
PARKING_RAIL_TYPES = (1, 2, 12)   # subway, rail, monorail: streetcar stops are not park-and-ride stations
BASIS_RANK = {"agency": 0, "stop name": 1, "OSM park_ride": 2, "OSM parking near a station": 3}


def station_key(name):
    """'Union Station GO', 'Kipling Station - Eastbound Platform' -> 'union', 'kipling'."""
    base = re.split(r"\s+-\s+", name or "")[0].lower().replace("&", " and ")
    return " ".join(w for w in re.findall(r"[a-z0-9']+", base) if w not in ("go", "station", "stn"))


def overpass_parking(box, warnings, max_age_h=24.0):
    """OSM park-and-ride lots, and parking within ~550 m of OSM railway stations, inside `box`: one Overpass
    query, cached 24 h. On HTTP 429 (busy) or 504 (overloaded) waits 30 s and retries once. Returns raw
    elements, or None on failure."""
    s, w = math.floor(box["min_lat"] * 20) / 20.0, math.floor(box["min_lon"] * 20) / 20.0
    n, e = math.ceil(box["max_lat"] * 20) / 20.0, math.ceil(box["max_lon"] * 20) / 20.0
    q = ('[out:json][timeout:90][bbox:%.2f,%.2f,%.2f,%.2f];'
         'nwr["amenity"="parking"]["park_ride"]["park_ride"!="no"]->.pr;'
         'nwr["railway"~"^(station|halt)$"]->.st;'
         'nwr(around.st:%d)["amenity"="parking"]->.near;'
         '(.pr;.near;);out center tags;' % (s, w, n, e, STATION_PARKING_M + 150))
    path = os.path.join(cache_dir(), "overpass-%s.json" % hashlib.sha1(q.encode("utf-8")).hexdigest()[:16])
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh).get("elements", [])
        except (OSError, ValueError):
            pass
    url = OVERPASS_URL + "?" + urllib.parse.urlencode({"data": q})
    data, err = None, None
    for attempt in range(2):
        try:
            data = json.loads(http_get(url, timeout=120, min_interval=1.0))
            if "error" in (data.get("remark") or "").lower() or "timed out" in (data.get("remark") or ""):
                err, data = "Overpass: %s" % data["remark"][:120], None
            break
        except urllib.error.HTTPError as ex:
            err = ex
            if ex.code in (429, 504) and attempt == 0:
                log("Overpass is busy (HTTP %d): waiting 30 s, then one retry" % ex.code)
                time.sleep(30)
                continue
            break
        except Exception as ex:  # noqa: BLE001
            err = ex
            break
    if data is None:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    warnings.append("OpenStreetMap parking lookup failed (%s); used a cached copy older than %d h."
                                    % (err, max_age_h))
                    return json.load(fh).get("elements", [])
            except (OSError, ValueError):
                pass
        warnings.append("OpenStreetMap parking lookup failed (%s), so only the region's station parking rules "
                        "were used to pick hubs with parking." % err)
        return None
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    return data.get("elements", [])


def _minutes(text):
    """'2 hours', '90 min', '3h' -> minutes, else None."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minutes)?\s*$", text or "", re.I)
    if not m:
        return None
    unit = (m.group(2) or "h").lower()
    return float(m.group(1)) * (1 if unit.startswith("m") else 60)


def osm_lots(elements):
    """Overpass elements -> public parking lots {id, lat, lon, name, park_ride, fee, charge, maxstay_min, capacity}
    (drop-off, pick-up, staff and permit areas left out by name)."""
    out = []
    for el in elements or []:
        tags = el.get("tags") or {}
        if tags.get("amenity") != "parking" or tags.get("access", "").strip().lower() not in PUBLIC_ACCESS:
            continue
        if NOT_PARKING_RE.search(tags.get("name", "")):   # kiss-and-ride and pick-up areas are tagged as parking
            continue
        if AIRPORT_LOT_RE.search("%s %s" % (tags.get("name", ""), tags.get("operator", ""))):
            continue
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        pr = tags.get("park_ride", "").strip().lower()
        out.append({"id": "%s/%s" % (el.get("type", "node"), el.get("id")), "lat": float(lat), "lon": float(lon),
                    "name": tags.get("name") or tags.get("operator") or "", "park_ride": bool(pr) and pr != "no",
                    "fee": tags.get("fee", "").strip().lower(), "charge": tags.get("charge", ""),
                    "maxstay_min": _minutes(tags.get("maxstay")), "capacity": tags.get("capacity")})
    return out


def charge_cost(charge, stay_min):
    """Cost of a stay under an OSM charge tag ('$2/hr;$5 max', '1.75 CAD/30 minutes', '$10/day'), or None."""
    hourly, daily, cap = None, None, None
    for amt, per_n, unit in re.findall(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:CAD|\$)?\s*(?:/|per|an?\b)?\s*(\d+)?\s*"
                                       r"(minutes|minute|mins|min|hours|hour|hrs|hr|h|day|daily|max|maximum)\b",
                                       charge or "", re.I):
        amt, unit = float(amt), unit.lower()
        if unit.startswith("max"):
            cap = amt
        elif unit in ("day", "daily"):
            daily = amt
        else:
            mins = float(per_n or 1) * (1 if unit.startswith("m") else 60)
            hourly = amt * 60.0 / mins
    if hourly is not None:
        cost = hourly * math.ceil(stay_min / 60.0)
        for lim in (cap, daily):
            if lim is not None:
                cost = min(cost, lim * max(1, math.ceil(stay_min / 1440.0)))
        return round(cost, 2)
    if daily is not None:
        return round(daily * max(1, math.ceil(stay_min / 1440.0)), 2)
    return None


def agency_price(ent, day, holidays):
    """(cost, text) of an agency's station parking rule on `day`."""
    p = ent.get("price") or {}
    if p.get("free"):
        return 0.0, ent.get("text", "free parking")
    if p.get("weekend_holiday_free") and (day.weekday() >= 5 or day.isoformat() in holidays):
        return 0.0, ent.get("text_free_day") or ent.get("text", "free parking")
    if p.get("weekday_daily") is not None:
        return float(p["weekday_daily"]), ent.get("text", "")
    return None, ent.get("text", "price unknown, check signs")


def hub_parking(net, clusters, blocks, lots, stay_min, day, holidays):
    """--access car: keep only hubs with somewhere to park. A hub qualifies by (a) a rail station of an agency in
    a region's park_and_ride block (except its no_free_parking names; paid_only names are paid, price unknown),
    (a2) a stop whose name says park and ride or carpool, (b) an OSM park_ride lot within 300 m of its stops, or
    (c) public OSM parking within 400 m of a rail station among its stops. Each kept hub gets c["parking"] (the
    cheapest known choice). Returns (kept clusters, summary)."""
    rail = {}
    for (dep, arr, s0, s1, ti, *_r) in net.conns:
        tr = net.trips[ti]
        if tr[4] in PARKING_RAIL_TYPES:
            for st in (s0, s1):
                rail.setdefault(st, set()).add(((tr[1] or "").lower(), tr[4]))
    grid, cell = {}, float(STATION_CLAIM_M)   # near_lots reaches at least one cell in every direction
    lat0 = (sum(c["lat"] for c in clusters) / len(clusters)) if clusters else 45.0
    ky_m, kx_m = cell / 111320.0, cell / (111320.0 * max(0.2, math.cos(math.radians(lat0))))
    for lot in lots or []:
        grid.setdefault((int(lot["lat"] // ky_m), int(lot["lon"] // kx_m)), []).append(lot)

    def near_lots(lat, lon):
        ky, kx = int(lat // ky_m), int(lon // kx_m)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for lot in grid.get((ky + dy, kx + dx), ()):
                    yield lot

    entries = [(b, ent, {station_key(x) for x in ent.get("stations") or []},
                {station_key(x) for x in ent.get("no_free_parking") or []})
               for b in blocks for ent in b.get("agencies") or []]
    # stations an agency lists as having no free parking (or paid parking only): an OSM lot there is paid
    restricted = {fare_station(x) for b in blocks for ent in b.get("agencies") or []
                  for x in list(ent.get("no_free_parking") or []) + [p["name"] for p in ent.get("paid_only") or []]}
    # agencies whose block lists every station with parking ("stations"): at their stations an OSM lot neither makes
    # a hub off the list (a free lot near Old Mill is not a TTC commuter lot) nor beats the listed rule
    closed = [(ent["agency"].lower(), set(ent.get("route_types") or PARKING_RAIL_TYPES))
              for b in blocks for ent in b.get("agencies") or [] if ent.get("stations")]

    def closed_only(s):
        """A rail stop served only by agencies with a closed station list."""
        return bool(closed) and all(any(ag == c_ag and rt in c_rt for c_ag, c_rt in closed) for ag, rt in rail[s])
    # a lot beside a rail station belongs to that station: a bus stop near the same lot is not a separate hub
    # (park at the GO lot, then ride a local bus to that same GO station)
    claimed = set()
    for c in clusters:
        for s in c["stops"]:
            if s in rail:
                for lot in near_lots(net.stop_lat[s], net.stop_lon[s]):
                    if haversine_m(lot["lat"], lot["lon"], net.stop_lat[s], net.stop_lon[s]) <= STATION_CLAIM_M:
                        claimed.add(lot["id"])
    kept, by_basis, stay_h = [], {}, stay_min / 60.0
    for c in clusters:
        cands = []
        for s in c["stops"]:
            key = station_key(net.stop_name[s])
            for b, ent, only, no_free in entries:
                types = ent.get("route_types") or PARKING_RAIL_TYPES
                if not any(ag == ent["agency"].lower() and rt in types for ag, rt in rail.get(s, ())):
                    continue
                if (only and key not in only) or key in no_free:
                    continue
                paid = [p for p in ent.get("paid_only") or [] if station_key(p["name"]) == key]
                cost, text = (None, paid[0]["text"]) if paid else agency_price(ent, day, holidays)
                cands.append({"basis": ent.get("label") or "%s rule" % ent["agency"], "rank": BASIS_RANK["agency"],
                              "text": text, "cost": cost, "name": net.stop_name[s], "dist_m": 0,
                              "source": ent.get("source", "")})
            low = (net.stop_name[s] or "").lower()
            for b in blocks:
                if any(wd in low for wd in b.get("stop_name_words") or []):
                    cands.append({"basis": "stop name", "rank": BASIS_RANK["stop name"],
                                  "text": b.get("stop_name_text", "free park and ride lot"), "cost": 0.0,
                                  "name": net.stop_name[s], "dist_m": 0, "source": b.get("stop_name_source", "")})
        rail_stops = [s for s in c["stops"] if s in rail]
        paid_here = any(fare_station(net.stop_name[s]) in restricted for s in rail_stops)
        listed_only = [s for s in rail_stops if closed_only(s)]
        if listed_only:   # OSM lots count only through stations of agencies without a closed list
            rail_stops = [s for s in rail_stops if s not in listed_only]
        seen = set()
        for lot in near_lots(c["lat"], c["lon"]):
            if listed_only and not rail_stops:
                break
            if lot["id"] in seen or (lot["maxstay_min"] is not None and lot["maxstay_min"] < stay_min):
                continue
            if lot["id"] in claimed and not rail_stops:
                continue
            seen.add(lot["id"])
            d_any = min(haversine_m(lot["lat"], lot["lon"], net.stop_lat[s], net.stop_lon[s]) for s in c["stops"])
            d_rail = min([haversine_m(lot["lat"], lot["lon"], net.stop_lat[s], net.stop_lon[s]) for s in rail_stops]
                         or [INF])
            if lot["park_ride"] and d_any <= PARK_RIDE_M and not listed_only:
                basis, d = "OSM park_ride", d_any
            elif d_rail <= STATION_PARKING_M:
                basis, d = "OSM parking near a station", d_rail
            else:
                continue
            if lot["fee"] == "no" and not paid_here:
                cost, what = 0.0, "free"
            else:
                cost = charge_cost(lot["charge"], stay_min)
                if cost is not None:
                    what = "$%.2f for %g h (OSM charge: %s)" % (cost, stay_h, lot["charge"])
                elif paid_here:   # the agency lists no free parking here: an untagged or fee=no lot is not free
                    what = "paid parking, price unknown (no free parking at this station), check signs"
                else:
                    what = "price unknown, check signs"
            label = "OSM park and ride lot" if basis == "OSM park_ride" else "OSM parking"
            cands.append({"basis": basis, "rank": BASIS_RANK[basis], "cost": cost, "name": lot["name"],
                          "dist_m": int(round(d)), "source": "https://www.openstreetmap.org/%s" % lot["id"],
                          "text": "%s, %s%s, %d m from the stop" % (what, label, (" '%s'" % lot["name"]) if lot["name"]
                                                                   else "", int(round(d)))})
        if not cands:
            continue
        best = min(cands, key=lambda x: (x["cost"] is None, x["cost"] or 0.0, x["rank"], x["dist_m"]))
        c["parking"] = {"basis": best["basis"], "text": best["text"], "cost": best["cost"],
                        "free": best["cost"] == 0.0, "price_unknown": best["cost"] is None, "name": best["name"],
                        "dist_m": best["dist_m"], "source": best["source"],
                        "also": sorted({x["basis"] for x in cands} - {best["basis"]})}
        by_basis[best["basis"]] = by_basis.get(best["basis"], 0) + 1
        kept.append(c)
    return kept, {"hubs_in_range": len(clusters), "with_parking": len(kept), "excluded": len(clusters) - len(kept),
                  "by_basis": by_basis, "osm_lots": None if lots is None else len(lots)}


# --------------------------------------------------------------------------- second opinion (Transitous)

def transitous(o, d, when_local, arrive_by, tzname, region):
    """Ask the Transitous (MOTIS) router for transit trips from point o. Non-commercial, best-effort public
    service. Its experimental car drop-off mode is deliberately not used: it plans disguised ride-all-the-way
    trips; the hub search here does that job. Returns (itineraries with local datetimes, how the time zone
    was resolved)."""
    off, how = offset_at_local(when_local, tzname, region)
    q = {"fromPlace": "%.6f,%.6f" % o, "toPlace": "%.6f,%.6f" % d,
         "time": (when_local - off).strftime("%Y-%m-%dT%H:%M:%SZ"), "arriveBy": "true" if arrive_by else "false",
         "numItineraries": 5, "detailedLegs": "false"}   # no leg geometry or turn steps: about half the size
    data = json.loads(http_get(TRANSITOUS_URL + "?" + urllib.parse.urlencode(q), timeout=40, min_interval=1.0))

    def loc(ts):
        utc = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        return utc + offset_at_utc(utc, tzname, region)[0]

    out = []
    for it in data.get("itineraries", []):
        legs = it.get("legs", [])
        transit = [lg for lg in legs if lg.get("mode") not in ("WALK", "CAR", "CAR_DROPOFF", "BIKE")]
        if not transit:
            continue
        first = transit[0]
        out.append({
            "hub": first.get("from", {}).get("name"),
            "depart_dt": loc(legs[0]["startTime"]), "arrive_dt": loc(legs[-1]["endTime"]),
            "board_dt": loc(first["startTime"]),   # its first transit vehicle, after any walk
            "legs": ["%s %s %s->%s %s-%s" % (lg.get("agencyName") or "", lg.get("routeShortName") or lg.get("mode"),
                                             lg.get("from", {}).get("name"), lg.get("to", {}).get("name"),
                                             loc(lg["startTime"]).strftime("%H:%M"),
                                             loc(lg["endTime"]).strftime("%H:%M")) for lg in transit],
        })
    return out, how


# --------------------------------------------------------------------------- planner

def check_when_text(flag, text):
    """Stop with a format hint (not a traceback) when a --depart or --arrive-by value cannot be read."""
    if text in (None, "", "now"):
        return
    try:
        parse_when(text, None, None, [])
    except ValueError:
        raise SystemExit("%s %r: use 'YYYY-MM-DD HH:MM', 'HH:MM' or 'now' (local time at the trip)" % (flag, text))


SAME_PLACE_M = 100.0   # origin and destination closer than this: nothing to plan


def plan(args):
    warnings = []
    check_when_text("--arrive-by", args.arrive_by)
    if not args.arrive_by:
        check_when_text("--depart", args.depart)
    regions = load_regions()
    net_status = preflight(args, regions, warnings)
    strict = not args.allow_street_match
    o_lat, o_lon, o_label = resolve_place(args.origin, "origin", warnings, strict)
    d_lat, d_lon, d_label = resolve_place(args.dest, "destination", warnings, strict)
    if haversine_m(o_lat, o_lon, d_lat, d_lon) < SAME_PLACE_M:
        raise SystemExit("the origin and destination are the same place (%.0f m apart), so there is no trip to plan. "
                         "Check the two addresses." % haversine_m(o_lat, o_lon, d_lat, d_lon))
    infos = {}   # town-level reverse geocoding, once per point and run: country checks, discovery, border checks

    def place_info(lat, lon):
        k = ("%.3f" % lat, "%.3f" % lon)
        if k not in infos:
            infos[k] = reverse_place_info(lat, lon, warnings)   # zoom 10, 3 decimals: never the door
        return infos[k]

    # a region file never claims a point in another country (the Ontario boxes hold Detroit and Buffalo). Ask only
    # for ends inside a box of a region file that names its country, and not when preflight found Nominatim blocked:
    # then, or when the lookup fails, the boxes decide as before
    look_up = args.region == "auto" and net_status.get("nominatim.openstreetmap.org", "ok") == "ok"

    def country_of(lat, lon):
        if not look_up or not any(r.get("country") and r.get("bbox") and in_bbox(lat, lon, r["bbox"]) for r in regions):
            return None
        return place_info(lat, lon)["country"]

    o_cc, d_cc = country_of(o_lat, o_lon), country_of(d_lat, d_lon)
    home, extra = regions_for_trip(regions, args.region, (o_lat, o_lon), (d_lat, d_lon),
                                   haversine_m(o_lat, o_lon, d_lat, d_lon), countries=(o_cc, d_cc))
    region = merge_regions(home, extra, d_lat, d_lon,
                           pricing_regions(regions, o_lat, o_lon, o_cc) if args.region != "none" else ())
    tzname = (region or {}).get("timezone")
    rc = (region or {}).get("country")
    o_foreign = bool(rc and o_cc and o_cc != rc)   # an end in another country than the region file pricing the trip
    d_foreign = bool(rc and d_cc and d_cc != rc)
    cross_border = bool(o_cc and d_cc and o_cc != d_cc)
    if cross_border:
        warnings.append("this trip crosses the %s-%s border: riding all the way, and rides to hubs on the other side, "
                        "need a service that crosses it; prices and times leave out the border crossing."
                        % (o_cc, d_cc))

    od_m, radius_m, dest_zone_m, query_box, near = trip_geometry(o_lat, o_lon, d_lat, d_lon, args)
    final_m = final_ride_radius(od_m, args)
    if args.max_hours is None:   # long trips need a window that fits the whole journey
        hours = 4.0
        if od_m >= LONG_TRIP_M:
            hours = min(MAX_WINDOW_H, max(4.0, 1.5 + 2.5 * heuristic_ride(od_m)[1] / 3600.0))
        args = argparse.Namespace(**dict(vars(args), max_hours=round(hours, 1)))

    # ---- feeds (no time needed yet)
    feeds, used_ids, discovery, map_skipped = [], set(), None, []   # (id, label, urls, discovered, holidays)
    dest_feeds = 0   # selected feeds with stops near the destination itself
    if region:
        for f in region.get("feeds", []):
            if f.get("bbox") and not bbox_overlap(f["bbox"], query_box):
                continue
            if f.get("skip"):
                warnings.append("%s is skipped: %s" % (f.get("name", f["id"]), f.get("notes", "")))
                continue
            used_ids.add(f["id"])
            if f.get("coverage") and not coverage_near(f["coverage"], near):
                map_skipped.append(f.get("name", f["id"]))   # stop map: nothing near either end, no download
                continue
            if f.get("manual"):   # hand-entered timetable of an operator with no public GTFS
                try:
                    urls = [manual_feed_zip(f)]
                except (KeyError, ValueError, TypeError, OSError) as e:
                    warnings.append("hand-entered timetable %s is malformed and was skipped: %s" % (f["id"], e))
                    continue
            else:
                urls = f["urls"]
            if (coverage_near(f["coverage"], [(d_lat, d_lon, dest_zone_m)]) if f.get("coverage")
                    else bool(f.get("bbox")) and in_bbox(d_lat, d_lon, f["bbox"])):
                dest_feeds += 1
            feeds.append((f["id"], f.get("name", f["id"]), urls, False,
                          {"holidays": holiday_modes(region, f), "exclude_routes": set(f.get("exclude_routes") or []),
                           "board_buffer_s": int(float(f.get("board_buffer_min") or 0) * 60)}))
    if map_skipped:
        log("not downloaded (no stops near either end, per the region's stop map): %s" % ", ".join(map_skipped))
    for spec in args.feed or []:
        fid, src = parse_feed_spec(spec, used_ids)
        feeds.append((fid, fid, [src], False, {}))

    def unique_id(base):
        fid, n = base, 2
        while fid in used_ids:
            fid, n = "%s_%d" % (base, n), n + 1
        used_ids.add(fid)
        return fid

    def feed_label(h):
        label = h["provider"] or h["name"] or h["id"]
        if h["name"] and h["name"].lower() not in label.lower():
            label = "%s (%s)" % (label, h["name"])
        return label

    promo = []   # large official feeds, used only if the local feeds leave either end without stops
    if not region and not args.no_discover:
        log("no region file covers this trip: discovering feeds in the MobilityDatabase catalog")
        o_info, d_info = place_info(o_lat, o_lon), place_info(d_lat, d_lon)
        places = o_info["names"] | d_info["names"]
        found, too_big, far, dups = discover_feeds(query_box, places=places)
        countries = {c for c in (o_info["country"], d_info["country"]) if c}
        states = {s for s in (o_info["state"], d_info["state"]) if s}

        def relevance(h):   # official, same country, same state/province, same town, near, small
            town = (h.get("municipality") or "").lower()
            return (not h["official"], h.get("country") not in countries, h.get("subdivision") not in states,
                    not (town and any(p in town for p in places)), h.get("center_km", 0), h["area"])

        too_big.sort(key=relevance)
        promo = [h for h in too_big if h["official"] and (not countries or h.get("country") in countries)
                 and (in_bbox(o_lat, o_lon, h["bbox"]) or in_bbox(d_lat, d_lon, h["bbox"]))][:3]
        # on a long trip the limit must not be spent on one end: nearest to either end first
        found.sort(key=lambda h: min(bbox_dist_m(o_lat, o_lon, h.get("bbox")), bbox_dist_m(d_lat, d_lon, h.get("bbox"))))
        discovery = {"found": found, "too_big": too_big, "far": far, "duplicates": dups, "promoted": []}
        known = {u for f in feeds for u in f[2]}
        for h in found:
            if not any(u in known for u in h["urls"]):
                feeds.append((unique_id(h["id"]), feed_label(h), h["urls"], True, {}))
    elif region and not args.no_discover:
        # ends the region files do not serve: no transit near the destination, or an end in another country
        # than the region files' (Toronto to Buffalo): find feeds there
        ends = []
        if d_foreign or (od_m >= DEST_DISCOVER_MIN_M and not dest_feeds):
            ends.append(("destination", d_lat, d_lon, max(dest_zone_m, final_m), d_cc if d_foreign else None))
        if o_foreign:
            ends.append(("origin", o_lat, o_lon, radius_m, o_cc))
        for end, lat, lon, reach, foreign in ends:
            log("no region file %s the %s: discovering feeds within %.0f km of it"
                % ("of its country covers" if foreign else "has transit near", end, reach / 1000))
            info = place_info(lat, lon)
            try:
                found = discover_feeds(box_around(lat, lon, reach), places=info["names"])[0]
            except (Exception, SystemExit) as e:  # noqa: BLE001  (the catalog loader exits when offline)
                found = []
                warnings.append("could not search the feed catalog near the %s: %s" % (end, e))
            found.sort(key=lambda h: bbox_dist_m(lat, lon, h.get("bbox")))
            known = {u for f in feeds for u in f[2]} | {u for f in region.get("feeds", []) for u in f.get("urls", [])}
            added = []
            for h in found[:DISCOVER_DEST_MAX]:
                if not any(u in known for u in h["urls"]):
                    feeds.append((unique_id(h["id"]), feed_label(h), h["urls"], False, {}))
                    added.append(feed_label(h))
            warnings.append(("the %s is in %s, outside the region files' country (%s); " % (end, foreign, rc)
                             if foreign else "no region file has transit near the destination; ")
                            + "feeds found there: %s. Check that they run on the date (holidays are often missing "
                            "from these files)." % (", ".join(added) or "none"))
    if not feeds and not promo:
        msg = "no GTFS feeds for this trip."
        if discovery and discovery["too_big"]:
            msg += " Large feeds that cover the area (pass with --feed id=URL): " + "; ".join(
                "%s=%s (%s)" % (h["id"], h["urls"][0], h["provider"]) for h in discovery["too_big"][:8])
        else:
            msg += " Run --discover, then pass feeds with --feed id=URL."
        raise SystemExit(msg)

    # ---- time zone: region file first, else the first feed's agency_timezone
    bad_urls = {}   # url -> error, so a failing download is not repeated in this run
    if not tzname:
        for fid, url in [(f[0], f[2][0]) for f in feeds] + [(h["id"], h["urls"][0]) for h in promo]:
            try:
                tzname = feed_timezone(fetch_url(fid, url, args.max_age_hours, args.refresh))
            except Exception as e:  # noqa: BLE001
                bad_urls[url] = e
                tzname = None
            if tzname:
                break
        if not tzname and not region:
            warnings.append("could not read the trip's time zone from any feed; times use this computer's clock.")

    arrive_by = bool(args.arrive_by)
    if arrive_by:
        deadline = parse_when(args.arrive_by, tzname, region, warnings)
        depart = deadline - dt.timedelta(hours=args.max_hours)
    else:
        deadline = None
        depart = parse_when(args.depart, tzname, region, warnings)
    day = depart.date()
    t0 = depart.hour * 3600 + depart.minute * 60
    t_end = t0 + int(args.max_hours * 3600)
    now_local, _ = local_now(tzname, region)
    if (deadline or depart) < now_local - dt.timedelta(minutes=5):
        warnings.append("the requested time %s is in the past (local time now %s)."
                        % ((deadline or depart).strftime("%a %Y-%m-%d %H:%M"), now_local.strftime("%H:%M")))
    # options must still be bookable when the trip window includes the present moment
    now_s = int((now_local - dt.datetime.combine(day, dt.time(0))).total_seconds())
    if not (t0 - 3600 <= now_s <= t_end):
        now_s = -INF
    elif not arrive_by and now_s > t0:   # a start time a few minutes ago means "now"
        t0 = int(math.ceil(now_s / 60.0)) * 60
        depart = dt.datetime.combine(day, dt.time(0)) + dt.timedelta(seconds=t0)
        warnings.append("the start time had passed, so the plan starts now (%s)." % fmt_t(t0))
    day0 = dt.datetime.combine(day, dt.time(0))
    off_a, how_a = offset_at_local(day0 + dt.timedelta(seconds=t0), tzname, region)
    off_b, how_b = offset_at_local(day0 + dt.timedelta(seconds=t_end), tzname, region)
    if off_a != off_b and "machine clock" not in (how_a, how_b):
        warnings.append("the clocks change between %s and %s (UTC%+g to UTC%+g): timetable times near the change "
                        "can be an hour off, so confirm them with the operator."
                        % (fmt_t(t0), fmt_t(t_end), off_a.total_seconds() / 3600.0, off_b.total_seconds() / 3600.0))

    # ---- load
    net = Network()
    net.warnings = warnings
    loaded, skipped_far, capped, failed = [], [], [], []

    def load_one(fid, label, urls, opts=None):
        opts = opts or {}
        try:
            path = choose_feed_file(fid, label, urls, day, near, args, warnings, bad_urls)
            if path is None:
                skipped_far.append(label)
                log("skipping %s: no stops near the origin or destination" % label)
                return False
            t = time.time()
            load_feed(net, fid, label, path, day, t0, t_end, opts.get("holidays"), tzname, region,
                      opts.get("exclude_routes"), opts.get("board_buffer_s", 0))
            loaded.append(label)
            log("loaded %s in %.1fs (%d connections so far)" % (label, time.time() - t, len(net.conns)))
            return True
        except Exception as e:  # noqa: BLE001
            failed.append(label)
            warnings.append("feed %s failed to load, so its service is missing from this answer: %s" % (label, e))
            log("warning: feed %s failed: %s" % (label, e))
            return False

    n_discovered = 0
    for fid, label, urls, discovered, hol in feeds:
        if discovered and n_discovered >= DISCOVER_MAX_FEEDS:
            capped.append("%s=%s (%s)" % (fid, urls[0], label))
            continue
        if load_one(fid, label, urls, hol):
            n_discovered += bool(discovered)

    if promo:
        def covered(lat, lon, meters):
            return any(haversine_m(lat, lon, net.stop_lat[i], net.stop_lon[i]) <= meters
                       for i in range(len(net.stop_name)))
        need = []
        if not covered(o_lat, o_lon, max(2000.0, 2.0 * args.origin_walk_m)):
            need.append(("origin", o_lat, o_lon))
        if not covered(d_lat, d_lon, max(2000.0, float(args.dest_walk_m))):
            need.append(("destination", d_lat, d_lon))
        for h in promo:
            if len(discovery["promoted"]) >= 2 or not need:
                break
            if any(in_bbox(a, b, h["bbox"]) for _n, a, b in need):
                if load_one(unique_id(h["id"]), feed_label(h), h["urls"]):
                    discovery["promoted"].append(h)
        if discovery["promoted"]:
            warnings.append("local feeds had no stops near the %s, so large feeds were loaded: %s."
                            % (" or ".join(n for n, _a, _b in need),
                               ", ".join("%s (%s)" % (h["provider"], h["id"]) for h in discovery["promoted"])))
    if discovery is not None:
        warnings.append("no region file covers this trip, so feeds were discovered automatically. Loaded: %s."
                        % (", ".join(loaded) or "none"))
        if skipped_far:
            warnings.append("discovered but skipped (no stops near the origin or destination): %s"
                            % ", ".join(skipped_far))
        no_bbox = [h["provider"] for h in discovery["found"] if h.get("no_bbox")]
        if no_bbox:
            warnings.append("matched by town name (no location data in the catalog): %s" % ", ".join(no_bbox))
        if capped:
            warnings.append("over the %d-feed limit, not loaded: %s. Add any that matter with --feed id=URL "
                            "(it adds to the discovered feeds)." % (DISCOVER_MAX_FEEDS, "; ".join(capped)))
        rest = [h for h in discovery["too_big"] if h not in discovery["promoted"]]
        if rest:
            warnings.append("large (national/intercity) feeds not loaded, most relevant first: %s" % "; ".join(
                "%s=%s (%s)" % (h["id"], h["urls"][0], h["provider"]) for h in rest[:8]))
    tzs = sorted(net.timezones)
    if any(not same_clock(tzs[0], z) for z in tzs[1:]):
        warnings.append("feeds use different time zones %s; times assume one local clock" % tzs)
    if not net.conns:
        raise SystemExit("no transit departures found in the time window. Warnings: %s" % warnings)

    used = sorted({c[2] for c in net.conns} | {c[3] for c in net.conns})
    egress, dest_m = {}, args.dest_walk_m
    for attempt in range(2):
        for s in used:
            d = haversine_m(d_lat, d_lon, net.stop_lat[s], net.stop_lon[s])
            if d <= dest_m:
                egress[s] = walk_s(d)
        if egress or attempt:
            break
        warnings.append("no stop within %d m of the destination; widened to %d m" % (dest_m, dest_m * 2))
        dest_m *= 2
    if not egress:
        if final_m <= 0 and getattr(args, "final_ride", "auto") != "off":
            final_m = final_ride_radius(od_m, args, force=True)   # a short trip to a place with no stop
        if final_m <= 0:
            raise SystemExit("no transit stop near the destination in the loaded feeds")
        warnings.append("no transit stop within %d m of the destination, so every transit plan ends with a ride "
                        "(see FINISH WITH A RIDE)." % dest_m)

    t = time.time()
    min_change_s = int(args.min_change_min * 60)
    foot = build_footpaths(net, used, args.footpath_m, min_change_s)
    change_at = same_stop_change(net, min_change_s)
    scan = ProfileScan(net, egress, foot, min_change_s, change_at)
    log("scanned %d connections over %d stops in %.1fs" % (len(net.conns), len(used), time.time() - t))

    pricing = dict(DEFAULT_PRICING)
    if (region or {}).get("ride_pricing"):
        pricing.update(region["ride_pricing"])
        if "currency" not in region["ride_pricing"]:   # a rate card without one is in local money
            pricing["currency"] = local_currency(tzname) or pricing["currency"]
    else:
        cur = local_currency(tzname)
        if cur:
            pricing["currency"] = cur
            pricing["source"] = "generic North American-style rate card in local currency, not a local quote"
    if o_foreign:   # no rate card of the origin's country: the generic card, in the trip's currency
        pricing = dict(DEFAULT_PRICING, currency=pricing.get("currency"),
                       source="generic North American-style rate card, not a local quote: the origin is outside the "
                              "region files' country")
    else:
        pricing = pricing_at(region, o_lat, o_lon, pricing)   # the first ride is priced where it starts
    wait_s = int((args.pickup_wait_min if args.pickup_wait_min is not None
                  else (region or {}).get("pickup_wait_min", 6)) * 60)
    # --access car: drive your own car to the hub (no pickup wait; park and walk to the platform instead)
    car = getattr(args, "access", "ride") == "car"
    hail_pricing = pricing   # the ride-hail card, for final rides when the region file names none
    # the first ride starts at the origin: where the region file says there is no ride-hail it is a local taxi
    # (30 min to arrive unless the area says otherwise, no Uber link); where ride-hail is sparse, book it ahead
    o_area = None if car or o_foreign else ride_area(region, o_lat, o_lon)
    no_ride_hail_origin = (o_area or {}).get("uber") == "no"
    first_ride_wait_s = wait_s
    if no_ride_hail_origin:
        tp = taxi_pricing(region, o_area)
        if tp:
            tp.setdefault("currency", pricing.get("currency"))
            tp.setdefault("source", "the region file's taxi rate for %s" % o_area.get("name", "the origin"))
            pricing = tp   # the first ride, riding all the way and the short ride to the first stop
        first_ride_wait_s = max(wait_s, int((o_area.get("taxi_wait_min") or 30) * 60))
        taxis = taxis_text(o_area)
        warnings.append("there is no ride-hail near %s (the origin)%s. %s The plan prices the first ride %s and "
                        "allows %d min for it to arrive; book it ahead and confirm one will come."
                        % (o_area.get("name", "the origin"),
                           (" (area note: %s)" % o_area["note"].rstrip(".")) if o_area.get("note") else "",
                           ("Taxis listed: %s." % taxis) if taxis else "No taxi is listed for it.",
                           "as a local taxi" if tp else "at the ride-hail card (the region file has no taxi rate)",
                           first_ride_wait_s // 60))
    elif (o_area or {}).get("uber") == "sparse":
        if o_area.get("taxi_wait_min"):
            first_ride_wait_s = max(wait_s, int(o_area["taxi_wait_min"] * 60))
        taxis = taxis_text(o_area)
        warnings.append("ride-hail near %s (the origin) is sparse: book the first ride ahead (Uber Reserve) or call a "
                        "local taxi%s." % (o_area.get("name", "the origin"),
                                           (" (taxis listed: %s)" % taxis) if taxis else ""))
    safety_min = args.safety_min
    if safety_min is None:   # car mode plans the same spare as drive_plan.py (costs.json arrival slack)
        safety_min = car_plan_defaults()["slack_min"] if car else 3.0
    safety_s = int(max(0.0, safety_min) * 60)
    # the reference factor (shown in the car cost model); each drive below is priced at its own start time
    ride_time = deadline - dt.timedelta(minutes=60) if arrive_by else depart
    tf_basis = None
    hol = {h["date"] for r in regions for h in r.get("holidays") or [] if isinstance(h, dict) and h.get("stat", True)}
    if args.traffic_factor:
        tf = args.traffic_factor
    elif car:   # leave-by on the planning allowance, like drive_plan.py, not the typical time
        tf, tf_basis = plan_traffic_factor(ride_time, hol)
    else:
        tf = traffic_factor_for(ride_time)
    cpk = float(getattr(args, "car_cost_per_km", 0.17) or 0.0)
    stay_min = float(getattr(args, "stay_hours", 4.0) or 0.0) * 60.0
    first_wait_s, board_s = ((0, int(float(getattr(args, "park_min", 4.0)) * 60)) if car
                             else (first_ride_wait_s, PLATFORM_S))
    target = t_end - safety_s  # arrive-by mode: arrive with a few minutes spare

    def factor_at(sec):
        """Traffic factor for a drive that starts `sec` seconds after midnight of the travel day."""
        if args.traffic_factor:
            return tf
        when = day0 + dt.timedelta(seconds=int(sec))
        return plan_traffic_factor(when, hol)[0] if car else traffic_factor_for(when)

    def settle(free_s, start_of):
        """The factor for a drive whose start depends on its own length (arrive-by: start_of(ride seconds) gives
        the start). A few rounds until it stops changing; at a peak boundary that flips, the slower one."""
        f, tried = tf, []
        while len(tried) < 4:
            tried.append(f)
            f2 = factor_at(start_of(free_s * f))
            if f2 == f or (f2 in tried and max(f, f2) == f):
                break
            f = max(f, f2) if f2 in tried else f2
        return f

    def osrm_label(f):
        return "osrm x%.2f" % f

    def first_cost(c, km, sec, ends):
        """The first leg's cost: a ride-hail fare, or in car mode km x cost per km plus the hub's parking."""
        if not car:
            return ride_cost(pricing, km, sec, ends)
        return km * cpk + (((c or {}).get("parking") or {}).get("cost") or 0.0)

    # ---- drop points for finishing with a ride: transit to a stop short of the destination, then a
    # ride for the rest. One scan per drop point keeps it cost-aware: a single scan would always pick
    # the earliest arrival, which is usually "leave the bus early and ride far".
    pick, fpricing, area = [], None, None
    if final_m > 0:
        fpricing = dict(DEFAULT_PRICING, currency=pricing.get("currency", DEFAULT_PRICING["currency"]))
        if d_foreign:   # the region files' cards and ride notes are for their own country
            fpricing["source"] = ("generic North American-style rate card, not a local quote: the destination is "
                                  "outside the region files' country")
        else:
            fpricing.update((region or {}).get("final_ride_pricing") or hail_pricing)
        fpricing.setdefault("currency", pricing.get("currency"))
        area = None if d_foreign else ride_area(region, d_lat, d_lon)
        pts = []
        for s in used:
            dd = haversine_m(d_lat, d_lon, net.stop_lat[s], net.stop_lon[s])
            if args.dest_walk_m < dd <= final_m:   # nearer stops are walked to
                pts.append((dd, s))
        pts.sort()
        in_reach = {st for _dd, st in pts}
        arrivals, long_haul = {}, {}   # stop -> vehicles arriving; trip -> stops it reaches by a long hop
        for (dep, arr, s0, s1, ti, pk, dk, _k) in net.conns:
            if not dk or s1 not in in_reach:
                continue
            arrivals[s1] = arrivals.get(s1, 0) + 1
            if net.trips[ti][4] in (0, 1, 2, 12) or haversine_m(net.stop_lat[s0], net.stop_lon[s0], net.stop_lat[s1],
                                                                  net.stop_lon[s1]) > LONG_HOP_M:
                long_haul.setdefault(ti, set()).add(s1)   # trains, and coaches covering 10+ km between stops
        drops = [e for e in cluster_stops(net, pts) if any(st in arrivals for st in e["stops"])]
        home_of = {st: e for e in drops for st in e["stops"]}
        # each coach or train line's stop nearest the destination (where its riders would get off), most
        # served first; then the nearest local stops, for a short ride at the end
        last_stop = {}
        for ti, stops_ in long_haul.items():
            e = min((home_of[st] for st in stops_ if st in home_of), key=lambda x: x["straight_m"], default=None)
            if e is not None:
                last_stop[id(e)] = (e, last_stop.get(id(e), (e, 0))[1] + 1)
        lines = [e for e, _n in sorted(last_stop.values(), key=lambda x: (-x[1], x[0]["straight_m"]))]
        for e in lines[:FINAL_RIDE_DROP_POINTS - 2] + drops[:2] + lines + drops:
            if e not in pick:
                pick.append(e)
            if len(pick) >= FINAL_RIDE_DROP_POINTS:
                break
        if cross_border and pick:   # a final ride from across the border (VIA Windsor for Detroit) is not planned
            same = [e for e in pick if place_info(e["lat"], e["lon"])["country"] in (None, d_cc)]
            if len(same) < len(pick):
                warnings.append("%d drop points across the border from the destination were left out: a final ride "
                                "from there would cross the %s-%s border, which this plan cannot price or time."
                                % (len(pick) - len(same), o_cc, d_cc))
            pick = same
        for e in pick:
            km, sec = heuristic_ride(e["straight_m"])
            e.update(ride_km=km, ride_free_s=sec, osrm=False)
        if pick and args.routing in ("auto", "osrm"):
            for base in [args.osrm_url, OSRM_FALLBACK]:
                try:
                    res = osrm_table(base, (d_lat, d_lon), [(e["lat"], e["lon"]) for e in pick])
                except Exception:  # noqa: BLE001
                    continue
                for e, r in zip(pick, res):
                    if r:
                        e.update(ride_free_s=r[0], ride_km=r[1] / 1000.0, osrm=True)
                break
        for e in pick:   # arrive-by: the final ride ends at the deadline, so it starts about one ride before it
            f = settle(e["ride_free_s"], lambda rs: target - rs) if arrive_by else tf
            e.update(ride_tf=f, ride_s=e["ride_free_s"] * f, ride_source=osrm_label(f) if e["osrm"] else "estimate")
        taxi_wait_s = int(((area or {}).get("taxi_wait_min") or (30 if (area or {}).get("uber") == "no" else 0)) * 60)
        egrid = Grid(net.stop_lat, net.stop_lon, used, FINAL_RIDE_WALK_M, d_lat)
        for e in pick:
            lead_e = PLATFORM_S + max(wait_s, taxi_wait_s) + int(e["ride_s"])
            eg = {}
            for st in egrid.near(e["lat"], e["lon"]):   # walk off the vehicle to the pickup point
                w = haversine_m(e["lat"], e["lon"], net.stop_lat[st], net.stop_lon[st])
                if w <= FINAL_RIDE_WALK_M:
                    eg[st] = walk_s(w) + lead_e
            e["scan"] = ProfileScan(net, eg, foot, min_change_s, change_at)
            ep = pricing_at(region, e["lat"], e["lon"], fpricing, country=d_cc)
            if (area or {}).get("uber") == "no" and taxi_pricing(region, area):
                ep = taxi_pricing(region, area)   # no ride-hail at the destination: a local taxi
            e["pricing_label"], e["pricing"] = ep.get("label", ""), ep
            e["cost"] = ride_cost(ep, e["ride_km"], e["ride_s"], ((e["lat"], e["lon"]), (d_lat, d_lon)))
        if pick:
            log("finish with a ride: %d drop points within %.0f km of the destination"
                % (len(pick), final_m / 1000.0))

    # ---- candidate hubs: every served stop within ride radius, clustered
    cand = []
    stations = {st for (dep, arr, s0, s1, ti, *_r) in net.conns if net.trips[ti][4] in (0, 1, 2, 12)
                for st in (s0, s1)}
    for s in used:
        if scan.has_service(s) or any(e["scan"].has_service(s) for e in pick):
            d = haversine_m(o_lat, o_lon, net.stop_lat[s], net.stop_lon[s])
            # a rail station just past the radius beats a local stop beside it
            if d <= radius_m or (s in stations and d <= STATION_RADIUS_FACTOR * radius_m):
                cand.append((d, s))
    cand.sort()
    clusters = cluster_stops(net, cand)
    parking_summary = None
    if car:
        blocks = [r["park_and_ride"] for r in ([home] + list(extra) if home else []) if r.get("park_and_ride")]
        lots = None
        if clusters:
            box = box_around(o_lat, o_lon, STATION_RADIUS_FACTOR * radius_m + STATION_PARKING_M + 200)
            elements = overpass_parking(box, warnings)
            lots = None if elements is None else osm_lots(elements)
        holidays = {h["date"] for r in regions for h in r.get("holidays") or []
                    if isinstance(h, dict) and h.get("stat", True)}
        clusters, parking_summary = hub_parking(net, clusters, blocks, lots, stay_min, day, holidays)
        parking_summary["region_rules"] = bool(blocks)
        log("park and ride: %d of %d hubs have parking" % (parking_summary["with_parking"],
                                                            parking_summary["hubs_in_range"]))
        if not blocks:
            warnings.append("no region file lists station parking rules here, so hubs with parking come from "
                            "OpenStreetMap only.")
        if not clusters:
            warnings.append("no hub within %.1f km has known parking; try a larger --radius-km or --access ride."
                            % (radius_m / 1000.0))

    def best_on(c, sc):
        """depart mode: (arrival, stop, ready, slack: t0 + slack is the latest booking). arrive-by mode: (latest
        booking, stop, board, arrival). Ties go to the stop that leaves more slack."""
        best = None
        for s in c["stops"]:
            extra = walk_s(haversine_m(c["lat"], c["lon"], net.stop_lat[s], net.stop_lon[s]))
            lead = first_wait_s + int(c["ride_s"]) + board_s + extra + safety_s
            if arrive_by:
                r = sc.latest_departure(s, target)
                if r and r[0] - lead >= t0 and (best is None or r[0] - lead > best[0]):
                    best = (r[0] - lead, s, r[0], r[1])
            else:
                arr, c0 = sc.evaluate(s, t0 + lead)
                if arr >= INF:
                    continue
                slack = net.conns[c0][0] - net.trip_buffer[net.conns[c0][4]] - (t0 + lead)
                if best is None or (arr, -slack) < (best[0], -best[3]):
                    best = (arr, s, t0 + lead, slack)
        return best

    def evaluate_cluster(c):
        c["result"] = best_on(c, scan)
        c["fresults"] = [best_on(c, e["scan"]) for e in pick]

    def set_ride(c, free_s, osrm):
        """The ride to hub c in the traffic of its own start, then its best connections. Depart mode: timed as if
        booked now (picked up after the wait); make_option retimes each later book-by (rebook). Arrive-by: the
        start (the hub's book-by) moves with the ride's length, so settle it."""
        f, tried = (tf if arrive_by else factor_at(t0 + first_wait_s)), []
        c["ride_free_s"] = free_s
        while True:
            c["ride_s"] = free_s * f
            evaluate_cluster(c)
            tried.append(f)
            books = [r[0] for r in [c["result"]] + c["fresults"] if r]
            if not arrive_by or args.traffic_factor or not books or len(tried) > 3:
                break
            f2 = factor_at((c["result"][0] if c["result"] else max(books)) + first_wait_s)
            if f2 == f or (f2 in tried and max(f, f2) == f):
                break
            f = max(f, f2) if f2 in tried else f2
        c["ride_tf"], c["ride_source"] = f, osrm_label(f) if osrm else "estimate"

    for c in clusters:
        km, sec = heuristic_ride(c["straight_m"])
        c["ride_km"] = km
        set_ride(c, sec, False)
    km, sec = heuristic_ride(od_m)
    f_all = tf if arrive_by else factor_at(t0 + first_wait_s)   # depart mode: picked up after the wait
    ride_all = {"ride_km": km, "ride_s": sec * f_all, "source": "estimate", "free_s": sec}

    osrm_ok, routed = False, None   # routed: (hubs with OSRM driving times, hubs left on estimates) under the cap
    if args.routing in ("auto", "osrm"):
        live = [c for c in clusters if c["result"] or any(c["fresults"])]

        def score(c):
            return min((-r[0] if arrive_by else r[0]) for r in [c["result"]] + c["fresults"] if r)

        # refine the estimated cost/time front first (cheap hubs), then the fastest hubs
        front, best_score = [], INF
        for c in sorted(live, key=lambda c: (c["ride_s"], score(c))):
            if score(c) < best_score:
                front.append(c)
                best_score = score(c)
        seen = {id(c) for c in front}
        # then stations served by rail, metro or tram, then everything else by promise
        rail = {s for (dep, arr, s, s1, ti, *_r) in net.conns if net.trips[ti][4] in (0, 1, 2, 12)}
        refine = front + [c for c in sorted(live, key=lambda c: (not any(s in rail for s in c["stops"]),
                                                                  score(c), c["straight_m"]))
                          if id(c) not in seen]
        # hubs the rough estimate made miss every departure can still work with real driving times
        # (a 07:50 coach at the airport looks missed at 60 km/h, not at highway speed)
        refine += sorted([c for c in clusters if not (c["result"] or any(c["fresults"]))],
                         key=lambda c: (not any(s in rail for s in c["stops"]), c["straight_m"]))
        refine = refine[:OSRM_BATCH * OSRM_MAX_REQUESTS - 1]
        pts = [(d_lat, d_lon)] + [(c["lat"], c["lon"]) for c in refine]
        res, err = None, None
        for base in [args.osrm_url, OSRM_FALLBACK]:
            try:
                res = osrm_table(base, (o_lat, o_lon), pts)
                break
            except Exception as e:  # noqa: BLE001
                err = e
        if res is None:
            msg = "driving times are estimates (OSRM unavailable: %s)" % err
            if args.routing == "osrm":
                raise SystemExit(msg)
            warnings.append(msg)
        else:
            osrm_ok = True
            if res[0]:
                ride_all.update(ride_s=res[0][0] * f_all, ride_km=res[0][1] / 1000.0, source=osrm_label(f_all),
                                free_s=res[0][0])
            for c, r in zip(refine, res[1:]):
                if r:
                    c["ride_km"] = r[1] / 1000.0
                    set_ride(c, r[0], True)
            unrefined = sum(1 for c in live if c["ride_source"] == "estimate")
            routed = (len(live) - unrefined, unrefined)
            if unrefined:
                warnings.append("%d of %d hubs kept rough driving estimates (OSRM request cap). They are left out "
                                "of the ranked lists and appear only with --all." % (unrefined, len(live)))

    if arrive_by:   # riding all the way ends at the deadline: price it in the traffic of its own start
        f = settle(ride_all["free_s"], lambda rs: target - rs)   # the pickup, one ride before the deadline
        ride_all["ride_s"] = ride_all["free_s"] * f
        if ride_all["source"] != "estimate":
            ride_all["source"] = osrm_label(f)
    ride_all.pop("free_s")
    ride_all["cost"] = (ride_all["ride_km"] * cpk if car else
                        ride_cost(pricing, ride_all["ride_km"], ride_all["ride_s"], ((o_lat, o_lon), (d_lat, d_lon))))
    ride_all["arrive"] = t0 + first_wait_s + ride_all["ride_s"]
    min_save = (0.0 if car else 0.2 * ride_all["cost"]) if args.min_saving is None else args.min_saving
    gain_s = int(args.min_gain_min * 60)
    flat_fares = (region or {}).get("flat_fares")
    card_discount = (region or {}).get("card_discount")
    transfer_rules = (region or {}).get("transfer_rules")

    def add_fare(o):
        total, cur, parts, complete = journey_fare(net, o["legs"], flat_fares, args.payment, card_discount,
                                                   transfer_rules, day)
        o["transit_fare"], o["fare_currency"], o["fare_parts"], o["fare_complete"] = total, cur, parts, complete
        return o

    def rebook(free_s, f0, book0, ends, floor=None):
        """A ride booked at book0 was timed in factor f0's traffic, but it is picked up first_wait_s after the
        booking: time it in the traffic of that pickup and move the booking by the change (a few rounds; at a band
        edge that flips, the slower factor). Returns (book, ride seconds, factor). floor (depart mode: the trip
        start, when book0 is not before it): if the later traffic needs booking before the floor, book at the floor
        in f0's traffic, which the scan already showed catches the departure. Call it with the book worked out from
        f0, never with one it returned."""
        if args.traffic_factor or free_s is None:
            return book0, (free_s or 0.0) * f0, f0
        f, tried, book = f0, [f0], book0
        while len(tried) < 5:
            f2 = factor_at(book + first_wait_s)
            if f2 == f or (f2 in tried and max(f, f2) == f):
                break
            f = max(f, f2) if f2 in tried else f2
            tried.append(f)
            book = book0 - (int(free_s * f) - int(free_s * f0))
        if floor is not None and book < floor <= book0:
            return floor, free_s * f0, f0
        return book, free_s * f, f

    def ride_at(c, book0, floor=None):
        """rebook() for the ride to hub c (a walk start is unchanged)."""
        if c.get("walk"):
            return book0, c["ride_s"], None
        return rebook(c.get("ride_free_s"), c["ride_tf"], book0, ((o_lat, o_lon), (c["lat"], c["lon"])), floor)

    def make_option(c, sc=None, result=None):
        sc = sc or scan
        if arrive_by:
            book, s, dep, arr = result or c["result"]
            _, legs = sc.journey(s, dep)
            earlier = sc.latest_departure(s, arr - 60)
            backup = None
            e_book = ride_at(c, earlier[0] - (dep - book))[0] if earlier else None   # booked earlier: its own traffic
            if earlier and e_book >= now_s:   # only if it can still be booked
                _, e_legs = sc.journey(s, earlier[0])
                if e_legs:
                    backup = {"board": e_legs[0]["depart"], "arrive": earlier[1], "route": leg_name(e_legs[0]),
                              "book_by": e_book}
            board, stretch = (legs[0]["depart"] if legs else dep), None
            book, ride_s, ride_tf = ride_at(c, book)   # settled already for the main result; a finish result moves
        else:
            arr, s, ready = (result or c["result"])[:3]
            _, legs = sc.journey(s, ready)
            board = legs[0]["depart"] if legs else ready
            cutoff = legs[0].get("be_there_by", board) if legs else board   # be at the stop by
            book, ride_s, ride_tf = ride_at(c, cutoff - (ready - t0), t0)   # the ride in the traffic after booking
            nxt_arr, nxt_legs = sc.journey(s, cutoff + 60)
            backup = None if nxt_arr >= INF or not nxt_legs else {
                "board": nxt_legs[0]["depart"], "arrive": nxt_arr, "route": leg_name(nxt_legs[0])}
            stretch = None
            if safety_s:
                s_arr, s_legs = sc.journey(s, ready - safety_s)
                if s_arr < arr and s_legs:
                    stretch = {"board": s_legs[0]["depart"], "arrive": s_arr, "route": leg_name(s_legs[0]),
                               "slack_min": round((s_legs[0].get("be_there_by", s_legs[0]["depart"])
                                                   - (ready - safety_s)) / 60.0, 1),
                               "book_by": ride_at(c, s_legs[0].get("be_there_by", s_legs[0]["depart"])
                                                  - (ready - safety_s - t0), t0)[0]}
        hub = (net.stop_lat[s], net.stop_lon[s])
        o = {
            "hub": net.stop_name[s], "hub_agency": legs[0]["agency"] if legs else net.stop_feed[s],
            "hub_lat": hub[0], "hub_lon": hub[1],
            "ride_km": round(c["ride_km"], 1), "ride_min": round(ride_s / 60.0, 1),
            "ride_source": osrm_label(ride_tf) if c["ride_source"].startswith("osrm") else c["ride_source"],
            "ride_cost": round(first_cost(c, c["ride_km"], ride_s, ((o_lat, o_lon), (c["lat"], c["lon"]))), 2),
            "book_by": book, "board": board, "arrive": arr, "legs": legs, "backup": backup, "stretch": stretch,
            # no Uber link from a place where the region file says there is no ride-hail
            "uber": None if no_ride_hail_origin else uber_link((o_lat, o_lon), hub, net.stop_name[s]),
            "check_drive": gmaps_link((o_lat, o_lon), hub, "driving"),
            "check_transit": gmaps_link(hub, (d_lat, d_lon), "transit"),
        }
        if car:   # ride_cost holds the car cost (driving + known hub parking); ranking adds the fare (car_rank_cost)
            o.update(uber=None, drive_cost=round(c["ride_km"] * cpk, 2), car_cost=o["ride_cost"],
                     parking=c.get("parking"),
                     parking_unknown=bool(c.get("parking")) and (c.get("parking") or {}).get("cost") is None)
        return o

    def car_rank_cost(o):
        """Car mode: car cost + transit fare, for ranking. An unknown fare or hub parking price sorts after every
        known total (it is not $0)."""
        fare = o.get("transit_fare") if o.get("fare_complete") else None
        return (o["ride_cost"] + (fare if fare is not None else UNKNOWN_PARKING_PENALTY)
                + (UNKNOWN_PARKING_PENALTY if o.get("parking_unknown") else 0.0))

    common = {
        "version": VERSION,
        "origin": {"lat": o_lat, "lon": o_lon, "label": o_label},
        "destination": {"lat": d_lat, "lon": d_lon, "label": d_label},
        "region": (region or {}).get("id"), "timezone": tzname,
        "region_notes": (region or {}).get("notes_file"),
        "region_notes_files": (region or {}).get("notes_files"), "window_h": args.max_hours,
        "feeds": net.feeds, "pricing": pricing, "payment": args.payment,
        "transfer_rules": (transfer_rules or {}).get("verified") or bool(transfer_rules),
        "pickup_wait_min": (wait_s if car else first_wait_s) / 60.0, "safety_min": safety_s / 60.0,
        "radius_km": round(radius_m / 1000.0, 1),
        "hubs_checked": len(clusters), "stops_checked": len(cand),
        "hubs_with_service": sum(1 for c in clusters if c["result"]),
        "warnings": warnings,
    }

    # ---- transit-only baseline (same safety margin as the ride options; must still be catchable)
    transit_only, near_origin, missed_dep = None, [], None
    for s in used:
        d = haversine_m(o_lat, o_lon, net.stop_lat[s], net.stop_lon[s])
        if d > args.origin_walk_m:
            continue
        near_origin.append(s)
        if not scan.has_service(s):
            continue
        lead = walk_s(d) + safety_s
        walk_m = int(d * WALK_DETOUR)   # street distance estimate, not straight line
        if arrive_by:
            r = scan.latest_departure(s, target)
            if not r:
                continue
            if r[0] - lead < now_s - 60:   # its last workable departure has already gone
                missed_dep = max(missed_dep or -INF, r[0] - lead)
                continue
            if transit_only is None or (r[0] - lead, -d) > (transit_only["leave_by"], -transit_only["_d"]):
                transit_only = {"leave_by": r[0] - lead, "arrive": r[1], "stop": net.stop_name[s],
                                "walk_to_stop_m": walk_m, "_d": d, "_s": s, "_t": r[0], "_lead": lead}
        else:
            arr, c0 = scan.evaluate(s, t0 + lead)
            if arr >= INF:
                continue
            leave = net.conns[c0][0] - net.trip_buffer[net.conns[c0][4]] - lead   # equal arrivals: leave latest
            if transit_only is None or (arr, -leave, d) < (transit_only["arrive"], -transit_only["leave_by"],
                                                           transit_only["_d"]):
                transit_only = {"arrive": arr, "leave_by": leave, "stop": net.stop_name[s],
                                "walk_to_stop_m": walk_m, "_d": d, "_s": s, "_t": t0 + lead, "_lead": lead}
    stop_cluster = {s: c for c in clusters for s in c["stops"]}
    if transit_only:
        s_first, lead = transit_only.pop("_s"), transit_only.pop("_lead")
        transit_only.pop("_d")
        transit_only["legs"] = scan.journey(s_first, transit_only.pop("_t"))[1]
        add_fare(transit_only)
        first_dep = transit_only["legs"][0]["depart"] if transit_only["legs"] else None
        first_cut = transit_only["legs"][0].get("be_there_by", first_dep) if transit_only["legs"] else None
        transit_only["backup"] = None
        if first_dep is not None and arrive_by:
            earlier = scan.latest_departure(s_first, transit_only["arrive"] - 60)
            if earlier and earlier[0] - lead >= now_s - 60:
                _, e_legs = scan.journey(s_first, earlier[0])
                if e_legs:
                    transit_only["backup"] = {"board": e_legs[0]["depart"], "arrive": earlier[1],
                                              "leave_by": earlier[0] - lead, "route": leg_name(e_legs[0])}
        elif first_dep is not None:
            nxt_arr, nxt_legs = scan.journey(s_first, first_cut + 60)
            if nxt_arr < INF and nxt_legs:
                transit_only["backup"] = {"board": nxt_legs[0]["depart"], "arrive": nxt_arr,
                                          "leave_by": nxt_legs[0].get("be_there_by", nxt_legs[0]["depart"]) - lead,
                                          "route": leg_name(nxt_legs[0])}
        if transit_only["walk_to_stop_m"] > 400 and first_dep is not None and not car:
            c = stop_cluster.get(s_first)
            if c is not None:
                km, sec = c["ride_km"], c["ride_s"]
            else:
                km, sec = heuristic_ride(transit_only["walk_to_stop_m"] / WALK_DETOUR)
                sec *= tf
            ride_lead = first_wait_s + int(sec) + PLATFORM_S + safety_s
            book, dep, arr_sr = first_cut - ride_lead, first_dep, transit_only["arrive"]
            if book < now_s - 60:   # too late to ride to this departure: use the next one it can make
                arr_sr, legs_sr = scan.journey(s_first, now_s + ride_lead)
                dep = legs_sr[0]["depart"] if legs_sr else None
                book = legs_sr[0].get("be_there_by", dep) - ride_lead if dep is not None else None
            if book is not None and c is not None:   # the ride in the traffic after booking
                book, sec, _f = rebook(c.get("ride_free_s"), c["ride_tf"], book,
                                       ((o_lat, o_lon), (c["lat"], c["lon"])), None if arrive_by else t0)
            if book is not None and arr_sr < INF and not (arrive_by and arr_sr > t_end):   # never after the deadline
                transit_only["short_ride"] = {"cost": round(ride_cost(pricing, km, sec), 2), "book_by": book,
                                              "board": dep, "arrive": arr_sr}
    elif missed_dep is not None:
        warnings.append("transit only is no longer possible: its last workable departure needed leaving by %s."
                        % fmt_t(missed_dep))
    elif not egress:
        warnings.append("transit only cannot reach the door: no stop within %d m of the destination (see FINISH WITH "
                        "A RIDE)." % dest_m)
    elif near_origin:
        warnings.append("%d stops within %d m of the origin have departures in the window, but none of them reaches "
                        "the destination in time. Transit only does not work at this hour; try --max-hours or "
                        "another time." % (len(set(net.stop_name[s] for s in near_origin)), args.origin_walk_m))
    elif any(haversine_m(o_lat, o_lon, a, b) <= args.origin_walk_m for pts in net.idle_stops.values() for a, b in pts):
        idle = [lbl for lbl, pts in net.idle_stops.items()
                if any(haversine_m(o_lat, o_lon, a, b) <= args.origin_walk_m for a, b in pts)]
        warnings.append("the stops near the origin have no service on %s (%s), so the transit-only baseline is "
                        "missing; the ride options below start from hubs that do run." % (day.isoformat(),
                                                                                          ", ".join(idle)))
    elif any(haversine_m(o_lat, o_lon, a, b) <= args.origin_walk_m for pts in net.feed_stops.values() for a, b in pts):
        n_near = sum(1 for pts in net.feed_stops.values() for a, b in pts
                     if haversine_m(o_lat, o_lon, a, b) <= args.origin_walk_m)
        warnings.append("no departures between %s and %s from the %d stop%s within %d m of the origin, so the "
                        "transit-only baseline is missing; the ride options below start from hubs that do run."
                        % (fmt_t(t0), fmt_t(t_end), n_near, "" if n_near == 1 else "s", args.origin_walk_m))
    else:
        warnings.append("no stop within %d m of the origin in the loaded feeds, so the transit-only baseline is "
                        "missing. The origin town's own transit operator may be absent from the data%s. Check its "
                        "schedules, especially commuter or express routes into the city, before recommending a "
                        "ride: they can beat every option below." % (
                            args.origin_walk_m, " (failed to load: %s)" % ", ".join(failed) if failed else ""))

    # ---- ranking
    options = [make_option(c) for c in clusters if c["result"]]
    if car:   # the fare is most of a park-and-ride trip's cost: rank on car cost + fare (car_rank_cost)
        for o in options:
            add_fare(o)
        cost_of = car_rank_cost
    else:
        cost_of = lambda o: o["ride_cost"]  # noqa: E731
    if arrive_by:
        ride_all["book_by"] = target - int(ride_all["ride_s"]) - first_wait_s
        ride_all["arrive"] = target
        beats_transit = ((lambda o: o["book_by"] >= transit_only["leave_by"] + gain_s) if transit_only
                         else (lambda o: True))
        better = lambda o, best: o["book_by"] > best  # noqa: E731
        start, rank = -INF, (lambda o: (-o["book_by"], o["arrive"], cost_of(o)))
        front_key = lambda o: o["book_by"]  # noqa: E731
    else:
        beats_transit = ((lambda o: o["arrive"] <= transit_only["arrive"] - gain_s) if transit_only
                         else (lambda o: True))
        better = lambda o, best: o["arrive"] < best  # noqa: E731
        start, rank = INF, (lambda o: (o["arrive"], -o["book_by"], cost_of(o)))
        front_key = lambda o: o["arrive"]  # noqa: E731
    if car and transit_only:
        # driving to the station replaces the walk and local bus, so --min-gain-min does not apply: a hub is hidden
        # only when transit only is at least as good on time (leaves no earlier; in depart mode arrives no later,
        # and on the same arrival leaves no earlier) and costs no more than car + parking + fare. Savings within one
        # Pareto cost bucket (below) count as equal, so a long drive does not show for a few cents off the fare.
        to_fare = transit_only.get("transit_fare") if transit_only.get("fare_complete") else None
        to_step = max(1.0, 0.05 * ride_all["cost"])

        def beats_transit(o):
            if arrive_by:
                time_ok = transit_only["leave_by"] >= o["book_by"]
            else:
                time_ok = (transit_only["arrive"] < o["arrive"] or
                           (transit_only["arrive"] == o["arrive"] and transit_only["leave_by"] >= o["book_by"]))
            return not (time_ok and (to_fare is None or car_rank_cost(o) > to_fare - to_step))
    bookable = [o for o in options if o["book_by"] >= now_s - 60]
    if len(bookable) < len(options):
        warnings.append(("%d hubs left out because you would have had to leave before now (%s)." if car else
                         "%d hubs left out because their ride would have had to be booked before now (%s).")
                        % (len(options) - len(bookable), fmt_t(now_s)))
    if arrive_by and ride_all["book_by"] < now_s - 60:
        warnings.append(("driving all the way needed leaving by %s; it is too late to reach the deadline that way."
                         if car else "riding all the way needed booking by %s; it is too late to reach the deadline "
                         "that way.") % fmt_t(ride_all["book_by"]))
    rankable = [o for o in bookable if not (osrm_ok and o["ride_source"] == "estimate")]
    # a ride that makes the same connection as transit only but skips its local buses (e.g. a short ride to
    # the coach stop instead of two town buses): not faster, so not ranked, but worth a line
    if transit_only and transit_only.get("legs"):
        n_to = sum(1 for lg in transit_only["legs"] if lg["type"] == "ride")
        same = [o for o in rankable if sum(1 for lg in o["legs"] if lg["type"] == "ride") < n_to
                and (o["book_by"] >= transit_only["leave_by"] if arrive_by else o["arrive"] <= transit_only["arrive"])]
        if same:
            o = min(same, key=lambda o: (sum(1 for lg in o["legs"] if lg["type"] == "ride"), o["ride_cost"],
                                         -o["book_by"]))   # fewest vehicles, then cheapest
            add_fare(o)
            transit_only["ride_instead"] = {
                "hub": o["hub"], "cost": o["ride_cost"], "km": o["ride_km"], "book_by": o["book_by"],
                "board": o["board"], "arrive": o["arrive"], "route": leg_name(o["legs"][0]) if o["legs"] else "",
                "skips": n_to - sum(1 for lg in o["legs"] if lg["type"] == "ride"), "uber": o["uber"],
                "transit_fare": o.get("transit_fare")}
            if car:
                transit_only["ride_instead"]["parking"] = o.get("parking")
    pre = [o for o in rankable if beats_transit(o)]
    useful = [o for o in pre if ride_all["cost"] - o.get("drive_cost", o["ride_cost"]) >= min_save]
    hidden_gain = len(rankable) - len(pre)
    hidden_save = len(pre) - len(useful)
    if hidden_save:
        hint = " (use --min-saving 0 to show them)" if args.min_saving is None else ""
        if min_save > 0:
            warnings.append(("%d hubs hidden because driving there saves less than %.0f %s vs driving all the way%s"
                             if car else "%d hubs hidden because their ride saves less than %.0f %s vs riding all "
                             "the way%s") % (hidden_save, min_save, pricing.get("currency", ""), hint))
        else:
            warnings.append(("%d hubs hidden because driving there costs more than driving all the way" if car else
                             "%d hubs hidden because their ride costs more than riding all the way") % hidden_save)
    transit_competitive = bool(transit_only) and not pre
    if transit_only and hidden_gain and car:
        warnings.append("%d hubs hidden because transit only is as fast and costs no more (car + parking + fare)."
                        % hidden_gain)
    elif transit_only and hidden_gain:
        warnings.append("%d hubs hidden because they gain less than %d min over transit only (--min-gain-min)."
                        % (hidden_gain, args.min_gain_min))

    # Pareto front on (ride cost, time). Costs within one bucket (about 5% of the full ride, at least
    # 1 currency unit) count as equal, so a few cents never beat a much better time. Car mode: car + fare.
    step = max(1.0, 0.05 * ride_all["cost"])
    pareto, best = [], start
    for o in sorted(useful, key=lambda o: (math.floor(cost_of(o) / step),) + rank(o)):
        if better(o, best):
            pareto.append(o)
            best = front_key(o)
    pareto.sort(key=rank)
    keys = {id(o) for o in pareto}

    def ride_keys(o):
        rides = [lg for lg in o["legs"] if lg["type"] == "ride"]
        return (rides[0]["trip_key"], rides[-1]["trip_key"]) if rides else (None, None)

    # runners-up: skip hubs that board the same first vehicle, or finish on the same last vehicle,
    # as an option already listed
    firsts = {ride_keys(o)[0] for o in pareto}
    lasts = {ride_keys(o)[1] for o in pareto}
    runners, same_vehicle, past_top = [], 0, 0   # the last two: hubs left out without another warning
    for o in sorted(useful, key=rank):
        if id(o) in keys:
            continue
        first, last = ride_keys(o)
        if first in firsts or last in lasts:
            same_vehicle += 1
            continue
        if len(runners) >= max(0, args.top - len(pareto)):
            past_top += 1
            continue
        firsts.add(first)
        lasts.add(last)
        runners.append(o)
    for o in pareto + runners:
        add_fare(o)

    # ---- finish with a ride: transit to a stop short of the destination, then a ride for the rest.
    # One scan per drop-off point keeps it cost-aware: a single scan would always pick the earliest
    # arrival, which is usually "leave the bus early and ride far".
    finish, finish_info = [], None
    if final_m > 0:
        starts = [c for c in clusters if c["ride_source"] != "estimate" or not osrm_ok]
        walk_start = {"lat": o_lat, "lon": o_lon, "ride_s": 0.0, "ride_km": 0.0, "ride_source": "walk",
                      "stops": [s for s in used if haversine_m(o_lat, o_lon, net.stop_lat[s], net.stop_lon[s])
                                <= args.origin_walk_m], "walk": True}
        def final_adjust(e, arr):
            """Depart mode: the final ride starts when the plan gets there, not at the start of the trip.
            (arrival, ride seconds, factor) with the ride in the traffic of that time."""
            f = factor_at(arr - e["ride_s"])
            ride_s = e["ride_free_s"] * f
            return arr - e["ride_s"] + ride_s, ride_s, f

        raw = []   # (score, total ride cost, start, drop, result, final ride)
        for k, e in enumerate(pick):
            for c in starts + ([walk_start] if walk_start["stops"] else []):
                if c.get("walk"):
                    best = None
                    for s in c["stops"]:
                        lead = walk_s(haversine_m(o_lat, o_lon, net.stop_lat[s], net.stop_lon[s])) + safety_s
                        if arrive_by:
                            r = e["scan"].latest_departure(s, target)
                            if r and r[0] - lead >= t0 and (best is None or r[0] - lead > best[0]):
                                best = (r[0] - lead, s, r[0], r[1])
                        else:
                            arr, _c0 = e["scan"].evaluate(s, t0 + lead)
                            if arr < INF and (best is None or arr < best[0]):
                                best = (arr, s, t0 + lead)
                    res, first_c = best, 0.0
                else:
                    res, first_c = c["fresults"][k], None
                if not res:
                    continue
                if not arrive_by and res[0] >= INF:
                    continue
                book_r = res[0] if arrive_by else None
                if first_c is None:   # the ride in the traffic after its own booking (make_option does the same)
                    book_r, rs, _f = ride_at(c, res[0]) if arrive_by else ride_at(c, t0 + res[3], t0)
                    first_c = first_cost(c, c["ride_km"], rs, ((o_lat, o_lon), (c["lat"], c["lon"])))
                if arrive_by and book_r < now_s - 60:   # its ride or walk would have had to start already
                    continue
                fr = {"ride_s": e["ride_s"], "cost": e["cost"], "source": e["ride_source"]}
                if not arrive_by:
                    arr2, rs2, f2 = final_adjust(e, res[0])
                    if rs2 != e["ride_s"]:
                        fr = {"ride_s": rs2, "source": osrm_label(f2) if e["osrm"] else "estimate",
                              "cost": ride_cost(e["pricing"], e["ride_km"], rs2,
                                                ((e["lat"], e["lon"]), (d_lat, d_lon)))}
                        res = (arr2,) + tuple(res[1:])
                score = -book_r if arrive_by else res[0]
                total = first_c + fr["cost"]
                if total <= ride_all["cost"] - min_save:
                    raw.append((score, total, c, e, res, fr))
        # drop mixes that a plan reaching the door by transit beats on both time and ride cost, and paid
        # rides that gain less than --min-gain-min over transit only
        main_pts = [(-o["book_by"] if arrive_by else o["arrive"], o["ride_cost"]) for o in rankable]
        if transit_only:
            to_score = -transit_only["leave_by"] if arrive_by else transit_only["arrive"]
            main_pts.append((to_score, 0.0))
            raw = [x for x in raw if x[0] <= to_score - gain_s]
        raw = [x for x in raw if not any(ms <= x[0] and mc <= x[1] for ms, mc in main_pts)]
        # Pareto on (total ride cost, time), with the same cost buckets as the main list
        front, best_score = [], INF
        for score, total, c, e, res, fr in sorted(raw, key=lambda x: (math.floor(x[1] / step), x[0], x[1],
                                                                      -(x[4][0] if arrive_by else x[4][2]))):
            if score < best_score:
                front.append((score, total, c, e, res, fr))
                best_score = score
        front.sort(key=lambda x: x[0])
        k = max(2, args.top // 2)
        shown = front if len(front) <= k else front[:k - 1] + front[-1:]   # fastest few and the cheapest
        for score, total, c, e, res, fr in shown:
            o = make_option(c, sc=e["scan"], result=res)
            if not arrive_by:   # a later backup or stretch reaches the final ride at another hour
                for alt in (o["backup"], o["stretch"]):
                    if alt:
                        alt["arrive"] = final_adjust(e, alt["arrive"])[0]
            if c.get("walk"):
                o.update(hub="walk to %s" % (o["legs"][0]["from"] if o["legs"] else "the first stop"),
                         ride_km=0.0, ride_min=0.0, ride_cost=0.0, ride_source="walk", uber=None, check_drive=None)
                if car:
                    o.update(drive_cost=0.0, car_cost=0.0, parking=None)
            if o["legs"] and o["legs"][-1]["type"] == "walk" and o["legs"][-1]["to"] == "destination":
                o["legs"].pop()
            last = next((lg for lg in reversed(o["legs"]) if lg["type"] == "ride"), None)
            drop_name = last["to"] if last else net.stop_name[e["stops"][0]]
            o["final_ride"] = {"from": drop_name, "lat": e["lat"], "lon": e["lon"], "km": round(e["ride_km"], 1),
                               "priced_with": e.get("pricing_label", ""),
                               "min": round(fr["ride_s"] / 60.0, 1), "source": fr["source"],
                               "cost": round(fr["cost"], 2), "book_at": last["arrive"] if last else None,
                               "service": (area or {}).get("service", "Uber/Lyft"),
                               "prebook": (area or {}).get("uber") in ("no", "sparse"),
                               # no Uber link where the region file says there is no ride-hail
                               "uber": (None if (area or {}).get("uber") == "no"
                                        else uber_link((e["lat"], e["lon"]), (d_lat, d_lon), d_label)),
                               "check_drive": gmaps_link((e["lat"], e["lon"]), (d_lat, d_lon), "driving")}
            o["legs"].append({"type": "final_ride", "to": "destination", "from": drop_name,
                              "seconds": int(fr["ride_s"]), "km": round(e["ride_km"], 1)})
            o["total_ride_cost"] = round(total, 2)
            add_fare(o)
            finish.append(o)
        finish_info = {"radius_km": round(final_m / 1000.0, 1), "drop_points": [
            {"name": net.stop_name[e["stops"][0]], "km": round(e["ride_km"], 1), "cost": round(e["cost"], 2)}
            for e in pick], "area": area}
        if not pick:
            warnings.append("no stop within %.0f km of the destination has arrivals in the window, so no plan "
                            "finishes with a ride." % (final_m / 1000.0))
        elif not finish and not options:
            warnings.append("stops within %.0f km of the destination (%s) have arrivals, but no plan reaches them "
                            "in the window: try an earlier start or --max-hours." % (
                                final_m / 1000.0, ", ".join(net.stop_name[e["stops"][0]] for e in pick[:3])))
        if pick and area and area.get("uber") == "no":
            # say what the region file says: which taxis it lists (with their own caveats) and the area note
            taxis = taxis_text(area)
            warnings.append("there is no ride-hail near %s%s. %s The plan allows %d min for a pre-booked taxi to "
                            "arrive; confirm one will come before relying on a plan that finishes with a ride."
                            % (area.get("name", "the destination"),
                               (" (area note: %s)" % area["note"].rstrip(".")) if area.get("note") else "",
                               ("Taxis listed: %s." % taxis) if taxis else "No taxi is listed for it.",
                               taxi_wait_s // 60))
        elif pick and area and area.get("uber") == "sparse":
            warnings.append("ride-hail near %s is sparse: book the final ride ahead (Uber Reserve) or call a local "
                            "taxi (see FINISH WITH A RIDE)." % area.get("name", "the destination"))

    if not options and not finish:
        warnings.append("no hub within %.1f km reaches the destination between %s and %s. Try a longer "
                        "--max-hours, another time, or a larger --radius-km." % (radius_m / 1000.0, fmt_t(t0),
                                                                                 fmt_t(t_end)))

    # ---- second opinion: Transitous transit-only from the origin, and from each of the top hubs
    crosscheck = None
    if args.crosscheck:
        base = dt.datetime.combine(day, dt.time(0))

        def to_dt(sec):
            return base + dt.timedelta(seconds=int(sec))

        when = deadline if arrive_by else depart
        lo_ok = when - dt.timedelta(hours=args.max_hours + 1) if arrive_by else when - dt.timedelta(minutes=30)
        hi_ok = when + dt.timedelta(minutes=30) if arrive_by else when + dt.timedelta(hours=args.max_hours + 1)
        crosscheck = {"source": "Transitous (transitous.org, non-commercial, best effort)", "transit_only": None,
                      "hubs": [], "attribution": TRANSITOUS_SOURCES_URL}

        def ask(point, at, by):
            its, how = transitous(point, (d_lat, d_lon), at, by, tzname, region)
            crosscheck["timezone_from"] = how
            inside = [i for i in its if lo_ok <= i["arrive_dt"] <= hi_ok]
            if its and not inside:
                warnings.append("Transitous answers fall outside the requested time window (time zone problem?); "
                                "they were not compared.")
            return inside

        def pick(its, by):   # arrive-by: the latest departure; otherwise the earliest arrival
            if not its:
                return None
            return max(its, key=lambda i: i["depart_dt"]) if by else min(its, key=lambda i: i["arrive_dt"])

        def summary(i):
            return None if i is None else {"from": i["hub"], "depart": i["depart_dt"].strftime("%H:%M"),
                                           "arrive": i["arrive_dt"].strftime("%H:%M"), "legs": i["legs"]}

        def verdict(i, our_board, our_arrive):
            if i is None:
                return "no itinerary found"
            if arrive_by:   # compare how late each boards its first vehicle (both start with a walk or a ride)
                diff = (i.get("board_dt", i["depart_dt"]) - to_dt(our_board)).total_seconds() / 60.0
                return ("agrees" if abs(diff) <= 3 else
                        "Transitous leaves %.0f min later: check its route" % diff if diff > 0 else
                        "Transitous leaves %.0f min earlier" % -diff)
            diff = (i["arrive_dt"] - to_dt(our_arrive)).total_seconds() / 60.0
            return ("agrees" if abs(diff) <= 3 else
                    "Transitous arrives %.0f min earlier: check its route" % -diff if diff < 0 else
                    "Transitous arrives %.0f min later" % diff)

        try:
            b = pick(ask((o_lat, o_lon), when, arrive_by), arrive_by)
            ours_b = transit_only["legs"][0]["depart"] if transit_only and transit_only["legs"] else None
            v = verdict(b, ours_b, transit_only["arrive"]) if transit_only else (
                "no itinerary found" if b is None else "Transitous found one; this data did not")
            crosscheck["transit_only"] = {"transitous": summary(b), "verdict": v}
        except Exception as e:  # noqa: BLE001
            warnings.append("Transitous cross-check (transit only) failed: %s" % e)
        for o in (pareto + runners)[:3]:
            try:
                at = when if arrive_by else to_dt(o["board"]) - dt.timedelta(minutes=2)
                b = pick(ask((o["hub_lat"], o["hub_lon"]), at, arrive_by), arrive_by)
                crosscheck["hubs"].append({"hub": o["hub"], "transitous": summary(b),
                                           "ours": {"board": fmt_t(o["board"]), "arrive": fmt_t(o["arrive"])},
                                           "verdict": verdict(b, o["board"], o["arrive"])})
            except Exception as e:  # noqa: BLE001
                warnings.append("Transitous cross-check from %s failed: %s" % (o["hub"], e))
        flagged = [h["hub"] for h in crosscheck["hubs"] if "check its route" in h["verdict"]]
        if crosscheck["transit_only"] and "check its route" in crosscheck["transit_only"]["verdict"]:
            flagged.append("transit only")
        if flagged:
            warnings.append("Transitous finds a better connection for: %s. Compare its route in SECOND OPINION; "
                            "it may use a route or walk this data lacks." % ", ".join(flagged))

    common.update({
        "mode": "arrive_by" if arrive_by else "depart",
        "depart": ("arrive by " + deadline.strftime("%a %Y-%m-%d %H:%M")) if arrive_by
        else depart.strftime("%a %Y-%m-%d %H:%M"),
        "deadline": t_end if arrive_by else None,
        "ride_all": ride_all, "transit_only": transit_only, "transit_competitive": transit_competitive,
        "min_gain_min": args.min_gain_min,
        "pareto": pareto, "others": runners, "finish_with_ride": finish, "finish_info": finish_info,
        "hidden_hubs": {"same_vehicle": same_vehicle, "past_top": past_top, "top": args.top},
        "hubs_routed": None if routed is None else {"osrm": routed[0], "estimate": routed[1]},
        "all_options": sorted(options, key=rank) if args.all else None,
        "crosscheck": crosscheck,
    })
    if car:
        common.update({"access": "car", "parking_summary": parking_summary,
                       "car": {"cost_per_km": cpk, "park_min": board_s / 60.0, "stay_hours": stay_min / 60.0,
                               "traffic_factor": tf, "traffic_basis": ("planning allowance, %s" % tf_basis)
                               if tf_basis else "--traffic-factor"}})
    return common


# --------------------------------------------------------------------------- output

def leg_name(lg):
    head = lg.get("headsign") or ""
    agency, short = lg["agency"] or "", lg["route"] or ""
    first = re.split(r"[\s-]", agency.strip())[0].lower() if agency.strip() else ""
    # feeds that repeat the brand in the route name ("FlixBus 2702", "VIA Rail") need it only once
    route = short if first and short.lower().startswith(first) else "%s %s" % (agency, short)
    return '%s "%s"' % (route, head) if head and head.lower() != (lg["route"] or "").lower() else route


def legs_text(legs):
    parts = []
    for lg in legs:
        if lg["type"] == "ride":
            hops = "nonstop" if lg["stops"] == 1 else "%d stops" % lg["stops"]
            notes = []
            if lg.get("be_there_by") is not None:
                notes.append("be at the stop by %s" % fmt_t(lg["be_there_by"]))
            if lg.get("request_board"):
                notes.append("request stop: call ahead or flag the driver to board")
            if lg.get("request_alight"):
                notes.append("request stop: tell the driver you are getting off")
            parts.append("%s: %s -> %s (%s-%s, %s)%s" % (
                leg_name(lg), lg["from"], lg["to"], fmt_t(lg["depart"]), fmt_t(lg["arrive"]), hops,
                " [%s]" % "; ".join(notes) if notes else ""))
        elif lg["type"] == "final_ride":
            parts.append("final ride %.1f km / %s from %s to the destination" % (lg["km"], fmt_min(lg["seconds"]),
                                                                              lg["from"]))
        else:
            parts.append("walk %s to %s" % (fmt_min(lg["seconds"]), lg["to"]))
    return "\n        ".join(parts)


def fare_text(o, cur_ride, has_region, rules_on=False):
    if o.get("transit_fare") is None and not o.get("fare_parts"):
        return "no transit fare"
    known = [p for p in o["fare_parts"] if p["fare"] is not None]
    missing = [p for p in o["fare_parts"] if p["fare"] is None]
    bits = []
    if known:
        each = []
        for p in known:
            if p.get("note") and not p["fare"]:
                each.append("%s %s" % (p["agency"], p["note"]))
            else:
                each.append("%s %.2f" % (p["agency"], p["fare"]))
        bits.append("transit fare %.2f %s (%s)" % (sum(p["fare"] for p in known), o.get("fare_currency") or "",
                                                    "; ".join(each)))
    for p in missing:
        where = p.get("fare_url") or ("see region notes" if has_region else "check the agency's website")
        bits.append("%s fare not in the data (%s)" % (p["agency"], where))
    txt = " + ".join(bits)
    if o.get("ride_cost") is not None and o.get("fare_complete") and (o.get("fare_currency") or cur_ride) == cur_ride:
        txt += ", total ~%.0f %s" % (o["ride_cost"] + o["transit_fare"], cur_ride)
    if not rules_on and len(o["fare_parts"]) > 1:
        txt += " (before transfer discounts)"
    return txt


def mins_vs(sec, pos, neg, same):
    """'12 min earlier' / '5 min later' / 'at the same time' for a signed difference in seconds."""
    m = int(round(sec / 60.0))
    return same if m == 0 else "%d min %s" % (abs(m), pos if m > 0 else neg)


def routed_text(res):
    """' (999 with OSRM driving times, 226 on rough estimates)' when the OSRM request cap left hubs unrouted."""
    r = res.get("hubs_routed") or {}
    if not r.get("estimate"):
        return ""
    return " (%d with OSRM driving times, %d on rough estimates past the request cap)" % (r["osrm"], r["estimate"])


def unknown_parking(p):
    """' + hub parking price unknown' when a car option's hub parking has no known price, else ''."""
    return " + hub parking price unknown" if p and p.get("cost") is None else ""


def second_opinion_lines(cc):
    """The SECOND OPINION section (Transitous cross-check), with the visible link to transitous.org/sources
    that Transitous's usage policy asks for. [] when no cross-check ran."""
    if not cc:
        return []
    out = ["SECOND OPINION  %s; times converted with %s" % (cc["source"], cc.get("timezone_from", "?")),
           "  data: Transitous and the open data it routes on, %s" % cc.get("attribution", TRANSITOUS_SOURCES_URL)]

    def its_text(t):
        return "%s-%s %s" % (t["depart"], t["arrive"], " | ".join(t["legs"])) if t else "no itinerary found"

    if cc.get("transit_only"):
        out.append("  transit only from the origin: %s  [%s]"
                   % (its_text(cc["transit_only"]["transitous"]), cc["transit_only"]["verdict"]))
    for h in cc.get("hubs") or []:
        out.append("  from %s (ours: board %s, arrive %s): %s  [%s]"
                   % (h["hub"], h["ours"]["board"], h["ours"]["arrive"], its_text(h["transitous"]), h["verdict"]))
    out.append("")
    return out


def render(res):
    cur = res["pricing"].get("currency", "")
    by = res.get("mode") == "arrive_by"
    has_region = bool(res.get("region"))
    rules_on = bool(res.get("transfer_rules"))
    car = res.get("access") == "car"
    out = []
    out.append("TRIP  %s  ->  %s" % (res["origin"]["label"], res["destination"]["label"]))
    out.append("WHEN  %s (%s)   region: %s   %s radius: %s km   search window %s h   fares at %s rates"
               % (res["depart"], res.get("timezone") or "local time", res["region"] or "none",
                  "drive" if car else "ride", res["radius_km"], res.get("window_h", "?"), res.get("payment", "card")))
    out.append("FEEDS  " + "; ".join("%s (%s..%s)" % (f["name"], f["service_from"], f["service_to"]) for f in res["feeds"]))
    p = res["pricing"]
    uplift = p.get("uplift", 1.0)
    if car:
        cm = res.get("car") or {}
        out.append("CAR COST MODEL  $%.2f/km for your own car (default: fuel about 0.14 + wear 0.03; "
                   "--car-cost-per-km), hub parking priced for a %g h stay (--stay-hours), %d min to park and walk "
                   "to the platform. No tolls counted."
                   % (cm.get("cost_per_km", 0.0), cm.get("stay_hours", 0.0), cm.get("park_min", 0)))
        if cm.get("traffic_factor"):
            out.append("DRIVE TIMES  free-flow x%.2f (%s), plus %d min spare at the hub"
                       % (cm["traffic_factor"], cm.get("traffic_basis", ""), res["safety_min"]))
        ps = res.get("parking_summary") or {}
        basis = ", ".join("%s %d" % (k, v) for k, v in sorted((ps.get("by_basis") or {}).items(), key=lambda x: -x[1]))
        out.append("COVERAGE  %d stops in range grouped into %d hubs; %d have parking (%s), %d without known parking "
                   "left out; %d of them reach the destination in the window%s."
                   % (res["stops_checked"], ps.get("hubs_in_range", res["hubs_checked"]), ps.get("with_parking", 0),
                      basis or "none", ps.get("excluded", 0), res["hubs_with_service"], routed_text(res)))
    else:
        out.append("RIDE COST MODEL  %s: %.2f + %.2f/km + %.2f/min + booking fee, x%.2f tax%s, %s "
                   "(%s). Pickup wait %d min." % (p.get("label"), p["base"], p["per_km"], p["per_min"],
                                                   1 + p.get("tax", 0.0),
                                                   ", x%.2f upfront uplift" % uplift if uplift != 1.0 else "",
                                                   cur, p.get("source", ""), res["pickup_wait_min"]))
        out.append("COVERAGE  %d stops in range grouped into %d hubs, %d of them reach the destination in the window%s."
                   % (res["stops_checked"], res["hubs_checked"], res["hubs_with_service"], routed_text(res)))
    notes = res.get("region_notes_files") or ([res["region_notes"]] if res.get("region_notes") else [])
    if notes:
        out.append("REGION NOTES  %s (on-demand services, fares, feed gaps)"
                   % ", ".join("references/regions/%s" % n for n in notes))
    out.append("")
    ra = res["ride_all"]
    if car:
        out.append("BASELINE drive all the way: %sarrive %s, %.1f km, %s, car ~$%.2f + parking at the destination "
                   "(run drive_plan.py for tolls and parking) (%s)"
                   % ("leave by %s, " % fmt_t(ra["book_by"]) if by else "", fmt_t(ra["arrive"]), ra["ride_km"],
                      fmt_min(ra["ride_s"]), ra["cost"], ra["source"]))
        if by and ra["ride_s"] > 1800 and not str((res.get("car") or {}).get("traffic_basis", "")).startswith("plan"):
            out.append("        only the %d min safety margin is built in on a %s drive: leave ~%d min earlier if "
                       "traffic is uncertain" % (res["safety_min"], fmt_min(ra["ride_s"]), round(ra["ride_s"] / 600.0)))
    elif by:
        out.append("BASELINE ride all the way: book by %s, arrive %s, %.1f km, %s, ~%.0f %s (%s)"
                   % (fmt_t(ra["book_by"]), fmt_t(ra["arrive"]), ra["ride_km"], fmt_min(ra["ride_s"]), ra["cost"],
                      cur, ra["source"]))
        if ra["ride_s"] > 1800:
            out.append("        only the %d min safety margin is built in on a %s drive: book ~%d min earlier if "
                       "traffic is uncertain" % (res["safety_min"], fmt_min(ra["ride_s"]), round(ra["ride_s"] / 600.0)))
    else:
        out.append("BASELINE ride all the way: arrive %s, %.1f km, %s, ~%.0f %s (%s)"
                   % (fmt_t(ra["arrive"]), ra["ride_km"], fmt_min(ra["ride_s"]), ra["cost"], cur, ra["source"]))
    to = res["transit_only"]
    if to:
        out.append("BASELINE transit only: leave by %s, arrive %s (walk ~%d m to %s), %s\n        %s"
                   % (fmt_t(to["leave_by"]) if to.get("leave_by") is not None else "?", fmt_t(to["arrive"]),
                      to["walk_to_stop_m"], to["stop"],
                      fare_text(to, cur, has_region, rules_on), legs_text(to["legs"])))
        if to.get("backup"):
            b = to["backup"]
            out.append("        %s: leave by %s, %s at %s, arrive %s"
                       % ("safer earlier option" if by else "backup if missed", fmt_t(b["leave_by"]), b["route"],
                          fmt_t(b["board"]), fmt_t(b["arrive"])))
        if to.get("short_ride"):
            sr = to["short_ride"]
            out.append("        or ride to that stop instead of walking: ~%.0f %s, book by %s, board %s, arrive %s "
                       "(luggage, weather)" % (sr["cost"], cur, fmt_t(sr["book_by"]), fmt_t(sr["board"]),
                                               fmt_t(sr["arrive"])))
        listed = {o["hub"] for o in (res.get("pareto") or []) + (res.get("others") or [])}
        same_trip = {(o["hub"], o["board"]) for o in (res.get("pareto") or []) + (res.get("others") or [])}
        if to.get("ride_instead") and car and to["ride_instead"]["hub"] in listed:
            pass   # car mode lists that hub below
        elif to.get("ride_instead") and (to["ride_instead"]["hub"], to["ride_instead"]["board"]) in same_trip:
            pass   # the same hub and vehicle is listed below
        elif to.get("ride_instead") and car:
            ri = to["ride_instead"]
            out.append("        or drive %.1f km to %s instead (parking: %s): car ~$%.2f%s, leave by %s, board %s at %s, "
                       "arrive %s (skips %d local bus%s)" % (ri["km"], ri["hub"], (ri.get("parking") or {}).get("text", "?"),
                                                             ri["cost"], unknown_parking(ri.get("parking")),
                                                             fmt_t(ri["book_by"]), ri["route"],
                                                             fmt_t(ri["board"]), fmt_t(ri["arrive"]), ri["skips"],
                                                             "es" if ri["skips"] > 1 else ""))
        elif to.get("ride_instead"):
            ri = to["ride_instead"]
            out.append("        or ride %.1f km to %s instead: ~%.0f %s, book by %s, board %s at %s, arrive %s "
                       "(skips %d local bus%s)" % (ri["km"], ri["hub"], ri["cost"], cur, fmt_t(ri["book_by"]),
                                                   ri["route"], fmt_t(ri["board"]), fmt_t(ri["arrive"]),
                                                   ri["skips"], "es" if ri["skips"] > 1 else ""))
    else:
        out.append("BASELINE transit only: none found (see WARNING lines)")
    if res.get("transit_competitive"):
        gain_all = ((to["leave_by"] - ra["book_by"]) if by else (to["arrive"] - ra["arrive"])) / 60.0 * (-1 if by else 1)
        if gain_all >= 1 and car:
            out.append("NOTE  no drive-to-transit hub beats transit only on time or cost. Driving all the way gains "
                       "%.0f min for ~$%.2f plus parking at the destination; otherwise transit only."
                       % (gain_all, ra["cost"]))
        elif car:
            out.append("NOTE  no drive-to-transit hub beats transit only on time or cost. Recommend transit only.")
        elif gain_all >= res["min_gain_min"]:
            out.append("NOTE  no ride-to-transit hub gains %d+ min over transit only. Riding all the way gains %.0f min "
                       "for ~%.0f %s; otherwise transit only (or the short ride to its first stop)."
                       % (res["min_gain_min"], gain_all, ra["cost"], cur))
        else:
            out.append("NOTE  no ride option gains %d+ min over transit only. Recommend transit only (or the short "
                       "ride to its first stop)." % res["min_gain_min"])
    out.append("")

    def block(title, opts):
        if not opts:
            return
        out.append(title)
        if car:
            car_block(opts)
            return
        for i, o in enumerate(opts, 1):
            save = ra["cost"] - o["ride_cost"]
            if by:
                vs = "saves ~%.0f %s on the ride, book %s" % (
                    save, cur, mins_vs(ra["book_by"] - o["book_by"], "earlier", "later", "at the same time"))
                if to:
                    vs += "; leaves %s" % mins_vs(o["book_by"] - to["leave_by"], "later than transit only",
                                                  "earlier than transit only", "when transit only does")
            else:
                vs = "saves ~%.0f %s on the ride, arrives %+.0f min" % (save, cur, (o["arrive"] - ra["arrive"]) / 60.0)
                if to:
                    vs += "; %s" % mins_vs(to["arrive"] - o["arrive"], "earlier than transit only",
                                           "later than transit only", "same time as transit only")
            out.append("%2d. %s  [%s]" % (i, o["hub"], o["hub_agency"]))
            out.append("    ride %.1f km / %.0f min (%s), ~%.0f %s  |  book by %s, board %s, ARRIVE %s"
                       % (o["ride_km"], o["ride_min"], o["ride_source"], o["ride_cost"], cur,
                          fmt_t(o["book_by"]), fmt_t(o["board"]), fmt_t(o["arrive"])))
            out.append("    vs ride all the way: " + vs)
            out.append("    " + fare_text(o, cur, has_region, rules_on))
            if o.get("stretch"):
                st = o["stretch"]
                out.append("    STRETCH (only %.1f min slack, book by %s): %s at %s, arrive %s"
                           % (st["slack_min"], fmt_t(st["book_by"]), st["route"], fmt_t(st["board"]),
                              fmt_t(st["arrive"])))
            out.append("        " + legs_text(o["legs"]))
            if o["backup"] and by:
                out.append("    safer earlier option: book by %s, %s at %s, arrive %s"
                           % (fmt_t(o["backup"]["book_by"]), o["backup"]["route"], fmt_t(o["backup"]["board"]),
                              fmt_t(o["backup"]["arrive"])))
            elif o["backup"]:
                out.append("    backup if missed: %s at %s, arrive %s"
                           % (o["backup"]["route"], fmt_t(o["backup"]["board"]), fmt_t(o["backup"]["arrive"])))
            out.append("    check: drive %s\n           transit %s" % (o["check_drive"], o["check_transit"])
                       + ("\n           uber quote %s" % o["uber"] if o["uber"] else ""))   # none: no ride-hail
            out.append("")

    def car_block(opts):
        for i, o in enumerate(opts, 1):
            diff = ra["cost"] - o["car_cost"]
            vs = "car ~$%.2f %s" % (abs(diff), "less" if diff >= 0 else "more")
            if by:
                vs += ", leave %s" % mins_vs(ra["book_by"] - o["book_by"], "earlier", "later", "at the same time")
                if to:
                    vs += "; leaves %s" % mins_vs(o["book_by"] - to["leave_by"], "later than transit only",
                                                  "earlier than transit only", "when transit only does")
            else:
                vs += ", arrives %+.0f min" % ((o["arrive"] - ra["arrive"]) / 60.0)
                if to:
                    vs += "; arrives %s" % mins_vs(to["arrive"] - o["arrive"], "earlier than transit only",
                                                   "later than transit only", "with transit only")
            pk = o.get("parking") or {}
            out.append("%2d. %s  [%s]" % (i, o["hub"], o["hub_agency"]))
            out.append("    drive %.1f km / %.0f min (%s), car ~$%.2f%s  |  leave by %s, board %s, ARRIVE %s"
                       % (o["ride_km"], o["ride_min"], o["ride_source"], o["drive_cost"],
                          " + parking $%.2f" % pk["cost"] if pk.get("cost") else unknown_parking(pk),
                          fmt_t(o["book_by"]), fmt_t(o["board"]), fmt_t(o["arrive"])))
            out.append("    parking: %s [%s]" % (pk.get("text", "?"), pk.get("basis", "?")))
            out.append("    vs drive all the way: %s (parking at the destination not counted)" % vs)
            txt = fare_text(dict(o, ride_cost=None), cur, has_region, rules_on)
            if o.get("transit_fare") is not None and o.get("fare_complete"):
                txt += ", total ~$%.2f (car%s + fare)%s" % (o["car_cost"] + o["transit_fare"],
                                                            " + parking" if pk.get("cost") else "", unknown_parking(pk))
            out.append("    " + txt)
            if o.get("stretch"):
                st = o["stretch"]
                out.append("    STRETCH (only %.1f min slack, leave by %s): %s at %s, arrive %s"
                           % (st["slack_min"], fmt_t(st["book_by"]), st["route"], fmt_t(st["board"]),
                              fmt_t(st["arrive"])))
            out.append("        " + legs_text(o["legs"]))
            if o["backup"] and by:
                out.append("    safer earlier option: leave by %s, %s at %s, arrive %s"
                           % (fmt_t(o["backup"]["book_by"]), o["backup"]["route"], fmt_t(o["backup"]["board"]),
                              fmt_t(o["backup"]["arrive"])))
            elif o["backup"]:
                out.append("    backup if missed: %s at %s, arrive %s"
                           % (o["backup"]["route"], fmt_t(o["backup"]["board"]), fmt_t(o["backup"]["arrive"])))
            out.append("    check: drive %s\n           transit %s" % (o["check_drive"], o["check_transit"]))
            out.append("")

    if by:
        block("BEST TRADE-OFFS (arrive by %s with %d min spare; no other hub is both cheaper and lets you leave later)"
              % (fmt_t(res["deadline"]), res["safety_min"]), res["pareto"])
        block("OTHER HUBS (latest departures, one per vehicle)", res["others"])
    else:
        block("BEST TRADE-OFFS (no other hub is both cheaper and earlier)", res["pareto"])
        block("OTHER HUBS (next fastest, one per vehicle)", res["others"])
    hid = res.get("hidden_hubs") or {}
    if hid.get("same_vehicle") or hid.get("past_top"):
        bits = ([("%d past --top %d" % (hid["past_top"], hid.get("top", 0)))] if hid.get("past_top") else []) + (
            ["%d on the same first or last vehicle as a listed hub" % hid["same_vehicle"]] if hid.get("same_vehicle")
            else [])
        out.append("NOT SHOWN  %d more hubs that beat the baselines: %s (--top N or --all lists them)."
                   % (hid.get("past_top", 0) + hid.get("same_vehicle", 0), ", ".join(bits)))
        out.append("")
    fin = res.get("finish_with_ride") or []
    if fin:
        info = res.get("finish_info") or {}
        area = info.get("area") or {}
        out.append("FINISH WITH A RIDE (transit to a stop up to %s km out, then a ride; cheapest and fastest mixes)"
                   % info.get("radius_km", "?"))
        if area:
            taxis = "; ".join("%s %s" % (t.get("name", ""), t.get("phone", "")) for t in area.get("taxis") or [])
            out.append("    near %s: %s%s%s" % (area.get("name", "the destination"), area.get("service"),
                                              (". Taxis: " + taxis) if taxis else "",
                                              (". " + area["note"]) if area.get("note") else ""))
        for i, o in enumerate(fin, 1):
            fr = o["final_ride"]
            if car and o["ride_source"] != "walk":
                first = ("drive %.1f km / %.0f min to %s (parking: %s), car ~$%.2f%s, leave by %s"
                         % (o["ride_km"], o["ride_min"], o["hub"], (o.get("parking") or {}).get("text", "?"),
                            o["car_cost"], unknown_parking(o.get("parking")), fmt_t(o["book_by"])))
            else:
                first = ("walk to the first stop" if o["ride_source"] == "walk" else
                         "ride %.1f km / %.0f min to %s ~%.0f %s, book by %s"
                         % (o["ride_km"], o["ride_min"], o["hub"], o["ride_cost"], cur, fmt_t(o["book_by"])))
            out.append("%2d. %s, board %s, ARRIVE %s" % (i, first, fmt_t(o["board"]), fmt_t(o["arrive"])))
            when = fmt_t(fr["book_at"]) if fr.get("book_at") is not None else "arrival"
            out.append("    final ride %.1f km / %.0f min (%s) from %s ~%.0f %s by %s, %s %s"
                       % (fr["km"], fr["min"], fr["source"], fr["from"], fr["cost"], cur, fr["service"],
                          "pre-book it for" if fr.get("prebook") else "request it at", when))
            txt = fare_text(dict(o, ride_cost=o["total_ride_cost"]), cur, has_region, rules_on)
            if car:
                out.append("    car and final ride ~$%.2f in total; %s"
                           % (o["total_ride_cost"], fare_text(dict(o, ride_cost=None), cur, has_region, rules_on)))
            else:
                out.append("    rides ~%.0f %s in total; %s" % (o["total_ride_cost"], cur, txt))
            out.append("        " + legs_text(o["legs"]))
            if o["backup"] and not by:
                out.append("    backup if missed: %s at %s, arrive %s"
                           % (o["backup"]["route"], fmt_t(o["backup"]["board"]), fmt_t(o["backup"]["arrive"])))
            elif o["backup"]:
                out.append("    safer earlier option: book by %s, %s at %s, arrive %s"
                           % (fmt_t(o["backup"]["book_by"]), o["backup"]["route"], fmt_t(o["backup"]["board"]),
                              fmt_t(o["backup"]["arrive"])))
            out.append("    check: final drive %s%s" % (fr["check_drive"], ("\n           final ride quote %s" % fr["uber"])
                                                         if fr.get("uber") else ""))
            out.append("")
    if res.get("all_options"):
        out.append("ALL HUBS WITH SERVICE")
        for o in res["all_options"]:
            if car:
                out.append("  %s  leave by %s  arrive %s  drive %.1f km, car ~$%.2f%s (%s)  parking: %s  first: %s"
                           % (o["hub"], fmt_t(o["book_by"]), fmt_t(o["arrive"]), o["ride_km"], o["car_cost"],
                              unknown_parking(o.get("parking")), o["ride_source"], (o.get("parking") or {}).get("text", "?"),
                              leg_name(o["legs"][0]) if o["legs"] else "-"))
                continue
            out.append("  %s  book by %s  arrive %s  ride %.1f km ~%.0f %s (%s)  first: %s"
                       % (o["hub"], fmt_t(o["book_by"]), fmt_t(o["arrive"]), o["ride_km"], o["ride_cost"], cur,
                          o["ride_source"], leg_name(o["legs"][0]) if o["legs"] else "-"))
        out.append("")
    out += second_opinion_lines(res.get("crosscheck"))
    for w in res["warnings"]:
        out.append("WARNING " + w)
    if car:
        out.append("NOTE driving times and car costs are estimates. The car stays at the hub: plan the trip back to it. "
                   "Verify the chosen hub's departure with the operator and check the parking signs at the hub.")
    else:
        out.append("NOTE ride costs and driving times are estimates. Verify the chosen hub's departure with the operator "
                   "and get a real quote in the ride app before booking.")
    # names and text from feeds, maps and APIs are printed as data: no control characters reach the terminal
    return "\n".join(safe_text(ln) for chunk in out for ln in str(chunk).split("\n"))


# --------------------------------------------------------------------------- discover command

def discover(args):
    warnings = []
    o_lat, o_lon, _ = resolve_place(args.origin, "origin", warnings, strict=False)
    radius = (args.radius_km or 25) * 1000
    box = box_around(o_lat, o_lon, radius)
    if args.dest:
        d_lat, d_lon, _ = resolve_place(args.dest, "destination", warnings, strict=False)
        box = box_union(box, box_around(d_lat, d_lon, 5000))
    infos = [reverse_place_info(o_lat, o_lon, warnings)]
    if args.dest:
        infos.append(reverse_place_info(d_lat, d_lon, warnings))
    places = set().union(*(i["names"] for i in infos))
    countries = {i["country"] for i in infos if i["country"]}
    states = {i["state"] for i in infos if i["state"]}
    found, too_big, far, dups = discover_feeds(box, places=places)
    too_big.sort(key=lambda h: (not h["official"], h.get("country") not in countries,
                                h.get("subdivision") not in states, h.get("center_km", 0), h["area"]))
    if args.json:
        print(json.dumps({"feeds": found, "too_big": too_big, "far": far, "duplicates": dups,
                          "warnings": warnings}, indent=2))
        return

    def say(text):   # catalog text is data: no control characters reach the terminal
        print("\n".join(safe_text(ln) for ln in str(text).split("\n")))
    for w in warnings:
        say("WARNING " + w)
    if not found:
        say("No local GTFS feeds in the MobilityDatabase catalog cover this area%s."
            % (" (see the large feeds below)" if too_big else ""))
    for f in found:
        say("%-10s %s | %s | %s%s%s\n           %s" % (
            f["id"], f["provider"], f["name"] or "", f["municipality"] or "",
            "  [source needs API key; mirror is keyless]" if f["needs_key"] else "",
            "  [no location data; matched by town name]" if f.get("no_bbox") else "", "\n           ".join(f["urls"])))
    if too_big:
        say("\nLarge (national/intercity, or bad catalog bounding box) feeds covering this area, not loaded "
            "automatically unless nothing local covers the origin:")
        for f in too_big:
            say("  %-10s %s%s  %s" % (f["id"], f["provider"], " [official]" if f["official"] else "", f["urls"][0]))
    if far:
        say("\nIgnored, bounding box centred far away (likely bad catalog data): %s"
            % ", ".join(sorted({"%s (%d km)" % (f["provider"], f["center_km"]) for f in far})))
    if dups:
        say("\nDuplicate catalog listings dropped: %s" % ", ".join(sorted({f["id"] for f in dups})))
    say("\nUse with: --feed <id>=<url> (repeat per feed). Feeds in the first list load automatically when no "
        "region file covers the trip.")


def print_timetable(regions, fid):
    """Hand-entered timetables (operators with no public GTFS) as text, for planning without network."""
    feeds = [(r["id"], f) for r in regions for f in r.get("feeds", []) if f.get("manual")]
    if fid == "list":
        for rid, f in feeds:
            print("%-28s %s  [%s]" % (f["id"], f.get("name", ""), rid))
        print("\nShow one with --timetable ID.")
        return
    match = [f for _rid, f in feeds if f["id"] == fid]
    if not match:
        raise SystemExit("no hand-entered timetable %r; --timetable list shows them" % fid)
    f = match[0]
    m = f["manual"]
    print("%s (%s)%s" % (f.get("name", fid), m.get("agency", ""), "  phone " + m["phone"] if m.get("phone") else ""))
    if f.get("notes"):
        print("NOTES  " + f["notes"])
    if f.get("board_buffer_min"):
        print("BOARD  be at the stop %s min before departure" % f["board_buffer_min"])
    for sid, sv in sorted(m["services"].items()):
        days = "".join(d for d, on in zip(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"], sv.get("days", "1111111"))
                       if on == "1")
        print("SERVICE %s: %s, %s to %s%s%s" % (sid, days, sv["start"], sv["end"],
                                               "; not on " + ", ".join(sv["except"]) if sv.get("except") else "",
                                               "; also " + ", ".join(sv["extra"]) if sv.get("extra") else ""))
    for r in m["routes"]:
        print("\nROUTE %s %s" % (r.get("route", ""), r.get("name", "")))
        for t in r["trips"]:
            print("  [%s] to %s: %s" % (t["service"], t.get("headsign", ""),
                                       " > ".join("%s %s" % (st[3] if len(st) < 5 else "%s/%s" % (st[3], st[4]), st[0])
                                                  for st in t["stops"])))
    if f.get("source"):
        print("\nSOURCE  " + (", ".join(f["source"]) if isinstance(f["source"], list) else str(f["source"])))


# --------------------------------------------------------------------------- main

def build_parser():
    """The command-line parser (drive_plan.py builds park-and-ride runs with it)."""
    ap = argparse.ArgumentParser(description="Find every transit hub worth taking a ride to (GTFS based).")
    ap.add_argument("--origin", help="'lat,lon' or an address")
    ap.add_argument("--dest", help="'lat,lon' or an address")
    ap.add_argument("--depart", default="now", help="'YYYY-MM-DD HH:MM', 'HH:MM' (next occurrence) or 'now' "
                    "(local time at the trip)")
    ap.add_argument("--arrive-by", help="plan backwards from a deadline: 'YYYY-MM-DD HH:MM' or 'HH:MM' (next "
                    "occurrence). Finds the latest you can book the ride at each hub")
    ap.add_argument("--region", default="auto", help="region file id in references/regions, 'auto' or 'none'")
    ap.add_argument("--feed", action="append", help="extra GTFS feed: id=URL, a URL or a local zip path (repeatable)")
    ap.add_argument("--no-discover", action="store_true", help="do not auto-discover feeds when no region file fits")
    ap.add_argument("--radius-km", type=float, help="max ride distance to a hub (default 70%% of the trip, at most "
                    "40 km; rail stations count up to 1.3 times that)")
    ap.add_argument("--dest-walk-m", type=int, default=1500, help="max walk from last stop to destination")
    ap.add_argument("--origin-walk-m", type=int, default=1200, help="max walk to a stop for the transit-only baseline")
    ap.add_argument("--footpath-m", type=int, default=400, help="max walk between stops when transferring")
    ap.add_argument("--min-change-min", type=float, default=2.0, help="min time for any transfer (same stop or a "
                    "short walk)")
    ap.add_argument("--pickup-wait-min", type=float, help="minutes from booking to pickup (default from region, else 6)")
    ap.add_argument("--safety-min", type=float, help="spare minutes planned at the hub, at the first "
                    "stop of transit only, and before an arrive-by deadline; tighter connections show as STRETCH "
                    "(default 3; 5 with --access car, as drive_plan.py)")
    ap.add_argument("--min-gain-min", type=float, default=10.0, help="hide ride options that gain less than this "
                    "over transit only")
    ap.add_argument("--payment", choices=["card", "contactless", "cash"], default="card",
                    help="fare basis: card (a transit fare card such as PRESTO, or one in a phone wallet), "
                         "contactless (a bank credit/debit tap; some agencies charge it the cash fare) or cash")
    ap.add_argument("--max-hours", type=float, help="search window length (default 4 h; longer trips get a window "
                    "sized to the drive, up to %g h)" % MAX_WINDOW_H)
    ap.add_argument("--final-ride", choices=["auto", "on", "off"], default="auto", help="also plan transit to a stop "
                    "short of the destination plus a ride for the rest (auto: trips of %d km or more, or when no "
                    "stop is near the destination)" % (FINAL_RIDE_MIN_M / 1000))
    ap.add_argument("--routing", choices=["auto", "osrm", "estimate"], default="auto")
    ap.add_argument("--osrm-url", default="https://router.project-osrm.org")
    ap.add_argument("--traffic-factor", type=float, help="multiplier on OSRM free-flow times (default by time of day: "
                    "typical traffic for a ride, the planning allowance for --access car)")
    ap.add_argument("--min-saving", type=float, help="hide hubs whose ride saves less than this vs riding all the "
                    "way (default 20%% of the full ride; 0 shows all)")
    ap.add_argument("--crosscheck", action="store_true", help="also ask the Transitous router (transit only "
                    "from the origin, and from each of the top hubs) as a second opinion")
    ap.add_argument("--top", type=int, default=8, help="how many options to print in total")
    ap.add_argument("--all", action="store_true", help="also list every hub with service")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--refresh", action="store_true", help="re-download feeds even if cached")
    ap.add_argument("--max-age-hours", type=float, default=72.0, help="re-download cached feeds older than this")
    ap.add_argument("--discover", action="store_true", help="list GTFS feeds covering the trip area, then exit")
    ap.add_argument("--allow-street-match", action="store_true", help="plan even when an address with a house "
                    "number only matched the street (the point can be kilometres off)")
    ap.add_argument("--check-network", action="store_true", help="report which needed domains this environment "
                    "can reach, then exit")
    ap.add_argument("--timetable", metavar="FEED_ID", help="print a hand-entered timetable from the region files "
                    "('list' lists them), then exit; works offline")
    ap.add_argument("--access", choices=["ride", "car"], default="ride", help="how you reach the hub: ride (Uber, "
                    "Lyft, taxi) or car (drive your own car and park; only hubs with parking count)")
    ap.add_argument("--car-cost-per-km", type=float, default=0.17, help="car mode: running cost per km (default "
                    "0.17: fuel about 0.14 + wear 0.03)")
    ap.add_argument("--park-min", type=float, default=4.0, help="car mode: minutes to park and walk to the platform")
    ap.add_argument("--stay-hours", type=float, default=4.0, help="car mode: hours the car stays at the hub, to price "
                    "paid hub parking")
    return ap


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.timetable:
        print_timetable(load_regions(), args.timetable)
        return

    if args.check_network:
        regions = load_regions()
        if args.origin and args.dest:
            hosts, note = trip_domains(args, regions)
            if not (LATLON_RE.match(args.origin) and LATLON_RE.match(args.dest)):
                try:   # geocode when possible, so the feed hosts for this trip can be listed too
                    o = resolve_place(args.origin, "origin", [], strict=False)
                    d = resolve_place(args.dest, "destination", [], strict=False)
                    coords = argparse.Namespace(**dict(vars(args), origin="%f,%f" % o[:2], dest="%f,%f" % d[:2]))
                    hosts = hosts[:2] + [h for h in trip_domains(coords, regions)[0] if h not in hosts[:2]]
                    note = ""
                except SystemExit:
                    pass
            if note:
                print(note)
        else:
            hosts = needed_domains(regions)
        status = check_network(hosts)
        for h in hosts:
            print("%-50s %s" % (h, status[h]))
        blocked = [h for h in hosts if status[h] != "ok"]
        print("\n%d of %d reachable.%s" % (len(hosts) - len(blocked), len(hosts),
                                            " Allow the blocked ones to run the live planner." if blocked else ""))
        return 1 if blocked else 0
    if not args.origin:
        ap.error("--origin is required")
    if args.discover:
        discover(args)
        return 0
    if not args.dest:
        ap.error("--dest is required unless --discover")
    res = plan(args)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
