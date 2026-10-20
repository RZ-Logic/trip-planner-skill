"""Driving routes for the driving planner: TomTom (keyed, live traffic), Valhalla and OSRM (keyless,
free-flow), a time-of-day traffic model for the keyless engines, 407 ETR detection and pricing, border
bridges, and retiming for a new departure or the return drive.

Standard library only. Python 3.8+. Data: references/driving/traffic.json, references/driving/tolls.json.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
import urllib.parse

import drive_common as dc
import tolls

th = dc.th
log = th.log

VALHALLA_URL = "https://valhalla1.openstreetmap.de/route"
OSRM_URLS = ["https://router.project-osrm.org", "https://routing.openstreetmap.de/routed-car"]
TOMTOM_URL = "https://api.tomtom.com/routing/1/calculateRoute/%s/json"
ENGINE_NAMES = {"tomtom": "TomTom", "valhalla": "Valhalla", "osrm": "OSRM"}
MAX_POLY = 2000
KEYLESS_CACHE_H = 24.0   # road geometry and free-flow times do not change by the hour
TOMTOM_CACHE_H = 0.15    # live traffic: reuse for about 10 minutes
HWY_KMH = 70.0
RETURN_KM_TOL = 0.05     # a TomTom return reply this much longer or shorter than the route took other roads
TOMTOM_CATEGORIES = {"JAM": "traffic jam", "ROAD_WORK": "roadworks", "ROAD_CLOSURE": "road closed",
                     "OTHER": "slow traffic"}


def traffic_data():
    return dc.load_data("traffic")


def area_of(lat, lon):
    """Name of the congested area containing the point, else ''."""
    for a in traffic_data()["areas"]:
        if dc.in_any(lat, lon, a["boxes"]):
            return a["name"]
    return ""


def _in_area(lat, lon, name):
    for a in traffic_data()["areas"]:
        if a["name"] == name:
            return dc.in_any(lat, lon, a["boxes"])
    return False


def _fmt_x(x):
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _short_err(e):
    s = str(e) or e.__class__.__name__
    return s if len(s) < 140 else s[:137] + "..."


def point_at(poly, cum, s):
    """Point at distance s (metres) along poly with cumulative distances cum."""
    s = max(0.0, min(cum[-1], s))
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg = cum[hi] - cum[lo]
    t = 0.0 if seg <= 0 else (s - cum[lo]) / seg
    (a, b), (c, d) = poly[lo], poly[hi]
    return a + (c - a) * t, b + (d - b) * t


# --------------------------------------------------------------------------- road names

def road_name(names):
    """Short display name from a list of street names and refs ('Hwy 401', '407 ETR', 'QEW', 'I-190')."""
    names = [n.strip() for n in names if n and n.strip()]
    for n in names:
        if "407 ETR" in n or "Express Toll Route" in n:
            return "407 ETR"
    for n in names:
        if n in ("QEW",) or "Queen Elizabeth Way" in n:
            return "QEW"
    for n in names:
        if re.match(r"^4\d\d$", n):   # Ontario 400-series freeway refs
            return "Hwy %s" % n
    for n in names:
        m = re.match(r"^(?:Highway|Hwy|King's Highway|Ontario Highway)\s+(\d+[A-Z]?)\b", n)
        if m:
            return "Hwy %s" % m.group(1)
    for n in names:
        m = re.match(r"^(?:I|Interstate)[ -]?(\d+)$", n)
        if m:
            return "I-%s" % m.group(1)
    for n in names:
        if not re.match(r"^[\d ;A-Z-]*\d[\d ;A-Z-]*$", n):
            return n
    for n in names:
        if re.match(r"^\d{3}$", n):
            return "Hwy %s" % n
    return ""


FREE_407_EAST = "Hwy 407 East (free)"


def road_parts(pts, m, name, east):
    """[(name, metres)] in driving order. A 407 piece east of Brock Rd (east_end_lon) is the toll-free
    provincial 407 East, so it reads 'Hwy 407 East (free)'; a piece that crosses Brock Rd is split by
    distance on each side."""
    if name not in ("407 ETR", "Hwy 407") or not pts:
        return [(name, m)]
    if all(p[1] >= east for p in pts):
        return [(FREE_407_EAST, m)]
    if all(p[1] < east for p in pts) or len(pts) < 2:
        return [(name, m)]
    segs = []
    for a, b in zip(pts, pts[1:]):
        d = th.haversine_m(a[0], a[1], b[0], b[1])
        lab = name if (a[1] + b[1]) / 2.0 < east else FREE_407_EAST
        if segs and segs[-1][0] == lab:
            segs[-1][1] += d
        else:
            segs.append([lab, d])
    tot = sum(x[1] for x in segs)
    if tot <= 0:
        return [(name, m)]
    return [(lab, m * d / tot) for lab, d in segs]


def _free_piece(pts, name, east):
    """True when a toll piece the engine flags is a road tolls.json lists as toll-free now (free_roads: the
    provincial 407 East, Hwys 412 and 418, all east of Brock Rd): by its name, with most of its points east of
    east_end_lon. Engines can keep the old toll flag; an unnamed piece is not assumed free."""
    free = set(road_name([fr.get("name") or ""]) for fr in tolls.data().get("free_roads") or []) - {""}
    if not pts or name not in free:
        return False
    return sum(1 for p in pts if p[1] >= east) > 0.5 * len(pts)


def _roads_summary(road_m, total_m):
    items = [(m, first, name) for name, (m, first) in road_m.items() if name]
    items.sort(key=lambda x: -x[0])
    keep = [x for x in items[:4] if x[0] >= 0.04 * total_m] or items[:1]
    keep.sort(key=lambda x: x[1])
    return [x[2] for x in keep]


# --------------------------------------------------------------------------- chunks

class _Builder(object):
    """Collects route pieces in driving order into chunks, 407 stretches and road totals."""

    def __init__(self, engine):
        self.engine = engine
        self.chunk_m = float(traffic_data().get("chunk_m") or 2000)
        self.east = tolls.data()["etr407"]["east_end_lon"]
        self.poly = []
        self.chunks = []
        self.stretches = []
        self.road_m = {}
        self.elapsed = 0.0
        self.total_m = 0.0
        self.free_407_east = False
        self.other_tolls = []   # toll pieces off the 407 (point lists): a listed crossing, or a toll not priced here
        self._open = None   # 407 stretch being built

    def add(self, pts, s, m, name="", etr=False, hwy=None):
        """One maneuver or step: pts [(lat, lon)], s seconds, m metres."""
        if pts:
            if self.poly and self.poly[-1] == pts[0]:
                self.poly.extend(pts[1:])
            else:
                self.poly.extend(pts)
        if name:
            for nm, mm in road_parts(pts, m, name, self.east):
                self.add_road(nm, mm)
        if name == "Hwy 407" and pts and any(p[1] >= self.east for p in pts):
            self.free_407_east = True
        if etr and pts and any(p[1] >= self.east for p in pts):
            self.free_407_east = True
        n = max(1, int(math.ceil(m / self.chunk_m))) if m > 0 and len(pts or []) >= 2 else 1
        cum = dc.cum_dist(pts) if pts and len(pts) >= 2 else None
        for k in range(n):
            if cum and cum[-1] > 0:
                a = point_at(pts, cum, cum[-1] * k / n)
                mid = point_at(pts, cum, cum[-1] * (k + 0.5) / n)
            else:
                a = mid = pts[0] if pts else (self.poly[-1] if self.poly else (0.0, 0.0))
            ar = area_of(mid[0], mid[1])
            fast = hwy if hwy is not None else (m > 0 and s > 0 and (m / s) * 3.6 >= HWY_KMH)
            self.chunks.append({"s": s / n, "m": m / n, "lat": round(a[0], 6), "lon": round(a[1], 6),
                                "urban": bool(ar), "area": ar, "etr": bool(etr and mid[1] < self.east),
                                "hwy": bool(fast)})
        self.elapsed += s
        self.total_m += m

    def add_road(self, name, m):
        """Metres on a named road; the first time a name is seen sets its order in the summary."""
        if not name:
            return
        cur = self.road_m.get(name)
        self.road_m[name] = [m + (cur[0] if cur else 0.0), cur[1] if cur else len(self.road_m)]

    def piece(self, pts, s, m, name, on_407, hwy=None):
        """Add a piece; on_407 pieces in a row form one 407 ETR stretch. The stretch keeps the base time at
        every point, so segments_407 can time the entry and exit after clipping the free 407 East."""
        if on_407:
            pts_l = list(pts or [])
            cum = dc.cum_dist(pts_l) if len(pts_l) >= 2 else [0.0] * len(pts_l)
            tot = cum[-1] if cum else 0.0
            times = [self.elapsed + (s * c / tot if tot > 0 else 0.0) for c in cum]
            if self._open is None:
                self._open = {"points": pts_l, "times": times, "offset_s": self.elapsed, "end_s": self.elapsed + s}
            else:
                self._open["points"].extend(pts_l)
                self._open["times"].extend(times)
                self._open["end_s"] = self.elapsed + s
        else:
            self.close_stretch()
        self.add(pts, s, m, name, etr=on_407, hwy=hwy)

    def close_stretch(self):
        if self._open is not None:
            self.stretches.append(self._open)
            self._open = None


def _on_stretch(etr, ramp, i):
    """A 407 piece, or an unnamed (toll) ramp next to one: the ramp's far end sits at the crossroad, which
    snaps to the right interchange (toll407.py rule)."""
    if etr[i]:
        return True
    return ramp[i] and ((i > 0 and etr[i - 1]) or (i + 1 < len(etr) and etr[i + 1]))


# --------------------------------------------------------------------------- Valhalla

def _valhalla_trip(trip):
    b = _Builder("valhalla")
    for leg in trip.get("legs") or []:
        shape = dc.decode_polyline(leg.get("shape") or "", 6)
        mans = leg.get("maneuvers") or []
        is_etr = [bool(mn.get("toll")) and any("407" in n for n in (mn.get("street_names") or [])) for mn in mans]
        ramp = [bool(mn.get("toll")) and not mn.get("street_names") for mn in mans]
        for i, mn in enumerate(mans):
            pts = shape[mn.get("begin_shape_index", 0):mn.get("end_shape_index", 0) + 1]
            s = float(mn.get("time") or 0.0)
            m = float(mn.get("length") or 0.0) * 1000.0
            on = _on_stretch(is_etr, ramp, i)
            name = road_name(mn.get("street_names") or [])
            if mn.get("toll") and not on and not _free_piece(pts, name, b.east):
                b.other_tolls.append(pts)
            b.piece(pts, s, m, name, on, hwy=bool(mn.get("highway")) or None)
        b.close_stretch()
    summ = trip.get("summary") or {}
    return b, {"engine_toll": bool(summ.get("has_toll"))}


def _valhalla_fetch(o, d, exclude_tolls, alternates, avoid_tolls_soft=False, through=None):
    """exclude_tolls bans every toll edge (border bridges too); avoid_tolls_soft (use_tolls 0) only makes
    them costly, so a border trip keeps its bridge but leaves the 407. through: [{"lat", "lon", "heading"}]
    pass-through points between origin and destination."""
    locs = [{"lat": o[0], "lon": o[1]}]
    for p in through or []:
        locs.append({"lat": p["lat"], "lon": p["lon"], "type": "through", "heading": p["heading"],
                     "heading_tolerance": 45})
    locs.append({"lat": d[0], "lon": d[1]})
    req = {"locations": locs, "costing": "auto", "units": "kilometers", "directions_type": "maneuvers"}
    if alternates:
        req["alternates"] = alternates
    if avoid_tolls_soft:
        req["costing_options"] = {"auto": {"use_tolls": 0.0}}
    elif exclude_tolls:
        req["costing_options"] = {"auto": {"exclude_tolls": True}}
    url = VALHALLA_URL + "?json=" + urllib.parse.quote(json.dumps(req, separators=(",", ":")))
    data = dc.fetch_json(url, timeout=40, min_interval=1.0, max_age_h=KEYLESS_CACHE_H)
    if not isinstance(data, dict) or "trip" not in data:
        raise RuntimeError((data or {}).get("error") or "no route")
    trips = [data["trip"]] + [a["trip"] for a in data.get("alternates") or [] if a.get("trip")]
    return [t for t in trips if (t.get("status", 0) == 0)]


def _valhalla(o, d, depart, opts, warnings, arrive_at=None):
    trips = _valhalla_fetch(o, d, False, 2)
    if not trips:
        raise RuntimeError("no route")
    routes = [_finish(*_valhalla_trip(t), engine="valhalla", depart=depart, opts=opts) for t in trips]
    fast, others = routes[0], routes[1:]
    tollfree = None
    if fast["etr"] or fast["toll_unpriced"]:
        border = bool(fast["bridges"])
        try:
            tf = _valhalla_fetch(o, d, True, 0, avoid_tolls_soft=border)
            if tf:
                cand = _finish(*_valhalla_trip(tf[0]), engine="valhalla", depart=depart, opts=opts)
                tollfree = _keep_comparison(fast, cand, "Valhalla", warnings)
        except Exception as e:  # noqa: BLE001
            warnings.append("Valhalla could not find a toll-free route (%s)" % _short_err(e))
    elif not any(r["etr"] for r in routes) and wants_407_option(fast, depart, opts):
        var = _valhalla_407_variant(o, d, fast, depart, opts)
        if var is not None:
            others = others + [var]
    return _pick(fast, tollfree, others)


def _keep_comparison(fast, cand, engine, warnings):
    """The route compared with the 407 must keep the fastest route's border crossing: excluding tolls also
    excludes the toll bridges, which can send the 'toll-free' route hundreds of km through the US. The same holds
    for a route whose only tolls are unpriced, which may cross at a crossing tolls.json does not list."""
    if cand is None:
        return None
    if fast["bridges"] and (not cand["bridges"] or cand["km"] > 1.3 * fast["km"] or cand["etr"]):
        warnings.append("%s found no route without the 407 that keeps the border crossing (its toll-free route "
                        "was %.0f km against %.0f km); check Google Maps with tolls avoided"
                        % (engine, cand["km"], fast["km"]))
        return None
    if fast.get("toll_unpriced") and not fast.get("etr") and cand["km"] > 1.3 * fast["km"]:
        # the unpriced toll can be a crossing missing from tolls.json (Sault Ste. Marie): excluding tolls bans it
        warnings.append("%s found no toll-free route close to the route with tolls (its toll-free route was %.0f km "
                        "against %.0f km; an unpriced toll can be a border crossing), so none is compared; check "
                        "Google Maps with tolls avoided" % (engine, cand["km"], fast["km"]))
        return None
    return cand


# --------------------------------------------------------------------------- 407 option at rush hour

VARIANT_NEAR_M = 25000.0   # one end of the trip within this of the 407 corridor
VARIANT_MIN_KM = 15.0      # the trip spans at least this much of the 407 chain
VARIANT_BACK_M = 300.0     # pass-through point this far before the interchange, on the mainline


def wants_407_option(route, depart, opts):
    """True when a keyless fastest route without the 407 spends at least 15 min in the GTHA and drives at
    least 10 min of it at weekday peak or shoulder, or leaves or returns in a getaway window: the free-flow
    engine does not see the rush hour the 407 avoids, so ask for a 407 variant to compare."""
    if route["engine"] == "tomtom" or not route["chunks"]:
        return False
    tt = traffic_data()
    hol = set(opts.get("holidays") or ()) | tolls.holidays_default()
    getaway = _getaway_dir(route)
    tor = _toronto(opts)
    t = gtha = busy = 0.0
    for c in route["chunks"]:
        when = depart + dt.timedelta(seconds=t)
        if tor and (c.get("etr") or c["urban"]):
            when = tor(when)
        if c["urban"] and c.get("area") == "GTHA":
            gtha += c["s"]
            if _row(tt["congested"], when, hol)["name"] in ("weekday peak", "weekday shoulder"):
                busy += c["s"]
        t += c["s"] * factors(c, when, hol, getaway, route["engine"])[0]
    away = bool(getaway) and _getaway_on(depart, getaway, hol)
    return gtha >= 900 and (busy >= 600 or away)


def _bearing(a, b):
    y = math.sin(math.radians(b[1] - a[1])) * math.cos(math.radians(b[0]))
    x = (math.cos(math.radians(a[0])) * math.sin(math.radians(b[0])) -
         math.sin(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.cos(math.radians(b[1] - a[1])))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def variant_through(o, d):
    """Two pass-through points on the 407 ETR mainline for a trip from o to d, or None when the 407 does
    not fit the trip (neither end near it, or under 15 km of it between the two ends)."""
    ics = sorted(tolls.data()["etr407"]["interchanges"], key=lambda i: i["km"])
    ko, off_o = tolls.chain_km(o[0], o[1])
    kd, off_d = tolls.chain_km(d[0], d[1])
    if min(off_o, off_d) > VARIANT_NEAR_M:
        return None
    east = kd > ko
    k_in = ko if off_o <= VARIANT_NEAR_M else (ics[0]["km"] if east else ics[-1]["km"])
    k_out = kd if off_d <= VARIANT_NEAR_M else (ics[-1]["km"] if east else ics[0]["km"])
    span = abs(k_out - k_in)
    if span < VARIANT_MIN_KM:
        return None
    # Inset from both ends: an end projects onto the chain at a right angle, which can sit before the
    # interchange a driver would really use (a south Milton origin projects before km 20; the natural entry is km 29+).
    sign = 1.0 if east else -1.0
    ka = k_in + sign * max(3.0, 0.25 * span)
    kb = k_out - sign * max(3.0, 0.1 * span)
    if east:
        ia = next((i for i, ic in enumerate(ics) if ic["km"] >= ka), None)
        ib = next((i for i in range(len(ics) - 1, -1, -1) if ics[i]["km"] <= kb), None)
    else:
        ia = next((i for i in range(len(ics) - 1, -1, -1) if ics[i]["km"] <= ka), None)
        ib = next((i for i, ic in enumerate(ics) if ic["km"] >= kb), None)
    if ia is None or ib is None or sign * (ics[ib]["km"] - ics[ia]["km"]) < 5.0:
        return None
    out = []
    step = 1 if east else -1
    for i in (ia, ib):
        up = i - step   # the interchange before this one in the direction of travel
        if not 0 <= up < len(ics):
            up = i + step
            sign = -1.0
        else:
            sign = 1.0
        here, prev = (ics[i]["lat"], ics[i]["lon"]), (ics[up]["lat"], ics[up]["lon"])
        dist = th.haversine_m(here[0], here[1], prev[0], prev[1])
        f = min(0.5, VARIANT_BACK_M / dist) if dist > 0 else 0.0
        pt = (here[0] + (prev[0] - here[0]) * f, here[1] + (prev[1] - here[1]) * f)
        head = _bearing(prev, here) if sign > 0 else _bearing(here, prev)
        out.append({"lat": round(pt[0], 6), "lon": round(pt[1], 6), "heading": round(head), "ic": ics[i]["name"]})
    return out


def _valhalla_407_variant(o, d, fast, depart, opts):
    """The same trip forced along the 407 ETR (one extra Valhalla call, cached like the others); None when
    it fails, misses the 407 or is a detour."""
    thr = variant_through(o, d)
    if not thr:
        return None
    try:
        trips = _valhalla_fetch(o, d, False, 0, through=thr)
    except Exception as e:  # noqa: BLE001
        log("Valhalla 407 variant failed: %s" % e)
        return None
    if not trips:
        return None
    r = _finish(*_valhalla_trip(trips[0]), engine="valhalla", depart=depart, opts=opts)
    if not r["etr"] or r["km"] > 1.25 * fast["km"]:
        return None
    r["notes"].append("407 option added because this trip crosses the GTHA at a busy time")
    r["_forced_407"] = True   # not the router's own pick: the navigation links need a via point to follow it
    return r


# --------------------------------------------------------------------------- OSRM

def _osrm_is_etr(step):
    return "407 ETR" in [r.strip() for r in (step.get("ref") or "").split(";")]


def _osrm_unnamed(step):
    return not (step.get("name") or step.get("ref"))


def _osrm_route(route):
    b = _Builder("osrm")
    for leg in route.get("legs") or []:
        steps = leg.get("steps") or []
        etr = [_osrm_is_etr(s) for s in steps]
        ramp = [_osrm_unnamed(s) for s in steps]
        for i, st in enumerate(steps):
            pts = [(c[1], c[0]) for c in (st.get("geometry") or {}).get("coordinates") or []]
            names = [st.get("name") or ""] + [r.strip() for r in (st.get("ref") or "").split(";")]
            b.piece(pts, float(st.get("duration") or 0.0), float(st.get("distance") or 0.0), road_name(names),
                    _on_stretch(etr, ramp, i))
        b.close_stretch()
    return b, {"engine_toll": None}


def _osrm(o, d, depart, opts, warnings, arrive_at=None):
    data, last = None, None
    for base in OSRM_URLS:
        url = "%s/route/v1/driving/%.6f,%.6f;%.6f,%.6f?overview=false&steps=true&geometries=geojson&alternatives=3" % (
            base, o[1], o[0], d[1], d[0])
        try:
            data = dc.fetch_json(url, timeout=40, min_interval=1.0, max_age_h=KEYLESS_CACHE_H)
            if data.get("code") != "Ok" or not data.get("routes"):
                raise RuntimeError(data.get("message") or data.get("code") or "no route")
            break
        except Exception as e:  # noqa: BLE001
            last, data = e, None
    if data is None:
        raise last or RuntimeError("no route")
    routes = [_finish(*_osrm_route(r), engine="osrm", depart=depart, opts=opts) for r in data["routes"]]
    fast, others = routes[0], routes[1:]
    tollfree = None
    if fast["etr"]:
        free = [r for r in others if not r["etr"]]
        if free:
            tollfree = _keep_comparison(fast, free[0], "OSRM", warnings)
        else:
            warnings.append("OSRM offered no alternative without the 407 ETR (it cannot avoid tolls); "
                            "check Google Maps with tolls avoided")
    return _pick(fast, tollfree, [r for r in others if r is not tollfree])


# --------------------------------------------------------------------------- TomTom

def _rfc3339(local, opts):
    off, _how = th.offset_at_local(local, opts.get("tz") or "America/Toronto", opts.get("region"))
    mins = int(off.total_seconds() // 60)
    sign = "-" if mins < 0 else "+"
    return local.strftime("%Y-%m-%dT%H:%M:%S") + "%s%02d:%02d" % (sign, abs(mins) // 60, abs(mins) % 60)


def tomtom_live(depart, opts, arrive_at=None):
    """True when the TomTom query asks for departAt=now (a departure within 10 minutes): its time is live
    traffic, which already reflects the weather on the roads."""
    if arrive_at is not None:
        return False
    now, _ = th.local_now(opts.get("tz") or "America/Toronto", opts.get("region"))
    return depart <= now + dt.timedelta(minutes=10)


def tomtom_url(o, d, depart, opts, arrive_at=None, avoid_tolls=False):
    """(url, cache_key). The key is never part of cache_key."""
    params = [("traffic", "true")]
    if arrive_at is not None:
        params.append(("arriveAt", _rfc3339(arrive_at, opts)))
    else:
        params.append(("departAt", "now" if tomtom_live(depart, opts) else _rfc3339(depart, opts)))
    params += [("routeType", "fastest"), ("travelMode", "car"), ("computeTravelTimeFor", "all"),
               ("sectionType", "toll"), ("sectionType", "traffic"), ("sectionType", "motorway"),
               ("sectionType", "importantRoadStretch"),
               # guidance instructions carry pointIndex and the cumulative travelTimeInSeconds (calculateRoute docs:
               # https://docs.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route), see _point_times
               ("instructionsType", "coded"),
               ("routeRepresentation", "polyline"), ("maxAlternatives", "1")]
    if avoid_tolls:
        params.append(("avoid", "tollRoads"))
    path = TOMTOM_URL % ("%.6f,%.6f:%.6f,%.6f" % (o[0], o[1], d[0], d[1]))
    q = urllib.parse.urlencode(params)
    return path + "?key=" + urllib.parse.quote(opts.get("tomtom_key") or "") + "&" + q, path + "?" + q


def _near_407(pts):
    """True when most of these points lie along the 407 ETR (within 1.5 km of the interchange chain)."""
    east = tolls.data()["etr407"]["east_end_lon"]
    west = [p for p in pts if p[1] < east]
    if len(west) < 2:
        return False
    sample = west[::max(1, len(west) // 20)]
    near = sum(1 for p in sample if tolls.chain_km(p[0], p[1])[1] < 1500)
    return near >= 0.6 * len(sample)


def _point_times(rt, cum, total_s):
    """Seconds from the start at every route point, interpolated by distance between TomTom's guidance
    instructions (pointIndex, cumulative travelTimeInSeconds); None without usable instructions (the caller then
    spreads the time evenly by distance)."""
    if len(rt.get("legs") or []) != 1:
        return None   # pointIndex counts the reply's points; multi-leg replies are de-duplicated in _tomtom_route
    n = len(cum)
    anchors = {0: 0.0, n - 1: float(total_s)}
    for ins in ((rt.get("guidance") or {}).get("instructions") or []):
        k, s = ins.get("pointIndex"), ins.get("travelTimeInSeconds")
        if isinstance(k, int) and 0 < k < n - 1 and isinstance(s, (int, float)):
            anchors[k] = float(s)
    ks = sorted(anchors)
    if len(ks) < 3 or any(anchors[y] < anchors[x] for x, y in zip(ks, ks[1:])):
        return None
    out = [0.0] * n
    for x, y in zip(ks, ks[1:]):
        span = cum[y] - cum[x]
        for j in range(x, y + 1):
            f = (cum[j] - cum[x]) / span if span > 0 else (j - x) / float(y - x)
            out[j] = anchors[x] + (anchors[y] - anchors[x]) * f
    return out


def _tomtom_route(rt):
    pts = []
    for leg in rt.get("legs") or []:
        lp = [(p["latitude"], p["longitude"]) for p in leg.get("points") or []]
        if pts and lp and pts[-1] == lp[0]:
            lp = lp[1:]
        pts.extend(lp)
    summ = rt.get("summary") or {}
    total_s = float(summ.get("travelTimeInSeconds") or 0.0)
    total_m = float(summ.get("lengthInMeters") or 0.0)
    cum = dc.cum_dist(pts) if len(pts) >= 2 else [0.0]
    scale = (total_m / cum[-1]) if cum[-1] > 0 and total_m else 1.0
    secs = rt.get("sections") or []
    tolls_ix = [(s.get("startPointIndex", 0), s.get("endPointIndex", 0)) for s in secs
                if (s.get("sectionType") or "").upper() == "TOLL"]
    mway_ix = [(s.get("startPointIndex", 0), s.get("endPointIndex", 0)) for s in secs
               if (s.get("sectionType") or "").upper() == "MOTORWAY"]
    etr_ix = [(a, z) for a, z in tolls_ix if _near_407(pts[a:z + 1])]
    # Road names come from importantRoadStretch sections (street name and road numbers), in route order.
    stretches = [s for s in secs if (s.get("sectionType") or "").upper() == "IMPORTANT_ROAD_STRETCH"]
    stretches.sort(key=lambda s: (s.get("startPointIndex", 0), s.get("importantRoadStretchIndex", 0)))

    def stretch_name(s):
        names = [((s.get("streetName") or {}).get("text") or "")]
        return road_name(names + [(rn or {}).get("text") or "" for rn in s.get("roadNumbers") or []])

    def toll_name(a, z):
        # the named stretch that overlaps the toll piece most, '' when none does
        ov = [(min(z, s.get("endPointIndex", 0)) - max(a, s.get("startPointIndex", 0)), stretch_name(s))
              for s in stretches]
        best = max([x for x in ov if x[0] > 0] or [(0, "")])
        return best[1]
    b = _Builder("tomtom")
    b.other_tolls = [pts[a:z + 1] for a, z in tolls_ix
                     if (a, z) not in etr_ix and not _free_piece(pts[a:z + 1], toll_name(a, z), b.east)]
    # Pieces between every section boundary, so 407 and motorway flags are exact per piece.
    cuts = sorted(set([0, max(0, len(pts) - 1)] + [i for ab in etr_ix + mway_ix for i in ab]))
    share = (total_s / cum[-1]) if cum[-1] > 0 else 0.0
    # piece times from TomTom's turn-by-turn times when the reply has them (a slow approach puts the 407 entry
    # later), else spread evenly by distance
    times = _point_times(rt, cum, total_s) if len(pts) >= 2 else None
    for a, z in zip(cuts, cuts[1:]):
        piece = pts[a:z + 1]
        m = (cum[z] - cum[a]) * scale
        s = (times[z] - times[a]) if times else (cum[z] - cum[a]) * share
        in_etr = any(x <= a and z <= y for x, y in etr_ix)
        in_mway = any(x <= a and z <= y for x, y in mway_ix)
        b.piece(piece, s, m, "", in_etr, hwy=in_mway)
    b.close_stretch()
    if len(pts) == 1:
        b.poly = list(pts)
    for s in stretches:
        a = max(0, min(len(cum) - 1, s.get("startPointIndex", 0)))
        z = max(a, min(len(cum) - 1, s.get("endPointIndex", 0)))
        name = stretch_name(s)
        if name:
            for nm, mm in road_parts(pts[a:z + 1], (cum[z] - cum[a]) * scale, name, b.east):
                b.add_road(nm, mm)
    delays = []
    for s in secs:
        if (s.get("sectionType") or "").upper() != "TRAFFIC":
            continue
        delay = float(s.get("delayInSeconds") or 0.0)
        cat = (s.get("simpleCategory") or "OTHER").upper()
        if delay < 60 and cat != "ROAD_CLOSURE":
            continue
        km_at = cum[min(len(cum) - 1, s.get("startPointIndex", 0))] * scale / 1000.0
        text = "%s, %s delay, %.0f km from the start" % (TOMTOM_CATEGORIES.get(cat, "slow traffic"),
                                                        dc.fmt_dur(delay), km_at)
        if cat == "ROAD_CLOSURE" and delay < 60:
            text = "road closure %.0f km from the start (route avoids it or waits)" % km_at
        delays.append({"text": text, "delay_s": round(delay)})
    extra = {"engine_toll": bool(tolls_ix), "delays": delays,
             "tomtom": {k: summ.get(k) for k in ("travelTimeInSeconds", "noTrafficTravelTimeInSeconds",
                                                  "historicTrafficTravelTimeInSeconds", "trafficDelayInSeconds",
                                                  "departureTime", "arrivalTime", "lengthInMeters")}}
    return b, extra


def _tomtom_fetch(o, d, depart, opts, arrive_at, avoid):
    url, ck = tomtom_url(o, d, depart, opts, arrive_at, avoid)
    # live traffic: a failed request never falls back to an older cached reply (it would be labelled live)
    data = dc.fetch_json(url, timeout=30, min_interval=0.25, max_age_h=TOMTOM_CACHE_H, cache_key=ck, stale_max_h=0)
    if not data.get("routes"):
        err = data.get("error") or {}
        raise RuntimeError(err.get("description") or data.get("detailedError", {}).get("message") or "no route")
    return data["routes"]


def _tt_depart(rt, fallback):
    s = ((rt.get("summary") or {}).get("departureTime") or "")[:19]
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return fallback


def _tt_finish(rt, when, opts, live):
    b, extra = _tomtom_route(rt)
    extra["live_now"] = live
    return _finish(b, extra, engine="tomtom", depart=when, opts=opts)


def _tomtom(o, d, depart, opts, warnings, arrive_at=None):
    rts = _tomtom_fetch(o, d, depart, opts, arrive_at, False)
    live = tomtom_live(depart, opts, arrive_at)
    when = _tt_depart(rts[0], depart) if arrive_at is not None else depart
    routes = [_tt_finish(r, when, opts, live) for r in rts]
    fast, others = routes[0], routes[1:]
    tollfree = None
    if fast["etr"] or fast["toll_unpriced"]:
        try:
            tf = _tomtom_fetch(o, d, depart, opts, arrive_at, True)
            w2 = _tt_depart(tf[0], depart) if arrive_at is not None else depart
            cand = _tt_finish(tf[0], w2, opts, live)
            tollfree = _keep_comparison(fast, cand, "TomTom", warnings)
        except Exception as e:  # noqa: BLE001
            warnings.append("TomTom could not find a toll-free route (%s)" % _short_err(e))
    return _pick(fast, tollfree, others)


# --------------------------------------------------------------------------- Route dicts

def _finish(b, extra, engine, depart, opts):
    poly_full = b.poly
    total_m = b.total_m or (dc.cum_dist(poly_full)[-1] if len(poly_full) > 1 else 0.0)
    base_s = sum(c["s"] for c in b.chunks)
    segs = tolls.segments_407(b.stretches)
    for sg in segs:
        sg["offset_base_s"] = sg["offset_s"]
        sg["end_base_s"] = sg.pop("end_s", sg["offset_s"])
    hwy_m = sum(c["m"] for c in b.chunks if c.get("hwy"))
    urban_s = sum(c["s"] for c in b.chunks if c["urban"])
    tt = extra.get("tomtom")
    bridges = tolls.bridges_on(poly_full) if len(poly_full) > 1 else []
    r = {"label": "", "engine": engine, "traffic": "live" if engine == "tomtom" else "free-flow",
         "km": round(total_m / 1000.0, 1), "base_s": round(base_s, 1), "chunks": b.chunks,
         "typical_s": 0.0, "plan_s": 0.0, "depart": depart, "arrive": None, "arrive_plan": None,
         "poly": dc.thin(poly_full, MAX_POLY), "hwy_share": round(hwy_m / total_m, 3) if total_m else 0.0,
         "urban_share": round(urban_s / base_s, 3) if base_s else 0.0,
         "roads": _roads_summary(b.road_m, total_m), "etr": segs,
         "toll_407_transponder": 0.0, "toll_407_camera": 0.0,
         "bridges": bridges, "toll_other_cad": 0.0, "has_tolls": False, "toll_unpriced": False,
         "delays": extra.get("delays") or [], "notes": [], "factor_note": "", "_engine_toll": extra.get("engine_toll"),
         "_toll_other": any(p and not _near_407(p) and not _bridge_piece(p, bridges) for p in b.other_tolls),
         "_queried_depart": depart,
         "_return": False, "_live_now": bool(extra.get("live_now"))}
    if tt:
        r["tomtom"] = tt
        nt, hist = tt.get("noTrafficTravelTimeInSeconds"), tt.get("historicTrafficTravelTimeInSeconds")
        if nt and hist:
            r["notes"].append("TomTom: %s with traffic, %s with no traffic, %s typical for this hour"
                              % (dc.fmt_dur(tt.get("travelTimeInSeconds") or 0), dc.fmt_dur(nt), dc.fmt_dur(hist)))
    if b.free_407_east:
        r["notes"].append("Hwy 407 east of Brock Rd is the provincial 407 East, toll-free since 2025-06-01")
    _bridge_totals(r)
    retime(r, depart, opts)
    return r


BORDER_NOTE = "border crossing: passport or NEXUS; check CBSA and CBP wait times; allow 15 to 60 min"
UNPRICED_NOTE = ("unpriced toll: the routing engine reports tolls on this route that are not priced here (only the "
                 "407 ETR and the listed border crossings are); check the toll operator's rates")
BRIDGE_PLAZA_M = 3000.0   # a short toll piece within this of a listed crossing is that crossing's plaza or approach


def _bridge_piece(pts, bridges):
    """True when a toll piece is a listed crossing on this route: the bridge point lies within its radius_m of
    the piece, or the piece is short and lies wholly within BRIDGE_PLAZA_M of it (the plaza)."""
    by_name = dict((br["name"], br) for br in tolls.data()["bridges"])
    cum = dc.cum_dist(pts) if len(pts) >= 2 else [0.0]
    for b in bridges or []:
        br = by_name.get(b["name"])
        if br is None:
            continue
        if len(pts) >= 2 and dc.project(pts, cum, br["lat"], br["lon"])[1] <= br["radius_m"]:
            return True
        if cum[-1] <= BRIDGE_PLAZA_M and all(th.haversine_m(p[0], p[1], br["lat"], br["lon"]) <= BRIDGE_PLAZA_M
                                             for p in pts):
            return True
    return False


def _bridge_totals(r):
    r["toll_other_cad"] = round(sum(b["cad"] for b in r["bridges"] if b.get("cad")), 2)
    r["notes"] = [n for n in r["notes"] if not n.startswith(("border toll: ", "border crossing: ", "unpriced toll: "))]
    for b in r["bridges"]:
        if b.get("cad") is None:
            r["notes"].append("border toll: %s %s (US dollars only, not in the CAD total)" % (b["name"], b["text"]))
        elif b.get("cad_estimated"):
            r["notes"].append("border toll: %s %s (charged in US dollars)" % (b["name"], b["text"]))
    if r["bridges"]:
        r["notes"].append(BORDER_NOTE)
    priced = bool(r["etr"]) or any((b.get("cad") or 0) > 0 or b.get("cad") is None for b in r["bridges"])
    # a toll the engine reports off the 407 and away from every listed crossing (a US toll road, a crossing
    # missing from tolls.json): has_tolls, so the toll-free route is kept, but its price is not known here
    r["toll_unpriced"] = bool(r.get("_toll_other"))
    if r["toll_unpriced"]:
        r["notes"].append(UNPRICED_NOTE)
    r["has_tolls"] = priced or r["toll_unpriced"]


def _money_toll(r):
    return r["toll_407_transponder"] + r["toll_other_cad"]


def _typ(r):
    return r["typical_s"]


def _comparison_label(fast, r):
    """'toll-free', or 'without 407' for a route that leaves the 407 but still pays a border toll."""
    if not r["has_tolls"]:
        return "toll-free"
    if fast["etr"] and not r["etr"]:
        return "without 407"
    return None


def _pick(first, tollfree, others):
    """fastest (by typical time among all the engine's routes, the toll-free one included), toll-free (or
    without 407 on a border trip), then one non-dominated alternative."""
    pool = [first] + [r for r in others if r is not tollfree] + ([tollfree] if tollfree is not None else [])
    fast = min(pool, key=_typ)
    rest = [r for r in pool if r is not fast]
    if tollfree is fast or (tollfree is not None and _comparison_label(fast, tollfree) is None):
        tollfree = None
    if tollfree is None and fast["has_tolls"]:
        free = [r for r in rest if not r["has_tolls"]]
        tollfree = min(free, key=_typ) if free else None
    fast["label"] = "fastest"
    out = [fast]
    if tollfree is not None and fast["has_tolls"] and not tollfree["etr"]:
        tollfree["label"] = _comparison_label(fast, tollfree)
        out.append(tollfree)
    for r in sorted(rest, key=_typ):
        if r is tollfree:
            continue
        same = any(abs(r["km"] - x["km"]) < 0.5 and abs(r["typical_s"] - x["typical_s"]) < 60 for x in out)
        dominated = any(x["typical_s"] <= r["typical_s"] and _money_toll(x) <= _money_toll(r) + 0.005 for x in out)
        if not same and not dominated:
            r["label"] = "alternative"
            out.append(r)
            break
    return out[:3]


def relabel(routes):
    """Re-rank after a retime (weather, arrive-by, a new departure): fastest by typical time, then the
    toll-free (or without 407) route when the fastest has tolls, then the rest as alternatives. Sorts the
    list in place and drops nothing; call it after retiming every route and before lettering them."""
    if not routes:
        return routes
    fast = min(routes, key=_typ)
    rest = sorted([r for r in routes if r is not fast], key=_typ)
    fast["label"] = "fastest"
    order = [fast]
    if fast["has_tolls"]:
        cands = [r for r in rest if not r["etr"] and _comparison_label(fast, r)]
        free = [r for r in cands if not r["has_tolls"]] or cands
        if free:
            free[0]["label"] = _comparison_label(fast, free[0])
            order.append(free[0])
    for r in rest:
        if not any(r is x for x in order):
            r["label"] = "alternative"
            order.append(r)
    routes[:] = order
    return routes


# --------------------------------------------------------------------------- traffic model

def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _in_window(hm, a, b):
    a, b = _hm(a), _hm(b)
    return a <= hm < b if a <= b else (hm >= a or hm < b)


def _row(rows, when, hol):
    hm = when.hour * 60 + when.minute
    weekday = when.weekday() < 5 and when.date().isoformat() not in hol
    for row in rows:
        if row["days"] == "weekday" and not weekday:
            continue
        if any(_in_window(hm, a, b) for a, b in row["windows"]):
            return row
    return rows[-1]


def _getaway_on(when, direction, hol):
    g = traffic_data()["getaway"]
    day = when.date()
    iso = day.isoformat()
    if direction == "leaving":
        nxt = (day + dt.timedelta(days=1)).isoformat()
        ok = day.weekday() == 4 or (day.weekday() < 5 and iso not in hol and nxt in hol)
        win = g["leaving"]["window"]
    elif direction == "returning":
        extra = set(g.get("extra_mondays") or ())   # Easter Monday, Civic Holiday: returns, not 407 holidays
        ok = day.weekday() == 6 or (day.weekday() == 0 and (iso in hol or iso in extra))
        win = g["returning"]["window"]
    else:
        return False
    return ok and _in_window(when.hour * 60 + when.minute, win[0], win[1])


def _getaway_dir(route):
    g = traffic_data()["getaway"]
    if not route["chunks"] or not route["poly"]:
        return None
    o = (route["chunks"][0]["lat"], route["chunks"][0]["lon"])
    d = route["poly"][-1]
    oi, di = _in_area(o[0], o[1], g["area"]), _in_area(d[0], d[1], g["area"])
    if oi and not di:
        return "leaving"
    if di and not oi:
        return "returning"
    return None


def calibration(chunk, engine):
    """Free-flow correction for a keyless engine chunk (traffic.json calibration): 407 ETR chunks and
    highway chunks outside congested areas; 1.0 elsewhere and for TomTom."""
    cal = (traffic_data().get("calibration") or {}).get(engine or "") or {}
    if not cal:
        return 1.0
    if chunk.get("etr"):
        return float(cal.get("etr407", 1.0))
    if chunk.get("hwy") and not chunk.get("urban"):
        return float(cal.get("rural_highway", 1.0))
    return 1.0


def factors(chunk, when, hol, getaway=None, engine=None):
    """(typical, plan, label) multipliers for a keyless engine chunk driven at `when`, including the
    engine's free-flow calibration. 407 ETR chunks use their own rows (congestion-priced, near free flow)."""
    tt = traffic_data()
    k = calibration(chunk, engine)
    if chunk.get("etr") and tt.get("etr407"):
        row = _row(tt["etr407"], when, hol)
        return row["typical"] * k, row["plan"] * k, row["name"]
    if chunk["urban"]:
        row = _row(tt["congested"], when, hol)
        return row["typical"] * k, row["plan"] * k, row["name"]
    if getaway and _getaway_on(when, getaway, hol):
        g = tt["getaway"]
        return g["typical"] * k, g["plan"] * k, "getaway peak"
    row = _row(tt["elsewhere"], when, hol)
    return row["typical"] * k, row["plan"] * k, row["name"]


def tomtom_plan_factor(chunk, when, hol):
    tp = traffic_data()["tomtom_plan"]
    if chunk.get("etr"):
        return tp["other"], "407"
    if chunk["urban"]:
        row = _row(traffic_data()["congested"], when, hol)
        if row["name"] == "weekday peak":
            return tp["congested_peak"], "peak"
        if row["name"] != "night":
            return tp["daytime"], "daytime"
    return tp["other"], "other"


def _weather_name(w):
    names = traffic_data().get("weather_names") or {}
    return names.get(_fmt_x(w)) or "weather"


def _cls(c):
    return "etr" if c.get("etr") else ("urban" if c["urban"] else "other")


def set_stops(route, stops):
    """stops: [(metres along the route, seconds stopped)] -> route["_stops"] = [(base seconds, seconds)], sorted.
    retime puts each stop on the clock at its point, so later chunks and 407 entries are timed after it;
    typical_s, plan_s and arrive stay driving time (drive_plan adds the stops to the arrival)."""
    out = []
    for m_at, dur in stops or []:
        if not dur or dur <= 0:
            continue
        acc_m = acc_s = 0.0
        pos = None
        for c in route["chunks"]:
            if acc_m + c["m"] >= m_at:
                frac = (m_at - acc_m) / c["m"] if c["m"] > 0 else 0.0
                pos = acc_s + c["s"] * max(0.0, min(1.0, frac))
                break
            acc_m += c["m"]
            acc_s += c["s"]
        out.append((round(acc_s if pos is None else pos, 1), float(dur)))
    route["_stops"] = sorted(out)
    return route


def _clock(depart, opts):
    """seconds after `depart` -> wall clock in the trip's time zone (it jumps at a daylight-saving change). Plain
    addition when the UTC offset is the same a day later, which is every night but two a year."""
    tz, reg = opts.get("tz") or "America/Toronto", opts.get("region")
    off0 = th.offset_at_local(depart, tz, reg)[0]
    if th.offset_at_utc(depart - off0 + dt.timedelta(days=1), tz, reg)[0] == off0:
        return lambda s: depart + dt.timedelta(seconds=s)
    return lambda s: dc.add_s(depart, s, tz, reg)


TRAFFIC_TZ = "America/Toronto"   # the 407 rate bands and the congested areas' rows are Ontario Eastern clock times


def _toronto(opts):
    """trip wall clock -> Toronto wall clock, for the 407 rate bands and the congested-area traffic rows (every
    area in traffic.json is in the Eastern zone); None when the trip is on Toronto time (--tz for another zone)."""
    tz, reg = opts.get("tz") or TRAFFIC_TZ, opts.get("region")
    if tz == TRAFFIC_TZ:
        return None

    def conv(when):
        utc = when - th.offset_at_local(when, tz, reg)[0]
        return utc + th.offset_at_utc(utc, TRAFFIC_TZ, None)[0]
    return conv


def retime(route, depart, opts):
    """Recompute typical and plan times, arrivals, 407 entry times and toll prices for `depart`. Stops from
    set_stops move the clock (later chunks and 407 entries) but not typical_s, plan_s or arrive."""
    w = float(opts.get("weather") or 1.0)
    hol = set(opts.get("holidays") or ()) | tolls.holidays_default()
    tomtom = route["engine"] == "tomtom"
    q = route.get("_queried_depart")
    # a live TomTom time (departAt=now, still timed for that departure) already has the weather in it
    live = bool(tomtom and route.get("_live_now") and not route.get("_return") and q is not None
                and abs((depart - q).total_seconds()) <= 600)
    wt = 1.0 if live else w
    getaway = None if tomtom else _getaway_dir(route)
    clock = _clock(depart, opts)
    tor = _toronto(opts)
    t = p = base = 0.0
    marks = [(0.0, 0.0)]   # (base seconds, clock seconds: driving plus the stops passed)
    stats = {}   # (class, label) -> [base seconds, typical factor x base seconds, plan factor x base seconds]
    areas = []
    stops = list(route.get("_stops") or [])
    si, waited = 0, 0.0
    for c in route["chunks"]:
        while si < len(stops) and stops[si][0] <= base + 1e-6:   # a stop at the chunk's start
            waited += stops[si][1]
            si += 1
            marks.append((base, t + waited))
        when = clock(t + waited)
        if tor and (c.get("etr") or c["urban"]):
            when = tor(when)
        if tomtom:
            ft = 1.0
            fp, lab = tomtom_plan_factor(c, when, hol)
        else:
            ft, fp, lab = factors(c, when, hol, getaway, route["engine"])
        key = (_cls(c), lab)
        st = stats.setdefault(key, [0.0, 0.0, 0.0])
        st[0] += c["s"]
        st[1] += c["s"] * ft
        st[2] += c["s"] * fp
        if c["urban"] and not c.get("etr") and c.get("area") and c["area"] not in areas:
            areas.append(c["area"])
        while si < len(stops) and stops[si][0] < base + c["s"]:   # a stop inside the chunk, at its own point
            b_at = stops[si][0]
            t_at = t + c["s"] * ft * wt * (b_at - base) / c["s"]
            marks.append((b_at, t_at + waited))
            waited += stops[si][1]
            si += 1
            marks.append((b_at, t_at + waited))
        t += c["s"] * ft * wt
        p += c["s"] * fp * wt
        base += c["s"]
        marks.append((base, t + waited))
    route["typical_s"] = round(t, 1)
    route["plan_s"] = round(max(p, t), 1)
    route["depart"] = depart
    route["arrive"] = clock(route["typical_s"])
    route["arrive_plan"] = clock(route["plan_s"])

    def to_typ(b):
        # a stop at b adds a mark at the same base position: the time after the stop is the one returned
        out, b = marks[-1][1], max(0.0, b)
        for (b0, t0), (b1, t1) in zip(marks, marks[1:]):
            if b1 > b0 and b0 <= b <= b1:
                out = t0 + (t1 - t0) * (b - b0) / (b1 - b0)
                if b < b1:
                    return out
        return out

    tot_tp = tot_cam = 0.0
    for sg in route["etr"]:
        sg["offset_s"] = round(to_typ(sg["offset_base_s"]), 1)
        entry = clock(sg["offset_s"]).replace(second=0, microsecond=0)
        at = tor(entry) if tor else entry   # entry_time stays on the trip's clock; the band is Toronto time
        pt = tolls.price_407(sg, at, True)
        pc = tolls.price_407(sg, at, False)
        sg.update({"entry_time": entry, "band_start": pt["band_start"], "day_type": pt["day_type"],
                   "toll_transponder": pt["toll"], "toll_camera": pc["toll"], "zones": pt["zones"],
                   "note": pc["note"], "band_toronto": bool(tor)})
        tot_tp += pt["toll"]
        tot_cam += pc["toll"]
    route["toll_407_transponder"] = round(tot_tp, 2)
    route["toll_407_camera"] = round(tot_cam, 2)
    route["factor_note"] = _factor_note(route, stats, areas, w, depart, live)
    return route


def _factor_note(route, stats, areas, w, depart, live=False):
    def avg(cls, idx):
        b = sum(v[0] for k, v in stats.items() if k[0] == cls)
        f = sum(v[idx] for k, v in stats.items() if k[0] == cls)
        return (f / b) if b else None, b

    def top(cls):
        best = max(((v[0], k[1]) for k, v in stats.items() if k[0] == cls), default=(0, ""))
        return best[1]

    wtxt = "; x%s %s" % (_fmt_x(w), _weather_name(w)) if abs(w - 1.0) > 1e-6 else ""
    if route["engine"] == "tomtom":
        txt = "TomTom live traffic"
        q = route.get("_queried_depart")
        if live and wtxt:
            wtxt = "; no x%s for %s: live traffic already reflects the weather" % (_fmt_x(w), _weather_name(w))
        if route.get("_return") and route.get("tomtom_return") == "queried":
            txt = "TomTom traffic time for the return at %s" % dc.fmt_clock(q)
        elif route.get("_return"):
            txt = "TomTom traffic time from the outbound query, not re-queried for the return"
        elif q is not None and abs((q - depart).total_seconds()) > 600:
            txt = "TomTom traffic time for a %s departure, not re-queried for %s" % (dc.fmt_clock(q), dc.fmt_clock(depart))
        tot = sum(v[0] for v in stats.values())
        fp = sum(v[2] for v in stats.values()) / tot if tot else 1.0
        return "%s; plan x%s for reliability%s" % (txt, _fmt_x(fp), wtxt)
    parts = []
    fe, _ = avg("etr", 1)
    if fe is not None:
        parts.append("x%s on the 407 ETR%s" % (_fmt_x(fe), " (weekday peak)" if "peak" in top("etr") else ""))
    fu, _ = avg("urban", 1)
    if fu is not None:
        parts.append("x%s in %s %s" % (_fmt_x(fu), "/".join(areas) or "cities", top("urban")))
    fo, _ = avg("other", 1)
    if fo is not None:
        lab = top("other")
        parts.append("x%s outside cities%s" % (_fmt_x(fo), " (getaway peak)" if lab == "getaway peak" else ""))
    head = "calibrated free-flow time " if (traffic_data().get("calibration") or {}).get(route["engine"]) else \
        "free-flow time "
    return head + ", ".join(parts or ["x1"]) + wtxt


def reverse_for_return(route, depart, opts):
    """The same roads driven back: reversed geometry and chunks, 407 direction flipped, retimed for `depart`."""
    r = copy.deepcopy(route)
    r["_stops"] = []   # the outbound stops do not apply backwards; drive_plan sets the return's own
    chunks = route["chunks"]
    end = route["poly"][-1] if route["poly"] else (chunks[-1]["lat"], chunks[-1]["lon"]) if chunks else (0.0, 0.0)
    rev = []
    for i in range(len(chunks) - 1, -1, -1):
        c = dict(chunks[i])
        nxt = chunks[i + 1] if i + 1 < len(chunks) else {"lat": end[0], "lon": end[1]}
        c["lat"], c["lon"] = nxt["lat"], nxt["lon"]
        rev.append(c)
    r["chunks"] = rev
    r["poly"] = list(reversed(route["poly"]))
    base = sum(c["s"] for c in chunks)
    segs = []
    for sg in reversed(route["etr"]):
        n = {"entry": sg["exit"], "entry_no": sg["exit_no"], "entry_km": sg["exit_km"],
             "exit": sg["entry"], "exit_no": sg["entry_no"], "exit_km": sg["entry_km"], "km": sg["km"],
             "direction": "W" if sg["direction"] == "E" else "E",
             "offset_base_s": round(base - sg["end_base_s"], 1), "end_base_s": round(base - sg["offset_base_s"], 1)}
        n["offset_s"] = n["offset_base_s"]
        segs.append(n)
    r["etr"] = segs
    total_m = route["km"] * 1000.0 if route.get("km") else (dc.cum_dist(route["poly"])[-1] if len(route["poly"]) > 1 else 0.0)
    r["bridges"] = tolls.flip_bridges(route["bridges"], total_m)   # same crossings, the other way
    r["delays"] = []
    r["roads"] = list(reversed(route["roads"]))
    r["notes"] = [n for n in route["notes"] if not n.startswith("TomTom: ") and not n.startswith("407 option added")]
    r["notes"].append("return on the same roads")
    r["_return"] = True
    _bridge_totals(r)
    if r["engine"] == "tomtom" and opts.get("tomtom_key"):
        _tomtom_return(r, depart, opts)
    return retime(r, depart, opts)


def _tomtom_return(r, depart, opts):
    """Ask TomTom for the drive back at its own time (one call, tolls avoided when the route has none) and spread
    its traffic time over the reversed route's chunks in proportion, so the 407 entry times and tolls follow it.
    A reply on other roads (with or without the 407 unlike the route, or a length more than RETURN_KM_TOL off)
    is not used: the outbound time stays. Sets r["tomtom_return"] to "queried" or "reused" (with
    r["tomtom_return_why"])."""
    poly = r.get("poly") or []
    why = None
    try:
        if len(poly) < 2:
            raise RuntimeError("no route line")
        rts = _tomtom_fetch(poly[0], poly[-1], depart, opts, None, not r["has_tolls"])
    except Exception as e:  # noqa: BLE001  (the reason is printed: it must not carry the key)
        key = opts.get("tomtom_key") or ""
        err = str(e).replace(urllib.parse.quote(key), "KEY").replace(key, "KEY") if key else str(e)
        rts, why = [], "TomTom did not answer: %s" % _short_err(err)
    best = None
    for rt in rts:
        summ = rt.get("summary") or {}
        km = float(summ.get("lengthInMeters") or 0.0) / 1000.0
        secs = float(summ.get("travelTimeInSeconds") or 0.0)
        b, _x = _tomtom_route(rt)
        same_407 = bool(tolls.segments_407(b.stretches)) == bool(r["etr"])
        if secs > 0 and r.get("km") and abs(km - r["km"]) <= RETURN_KM_TOL * r["km"] and same_407:
            if best is None or abs(km - r["km"]) < best[0]:
                best = (abs(km - r["km"]), secs, summ)
    if best is None:
        r["tomtom_return"] = "reused"
        r["tomtom_return_why"] = why or "TomTom's return took other roads"
        log("TomTom return not used: %s" % r["tomtom_return_why"])
        return r
    base = sum(c["s"] for c in r["chunks"])
    k = best[1] / base if base > 0 else 1.0
    for c in r["chunks"]:
        c["s"] *= k
    for sg in r["etr"]:
        sg["offset_base_s"] = round(sg["offset_base_s"] * k, 1)
        sg["end_base_s"] = round(sg["end_base_s"] * k, 1)
        sg["offset_s"] = sg["offset_base_s"]
    r["base_s"] = round(best[1], 1)
    r["tomtom"] = {key: best[2].get(key) for key in ("travelTimeInSeconds", "noTrafficTravelTimeInSeconds",
                                                     "historicTrafficTravelTimeInSeconds", "trafficDelayInSeconds",
                                                     "departureTime", "arrivalTime", "lengthInMeters")}
    r["_queried_depart"] = depart
    r["tomtom_return"] = "queried"
    r.pop("tomtom_return_why", None)
    return r


# --------------------------------------------------------------------------- entry point

ENGINES = {"tomtom": _tomtom, "valhalla": _valhalla, "osrm": _osrm}


def get_routes(o, d, depart, opts, warnings, arrive_at=None):
    """Route dicts, fastest first, at most 3 (fastest, toll-free, alternative); [] when every engine failed."""
    provider = (opts.get("provider") or "auto").lower()
    key = opts.get("tomtom_key")
    chain = []
    if provider == "tomtom" and not key:
        warnings.append("--provider tomtom needs TOMTOM_API_KEY; using the keyless engines (no live traffic)")
    if key and provider in ("auto", "tomtom"):
        chain.append("tomtom")
    chain += ["osrm", "valhalla"] if provider == "osrm" else ["valhalla", "osrm"]
    for i, eng in enumerate(chain):
        try:
            routes = ENGINES[eng](o, d, depart, opts, warnings, arrive_at)
        except Exception as e:  # noqa: BLE001
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            warnings.append("%s routing failed (%s)%s" % (ENGINE_NAMES[eng], _short_err(e),
                                                         "; using %s" % ENGINE_NAMES[nxt] if nxt else ""))
            log("%s routing failed: %s" % (ENGINE_NAMES[eng], e))
            continue
        if routes:
            return routes
    warnings.append("every routing engine failed, so there are no driving times; check Google Maps")
    return []
