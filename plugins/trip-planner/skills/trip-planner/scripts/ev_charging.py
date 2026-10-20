"""EV charging stops for drive_plan.py: car presets, DC fast chargers along the route and a greedy stop plan.

Standard library only. Chargers come from the NLR (ex-NREL) Alternative Fuel Stations API, which carries US DOE
and NRCan station data, queried with country=all (US and Canada; the API default is US only), with OpenStreetMap
via Overpass as a fallback. Car presets,
network prices and the consumption and charge-time model live in references/driving/ev.json.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse

import drive_common as dc

th = dc.th
log = th.log

NLR_BASE = "https://developer.nlr.gov/api/alt-fuel-stations/v1/"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_H = 24.0
ROUTE_POINTS = 300          # NLR LINESTRING vertices
ROUTE_MILES = 2             # NLR search distance from the line
DEST_MILES = 5
NEAR_LIMIT = 200            # nearest.json sorts by distance; 200 is the most it returns in one call
NEAR_DC_LIMIT = 20
NLR_COUNTRY = "all"        # US and Canada; the API default is US only
DC_CODES = ("J1772COMBO", "CHADEMO", "TESLA")
MIN_ADD = 0.03              # a stop must add at least this much charge (3 points), else it is no stop
MIN_TOPUP = 0.005           # ... unless a shorter top-up is the only way on (see _pick)
OSM_SOCKETS = {"type1_combo": "J1772COMBO", "chademo": "CHADEMO", "nacs": "TESLA", "tesla_supercharger": "TESLA",
               "type1": "J1772", "tesla_destination": "TESLA_L2"}
OSM_DC_BRANDS = ("tesla supercharger", "supercharger", "ivy", "electrify canada", "petro-canada", "petro canada",
                 "on the run", "circle k", "couche-tard", "flo fast", "jule")


def _ev():
    return dc.load_data("ev")


def _compact(s):
    return re.sub(r"[\s_\-+.]", "", str(s).lower())


def _interp(points, x):
    pts = sorted(points)
    if x <= pts[0][0]:
        return float(pts[0][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / float(x1 - x0)
    return float(pts[-1][1])


# --------------------------------------------------------------------------- car

def car_profile(model=None, battery_kwh=None, max_dc_kw=None, kwh_per_100km=None, nacs_adapter=False):
    """Preset from ev.json with overrides. `nacs_adapter` means the car carries the adapter for the other DC
    standard: a CCS1 car can then use NACS (Tesla) plugs, a NACS car can use CCS1 plugs."""
    d = _ev()
    cars = d["cars"]
    key = "generic"
    if model:
        want = _compact(model)
        key = None
        for k in cars:
            if _compact(k) == want:
                key = k
        if key is None:
            for a, k in d.get("aliases", {}).items():
                if _compact(a) == want:
                    key = k
        if key is None:
            raise SystemExit("Unknown EV model %r. Presets: %s (or give --battery-kwh, --max-dc-kw and "
                             "--kwh-per-100km with the generic preset)." % (model, ", ".join(sorted(cars))))
    p = cars[key]
    port = d["ports"][p["port"]]
    conns = set(port["connectors"])
    if nacs_adapter:
        conns |= set(port["with_adapter"])
    hwy, city = float(p["kwh_100km_hwy"]), float(p["kwh_100km_city"])
    over = []
    if kwh_per_100km:
        ratio = city / hwy
        hwy = float(kwh_per_100km)
        city = round(hwy * ratio, 1)
        over.append("%.1f kWh/100 km" % hwy)
    batt = float(battery_kwh) if battery_kwh else float(p["battery_kwh"])
    if battery_kwh:
        over.append("%.0f kWh usable" % batt)
    mx = float(max_dc_kw) if max_dc_kw else float(p["max_dc_kw"])
    if max_dc_kw:
        over.append("%.0f kW max DC" % mx)
    name = p["name"] + (" (your figures: %s)" % ", ".join(over) if over else "")
    if nacs_adapter and port["with_adapter"]:
        name += ", with %s" % port["adapter"]
    return {"name": name, "key": key, "battery_kwh": batt, "max_dc_kw": mx, "kwh_100km_hwy": hwy,
            "kwh_100km_city": city, "f_10_80": float(p["f_10_80"]), "connectors": conns, "tesla": bool(p["tesla"]),
            "port": p["port"], "nacs_adapter": bool(nacs_adapter), "confidence": p.get("confidence", ""),
            "notes": p.get("notes", ""), "source": p["source"]}


# --------------------------------------------------------------------------- model

def temp_factor(temp_c):
    return _interp(_ev()["model"]["temp_factor"]["points"], temp_c)


def speed_factor(speed_kmh):
    return _interp(_ev()["model"]["speed_factor"]["points"], speed_kmh)


def consumption(car, temp_c, speed_kmh=105, hwy_share=0.8):
    """kWh/100 km for the trip: NRCan highway x speed factor on the highway share, NRCan city on the rest,
    both x the temperature factor."""
    hs = max(0.0, min(1.0, hwy_share))
    hwy = car["kwh_100km_hwy"] * speed_factor(speed_kmh)
    return (hs * hwy + (1 - hs) * car["kwh_100km_city"]) * temp_factor(temp_c)


def month_normal(month):
    return _ev()["temp_normals_c"]["months"][month - 1]


def charge_minutes(car, kwh, station_kw, temp_c=None):
    """Minutes plugged in to add `kwh` (10 to 80% window), plus the plug-in allowance. Average power is the
    lower of what the charger gives (charger kW x charger_limited_f) and what the car takes (its peak x
    f_10_80), so a faster charger is never predicted slower."""
    m = _ev()["model"]
    if kwh <= 0:
        return 0.0
    station = station_kw or m["assumed_kw_when_missing"]
    avg_kw = max(1.0, min(station * m["charger_limited_f"], car["max_dc_kw"] * car["f_10_80"]))
    t = kwh / avg_kw * 60.0
    if temp_c is not None:
        w = m["winter_charge_time"]
        if temp_c < -10:
            t *= w["below_minus_10"]
        elif temp_c < 0:
            t *= w["below_0"]
    return t + m["plug_in_min"]


def billed_minutes(cmin):
    """Minutes a per-minute or per-hour network bills: the session without the plug-in allowance."""
    return max(0.0, cmin - _ev()["model"]["plug_in_min"])


def _deg(t):
    """Temperature as a whole number, never '-0'."""
    return "%d" % (int(round(t)) + 0)


# --------------------------------------------------------------------------- prices

_NUM = r"\$\s*(\d+(?:\.\d+)?)"
_FEE_WORDS = r"(?:connection|session|plug-?in|start(?:ing)?)\s*fee"
_DC_RE = re.compile(r"dc\s*fast|dcfc|\bdc\b|level 3|\bl3\b|fast charg")
_L2_RE = re.compile(r"level 2|\bl2\b")
_IDLE_RE = re.compile(r"idle|overstay|over-stay|parking|congestion")   # never the charging rate
_LATER_RE = re.compile(r"grace|after\s+\d+\s*min")   # a rate that starts later: not the rate when another is listed


def parse_price(text):
    """NLR ev_pricing text -> {"model": free|per_kwh|per_minute|per_hour, "rate", "fee", "first", "pairs"} or None."""
    if not text:
        return None
    t = text.lower().replace("\u00a0", " ")
    segs = [x for x in re.split(r"[;\n]|\.\s", t) if x.strip()]
    # each clause (a segment split also on commas and 'and') belongs to the last charging level named in its segment
    dcs, l2s, rest = [], [], []
    for seg in segs:
        tier = None
        for cl in re.split(r",|\band\b", seg):
            if not cl.strip():
                continue
            d = _DC_RE.search(cl)
            if d:
                tier = "dc"
                if _L2_RE.search(cl) and "$" in cl[d.start():]:   # 'Level 2 $1/hr DC fast $15/hr': the DC part
                    cl = cl[d.start():]
            elif _L2_RE.search(cl):
                tier = "l2"
            (dcs if tier == "dc" else l2s if tier == "l2" else rest).append(cl.strip())
    if any("$" in x or "free" in x for x in dcs):   # DC clauses with no price: the price is for every level
        t = "; ".join(dcs)
    elif l2s and rest:
        t = "; ".join(rest)
    if (re.match(r"\s*free\b", t) and "$" not in t) or t.strip() in ("$0", "$0.00", "no charge"):
        return {"model": "free", "rate": 0.0, "fee": 0.0, "first": None, "pairs": []}
    fee = re.search(_NUM + r"\s*" + _FEE_WORDS, t) or re.search(_FEE_WORDS + r"\s*(?:of|is|:)?\s*" + _NUM, t)
    out = {"fee": float(fee.group(1)) if fee else 0.0, "first": None, "pairs": []}
    first = re.search(_NUM + r"\s*(?:per hour\s*)?(?:for\s*)?(?:the\s*)?first hour", t)
    if first:
        out["first"] = float(first.group(1))
    elif re.search(r"free\s*(?:for\s*)?(?:the\s*)?first hour", t):
        out["first"] = 0.0
    rates = [x for x in re.split(r"[;,\n]|\.\s", t) if not _IDLE_RE.search(x)]   # time rates: never an idle fee
    tr = "; ".join(x for x in rates if not _LATER_RE.search(x))
    if not re.search(_NUM + r"\s*(?:per|/)\s*(?:min(?:ute)?|hour|hr|h)\b", tr):
        tr = "; ".join(rates)
    pairs = re.findall(_NUM + r"\s*(?:per|/)\s*min(?:ute)?\s*(?:@|at)\s*(\d+)\s*kw", tr)
    kwh = re.search(_NUM + r"\s*(?:per|/)\s*kwh", t)
    pmin = re.search(_NUM + r"\s*(?:per|/)\s*min(?:ute)?\b", tr)
    hours = re.findall(_NUM + r"\s*(?:per|/)\s*(?:hour|hr|h)\b", tr)
    flat = re.search(_NUM + r"\s*(?:flat|per session|/\s*session|a session|per charge)", t)
    if flat and not fee:
        out["fee"] = float(flat.group(1))
    if kwh:
        out.update(model="per_kwh", rate=float(kwh.group(1)))
        # a time charge on top of the energy charge ("$0.30 per kWh plus $0.10 per minute"), not an idle fee
        for seg in re.split(r"[;,\n]|\.\s", t):
            mm = re.search(_NUM + r"\s*(?:per|/)\s*min(?:ute)?\b", seg)
            if mm and not re.search(r"idle|overstay|parking|after|grace|congestion|level 2|\bl2\b", seg):
                out["per_min"] = float(mm.group(1))
                break
    elif pairs:
        out.update(model="per_minute", rate=float(pairs[0][0]), pairs=[(float(a), float(b)) for a, b in pairs])
    elif pmin:
        out.update(model="per_minute", rate=float(pmin.group(1)))
    elif hours:
        out.update(model="per_hour", rate=float(hours[-1]))
    elif out["first"] is not None:
        out.update(model="per_hour", rate=out["first"])
    elif flat:   # "$25 flat rate": one fee per session
        out.update(model="per_kwh", rate=0.0)
    else:
        return None
    return out


def _amt(x):
    s = "%.2f" % x if x >= 0.10 or x == 0 else "%.3f" % x
    return "$" + (s[:-3] if s.endswith(".00") else s)


def _pair_rate(p, kw):
    if p.get("pairs") and kw:
        return min(p["pairs"], key=lambda ab: abs(ab[1] - kw))
    return None


def price_short(p, kw=None):
    """Short text for a parsed price, e.g. '$0.48/kWh', '$0.38/min at 100 kW + $1 fee', '$5 first hour, then $24/h'."""
    if p["model"] == "free":
        return "free"
    rate = p["rate"]
    if p["model"] == "per_kwh":
        s = "%s per session" % _amt(p["fee"]) if rate == 0 and p.get("fee") else "%s/kWh" % _amt(rate)
        if p.get("per_min"):
            s += " + %s/min" % _amt(p["per_min"])
    elif p["model"] == "per_minute":
        pr = _pair_rate(p, kw)
        s = "%s/min at %d kW" % (_amt(pr[0]), pr[1]) if pr else "%s/min" % _amt(rate)
    else:
        s = "%s/h" % _amt(rate)
    if p.get("first") is not None and p["model"] != "per_kwh":
        s = "%s first hour, then %s" % ("free" if not p["first"] else _amt(p["first"]), s)
    if p.get("fee") and not (p["model"] == "per_kwh" and rate == 0):
        s += " + %s fee" % _amt(p["fee"])
    return s


def _price_cost(p, kwh, minutes, kw):
    if p["model"] == "free":
        return 0.0
    rate = p["rate"]
    pr = _pair_rate(p, kw)
    if pr:
        rate = pr[0]
    fee = p.get("fee") or 0.0
    if p["model"] == "per_kwh":
        return fee + rate * kwh + (p.get("per_min") or 0.0) * minutes
    if p["model"] == "per_minute":
        if p.get("first") is not None:
            return fee + p["first"] + rate * max(0.0, minutes - 60)
        return fee + rate * minutes
    if p.get("first") is not None:
        return fee + p["first"] + rate * max(0.0, minutes - 60) / 60.0
    return fee + rate * minutes / 60.0


def network_entry(network, car_is_tesla=True, name=""):
    nets = _ev()["networks"]
    by = {n["key"]: n for n in nets}
    low = (network or "").lower()
    if "petro-canada" in (name or "").lower() or "petro canada" in (name or "").lower():
        return by["petro_canada"]
    if low == "tesla" or "supercharger" in (name or "").lower():
        return by["tesla"] if car_is_tesla else by["tesla_non_tesla"]
    for n in nets:
        if any(low == m.lower() for m in n["match"]):
            return n
    hay = (low + " " + (name or "").lower())
    for n in nets:
        if any(len(m) > 3 and m.lower() in hay for m in n["match"]):
            return n
    return by["other"]


def is_tesla_site(st):
    return (st.get("network_code") or "").lower() == "tesla" or "supercharger" in (st.get("name") or "").lower()


def price_network(station, car):
    """The ev.json network entry used when a stop is priced by estimate, or None when the station lists a price."""
    if parse_price(station.get("pricing")) is not None and not (car["tesla"] and is_tesla_site(station)):
        return None
    return network_entry(station.get("network_code"), car["tesla"], station.get("name"))


def usd_cad():
    """(rate, date) to convert US dollar prices: the Bank of Canada rate stored in tolls.json."""
    fx = dc.load_data("tolls").get("usd_cad") or {}
    return float(fx.get("rate") or 1.0), fx.get("date") or ""


def stop_price(station, car, kwh, minutes, kw):
    """(cost, price_text, price_source) for one charging session. `minutes` are billed minutes (see
    billed_minutes). price_text is short and has no parentheses: the CLI prints it inside some."""
    tesla_site = is_tesla_site(station)
    # a price NLR lists at a Supercharger is the non-Tesla rate (trip-planner research, 2026-10-10); Tesla
    # owners pay the owner rate
    p = None if (car["tesla"] and tesla_site) else parse_price(station.get("pricing"))
    if p is not None:
        txt = "listed " + price_short(p, kw)
        if tesla_site:
            txt += " non-Tesla rate"
        cost = _price_cost(p, kwh, minutes, kw)
        if station.get("country") == "US":   # NLR lists US prices in US dollars
            rate, _ = usd_cad()
            txt = txt.replace("listed $", "listed US$", 1) + ", in CAD at %.2f" % rate
            cost *= rate
        return round(cost, 2), txt, "station"
    n = network_entry(station.get("network_code"), car["tesla"], station.get("name"))
    cost = _price_cost({"model": n["model"], "rate": n["rate"], "fee": 0.0, "first": None, "pairs": []},
                       kwh, minutes, kw)
    return round(cost, 2), "estimate; %s, check the app" % n["short"], "network estimate"


# --------------------------------------------------------------------------- stations

def _nlr_key(opts):
    return (opts or {}).get("api_key") or dc.env_key("NLR_API_KEY") or "DEMO_KEY"


def _nlr_call(path, params, key, warnings, post=False, with_total=False):
    """The reply's station list, or None after a warning. `with_total` returns (stations, total_results)."""
    url = NLR_BASE + path
    hdr = {"X-Api-Key": key}   # header, so the key never reaches a URL or a cache file name
    try:
        if post:
            body = dc.fetch(url, data=params, headers=hdr, timeout=60, max_age_h=CACHE_H, validate=dc.json_reply)
        else:
            body = dc.fetch(url + "?" + urllib.parse.urlencode(params), headers=hdr, timeout=60, max_age_h=CACHE_H,
                            validate=dc.json_reply)
        d = json.loads(body)
        st = d.get("fuel_stations") or []
        if with_total:
            return st, int(d.get("total_results") or len(st))
        return st
    except urllib.error.HTTPError as e:
        th.close_quietly(e)
        if e.code == 429:
            warnings.append("NLR charger API hourly limit reached (DEMO_KEY allows about 10 calls per hour); set "
                            "NLR_API_KEY (free at https://developer.nlr.gov/signup/)")
        else:
            warnings.append("NLR charger API failed (HTTP %s)" % e.code)
    except Exception as e:   # noqa: BLE001
        warnings.append("NLR charger API failed (%s)" % e.__class__.__name__)
    return None


def norm_nlr(r):
    units = {}
    for u in r.get("ev_charging_units") or []:
        level = u.get("charging_level") or ""
        for code, c in (u.get("connectors") or {}).items():
            n = c.get("port_count") or 0
            if not n:
                continue
            is_dc = level == "dc_fast" or (not level and code in DC_CODES)
            k = code if is_dc or code == "J1772" or code not in DC_CODES else code + "_L2"
            kw, ports = units.get(k, (None, 0))
            pk = c.get("power_kw")
            units[k] = (max(kw or 0, pk or 0) or None, ports + n)
    if not units:   # older records: connector list and counts only
        for code in r.get("ev_connector_types") or []:
            if code in DC_CODES:
                units[code] = (None, r.get("ev_dc_fast_num") or 1)
            elif code == "J1772":
                units[code] = (None, r.get("ev_level2_evse_num") or 1)
    addr = ", ".join(x for x in (r.get("street_address"), r.get("city")) if x)
    return {"id": "nlr:%s" % r.get("id"), "name": r.get("station_name") or "charger", "network_code": r.get("ev_network") or "",
            "lat": float(r["latitude"]), "lon": float(r["longitude"]), "address": addr, "units": units,
            "pricing": r.get("ev_pricing") or "", "access_text": r.get("access_days_time") or "",
            "dc_num": r.get("ev_dc_fast_num") or 0, "l2_num": r.get("ev_level2_evse_num") or 0,
            "status": r.get("status_code") or "E", "source": "NLR", "country": (r.get("country") or "").upper()}


def _osm_kw(v):
    m = re.search(r"(\d+(?:\.\d+)?)\s*kw", (v or "").lower())
    return float(m.group(1)) if m else None


def norm_osm(e):
    tags = e.get("tags") or {}
    lat = e.get("lat", (e.get("center") or {}).get("lat"))
    lon = e.get("lon", (e.get("center") or {}).get("lon"))
    if lat is None or lon is None:
        return None
    name = tags.get("name") or tags.get("brand") or tags.get("operator") or "charging station"
    hay = " ".join(tags.get(k, "") for k in ("name", "brand", "operator", "network")).lower()
    units, assumed = {}, False
    for sock, code in OSM_SOCKETS.items():
        v = tags.get("socket:" + sock)
        if not v or v == "no":
            continue
        try:
            n = int(v)
        except ValueError:
            n = 1
        units[code] = (_osm_kw(tags.get("socket:%s:output" % sock)), n)
    if not any(c in units for c in DC_CODES):
        if not any(b in hay for b in OSM_DC_BRANDS):
            return None
        assumed = True
        n = int(tags.get("capacity", "2")) if tags.get("capacity", "").isdigit() else 2
        units[("TESLA" if "tesla" in hay or "supercharger" in hay else "J1772COMBO")] = (None, n)
    net = "Tesla" if "tesla" in hay else (tags.get("brand") or tags.get("operator") or tags.get("network") or "")
    addr = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    if tags.get("addr:city"):
        addr = (addr + ", " if addr else "") + tags["addr:city"]
    return {"id": "osm:%s/%s" % (e.get("type"), e.get("id")), "name": name, "network_code": net, "lat": float(lat),
            "lon": float(lon), "address": addr, "units": units, "pricing": tags.get("charge", ""), "access_text": "",
            "dc_num": sum(p for c, (k, p) in units.items() if c in DC_CODES), "l2_num": units.get("J1772", (0, 0))[1],
            "status": "E", "source": "OSM", "assumed": assumed}


def _overpass_ok(body):
    """dc.fetch `validate` for Overpass: a timed-out or failed query comes back with HTTP 200, no or partial
    elements and a "remark"; such a reply is a failure and is never cached."""
    d = json.loads(body)
    if not isinstance(d, dict) or not isinstance(d.get("elements"), list):
        return "Overpass reply without elements"
    remark = str(d.get("remark") or "")
    if "error" in remark.lower() or "timed out" in remark.lower():
        return "Overpass: %s" % remark[:120]
    return None


def _overpass(query, warnings):
    for attempt in (1, 2):
        try:
            body = dc.fetch(OVERPASS_URL, data={"data": query}, timeout=90, min_interval=1.0, max_age_h=CACHE_H,
                            validate=_overpass_ok)
            return json.loads(body).get("elements") or []
        except dc.FetchError as e:
            warnings.append("OpenStreetMap charger fallback failed (%s)" % th.safe_text(str(e)))
        except urllib.error.HTTPError as e:
            th.close_quietly(e)
            if e.code == 429 and attempt == 1:
                log("Overpass busy (429); waiting 30 s")
                time.sleep(30)
                continue
            warnings.append("OpenStreetMap charger fallback failed (HTTP %s)" % e.code)
        except Exception as e:   # noqa: BLE001
            warnings.append("OpenStreetMap charger fallback failed (%s)" % e.__class__.__name__)
        return None
    return None


def stations_along(poly, warnings, opts=None):
    """DC fast stations near the route: (list of normalized stations, source text). Coordinates go out at 3
    decimals (about 110 m), plenty for a 2 mile corridor, so the request never pins the start address."""
    line = dc.thin(poly, ROUTE_POINTS)
    wkt = "LINESTRING(%s)" % ", ".join("%.3f %.3f" % (lon, lat) for lat, lon in line)
    log("Chargers: NLR nearby-route (%d points)" % len(line))
    recs = _nlr_call("nearby-route.json", {"route": wkt, "distance": ROUTE_MILES, "fuel_type": "ELEC",
                                           "ev_charging_level": "dc_fast", "country": NLR_COUNTRY, "status": "E",
                                           "access": "public", "limit": "all"}, _nlr_key(opts), warnings, post=True)
    if recs is not None:
        return ([norm_nlr(r) for r in recs if r.get("latitude") is not None],
                "NLR Alternative Fuel Stations API (US DOE and NRCan data), public DC fast within %d mi of the route" % ROUTE_MILES)
    pts = dc.thin(poly, 120)
    q = "[out:json][timeout:90];nwr[\"amenity\"=\"charging_station\"](around:3000,%s);out center tags;" % (
        ",".join("%.3f,%.3f" % p for p in pts))
    log("Chargers: Overpass fallback")
    els = _overpass(q, warnings)
    if els is None:
        return [], "none (charger sources failed)"
    out = [s for s in (norm_osm(e) for e in els) if s]
    warnings.append("Charger list from OpenStreetMap: plug types and power are often missing or assumed; check "
                    "PlugShare or the network app before relying on a stop")
    return out, "OpenStreetMap via Overpass (ODbL), fallback"


def stations_near(lat, lon, warnings, opts=None):
    """Level 2 and DC stations within DEST_MILES of a point (destination charging). One NLR call; NLR sorts
    by distance, so when the reply is cut at NEAR_LIMIT (a dense downtown full of Level 2 posts) a second call
    asks for DC fast only, and DC sites farther out are never crowded out."""
    log("Chargers: NLR nearest at the destination")
    base = {"latitude": "%.3f" % lat, "longitude": "%.3f" % lon, "radius": DEST_MILES, "fuel_type": "ELEC",
            "country": NLR_COUNTRY, "status": "E", "access": "public"}
    got = _nlr_call("nearest.json", dict(base, limit=NEAR_LIMIT), _nlr_key(opts), warnings, with_total=True)
    if got is not None:
        recs, total = got
        if total > len(recs):
            log("Chargers: NLR nearest DC fast at the destination (%d of %d stations listed)" % (len(recs), total))
            more = _nlr_call("nearest.json", dict(base, ev_charging_level="dc_fast", limit=NEAR_DC_LIMIT),
                             _nlr_key(opts), warnings)
            ids = set(r.get("id") for r in recs)
            recs = recs + [r for r in more or [] if r.get("id") not in ids]
        return [norm_nlr(r) for r in recs if r.get("latitude") is not None]
    q = "[out:json][timeout:60];nwr[\"amenity\"=\"charging_station\"](around:%d,%.3f,%.3f);out center tags;" % (
        DEST_MILES * 1609, lat, lon)
    els = _overpass(q, warnings)
    if els is None:
        return None
    out = []
    for e in els:
        s = norm_osm(e)
        if s is None:   # keep Level 2 sites too
            tags = e.get("tags") or {}
            la, lo = e.get("lat", (e.get("center") or {}).get("lat")), e.get("lon", (e.get("center") or {}).get("lon"))
            if la is None:
                continue
            n = int(tags.get("capacity", "1")) if tags.get("capacity", "").isdigit() else 1
            s = {"id": "osm:%s" % e.get("id"), "name": tags.get("name") or tags.get("operator") or "charging station",
                 "network_code": tags.get("operator") or tags.get("network") or "", "lat": float(la), "lon": float(lo),
                 "address": "", "units": {"J1772": (None, n)}, "pricing": tags.get("charge", ""), "access_text": "",
                 "dc_num": 0, "l2_num": n, "status": "E", "source": "OSM", "assumed": True}
        out.append(s)
    return out


def tesla_site_ok(st, car):
    """(usable, note) for a Tesla Supercharger record and this car."""
    if car["tesla"]:
        return True, ""
    ta = _ev()["tesla_access"]
    acc = (st.get("access_text") or "").lower()
    if ta["tesla_only_text"].lower() in acc:
        return False, ""
    if ta["partner_text"].lower() in acc or st.get("pricing"):
        return True, ""
    return True, "check in the Tesla app that this Supercharger is open to non-Tesla cars"


def compatible(st, car, dc_only=True):
    """(max_kw or None, ports, [connector codes], note) for the plugs this car can use, or None."""
    note = ""
    if is_tesla_site(st):
        ok, note = tesla_site_ok(st, car)
        if not ok:
            return None
    best, ports, codes = None, 0, []
    for code, (kw, n) in st["units"].items():
        if code in DC_CODES and code in car["connectors"]:
            pass
        # Tesla Destination (NACS AC) plugs only for Teslas: the 2026 LEAF has a NACS DC port but a J1772 AC port
        elif not dc_only and (code == "J1772" or (code == "TESLA_L2" and car["tesla"])):
            kw = kw or _ev()["model"]["level2_kw"]
        else:
            continue
        codes.append(code)
        ports += n
        if kw:
            best = max(best or 0, kw)
    if not codes:
        return None
    return best, ports, sorted(codes), note


# --------------------------------------------------------------------------- planner

def _display_network(st, car):
    code = st.get("network_code") or ""
    n = network_entry(code, car["tesla"], st.get("name"))
    if n["key"] == "tesla_non_tesla":
        return "Tesla Supercharger"
    if n["key"] != "other":
        return n["name"]
    return (code.replace("_", " ").title() if code.isupper() else code) or "unknown network"


def plan_charging(poly, route_km, depart, car, soc_start, temp_c, warnings, opts=None):
    """Greedy charging plan along `poly` (see the module doc and ev.json "model")."""
    opts = dict(opts or {})
    inner = bool(opts.get("_inner"))   # the drive back of a round trip, planned by the outer call
    ev = _ev()
    m = ev["model"]
    notes = []
    temp_note = ""
    if temp_c is None:
        month = depart.month if depart else 1
        temp_c = month_normal(month)
        temp_note = " (no forecast: %s month normal %s C)" % (depart.strftime("%B") if depart else "January",
                                                              _deg(temp_c))
    soc_start = max(0.0, min(1.0, float(soc_start)))
    hwy_share = opts.get("hwy_share")
    hwy_share = m["default_hwy_share"] if hwy_share is None else hwy_share
    speed = opts.get("speed_kmh") or m["default_speed_kmh"]
    reserve = opts.get("reserve")
    if reserve is None:
        reserve = m["reserve"]["below_0C"] if temp_c < 0 else m["reserve"]["default"]
    max_soc = opts.get("max_soc") or m["max_soc"]
    home_rate = opts.get("home_rate")
    home_rate = ev["home_rate"]["cad_per_kwh"] if home_rate is None else home_rate
    usable = car["battery_kwh"]
    cons = consumption(car, temp_c, speed, hwy_share)
    per_km = cons / 100.0 / usable
    dest = opts.get("dest") or (poly[-1] if poly else None)
    notes.append("Energy about %.1f kWh/100 km: %s, %.0f%% highway at %.0f km/h, %s C%s (x%.2f for temperature)"
                 % (cons, car["name"], hwy_share * 100, speed, _deg(temp_c), temp_note, temp_factor(temp_c)))

    direct_soc = soc_start - route_km * per_km
    return_trip = bool(opts.get("return_trip")) and not inner
    need_return = route_km * per_km + reserve if return_trip else None
    stops, infeasible, source, cands = [], False, "no charger search needed", []
    est_nets = []   # ev.json network entries used for estimated prices, in stop order
    pos, soc, detour_kwh = 0.0, soc_start, 0.0
    lw = []   # the stop loop's warnings, kept back until the retry above the charge limit is decided

    def remedy():   # after a stop, more charge at departure does not help
        return "use Level 2 on the way" if stops else "leave with more charge, or use Level 2 on the way"
    if soc_start - reserve < 0 and not inner:
        warnings.append("Starting charge %.0f%% is below the %.0f%% reserve" % (soc_start * 100, reserve * 100))

    search = direct_soc < reserve - 1e-9 or (need_return is not None and need_return > max_soc + 1e-9)
    stations, data_failed = None, False
    if search and poly and len(poly) >= 2:
        if opts.get("stations") is not None:   # the caller's list from another route of the same trip
            stations, source = opts["stations"], opts.get("stations_source") or "NLR"
        else:
            stations, source = stations_along(poly, warnings, opts)
        data_failed = source.startswith("none")
        cands = _candidates(stations, poly, route_km, car, m)
        if not cands and not data_failed and not inner:
            warnings.append("No compatible public DC fast charger found within %d mi of the route" % ROUTE_MILES)
    if direct_soc < reserve - 1e-9 and data_failed:
        stops, soc, pos, est_nets = _estimate_stops(car, route_km, soc_start, per_km, reserve, max_soc, temp_c, m)
        notes.append("Charger lists were unavailable, so the stops are an estimate (fast charger of about %.0f kW "
                     "at the km shown, network price); pick real chargers in the car's planner or PlugShare"
                     % min(car["max_dc_kw"], m["good_charger"]["min_kw"]))
        if stops and soc_start < reserve - 1e-9:
            notes.append("You start below the %.0f%% reserve: charge at the first fast charger you reach"
                         % (reserve * 100))
    elif direct_soc < reserve - 1e-9 and poly and len(poly) >= 2:
        # the stops' charge limit: max_soc, or the stretch limit on the retry below when max_soc finds no plan
        cap = opts.get("_stop_cap") or max_soc
        while len(stops) < 12:
            if soc - (route_km - pos) * per_km >= reserve - 1e-9:
                break
            pick = _pick(cands, pos, soc, per_km, reserve, cap, route_km, m)
            if pick is None and not stops and soc_start < reserve - 1e-9:
                # already below the reserve: the nearest charger the car can still reach is the right answer
                pick = _pick_low(cands, pos, soc, per_km, reserve, soc_start)
                if pick is not None:
                    notes.append("You start below the %.0f%% reserve: charge at the first stop (km %.0f)"
                                 % (reserve * 100, pick["km"]))
            if pick is None:
                infeasible = True
                gap_from = pos
                nxt = [c for c in cands if c["km"] > pos + 1]
                lw.append("No reachable compatible fast charger after km %.0f (range about %.0f km at %.0f%% with "
                          "%.0f%% reserve)%s; %s" % (gap_from, max(0.0, (soc - reserve) / per_km), soc * 100,
                                                     reserve * 100, ", next one at km %.0f" % nxt[0]["km"] if nxt
                                                     else "", remedy()))
                break
            c = pick
            half = c["detour_km"] / 2.0
            arr = soc - (c["km"] - pos + half) * per_km
            target = _target(c, cands, per_km, reserve, cap, route_km, m)
            if target - arr < MIN_ADD and c["good"]:
                # a good charger picked over a nearer slower one, where the stop adds nothing: skip it when
                # adding the same charge at the farther charger (a stop anyway) is no slower
                alt = _farther(c, cands, pos, soc, per_km, reserve, cap, route_km, m)
                if alt is not None and _skip_saves(car, c, alt, (cap - arr) * usable, temp_c, m):
                    c = alt
                    half = c["detour_km"] / 2.0
                    arr = soc - (c["km"] - pos + half) * per_km
                    target = _target(c, cands, per_km, reserve, cap, route_km, m)
            if target - arr < MIN_ADD:   # a stop that adds nothing: the next leg cannot be done from here
                target = cap
            if target - arr < MIN_TOPUP:
                infeasible = True
                lw.append("No compatible fast charger far enough ahead after km %.0f to finish the trip; %s"
                          % (pos, remedy()))
                break
            kwh = (target - arr) * usable
            kw = min(c["max_kw"] or m["assumed_kw_when_missing"], car["max_dc_kw"])
            cmin = charge_minutes(car, kwh, c["max_kw"], temp_c)
            cost, ptext, psrc = stop_price(c["st"], car, kwh, billed_minutes(cmin), kw)
            if psrc == "network estimate":
                est_nets.append(price_network(c["st"], car))
            stops.append({"name": c["st"]["name"], "network": _display_network(c["st"], car), "lat": c["st"]["lat"],
                          "lon": c["st"]["lon"], "address": c["st"]["address"], "km_along": round(c["km"], 1),
                          "detour_min": int(round(c["detour_min"])), "max_kw": c["max_kw"], "ports": c["ports"],
                          "connectors": c["codes"], "arrive_soc": round(arr, 3), "depart_soc": round(target, 3),
                          "charge_min": round(cmin, 1), "kwh": round(kwh, 1), "cost": cost, "price_text": ptext,
                          "price_source": psrc, "source": c["st"]["source"], "note": c["note"]})
            detour_kwh += c["detour_km"] * cons / 100.0
            soc = target - half * per_km
            pos = c["km"]

    stretch = m.get("max_soc_stretch", 0.95)
    if infeasible and stops and not data_failed and not opts.get("_stop_cap") and max_soc < stretch - 1e-9:
        # a gap after a stop that max_soc cannot bridge: plan again letting the stops charge up to the stretch
        # limit (each only as far as its next leg needs), on the same stations
        w2 = []
        r2 = plan_charging(poly, route_km, depart, car, soc_start, temp_c, w2,
                           dict(opts, _stop_cap=stretch, stations=stations, stations_source=source))
        if r2["feasible"]:
            warnings.extend(x for x in w2 if x not in warnings)
            hi = [s for s in r2["stops"] if s["depart_soc"] > max_soc + 1e-9]
            r2["notes"].append("No plan stays within the %.0f%% charge limit: charge to %s (charging above %.0f%% is "
                               "slower than the times shown)"
                               % (max_soc * 100, " and ".join("%.0f%% at %s" % (s["depart_soc"] * 100, s["name"])
                                                              for s in hi), max_soc * 100))
            return r2
    warnings.extend(lw)

    arrive_soc = soc - (route_km - pos) * per_km
    if arrive_soc < reserve - 0.005 and not infeasible:
        infeasible = True
        warnings.append("%sArrival charge about %.0f%% is below the %.0f%% reserve; charge before leaving"
                        % ("" if not inner else "Return trip: ", max(0.0, arrive_soc) * 100, reserve * 100))
    kwh_used = route_km * cons / 100.0 + detour_kwh
    public_kwh = sum(s["kwh"] for s in stops)
    public_cost = round(sum(s["cost"] or 0.0 for s in stops), 2)
    extra_s = sum((s["charge_min"] + s["detour_min"]) * 60.0 for s in stops)
    home_kwh = max(0.0, kwh_used - public_kwh)
    for s in stops:
        if s["note"]:
            notes.append("%s: %s" % (s["name"], s["note"]))
    if any(s["network"].startswith("Tesla") and s["depart_soc"] >= 0.795 for s in stops):
        notes.append("Tesla charges a congestion fee up to $0.50/min at busy sites above 80%; unplug at 80%")
    notes += _price_notes(est_nets)
    if stops and temp_c < 0:
        notes.append("Cold battery: set the charger as the navigation destination in the car so it preconditions; "
                     "charge times include x%.2f for the cold" % (m["winter_charge_time"]["below_minus_10"] if temp_c < -10
                                                                  else m["winter_charge_time"]["below_0"]))
    if len(stops) >= 2 and stops[-1]["kwh"] < 12:
        top = stops[-2]["depart_soc"] + (stops[-1]["depart_soc"] - stops[-1]["arrive_soc"])
        if top <= 0.90:
            notes.append("Charging to about %.0f%% at %s instead of %.0f%% would skip the short stop at %s%s"
                         % (top * 100, stops[-2]["name"], stops[-2]["depart_soc"] * 100, stops[-1]["name"],
                            " (charging above 80% is slower)" if top > 0.805 else ""))
    if any(s["source"] == "OSM" for s in stops):
        notes.append("Some stops come from OpenStreetMap with assumed plug types or power")

    low = arrive_soc < m["low_arrival_soc"] - 0.005
    dest_needed = (low or infeasible) and not inner
    dest_list, need_back, rplan = [], None, None
    if need_return is not None and need_return <= max_soc + 1e-9:
        if arrive_soc >= need_return:
            notes.append("Return trip: you arrive with %.0f%% and the drive back needs about %.0f%%, so no charging is "
                         "needed for the return" % (arrive_soc * 100, need_return * 100))
        else:
            add = need_return - max(0.0, arrive_soc)
            dest_needed = True
            notes.append("Return trip: you arrive with %.0f%% but the drive back needs about %.0f%%: add %.0f%% "
                         "(%.0f kWh) at the destination, {CH%.2f}, or stop at a fast charger on the way back"
                         % (max(0.0, arrive_soc) * 100, need_return * 100, add * 100, add * usable, add * usable))
    elif need_return is not None:
        rplan, txt, need_back, charge_dest, rwarn = _return_plan(
            poly, route_km, car, arrive_soc, temp_c, m, per_km, reserve, max_soc, usable,
            {"hwy_share": hwy_share, "speed_kmh": speed, "reserve": reserve, "max_soc": max_soc,
             "home_rate": home_rate, "stations": stations if stations is not None else [],
             "stations_source": source if stations is not None else "none (charger sources failed)",
             "dest": poly[0] if poly else None, "_inner": True})
        notes.append(txt)
        warnings.extend(rwarn)
        dest_needed = dest_needed or charge_dest
    if dest_needed and dest:
        dest_list = _dest_chargers(dest, car, warnings, opts)
        if low and not infeasible:
            notes.append("Low arrival charge (%.0f%%): chargers near the destination are listed" % (max(0.0, arrive_soc) * 100))
        if dest_list is None:
            dest_list = []
            notes.append("The charger list near the destination was unavailable; check PlugShare or the car's planner")
            if source == "no charger search needed":
                source = "charger lookup failed (NLR limit or Overpass busy)"
        elif not dest_list:
            notes.append("No public charger found within %d mi of the destination" % DEST_MILES)
    dest_list = _show_best_dc(dest_list)
    notes = [re.sub(r"\{CH([\d.]+)\}", lambda mm: _dest_time_text(float(mm.group(1)), dest_list, car, temp_c, m), n)
             for n in notes]

    out = {"needed": bool(stops) or infeasible, "feasible": not infeasible, "arrive_soc": round(arrive_soc, 3),
           "stops": stops, "extra_s": round(extra_s), "public_cost": public_cost, "kwh_used": round(kwh_used, 1),
           "kwh_100km": round(cons, 1), "home_energy_cost": round(home_kwh * home_rate, 2),
           "notes": notes, "dest_chargers": dest_list, "source": source, "temp_c": temp_c, "reserve": reserve,
           "soc_start": soc_start, "car": car["name"], "estimated": bool(data_failed and stops)}
    if stations is not None and not data_failed:
        out["_stations"], out["_stations_source"] = stations, source   # for reuse on another route; not printed
    if need_return is not None:
        out["return_soc_one_charge"] = round(need_return, 3)   # may exceed 100%: never print it as a percentage
        if need_return <= max_soc + 1e-9:
            out["return_soc_needed"] = round(need_return, 3)   # the CLI prints "leave the destination with N%"
        if need_back is not None:
            out["return_soc_to_charger"] = round(need_back, 3)
        if rplan is not None:
            out["return_plan"] = rplan
    return out


def _stop_list(stops, limit=4):
    names = ["%s at km %.0f" % (s["name"], s["km_along"]) for s in stops[:limit]]
    if len(stops) > limit:
        names.append("%d more" % (len(stops) - limit))
    return ", ".join(names)


def _return_plan(poly, route_km, car, arrive_soc, temp_c, m, per_km, reserve, max_soc, usable, back_opts):
    """The drive back when one charge cannot cover it: plan stops on the reversed route (chargers on the other
    carriageway), first from the arrival charge, else after charging at the destination (to the least charge that
    reaches the first stop with its reserve, up to max_soc).
    Returns (plan summary, note, soc to leave with or None, destination charge needed, warnings)."""
    rev = list(reversed(poly))
    start = max(0.0, arrive_soc)
    rw = []
    rp = plan_charging(rev, route_km, None, car, start, temp_c, rw, back_opts)
    charge_dest, need_back = False, None
    if not rp["feasible"] and start < max_soc - 0.01:
        rw2 = []
        rp2 = plan_charging(rev, route_km, None, car, max_soc, temp_c, rw2, back_opts)
        if rp2["feasible"] or not rp["stops"]:
            rp, rw, charge_dest = rp2, rw2, True
    if charge_dest and rp["feasible"] and rp["stops"]:
        # the least destination charge that reaches the first stop with ITS reserve (20% at a single stall), and the
        # plan from that charge, so the advice and the plan agree; whole percent, as the advice prints it
        first = rp["stops"][0]
        res1 = max(reserve, m["reserve"]["single_stall"]) if (first.get("ports") or 2) < 2 else reserve
        need_back = min(max_soc, math.ceil((max_soc - first["arrive_soc"] + res1) * 100 - 1e-6) / 100.0)
        if need_back < max_soc - 1e-9:
            rw3 = []
            rp3 = plan_charging(rev, route_km, None, car, need_back, temp_c, rw3, back_opts)
            if rp3["feasible"]:
                rp, rw = rp3, rw3
            else:
                need_back = max_soc
    soc0 = (need_back if need_back is not None else max_soc) if charge_dest else start
    # kwh_used and home_energy_cost price the drive back's battery energy, the destination top-up included, at
    # the home rate
    summary = {"stops": rp["stops"], "public_cost": rp["public_cost"], "extra_s": rp["extra_s"],
               "feasible": rp["feasible"], "start_soc": round(soc0, 3), "soc_start": round(soc0, 3),
               "kwh_used": rp["kwh_used"], "home_energy_cost": rp["home_energy_cost"],
               "charge_at_destination": charge_dest, "estimated": rp["estimated"], "arrive_soc": rp["arrive_soc"]}
    n = len(rp["stops"])
    est = " (estimate, charger lists unavailable)" if rp["estimated"] else ""
    head = "Return trip: the %.0f km drive back needs more than one charge" % route_km
    if not rp["feasible"]:
        txt = ("%s, and no fast charger plan was found for it even from %.0f%%%s; charge at the destination and plan "
               "the way back in the car's planner or PlugShare" % (head, max_soc * 100,
                                                                     ": stops so far %s" % _stop_list(rp["stops"]) if n else ""))
        return summary, txt, None, True, ["Return trip: " + w for w in rw]
    tail = "%d charging stop%s on the way back%s, km counted from the destination: %s; about %s and %s extra" % (
        n, "" if n == 1 else "s", est, _stop_list(rp["stops"]), dc.money(rp["public_cost"]),
        dc.fmt_dur(rp["extra_s"]))
    if not charge_dest:
        txt = "%s: from the %.0f%% you arrive with, plan %s" % (head, start * 100, tail)
        return summary, txt, None, False, []
    need_back = soc0
    add = max(0.0, need_back - start)
    txt = ("%s: charge at the destination to at least %.0f%% (add %.0f%%, %.0f kWh: {CH%.2f}), then plan %s"
           % (head, need_back * 100, add * 100, add * usable, add * usable, tail))
    return summary, txt, need_back, True, []


def _price_notes(nets):
    """One note per network priced by estimate, with the long ev.json text."""
    out, seen = [], set()
    for n in nets:
        if n is None or n["key"] in seen:
            continue
        seen.add(n["key"])
        out.append("%s price estimate, %s: %s; the app or charger screen has the real rate" % (n["name"], n["date"], n["text"]))
    return out


def _best_dc(dest_list):
    dcs = [x for x in dest_list or [] if x["level"] == "DC fast"]
    if not dcs:
        return None
    return max(dcs, key=lambda x: ((x["max_kw"] or 0), -x["km"]))


def _show_best_dc(dest_list):
    """Keep the fastest DC site among the first three rows, which is all the CLI prints."""
    best = _best_dc(dest_list)
    if not best or best in dest_list[:3]:
        return dest_list
    rest = [x for x in dest_list if x is not best]
    return rest[:2] + [best] + rest[2:]


def _dest_time_text(kwh, dest_list, car, temp_c, m):
    """'about 3.8 h on a 7 kW Level 2 or 31 min on the 100 kW DC at X' for `kwh` added at the destination."""
    l2 = [x for x in dest_list or [] if x["level"] != "DC fast"]
    l2kw = min((l2[0]["max_kw"] if l2 else None) or m["level2_kw"], m["level2_max_kw"])
    txt = "about %.1f h on a %s kW Level 2" % (kwh / (l2kw * m["level2_efficiency"]), ("%.1f" % l2kw).rstrip("0").rstrip("."))
    best = _best_dc(dest_list)
    if best:
        kw = best["max_kw"] or m["assumed_kw_when_missing"]
        txt += " or %d min on the %.0f kW DC at %s" % (round(charge_minutes(car, kwh, kw, temp_c)), kw, best["name"])
    return txt


def _estimate_stops(car, route_km, soc_start, per_km, reserve, max_soc, temp_c, m):
    """Stops when no charger list could be fetched: charge where the battery reaches the reserve plus 5 points
    (or at once when it is already lower), at an assumed fast charger and the generic network price.
    Returns (stops, soc after the last, its km, network entries used)."""
    kw = min(car["max_dc_kw"], m["good_charger"]["min_kw"])
    stops, pos, soc, nets = [], 0.0, soc_start, []
    arr_at = reserve + 0.05
    blank = {"network_code": "", "pricing": "", "name": ""}
    while len(stops) < 12 and soc - (route_km - pos) * per_km < reserve - 1e-9:
        km = pos + max(1.0, (soc - arr_at) / per_km)
        arr = max(0.0, soc - (km - pos) * per_km)
        need = (route_km - km) * per_km + reserve + m["final_margin"]
        target = min(max_soc, need)
        if target - arr < 0.03:
            target = max_soc
        kwh = (target - arr) * car["battery_kwh"]
        cmin = charge_minutes(car, kwh, kw, temp_c)
        cost, ptext, psrc = stop_price(blank, car, kwh, billed_minutes(cmin), kw)
        nets.append(price_network(blank, car))
        stops.append({"name": "a fast charger near km %.0f" % km, "network": "any network", "lat": None, "lon": None,
                      "address": "", "km_along": round(km, 1), "detour_min": int(m["detour_ramp_min"]),
                      "max_kw": kw, "ports": None, "connectors": sorted(car["connectors"]),
                      "arrive_soc": round(arr, 3), "depart_soc": round(target, 3), "charge_min": round(cmin, 1),
                      "kwh": round(kwh, 1), "cost": cost, "price_text": ptext, "price_source": psrc,
                      "source": "estimate", "note": ""})
        pos, soc = km, target
    return stops, soc, pos, nets


_BOUND = {"east": 90.0, "west": 270.0, "north": 0.0, "south": 180.0}
_ABBR = {"EB": 90.0, "WB": 270.0, "NB": 0.0, "SB": 180.0}
_HWY_ADDR = re.compile(r"\b(?:hwy|highway|on-)\s*4\d\d\b", re.I)


def station_bound(st):
    """Compass bearing of the carriageway a station's name or address names (Westbound, 401 EB), or None."""
    hay = "%s | %s" % (st.get("name") or "", st.get("address") or "")
    mm = re.search(r"\b(east|west|north|south)\s*-?\s*bound\b", hay, re.I)
    if mm:
        return _BOUND[mm.group(1).lower()]
    mm = re.search(r"(?:\b\d{2,3}|\bQEW)\s*(EB|WB|NB|SB)\b|\b(EB|WB|NB|SB)\s*(?:hwy|highway|on-)?\s*(?:\d{2,3}|QEW)\b", hay)
    if mm:
        return _ABBR[mm.group(1) or mm.group(2)]
    return None


def is_service_centre(st):
    """A highway service centre (ONroute and the like): reachable only from its own carriageway."""
    name = (st.get("name") or "").lower()
    return ("onroute" in name or "service centre" in name or "service center" in name
            or bool(_HWY_ADDR.search(st.get("address") or "")) or station_bound(st) is not None)


def _xy(a, b):
    kx = math.cos(math.radians(a[0]))
    return (b[1] - a[1]) * kx, b[0] - a[0]


def _point_at(line, cum, d):
    d = max(0.0, min(cum[-1], d))
    for i in range(len(cum) - 1):
        if cum[i + 1] >= d:
            seg = cum[i + 1] - cum[i]
            t = 0.0 if seg <= 0 else (d - cum[i]) / seg
            (la, lo), (lb, lob) = line[i], line[i + 1]
            return la + (lb - la) * t, lo + (lob - lo) * t
    return line[-1]


def route_bearing(line, cum, along, span=1000.0):
    """Compass bearing of travel over about 2 km around `along` (metres)."""
    a, b = _point_at(line, cum, along - span), _point_at(line, cum, along + span)
    dx, dy = _xy(a, b)
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360.0


def on_left(line, i, lat, lon):
    """True when (lat, lon) is left of the direction of travel on segment i (right-hand traffic: wrong side)."""
    j, k = i, min(i + 1, len(line) - 1)
    while line[j] == line[k] and (j > 0 or k < len(line) - 1):   # skip repeated vertices
        j, k = max(0, j - 1), min(len(line) - 1, k + 1)
    dx, dy = _xy(line[j], line[k])
    vx, vy = _xy(line[j], (lat, lon))
    return dx * vy - dy * vx > 0


def _wrong_side(st, line, cum, along, off, i):
    """A service centre on the other carriageway of a divided highway: reachable only by leaving the highway and
    doubling back. Uses the side of the line for stations close to it and any direction word in the record."""
    bound = station_bound(st)
    if bound is not None:
        br = route_bearing(line, cum, along)
        if br is not None and abs((br - bound + 180.0) % 360.0 - 180.0) > 90.0:
            return True
    if 8.0 <= off <= 400.0 and is_service_centre(st):
        return on_left(line, i, st["lat"], st["lon"])
    return False


def _candidates(stations, poly, route_km, car, m):
    line = dc.thin(poly, 800)
    cum = dc.cum_dist(line)
    scale = route_km * 1000.0 / cum[-1] if cum[-1] > 0 else 1.0
    good = m["good_charger"]
    best_by_sig = {}
    for st in stations:
        if st.get("status", "E") != "E":
            continue
        comp = compatible(st, car)
        if comp is None:
            continue
        kw, ports, codes, note = comp
        along, off, i = dc.project(line, cum, st["lat"], st["lon"])
        km = along * scale / 1000.0
        if off > 5000 or km < 2 or km > route_km - 3:
            continue
        if _wrong_side(st, line, cum, along, off, i):
            continue
        dkm = 2 * off * 1.3 / 1000.0
        dmin = dkm / m["detour_speed_kmh"] * 60.0 + (m["detour_ramp_min"] if off > 300 else 0.0)
        min_kw = min(good["min_kw"], car["max_dc_kw"])
        c = {"st": st, "km": km, "off_m": off, "detour_km": dkm, "detour_min": dmin, "max_kw": kw,
             "ports": ports, "codes": codes, "note": note,
             "good": (kw or m["assumed_kw_when_missing"]) >= min_kw and ports >= good["min_ports"]}
        # co-located records (a FLO post beside a Supercharger): keep the faster site, then the one with more ports
        sig = (round(st["lat"], 3), round(st["lon"], 3))
        old = best_by_sig.get(sig)
        if old is None or ((kw or 0), ports) > ((old["max_kw"] or 0), old["ports"]):
            best_by_sig[sig] = c
    out = sorted(best_by_sig.values(), key=lambda c: c["km"])
    return out


def _res_at(c, reserve, m):
    return max(reserve, m["reserve"]["single_stall"]) if c["ports"] < 2 else reserve


def _reachable(cands, pos, soc, per_km, reserve, m):
    return [c for c in cands if c["km"] > pos + 1 and
            soc - (c["km"] - pos + c["detour_km"] / 2.0) * per_km >= _res_at(c, reserve, m) - 1e-9]


def _useful(cands, pos, soc, per_km, reserve, max_soc, m, min_add=MIN_ADD):
    """Reachable chargers where a stop adds charge: one reached within `min_add` of max_soc adds nothing, and
    the stop loop rejects it."""
    return [c for c in _reachable(cands, pos, soc, per_km, reserve, m)
            if soc - (c["km"] - pos + c["detour_km"] / 2.0) * per_km < max_soc - min_add]


def _pick_low(cands, pos, soc, per_km, reserve, soc_start):
    """First stop when the car starts below the reserve: the nearest good charger it reaches with at least
    max(5%, start - 10 points), else the nearest charger it reaches with 5%."""
    def arr(c):
        return soc - (c["km"] - pos + c["detour_km"] / 2.0) * per_km
    low_res = max(0.05, min(reserve, soc_start - 0.10))
    ahead = [c for c in cands if c["km"] > pos + 1]
    good = [c for c in ahead if c["good"] and c["detour_min"] <= 10 and arr(c) >= low_res - 1e-9]
    if good:
        return good[0]
    near = [c for c in ahead if arr(c) >= 0.05 - 1e-9]
    return near[0] if near else None


def _onward(c, cands, per_km, reserve, max_soc, route_km, m):
    start = max_soc - c["detour_km"] / 2.0 * per_km
    if start - (route_km - c["km"]) * per_km >= reserve - 1e-9:
        return True
    return bool(_useful(cands, c["km"], start, per_km, reserve, max_soc, m))


def _pick(cands, pos, soc, per_km, reserve, max_soc, route_km, m):
    reach = _useful(cands, pos, soc, per_km, reserve, max_soc, m)
    if not reach:   # only a short top-up gets the car on: take it rather than call the trip impossible
        reach = [c for c in _useful(cands, pos, soc, per_km, reserve, max_soc, m, MIN_TOPUP)
                 if _onward(c, cands, per_km, reserve, max_soc, route_km, m)]
    if not reach:
        return None
    ok = [c for c in reach if _onward(c, cands, per_km, reserve, max_soc, route_km, m)] or reach
    ok = [c for c in ok if c["detour_min"] <= 10] or ok
    good = [c for c in ok if c["good"]]
    pool = good or ok
    far = max(c["km"] for c in pool)
    # among stations within 15 km of the farthest, take the fastest (then most ports)
    near = [c for c in pool if c["km"] >= far - 15]
    return max(near, key=lambda c: ((c["max_kw"] or 0), c["ports"], c["km"]))


def _farther(c, cands, pos, soc, per_km, reserve, max_soc, route_km, m):
    """The charger _pick would take among those beyond `c`, at any power, or None."""
    reach = [x for x in _useful(cands, pos, soc, per_km, reserve, max_soc, m) if x["km"] > c["km"]]
    if not reach:
        return None
    ok = [x for x in reach if _onward(x, cands, per_km, reserve, max_soc, route_km, m)] or reach
    ok = [x for x in ok if x["detour_min"] <= 10] or ok
    far = max(x["km"] for x in ok)
    return max([x for x in ok if x["km"] >= far - 15], key=lambda x: ((x["max_kw"] or 0), x["ports"], x["km"]))


def _skip_saves(car, c, alt, kwh, temp_c, m):
    """True when adding `kwh` at `alt` (a stop anyway, so no second plug-in) takes no longer than a stop at `c`
    for it."""
    stop = charge_minutes(car, kwh, c["max_kw"], temp_c) + c["detour_min"]
    later = charge_minutes(car, kwh, alt["max_kw"], temp_c) - m["plug_in_min"]
    return later <= stop


def _target(c, cands, per_km, reserve, max_soc, route_km, m):
    """State of charge to leave `c` with: enough for the destination plus reserve, else enough for the next
    good charger reachable at max_soc, never above max_soc."""
    half = c["detour_km"] / 2.0
    margin = 0.02
    need = (route_km - c["km"] + half) * per_km + reserve + m["final_margin"]
    if need <= max_soc:
        return need
    nxt = _reachable(cands, c["km"], max_soc - half * per_km, per_km, reserve, m)
    nxt = [x for x in nxt if _onward(x, cands, per_km, reserve, max_soc, route_km, m)] or nxt
    if not nxt:
        return max_soc
    pool = [x for x in nxt if x["good"]] or nxt
    far = max(pool, key=lambda x: x["km"])
    need = (far["km"] - c["km"] + half + far["detour_km"] / 2.0) * per_km + _res_at(far, reserve, m) + margin
    return min(max_soc, need)


def _l2_price_text(raw, country=""):
    """Short Level 2 price text: no parentheses, at most about 60 characters of the listed text. A US price
    (NLR lists those in US dollars) says US$ and adds the rate in CAD, as stop_price does for DC."""
    raw = re.sub(r"\s+", " ", (raw or "").replace("(", "").replace(")", "")).strip()
    if not raw:
        return "Level 2, price set by the host; check the app"
    low = raw.lower()
    if (re.match(r"free\b", low) and "$" not in low) or low in ("$0", "$0.00", "no charge"):
        return "listed free"
    p = parse_price(raw) if country == "US" and "$" in raw else None
    if len(raw) > 60:
        raw = raw[:57].rsplit(" ", 1)[0].rstrip(";,") + "..."
    if country == "US" and "$" in raw:
        raw = re.sub(r"(?<![A-Za-z])\$", "US$", raw)
        if p is not None and p["model"] != "free":
            fx, _ = usd_cad()
            cad = dict(p, rate=p["rate"] * fx, fee=(p.get("fee") or 0.0) * fx,
                       first=None if p.get("first") is None else p["first"] * fx,
                       pairs=[(a * fx, b) for a, b in p.get("pairs") or []])
            if p.get("per_min"):
                cad["per_min"] = p["per_min"] * fx
            return "listed: %s; about %s in CAD at %.2f" % (raw, price_short(cad), fx)
    return "listed: " + raw


def _dest_chargers(dest, car, warnings, opts):
    lat, lon = dest
    out = []
    near = stations_near(lat, lon, warnings, opts)
    if near is None:
        return None
    cc = ((opts or {}).get("dest_country") or "").upper()
    if cc:   # a charger across a border river is close but not reachable on foot or without a crossing
        near = [st for st in near if not st.get("country") or st["country"] == cc]
    for st in near:
        comp = compatible(st, car, dc_only=False)
        if comp is None:
            continue
        kw, ports, codes, note = comp
        is_dc = any(c in DC_CODES for c in codes)
        if is_dc:
            ptxt = stop_price(st, car, 30.0, 30.0, kw or 50)[1]
        else:
            ptxt = _l2_price_text(st.get("pricing"), st.get("country"))
        km = th.haversine_m(lat, lon, st["lat"], st["lon"]) / 1000.0
        if km > DEST_MILES * 1.609 * 1.25:
            continue
        out.append({"name": st["name"], "network": _display_network(st, car), "lat": st["lat"], "lon": st["lon"],
                    "km": round(km, 1), "max_kw": kw, "ports": ports, "price_text": ptxt,
                    "level": "DC fast" if is_dc else "Level 2", "connectors": codes})
    out.sort(key=lambda x: x["km"])
    sites = []
    for x in out:   # one row per site: NLR lists each post of a garage as its own station
        for y in sites:
            # another network next door (a FLO post beside a Supercharger) is another site with its own power
            if y["level"] == x["level"] and y["network"] == x["network"] \
                    and th.haversine_m(x["lat"], x["lon"], y["lat"], y["lon"]) < 120:
                ports = y["ports"] + x["ports"]
                if (x["max_kw"] or 0) > (y["max_kw"] or 0):   # the row names the post whose power it shows
                    y.update(x)
                y["ports"] = ports
                break
        else:
            sites.append(dict(x))
    fast = [x for x in sites if x["level"] == "DC fast"][:3]
    slow = [x for x in sites if x["level"] != "DC fast"][:6 - len(fast)]
    return sorted(fast + slow, key=lambda x: x["km"])
