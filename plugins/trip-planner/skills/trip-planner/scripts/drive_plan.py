#!/usr/bin/env python3
"""Driving planner: routes with time-of-day traffic, 407 ETR tolls versus time, fuel or charging, parking at
the destination, arrival planning, park and ride, incidents and map links for the hand-off to navigation.

Standard library only. Python 3.8+. Run: python3 drive_plan.py --origin "lat,lon" --dest "lat,lon" --depart now
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import importlib
import io
import json
import math
import re
import sys
import threading
import os
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:   # `python -I` leaves the script's folder off sys.path
    sys.path.insert(0, _HERE)

import drive_common as dc  # noqa: E402

th = dc.th
log = th.log

DEFAULT_TZ = "America/Toronto"
DIRS = {"E": "eastbound", "W": "westbound"}
LABELS = {"fastest": "Fastest", "toll-free": "Toll-free", "without 407": "Without 407", "alternative": "Alternative"}
ENGINES = {"tomtom": "TomTom", "valhalla": "Valhalla", "osrm": "OSRM"}
OSM_CREDIT = "(c) OpenStreetMap contributors, ODbL"   # the free routing engines and geocoders run on OSM data
# Open-Meteo's API data are CC BY 4.0 and its licence page asks for a credit linking to Open-Meteo.com
# (https://open-meteo.com/en/licence, read 2026-10-20)
WEATHER_SOURCE = ("weather: Open-Meteo forecast, weather data by Open-Meteo.com "
                  "(CC BY 4.0, https://open-meteo.com/en/licence)")
CONF_ORDER = {"official": 0, "curated": 1, "osm": 2, "stale": 3}
EV_FLAGS = ("ev_model", "battery_kwh", "soc", "max_dc_kw", "nacs_adapter", "kwh_per_100km")
ROUTING_HOSTS = ["valhalla1.openstreetmap.de", "router.project-osrm.org", "routing.openstreetmap.de"]   # defaults
DRIVE_HOSTS = ["nominatim.openstreetmap.org", "photon.komoot.io"] + ROUTING_HOSTS + [
    "www.ontario.ca", "prod-energy-fuel-prices.s3.amazonaws.com", "api.open-meteo.com", "overpass-api.de",
    "developer.nlr.gov", "secure.toronto.ca", "ckan0.cf.opendata.inter.prod-toronto.ca",
    "open.canada.ca", "opencanada.blob.core.windows.net"]   # last two: NRCan car ratings (years not bundled)
GEOCODE_HOSTS = ["nominatim.openstreetmap.org", "photon.komoot.io"]


def routing_hosts():
    """The routing hosts in use: TRIP_PLANNER_VALHALLA_URL and TRIP_PLANNER_OSRM_URL when set, else ROUTING_HOSTS."""
    return th.routing_hosts()


def drive_hosts():
    """Hosts the driving planner calls: the core list (with the configured routers in place of the default ones),
    then the city parking layers (each used only near its city)."""
    hosts = []
    for h in DRIVE_HOSTS:
        for x in (routing_hosts() if h in ROUTING_HOSTS else [h]):
            if x not in hosts:
                hosts.append(x)
    try:
        for layer in dc.load_data("parking").get("city_layers") or []:
            h = urllib.parse.urlsplit(layer.get("url", "")).netloc
            if h and h not in hosts:
                hosts.append(h)
    except (OSError, ValueError):
        pass
    return hosts


def blocked_note(warnings):
    """NETWORK BLOCKED text when the routing hosts cannot be reached at all (a sandbox), TLS FAILED text when this
    Python could not verify their certificates, else None."""
    hosts = routing_hosts()
    status = th.check_network(hosts, timeout=6)
    blocked = [h for h in hosts if status[h] != "ok"]
    if len(blocked) < len(hosts):
        return None
    tls = [h for h in blocked if status[h].startswith("tls failed")]
    if tls and len(tls) == len(blocked):   # certificates, not the allow-list
        return th.tls_failed_message(tls) + (" Meanwhile the parking above comes from the curated files and the "
                                             "post-2019 Green P carparks in parking.json only.")
    note = ("NETWORK BLOCKED: this environment cannot reach the routing hosts (%s), so there are no driving times. "
            "This is not an address problem. Allow these domains in this environment's network settings (on "
            "claude.ai, the code execution network access setting): %s. Meanwhile follow the manual method in "
            "references/driving.md (Google Maps for times, the 407 zone table in references/driving/tolls.json, "
            "the curated parking files); the parking above comes from those files and the post-2019 Green P "
            "carparks in parking.json only."
            % (", ".join(blocked), ", ".join(drive_hosts())))
    if tls:   # one host failed its certificate check, another is blocked: both need fixing
        note += " The certificate check also failed for %s: %s" % (", ".join(tls), th.TLS_HELP)
    return note


def geocode_blocked_message():
    """NETWORK BLOCKED text for a drive whose places could not be geocoded (both geocoders unreachable)."""
    return ("NETWORK BLOCKED: this environment cannot reach the geocoders (%s), so the places could not be found "
            "and no route or parking was planned. This is not an address problem. Pass the origin and the "
            "destination as 'lat,lon' to still get the curated parking for the stay. Allow these domains in this "
            "environment's network settings (on claude.ai, the code execution network access setting): %s. "
            "Meanwhile follow the manual method in references/driving.md (Google Maps for times, the 407 zone "
            "table in references/driving/tolls.json, the curated parking files)."
            % (", ".join(GEOCODE_HOSTS), ", ".join(drive_hosts())))


def resolve_drive_place(text, label, warnings, strict):
    """th.resolve_place, with a driving-specific message when the geocoders are blocked (the transit planner's
    message points to timetables, which a drive has no use for)."""
    try:
        return th.resolve_place(text, label, warnings, strict)
    except SystemExit as e:
        if isinstance(e.code, str) and e.code.startswith("NETWORK BLOCKED"):
            raise SystemExit(geocode_blocked_message())
        raise


def costs():
    return dc.load_data("costs")


def home_rate_note(day):
    """A warning once the trip day is past the OEB rate period the home electricity estimate is from, else None."""
    vt = costs()["electricity"].get("valid_to")
    if vt and day > dt.date(*map(int, vt.split("-"))):
        return ("home electricity rate is the OEB estimate in effect to %s; check oeb.ca for the current rate" % vt)
    return None


def _mod(name):
    """Sibling module written by another part of the planner, or None when it is missing or broken."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _iso(x):
    if isinstance(x, dt.datetime):   # minute precision, like the text output (which truncates to HH:MM)
        return x.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    if isinstance(x, dt.date):
        return x.isoformat()
    if isinstance(x, (set, frozenset, tuple)):
        return sorted(x) if isinstance(x, (set, frozenset)) else list(x)
    return str(x)


def holiday_name(day, regions):
    for r in regions:
        for h in r.get("holidays") or []:
            if h.get("date") == day.isoformat() and h.get("stat", True):
                return re.sub(r"\s*\(.*?\)\s*", "", h.get("name", "")).strip() or "holiday"
    return None


def floor_min(when):
    return when.replace(second=0, microsecond=0)


def _when(flag, text, tzname, region, warnings):
    """--depart / --arrive-by / --leave-at text -> naive local datetime, or a one-line error."""
    try:
        return dc.local_dt(text, tzname, region, warnings)
    except ValueError:
        raise SystemExit("%s %r: use 'YYYY-MM-DD HH:MM', 'HH:MM' or 'now' (local time)" % (flag, text))


# --------------------------------------------------------------------------- place labels

_NUM_RE = re.compile(r"^\d+[A-Za-z]?$")
ROAD_ABBR = {"Avenue": "Ave", "Street": "St", "Road": "Rd", "Drive": "Dr", "Boulevard": "Blvd",
             "Crescent": "Cres", "Court": "Crt", "Place": "Pl"}
DIR_ABBR = {"East": "E", "West": "W", "North": "N", "South": "S"}


def abbr_road(road):
    """'Santa Maria Boulevard' -> 'Santa Maria Blvd', 'King Street West' -> 'King St W' (street-type word only)."""
    w = (road or "").split()
    if not w:
        return road or ""
    tail = []
    if len(w) > 2 and w[-1] in DIR_ABBR:
        tail = [DIR_ABBR[w[-1]]]
        w = w[:-1]
    if len(w) > 1 and w[-1] in ROAD_ABBR:
        w[-1] = ROAD_ABBR[w[-1]]
    return " ".join(w + tail)


def short_label(text):
    """Geocoder display name -> first two parts; a house number joins its street ('CN Tower, 290 Bremner
    Boulevard', '805 Santa Maria Boulevard, Coates'). 'lat,lon' text is returned unchanged."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if len(parts) == 2 and th.LATLON_RE.match(text):
        return text.strip()
    if len(parts) > 2 and _NUM_RE.match(parts[0]):
        return "%s %s, %s" % (parts[0], parts[1], parts[2])
    if len(parts) > 2 and _NUM_RE.match(parts[1]):
        return "%s, %s %s" % (parts[0], parts[1], parts[2])
    return ", ".join(parts[:2]) if parts else text


def _province_from_text(text):
    t = (text or "").lower()
    if ", ontario" in t:
        return "ontario"
    for name in ("quebec", "québec", "manitoba", "new york", "michigan", "ohio", "pennsylvania", "minnesota",
                 "vermont"):
        if ", " + name in t:
            return name
    if "united states" in t:
        return "us"
    return None


def _country_from_text(text):
    """'CA' or 'US' from a geocoder display name ending in the country, else None."""
    t = (text or "").strip().lower()
    if t.endswith(", canada"):
        return "CA"
    if t.endswith(", united states") or t.endswith(", united states of america"):
        return "US"
    return None


def reverse_address(lat, lon):
    """One Nominatim /reverse call (cached 30 days, 1 request per second): {"label", "country", "state"} or {}."""
    g = costs().get("geocode") or {}
    base = g.get("reverse_url", "https://nominatim.openstreetmap.org/reverse")
    q = {"format": "jsonv2", "zoom": "16", "addressdetails": "1"}   # 16: street level, enough for a label
    # 3 decimals (about 100 m), like trip_hubs' reverse lookups, so a home pin never asks for its house number;
    # the cache key is the same request
    url = base + "?" + urllib.parse.urlencode(dict(q, lat="%.3f" % lat, lon="%.3f" % lon))
    key = url
    try:
        data = dc.fetch_json(url, timeout=20, max_age_h=g.get("cache_h", 720), min_interval=g.get("min_interval_s", 1.1),
                             cache_key=key)
    except Exception:  # noqa: BLE001  (labels are cosmetic: fall back to the coordinates silently)
        return {}
    a = (data or {}).get("address") or {}
    town = next((a[k] for k in ("city", "town", "village", "municipality", "hamlet", "suburb") if a.get(k)), "")
    road = abbr_road(a.get("road") or "")
    cat, typ = (data or {}).get("category"), (data or {}).get("type")
    if a.get("house_number") and road:
        head = "%s %s" % (a["house_number"], road)
    elif cat != "highway" and (data or {}).get("name"):
        head = data["name"]
    elif typ in ("footway", "path", "steps", "corridor", "pedestrian", "cycleway", "service", "track"):
        head = ""   # a walkway name ('PATH') says nothing about the place: the town alone
    else:
        head = road
    label = "%s, %s" % (head, town) if head and town and head != town else (head or town or "")
    return {"label": label, "country": (a.get("country_code") or "").upper() or None,
            "state": (a.get("state") or "").lower() or None}


def reverse_country(lat, lon):
    """Country code of a point ('CA', 'US') or None: one Nominatim /reverse call at country level (zoom 3), sent
    with 2-decimal coordinates (about 1 km) since only the country is needed. Cached 30 days."""
    g = costs().get("geocode") or {}
    base = g.get("reverse_url", "https://nominatim.openstreetmap.org/reverse")
    url = base + "?" + urllib.parse.urlencode({"format": "jsonv2", "zoom": "3", "addressdetails": "1",
                                               "lat": "%.2f" % lat, "lon": "%.2f" % lon})
    try:
        data = dc.fetch_json(url, timeout=20, max_age_h=g.get("cache_h", 720), min_interval=g.get("min_interval_s", 1.1))
    except Exception:  # noqa: BLE001  (the country only narrows the parking and charger search)
        return None
    cc = ((data or {}).get("address") or {}).get("country_code") if isinstance(data, dict) else None
    return cc.upper() if cc else None


REGION_COUNTRY = "CA"   # the shipped region files are all Canadian; a region file may set its own "country"


def _home_label(lat, lon):
    """TRIP_HOME='lat,lon' or 'lat,lon;label': within 50 m the label (default 'home') is used, with no lookup."""
    raw = dc.env_key("TRIP_HOME")
    if not raw:
        return None
    pos, _, name = raw.partition(";")
    m = th.LATLON_RE.match(pos)
    if not m:
        return None
    if th.haversine_m(lat, lon, float(m.group(1)), float(m.group(2))) <= 50:
        return name.strip() or "home"
    return None


def place_label(text, lat, lon):
    """(short label, {"country", "state"} or {}) for the DRIVE line. A 'lat,lon' input is reverse geocoded once
    at street level ('Santa Maria Blvd, Milton'); an address keeps the geocoder's name."""
    if not th.LATLON_RE.match(text or ""):
        st = _province_from_text(text)
        return short_label(text), ({"state": st} if st else {})
    home = _home_label(lat, lon)
    if home:
        return home, {}
    rev = reverse_address(lat, lon)
    return rev.get("label") or (text or "").strip(), rev


def province_of(info):
    """'ontario', another province or state name, 'us', or None when unknown."""
    if not info:
        return None
    if info.get("country") and info["country"] != "CA":
        return "us" if info["country"] == "US" else info["country"].lower()
    return info.get("state")


# --------------------------------------------------------------------------- fuel

def _market_name(col):
    return col.split("/")[0].strip()


def nearest_market(lat, lon):
    cfg = costs()["fuel"]
    best = min(cfg["markets"], key=lambda m: th.haversine_m(lat, lon, m["lat"], m["lon"]))
    return best, th.haversine_m(lat, lon, best["lat"], best["lon"]) / 1000.0


FUEL_CSV_MIN_COLUMNS = 19   # parse_fuel_csv falls back to column 18 (Fuel Type) when the header is renamed


def _fuel_csv_check(body):
    """fetch `validate` for the fuel price file: a proxy or login page sent with HTTP 200 is never cached."""
    text = body.decode("utf-8-sig", "replace")
    first = next(csv.reader(io.StringIO(text)), [])
    if text.lstrip().startswith("<") or len(first) < FUEL_CSV_MIN_COLUMNS:
        return "not the Ontario fuel price CSV"
    return None


def parse_fuel_csv(text, column, fuel_type):
    """Latest weekly price (c/L) in `column` for `fuel_type`: (c_per_l, date, column used). Rows with 0.0 in the
    market column fall back to the Southern, then the Ontario average of the same week."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return None
    hdr = [h.strip() for h in rows[0]]
    idx = {h: i for i, h in enumerate(hdr)}
    try:
        ft = idx["Fuel Type"]
    except KeyError:
        ft = 18
    cols = [column] + [c for c in costs()["fuel"]["average_columns"] if c != column]
    latest = None
    for r in rows[1:]:
        if len(r) <= ft or r[ft].strip() != fuel_type:
            continue
        if latest is None or r[0] > latest[0]:
            latest = r
    if latest is None:
        return None
    for c in cols:
        i = idx.get(c)
        if i is None or i >= len(latest):
            continue
        try:
            v = float(latest[i])
        except ValueError:
            continue
        if v > 0:
            return v, latest[0], c
    return None


def _avg_name(col):
    if col.startswith("Northern"):
        return "Northern Ontario average"
    if col.startswith("Southern"):
        return "Southern Ontario average"
    return "Ontario average"


def fuel_price(lat, lon, fuel, warnings, override=None, province=None):
    """$/L at the origin. fuel: regular|premium|diesel. province: from the geocoder ('ontario', 'quebec', 'us',
    ...) or None (then the Ontario bounding box decides). Returns {"per_l", "market", "date", "text", "source"}."""
    cfg = costs()["fuel"]
    if override is not None:
        per_l = override / 100.0 if override >= 20 else override
        return {"per_l": per_l, "market": "your price", "date": None, "fuel": fuel, "source": "--fuel-price",
                "text": "your price"}
    market, km = nearest_market(lat, lon)
    in_ont = province == "ontario" if province else th.in_bbox(lat, lon, cfg["ontario_bbox"])
    local = in_ont and km <= cfg["max_market_km"]
    if local:
        column = market["column"]
    elif in_ont:   # far from every surveyed market: the regional average
        column = cfg["northern_column"] if lat >= cfg["north_from_lat"] else cfg["average_columns"][0]
    else:
        column = cfg["average_columns"][-1]
    fb = cfg["fallback"]
    got = None
    try:
        body = dc.fetch(cfg["csv_url"], timeout=60, max_age_h=cfg["cache_h"], validate=_fuel_csv_check)
        got = parse_fuel_csv(body.decode("utf-8", "replace"), column, cfg["fuel_types"][fuel])
        if got is None:
            warnings.append("the Ontario fuel price file had no %s price; using the %s figure" % (fuel, fb["basis"]))
    except Exception as e:  # noqa: BLE001
        warnings.append("Ontario fuel prices could not be fetched (%s); using the %s figure" % (e, fb["basis"]))
    if got is None:
        c, date, used = fb["c_per_l"][fuel], fb["date"], "Ontario Average"
    else:
        c, date, used = got
    name = _market_name(used) if used == column and local else _avg_name(used)
    if not in_ont:
        warnings.append("the origin is outside Ontario's fuel survey, so fuel is priced at the Ontario average; pass "
                        "--fuel-price for the local price")
    elif local and used != column and got is not None:
        warnings.append("no %s price for %s that week; using the %s" % (fuel, market["name"], name))
    return {"per_l": c / 100.0, "market": name, "date": date, "fuel": fuel, "fallback": got is None,
            "source": cfg["csv_url"] if got is not None else "costs.json fallback",
            "text": "%s %s, week of %s" % (name, fuel, date) if date else name}


def cold_factor(temp_c):
    if temp_c is None:
        return 1.0
    for c in costs()["consumption"]["cold"]:   # coldest threshold first
        if temp_c < c["below_c"]:
            return c["factor"]
    return 1.0


def consumption(vehicle, car_class, hwy_share, temp_c, override=None, figures=None):
    """L/100 km for the trip: (value, basis). The car's city and highway ratings (`figures`, from --car or the
    saved car) or the class medians, blended by highway share, times the cold factor."""
    cfg = costs()["consumption"]
    f = cold_factor(temp_c)
    if override:
        base, basis = float(override), "your figure"
    else:
        h = max(0.0, min(1.0, hwy_share or 0.0))
        if figures:
            c = figures
            what = "%s rating" % c.get("label", "car")
        else:
            c = cfg["hybrid"] if vehicle == "hybrid" else cfg["classes"][car_class or cfg["default_class"]]
            what = "%s median" % c["label"]
        base = c["city"] * (1 - h) + c["hwy"] * h
        basis = "%s, %d%% highway" % (what, round(h * 100))
    if f != 1.0:
        basis += ", x%.2f cold" % f
    return base * f, basis


def fuel_cost(route, l100, price):
    return route.get("km", 0.0) * l100 / 100.0 * price["per_l"]


# --------------------------------------------------------------------------- the car

KIND_VEHICLE = {"gas": "gas", "hybrid": "hybrid", "phev": "hybrid", "diesel": "diesel", "ev": "ev"}
SAVE_FLAGS = ("l_per_100km", "vehicle", "ev_model", "battery_kwh", "max_dc_kw", "nacs_adapter", "kwh_per_100km")
EV_SAVE_FLAGS = ("ev_model", "battery_kwh", "max_dc_kw", "nacs_adapter", "kwh_per_100km")
NUM_SAVE_FLAGS = ("l_per_100km", "battery_kwh", "max_dc_kw", "kwh_per_100km")
CAR_HINT = 'Pass --car "YEAR MAKE MODEL" for your car\'s figure, or --save-car to keep it'
KIND_WORD = {"gas": "a gas car", "hybrid": "a hybrid", "diesel": "a diesel", "ev": "electric"}


def _vehicles():
    """vehicles.py (the NRCan Fuel Consumption Ratings lookup and the saved car), or None when it is missing."""
    return _mod("vehicles")


def _flag_values(args):
    """The car flags given on this run (what --save-car keeps besides the --car lookup)."""
    out = {}
    for f in SAVE_FLAGS:
        if f == "vehicle" and not getattr(args, "vehicle_given", False):
            continue
        v = getattr(args, f, None)
        if v not in (None, False):
            out[f] = v
    return out


def _saved_parts(p, warnings=None, path=None):
    """Saved profile -> (looked-up car dict or None, saved flags). A value a hand edit or an older version broke
    ('abc' for a figure, a figure without its pair, an unknown vehicle) is dropped with a warning, never a crash."""
    if not isinstance(p, dict):
        return None, {}
    where = path or "your saved car file"
    bad = []
    raw = dict((k, p[k]) for k in SAVE_FLAGS if p.get(k) not in (None, False))
    extra = p.get("flags")
    if isinstance(extra, dict):
        raw.update((k, v) for k, v in extra.items() if k in SAVE_FLAGS and v not in (None, False))
    elif extra is not None:
        bad.append("flags")
    flags = {}
    for k, v in raw.items():
        if k in NUM_SAVE_FLAGS:
            try:
                x = float(v)
            except (TypeError, ValueError):
                x = None
            if x is None or not 0 < x < 1e6:
                bad.append(k)
            else:
                flags[k] = x
        elif k == "vehicle":
            if v in KIND_VEHICLE.values():
                flags[k] = v
            else:
                bad.append(k)
        elif k == "ev_model":
            if isinstance(v, str) and v.strip():
                flags[k] = v.strip()
            else:
                bad.append(k)
        else:
            flags[k] = bool(v)
    lk = None
    if p.get("label") or p.get("city") is not None or p.get("hwy") is not None or p.get("comb") is not None:
        try:
            f = _figs(p)
        except (TypeError, ValueError, KeyError):
            f = None
        if p.get("label") and f and 0 < f["city"] < 1e3 and 0 < f["hwy"] < 1e3:
            lk = p
        else:
            bad.append("rating")
    if warnings is not None:
        for b in bad:
            warnings.append("saved car file %s has an unusable value for %s; ignored it" % (where, b))
    return lk, flags


def _kind_of(lk, flags):
    """gas | hybrid | diesel | ev for a looked-up car and/or saved flags."""
    if lk is not None:
        return KIND_VEHICLE.get(lk.get("kind"), "gas")
    if any(flags.get(f) for f in EV_SAVE_FLAGS):
        return "ev"
    return flags.get("vehicle") or "gas"


def _nrcan_year(text):
    m = re.search(r"MY ?(\d{4})", text or "")
    return m.group(1) if m else ""


def _preset_match(make, model):
    """ev.json preset key for one make and model name, or None. Every word of a preset name or alias (less the
    make) must appear in order, and a bare number right after the match means a different model ('IONIQ 6' is
    not the 'ioniq' alias)."""
    try:
        d = dc.load_data("ev")
    except (OSError, ValueError):
        return None
    toks = re.findall(r"[a-z0-9]+", ("%s %s" % (make or "", model or "")).lower().replace("e+", "plus"))
    mk = set(re.findall(r"[a-z0-9]+", (make or "").lower())) | {"chevy"}
    best, score = None, 0
    for alias, key in [(k, k) for k in d.get("cars", {}) if k != "generic"] + list(d.get("aliases", {}).items()):
        words = [w for w in re.findall(r"[a-z0-9]+", alias.lower().replace("e+", "plus")) if w not in mk]
        i, ok, last = 0, True, -1
        for w in words:
            while i < len(toks) and toks[i] != w:
                i += 1
            if i >= len(toks):
                ok = False
                break
            last, i = i, i + 1
        if not ok or not words:
            continue
        nxt = toks[last + 1] if last + 1 < len(toks) else ""
        if nxt.isdigit() and len(nxt) <= 2:
            continue
        s = sum(len(w) for w in words)
        if s > score:
            best, score = key, s
    return best


LEAF_NACS_FROM = 2026   # the 2026 LEAF has a NACS port; the 2018 to 2025 LEAFs charge on CHAdeMO
# The 40 kWh 2018 to 2025 LEAF: its own preset when ev.json has one, else the LEAF PLUS preset (the same CHAdeMO
# port and about the same charging speed) with a 39 kWh usable battery
LEAF_40 = ("leaf-40", "leaf-plus", 39.0)
# 2011 to 2017 LEAFs use that preset with their own pack: 24 kWh (about 21 usable) to 2015 and in some 2016s,
# 30 kWh (about 27 usable) in the other 2016s and every 2017
LEAF_OLD_KWH = {24: 21.0, 30: 27.0}
_PLUS_RE = re.compile(r"\bplus\b|\be\+", re.I)


def _ev_presets():
    try:
        return dc.load_data("ev").get("cars") or {}
    except (OSError, ValueError):
        return {}


def _leaf_trim(car, key):
    """'leaf' (2026 on, NACS), 'leaf-plus' (62 kWh) or LEAF_40's key (40 kWh) for a LEAF by its model year and
    trim; any other key, or a LEAF without a year, unchanged. PLUS (or e+) in the name, or in every NRCan variant,
    means the 62 kWh car."""
    if key not in ("leaf", "leaf-plus"):
        return key
    try:
        year = int(car.get("year"))
    except (TypeError, ValueError):
        return key
    if year >= LEAF_NACS_FROM:
        return "leaf"
    names = car.get("variant_names") or []
    if any(_PLUS_RE.search(str(car.get(k) or "")) for k in ("query", "label", "model")) or (
            names and all(_PLUS_RE.search(str(n)) for n in names)):
        return "leaf-plus"
    return LEAF_40[0]


def _preset_wanted(car):
    """The preset key this car should use (it may be missing from ev.json: see ev_preset_for), or None."""
    key = _preset_match(car.get("make"), car.get("model") or car.get("label"))
    if not key and car.get("variant_names"):
        names = car["variant_names"]
        hits = {}
        for n in names:
            k = _preset_match(car.get("make"), n)
            if k:
                hits[k] = hits.get(k, 0) + 1
        if hits:
            k = max(hits, key=lambda x: hits[x])
            key = k if hits[k] * 2 > len(names) else None
    return _leaf_trim(car, key)


def ev_preset_for(car):
    """ev.json preset key matching a looked-up EV ('Tesla' 'Model Y' -> 'model-y'), else the preset most of its
    NRCan variant names match ('Model Y Long Range AWD', ...), else None. A LEAF gets the preset for its model
    year and trim (_leaf_trim); ev_battery_for gives the battery a stand-in preset needs."""
    key = _preset_wanted(car)
    if key == LEAF_40[0] and key not in _ev_presets():
        return LEAF_40[1]
    return key


def _old_leaf_pack(car):
    """24 or 30 (kWh pack) for a 2011 to 2017 LEAF, else None. A 2016 is the 30 kWh car only when its name or
    every NRCan variant says so."""
    try:
        year = int(car.get("year"))
    except (TypeError, ValueError):
        return None
    if year >= 2018:
        return None
    if year >= 2017:
        return 30
    if year <= 2015:
        return 24
    said = " ".join(str(car.get(k) or "") for k in ("query", "label", "model"))
    names = car.get("variant_names") or []
    thirty = re.compile(r"\b30\s*kwh", re.I)
    return 30 if thirty.search(said) or (names and all(thirty.search(str(n)) for n in names)) else 24


def ev_battery_for(car):
    """Usable kWh when the preset's own battery does not fit the car: a 2011 to 2017 LEAF (24 or 30 kWh pack),
    or the 40 kWh LEAF when ev.json has no leaf-40 preset. Else None."""
    key = _preset_wanted(car)
    if key != LEAF_40[0]:
        return None
    pack = _old_leaf_pack(car)
    if pack:
        return LEAF_OLD_KWH[pack]
    return LEAF_40[2] if key not in _ev_presets() else None


TRIM_GAP = 0.10   # a looked-up EV whose NRCan range x rating implies a battery this much smaller gets a scaled one


def _nrcan_energy(c):
    """NRCan range x combined kWh/100 km (about the energy one full charge gives), or None."""
    try:
        return float(c["range_km"]) * float(c["comb"]) / 100.0
    except (KeyError, TypeError, ValueError):
        return None


def _ref_ratio(p):
    """A preset's usable kWh over the NRCan energy of its own reference row, or None (the generic preset)."""
    ref = p.get("nrcan_ref") or {}
    try:
        return float(p["battery_kwh"]) / (float(ref["range_km"]) * float(ref["kwh_100km"]) / 100.0)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def ev_trim_battery(car, key):
    """Usable kWh for a looked-up EV whose NRCan range and rating imply a battery more than TRIM_GAP smaller than
    its preset's (a Standard Range or RWD trim on a Long Range preset; an EV with no preset on the generic one):
    its NRCan energy x the preset's own ratio (for the generic preset, the median ratio of the presets). Never
    larger than the preset's battery. Else None."""
    presets = _ev_presets()
    p = presets.get(key or "generic")
    e = _nrcan_energy(car)
    if not p or not e:
        return None
    ratio = _ref_ratio(p)
    if ratio is None:
        rs = sorted(r for r in (_ref_ratio(x) for x in presets.values()) if r)
        if not rs:
            return None
        mid = len(rs) // 2
        ratio = rs[mid] if len(rs) % 2 else (rs[mid - 1] + rs[mid]) / 2.0
    kwh = round(e * ratio, 1)
    return kwh if kwh < float(p["battery_kwh"]) * (1 - TRIM_GAP) else None


def _figs(c):
    return {"label": c.get("label") or "your car", "city": float(c["city"]), "hwy": float(c["hwy"])} \
        if c.get("city") is not None and c.get("hwy") is not None else (
        {"label": c.get("label") or "your car", "city": float(c["comb"]), "hwy": float(c["comb"])}
        if c.get("comb") is not None else None)


def _base_name(name):
    """A preset name less its notes: 'Tesla Model Y Long Range AWD (your figures: ...)' -> 'Tesla Model Y Long Range
    AWD'."""
    return re.sub(r"\s*\(.*$", "", name or "").strip()


def build_ev(car, args, warnings):
    """The EV profile for the CAR line and for charging: the ev.json preset (or the generic one) with the battery
    and charging overrides, and the NRCan rating of the looked-up or saved EV in place of the preset's figures.
    Sets car["ev"]; None when ev_charging.py is missing."""
    evc = _mod("ev_charging")
    if evc is None:
        return None
    ea = car.get("ev_args") or {}

    def prof(model):
        return evc.car_profile(model, ea.get("battery_kwh"), ea.get("max_dc_kw"), ea.get("kwh_per_100km"),
                               ea.get("nacs_adapter"))
    try:
        e = prof(ea.get("model"))
    except SystemExit:
        if not ea.get("model") or getattr(args, "ev_model", None):
            raise
        warnings.append("your saved EV preset %r is unknown, so charging uses the generic preset" % ea["model"])
        ea["model"] = None
        e = prof(None)
    nr = ea.get("nrcan")
    if nr and not ea.get("kwh_per_100km"):   # the NRCan rating of the looked-up or saved EV
        e["kwh_100km_city"], e["kwh_100km_hwy"] = nr["city"], nr["hwy"]
        e["name"] = ("%s (NRCan rating; battery scaled from its NRCan range, charging from the %s preset)"
                     if car.get("battery_scaled") else
                     "%s (NRCan rating; battery and charging from the %s preset)") % (nr["label"], e["key"])
    car["ev"] = e
    return e


def _shown_path(vm, path):
    """A saved-car path for the output and --json: the home folder as '~' (vehicles.shown_path), so neither
    carries the user's account name."""
    f = getattr(vm, "shown_path", None)
    return f(path) if f is not None and path else path


def forget_car():
    """Delete the saved car: one line saying what happened."""
    vm = _vehicles()
    if vm is None:
        raise SystemExit("vehicles.py is missing next to drive_plan.py, so there is no saved car to forget.")
    path = _shown_path(vm, vm.profile_path())
    return ("forgot your saved car (%s)" % path) if vm.forget_profile() else ("no saved car to forget (%s)" % path)


def resolve_car(args, warnings):
    """Which car this plan uses, by precedence: --l-per-100km > --car > saved car > --car-class / --vehicle >
    the default class (EVs: --ev-model > --car > saved car > generic). Sets args.vehicle; returns the car block."""
    vm = _vehicles()
    car = {"how": None, "label": None, "query": args.car or None, "notes": []}
    if args.forget_car:
        car["notes"].append(forget_car())
        car["forgot"] = True
    explicit = args.vehicle if getattr(args, "vehicle_given", False) else None
    ev_flags = [f for f in EV_FLAGS if getattr(args, f, None) not in (None, False)]

    # ---- --car: NRCan lookup
    looked, failed = None, None
    if args.car:
        lw = []
        if vm is None:
            lw.append("vehicles.py is missing")
        else:
            try:
                looked = vm.lookup(args.car, lw)
            except Exception as e:  # noqa: BLE001
                lw.append("the lookup failed (%s)" % e)
                looked = None
        if looked is None or _figs(looked) is None:
            looked, failed = None, "; ".join(x for x in lw if x) or "no match"
        else:
            warnings.extend(lw)
    if looked is not None:
        lk = _kind_of(looked, {})
        if lk == "ev" and explicit in ("gas", "hybrid", "diesel"):
            raise SystemExit("--vehicle %s does not fit --car %r, which is electric" % (explicit, args.car))
        if lk != "ev" and (ev_flags or explicit == "ev"):
            raise SystemExit("--car %r is %s, but EV options were given (%s)" % (
                args.car, KIND_WORD.get(lk, "not electric"),
                ", ".join("--" + f.replace("_", "-") for f in ev_flags) or "--vehicle ev"))
        if lk == "ev" and args.l_per_100km:
            raise SystemExit("--l-per-100km does not fit --car %r, which is electric; use --kwh-per-100km" % args.car)
        if explicit and explicit != lk:
            warnings.append("--car %s is rated as %s, so --vehicle %s is ignored" % (
                looked.get("label") or args.car, KIND_WORD.get(lk, lk), explicit))
        args.vehicle = lk
        if args.car_class:
            warnings.append("--car-class %s is ignored: --car %s gave the car's own NRCan rating" % (
                args.car_class, looked.get("label") or args.car))
        if args.l_per_100km:
            warnings.append("--l-per-100km %s replaces the NRCan rating of %s" % (
                _fig(args.l_per_100km), looked.get("label") or args.car))

    # ---- the saved car (only when --car gave nothing)
    saved, saved_flags, saved_path, profile = None, {}, None, None
    if looked is None and vm is not None and not args.no_saved_car and not args.forget_car:
        try:
            try:
                p = vm.load_profile(warnings)
            except TypeError:
                p = vm.load_profile()
            saved_path = _shown_path(vm, vm.profile_path()) if p else None
        except Exception as e:  # noqa: BLE001
            p = None
            warnings.append("your saved car could not be read (%s), so it is not used" % e)
        vehicle0 = args.vehicle
        try:
            pw = []
            s_lk, s_flags = _saved_parts(p, pw, saved_path) if p else (None, {})
            if p and s_lk is None and not s_flags:
                bad = ", ".join(x.split("unusable value for ")[-1].split(";")[0] for x in pw)
                warnings.append("saved car file %s holds no usable car%s; ignored it. Save the car again to "
                                "replace it" % (saved_path, " (bad %s)" % bad if bad else ""))
            elif p:
                warnings.extend(pw)
                sk = _kind_of(s_lk, s_flags)
                # --l-per-100km means a fuel car: any but a saved EV fits it
                want = "ev" if ev_flags else explicit
                name = (s_lk or {}).get("label") or s_flags.get("ev_model") or "your saved car"
                if args.ev_model:
                    pass   # an explicit preset wins over the saved EV and its saved figures
                elif (want and want != sk) or (not want and args.l_per_100km and sk == "ev"):
                    warnings.append("your saved car (%s) is %s, so it is not used for this %s run (--forget-car "
                                    "or --save-car to change it)" % (name, KIND_WORD.get(sk, sk), want or "gas"))
                else:
                    saved, saved_flags, profile = (s_lk or {}), s_flags, p
                    car["profile_path"] = saved_path
                    args.vehicle = sk
                    if args.car_class:
                        warnings.append("--car-class %s is ignored: the plan uses your saved car (%s); pass "
                                        "--no-saved-car to use the class" % (args.car_class, name))
        except (ValueError, KeyError, TypeError) as e:
            saved, saved_flags, profile = None, {}, None
            car.pop("profile_path", None)
            args.vehicle = vehicle0
            warnings.append("your saved car could not be used (%s: %s), so the plan assumes the class default"
                            % (e.__class__.__name__, e))
    using_saved = saved is not None
    src = looked if looked is not None else (saved if saved else None)
    ev = args.vehicle == "ev"
    if src:
        for k in ("label", "year", "make", "model", "kind", "unit", "city", "hwy", "comb", "variants",
                  "range", "picked", "alternatives", "range_km", "source", "source_url", "confidence"):
            if src.get(k) is not None:
                car[k] = src[k]
        # "class" is always a costs.json class key; NRCan's own class name is "nrcan_class"
        if src.get("class") is not None:
            car["nrcan_class"] = src["class"]
        if src.get("car_class") is not None:
            car["class"] = src["car_class"]
    car["vehicle"] = args.vehicle

    if not ev:
        cfg = costs()["consumption"]
        flag_l = args.l_per_100km or (saved_flags.get("l_per_100km") if using_saved else None)
        if src:
            fuel = src.get("fuel") if src.get("fuel") in ("regular", "premium", "diesel") else (
                "diesel" if args.vehicle == "diesel" else "regular")
        else:
            fuel = "diesel" if args.vehicle == "diesel" else "regular"
        car["fuel"] = fuel
        car["unit"] = "L/100 km"
        if args.l_per_100km:
            car["how"], car["l_per_100km"] = "flag", float(args.l_per_100km)
        elif looked is not None:
            car["how"], car["figures"] = "lookup", _figs(looked)
        elif using_saved and flag_l:
            car["how"], car["l_per_100km"] = "saved", float(flag_l)
        elif using_saved and src:
            car["how"], car["figures"] = "saved", _figs(src)
        else:
            car["how"] = "class default"
            if args.vehicle == "hybrid":
                c = cfg["hybrid"]
                car["class"] = "hybrid"
            else:
                car["class"] = args.car_class or cfg["default_class"]
                c = cfg["classes"][car["class"]]
            car.update(class_label=c["label"], city=c["city"], hwy=c["hwy"], comb=c.get("comb"),
                       class_given=bool(args.car_class), source=cfg["source"], source_url=cfg["source_url"])
    else:
        car["fuel"], car["unit"] = "electricity", "kWh/100 km"
        model, stand_in_kwh, trim_kwh, trim_src = args.ev_model, None, None, None
        if not model and looked is not None:
            model, stand_in_kwh, trim_src = ev_preset_for(looked), ev_battery_for(looked), looked
        if not model and using_saved:
            model = saved_flags.get("ev_model")
            if not model and saved:
                model, stand_in_kwh, trim_src = ev_preset_for(saved), ev_battery_for(saved), saved
        if trim_src is not None and not stand_in_kwh:   # the preset was matched, not given: check its battery fits
            trim_kwh = ev_trim_battery(trim_src, model)
        sf = saved_flags if using_saved else {}
        ea = {"model": model,
              "battery_kwh": args.battery_kwh or sf.get("battery_kwh") or stand_in_kwh or trim_kwh,
              "max_dc_kw": args.max_dc_kw or sf.get("max_dc_kw"),
              "kwh_per_100km": args.kwh_per_100km or (sf.get("kwh_per_100km") if not args.ev_model else None),
              "nacs_adapter": bool(args.nacs_adapter or sf.get("nacs_adapter")), "nrcan": None}
        if trim_kwh and ea["battery_kwh"] == trim_kwh:
            pk = model or "generic"
            car["battery_scaled"] = {"kwh": trim_kwh, "preset": pk, "preset_kwh": _ev_presets()[pk]["battery_kwh"]}
            warnings.append("%s: its NRCan range and rating imply about %.0f kWh usable, less than the %s preset's "
                            "%.0f kWh, so charging uses %.0f kWh%s; pass --battery-kwh for your car's exact battery"
                            % (car.get("label") or "your EV", trim_kwh, pk, car["battery_scaled"]["preset_kwh"],
                               trim_kwh, "" if pk == "generic" else
                               " (fast charging stays the preset's; smaller batteries often charge slower, so "
                               "pass --max-dc-kw too if you know it)"))
        if stand_in_kwh and ea["battery_kwh"] == stand_in_kwh:
            pack = _old_leaf_pack(looked or saved or {})
            warnings.append("charging for %s uses the %s preset (the same CHAdeMO port and charging speed) with a "
                            "%g kWh usable battery%s; pass --battery-kwh if yours differs"
                            % (car.get("label") or "your LEAF", model, stand_in_kwh,
                               " (its %d kWh pack; the CHAdeMO fast-charge port was optional on 2011 to 2017 LEAFs, "
                               "so check yours has one)" % pack if pack else ""))
        if args.kwh_per_100km:
            car["how"] = "flag"
        elif ea["kwh_per_100km"] and looked is None:
            car["how"] = "saved"   # the saved kWh/100 km figure, like a saved --l-per-100km
        elif args.ev_model:
            car["how"] = "flag"
            for k in ("city", "hwy", "comb", "figures"):
                car.pop(k, None)
            if looked is None:
                car["label"] = None
        elif looked is not None:
            car["how"], ea["nrcan"] = "lookup", _figs(looked)
        elif using_saved:
            car["how"] = "saved"
            ea["nrcan"] = _figs(saved) if saved else None
        else:
            car["how"] = "class default"
        if ea["nrcan"] and not ea["model"]:
            miss = [f for f in ("battery_kwh", "max_dc_kw") if not ea[f]]
            if miss:
                warnings.append("no EV preset matches %s, so charging uses the generic %s; pass %s for a real "
                                "charging plan" % (car.get("label") or "your car", " and ".join(
                                    {"battery_kwh": "70 kWh battery", "max_dc_kw": "150 kW fast charging"}[f]
                                    for f in miss), " and ".join("--" + f.replace("_", "-") for f in miss)))
                car["generic_ev"] = True
        car["ev_args"] = ea
        build_ev(car, args, warnings)

    if failed is not None:
        nxt = ("your saved car" if car["how"] == "saved" else "your figure" if car["how"] == "flag" else
               "the generic EV" if ev else "the typical hybrid figure" if car.get("class") == "hybrid" else
               "the %s class median" % car.get("class_label", "car"))
        warnings.append("--car %r was not found in the NRCan Fuel Consumption Ratings (%s); using %s instead"
                        % (args.car, failed, nxt))
        car["lookup_failed"] = args.car

    # ---- --save-car
    if args.save_car:
        flags = _flag_values(args)
        if looked is None and not flags:
            raise SystemExit("--save-car: nothing to save%s. Give it with --car \"YEAR MAKE MODEL\" (or "
                             "--l-per-100km, --kwh-per-100km, --vehicle, --ev-model)." % (
                                 " (--car %r was not found: %s)" % (args.car, failed) if failed else ""))
        if vm is None:
            raise SystemExit("vehicles.py is missing next to drive_plan.py, so the car cannot be saved.")
        old = None
        if looked is not None:
            payload = dict(looked)
        elif using_saved:   # an override for the saved car keeps the car: '--battery-kwh 75 --save-car'
            payload = dict((k, v) for k, v in profile.items() if k not in ("saved", "profile_version"))
        else:
            payload = {}
        if not (looked is None and using_saved):
            try:
                old = vm.load_profile([])
            except Exception:  # noqa: BLE001
                old = None
        payload.update(flags)   # overrides sit beside the lookup fields (vehicles.save_profile's layout)
        try:
            car["saved_to"] = _shown_path(vm, vm.save_profile(payload))
        except OSError as e:
            raise SystemExit("--save-car: could not write %s (%s)" % (
                _shown_path(vm, getattr(e, "filename", None) or vm.profile_path()), e.strerror or e))
        if isinstance(old, dict) and old:
            oname = old.get("label") or old.get("ev_model") or (
                "%s L/100 km" % old["l_per_100km"] if old.get("l_per_100km") else "") or old.get("vehicle")
            if oname and oname != payload.get("label"):
                car["notes"].append("replaced your saved car (%s)" % oname)
        car["notes"].append("saved as your car in %s; later runs use it without --car (--forget-car removes it)"
                            % car["saved_to"])
    return car


def _fig(x):
    return "%.1f" % float(x)


def _nrcan_text(c):
    """'NRCan 2022 rating, median of 4 variants'."""
    yr = c.get("year")
    out = "NRCan %s rating" % yr if yr else "NRCan rating"
    if isinstance(c.get("picked"), str) and c["picked"]:
        return "%s, %s" % (out, c["picked"])
    n = c.get("variants")
    n = len(n) if isinstance(n, (list, tuple)) else n
    if isinstance(n, int) and n > 1:
        out += ", median of %d variants" % n
    return out


def _alt_text(c):
    """Second CAR line: what else matched, so the user can pick their exact car."""
    alts = c.get("alternatives") or []
    names = []
    for a in alts[:4]:
        if isinstance(a, dict):
            nm = a.get("label") or " ".join(str(a.get(k)) for k in ("year", "make", "model") if a.get(k))
            if a.get("comb") is not None:
                nm += " (%s comb)" % _fig(a["comb"])
            names.append(nm)
        elif a:
            names.append(str(a))
    if not names:
        return None
    return "also matched: %s; pass the exact name with --car to use one of them" % "; ".join(names)


def car_lines(res):
    """The CAR section: (first line, extra lines)."""
    c = res.get("car") or {}
    how, unit = c.get("how"), c.get("unit") or "L/100 km"
    label = c.get("label")
    saved = " (your saved car)" if how == "saved" else ""
    rest = []
    rough = ""
    if how in ("lookup", "saved") and c.get("confidence") == "low":
        rough = "; rough match, check WARNINGS%s" % (" and the alternatives" if c.get("alternatives") else "")
    if c.get("vehicle") == "ev" or c.get("fuel") == "electricity":
        e = c.get("ev") or {}
        ea = c.get("ev_args") or {}
        bat = ("%.0f kWh battery, %.0f kW max DC" % (e["battery_kwh"], e["max_dc_kw"])
               if e.get("battery_kwh") and e.get("max_dc_kw") else "")
        city_k, hwy_k = e.get("kwh_100km_city"), e.get("kwh_100km_hwy")
        if not hwy_k:   # no profile (ev_charging.py missing): the rating or the figure given
            nr = ea.get("nrcan") or {}
            city_k, hwy_k = (nr.get("city"), nr.get("hwy")) if nr.get("hwy") else (
                ea.get("kwh_per_100km"), ea.get("kwh_per_100km"))
        figs = ("%s city / %s hwy kWh/100 km" % (_fig(city_k or hwy_k), _fig(hwy_k)) if hwy_k else "")
        preset = e.get("key")
        kwh_given = ea.get("kwh_per_100km")
        if how == "class default":
            first = ("not given, so charging assumes a generic EV (%s, %s). Pass --car \"YEAR MAKE MODEL\" or "
                     "--ev-model for your car, or --save-car to keep it" % (bat or "70 kWh battery", figs)
                     if e else "not given, so energy assumes %s kWh/100 km. Pass --car \"YEAR MAKE MODEL\" for your "
                     "car's figure" % _fig(c.get("kwh_100km") or costs()["electricity"]["default_kwh_100km"]["value"]))
        elif how in ("flag", "saved") and kwh_given:
            nm = label or _base_name(e.get("name")) or "your EV"
            tail = ("; %s from the %s preset" % (bat, preset) if bat and preset not in (None, "generic") else
                    ", " + bat if bat else "")
            first = "%s%s: %s kWh/100 km (%s)%s" % (nm, saved, _fig(kwh_given),
                                                    "your saved figure" if how == "saved" else "your figure", tail)
        elif how in ("lookup", "saved") and ea.get("nrcan"):
            scaled = c.get("battery_scaled")
            where = ("battery scaled from its NRCan range, generic charging, pass --max-dc-kw"
                     if scaled and c.get("generic_ev") else
                     "battery scaled from its NRCan range, charging from the %s preset" % preset if scaled else
                     "from the %s preset" % preset if preset and preset != "generic" and not c.get("generic_ev")
                     else "generic, pass --battery-kwh" if c.get("generic_ev") else "your figures")
            first = "%s%s: %s (%s)%s%s" % (label, saved, figs, _nrcan_text(c), "; %s %s" % (bat, where) if bat else "",
                                           rough)
        else:
            nm = _base_name(e.get("name")) or label or "your EV"
            if label and label not in nm:
                nm = "%s (%s)" % (label, nm)
            dflt = c.get("kwh_100km") or costs()["electricity"]["default_kwh_100km"]["value"]
            first = "%s%s: %s%s" % (nm, saved, figs or "%s kWh/100 km (typical EV)" % _fig(dflt),
                                    ", " + bat if bat else "")
    else:
        fuel = c.get("fuel") or "regular"
        if how == "flag" or (how == "saved" and c.get("l_per_100km")):
            who = "your saved figure" if how == "saved" else "your figure"
            first = "%s%s: %s L/100 km (%s), %s" % (label or "your car", saved if label else "",
                                                    _fig(c["l_per_100km"]), who, fuel)
        elif how in ("lookup", "saved"):
            if c.get("city") is not None and c.get("hwy") is not None:
                figs = "%s city / %s hwy L/100 km" % (_fig(c["city"]), _fig(c["hwy"]))
            else:   # a saved car with only its combined figure
                f = c.get("figures") or _figs(c) or {}
                figs = "%s L/100 km combined" % _fig(c.get("comb") if c.get("comb") is not None else f.get("hwy") or 0)
            first = "%s%s: %s, %s (%s)%s" % (label or "your car", saved, figs, fuel, _nrcan_text(c), rough)
        else:
            yr = _nrcan_year(c.get("source"))
            med = "NRCan %s %s: %s city / %s hwy L/100 km" % (
                yr, "median" if c.get("class") != "hybrid" else "typical", _fig(c["city"]), _fig(c["hwy"]))
            what = "a %s" % c.get("class_label") if c.get("class") != "hybrid" else "a typical hybrid"
            dsl = ", priced as diesel" if fuel == "diesel" else ""
            if c.get("class_given"):
                first = "%s, as given (%s)%s. Pass --car \"YEAR MAKE MODEL\" for your car's figure" % (
                    what[2:] if what.startswith("a ") else what, med, dsl)
            else:
                lead = ("%r not found, so" % c["lookup_failed"]) if c.get("lookup_failed") else (
                    "your saved car has no model, so") if c.get("profile_path") else "not given, so"
                first = "%s fuel assumes %s (%s)%s. %s" % (lead, what, med, dsl, CAR_HINT)
    if c.get("lookup_failed") and how != "class default":
        rest.append("--car %r was not found (see WARNINGS)" % c["lookup_failed"])
    alt = _alt_text(c)
    if alt:
        rest.append(alt)
    rest += c.get("notes") or []
    return first, rest


def car_json(car):
    """The car block for --json, without the planner's internal fields."""
    out = {k: v for k, v in car.items() if k not in ("ev_args", "figures") and v is not None}
    if not out.get("notes"):
        out.pop("notes", None)
    return out


# --------------------------------------------------------------------------- weather

def month_normal(lat, lon, month):
    st = min(costs()["climate_normals"]["stations"], key=lambda s: th.haversine_m(lat, lon, s["lat"], s["lon"]))
    return st["monthly_c"][month - 1], st["name"]


def _forecast(points, tzname):
    """One Open-Meteo request for every point: [{"temp", "rain", "snow"}] at each point's own hour."""
    cfg = costs()["weather"]
    days = sorted({p[2].date() for p in points})
    url = cfg["forecast_url"] + "?" + urllib.parse.urlencode({
        "latitude": ",".join("%.4f" % p[0] for p in points), "longitude": ",".join("%.4f" % p[1] for p in points),
        "hourly": "temperature_2m,precipitation,snowfall", "timezone": tzname,
        "start_date": days[0].isoformat(), "end_date": days[-1].isoformat()})
    data = dc.fetch_json(url, timeout=20, max_age_h=1.0, stale_max_h=6.0)
    if isinstance(data, dict):
        data = [data]
    out = []
    for (lat, lon, when), loc in zip(points, data):
        h = loc.get("hourly") or {}
        i = (h.get("time") or []).index(when.strftime("%Y-%m-%dT%H:00"))
        out.append({"temp": h["temperature_2m"][i], "rain": (h.get("precipitation") or [0])[i] or 0.0,
                    "snow": (h.get("snowfall") or [0])[i] or 0.0})
    if len(out) != len(points):
        raise ValueError("forecast reply has %d locations for %d points" % (len(out), len(points)))
    return out


def _classify(s, cfg):
    """(kind, multiplier, detail) for one forecast hour. Snow counts only at or below snow_max_temp_c: wet snow
    above freezing is treated like rain."""
    if s["snow"] >= cfg["snow_cm_h"] and s["temp"] <= cfg["snow_max_temp_c"]:
        return "snow", cfg["snow_factor"], "snow %.1f cm/h" % s["snow"]
    if s["rain"] >= cfg["rain_mm_h"]:
        return "rain", cfg["rain_factor"], "rain %.1f mm/h" % s["rain"]
    if s["rain"] > 0:
        return "light rain", 1.0, "light rain"
    return "dry", 1.0, "dry"


def weather_points(points, tzname, warnings, today=None):
    """Forecast along a drive: points [(lat, lon, naive local when)]. {"mult", "temp_c", "text", "kind", ...}.
    The time multiplier is the mean over the points, and only within apply_days of today; further out the
    forecast is shown but not added."""
    cfg = costs()["weather"]
    first = points[0][2]
    mid = points[len(points) // 2]
    today = today or first.date()
    days = (first.date() - today).days
    out = {"mult": 1.0, "temp_c": None, "text": "", "kind": "clear", "source": None, "forecast": False,
           "days_out": days}
    t, name = month_normal(mid[0], mid[1], first.month)
    if first.date() < today:
        out.update(temp_c=t, source="month normal (%s)" % name, past=True,
                   text="date is in the past, %s normal %d C, planned as clear" % (first.strftime("%B"), round(t)))
        return out
    if days <= cfg["forecast_days"]:
        try:
            samples = _forecast(points, tzname)
        except Exception as e:  # noqa: BLE001
            warnings.append("weather forecast unavailable (%s); planning for clear roads" % e)
            samples = None
        if samples:
            temp = sum(s["temp"] for s in samples) / len(samples)
            kinds = [_classify(s, cfg) for s in samples]
            worst = max(kinds, key=lambda k: k[1])
            out.update(temp_c=round(temp, 1), forecast=True, source="Open-Meteo forecast")
            apply = days <= cfg.get("apply_days", 3)
            if worst[1] > 1.0 and apply:
                mult = sum(k[1] for k in kinds) / len(kinds)
                part = "" if all(k[0] == worst[0] for k in kinds) else " on part of the route"
                out.update(mult=round(mult, 3), kind=worst[0],
                           text="forecast %d C, %s%s (+%d%%)" % (round(temp), worst[2], part, round((mult - 1) * 100)))
                warnings.append("x%.2f for forecast %s applied to the drive time" % (mult, worst[0]))
            elif worst[1] > 1.0:
                out.update(text="forecast %d C, %s possible (not added; forecast is %d days out)"
                                % (round(temp), worst[0], days))
                warnings.append("the forecast %d days out shows %s on the route; the times do not include it. Check "
                                "the forecast again closer to the day" % (days, worst[0]))
            else:
                light = any(k[0] == "light rain" for k in kinds)
                out.update(text="forecast %d C, %s%s" % (round(temp), "light rain, no time added" if light else "dry",
                                                         "" if apply else " (%d days out)" % days))
            return out
    out.update(temp_c=t, source="month normal (%s)" % name,
               text="no forecast yet, %s normal %d C, planned as clear" % (first.strftime("%B"), round(t)))
    return out


def weather_at(lat, lon, when, tzname, warnings, today=None):
    """Forecast for the hour `when` (naive local) at one point."""
    return weather_points([(lat, lon, when)], tzname, warnings, today)


def route_samples(route, o, d, depart):
    """Three points along the route (one for short drives) at the hour each is driven."""
    poly = route.get("poly") or [o, d]
    dur = route.get("typical_s") or route.get("base_s") or 0.0
    fracs = (0.2, 0.5, 0.8) if len(poly) >= 3 and dur >= 1800 else (0.5,)
    out = []
    for f in fracs:
        p = poly[int(round(f * (len(poly) - 1)))]
        # naive addition: across a daylight-saving change the forecast hour is one off, which does not matter here
        out.append((p[0], p[1], depart + dt.timedelta(seconds=dur * f)))
    return out


def weather_manual(kind, temp_c):
    cfg = costs()["weather"]
    mult = {"clear": 1.0, "rain": cfg["rain_factor"], "snow": cfg["snow_factor"]}[kind]
    text = "%s (your setting%s)" % (kind, ", +%d%%" % round((mult - 1) * 100) if mult > 1 else "")
    return {"mult": mult, "temp_c": temp_c, "text": text, "kind": kind, "source": "--weather", "forecast": False}


# --------------------------------------------------------------------------- arrival planning

def mid_point(route, o, d):
    poly = route.get("poly") or []
    return poly[len(poly) // 2] if poly else ((o[0] + d[0]) / 2.0, (o[1] + d[1]) / 2.0)


def plan_backwards(dr, route, deadline, extra_s, opts):
    """Arrive-by: the latest whole-minute departure with depart + plan time + extra_s <= deadline. Two passes,
    then step back while late and forward while it still holds (a traffic band edge moves the plan time).
    Clock sums follow opts["tz"] across a daylight-saving change."""
    tz, reg = opts.get("tz"), opts.get("region")

    def late(dep):
        return dc.elapsed_s(deadline, dc.add_s(dep, (route.get("plan_s") or 0) + extra_s, tz, reg), tz, reg)
    dep = route.get("depart") or deadline
    for _ in range(2):
        dep = floor_min(dc.add_s(deadline, -((route.get("plan_s") or 0) + extra_s), tz, reg))
        dr.retime(route, dep, opts)
    for _ in range(8):
        lt = late(dep)
        if lt <= 0:
            break
        dep = floor_min(dc.add_s(dep, -lt, tz, reg))
        dr.retime(route, dep, opts)
    for _ in range(30):   # early at a band edge: move later one minute at a time while still on time
        cand = dc.add_s(dep, 60, tz, reg)
        dr.retime(route, cand, opts)
        if late(cand) <= 0:
            dep = cand
        else:
            dr.retime(route, dep, opts)
            break
    over = late(dep)
    if over > 0:   # the step back did not settle (a time model that is not first-in first-out): still late
        route["leave_by_late_s"] = over
    else:
        route.pop("leave_by_late_s", None)
    return dep


def route_stops(route, charging, border_min):
    """[(metres along, seconds)] for the border allowance at each listed crossing and each charging stop, for
    drive_route.set_stops (a crossing missing from tolls.json has no position, so its allowance is not placed)."""
    out = [(float(b.get("along_m") or 0.0), border_min * 60.0) for b in route.get("bridges") or []]
    for s in (charging or {}).get("stops") or []:
        out.append((float(s.get("km_along") or 0.0) * 1000.0,
                    (float(s.get("charge_min") or 0.0) + float(s.get("detour_min") or 0.0)) * 60.0))
    return out


# --------------------------------------------------------------------------- money and the trade-off

def route_tolls(route, transponder):
    t = route.get("toll_407_transponder" if transponder else "toll_407_camera") or 0.0
    return t + (route.get("toll_other_cad") or 0.0)


def account_fee():
    """407 ETR's monthly account fee without a transponder (tolls.json account_fee_monthly)."""
    return float(dc.load_data("tolls")["etr407"].get("account_fee_monthly", 5.0))


def fee_text():
    """The account fee as the texts print it: '$5' for whole dollars, else '$5.50'."""
    f = account_fee()
    return "$%d" % f if f == int(f) else dc.money(f)


def trade_off(items, vot, transponder, round_trip=False, fee_included=False, arrive_by=False):
    """items: [{"letter", "route", "money", "time_s", "typical_s"}]. Scored on the whole minutes shown; a tie goes
    to the cheaper route. In arrive-by mode time_s runs from the leave-by time to the door (the allow time), so the
    text says the toll route lets you leave later, and gives the typical-drive difference beside it.
    Returns {"rec", "text", "fee_note", ...}."""
    for it in items:
        it["minutes"] = int(round(it["time_s"] / 60.0))
        it["score"] = it["money"] + it["minutes"] / 60.0 * vot
    rec = min(items, key=lambda it: (round(it["score"], 2), round(it["money"], 2)))
    out = {"rec": rec["letter"], "value_of_time": vot, "scores": {it["letter"]: round(it["score"], 2) for it in items}}
    fast = items[0]
    free = next((it for it in items if not it["route"].get("has_tolls")), None)
    if free is None and fast["route"].get("etr"):   # border trip: compare with the route that leaves the 407
        free = next((it for it in items if it is not fast and not it["route"].get("etr")
                     and it["route"].get("label") == "without 407"), None)
    has_etr = any(it["route"].get("etr") for it in items)
    what = "The 407" if fast["route"].get("etr") else "The toll route"
    unpriced = bool(fast["route"].get("toll_unpriced"))   # money leaves out tolls the engine reported off the 407
    if not fast["route"].get("has_tolls"):
        lead = "No tolls on the fastest route."
    elif free is None:
        lead = "Every route found has tolls."
    else:
        saved = free["minutes"] - fast["minutes"]
        extra = fast["money"] - free["money"]
        out.update(saved_min=saved, extra_cost=round(extra, 2))
        leave_later = arrive_by and not round_trip
        typ = None
        if leave_later and fast.get("typical_s") is not None and free.get("typical_s") is not None:
            typ = int(round((free["typical_s"] - fast["typical_s"]) / 60.0))
            out["typical_saved_min"] = typ
        typ_txt = ("" if typ is None else " (typical drive about %d min shorter)" % typ if typ >= 1
                   else " (typical drive about the same)")
        tail = "; its tolls are not priced here." if unpriced else ""
        if saved < 1:
            lead = ("%s does not let you leave later than %s." if leave_later else "%s saves no time over %s.") % (
                what, free["letter"])
        elif extra <= 0:
            costs_txt = "" if unpriced else " and costs no more"
            lead = ("%s lets you leave %d min later%s%s." % (what, saved, typ_txt, costs_txt) if leave_later
                    else "%s saves %d min%s." % (what, saved, costs_txt))
        else:
            per_h = extra / (saved / 60.0)
            out["per_hour"] = round(per_h, 2)
            tail = ", plus tolls not priced here." if unpriced else ""
            lead = ("%s lets you leave %d min later%s for %s ($%d/h)." % (what, saved, typ_txt, dc.money(extra),
                                                                           round(per_h)) if leave_later
                    else "%s saves %d min for %s ($%d/h of time saved)." % (what, saved, dc.money(extra), round(per_h)))
        if round_trip and saved >= 1:
            lead = lead[:-1] + (", there and back (a later leave-by plus the drive back)." if arrive_by
                                else ", there and back.")
        if tail:
            lead = lead[:-1] + tail
    rec_route = rec["route"]
    tag = LABELS.get(rec_route.get("label"), rec_route.get("label", "")).lower()
    if rec_route.get("toll_unpriced"):
        tag += ", tolls not priced"
    out["text"] = "%s At $%g/h of your time: take %s (%s)." % (lead, vot, rec["letter"], tag)
    if has_etr and not transponder:
        out["fee_note"] = ("Without a transponder 407 ETR also bills a %s account fee in any month you drive on it %s"
                           % (fee_text(), "(counted once in the 407 totals above; pass --407-this-month if you already "
                              "drive it this month)." if fee_included else "(camera charges are included above)."))
    return out


# --------------------------------------------------------------------------- links

def ll(p):
    return "%.6f,%.6f" % (p[0], p[1])


def google_link(o, d, avoid_tolls=False, via=None):
    q = {"api": "1", "origin": ll(o), "destination": ll(d), "travelmode": "driving"}
    if via:
        q["waypoints"] = ll(via)
    if avoid_tolls:
        q["avoid"] = "tolls"
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(q)


def google_time_link(o, d, when, arrive_by=False, avoid_tolls=False):
    """Undocumented desktop form: !6e0 depart at / !6e1 arrive by, !8j = local wall clock as if UTC epoch."""
    flags = "!2b1" if avoid_tolls else ""
    k = 3 + (1 if avoid_tolls else 0)
    t = calendar.timegm(when.timetuple())
    return "https://www.google.com/maps/dir/%s/%s/data=!4m%d!4m%d!2m%d%s!6e%d!7e2!8j%d!3e0" % (
        ll(o), ll(d), k + 3, k + 2, k, flags, 1 if arrive_by else 0, t)


def waze_link(d, avoid_tolls=False):
    return "https://waze.com/ul?ll=%s&navigate=yes%s" % (ll(d), "&avoid_tolls=t" if avoid_tolls else "")


def apple_link(o, d, avoid_tolls=False):
    q = {"source": ll(o), "destination": ll(d), "mode": "driving"}
    if avoid_tolls:
        q["avoid"] = "tolls"
    return "https://maps.apple.com/directions?" + urllib.parse.urlencode(q)


def walk_link(a, b):
    return th.gmaps_link(a, b, "walking")


def alt_waypoint(rec_r, routes, min_off_m=1000.0):
    """A point on rec_r's own road (the vertex farthest from every other route), so Google follows it; None when
    the route never leaves the others by min_off_m."""
    poly = rec_r.get("poly") or []
    refs = []
    for r in routes:
        if r is not rec_r and r.get("poly"):
            ref = dc.thin(r["poly"], 400)
            refs.append((ref, dc.cum_dist(ref)))
    if not refs or len(poly) < 3:
        return None
    pts = dc.thin(poly, 120)
    cut = len(pts) // 10
    best = None
    for p in pts[cut:len(pts) - cut] or pts:
        off = min(dc.project(ref, cum, p[0], p[1])[1] for ref, cum in refs)
        if best is None or off > best[0]:
            best = (off, p)
    return best[1] if best and best[0] >= min_off_m else None


# --------------------------------------------------------------------------- incidents

def _local_to_utc_ms(when, tzname, region):
    off, _ = th.offset_at_local(when, tzname, region)
    return calendar.timegm((when - off).timetuple()) * 1000


class SegIndex:
    """Grid of polyline segments for fast 'within N metres of the route' tests."""
    CELL = 0.005

    def __init__(self, poly, box=None):
        self.poly, self.cells = poly, {}
        for i in range(len(poly) - 1):
            a, b = poly[i], poly[i + 1]
            if box and not (th.in_bbox(a[0], a[1], box) or th.in_bbox(b[0], b[1], box)):
                continue
            for ci in range(int(math.floor(min(a[0], b[0]) / self.CELL)), int(math.floor(max(a[0], b[0]) / self.CELL)) + 1):
                for cj in range(int(math.floor(min(a[1], b[1]) / self.CELL)),
                                int(math.floor(max(a[1], b[1]) / self.CELL)) + 1):
                    self.cells.setdefault((ci, cj), []).append(i)

    def dist_m(self, lat, lon):
        ci, cj = int(math.floor(lat / self.CELL)), int(math.floor(lon / self.CELL))
        segs = set()
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                segs.update(self.cells.get((ci + di, cj + dj), ()))
        best = float("inf")
        kx, ky = 111320.0 * math.cos(math.radians(lat)), 110540.0
        for i in segs:
            (a_lat, a_lon), (b_lat, b_lon) = self.poly[i], self.poly[i + 1]
            ax, ay = (a_lon - lon) * kx, (a_lat - lat) * ky
            bx, by = (b_lon - lon) * kx, (b_lat - lat) * ky
            dx, dy = bx - ax, by - ay
            s2 = dx * dx + dy * dy
            t = 0.0 if s2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / s2))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
        return best


def _utc(sec):
    return dt.datetime.fromtimestamp(sec, dt.timezone.utc).replace(tzinfo=None)


def fix_json_escapes(text):
    """Double each backslash that does not start a valid JSON escape (the Toronto feed sends 'Water \\ Sewer'
    unescaped). A run of backslashes is read in pairs, so an escaped backslash ('C:\\\\x') stays as it is."""
    def fix(m):
        run, nxt = m.group(1), m.group(2)
        if len(run) % 2 and nxt and nxt not in '"\\/bfnrtu':
            return run + "\\" + nxt
        return m.group(0)
    return re.sub(r"(\\+)(.?)", fix, text, flags=re.S)


def _toronto_check(body):
    """fetch `validate` for the Toronto restrictions feed (JSON once fix_json_escapes has run, with a Closure
    list: a reply without one would read as no closures)."""
    try:
        data = json.loads(fix_json_escapes(body.decode("utf-8", "replace")))
    except ValueError:
        return "not JSON"
    if not isinstance(data, dict) or "Closure" not in data:
        return "no Closure list in the reply, so closures were not checked"
    return None


def _sched_ok(sched, start, end):
    """scheduleEveryday like '07:00-18:30': True when the drive window overlaps it (or it is unparseable)."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", sched or "")
    if not m:
        return True
    a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
    s, e = start.hour * 60 + start.minute, end.hour * 60 + end.minute
    if end.date() > start.date():
        e += 1440
    if b <= a:   # overnight window
        b += 1440
    return s < b and e > a or s + 1440 < b and e + 1440 > a


def _densify(pts, step_m=10.0, max_pts=200):
    out = []
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(th.haversine_m(a[0], a[1], b[0], b[1]) / step_m))
        out += [(a[0] + (b[0] - a[0]) * k / float(n), a[1] + (b[1] - a[1]) * k / float(n)) for k in range(n)]
    out.append(pts[-1])
    return dc.thin(out, max_pts)


def _point_along(poly, cum, s):
    import bisect
    s = max(0.0, min(cum[-1], s))
    i = max(1, min(len(poly) - 1, bisect.bisect_left(cum, s)))
    a, b = poly[i - 1], poly[i]
    seg = cum[i] - cum[i - 1]
    t = 0.0 if seg <= 0 else (s - cum[i - 1]) / seg
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _cos_between(a, b, c, d):
    """|cos| of the angle between vectors a->b and c->d (lat, lon points); None when either is a point."""
    k = math.cos(math.radians(a[0]))
    v1 = ((b[1] - a[1]) * k, b[0] - a[0])
    v2 = ((d[1] - c[1]) * k, d[0] - c[0])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return None
    return abs(v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)


def along_route(poly, cum, idx, pts, near_m, min_cos=0.7):
    """True when a closure runs along the route, not across it: its points within near_m of the route cover at
    least 40 m of the route (150 m for a closure over 250 m long, or most of a short closure), and it heads the
    same way as the route there. A side street that touches or crosses the route fails."""
    if len(pts) == 1:
        return idx.dist_m(pts[0][0], pts[0][1]) <= near_m
    length = sum(th.haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:]))
    pts = dc.thin(pts, 12) if len(pts) > 12 else pts
    if len(pts) < 6 and length > 40:   # densify short polylines so the overlap can be measured
        dense = []
        for a, b in zip(pts, pts[1:]):
            dense += [(a[0] + (b[0] - a[0]) * k / 4.0, a[1] + (b[1] - a[1]) * k / 4.0) for k in range(4)]
        pts = dense + [pts[-1]]
    near = [(p, dc.project(poly, cum, p[0], p[1])[0]) for p in pts if idx.dist_m(p[0], p[1]) <= near_m]
    if len(near) < 2:
        return False
    pos = [s for _, s in near]
    need = min(150.0, 0.6 * length) if length > 250 else min(40.0, 0.6 * length)
    if max(pos) - min(pos) < need:
        return False
    c = _cos_between(near[0][0], near[-1][0], _point_along(poly, cum, min(pos)), _point_along(poly, cum, max(pos)))
    return c is None or c >= min_cos


def ramp_on_route(poly, cum, idx, pts, near_m=15.0, share=0.8):
    """True when the route drives most of a ramp (share of its length within near_m), not just passes it."""
    if len(pts) < 2:
        return idx.dist_m(pts[0][0], pts[0][1]) <= near_m
    dense = _densify(pts)
    inside = sum(1 for p in dense if idx.dist_m(p[0], p[1]) <= near_m)
    return inside >= share * len(dense)


DIR_WORDS = {"eastbound": (0.0, 1.0), "westbound": (0.0, -1.0), "northbound": (1.0, 0.0), "southbound": (-1.0, 0.0)}


def closure_direction(name, text=""):
    """'eastbound' etc. when the closure names exactly one direction of travel, else None. A ramp's name carries
    its direction as a letter ('Park Lawn Gardiner W Ramp'); otherwise the text must name one direction only."""
    if re.search(r"\bramp\b", name or "", re.I):
        letters = set(re.findall(r"\b([EWNS])\b", name))
        if len(letters) == 1:
            return {"E": "eastbound", "W": "westbound", "N": "northbound", "S": "southbound"}[letters.pop()]
        return None
    t = ("%s %s" % (name or "", text or "")).lower()
    found = [w for w in DIR_WORDS if w in t]
    return found[0] if len(found) == 1 else None


def _vertex_at(poly, cum, s):
    import bisect
    i = max(0, min(len(poly) - 1, bisect.bisect_left(cum, s)))
    return poly[i]


def heading_matches(poly, cum, pts, word):
    """False only when the route clearly runs the other way where it passes the closure."""
    lat, lon = pts[len(pts) // 2]
    along = dc.project(poly, cum, lat, lon)[0]
    a, b = _vertex_at(poly, cum, along - 250), _vertex_at(poly, cum, along + 250)
    dy, dx = b[0] - a[0], (b[1] - a[1]) * math.cos(math.radians(lat))
    norm = math.hypot(dx, dy)
    if norm == 0:
        return True
    vy, vx = DIR_WORDS[word]
    return (dy * vy + dx * vx) / norm > -0.5


def _legs(legs):
    """[(name, poly, start, end)] from 3-tuples (name 'out') or 4-tuples."""
    return [lg if len(lg) == 4 else ("out",) + tuple(lg) for lg in legs]


def toronto_incidents(legs, tzname, region, warnings):
    """legs: [(poly, start, end)] or [(name, poly, start, end)] naive local. Closures and restrictions on the
    route inside Toronto; each item lists the legs ('out', 'back') it is on."""
    cfg = costs()["incidents"]
    box = cfg["toronto_bbox"]
    legs = [lg for lg in _legs(legs) if any(th.in_bbox(p[0], p[1], box) for p in lg[1])]
    if not legs:
        return []
    try:
        text = dc.fetch(cfg["toronto_url"], timeout=60, max_age_h=1.0, stale_max_h=24.0,
                        validate=_toronto_check).decode("utf-8", "replace")
        items = json.loads(fix_json_escapes(text)).get("Closure") or []
    except Exception as e:  # noqa: BLE001
        warnings.append("Toronto road restrictions feed unavailable (%s)" % e)
        return []
    out, by_id, seen = [], {}, set()
    for leg, poly, start, end in legs:
        idx = SegIndex(poly, box)
        cum = dc.cum_dist(poly)
        rb = dc.bbox_of([p for p in poly if th.in_bbox(p[0], p[1], box)] or poly, pad_m=200)
        t0, t1 = _local_to_utc_ms(start, tzname, region), _local_to_utc_ms(end, tzname, region)
        for c in items:
            try:
                lat, lon = float(c.get("latitude")), float(c.get("longitude"))
            except (TypeError, ValueError):
                continue
            if not th.in_bbox(lat, lon, rb):
                continue
            impact = c.get("maxImpact") or ""
            if impact not in ("Medium", "High") and c.get("type") != "ROAD_CLOSED":
                continue
            try:
                s = int(c.get("startTime") or 0)
                e = int(c.get("endTime") or 0) or 9 * 10 ** 15
            except ValueError:
                continue
            if s > t1 or e < t0 or not _sched_ok(c.get("scheduleEveryday"), start, end):
                continue
            pts = [(float(b), float(a)) for a, b in re.findall(r"\[(-?\d+\.?\d*),\s*(-?\d+\.?\d*)\]",
                                                                 c.get("geoPolyline") or "")] or [(lat, lon)]
            if not along_route(poly, cum, idx, pts, cfg["near_m"]):
                continue
            both = (c.get("directionsAffected") or "").upper() == "BOTH_DIRECTIONS"
            way = None if both else closure_direction(c.get("name") or "", c.get("description") or "")
            if way and not heading_matches(poly, cum, pts, way):
                continue   # e.g. a westbound on-ramp beside an eastbound route
            if c.get("id") in by_id:
                if leg not in by_id[c["id"]]["legs"]:
                    by_id[c["id"]]["legs"].append(leg)
                continue
            twin = (c.get("road"), (c.get("description") or "")[:80], c.get("endTime"), c.get("type"))
            if twin in seen:   # the feed often lists one job as several near-identical records
                continue
            seen.add(twin)
            closed = c.get("type") == "ROAD_CLOSED"
            what = (c.get("type") or "").replace("_", " ").lower()
            if re.search(r"\bramp\b", "%s %s" % (c.get("name") or "", c.get("road") or ""), re.I) \
                    and not ramp_on_route(poly, cum, idx, pts):
                what = "ramp %s beside your route" % ("closed" if closed else "work")
                closed = False
            until = _utc(e / 1000.0) if e < 9 * 10 ** 15 else None
            if until:
                until = until + th.offset_at_utc(until, tzname, region)[0]
            item = {"source": "City of Toronto", "road": c.get("road") or "", "name": c.get("name") or "",
                    "type": c.get("type") or "", "what": what, "impact": impact,
                    "until": until.strftime("%Y-%m-%d") if until else None,
                    "schedule": c.get("scheduleEveryday"), "legs": [leg],
                    "description": re.sub(r"^Toronto-TMC:\s*", "", (c.get("description") or "").strip()),
                    "closed": closed}
            by_id[c.get("id")] = item
            out.append(item)
    out.sort(key=lambda x: (not x["closed"], x["impact"] != "High"))
    return out[:cfg["max_items"]]


def on511_incidents(key, legs, tzname, region, warnings):
    """Ontario 511 events near the route (needs ON511_API_KEY; documented API, 10 calls / 60 s)."""
    cfg = costs()["incidents"]
    try:
        data = dc.fetch_json(cfg["on511_url"] + urllib.parse.quote(key), timeout=30, max_age_h=0.25,
                             min_interval=6.0, cache_key=cfg["on511_url"] + "KEY", stale_max_h=2.0)
    except Exception as e:  # noqa: BLE001
        # the key is in the URL, so an error that quotes the URL must not carry it (raw or URL-quoted)
        warnings.append("Ontario 511 events unavailable (%s)"
                        % str(e).replace(urllib.parse.quote(key), "KEY").replace(key, "KEY"))
        return []
    if isinstance(data, dict):
        data = data.get("events") or data.get("Events") or []
    out, by_id = [], {}
    for leg, poly, start, end in _legs(legs):
        idx = SegIndex(dc.thin(poly, 1500))
        t0, t1 = _local_to_utc_ms(start, tzname, region) / 1000, _local_to_utc_ms(end, tzname, region) / 1000
        for ev in data:
            try:
                lat, lon = float(ev.get("Latitude")), float(ev.get("Longitude"))
            except (TypeError, ValueError):
                continue
            s = ev.get("StartDate") or 0
            e = ev.get("PlannedEndDate") or 9 * 10 ** 12
            if s > t1 or e < t0:
                continue
            if idx.dist_m(lat, lon) > cfg["on511_near_m"]:
                continue
            if ev.get("ID") in by_id:
                if leg not in by_id[ev["ID"]]["legs"]:
                    by_id[ev["ID"]]["legs"].append(leg)
                continue
            until = None
            if ev.get("PlannedEndDate"):
                u = _utc(ev["PlannedEndDate"])
                until = (u + th.offset_at_utc(u, tzname, region)[0]).strftime("%Y-%m-%d %H:%M")
            item = {"source": "Ontario 511", "road": ev.get("RoadwayName") or "",
                    "name": ev.get("DirectionOfTravel") or "", "type": ev.get("EventType") or "",
                    "impact": ev.get("Severity") or "", "until": until, "schedule": None, "legs": [leg],
                    "description": (ev.get("Description") or "").strip(),
                    "closed": bool(ev.get("IsFullClosure"))}
            by_id[ev.get("ID")] = item
            out.append(item)
    rank = {"closures": 0, "accidentsAndIncidents": 1}
    out.sort(key=lambda x: (not x["closed"], rank.get(x["type"], 2)))
    return out[:cfg["max_items"]]


# --------------------------------------------------------------------------- park and ride

def hub_args(o, d, when, arrive_by, cost_per_km, stay_h, traffic_factor=None, safety_min=None):
    """trip_hubs arguments for --access car. traffic_factor and safety_min put the hub drive on the same footing
    as the drive plan (the planning allowance, the same minutes spare)."""
    argv = ["--origin", ll(o), "--dest", ll(d), "--arrive-by" if arrive_by else "--depart",
            when.strftime("%Y-%m-%d %H:%M"), "--access", "car", "--car-cost-per-km", "%.3f" % cost_per_km,
            "--stay-hours", "%g" % stay_h]
    if traffic_factor:
        argv += ["--traffic-factor", "%.2f" % traffic_factor]
    if safety_min is not None:
        argv += ["--safety-min", "%g" % safety_min]
    argv.append("--json")
    return th.build_parser().parse_args(argv), argv


def _run_with_timeout(fn, arg, timeout_s, cancel=None):
    box = {}

    def go():
        try:
            box["res"] = fn(arg)
        except BaseException as e:  # noqa: BLE001  (SystemExit from trip_hubs too)
            box["err"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        if cancel is not None:
            cancel(t)   # its next request raises, so the abandoned search stops using the network
        return None, "took longer than %d s" % timeout_s
    if "err" in box:
        return None, str(box["err"]) or box["err"].__class__.__name__
    return box["res"], None


def _parking_text(p):
    if p is None:
        return "parking not known"
    if isinstance(p, str):
        return p
    for k in ("text", "cost_text", "basis"):
        if p.get(k):
            return p[k]
    return "free" if p.get("free") else "price unknown, check signs"


def _parking_cost(p):
    if isinstance(p, dict):
        c = p.get("cost")
        return c if isinstance(c, (int, float)) else 0.0
    return 0.0


def pr_parking_cost(lots, best):
    """(cost, note) of the destination parking for the park-and-ride trigger and the driving baseline: #1's price
    when it needs no check; when #1's price is unknown or has a check line (OpenStreetMap, old or unverified data),
    the higher of it and the cheapest walkable lot that fits the stay with a price needing no check, so an unchecked
    '$5' or 'free' at #1 cannot switch park and ride off or zero the parking. note: None, or why #1's price was not
    used."""
    if not best:
        return None, None
    cost = best.get("cost")
    if cost is not None and not best.get("check"):
        return cost, None
    ok = [x["cost"] for x in lots or [] if x is not best and x.get("cost") is not None and not x.get("check")
          and x.get("fits_stay", True) and not x.get("beyond_walk")]
    if not ok or (cost is not None and cost >= min(ok)):
        return cost, None
    why = (best.get("check") or {}).get("why") or "price unknown"
    at = "" if cost is None else " (free)" if cost == 0 else " (%s)" % dc.money(cost)
    return min(ok), "the cheapest lot with a checked price; #1%s needs a check: %s" % (at, why)


def _crosses(start, end, a_hm, b_hm):
    """True when [start, end) overlaps the daily window a_hm to b_hm ('02:00', '05:00') on any day."""
    a = dt.time(*[int(x) for x in a_hm.split(":")])
    b = dt.time(*[int(x) for x in b_hm.split(":")])
    day = start.date() - dt.timedelta(days=1)
    while day <= end.date():
        ws, we = dt.datetime.combine(day, a), dt.datetime.combine(day, b)
        if we <= ws:
            we += dt.timedelta(days=1)
        if start < we and end > ws:
            return True
        day += dt.timedelta(days=1)
    return False


def hub_limit(op, park_from, stay_h):
    """Why the hub's parking cannot hold the car for this stay (GO 48 h, TTC 02:00 to 05:00), or None."""
    text = _parking_text(op.get("parking")).lower()
    for lim in costs()["park_and_ride"].get("hub_limits") or []:
        if lim["match"].lower() not in text:
            continue
        if lim.get("max_stay_h") and stay_h > lim["max_stay_h"]:
            return "%s allows at most %g h" % (lim["name"], lim["max_stay_h"])
        win = lim.get("no_parking")
        if win and park_from is not None and _crosses(park_from, park_from + dt.timedelta(hours=stay_h), *win):
            return "%s: no parking %s to %s" % (lim["name"], win[0], win[1])
    return None


def _fmt_backup(b, by):
    if not b:
        return None
    out = {"board": th.fmt_t(b.get("board", 0)), "arrive": th.fmt_t(b.get("arrive", 0)), "route": b.get("route") or ""}
    if by and b.get("book_by") is not None:
        out["leave_by"] = th.fmt_t(b["book_by"])
    return out


def select_options(entries, arrive_by, n=3, vot=30.0):
    """Up to n options: the best complete there-and-back total counting time at the value of time (vot, $/h),
    the best for time, the next best for time; shown best score first, unknown totals last. Time is the extra
    minutes against the best option (an earlier leave in arrive-by mode, a later arrival in depart mode),
    counted twice because the return goes back the same way."""
    if not entries:
        return []
    time_key = (lambda e: -e["leave_s"]) if arrive_by else (lambda e: e["arrive_s"])
    best_t = min(time_key(e) for e in entries)

    def score(e):
        money = e["rt_total"] if e["rt_total"] is not None else e["car_rt"]
        extra_min = max(0.0, (time_key(e) - best_t) / 60.0)
        e["time_cost"] = round(2 * extra_min / 60.0 * vot, 2)
        return money + e["time_cost"]

    pick = []
    known = [e for e in entries if e["rt_total"] is not None]
    if known:   # the cheapest there-and-back total is always shown, and the best value at the value of time
        for e in (min(known, key=lambda e: (e["rt_total"], time_key(e))), min(known, key=lambda e: (score(e), time_key(e)))):
            if all(e["hub"] != p["hub"] for p in pick):
                pick.append(e)
    for e in sorted(entries, key=lambda e: (time_key(e), e["rt_total"] is None, e["rt_total"] or e["car_rt"])):
        if len(pick) >= n:
            break
        if all(e["hub"] != p["hub"] for p in pick):
            pick.append(e)
    return sorted(pick, key=lambda e: (e["rt_total"] is None, score(e), time_key(e)))


def park_and_ride(o, d, when, arrive_by, cost_per_km, stay_h, drive_total, park_cost, warnings, timeout_s,
                  ctx=None):
    """Run trip_hubs in car mode and keep the hubs that beat driving on the round trip cost or on time.
    ctx: {"drive_rt", "door" (depart mode), "drive_leave" (arrive-by), "day", "traffic_factor", "safety_min"}."""
    ctx = ctx or {}
    ns, argv = hub_args(o, d, when, arrive_by, cost_per_km, stay_h, ctx.get("traffic_factor"),
                        ctx.get("safety_min"))
    cmd = "python3 trip_hubs.py " + " ".join('"%s"' % a if " " in a else a for a in argv[:-1])
    log("park and ride: running trip_hubs in car mode")
    res, err = _run_with_timeout(th.plan, ns, timeout_s, getattr(th, "cancel_thread", None))
    if err:
        warnings.append("park and ride search failed (%s). Run it yourself: %s" % (err, cmd))
        return {"ran": False, "command": cmd, "error": err}
    if res.get("access", "car") != "car":
        warnings.append("trip_hubs did not run in car mode (no --access car support yet), so park and ride is not "
                        "shown. Run: %s" % cmd)
        return {"ran": False, "command": cmd, "error": "no car mode"}
    for w in res.get("warnings") or []:
        warnings.append("park and ride: %s" % w)
    drive_rt = ctx.get("drive_rt")
    if drive_rt is None:
        drive_rt = 2 * drive_total + (park_cost or 0.0)
    day = ctx.get("day") or when.date()
    base = dt.datetime.combine(day, dt.time())
    door, drive_leave = ctx.get("door"), ctx.get("drive_leave")
    entries, dropped, limited, seen = [], 0, [], set()
    for op in list(res.get("pareto") or []) + list(res.get("others") or []):
        if op.get("hub") in seen:
            continue
        seen.add(op.get("hub"))
        legs = [lg for lg in op.get("legs") or [] if lg.get("type") == "ride"]
        first = legs[0] if legs else {}
        hub_park = _parking_cost(op.get("parking"))
        car = op.get("car_cost")   # trip_hubs car mode: km x cost per km + hub parking
        if car is None:
            car = (op.get("ride_km") or 0.0) * cost_per_km + hub_park
        complete = op.get("fare_complete", True)
        fare = op.get("transit_fare") if complete else None
        missing = [p.get("agency") or "?" for p in op.get("fare_parts") or [] if p.get("fare") is None]
        if not complete and not missing:
            missing = ["part of the trip"]
        p = op.get("parking")
        park_unknown = bool(op.get("parking_unknown")) or (isinstance(p, dict) and p.get("cost") is None
                                                          and not p.get("free"))
        car_rt = 2 * (car - hub_park) + hub_park   # drive to the hub and back, hub parking once
        # an unknown hub parking price is not $0: the there-and-back total is unknown too
        rt_total = round(car_rt + 2 * fare, 2) if fare is not None and not park_unknown else None
        leave_s = op.get("leave_by", op.get("book_by", 0)) or 0
        leave_dt = base + dt.timedelta(seconds=leave_s)
        park_from = leave_dt + dt.timedelta(minutes=op.get("ride_min") or 0)
        why = hub_limit(op, park_from, stay_h)
        if why:
            limited.append("%s (%s)" % (op.get("hub"), why))
            continue
        # differences between the clock times as printed (both truncate to the minute), so '07:17' against '07:03'
        # reads 14 min, never 15
        later = None
        if door is not None and isinstance(op.get("arrive"), (int, float)):
            later = round((floor_min(base + dt.timedelta(seconds=op["arrive"])) - floor_min(door)).total_seconds()
                          / 60.0)
        leave_later = None
        if drive_leave is not None:
            leave_later = round((floor_min(leave_dt) - floor_min(drive_leave)).total_seconds() / 60.0)
        faster = (leave_later is not None and leave_later > 0) if arrive_by else (later is not None and later < 0)
        dearer = (rt_total if rt_total is not None else car_rt) >= drive_rt
        if dearer and not faster:
            dropped += 1
            continue
        entries.append({
            "hub": op.get("hub"), "km": op.get("ride_km"), "drive_min": op.get("ride_min"),
            "parking": {"text": _parking_text(p), "cost": p.get("cost") if isinstance(p, dict) else None,
                        "price_unknown": park_unknown or (p.get("price_unknown") if isinstance(p, dict) else None),
                        "basis": p.get("basis") if isinstance(p, dict) else None},
            "leave_by": th.fmt_t(leave_s), "leave_s": leave_s,
            "board": th.fmt_t(op.get("board", first.get("depart", 0))),
            "route": th.leg_name(first) if first else "", "arrive": th.fmt_t(op.get("arrive", 0)),
            "arrive_s": op.get("arrive"), "car_cost": round(car, 2), "fare": fare, "fare_missing": missing or None,
            "total_one_way": round(car + fare, 2) if fare is not None else None,
            "car_rt": round(car_rt, 2), "rt_total": rt_total, "drive_rt": round(drive_rt, 2),
            "later_min": later, "leave_later_min": leave_later,
            "backup": _fmt_backup(op.get("backup"), arrive_by)})
    if limited:
        warnings.append("park and ride: left out %s" % "; ".join(limited))
    options = select_options(entries, arrive_by, vot=float(30.0 if ctx.get("vot") is None else ctx["vot"]))
    checked = len(seen)
    return {"ran": True, "command": cmd, "options": options, "checked": res.get("hubs_with_service") or checked,
            "candidates": checked, "dropped": dropped, "drive_total": round(drive_total, 2), "drive_rt": round(drive_rt, 2),
            "park_cost": park_cost, "traffic_factor": ctx.get("traffic_factor"), "safety_min": ctx.get("safety_min")}


# --------------------------------------------------------------------------- the plan

def make_opts(args, region, tzname, holidays):
    return {"provider": args.provider, "tomtom_key": dc.env_key("TOMTOM_API_KEY"), "weather": 1.0,
            "holidays": holidays, "tz": tzname, "region": region}


def _off_share(poly, refs, km=3.0):
    """Share of poly's points more than km from every reference polyline."""
    if not poly or not refs:
        return 1.0
    lines = []
    for ref in refs:
        t = dc.thin(ref, 800)
        lines.append((t, dc.cum_dist(t)))
    pts = dc.thin(poly, 100)
    off = sum(1 for p in pts if all(dc.project(t, c, p[0], p[1])[1] > km * 1000.0 for t, c in lines))
    return off / float(len(pts))


def past_suggestion(when, now):
    """The same month, day and time in the first year that puts it in the future."""
    for y in range(now.year, now.year + 3):
        try:
            cand = when.replace(year=y)
        except ValueError:   # 29 February
            continue
        if cand > now:
            return cand
    return None


def parse_leave_at(text, ref_day, door_est, tzname, region, warnings):
    """--leave-at: 'HH:MM' is on the trip day (the next day when that is before you get there);
    'YYYY-MM-DD HH:MM' must be after the arrival."""
    txt = (text or "").strip().replace("T", " ")
    m = re.match(r"^(\d{1,2}):(\d{2})$", txt)
    if m:
        try:
            when = dt.datetime.combine(ref_day, dt.time(int(m.group(1)), int(m.group(2))))
        except ValueError:
            raise SystemExit("--leave-at %r: use 'HH:MM' or 'YYYY-MM-DD HH:MM' (local time)" % text)
        if door_est is not None and when <= door_est:
            when += dt.timedelta(days=1)
            warnings.append("--leave-at %s is before you get there (about %s), so it is read as %s; pass "
                            "'YYYY-MM-DD HH:MM' to choose the day" % (txt, dc.fmt_clock(door_est),
                                                                     when.strftime("%a %Y-%m-%d %H:%M")))
        return when
    when = _when("--leave-at", txt, tzname, region, warnings)
    if door_est is not None and when <= door_est:
        raise SystemExit("--leave-at %s is before you get there (about %s); pass a later time"
                         % (txt, door_est.strftime("%Y-%m-%d %H:%M")))
    return when


def parse_slots(rules):
    """[(start 'HH:MM', end 'HH:MM')] from a reservation rule that lists slots ('4 h slots 08:00-12:00, ...')."""
    out = []
    for r in rules or []:
        if "slot" not in r.lower():
            continue
        for a, b in re.findall(r"\b(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\b", r):
            if (a, b) not in out:
                out.append((a, b))
    return out


def pick_slot(slots, arrive, min_left_min=120):
    """The latest slot that has started by `arrive` with min_left_min left, else the next one to start."""
    day = arrive.date()
    win = [(dt.datetime.combine(day, dt.time(*map(int, a.split(":")))),
            dt.datetime.combine(day, dt.time(*map(int, b.split(":"))))) for a, b in slots]
    ok = [w for w in win if w[0] <= arrive and (w[1] - arrive).total_seconds() >= min_left_min * 60]
    if ok:
        return max(ok)
    later = [w for w in win if w[0] > arrive]
    return min(later) if later else None


def validate(args):
    if not hasattr(args, "vehicle_given"):
        args.vehicle_given = args.vehicle is not None
    if args.vehicle is None:
        args.vehicle = "gas"
    for f in ("car", "save_car", "forget_car", "no_saved_car"):
        if not hasattr(args, f):
            setattr(args, f, None)
    if args.save_car and args.forget_car:
        raise SystemExit("--save-car and --forget-car cannot be combined")
    ev_set = [f for f in EV_FLAGS if getattr(args, f, None) not in (None, False)]
    if ev_set and args.vehicle in ("hybrid", "diesel"):
        raise SystemExit("--vehicle %s cannot be combined with EV options (%s)"
                         % (args.vehicle, ", ".join("--" + f.replace("_", "-") for f in ev_set)))
    if getattr(args, "l_per_100km", None) and (ev_set or args.vehicle == "ev"):
        raise SystemExit("--l-per-100km is for a fuel car; for an EV use --kwh-per-100km")
    if ev_set and args.vehicle == "gas":
        args.vehicle = "ev"   # EV options imply --vehicle ev
    tz = getattr(args, "tz", None)
    if tz and th._zoneinfo(tz) is None and tz not in th.TZ_TABLE:
        raise SystemExit("--tz %r: unknown time zone; use an IANA name such as America/Winnipeg" % tz)


def plan(args):
    """The drive plan for `args`; any reply that came from an older cached copy is named in its warnings."""
    dc.stale_notes()   # forget stand-ins from an earlier plan in the same process
    res = _plan(args)
    res["warnings"].extend(w for w in dc.stale_notes() if w not in res["warnings"])
    return res


def _plan(args):
    validate(args)
    warnings = []
    car_block = resolve_car(args, warnings)
    regions = th.load_regions()
    strict = not args.allow_street_match
    o_lat, o_lon, o_label = resolve_drive_place(args.origin, "origin", warnings, strict)
    d_lat, d_lon, d_label = resolve_drive_place(args.dest, "destination", warnings, strict)
    o, d = (o_lat, o_lon), (d_lat, d_lon)
    region = th.pick_region(regions, "auto", o_lat, o_lon)
    tz_arg = getattr(args, "tz", None)
    tzname = tz_arg or (region or {}).get("timezone") or DEFAULT_TZ
    if not tz_arg and region is None:
        warnings.append("the origin is outside the bundled region files, so the times you gave and the times shown "
                        "are %s time; if the origin keeps another clock (northwestern Ontario is on Central time), "
                        "rerun with --tz ZONE, an IANA name such as America/Winnipeg" % DEFAULT_TZ)
    holidays = dc.holidays_for(regions)
    now, _ = th.local_now(tzname, region)
    arrive_by = bool(args.arrive_by)
    deadline = _when("--arrive-by", args.arrive_by, tzname, region, warnings) if arrive_by else None
    opts = make_opts(args, region, tzname, holidays)
    ev = args.vehicle == "ev"
    sources = []

    dr = _mod("drive_route")
    if dr is None:
        raise SystemExit("drive_route.py is missing next to drive_plan.py; it does the routing.")
    straight = th.haversine_m(o_lat, o_lon, d_lat, d_lon)
    if arrive_by:
        guess = th.heuristic_ride(straight)[1] * 1.6 + 15 * 60
        depart = floor_min(deadline - dt.timedelta(seconds=guess))
    else:
        depart = _when("--depart", args.depart, tzname, region, warnings)
    ref_t = deadline or depart
    if ref_t < now - dt.timedelta(minutes=5):
        sug = past_suggestion(ref_t, now)
        warnings.append("that time is in the past (%s)%s" % (
            ref_t.strftime("%a %Y-%m-%d %H:%M"), "; did you mean %s?" % sug.strftime("%a %Y-%m-%d %H:%M") if sug else ""))
    log("routing %s -> %s" % (ll(o), ll(d)))
    routes = dr.get_routes(o, d, depart, opts, warnings, arrive_at=deadline)
    res = {"version": th.VERSION, "origin": {"lat": o_lat, "lon": o_lon, "label": o_label},
           "destination": {"lat": d_lat, "lon": d_lon, "label": d_label}, "timezone": tzname,
           "region": (region or {}).get("id"), "mode": "arrive_by" if arrive_by else "depart",
           "depart": depart, "deadline": deadline, "now": now, "warnings": warnings, "sources": sources,
           "vehicle": args.vehicle, "transponder": args.transponder, "round_trip": args.round_trip,
           "car": car_block}
    day = ref_t.date()
    res["holiday"] = holiday_name(day, regions)
    if not routes:
        if not any("routing engine" in w for w in warnings):
            warnings.append("no routing engine answered, so there is no drive plan. Check the network and retry, or "
                            "use the Google Maps link.")
        note = blocked_note(warnings)
        if note:
            warnings.insert(0, note)
        res.update(routes=[], links={"google": google_link(o, d)}, weather=None, network_blocked=bool(note))
        pk = _mod("parking")
        if pk is not None:   # the curated parking files work offline; price them for a rough arrival time
            stay_min = (args.stay_hours if args.stay_hours is not None else 2.0) * 60.0
            arrive = deadline if arrive_by else depart + dt.timedelta(seconds=th.heuristic_ride(straight)[1])
            try:
                parking = pk.find_parking(d_lat, d_lon, arrive, stay_min, {
                    "max_walk_m": args.max_walk_m, "height_m": args.height_m, "holidays": holidays,
                    "region": region, "offline": bool(note)}, warnings)
            except Exception as e:  # noqa: BLE001
                parking = None
                warnings.append("parking search failed (%s)" % e)
            if parking is not None and not note:
                opts_now = parking.get("options") or []
                parking = attach_reviews(parking, d, opts_now[0] if opts_now else None, warnings, today=now.date())
                if review_source(parking):
                    sources.append(review_source(parking))
            if parking is not None:
                res.update(parking=parking, parking_from=arrive, parking_until=arrive + dt.timedelta(minutes=stay_min),
                           parking_stay_min=stay_min, stay_min=stay_min, parking_estimate=True)
        return res
    o_short, o_info = place_label(o_label, o_lat, o_lon)
    d_short, d_info = place_label(d_label, d_lat, d_lon)
    dest_cc = (d_info or {}).get("country")
    if not dest_cc and _home_label(d_lat, d_lon):   # home: no lookup (TRIP_HOME's promise); its region says the country
        d_region = th.pick_region(regions, "auto", d_lat, d_lon)
        dest_cc = (d_region.get("country") or REGION_COUNTRY) if d_region else None
    elif not dest_cc:
        dest_cc = reverse_country(d_lat, d_lon)
    res["destination"]["country"] = dest_cc
    res["origin"]["short"], res["destination"]["short"] = o_short, d_short
    # a crossing missing from tolls.json: told apart by the two ends' countries (the origin's looked up only when
    # no listed crossing explains the trip), it gets the border allowance and a warning, its toll unpriced
    a_cfg = costs()["arrival"]
    border_min = float(a_cfg.get("border_wait_min", 30))
    orig_cc = (o_info or {}).get("country") or _country_from_text(o_label)
    listed = all(r.get("bridges") for r in routes)   # each route without a listed crossing needs the allowance
    if not orig_cc and dest_cc and not listed:
        if _home_label(o_lat, o_lon):
            orig_cc = (region.get("country") or REGION_COUNTRY) if region else None
        else:
            orig_cc = reverse_country(o_lat, o_lon)
    res["origin"]["country"] = orig_cc
    unlisted_border = bool(orig_cc and dest_cc and orig_cc != dest_cc and not listed)
    if unlisted_border:
        warnings.append("this drive crosses the Canada-US border at a crossing with no data here: its toll, if any, "
                        "is not priced; bring a passport or NEXUS card and check CBSA and CBP wait times; a %d min "
                        "allowance is included" % round(border_min))

    # ---- weather along the route, at the hour each part is driven
    fast = routes[0]
    if args.weather != "auto":
        wx = weather_manual(args.weather, args.temp_c)
    else:
        wx = weather_points(route_samples(fast, o, d, depart), tzname, warnings, today=now.date())
        if wx.get("forecast"):
            sources.append(WEATHER_SOURCE)
    if args.temp_c is not None:
        wx["temp_c"] = args.temp_c
    res["weather"] = wx
    opts["weather"] = wx["mult"]
    if wx["mult"] != 1.0:
        for r in routes:
            dr.retime(r, depart, opts)
        if hasattr(dr, "relabel"):
            dr.relabel(routes)
            fast = routes[0]
        if all(r.get("_live_now") for r in routes):   # drive_route adds no weather factor to live TomTom times
            warnings[:] = [w for w in warnings if not (w.startswith("x") and w.endswith("applied to the drive time"))]
            wx["text"] = re.sub(r" \(\+\d+%\)|, \+\d+%(?=\))", "", wx.get("text") or "") + \
                "; not added to the time: TomTom live traffic already reflects the weather"
            wx["live_traffic"] = True

    # ---- fuel or energy
    price, energy = None, {}
    if car_block.get("how") in ("lookup", "saved") and car_block.get("source"):
        sources.append("car: %s" % _nrcan_text(car_block))
    if not ev:
        fuel = car_block.get("fuel") or ("diesel" if args.vehicle == "diesel" else "regular")
        price = fuel_price(o_lat, o_lon, fuel, warnings, args.fuel_price, province_of(o_info))
        if price.get("date"):
            sources.append(("fuel: stored Ontario average, week of %s (survey file unavailable)" if price.get("fallback")
                            else "Ontario fuel survey week of %s") % price["date"])
        res["fuel_price"] = price
        if args.vehicle == "diesel" and car_block.get("how") == "class default":
            warnings.append(costs()["consumption"]["diesel_note"])
    evc = _mod("ev_charging") if ev else None
    car = None
    if ev and evc is not None:
        car = car_block.get("ev") or build_ev(car_block, args, warnings)   # resolve_car builds it
    elif ev:
        warnings.append("ev_charging.py is missing, so no charging stops were planned; energy is priced at the home "
                        "rate")
    home_rate = costs()["electricity"]["home_rate"]
    rate_note = home_rate_note(depart.date()) if ev else None
    if rate_note:   # the home rate prices every EV plan's energy
        warnings.append(rate_note)
    shared = {}
    api_key = dc.env_key("NLR_API_KEY") or "DEMO_KEY"

    def ev_opts(poly, return_trip, dest):
        eo = {"return_trip": return_trip, "dest": dest, "home_rate": home_rate, "api_key": api_key}
        if shared.get("stations") is not None and _off_share(poly, shared["polys"]) <= 0.10:
            eo.update(stations=shared["stations"], stations_source=shared["source"])   # same corridor: no new call
        return eo

    def keep_stations(ch, poly):
        got = ch.get("_stations")
        if got is None:
            return
        if shared.get("stations") is None:
            shared.update(stations=list(got), source=ch.get("_stations_source"), polys=[poly])
        elif got is not shared["stations"]:   # this route's own list: merge it for later routes
            ids = {s.get("id") for s in shared["stations"]}
            shared["stations"] += [s for s in got if s.get("id") not in ids]
            shared["polys"].append(poly)

    def energy_for(r, temp_c=None):
        """Fuel or energy for route r at temp_c (default: the outbound weather). The charging plan (an EV with
        ev_charging) always uses the outbound weather: the drive back plans its own in make_return."""
        tc = wx.get("temp_c") if temp_c is None else temp_c
        if not ev:
            cls = car_block.get("class") if car_block.get("how") == "class default" else args.car_class
            l100, basis = consumption(args.vehicle, cls, r.get("hwy_share"),
                                      tc, car_block.get("l_per_100km"), car_block.get("figures"))
            cost = fuel_cost(r, l100, price)
            cf = cold_factor(tc)
            return {"kind": "fuel", "l100": l100, "basis": basis, "cost": cost, "extra_s": 0.0,
                    "text": "fuel ~%s (%.0f km at %.1f L/100 km%s, $%.3f/L %s)" % (
                        dc.money(cost), r.get("km", 0.0), l100,
                        " incl. %d%% for the cold" % round((cf - 1) * 100) if cf != 1.0 else "", price["per_l"],
                        price["text"])}
        if evc is not None:
            soc = (args.soc if args.soc is not None else 90.0) / 100.0
            poly = r.get("poly") or [o, d]
            eo = ev_opts(poly, args.round_trip, d)
            eo["hwy_share"] = r.get("hwy_share")
            eo["dest_country"] = dest_cc
            ch = evc.plan_charging(poly, r.get("km", 0.0), r.get("depart") or depart, car, soc,
                                   wx.get("temp_c"), warnings, eo)
            keep_stations(ch, poly)
            ch = {k: v for k, v in ch.items() if not k.startswith("_")}
            ch.setdefault("soc_start", soc)
            ch["soc_assumed"] = args.soc is None   # the CHARGING header marks the 90% default
            cost = (ch.get("public_cost") or 0.0) + (ch.get("home_energy_cost") or 0.0)
            return {"kind": "ev", "charging": ch, "cost": cost, "extra_s": ch.get("extra_s") or 0.0,
                    "kwh": ch.get("kwh_used"), "kwh100": ch.get("kwh_100km"),
                    "text": "energy ~%s (%.1f kWh at %.1f kWh/100 km%s)" % (
                        dc.money(cost), ch.get("kwh_used") or 0.0, ch.get("kwh_100km") or 0.0,
                        ", %s at public chargers" % dc.money(ch["public_cost"]) if ch.get("public_cost") else
                        ", home rate $%.2f/kWh" % home_rate)}
        dflt = costs()["electricity"]["default_kwh_100km"]["value"]
        ea_ = car_block.get("ev_args") or {}
        nr = ea_.get("nrcan")
        k100 = (ea_.get("kwh_per_100km") or (nr and (nr["city"] + nr["hwy"]) / 2.0) or dflt) * \
            cold_factor(tc)
        kwh = r.get("km", 0.0) * k100 / 100.0
        return {"kind": "ev", "charging": None, "cost": kwh * home_rate, "extra_s": 0.0, "kwh": kwh, "kwh100": k100,
                "text": "energy ~%s (%.1f kWh/100 km x %.0f km at $%.2f/kWh home rate)" % (
                    dc.money(kwh * home_rate), k100, r.get("km", 0.0), home_rate)}

    for r in routes[:3]:
        energy[id(r)] = energy_for(r)
        st = route_stops(r, energy[id(r)].get("charging"), border_min)
        if st and hasattr(dr, "set_stops"):   # the waits go on the clock, so the 407 band is for after them
            dr.set_stops(r, st)
            dr.retime(r, r.get("depart") or depart, opts)
    if ev and evc is not None and args.soc is None:
        warnings.append("the starting charge was not given, so charging assumes you leave with 90%; pass --soc "
                        "with the battery percent")
    if hasattr(dr, "relabel") and any(r.get("_stops") for r in routes):
        dr.relabel(routes)
        fast = routes[0]

    def border_s(r):
        """Planning allowance for each border crossing on the route (CBSA / CBP wait); one for a crossing
        missing from tolls.json."""
        n = len((r or {}).get("bridges") or [])
        if not n and unlisted_border:
            n = 1
        return border_min * 60.0 * n

    def lot_arrival(r):
        a = r.get("arrive") or r.get("depart") or depart
        return dc.add_s(a, energy[id(r)]["extra_s"] + border_s(r), tzname, region)

    # ---- when you leave the destination, and the first parking search (search and walk minutes)
    door_est = deadline if arrive_by else dc.add_s(lot_arrival(fast), a_cfg["default_search_min"] * 60.0, tzname,
                                                   region)
    leave_at = parse_leave_at(args.leave_at, day, door_est, tzname, region, warnings) if args.leave_at else None
    stay_min = (args.stay_hours if args.stay_hours is not None else 2.0) * 60.0
    if leave_at:
        stay_min = dc.elapsed_s(door_est, leave_at, tzname, region) / 60.0
    pk = _mod("parking")
    park_arrive = (deadline - dt.timedelta(minutes=10)) if arrive_by else lot_arrival(fast)
    park_minutes = stay_min
    pk_opts = {"max_walk_m": args.max_walk_m, "height_m": args.height_m, "holidays": holidays, "region": region,
               "country": dest_cc}
    parking = None
    if pk is None:
        warnings.append("parking.py is missing, so parking was not searched")
    else:
        try:
            parking = pk.find_parking(d_lat, d_lon, park_arrive, stay_min, pk_opts, warnings)
        except Exception as e:  # noqa: BLE001
            warnings.append("parking search failed (%s)" % e)
    lots = targets((parking or {}).get("options"))
    best = lots[0] if lots else None
    slack_min = a_cfg["slack_min"]

    def lot_minutes(b):
        return (b.get("search_min", 2) if b else a_cfg["default_search_min"]), (b.get("walk_min", 0) if b else 0)
    search_min, walk_min = lot_minutes(best)

    # ---- arrive-by: latest departure per route
    def back_solve(r):
        extra = (search_min + walk_min + slack_min) * 60.0 + energy[id(r)]["extra_s"] + border_s(r)
        r["leave_by"] = plan_backwards(dr, r, deadline, extra, opts)
    if arrive_by:
        for r in routes:
            back_solve(r)
        if hasattr(dr, "relabel"):
            dr.relabel(routes)

    # ---- when the car leaves the lot (start of the drive back), capped by the lot's maximum stay or slot
    def lot_leave(r):
        """(lot arrival, lot departure, cap text or None, slot (start, end) or None)."""
        arr = lot_arrival(r)
        if leave_at:
            end = dc.add_s(leave_at, walk_min * 60.0, tzname, region)
        elif arrive_by:
            end = dc.add_s(deadline, (stay_min + walk_min) * 60.0, tzname, region)
        else:
            end = dc.add_s(arr, (search_min + 2 * walk_min + stay_min) * 60.0, tzname, region)
        cap, slot = None, None
        park_start = dc.add_s(arr, search_min * 60.0, tzname, region)
        if best and best.get("reservation") and "slot" in str(best.get("reservation")).lower():
            slot = pick_slot(parse_slots((parking or {}).get("place_rules")), park_start)
            if slot and end > slot[1]:
                end, cap = slot[1], "slot ends %s" % dc.fmt_clock(slot[1])
        if best and best.get("max_stay_min"):
            lim = dc.add_s(max(park_start, slot[0] if slot else park_start), best["max_stay_min"] * 60.0, tzname,
                           region)
            fits = None
            if end > lim and pk is not None and hasattr(pk, "stay_fits"):
                # a limit tied to the paid hours (Burlington "Mon-Fri 9-6, 3 hr limit") does not bind on a free day
                fits = pk.stay_fits(best, park_start, dc.elapsed_s(park_start, end, tzname, region) / 60.0, holidays)
            if end > lim and not fits:
                end, cap = lim, "max stay %s" % fmt_stay(best["max_stay_min"])
        return arr, end, cap, slot

    # ---- round trip: the drive back at its own time and weather, with its own charging
    wx_back_cache = {}

    def make_return(r):
        _, back_at, _, _ = lot_leave(r)
        o_back = opts
        if args.weather == "auto":
            key = (id(r), back_at)
            if key not in wx_back_cache:
                rev = dict(r, poly=list(reversed(r.get("poly") or [o, d])))
                wx_back_cache[key] = weather_points(route_samples(rev, d, o, back_at), tzname, warnings,
                                                    today=now.date())
            wb = wx_back_cache[key]
            o_back = dict(opts, weather=wb["mult"])
        else:
            wb = wx
        t_back = wb.get("temp_c") if wb.get("temp_c") is not None else wx.get("temp_c")
        back = dr.reverse_for_return(r, back_at, o_back)
        back["depart"] = back.get("depart") or back_at
        back["weather"] = {k: wb.get(k) for k in ("mult", "temp_c", "text", "kind")}
        en = energy[id(r)]
        back["energy_cost"], back["charge_s"], back["charging"] = en["cost"], 0.0, None
        if not ev or evc is None:   # fuel, or an EV priced at the home rate: at the drive back's temperature
            back["energy_cost"] = energy_for(back, t_back)["cost"]
        ch = en.get("charging")
        if ev and evc is not None and ch:
            rp = ch.get("return_plan")
            soc0 = ch.get("arrive_soc") or 0.0
            need = ch.get("return_soc_needed")
            tf, t_out = getattr(evc, "temp_factor", None), ch.get("temp_c")
            if rp is None and need is not None and tf is not None and None not in (t_back, t_out, ch.get("reserve")):
                # the need at the drive back's own temperature (its energy, and the 20% reserve below 0 C); a
                # warmer drive back keeps the outbound need, as the outbound note advises
                m_ev = dc.load_data("ev")["model"]
                need_b = ((need - ch["reserve"]) * tf(t_back) / tf(t_out)
                          + m_ev["reserve"]["below_0C" if t_back < 0 else "default"])
                if soc0 >= need and soc0 < need_b - 1e-9:   # the outbound note said no charging is needed
                    ch["notes"] = [n for n in ch.get("notes") or [] if not n.startswith("Return trip")]
                    ch["notes"].append("Return trip: you arrive with %.0f%% but at its forecast %d C the drive back "
                                       "needs about %.0f%%: charge at the destination, or stop on the way back as "
                                       "planned above" % (soc0 * 100, round(t_back), need_b * 100))
                    if need_b <= m_ev["max_soc"] + 1e-9:
                        ch["return_soc_needed"] = round(need_b, 3)
                    else:
                        ch.pop("return_soc_needed", None)
                need = max(need, need_b)
            if rp is None and need is not None and soc0 < need:
                bw = []
                bpoly = back.get("poly") or list(reversed(r.get("poly") or [o, d]))
                eo = ev_opts(bpoly, False, o)
                eo["hwy_share"] = back.get("hwy_share", r.get("hwy_share"))
                rp = evc.plan_charging(bpoly, back.get("km", r.get("km", 0.0)), back_at, car, soc0,
                                       t_back, bw, eo)
                keep_stations(rp, bpoly)
                rp = {k: v for k, v in rp.items() if not k.startswith("_")}
                warnings.extend("drive back: %s" % w for w in bw)
            if rp is not None:
                rp.setdefault("soc_start", rp.get("start_soc", soc0))   # a destination charge, else the arrival
                back["charging"] = rp
                back["energy_cost"] = (rp.get("public_cost") or 0.0) + (rp.get("home_energy_cost") or 0.0)
                back["charge_s"] = rp.get("extra_s") or 0.0
                if rp.get("feasible") is False:
                    back["charging_infeasible"] = True
            else:   # no charging needed on the way back: the energy is recharged at home
                kwh_b = ch.get("kwh_used") or en.get("kwh") or 0.0
                if tf is not None and t_back is not None and t_out is not None:
                    kwh_b *= tf(t_back) / tf(t_out)   # the drive back at its own temperature
                back["energy_cost"] = kwh_b * home_rate
        st = route_stops(back, back.get("charging"), border_min)
        if st and hasattr(dr, "set_stops"):   # before the arrival additions below: retime resets arrive
            dr.set_stops(back, st)
            dr.retime(back, back_at, o_back)
        back["border_s"] = border_s(back)
        if back["charge_s"] or back["border_s"]:
            for k in ("arrive", "arrive_plan"):
                if back.get(k):
                    back[k] = dc.add_s(back[k], back["charge_s"] + back["border_s"], tzname, region)
        return back

    returns = {}
    if args.round_trip:
        for r in routes:
            try:
                returns[id(r)] = make_return(r)
            except Exception as e:  # noqa: BLE001
                warnings.append("return trip could not be priced (%s)" % e)

    # ---- trade-off
    fee_amt = account_fee()
    count_fee = not args.transponder and not args.etr_this_month

    def item_for(i, r):
        en = energy[id(r)]
        money = en["cost"] + route_tolls(r, args.transponder)
        if arrive_by and r.get("leave_by"):
            time_s = dc.elapsed_s(r["leave_by"], deadline, tzname, region)   # what you commit to: leave-by to the door
        else:
            time_s = (r.get("typical_s") or 0.0) + en["extra_s"] + border_s(r)
        back = returns.get(id(r))
        if back is not None:
            money += back["energy_cost"] + route_tolls(back, args.transponder)
            time_s += (back.get("typical_s") or 0.0) + back["charge_s"] + (back.get("border_s") or 0.0)
        fee = fee_amt if count_fee and (r.get("etr") or (back or {}).get("etr")) else 0.0
        return {"letter": chr(65 + i), "route": r, "money": money + fee, "fee": fee, "time_s": time_s,
                "typical_s": (r.get("typical_s") or 0.0) + en["extra_s"] + border_s(r)}

    def decide():
        """(items, trade-off, recommended item, notes for WARNINGS) for the routes as timed now; called again
        when a new lot's walk re-solves every route, so the notes are added to the warnings once, at the end."""
        notes = []
        its = [item_for(i, r) for i, r in enumerate(routes)]
        for it in its:
            back = returns.get(id(it["route"]))
            for b in (it["route"].get("bridges") or []) + ((back or {}).get("bridges") or []):
                if b.get("cad") is None:
                    notes.append("%s charges in US dollars only (%s); not in the totals" % (b["name"], b["text"]))
            if it["route"].get("toll_unpriced") or (back or {}).get("toll_unpriced"):
                notes.append("%s has tolls this planner does not price (only the 407 ETR and the listed border "
                             "crossings are); its total leaves them out" % it["letter"])
        cands = its
        if arrive_by:
            late = [it for it in its if it["route"]["leave_by"] < now]
            if len(late) == len(its):
                notes.append("the deadline cannot be met any more: even the fastest route needed you to leave by %s"
                             % dc.fmt_clock(min(r["leave_by"] for r in routes)))
            elif late:
                cands = [it for it in its if it not in late]
                notes.append("%s needed you to leave by %s (already past), so %s not recommended" % (
                    " and ".join(it["letter"] for it in late),
                    " / ".join(dc.fmt_clock(it["route"]["leave_by"]) for it in late),
                    "it is" if len(late) == 1 else "they are"))
            for it in its:
                over = it["route"].get("leave_by_late_s")
                if over:
                    notes.append("%s: leaving by %s still arrives about %d min after the deadline by the traffic "
                                 "model (its leave-by did not settle); leave earlier" % (
                                     it["letter"], dc.fmt_clock(it["route"]["leave_by"]), int(math.ceil(over / 60.0))))

        # a route whose charging stops (or drive back) cannot be planned is not recommended while another's can
        def no_charge(it):
            ch_ = energy[id(it["route"])].get("charging") or {}
            back_ = returns.get(id(it["route"])) or {}
            return ch_.get("feasible") is False or bool(back_.get("charging_infeasible"))
        stuck = [it for it in cands if no_charge(it)]
        if stuck and len(stuck) < len(cands):
            cands = [it for it in cands if it not in stuck]
            notes.append("%s cannot be driven on the fast chargers found (see CHARGING), so %s not recommended" % (
                " and ".join(it["letter"] for it in stuck), "it is" if len(stuck) == 1 else "they are"))
        t = trade_off(cands, args.value_of_time, args.transponder, args.round_trip,
                      fee_included=count_fee and any(it["fee"] for it in its), arrive_by=arrive_by)
        if not count_fee and not args.transponder and t.get("fee_note"):
            t["fee_note"] = "407 ETR account fee not counted (--407-this-month)."
        return its, t, next(it for it in its if it["letter"] == t["rec"]), notes

    items, tr, rec, tr_notes = decide()
    rec_r = rec["route"]

    # ---- parking at the recommended arrival, for the time the car is actually parked
    def parked_min(a, b):
        return dc.elapsed_s(a, b, tzname, region) / 60.0   # elapsed time: the clock may change while parked

    lot_arr, lot_end, cap, slot = lot_leave(rec_r)
    parked = parked_min(lot_arr, lot_end)

    def _key(at, minutes):
        return floor_min(at), int(round(minutes))

    def _same(k1, k2):
        return abs((k1[0] - k2[0]).total_seconds()) <= 60 and abs(k1[1] - k2[1]) <= 1

    results = {_key(park_arrive, park_minutes): parking} if parking is not None else {}
    parking_key = _key(park_arrive, park_minutes)
    skipped = []
    for _ in range(4):   # each lot has its own walk, which moves the time parked: search until the pick holds
        lot_arr, lot_end, cap, slot = lot_leave(rec_r)
        parked = parked_min(lot_arr, lot_end)
        key = _key(lot_arr, parked)
        if parking is None or pk is None or _same(key, parking_key):
            break
        hit = next((v for k, v in results.items() if _same(k, key)), None)
        if hit is None:
            try:
                hit = pk.find_parking(d_lat, d_lon, lot_arr, parked, pk_opts, warnings)
            except Exception as e:  # noqa: BLE001
                warnings.append("parking search failed (%s)" % e)
                break
            results[key] = hit
        parking, parking_key = hit, key
        # the first ranked lot whose own walk gives this parked time; a cheaper lot whose longer walk keeps the
        # car there longer was (or is next) priced for its own time and is skipped once that search is done
        prev = (best, search_min, walk_min)
        pick, skipped = None, []
        for cand in targets(parking.get("options")):
            best = cand
            search_min, walk_min = lot_minutes(cand)
            a2, e2, _c, _s = lot_leave(rec_r)
            ok = _key(a2, parked_min(a2, e2))
            if _same(ok, key) or not any(_same(k, ok) for k in results):
                pick = cand
                break
            skipped.append((cand, parked_min(a2, e2)))
        best, search_min, walk_min = prev
        if pick is None:
            lots_now = targets(parking.get("options"))
            pick, skipped = (lots_now[0] if lots_now else None), []
        best = pick
        s2, w2 = lot_minutes(best)
        if (s2, w2) != (search_min, walk_min):
            search_min, walk_min = s2, w2
            # every route leaves, parks and returns on the new walk: re-solve them all, then the trade-off (the rows
            # and TRADE-OFF must agree); a new recommendation is searched for on the next pass
            for r in routes:
                if arrive_by:
                    back_solve(r)
                if id(r) in returns:
                    try:
                        returns[id(r)] = make_return(r)
                    except Exception as e:  # noqa: BLE001
                        warnings.append("return trip could not be priced (%s)" % e)
            items, tr, rec, tr_notes = decide()
            rec_r = rec["route"]
    warnings.extend(tr_notes)
    res["trade_off"] = {k: v for k, v in tr.items()}
    lot_arr, lot_end, cap, slot = lot_leave(rec_r)
    parked = parked_min(lot_arr, lot_end)
    if parking is not None and best is not None and (parking.get("options") or [None])[0] is not best:
        opts_now = [x for x in parking.get("options") or [] if x is not best]
        parking = dict(parking, options=[best] + [x for x in opts_now if all(x is not y for y, _m in skipped)]
                       + [y for y, _m in skipped])
        if pk is not None and hasattr(pk, "check_list"):
            parking["check"] = pk.check_list(parking["options"])
        for sk, m in skipped:
            warnings.append("%s looks cheaper for %s parked, but its %d min walk keeps the car there %s, and for "
                            "that time it is not among the options found (check its signs and limits); the plan uses "
                            "%s" % (sk.get("name") or "a lot", fmt_stay(parking_key[1]),
                                    round(sk.get("walk_min") or 0), fmt_stay(m), best.get("name") or "the next lot"))
    parking = attach_reviews(parking, d, best, warnings, today=now.date())
    if cap and best:
        warnings.append("%s: %s, so the return is planned at %s, not after your %s stay" % (
            best.get("name") or "the lot", cap, dc.fmt_clock(lot_end, ref_t), fmt_stay(stay_min)))
    res["parking"] = parking
    res["stay_min"] = round(stay_min, 1)
    res["parking_stay_min"] = round(parked, 1)
    res["parking_from"], res["parking_until"] = lot_arr, lot_end
    if arrive_by:
        res["depart_guess"] = depart
        res["depart"] = rec_r.get("leave_by")
    target = (best["lat"], best["lon"]) if best else d

    out_routes = []
    for it in items:
        r, en = it["route"], energy[id(it["route"])]
        row = {k: v for k, v in r.items() if k not in ("chunks", "poly") and not k.startswith("_")}
        row["polyline"] = dc.encode_polyline(dc.thin(r.get("poly") or [], 500)) if r.get("poly") else ""
        row.update(letter=it["letter"], energy={k: v for k, v in en.items() if k != "charging"},
                   charge_s=en["extra_s"], border_s=border_s(r), money=round(it["money"], 2), account_fee=it["fee"],
                   time_s=it["time_s"],
                   tolls=round(route_tolls(r, args.transponder), 2))
        back = returns.get(id(r))
        if back is not None:
            row["return"] = {k: v for k, v in back.items() if k not in ("chunks", "poly") and not k.startswith("_")}
            row["return"]["tolls"] = round(route_tolls(back, args.transponder), 2)
        out_routes.append(row)
    res["routes"] = out_routes
    res["recommended"] = tr["rec"]

    # ---- arrival
    rdep = rec_r.get("leave_by") if arrive_by else rec_r.get("depart") or depart
    lot_typ = rec_r.get("arrive")
    lot_plan = rec_r.get("arrive_plan")
    rch = energy[id(rec_r)]["extra_s"] + border_s(rec_r)
    if rch:
        lot_typ = dc.add_s(lot_typ, rch, tzname, region) if lot_typ else lot_typ
        lot_plan = dc.add_s(lot_plan, rch, tzname, region) if lot_plan else lot_plan
    at_door = dc.add_s(lot_typ, (search_min + walk_min) * 60.0, tzname, region) if lot_typ else None
    res["arrival"] = {"leave": rdep, "at_lot": lot_typ, "at_lot_plan": lot_plan, "lot": best["name"] if best else None,
                      "search_min": search_min, "walk_min": walk_min, "slack_min": slack_min,
                      "charge_min": round(energy[id(rec_r)]["extra_s"] / 60.0), "at_door": at_door,
                      "border_min": round(border_s(rec_r) / 60.0),
                      "plan_s": rec_r.get("plan_s"), "deadline": deadline, "lot_leave": lot_end, "cap": cap}
    if best and best.get("often_full"):
        fb = fallback_lot(targets((parking or {}).get("options")), best)
        res["arrival"]["often_full"] = True
        res["arrival"]["fallback"] = {"name": fb.get("name"), "cost_text": fb.get("cost_text"),
                                      "walk_m": fb.get("walk_m"), "lat": fb.get("lat"), "lon": fb.get("lon")} if fb else None
    if best and best.get("maybe_closed"):
        res["arrival"]["maybe_closed"] = True
        warnings.append("%s may have closed; check its Green P page before leaving%s" % (
            best.get("name") or "the parking lot",
            " (%s)" % (best.get("check") or {}).get("url") if (best.get("check") or {}).get("url") else ""))
    if arrive_by and rdep:
        res["arrival"]["leave_in_min"] = round(dc.elapsed_s(now, rdep, tzname, region) / 60.0)
    if slot:
        res["arrival"]["slot"] = {"start": slot[0], "end": slot[1]}
    if leave_at and at_door and leave_at <= at_door:
        warnings.append("--leave-at %s is before the typical arrival at the door (about %s); the return uses your "
                        "time" % (dc.fmt_clock(leave_at, ref_t), dc.fmt_clock(at_door, ref_t)))
    back = returns.get(id(rec_r))
    if back is not None:
        res["arrival"]["return"] = {"leave": back.get("depart"), "home": back.get("arrive"),
                                    "home_plan": back.get("arrive_plan"),
                                    "charge_min": round((back.get("charge_s") or 0) / 60.0),
                                    "border_min": round((back.get("border_s") or 0) / 60.0)}
        if back.get("charging_infeasible"):
            warnings.append("the drive back from %d%% needs charging that could not be planned (see CHARGING); the "
                            "round trip time leaves it out" % round(max(0.0, (back.get("charging") or {})
                                                                        .get("soc_start") or 0) * 100))

    # ---- charging
    ch = energy[id(rec_r)].get("charging")
    res["charging"] = ch
    res["charging_return"] = back.get("charging") if back is not None else None

    # ---- park and ride
    pr_cfg = costs()["park_and_ride"]
    gtha_boxes = next((a["boxes"] for a in dc.load_data("traffic")["areas"] if a["name"] == "GTHA"), [])
    in_gtha = dc.in_any(d_lat, d_lon, gtha_boxes)
    downtown = th.in_bbox(d_lat, d_lon, pr_cfg["downtown_toronto"])
    best_cost, park_note = pr_parking_cost((parking or {}).get("options"), best)
    trigger = None
    if args.park_and_ride == "on":
        trigger = "asked for"
    elif args.park_and_ride == "auto" and in_gtha:
        if downtown:
            trigger = "the destination is in downtown Toronto"
        elif best_cost is not None and best_cost > pr_cfg["trigger_parking_cad"]:
            trigger = "parking near the destination costs %s for the stay%s" % (
                dc.money(best_cost), " (%s)" % park_note if park_note else "")
    res["park_and_ride"] = None
    if trigger:
        en_r = energy[id(rec_r)]
        if en_r.get("kind") == "ev" and en_r.get("kwh") is not None and rec_r.get("km"):
            per_km = en_r["kwh"] / rec_r["km"] * home_rate   # the short drive to the station is charged at home
        else:
            per_km = (en_r["cost"] / rec_r["km"]) if rec_r.get("km") else 0.14
        one_way = en_r["cost"] + route_tolls(rec_r, args.transponder)
        if args.round_trip:
            drive_rt = rec["money"]
        else:   # the drive back at its own 407 band and direction (a one-way bridge toll is not doubled), the
                # same energy again, the account fee once
            drive_rt = 2 * (rec["money"] - rec["fee"]) + rec["fee"]
            try:
                pr_back = dr.reverse_for_return(rec_r, lot_leave(rec_r)[1], opts)
            except Exception as e:  # noqa: BLE001
                warnings.append("park and ride: the drive back could not be priced (%s), so driving there and "
                                "back is the drive there doubled" % e)
            else:
                fee = rec["fee"] or (fee_amt if count_fee and pr_back.get("etr") else 0.0)
                drive_rt = (rec["money"] - rec["fee"]) + en_r["cost"] + route_tolls(pr_back, args.transponder) + fee
        drive_rt += best_cost or 0.0
        ratio = (rec_r.get("plan_s") or 0) / float(rec_r.get("typical_s") or 1) if rec_r.get("typical_s") else 1.0
        ride_time = dc.add_s(deadline, -3600.0, tzname, region) if arrive_by else rdep
        if hasattr(th, "plan_traffic_factor"):   # the same planning-allowance rows as this drive, times the weather
            tf = th.plan_traffic_factor(ride_time, holidays)[0] * (opts.get("weather") or 1.0)
        else:
            tf = th.traffic_factor_for(ride_time) * max(1.0, ratio)
        hub_stay_h = stay_min / 60.0 + 2 * max(0.5, (rec_r.get("typical_s") or 1800) / 3600.0)
        pr = park_and_ride(o, d, deadline if arrive_by else rdep, arrive_by, per_km, hub_stay_h, one_way,
                           best_cost, warnings, pr_cfg["timeout_s"],
                           ctx={"drive_rt": drive_rt, "door": res["arrival"].get("at_door") if not arrive_by else None,
                                "drive_leave": rdep if arrive_by else None, "day": day, "traffic_factor": tf,
                                "safety_min": slack_min, "vot": args.value_of_time})
        pr["trigger"] = trigger
        pr["park_note"] = park_note
        res["park_and_ride"] = pr

    # ---- incidents
    inc = {"items": [], "pointer": None, "route": rec["letter"], "return": back is not None}
    if args.incidents == "auto":
        legs = [("out", rec_r.get("poly") or [], rdep, rec_r.get("arrive_plan") or rdep)]
        if back is not None and back.get("poly"):
            legs.append(("back", back["poly"], back.get("depart"), back.get("arrive_plan") or back.get("depart")))
        legs = [lg for lg in legs if lg[1] and lg[2]]
        inc["items"] = toronto_incidents(legs, tzname, region, warnings)
        if inc["items"]:
            sources.append("incidents: City of Toronto road restrictions")
        key = dc.env_key("ON511_API_KEY")
        if key:
            inc["items"] += on511_incidents(key, legs, tzname, region, warnings)
            sources.append("incidents: Ontario 511")
        else:
            inc["pointer"] = ("Highway closures and collisions: check https://511on.ca and Google Maps live traffic "
                              "before you leave (set ON511_API_KEY to list them here).")
    res["incidents"] = inc

    # ---- links (navigation goes to the parking, not the door, and follows the recommended route)
    any_tolls = any(r.get("has_tolls") for r in routes)
    toll_free = not rec_r.get("has_tolls") and any_tolls
    avoid = toll_free and not rec_r.get("bridges")   # avoid=tolls would also avoid the border crossing
    # a via point for a route Google would not pick by itself: an alternative, the 'without 407' route on a border
    # trip, the forced 407 option, or a toll-free route that keeps a toll bridge
    not_default = (rec_r.get("label") in ("alternative", "without 407") or rec_r.get("_forced_407")
                   or (toll_free and not avoid))
    via = alt_waypoint(rec_r, routes) if not_default else None
    links = {"google": google_link(o, target, avoid, via),
             # the other way: the route with tolls when the first link is the toll-free one (via or avoid=tolls)
             "google_alt": google_link(o, target, not toll_free) if any_tolls else None,
             "google_alt_text": ("with tolls" if toll_free else "avoid tolls") if any_tolls else None,
             "google_avoid_tolls": google_link(o, target, True) if any_tolls else None,
             "via": via, "waze": waze_link(target, avoid), "apple": apple_link(o, target, avoid),
             "walk": walk_link(target, d) if best else None, "target": target,
             "target_name": best["name"] if best else "the destination", "follows": rec["letter"]}
    if arrive_by:
        lot_by = dc.add_s(deadline, -(search_min + walk_min) * 60.0, tzname, region)
        links["google_timed"] = google_time_link(o, target, lot_by, True, avoid)
        links["timed_text"] = "Google Maps arrive by %s (desktop%s)" % (
            dc.fmt_clock(lot_by), "; Google picks its own route" if via else "")
    else:
        links["google_timed"] = google_time_link(o, target, rdep, False, avoid)
        links["timed_text"] = "Google Maps with this departure time (desktop%s)" % (
            "; Google picks its own route" if via else "")
    res["links"] = links

    # ---- sources
    if any(r.get("etr") for r in routes):
        yr = next((s.get("rates_year") for r in routes for s in r.get("etr") or [] if s.get("rates_year")), 2026)
        sources.insert(0, "407 ETR %s rate chart" % yr)
    if parking and parking.get("sources"):
        sources.append("parking: %s" % "; ".join(parking["sources"]))
    sources += parking_attributions(parking)
    if review_source(parking):
        sources.append(review_source(parking))
    sources.append(routing_source(fast.get("engine")))
    # an address was geocoded (Nominatim, Photon when it found the house) or a lat,lon end got a Nominatim label
    typed = not (th.LATLON_RE.match(args.origin or "") and th.LATLON_RE.match(args.dest or ""))
    if typed or any((i or {}).get("label") for i in (o_info, d_info)):
        sources.append("geocoding: %s, %s" % ("Nominatim/Photon" if typed else "Nominatim", OSM_CREDIT))
    if ch and ch.get("source"):
        sources.append("charging: %s" % ch["source"])
    rch_back = res.get("charging_return") or {}
    if rch_back.get("source") and rch_back.get("source") != (ch or {}).get("source"):
        sources.append("charging on the drive back: %s" % rch_back["source"])
    return res


# --------------------------------------------------------------------------- output

PAD = 11


def sec(label, first, rest=()):
    head = label.ljust(PAD - 1) + " " if len(label) < PAD else label + "  "
    lines = [head + first if first else head.rstrip()]
    lines += [" " * PAD + x for x in rest]
    return lines


def etr_text(r):
    segs = r.get("etr") or []
    if not segs:
        return ""
    km = sum(s.get("km") or 0.0 for s in segs)
    return " (%s to %s, %.0f km)" % (segs[0].get("entry", "?"), segs[-1].get("exit", "?"), km)


def band_text(s):
    """'weekday 07:00 to 09:30 band'; the night band wraps past midnight ('weekend 21:00 to 08:30 band')."""
    start, kind = s.get("band_start", ""), s.get("day_type", "")
    end = s.get("band_end")
    if not end:
        try:
            starts = dc.load_data("tolls")["etr407"]["bands"][kind]
            end = starts[(starts.index(start) + 1) % len(starts)]
        except (KeyError, ValueError, IndexError):
            end = None
    tor = " (Toronto time)" if s.get("band_toronto") else ""   # --tz for another zone: the 407 runs on Toronto's clock
    return ("%s %s to %s band%s" % (kind, start, end, tor)) if end else "%s band from %s%s" % (kind, start, tor)


def etr_lines(r, transponder, fee):
    out = []
    for s in r.get("etr") or []:
        out.append("407 ETR %s, %s: %s with a transponder, %s without%s" % (
            DIRS.get(s.get("direction"), ""), band_text(s), dc.money(s.get("toll_transponder") or 0.0),
            dc.money(s.get("toll_camera") or 0.0), " (+%s account fee that month)" % fee_text() if fee else ""))
        if s.get("note") and "rates" in s["note"] and "not published" in s["note"]:
            out.append("  (%s)" % s["note"])
    for b in r.get("bridges") or []:
        out.append("%s: %s" % (b.get("name"), b.get("text")))
    return out


def route_lines(row, res):
    tp = res["transponder"]
    ref = res.get("deadline") or res.get("depart_guess") or res.get("depart")
    title = LABELS.get(row.get("label"), (row.get("label") or "Route").capitalize())
    via = "407 ETR" + etr_text(row) if row.get("etr") else ", ".join((row.get("roads") or [])[:4]) or (
        "motorways" if (row.get("hwy_share") or 0) >= 0.5 else "local roads")
    arr = row.get("arrive")
    cs = row.get("charge_s") or 0
    bs = row.get("border_s") or 0
    if arr and (cs or bs):
        arr = dc.add_s(arr, cs + bs, res.get("timezone"), None)
    when = ("leave by %s, arrive %s" % (dc.fmt_clock(row["leave_by"], ref), dc.fmt_clock(arr, ref))
            if res["mode"] == "arrive_by" and row.get("leave_by") else "arrive %s" % dc.fmt_clock(arr, ref)
            if arr else "")
    if cs and when:
        when += " with %s of charging" % dc.fmt_dur(cs)
    if bs and when:
        when += "%s %d min at the border" % (" and" if cs else " with", round(bs / 60.0))
    head = "%s. %s via %s: %.0f km, about %s (allow %s), %s" % (
        row["letter"], title, via, row.get("km") or 0.0, dc.fmt_dur(row.get("typical_s") or 0),
        dc.fmt_dur(row.get("plan_s") or 0), when)
    en = row["energy"]
    etr_any = row.get("etr") or (row.get("return") or {}).get("etr")
    tp_txt = (" with a transponder" if tp else " without a transponder") if etr_any else ""
    fee_txt = ", incl. the %s account fee" % dc.money(row["account_fee"]) if row.get("account_fee") else ""
    total = "total ~%s%s%s" % (dc.money(row["money"]), tp_txt, fee_txt)
    if res["round_trip"]:
        total = "round trip total ~%s%s%s" % (dc.money(row["money"]), tp_txt, fee_txt)
    if not row.get("has_tolls") and not res["round_trip"] and not row.get("notes") and not row.get("delays"):
        return ["  " + head + ", %s, %s" % (en["text"].split(" (")[0], total)]
    lines = ["  " + head]
    # the fee note only when the total counts it (not with a transponder or --407-this-month)
    lines += ["     " + x for x in etr_lines(row, tp, bool(row.get("etr") and row.get("account_fee")))]
    lines.append("     " + en["text"])
    for dl in row.get("delays") or []:
        lines.append("     traffic: %s (+%s)" % (dl.get("text"), dc.fmt_dur(dl.get("delay_s") or 0)))
    for n in row.get("notes") or []:
        lines.append("     note: %s" % n)
    back = row.get("return")
    if back:
        chg = " with %s of charging" % dc.fmt_dur(back["charge_s"]) if back.get("charge_s") else ""
        if back.get("border_s"):
            chg += "%s %d min at the border" % (" and" if chg else " with", round(back["border_s"] / 60.0))
        bl = "return at %s on the same roads: about %s (allow %s), home %s%s" % (
            dc.fmt_clock(back["depart"], ref) if back.get("depart") else "", dc.fmt_dur(back.get("typical_s") or 0),
            dc.fmt_dur(back.get("plan_s") or 0), dc.fmt_clock(back["arrive"], ref) if back.get("arrive") else "?", chg)
        if back.get("tomtom_return") == "reused":   # the honest note the ROUTING line cannot carry for the return
            bl += "; time reused from the drive there, not TomTom traffic at the return time (%s)" % (
                back.get("tomtom_return_why") or "not re-queried")
        wb = back.get("weather") or {}
        if wb.get("text") and wb.get("text") != (res.get("weather") or {}).get("text"):
            bl += "; weather %s" % wb["text"]
        lines.append("     " + bl)
        lines += ["       " + x for x in etr_lines(back, tp, False)]
    lines.append("     " + total)
    return lines


def _num(x):
    if isinstance(x, (int, float)):
        return "%g" % round(x, 1)
    return x or "?"


def fmt_stay(minutes):
    m = int(round(minutes))
    return "%d h" % (m // 60) if m >= 60 and m % 60 == 0 else dc.fmt_dur(m * 60)


def _target_group(x):
    """0 a priced lot (not flagged, not a facility-like free lot), 1 a priced lot that may have closed, 2 the rest
    (facility-like free lots, unknown prices), 3 an unpriced lot that may have closed."""
    if x.get("cost") is None:
        return 3 if x.get("maybe_closed") else 2
    if x.get("unverified") and "facility" in (x.get("cost_text") or ""):
        return 2
    return 1 if x.get("maybe_closed") else 0


def targets(options):
    """Lots the plan may drive to, in the order to try them: the ranked order, with fitting walkable lots first
    and, among those, priced lots before priced lots that may have closed before the rest (parking.rank_key
    orders them the same way; this holds when a later pick or a review lot moves the list). A lot that may have
    closed is still a target when nothing priced and open is left; the plan then says so."""
    options = list(options or [])
    return sorted(options, key=lambda x: (0 if x.get("fits_stay", True) else 1, 1 if x.get("beyond_walk") else 0,
                                          _target_group(x)))


def fallback_lot(lots, best):
    """The next target after `best` that is not often full and not flagged as maybe closed, or None."""
    for x in lots or []:
        if x is not best and not x.get("often_full") and not x.get("maybe_closed") and x.get("name") != (best or {}).get("name"):
            return x
    return None


REVIEW_LOTS_MAX = 3   # review-only places added to the lot list
SHOWN_LOTS = 5        # lots printed in the PARKING section
REVIEW_HINT = "Google Maps reviews of a lot often quote recent rates"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _nodash(text):
    return re.sub("\\s*[\u2012-\u2015]\\s*", " - ", text or "").strip()


def _ym_text(ym):
    try:
        y, mo = (ym or "").split("-")[:2]
        return "%s %s" % (_MONTHS[int(mo) - 1], y)
    except (ValueError, IndexError):
        return ""


def review_rates(pr, mentions, limit=3, authors=True, links=False):
    """'$15 flat (Aug 2026, Jane D.), $20 per day (Mar 2026, Sam K.)': newest first, one per distinct rate. Each
    keeps its review's author and month (Google's terms: credit the author of review content shown); links=True
    adds the review's own Google Maps link (googleMapsUri of the review, else of its place) so the reader can open
    the individual review; a string `links` is the link used when a mention has none. A rate more than one
    review quotes says so ('Jane D. and 2 more reviews')."""
    seen, bits, counts = set(), [], {}
    for m in mentions or []:   # one mention is one review's report of that rate
        counts[pr.mention_text(m)] = counts.get(pr.mention_text(m), 0) + 1
    for m in sorted(mentions or [], key=lambda x: x.get("date") or "", reverse=True):
        t = pr.mention_text(m)
        if t in seen:
            continue
        seen.add(t)
        who, more = _nodash(m.get("author")) if authors else "", counts[t] - 1
        if more:
            who = ("%s and %d more review%s" % (who, more, "" if more == 1 else "s") if who
                   else "%d reviews" % counts[t])
        tag = [x for x in (_ym_text(m.get("date")), who,
                           "translated" if authors and m.get("translated") else "",
                           (m.get("review_url") or m.get("maps_url") or (links if isinstance(links, str) else ""))
                           if links else "") if x]
        bits.append("%s (%s)" % (t, ", ".join(tag)) if tag else t)
        if len(bits) >= limit:
            break
    return ", ".join(bits)


def review_text(pr, lot):
    """'Google reviews mention $9 weekends (Aug 2026, Jane D., <review link>), unverified', grouped by the Google
    place each review belongs to ('Google reviews of <place> mention ...' when that place is not the lot itself)."""
    groups, order = {}, []
    for m in lot.get("review_mentions") or []:
        place = _nodash(m.get("place") or lot.get("review_place") or "")
        if place not in groups:
            groups[place] = []
            order.append(place)
        groups[place].append(m)
    bits = []
    for place in order:
        who = "" if not place or place == lot.get("name") else " of %s" % place
        bits.append("Google reviews%s mention %s" % (who, review_rates(pr, groups[place],
                                                                       links=lot.get("review_url") or True)))
    return "; ".join(bits) + ", unverified" if bits else ""


def attach_reviews(parking, dest, best, warnings, today=None):
    """Rates mentioned in recent Google reviews of parking near the destination, when GOOGLE_PLACES_API_KEY is
    set (official Places API through place_reviews.py, one call at the parking radius; Google Maps itself is
    never scraped). Mentions go on the matching lot ("review_text", "review_url"), review-only places are added
    after the priced lots (at most REVIEW_LOTS_MAX, never ahead of the arrival lot), and a lot's check hint says
    when reviews mention a rate. parking["reviews"] is None without a key or on any error (no call is made
    without a key), else {"source", "attribution", "places", "with_rates", "matched", "added"}."""
    if not parking:
        return parking
    parking["reviews"] = None
    if not dc.env_key("GOOGLE_PLACES_API_KEY"):
        return parking
    pr = _mod("place_reviews")
    if pr is None:
        return parking
    try:
        got = pr.review_prices(dest[0], dest[1], parking.get("radius_m") or 800, warnings, today=today)
    except Exception as e:  # noqa: BLE001 (never let an optional source sink the plan; no message: it may hold the key)
        warnings.append("Google Places reviews skipped (%s)" % type(e).__name__)
        return parking
    if got is None:
        return parking
    options = list(parking.get("options") or [])
    extras = pr.match_lots(got.get("places") or [], options, dest=dest)[:REVIEW_LOTS_MAX]
    radius = parking.get("radius_m")
    added = []
    for x in extras:
        lot = {k: v for k, v in x.items() if not k.startswith("_")}
        lot["name"] = _nodash(lot.get("name")) or "Parking"
        lot["address"] = _nodash(lot.get("address"))
        lot["cost_text"] = "price unknown"
        lot["notes"] = lot["address"]
        lot["maybe_closed"] = False
        if radius and (lot.get("walk_m") or 0) > radius:
            lot["beyond_walk"] = True
        lot["check"] = {"why": "price from Google reviews only", "url": lot.get("review_url") or None,
                        "search": "%s parking rates" % re.sub(r"[()\"',;:]", " ", lot["name"]).strip()}
        added.append(lot)
    matched = []
    for lot in options:
        if not lot.get("review_mentions"):
            continue
        matched.append(lot.get("name"))
        if lot.get("check"):   # no rate quoted here: the review line under the lot names each rate's author
            c = dict(lot["check"])
            c["why"] = "%s; Google reviews mention a rate (see its review line)" % (c.get("why") or "check")
            lot["check"] = c
    for lot in options + added:
        if lot.get("review_mentions"):
            lot["review_text"] = review_text(pr, lot)
    if added:
        # after the priced lots, never ahead of the arrival lot, and within the five lots the text shows
        last = max([i for i, x in enumerate(options) if x.get("cost") is not None and not x.get("maybe_closed")]
                   or [-1])
        first = 1 if options and options[0] is best else 0
        at = max(min(last + 1, SHOWN_LOTS - 1), first)
        options = options[:at] + added + options[at:]
    parking = dict(parking, options=options)
    pk = _mod("parking")
    if pk is not None and hasattr(pk, "check_list"):
        parking["check"] = pk.check_list(options)
    places = got.get("places") or []
    parking["reviews"] = {"source": got.get("source"), "attribution": got.get("attribution"), "places": places,
                          "with_rates": sum(1 for p in places if p.get("mentions")), "matched": matched,
                          "added": [x["name"] for x in added]}
    return parking


def routing_source(engine):
    """SOURCES entry for the routing engine, with the OpenStreetMap credit the free engines need."""
    name = ENGINES.get(engine, engine or "?")
    if engine in ("valhalla", "osrm"):
        return "routing: %s, map data %s" % (name, OSM_CREDIT)
    return "routing: %s" % name


def parking_attributions(parking):
    """The licence statement of each city's open data behind a lot the text shows (the first SHOWN_LOTS), once
    each, in order: a lot from a city layer or Green P carries its city's "attribution" (parking.json)."""
    out = []
    for x in ((parking or {}).get("options") or [])[:SHOWN_LOTS]:
        if x.get("attribution") and x["attribution"] not in out:
            out.append(x["attribution"])
    return out


def review_source(parking):
    """The SOURCES entry for Google review rates (the attribution Google's terms require), or None. It claims
    review rates only when one of the lots the text shows (the first SHOWN_LOTS) carries them."""
    rv = (parking or {}).get("reviews")
    if not rv:
        return None
    shown = [x for x in ((parking or {}).get("options") or [])[:SHOWN_LOTS] if x.get("review_mentions")]
    if shown:
        return "parking review rates: Google Maps (Places API). %s" % (rv.get("attribution") or "").rstrip(".")
    if rv.get("matched") or rv.get("added"):
        return "parking review rates: Google Maps (Places API), none shown (rates found in reviews of %d of %d " \
               "places nearby, on lots past the first %d)" % (rv.get("with_rates") or 0, len(rv.get("places") or []),
                                                              SHOWN_LOTS)
    return "parking review rates: Google Maps (Places API), none in recent reviews of %d places nearby" % len(
        rv.get("places") or [])


def check_text(items):
    """'#1 Green P ...: 2019 price, search "..."; #3 ...: price unknown, https://...' (parking.check)."""
    out = []
    for c in items or []:
        how = ""
        if c.get("url") and c.get("search"):
            how = '%s or search "%s"' % (c["url"], c["search"])
        elif c.get("url"):
            how = c["url"]
        elif c.get("search"):
            how = 'search "%s"' % c["search"]
        if c.get("osm_url"):   # an OSM website tag: shown labelled, never the check link (parking.check_hint)
            osm = "OSM-supplied, unverified: %s" % c["osm_url"]
            how = "%s (%s)" % (how, osm) if how else osm
        out.append("#%d %s: %s%s" % (c.get("n") or 0, c.get("name") or "parking", c.get("why") or "check",
                                     ", " + how if how else ""))
    return "; ".join(out)


def lot_line(i, lot, radius_m=None):
    bits = [lot.get("cost_text") or ("free" if lot.get("free") else "price unknown")]
    bits.append("%d m walk" % round(lot.get("walk_m") or 0))
    if lot.get("beyond_walk"):
        bits.append("beyond the %s walk radius" % ("%d m" % radius_m if radius_m else "usual"))
    if not lot.get("fits_stay", True):
        bits.append(lot.get("unfit_reason") or ("max stay %s, shorter than your stay" % dc.fmt_dur(
            (lot.get("max_stay_min") or 0) * 60) if lot.get("max_stay_min") else "does not fit your stay"))
    if lot.get("maybe_closed"):
        bits.append("MAY HAVE CLOSED")
    if lot.get("often_full"):
        bits.append("often full")
    if lot.get("reservation"):
        bits.append("reserve: %s" % lot["reservation"])
    src = lot.get("confidence") or ""
    if lot.get("source") and src == "curated":
        src = "curated, %s" % re.sub(r"\s*\(curated\)$", "", lot["source"])
    elif lot.get("source"):
        src = "%s, %s" % (src, lot["source"]) if src else lot["source"]
    s = "  %d. %s: %s" % (i, lot.get("name") or "parking", ", ".join(bits))
    if src:
        s += " (%s)" % src
    if lot.get("notes"):
        s += "; " + lot["notes"]
    return s


def group_rules(rules):
    """[(place name or None, [rule text])]: consecutive rules that start with the same 'Place: ' share a header."""
    out = []
    for r in rules:
        head, sep, rest = r.partition(": ")
        if sep and len(head) <= 80 and out and out[-1][0] == head:
            out[-1][1].append(rest)
        elif sep and len(head) <= 80 and sum(1 for x in rules if x.startswith(head + ": ")) >= 2:
            out.append((head, [rest]))
        else:
            out.append((None, [r]))
    return out


def clip(text, n):
    """Cut at a word boundary, with '...' when shortened."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:.")
    return cut + "..."


def pr_lines(pr, res):
    by = res["mode"] == "arrive_by"
    first = "run because %s" % pr.get("trigger")
    if pr.get("park_note") and pr["park_note"] not in (pr.get("trigger") or ""):
        first += "; parking counted at %s (%s)" % (dc.money(pr["park_cost"]), pr["park_note"])
    if pr.get("traffic_factor"):
        first += "; hub drives planned like the drive (x%.2f traffic allowance, %g min spare)" % (
            pr["traffic_factor"], pr.get("safety_min") or 0)
    L = sec("PARK AND RIDE", first)
    opts = pr.get("options") or []
    for i, op in enumerate(opts, 1):
        L.append("  %d. Drive %.1f km to %s (%s), leave by %s, board %s at %s, arrive %s" % (
            i, op["km"] or 0.0, op["hub"], op["parking"]["text"], op["leave_by"], op["route"], op["board"],
            op["arrive"]))
        car_rt = op.get("car_rt", op["car_cost"])
        if (op.get("parking") or {}).get("price_unknown"):
            fares = ("fares %s" % dc.money(2 * op["fare"])) if op.get("fare") is not None else "fares not known%s" % (
                " for %s" % ", ".join(op["fare_missing"]) if op.get("fare_missing") else "")
            cost = "there and back: car %s + hub parking price unknown + %s (total not known)" % (
                dc.money(car_rt), fares)
        elif op.get("fare") is not None:
            cost = "there and back: car %s + fares %s = %s" % (
                dc.money(car_rt), dc.money(2 * op["fare"]), dc.money(op["rt_total"]))
        else:
            cost = "there and back: car %s + fares not known%s (check the agency)" % (
                dc.money(car_rt), " for %s" % ", ".join(op["fare_missing"]) if op.get("fare_missing") else "")
        park = dc.money(pr["park_cost"]) if pr.get("park_cost") is not None else "unknown"
        vs = "driving ~%s + parking %s" % (dc.money(pr["drive_rt"] - (pr.get("park_cost") or 0.0)), park)
        if pr.get("park_cost") is not None:
            vs += " = %s" % dc.money(pr["drive_rt"])
        when = ""
        if by and isinstance(op.get("leave_later_min"), (int, float)):
            lm = op["leave_later_min"]
            when = "; leave %s" % ("%s later than driving" % dc.fmt_dur(lm * 60) if lm > 0 else
                                   "%s earlier than driving" % dc.fmt_dur(-lm * 60) if lm < 0 else
                                   "when driving leaves")
        elif isinstance(op.get("later_min"), (int, float)):
            late = op["later_min"]
            when = "; at the door %s" % ("%s later than driving" % dc.fmt_dur(late * 60) if late > 0 else
                                         "%s sooner than driving" % dc.fmt_dur(-late * 60) if late < 0 else
                                         "when driving gets there")
        L.append("     %s, vs %s%s" % (cost, vs, when))
        b = op.get("backup")
        if b and by and b.get("leave_by"):
            L.append("     backup: leave by %s for %s at %s, arrive %s" % (b["leave_by"], b["route"], b["board"],
                                                                         b["arrive"]))
        elif b:
            L.append("     if you miss it: %s at %s, arrive %s" % (b["route"], b["board"], b["arrive"]))
    if opts:
        L.append("     The car stays at the hub: come back the same way to pick it up.")
    elif pr.get("candidates"):
        n = pr.get("checked") or pr["candidates"]
        L.append("  checked %d hub%s: driving is cheaper and faster" % (n, "" if n == 1 else "s"))
    else:
        L.append("  no hub with parking and transit reaches the destination for this trip")
    return L


def _inner(when, ref):
    """fmt_clock for a time already inside parentheses: '03:08 Sat 17 Oct'."""
    return dc.fmt_clock(when, ref).replace(" (", " ").replace(")", "")


def parking_lines(res, day):
    L = []
    pk = res.get("parking")
    if pk is not None:
        from_t, until = res.get("parking_from"), res.get("parking_until")
        rules = pk.get("place_rules") or []
        span = ", parked %s to %s" % (dc.fmt_clock(from_t, day), dc.fmt_clock(until, day)) if from_t and until else ""
        est = " (arrival estimated without a route)" if res.get("parking_estimate") else ""
        L += sec("PARKING", "near the destination, %s stay%s (%s), within %d m%s" % (
            fmt_stay(res.get("stay_min", res["parking_stay_min"])), span, fmt_stay(res["parking_stay_min"]),
            pk.get("radius_m") or 0, est))
        lots = pk.get("options") or []
        if not lots and pk.get("offline"):
            L.append("  no curated parking for this spot in the offline data; live parking sources were blocked")
        elif not lots:
            L.append("  no public parking found in the data; check signs or the venue's site")
        for i, lot_d in enumerate(lots[:SHOWN_LOTS], 1):
            L.append(lot_line(i, lot_d, pk.get("radius_m")))
            if lot_d.get("review_text"):
                L.append("       %s" % lot_d["review_text"])
        if pk.get("check"):
            L.append("     check before you go (prices and status may be out of date): %s%s" % (
                check_text(pk["check"]), "" if pk.get("reviews") else "; " + REVIEW_HINT))
        for head, items in group_rules(rules):
            if head:
                L.append("     %s:" % head)
                L += ["       " + x for x in items]
            else:
                L += ["     " + x for x in items]
        for rv in pk.get("reservations") or []:
            L.append("     reserve: %s%s%s" % (rv.get("name", ""), ": " + rv["text"] if rv.get("text") else "",
                                               " (%s)" % rv["url"] if rv.get("url") else ""))
        if pk.get("caveat"):
            L.append("     " + pk["caveat"])
    return L

def plain_text(lines):
    """The text output: names and text from feeds, maps, APIs and reviews (lot and charger names, closure and 511
    text, review snippets) can hold control or bidi-override characters, which print as spaces, and invisible
    characters, which are dropped (th.safe_text)."""
    return "\n".join(th.safe_text(x) for x in lines)


def render(res):
    L = []
    o = res["origin"].get("short") or short_label(res["origin"]["label"])
    d = res["destination"].get("short") or short_label(res["destination"]["label"])
    day = res["deadline"] or res.get("depart_guess") or res["depart"]
    hol = " (%s)" % res["holiday"] if res.get("holiday") else ""
    when = "arrive by %s" % dc.fmt_clock(res["deadline"]) if res["mode"] == "arrive_by" else \
        "depart %s" % dc.fmt_clock(res["depart"])
    rt = ", round trip" if res["round_trip"] else ""
    if res.get("timezone") and res["timezone"] != DEFAULT_TZ:
        rt += ", times %s" % res["timezone"]
    L += sec("DRIVE", "%s -> %s, %s%s, %s%s" % (o, d, day.strftime("%a %Y-%m-%d"), hol, when, rt))
    routes = res.get("routes") or []
    if not routes:
        if res.get("car"):
            first, rest = car_lines(res)
            L += sec("CAR", first, rest)
        L += sec("ROUTES", "none found")
        L += parking_lines(res, day)
        L += sec("LINKS", "Google Maps %s" % res["links"]["google"])
        if res["warnings"]:
            L += sec("WARNINGS", res["warnings"][0], res["warnings"][1:])
        return plain_text(L)
    eng = routes[0].get("engine")
    how = ("TomTom live traffic" if eng == "tomtom" else
           "%s free-flow times x time-of-day traffic (no live traffic; set TOMTOM_API_KEY)" % ENGINES.get(eng, eng))
    wx = res.get("weather") or {}
    L += sec("ROUTING", "%s; weather: %s" % (how, wx.get("text") or "unknown"),
             [routes[0]["factor_note"]] if routes[0].get("factor_note") else [])
    if res.get("car"):
        first, rest = car_lines(res)
        L += sec("CAR", first, rest)
    L.append("ROUTES")
    for row in routes:
        L += route_lines(row, res)
    tr = res["trade_off"]
    L += sec("TRADE-OFF", tr["text"], [tr["fee_note"]] if tr.get("fee_note") else [])

    a = res["arrival"]
    lot = a["lot"] or "the destination"
    lot_note = ""
    if a.get("maybe_closed"):
        lot_note += " (may have closed, check first)"
    walk = ", walk %d min to the door" % round(a["walk_min"]) if a["lot"] else ""
    charge = " + charging %d" % a["charge_min"] if a.get("charge_min") else ""
    if a.get("border_min"):
        charge += " + border %d" % a["border_min"]
    border_txt = ("Includes %d min at the border (an allowance; check CBSA and CBP wait times before you leave)."
                  % a["border_min"]) if a.get("border_min") else None
    if res["mode"] == "arrive_by":
        first = "Leave by %s to be at the door by %s (planned on the allow time %s + parking %d + walk %d%s + %d min " \
                "spare)%s." % (dc.fmt_clock(a["leave"], day), dc.fmt_clock(a["deadline"], day),
                               dc.fmt_dur(a["plan_s"] or 0), round(a["search_min"]), round(a["walk_min"]), charge,
                               a["slack_min"], lot_note)
        lim = a.get("leave_in_min")
        if isinstance(lim, (int, float)) and 0 <= lim < 5:
            first += " That is %s: leave now." % ("now" if lim < 1 else "in %d min" % lim)
        rest = ["Typical drive: arrive at %s about %s." % (lot, dc.fmt_clock(a["at_lot"], day))] if a.get("at_lot") \
            else []
    else:
        first = "Leave %s, arrive at %s%s about %s (allow until %s), park ~%d min%s." % (
            dc.fmt_clock(a["leave"], day), lot, lot_note, dc.fmt_clock(a["at_lot"], day),
            _inner(a["at_lot_plan"], day), round(a["search_min"]), walk)
        rest = []
        if a.get("charge_min"):
            rest.append("Includes %d min of charging stops." % a["charge_min"])
    if a.get("often_full"):
        fb = a.get("fallback")
        full = "%s is often full" % lot
        if fb:
            full += "; fallback %s (%s, %d m walk)" % (fb.get("name") or "the next lot", fb.get("cost_text") or "price unknown",
                                                       round(fb.get("walk_m") or 0))
        rest.insert(0, full + ".")
    if border_txt:
        rest.append(border_txt)
    if a.get("slot"):
        s = a["slot"]
        rest.append("Book the %s to %s parking slot (arrive with at least 2 h left in it)." % (
            dc.fmt_clock(s["start"]), dc.fmt_clock(s["end"])))
    if a.get("return"):
        r = a["return"]
        rest.append("Return: leave %s about %s%s, home about %s (allow until %s)%s%s." % (
            lot, dc.fmt_clock(r["leave"], day), " (%s)" % a["cap"] if a.get("cap") else "",
            dc.fmt_clock(r["home"], day) if r.get("home") else "?",
            _inner(r["home_plan"], day) if r.get("home_plan") else "?",
            ", with %d min of charging" % r["charge_min"] if r.get("charge_min") else "",
            ", %d min at the border" % r["border_min"] if r.get("border_min") else ""))
    elif a.get("cap"):
        rest.append("Leave %s by %s (%s)." % (lot, dc.fmt_clock(a["lot_leave"], day), a["cap"]))
    L += sec("ARRIVAL", first, rest)

    L += parking_lines(res, day)

    pr = res.get("park_and_ride")
    if pr and pr.get("ran"):
        L += pr_lines(pr, res)

    ch = res.get("charging")
    if ch is not None:
        n_st = len(ch.get("stops") or [])
        start = "from %d%%%s" % (round((ch.get("soc_start") or 0) * 100),
                                 " (assumed; pass --soc)" if ch.get("soc_assumed") else "")
        if ch.get("needed") and ch.get("feasible") is False:
            first = ("%d stop(s) planned %s, but no reachable compatible fast charger was found for the rest of the "
                     "trip (see WARNINGS): plan Level 2 stops" % (n_st, start) if n_st else
                     "charging is needed %s, but no reachable compatible fast charger was found (see WARNINGS): "
                     "leave with more charge or plan Level 2 stops" % start)
        elif ch.get("needed"):
            first = "%d stop%s %s%s, %s extra, public charging ~%s; arrive with %d%%" % (
                n_st, "" if n_st == 1 else "s", start,
                " (estimate, charger lists unavailable)" if ch.get("estimated") else "",
                dc.fmt_dur(ch.get("extra_s") or 0), dc.money(ch.get("public_cost") or 0),
                round((ch.get("arrive_soc") or 0) * 100))
        else:
            first = "no stop needed %s; arrive with about %d%%" % (start, round((ch.get("arrive_soc") or 0) * 100))
        L += sec("CHARGING", first)
        for i, st in enumerate(ch.get("stops") or [], 1):
            L.append("  %d. %s (%s, %s kW%s) at km %.0f: %d%% to %d%%, %d min, ~%s (%s)%s" % (
                i, st.get("name"), st.get("network") or "?", _num(st.get("max_kw")),
                ", %s ports" % _num(st["ports"]) if st.get("ports") else "", st.get("km_along") or 0, round((st.get("arrive_soc") or 0) * 100),
                round((st.get("depart_soc") or 0) * 100), round(st.get("charge_min") or 0),
                dc.money(st.get("cost") or 0), st.get("price_text") or st.get("price_source") or "",
                ", +%d min detour" % st["detour_min"] if st.get("detour_min") else ""))
        if ch.get("return_soc_needed"):
            L.append("     to drive home without charging, leave the destination with %d%%"
                     % round(ch["return_soc_needed"] * 100))
        rp = res.get("charging_return")
        if rp:
            stops = rp.get("stops") or []
            back_from = "%d%%%s" % (round(max(0.0, rp.get("soc_start") or 0) * 100),
                                    " (charged at the destination)" if rp.get("charge_at_destination") else "")
            if rp.get("feasible") is False:
                L.append("     drive back from %s: no reachable fast charger found for the whole way (see "
                         "WARNINGS); charge at the destination" % back_from)
            elif stops:
                L.append("     drive back from %s: %d stop%s%s, %s extra, public charging ~%s (in the round trip "
                         "total): %s" % (
                             back_from, len(stops), "" if len(stops) == 1 else "s",
                             " (estimate)" if rp.get("estimated") else "", dc.fmt_dur(rp.get("extra_s") or 0),
                             dc.money(rp.get("public_cost") or 0),
                             "; ".join("%s at km %.0f, %d min" % (s.get("name"), s.get("km_along") or 0,
                                                                  round(s.get("charge_min") or 0)) for s in stops)))
                if rp.get("stretch_note"):   # stops that charge above the charge limit, and to how much
                    L.append("     drive back: %s%s" % (rp["stretch_note"][0].lower(), rp["stretch_note"][1:]))
        for dch in (ch.get("dest_chargers") or [])[:3]:
            L.append("     near the destination: %s (%s, %s kW, %.1f km)%s" % (
                dch.get("name"), dch.get("network") or "?", _num(dch.get("max_kw")), dch.get("km") or 0,
                ", " + dch["price_text"] if dch.get("price_text") else ""))
        for n in ch.get("notes") or []:
            L.append("     " + n)

    inc = res.get("incidents") or {}
    if inc.get("items") or inc.get("pointer"):
        rows = []
        legs_txt = {"out": "on the way there", "back": "on the way back"}
        if inc.get("items"):
            rows.append("on route %s (the recommended route)%s:" % (
                inc.get("route") or res.get("recommended") or "?", ", there and back" if inc.get("return") else ""))
        for x in inc.get("items") or []:
            what = x.get("what") or x["type"].replace("_", " ").lower()
            lg = x.get("legs") or []
            where = ""
            if inc.get("return"):
                where = ", both ways" if len(lg) > 1 else ", " + legs_txt.get(lg[0], lg[0]) if lg else ""
            rows.append("%s: %s (%s%s%s%s)%s" % (
                x["source"], x["name"] or x["road"], what, ", impact %s" % x["impact"] if x.get("impact") else "",
                ", until %s" % x["until"] if x.get("until") else "", where,
                "; " + clip(x["description"], 110) if x.get("description") else ""))
        if inc.get("pointer"):
            rows.append(inc["pointer"])
        L += sec("INCIDENTS", rows[0], rows[1:])

    ln = res["links"]
    parts = ["Google Maps %s" % ln["google"]]
    alt = ln.get("google_alt")
    if alt:
        parts.append("%s %s" % (ln.get("google_alt_text") or "avoid tolls", alt))
    parts += ["Waze %s" % ln["waze"], "Apple Maps %s" % ln["apple"]]
    first = "Navigate to %s on route %s: %s" % ("the parking" if ln.get("walk") else "the destination",
                                                 ln.get("follows") or res.get("recommended"), " | ".join(parts))
    timed = "%s: %s" % (ln["timed_text"], ln["google_timed"])
    rest = [timed + ("; walk from the lot: %s" % ln["walk"] if ln.get("walk") else "")]
    if ln.get("via"):
        rest.append("The first Google link goes via a point on route %s so Google follows it; Waze and Apple Maps "
                    "pick their own way." % (ln.get("follows") or ""))
    L += sec("LINKS", first, rest)
    if res["warnings"]:
        seen, ws = set(), []
        for w in res["warnings"]:
            if w not in seen:
                seen.add(w)
                ws.append(w)
        L += sec("WARNINGS", ws[0], ws[1:])
    if res.get("sources"):
        L += sec("SOURCES", "; ".join(res["sources"]))
    return plain_text(L)


def to_json(res):
    if isinstance(res.get("car"), dict):
        res = dict(res, car=car_json(res["car"]))
    return json.dumps(res, indent=2, default=_iso)


def _num_arg(lo=None, hi=None, lo_open=False, cast=float):
    def check(s):
        try:
            v = cast(s)
        except ValueError:
            raise argparse.ArgumentTypeError("%r is not a number" % s)
        if v != v or v in (math.inf, -math.inf):   # NaN, inf, 1e999 (isfinite overflows on a huge int)
            raise argparse.ArgumentTypeError("%r is not a number" % s)
        if lo is not None and (v < lo or (lo_open and v == lo)):
            raise argparse.ArgumentTypeError("must be %s %g" % ("above" if lo_open else "at least", lo))
        if hi is not None and v > hi:
            raise argparse.ArgumentTypeError("must be at most %g" % hi)
        return v
    return check


def build_parser():
    ap = argparse.ArgumentParser(description="Plan a drive: time-of-day traffic model (live traffic with "
                                             "TOMTOM_API_KEY), 407 tolls versus time, fuel or charging, parking, "
                                             "arrival planning and park and ride.")
    pos = _num_arg(0, lo_open=True)
    ap.add_argument("--origin", help="'lat,lon' or an address (required)")
    ap.add_argument("--dest", help="'lat,lon' or an address (required)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--depart", default="now", help="'YYYY-MM-DD HH:MM', 'HH:MM' or 'now' (local time)")
    g.add_argument("--arrive-by", help="be at the door by 'YYYY-MM-DD HH:MM' or 'HH:MM'")
    ap.add_argument("--tz", help="IANA time zone for the times you give and the times printed, for an origin on "
                                 "another clock (default: the region file's, America/Toronto)")
    s = ap.add_mutually_exclusive_group()
    s.add_argument("--stay-hours", type=pos, help="hours at the destination (default 2)")
    s.add_argument("--leave-at", help="when you leave the destination: 'HH:MM' on the trip day, or "
                                      "'YYYY-MM-DD HH:MM'")
    ap.add_argument("--round-trip", action="store_true", help="also price the drive back after the stay")
    ap.add_argument("--car", help="your car as 'YEAR MAKE MODEL' (e.g. '2022 Toyota Corolla'): its fuel or energy use "
                                  "from the NRCan Fuel Consumption Ratings")
    ap.add_argument("--save-car", action="store_true", help="keep this run's car (--car and any --l-per-100km, "
                    "--vehicle, --ev-model, --battery-kwh, --max-dc-kw, --nacs-adapter) for later runs")
    ap.add_argument("--forget-car", action="store_true", help="delete the saved car (then plan if --origin and "
                    "--dest are given)")
    ap.add_argument("--no-saved-car", action="store_true", help="ignore the saved car for this run")
    ap.add_argument("--vehicle", choices=["gas", "hybrid", "diesel", "ev"], default=None,
                    help="gas (default), hybrid, diesel or ev")
    ap.add_argument("--car-class", choices=["compact", "midsize", "small-suv", "suv", "pickup"], default=None,
                    help="consumption class (default small-suv)")
    ap.add_argument("--l-per-100km", type=pos, help="your car's consumption")
    ap.add_argument("--fuel-price", type=pos, help="fuel price in $/L (or c/L from 20 up)")
    ap.add_argument("--ev-model", help="EV preset (ioniq5, ev9, equinox, model-y, model-3, niro, mach-e, leaf for "
                                       "the 2026 LEAF, leaf-plus for the 2019 to 2025 LEAF PLUS, generic)")
    ap.add_argument("--battery-kwh", type=pos)
    ap.add_argument("--soc", type=_num_arg(0, 100), help="battery percent at departure, 0 to 100 (default 90)")
    ap.add_argument("--max-dc-kw", type=pos)
    ap.add_argument("--kwh-per-100km", type=pos)
    ap.add_argument("--nacs-adapter", action="store_true", help="the EV can use Tesla Superchargers with an adapter")
    ap.add_argument("--transponder", action="store_true", help="you have a 407 ETR transponder")
    ap.add_argument("--407-this-month", dest="etr_this_month", action="store_true",
                    help="you already drive the 407 this month without a transponder, so the monthly account fee "
                         "is not counted")
    ap.add_argument("--value-of-time", type=_num_arg(0), default=30.0, help="$ per hour of your time (default 30)")
    ap.add_argument("--weather", choices=["auto", "clear", "rain", "snow"], default="auto")
    ap.add_argument("--temp-c", type=_num_arg(), help="outside temperature (default forecast or month normal)")
    ap.add_argument("--provider", choices=["auto", "tomtom", "valhalla", "osrm"], default="auto")
    ap.add_argument("--max-walk-m", type=_num_arg(0, lo_open=True, cast=int), default=800,
                    help="max walk from parking to the door")
    ap.add_argument("--height-m", type=pos, help="vehicle height, for garage clearance")
    ap.add_argument("--park-and-ride", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--incidents", choices=["auto", "off"], default="auto")
    ap.add_argument("--allow-street-match", action="store_true", help="plan even when an address with a house "
                    "number only matched the street")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check-network", action="store_true", help="report which hosts the driving planner needs "
                    "and whether this environment can reach them, then exit")
    return ap


def car_only(args):
    """--save-car or --forget-car without a trip: print the CAR line (or the forget note) and exit."""
    validate(args)
    if args.forget_car:
        msg = forget_car()
        print(json.dumps({"car": {"forgot": True, "notes": [msg]}}, indent=2) if args.json else plain_text([msg]))
        return 0
    warnings = []
    car = resolve_car(args, warnings)
    res = {"car": car, "warnings": warnings}
    if args.json:
        print(json.dumps({"car": car_json(car), "warnings": warnings}, indent=2, default=_iso))
        return 0
    first, rest = car_lines(res)
    L = sec("CAR", first, rest)
    if warnings:
        L += sec("WARNINGS", warnings[0], warnings[1:])
    print(plain_text(L))
    return 0


def main(argv=None):
    try:
        return _main(argv)
    except th.DataFileError as e:   # a broken install (costs, traffic or tolls file): one line, not a traceback
        raise SystemExit(str(e))


def _main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = build_parser()
    args = ap.parse_args(argv)
    routing_hosts()   # a TRIP_PLANNER_VALHALLA_URL or TRIP_PLANNER_OSRM_URL that is not a URL stops here, in one line
    if args.check_network:
        hosts = drive_hosts()
        status = th.check_network(hosts)
        for h in hosts:
            print("%-50s %s" % (h, status[h]))
        blocked = [h for h in hosts if status[h] != "ok"]
        print("\n%d of %d reachable.%s" % (len(hosts) - len(blocked), len(hosts),
                                            " Allow the blocked ones to run the live planner." if blocked else ""))
        if any(status[h].startswith("tls failed") for h in hosts):
            print(th.TLS_HELP)
        return 1 if blocked else 0
    if not args.origin or not args.dest:
        if args.forget_car or args.save_car:   # car only: save or forget, then exit
            return car_only(args)
        ap.error("--origin and --dest are required")
    res = plan(args)
    print(to_json(res) if args.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
