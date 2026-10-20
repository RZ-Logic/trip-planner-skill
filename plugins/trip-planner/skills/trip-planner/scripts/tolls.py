"""407 ETR toll engine and border bridge tolls for the driving planner.

Standard library only. Python 3.8+. Data: references/driving/tolls.json (2026 light-vehicle rate chart,
41 interchanges, fees, holidays priced as weekend, border bridges). The 407 ETR calculator API needs
reCAPTCHA, so tolls are computed here from the published chart.
"""
from __future__ import annotations

import datetime as dt

import drive_common as dc

th = dc.th


def data():
    return dc.load_data("tolls")


def _etr():
    return data()["etr407"]


def _chain():
    """Interchanges sorted by km: (poly [(lat, lon)], km list, interchange list)."""
    ics = sorted(_etr()["interchanges"], key=lambda ic: ic["km"])
    return [(ic["lat"], ic["lon"]) for ic in ics], [ic["km"] for ic in ics], ics


def chain_km(lat, lon):
    """Kilometre position of (lat, lon) projected onto the interchange chain, and metres off it."""
    poly, kms, _ = _chain()
    along, off, _i = dc.project(poly, kms, lat, lon)
    return along, off


def snap(km):
    """Nearest interchange to a chain km."""
    _, _, ics = _chain()
    return min(ics, key=lambda ic: abs(ic["km"] - km))


def segments_407(stretches):
    """stretches: [{"points": [(lat, lon), ...], "offset_s": float}] = pieces of a route on the 407 ETR in
    driving order. Clips geometry east of the toll-free provincial 407 East, projects both ends onto the
    interchange chain, snaps to the nearest interchange and merges pieces whose gap is under 0.5 km.
    A stretch may also carry "end_s" (seconds at its last point); merged segments then carry "end_s" too.
    With "times" (seconds at each point, parallel to "points"), offset_s and end_s move to the first and
    last kept point, so a stretch that starts or ends on the free 407 East is timed at Brock Rd."""
    east = _etr()["east_end_lon"]
    raw = []
    for st in stretches:
        allp = st.get("points") or []
        idx = [i for i, p in enumerate(allp) if p[1] < east]
        pts = [allp[i] for i in idx]
        if len(pts) < 2:
            continue
        k0, _ = chain_km(pts[0][0], pts[0][1])
        k1, _ = chain_km(pts[-1][0], pts[-1][1])
        times = st.get("times")
        piece = {"k0": k0, "k1": k1, "offset_s": float(st.get("offset_s") or 0.0)}
        if st.get("end_s") is not None:
            piece["end_s"] = float(st["end_s"])
        if times and len(times) == len(allp):
            piece["offset_s"] = float(times[idx[0]])
            piece["end_s"] = float(times[idx[-1]])
        if raw and abs(raw[-1]["k1"] - k0) < 0.5 and (raw[-1]["k1"] - raw[-1]["k0"]) * (k1 - k0) >= 0:
            raw[-1]["k1"] = k1
            if "end_s" in piece:
                raw[-1]["end_s"] = piece["end_s"]
            continue
        raw.append(piece)
    out = []
    for p in raw:
        a, b = snap(p["k0"]), snap(p["k1"])
        if a["km"] == b["km"]:
            continue
        seg = {"entry": a["name"], "entry_no": a["exit"], "entry_km": a["km"],
               "exit": b["name"], "exit_no": b["exit"], "exit_km": b["km"],
               "km": round(abs(b["km"] - a["km"]), 1), "direction": "E" if b["km"] > a["km"] else "W",
               "offset_s": round(p["offset_s"], 1)}
        if "end_s" in p:
            seg["end_s"] = round(p["end_s"], 1)
        out.append(seg)
    return out


def band_index(when, starts):
    """Index of the band whose start <= clock time; before the first start wraps to the last band."""
    hm = when.hour * 60 + when.minute
    idx = len(starts) - 1
    for i, s in enumerate(starts):
        h, m = s.split(":")
        if hm >= int(h) * 60 + int(m):
            idx = i
    return idx


esa_holidays = dc.th.esa_holidays   # one copy of the Ontario ESA rule, in trip_hubs


_HOL = {}


def _stored_years():
    return set(int(d[:4]) for d in _etr()["holidays_priced_as_weekend"])


def holidays_default():
    """Stored 407 holiday list, plus ESA holidays computed for the years the list does not cover."""
    key = id(_etr())
    if key not in _HOL:
        stored = set(_etr()["holidays_priced_as_weekend"])
        years = _stored_years()
        for y in range(min(years) - 2, max(years) + 11):
            if y not in years:
                stored |= esa_holidays(y)
        _HOL.clear()
        _HOL[key] = stored
    return set(_HOL[key])


def price_407(seg, entry_time, transponder, holidays=None):
    """Toll in CAD for one 407 ETR trip (seg from segments_407), priced at the band of entry_time."""
    e = _etr()
    hol = holidays_default() if holidays is None else set(holidays)
    day = entry_time.date()
    dtype = "weekend" if day.weekday() >= 5 or day.isoformat() in hol else "weekday"
    starts = e["bands"][dtype]
    band = band_index(entry_time, starts)   # early morning wraps to the day's own night band (flat rate)
    direction = seg["direction"]
    rates = e["rates"][direction][dtype][band]
    a, b = sorted((seg["entry_km"], seg["exit_km"]))
    cents, zones = 0.0, []
    for z in e["zones"]:
        over = min(b, z["km_to"]) - max(a, z["km_from"])
        if over > 1e-9:
            r = rates[z["zone"] - 1]
            cents += over * r
            zones.append({"zone": z["zone"], "km": round(over, 2), "cents_per_km": r})
    toll = cents / 100.0 + e["trip_toll_charge"] + (0.0 if transponder else e["camera_charge"])
    notes = ["%d rates" % e["year"]]
    if entry_time.year > e["year"]:
        notes[0] += "; %d rates not published yet" % entry_time.year
    if holidays is None and entry_time.year not in _stored_years():
        notes.append("%d holidays computed from Ontario ESA rules" % entry_time.year)
    if not transponder:
        notes.append("plus $%.0f account fee in any month you use the 407 without a transponder"
                     % e["account_fee_monthly"])
    return {"toll": round(toll, 2), "band_start": starts[band], "day_type": dtype, "direction": direction,
            "km": round(b - a, 1), "zones": zones, "rates_year": e["year"], "note": "; ".join(notes)}


SIDE_M = 200.0   # a crossing passes this close to both ends of the bridge (canada_side, us_side)


def _usd_cad():
    return data().get("usd_cad") or {}


def bridge_price(br, direction):
    """(cad, usd, text, estimated) for crossing bridge `br` in `direction` ('into_us' or 'into_canada').
    A toll charged only in US dollars is converted at the stored Bank of Canada rate (estimated=True)."""
    price = br.get(direction)
    if not price:
        return 0.0, None, "no toll this way", False
    cad, usd, text = price.get("cad"), price.get("usd"), price.get("text") or ""
    fx = _usd_cad()
    if cad is None and usd and fx.get("rate"):
        cad = round(usd * fx["rate"], 2)
        return cad, usd, "%s; about C$%.2f at the Bank of Canada rate of %s" % (text, cad, fx.get("date", "")), True
    return cad, usd, text, False


def _bridge_row(br, direction, along):
    cad, usd, text, est = bridge_price(br, direction)
    row = {"name": br["name"], "direction": direction, "cad": cad, "text": text, "usd": usd,
           "source": br.get("source", ""), "along_m": round(along)}
    if est:
        row["cad_estimated"] = True
    return row


def bridges_on(poly, side_m=SIDE_M):
    """Border bridges and the tunnel the route crosses, with direction and the toll for that direction.
    The route must pass within side_m of both ends of the crossing (canada_side and us_side); the order it
    reaches them gives the direction. A road along the river bank or parallel to a tunnel passes one end
    at most, so it does not count."""
    if not poly or len(poly) < 2:
        return []
    cum = dc.cum_dist(poly)
    box = dc.bbox_of(poly, 1000)
    out = []
    for br in data()["bridges"]:
        if not th.in_bbox(br["lat"], br["lon"], box):
            continue
        along, off, _ = dc.project(poly, cum, br["lat"], br["lon"])
        if off > br["radius_m"]:
            continue
        ca, us = br["canada_side"], br["us_side"]
        a_ca, off_ca, _ = dc.project(poly, cum, ca[0], ca[1])
        a_us, off_us, _ = dc.project(poly, cum, us[0], us[1])
        if off_ca > side_m or off_us > side_m or abs(a_us - a_ca) < 1.0:
            continue
        direction = "into_us" if a_ca < a_us else "into_canada"
        out.append(_bridge_row(br, direction, along))
    out.sort(key=lambda b: b["along_m"])
    return out


def flip_bridges(bridges, total_m):
    """The same crossings driven the other way (return trip), priced for the new direction."""
    by_name = dict((b["name"], b) for b in data()["bridges"])
    out = []
    for b in reversed(bridges or []):
        br = by_name.get(b["name"])
        if br is None:
            continue
        direction = "into_canada" if b["direction"] == "into_us" else "into_us"
        out.append(_bridge_row(br, direction, max(0.0, total_m - b.get("along_m", 0))))
    return out
