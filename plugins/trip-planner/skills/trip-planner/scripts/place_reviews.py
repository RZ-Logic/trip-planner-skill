"""Parking rates reported in recent Google reviews, through the official Places API (New). Optional.

Needs GOOGLE_PLACES_API_KEY (or key=...). Without a key review_prices() returns None and prints nothing.
Google Maps itself is never scraped (Google's terms); only the documented API is called.

Public API
    review_prices(lat, lon, radius_m, warnings, key=None, today=None) -> dict or None
    extract_mentions(text, date="", rating=None) -> [mention]
    match_lots(places, lots, max_m=80.0, dest=None) -> [extra candidate lot]
    summary_text(mentions, limit=4) -> "reviews mention $15 flat (Aug 2026), ..."

Docs facts (read 2026-10-10)
  Nearby Search (New): POST https://places.googleapis.com/v1/places:searchNearby
    https://developers.google.com/maps/documentation/places/web-service/nearby-search
    Headers X-Goog-Api-Key and X-Goog-FieldMask (required; comma list, no spaces, e.g. places.displayName).
    Body: includedTypes (Table A, up to 50), maxResultCount 1..20 (default 20), rankPreference POPULARITY
    (default) or DISTANCE, locationRestriction.circle {center {latitude, longitude}, radius 0 < r <= 50000 m}.
    Reply {"places": [Place]} with only the masked fields; {} when nothing matches.
  Parking types in Table A: parking, parking_garage, parking_lot (the last two added 2026-02-12), park_and_ride.
    https://developers.google.com/maps/documentation/places/web-service/place-types
  Text Search (New): POST https://places.googleapis.com/v1/places:searchText, textQuery required, includedType,
    locationBias circle or rectangle, locationRestriction rectangle only, pageSize 1..20 (maxResultCount is
    deprecated there), at most 60 results across pages. Not used here.
    https://developers.google.com/maps/documentation/places/web-service/text-search
  Place fields: displayName {text, languageCode}, formattedAddress, location {latitude, longitude},
    googleMapsUri, types, reviews (at most 5). Review: name, relativePublishTimeDescription, rating (1..5),
    text and originalText {text, languageCode}, authorAttribution {displayName, uri, photoUri}, publishTime
    (RFC 3339, e.g. 2014-10-02T15:01:23Z), flagContentUri, googleMapsUri.
    https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places
  Billing: a Nearby or Text Search request bills at the highest SKU any masked field triggers. displayName,
    formattedAddress, location, googleMapsUri and types are Pro; rating is Enterprise; reviews is
    Enterprise + Atmosphere. So this one call is a "Nearby Search Enterprise + Atmosphere" event: 1,000 free
    events per month, then USD 40.00 per 1,000 (first paid tier). Billing must be enabled on the project.
    https://developers.google.com/maps/documentation/places/web-service/data-fields
    https://developers.google.com/maps/billing-and-pricing/pricing
    https://developers.google.com/maps/documentation/places/web-service/usage-and-billing
  Policies: show "Google Maps" attribution (logo or the unaltered text) when not on a Google map; always
    credit the author when displaying reviews (name, and photo and link when space allows); let users open the
    source on Google Maps via googleMapsUri; give a notice of how reviews are ordered and filtered (relevance
    by default); for places in France show the visit month and year; recommended: say when a review is
    translated and that Google does not verify reviews. Caching: place IDs may be stored indefinitely; other
    Places content must not be pre-fetched, cached or stored beyond the exceptions (the Service Specific Terms
    allow latitude/longitude for up to 30 consecutive calendar days).
    https://developers.google.com/maps/documentation/places/web-service/policies
    https://cloud.google.com/maps-platform/terms/maps-service-terms
  So: no disk cache at all here (drive_common.fetch with max_age_h=0), the key goes only in the
  X-Goog-Api-Key header (never in a URL, a warning or a cache key), and every quote keeps its author,
  month and Google Maps link.

Standard library only. Python 3.8+.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import drive_common as dc  # noqa: E402
import trip_hubs as th  # noqa: E402

ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"
FIELD_MASK = ",".join("places." + f for f in
                      ("displayName", "formattedAddress", "location", "googleMapsUri", "types", "reviews"))
INCLUDED_TYPES = ["parking", "parking_garage", "parking_lot"]
SOURCE = "Google reviews via Places API"
ATTRIBUTION = ("Rates quoted from Google Maps reviews: unverified review reports, not confirmed by the operator, "
               "and Google does not verify reviews. Google returns at most 5 reviews per place, ordered by "
               "relevance; only reviews from the last 18 months are used. Each quote names its Google Maps "
               "author; open the place's Google Maps link to read the full review.")
MAX_AMOUNT = 100.0
KEEP_MONTHS = 18
SNIPPET_MAX = 120
UNITS = ("flat", "day", "hour", "half hour", "minutes", "evening", "overnight", "weekend", "weekday", "event",
         "unknown")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# --------------------------------------------------------------------------- price extraction

_N = r"\d{1,3}(?:,\d{3})*(?:[.,]\d{1,2})?(?!\d)"
_AMT = re.compile(
    r"(?:\b(?:C|CA|CAD)\s?)?\$\s?(?P<a>%s)(?:\s?(?:-|to)\s?\$?\s?(?P<b>%s))?" % (_N, _N)
    + r"|\bCAD\s?(?P<e>%s)" % _N
    + r"|(?<![\w$.,])(?P<c>%s)(?:\s?(?:-|to|a|\u00e0)\s?(?P<d>%s))?\s?(?:\$(?!\s?\d)|dollars?\b|bucks\b|CAD\b)" % (_N, _N),
    re.I)

_PARKING_WORDS = re.compile(
    r"\bpark(?:ed|ing|ade)?\b|\blots?\b|garage|\brates?\b|\bhours?\b|\bhrs?\b|\bhourly\b|\bday\b|daily|\bflat\b|"
    r"\bmax\b|maximum|weekends?|evenings?|nights?|events?|\bcharged?\b|\bcharges\b|\bpaid\b|\bpay\b|\bcosts?\b|"
    r"\bspots?\b|\bfee\b|/\s?h(?:r|our)?\b|stationnement|heure|jour|journ[e\u00e9]e|pay[e\u00e9]|tarif|soir[e\u00e9]e|"
    r"fin de semaine|forfait", re.I)

_BENIGN = re.compile(
    r"ticket\s+(?:machine|booth|kiosk|stub|dispenser|reader|at the (?:gate|entrance|exit))|"
    r"(?:take|took|taking|grab|grabbed|pull|pulled|print|printed|get|got|insert|inserted|scan|scanned)\s+"
    r"(?:a|the|your|my)\s+ticket\s+(?:at|from|on|when|upon|as)\b", re.I)
_SKIP = re.compile(
    r"\btick(?:et|eted|ets)\b|\bfined\b|\bfines\b|\b(?:a|the|parking)\s+fine\b|\$\s?\d+(?:\.\d+)?\s+fine\b|"
    r"\bpenalty\b|\binfraction\b|\bviolation\b|\bby-?law\b|\benforcement\b|\btow(?:ed|ing|s)?\b|\bimpound|"
    r"\btip(?:s|ped)?\b|gratuity|\bfood\b|\bmeals?\b|\blunch\b|\bdinner\b|\bbreakfast\b|burger|pizza|coffee|"
    r"\bdrinks?\b|\bbeers?\b|snack|restaurant|popcorn|hot ?dog|\btolls?\b|\b407\b|\betr\b|\buber\b|\btaxi\b|"
    r"\bcab\b|\bgas\b|\bfuel\b|monthly|(?:per|a|an|/)\s?(?:month|mo|year|yr)\b|\bannual|\byearly\b|"
    r"\bpermits?\b|membership|subscription|"
    r"\bchargers?\b|\bev\b|electric vehicle|charging (?:station|stall|spot|port|point)s?|car ?wash|detailing|"
    r"\brefund|voucher|\bdeposit|"
    r"\bamende\b|\bcontravention\b|remorqu", re.I)
# R3 (b), R6: one amount, judged by the words right around it: a fee on top (but not a fee that is the parking
# itself, '$15 parking fee'), a difference, a past price, another purchase ('$90 for the hotel')
_SKIP_AFTER = re.compile(
    r"^\s*(?!(?:\w+\s+){0,2}?(?:parking|lot|garage|entry|entrance|daily|day)\s+fee\b)(?:\w+\s+){0,3}(?:fee|surcharge)\b|"
    r"^\s*(?:\w+\s+){0,2}(?:service|convenience|processing|booking|transaction)\s+charge\b|"
    r"^\s*(?:extra|cheaper|more|less|off)\b|^\s*(?:\w+\s+){0,2}?(?:back then|(?:years?|months?) ago)\b|"
    r"^\s*(?:for|on)\s+(?:the|our|my|a|an)?\s*(?:hotel|motel|room|suite)s?\b", re.I)
_SKIP_BEFORE = re.compile(r"(?:more|less) than\s*$|\bsave[sd]?\s*$|"
                          r"(?:used to be|was previously|previously|last year|before (?:it|they)|old rate|originally)"
                          r"\s*(?:\w+\s+)?$|\bback in (?:19|20)\d\d\b(?:\s+\w+){0,3}\s*$|"
                          r"\b(?:recall|remember)(?:ed)?\s+(?:paying|it (?:was|being))\s*$|"
                          r"\b(?:hotel|motel|room|suite)s?\s+(?:(?!park\w*\b|lot\b|garage\b)\w+\s+)?"
                          r"(?:was|is|cost|costs|came to)\s*$", re.I)

_HALF = re.compile(r"^\s*(?:/|per|an?|every|each|for|for (?:a|each|every))?\s*(?:half[- ]?(?:an? )?hour|"
                   r"30\s?min(?:ute)?s?\b|1/2\s?(?:hr|hour)|demi-heure)", re.I)
_HOUR = re.compile(r"^\s*(?:(?:/|per|an?|each|every|for (?:the )?first|for (?:an?|each|one)|"
                   r"(?:every|each|per|an?) additional)\s*(?:hr|hour|h)\b|hourly\b|"
                   r"p/h\b|(?:de l'|l'|par )heure\b|/\s?h\b)", re.I)
_MINUTES = re.compile(r"^\s*(?:/|per|every|each|for)\s*(\d{1,2})\s*min", re.I)
_DUR_WIDE = re.compile(r"\b(?:for|parked)\s+(?:about\s+|around\s+)?(\d+(?:\.\d)?|an?|one|two|three|four|five|six)\s*"
                       r"(?:hours?|hrs?)\b", re.I)
_DAY_AFTER = re.compile(r"^\s*(?:(?:/|per|a|each|for (?:the|a))\s*(?:whole |entire |full )?day\b|daily\b|day max|"
                        r"day rate|max(?:imum)?\b|all[- ]day\b|full day\b|for 24\s?h|24\s?h|"
                        r"(?:par |la )?jour(?:n[e\u00e9]e)?\b)", re.I)
_DAY_BEFORE = re.compile(r"(?:daily|day|all[- ]day|full[- ]day|24\s?h(?:ours?|rs?)?)\s*(?:max(?:imum)?|rate|"
                         r"cap|parking)?\s*(?:of|is|was|for|:|=|at)?\s*$|\bmax(?:imum)?\s*(?:of|is|was|:)?\s*$",
                         re.I)
_WORDNUM = {"a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0, "six": 6.0}
_DUR_AFTER = re.compile(r"^\s*for\s+(?:the\s+first\s+)?(\d+(?:\.\d)?|a|an|one|two|three|four|five|six)\s*"
                        r"(?:hours?|hrs?|h)\b", re.I)
_DUR_BEFORE = re.compile(r"(\d+(?:\.\d)?|an?|one|two|three|four|five|six)\s*(?:hours?|hrs?|h)\s*(?:of parking\s*)?"
                         r"(?:for|is|was|=|cost(?:s|ed)?(?: me)?|:|-|were|came to|,?\s*(?:and\s+)?(?:i\s+|we\s+)?"
                         r"paid|and it (?:was|cost))\s*$", re.I)
_CTX = [
    ("event", re.compile(r"\bevents?\b|[e\u00e9]v[e\u00e9]nement|\bgames?\b|game ?day|\bconcerts?\b|\bmatch\b|\bleafs\b|"
                         r"\bjays\b|\braptors\b|\bargos\b|\btfc\b|festival|\bcne\b", re.I)),
    ("overnight", re.compile(r"(?:per|a|/)\s?night\b|overnight", re.I)),
    ("evening", re.compile(r"evenings?|\bnights?\b|after\s?[4-9](?::\d\d)?\s*(?:pm|p\.m\.)?\b|soir", re.I)),
    ("weekend", re.compile(r"weekends?|saturdays?|sundays?|\bsat\b|\bsun\b|fin de semaine", re.I)),
    ("weekday", re.compile(r"weekdays?|mon(?:day)?\s*(?:-|to)\s*fri(?:day)?|business days|en semaine", re.I)),
    ("day", re.compile(r"\bday\b|daily|\bmax(?:imum)?\b|24\s?h|journ[e\u00e9]e|\bjour\b", re.I)),
    ("flat", re.compile(r"\bflat\b|one price|fixed (?:rate|price)|forfait", re.I)),
]
_STRONG = re.compile(r"[.!?]+(?=\s|$)\s*|[;\n]+\s*|\s+(?:but|however|whereas|while|though|although|mais)\s+",
                     re.I)
_WEAK = re.compile(r"(?<!\d),\s*|,(?=\s)\s*|[:()]\s*|\s+-\s+")


def _num(s):
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        return float(s.replace(",", ""))
    return float(s.replace(",", "."))


def _clean(text):
    text = (text or "").replace("\u2013", "-").replace("\u2014", "-").replace("\u2012", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _spans(text, rx, lo, hi):
    """Split text[lo:hi] by `rx`, as (start, end) spans."""
    out, pos = [], lo
    for m in rx.finditer(text, lo, hi):
        if m.start() > pos:
            out.append((pos, m.start()))
        pos = m.end()
    if pos < hi:
        out.append((pos, hi))
    return out


def _snippet(text, s, e):
    """At most SNIPPET_MAX characters of text around [s, e), cut at word edges, '...' where trimmed."""
    room = SNIPPET_MAX - (e - s) - 6
    left = min(s, max(0, room * 2 // 5))
    right = min(len(text) - e, max(0, room - left))
    left = min(s, max(0, room - right))
    a, b = s - left, e + right
    if a > 0:
        sp = text.find(" ", a, s)
        a = sp + 1 if sp != -1 else a
    if b < len(text):
        sp = text.rfind(" ", e, b)
        b = sp if sp != -1 else b
    out = ("..." if a > 0 else "") + text[a:b].strip() + ("..." if b < len(text) else "")
    return out[:SNIPPET_MAX]


def _nearest(ctx, pos, units):
    """The context label from `units` whose match lies nearest the amount at `pos` in ctx (ties: list order), or
    None. An event word anywhere in the clause wins ('on event nights they charge $30'); a nearer weekday word
    blocks the weekend label ('$6 on weekends and $18 weekdays')."""
    if "event" in units and _CTX[0][1].search(ctx):
        return "event"
    best = None
    for rank, (unit, rx) in enumerate(_CTX):
        if unit not in units and unit != "weekday":
            continue
        for m in rx.finditer(ctx):
            d = pos[0] - m.end() if m.end() <= pos[0] else (m.start() - pos[1] if m.start() >= pos[1] else 0)
            if best is None or (d, rank) < best[:2]:
                best = (d, rank, unit)
    if best is None or (best[2] == "weekday" and "weekday" not in units):
        return None
    return best[2]


_ALL = tuple(u for u, _rx in _CTX)
_SPECIAL = ("event", "overnight", "evening", "weekend")


def _unit(text, s, e, ctx, pos=None):
    """(unit, duration_h) of the amount text[s:e]; ctx is its clause (with the clause before it when that one
    has no amount), pos the amount's (start, end) in ctx."""
    after, before = text[e:e + 40], text[max(0, s - 40):s]
    pos = pos or (len(ctx), len(ctx))
    dur = None
    m = _DUR_AFTER.match(after) or _DUR_BEFORE.search(before)
    if m:
        tok = m.group(1).lower()
        dur = _WORDNUM.get(tok) or float(tok)
    if _HALF.match(after) or re.search(r"(?:half[- ]?(?:an? )?hour|30\s?min(?:ute)?s?)\s*(?:for|is|:|=)?\s*$",
                                       before, re.I):
        return "half hour", None
    if dur is not None:
        if dur >= 12:
            return "day", None
        return ("hour", None) if dur == 1.0 else ("flat", dur)
    if _HOUR.match(after) or re.search(r"(?:hourly|per hour|an hour|/hr)\s*(?:rate)?\s*(?:of|is|was|:)?\s*$",
                                       before, re.I):
        return "hour", None
    mm = _MINUTES.match(after)
    if mm:
        return "minutes", float(mm.group(1))
    if _DAY_AFTER.match(after) and not re.match(r"^\s*max(?:imum)?\s*(?:for|of)?\s*(?:the\s+)?(?:\d+\s*)?"
                                                 r"(?:hours?|hrs?|h|min)", after, re.I):
        return _nearest(ctx, pos, _SPECIAL) or "day", None
    if _DAY_BEFORE.search(before):
        return _nearest(ctx, pos, _SPECIAL) or "day", None
    wide = [x for x in _DUR_WIDE.finditer(ctx[:pos[0]]) if not _AMT.search(ctx, x.end(), pos[0])]
    if wide:   # 'parked here for 2 hours on Saturday, the machine charged me $10'
        tok = wide[-1].group(1).lower()
        d = _WORDNUM.get(tok) or float(tok)
        if d >= 12:
            return "day", None
        return ("hour", None) if d == 1.0 else ("flat", d)
    u = _nearest(ctx, pos, _ALL)
    return (u or "unknown"), None


def extract_mentions(text, date="", rating=None):
    """CAD amounts near parking words in one review: [{"amount", "unit", "text", "date", "rating"} plus "max"
    for a range like $20-25, "duration_h" for '2 hours for $8' and "per_min" for '$2 per 20 minutes']. Skips
    tickets, fines, towing, tips, food, tolls, monthly permits, EV charging, car washes, refunds, amounts over
    $100, and single amounts that are a fee on top ('$3 card processing fee', '$4 service charge', '$3 extra';
    not '$15 parking fee'), a difference ('$5 cheaper', '$5 off', 'more than $10'), a past price ('used to be
    $7', 'back in 2023 it was $12', '$10 two years ago') or another purchase ('$90 for the hotel'). Text is
    cleaned (no en or em dashes)."""
    text = _clean(text)
    out, seen = [], set()
    for ss, se in _spans(text, _STRONG, 0, len(text)):
        if not _PARKING_WORDS.search(text, ss, se):
            continue
        clauses = _spans(text, _WEAK, ss, se)
        for i, (cs, ce) in enumerate(clauses):
            for m in _AMT.finditer(text, cs, ce):
                ga = m.group("a") or m.group("c") or m.group("e")
                gb = m.group("b") or m.group("d")
                if not ga:
                    continue
                amt = _num(ga)
                hi = _num(gb) if gb else None
                if amt <= 0 or amt > MAX_AMOUNT or (hi is not None and hi > MAX_AMOUNT):
                    continue
                if hi is not None and re.search(r"\b(?:from|went up|increased|raised)\s*$",
                                                text[max(0, m.start() - 15):m.start()], re.I):
                    amt, hi = hi, None   # 'went from $3 to $4 per hour': the newer rate
                if hi is not None and hi <= amt:
                    hi = None
                own = _BENIGN.sub(" ", text[cs:ce])
                if _SKIP.search(own):
                    continue
                if _SKIP_AFTER.search(text[m.end():m.end() + 30]) or _SKIP_BEFORE.search(text[max(cs, m.start() - 25):m.start()]):
                    continue   # '$0.50 credit card fee', '$5 cheaper', 'more than $10', 'used to be $7'
                ctx, off = text[cs:ce], cs
                if i > 0:
                    ps, pe = clauses[i - 1]
                    lead = text[ps:pe]
                    if not _AMT.search(lead):
                        if _SKIP.search(_BENIGN.sub(" ", lead)):
                            continue
                        ctx = lead + " " + ctx
                        off = cs - len(lead) - 1
                unit, dur = _unit(text, m.start(), m.end(), ctx, (m.start() - off, m.end() - off))
                key = (amt, hi, unit, dur)
                if key in seen:
                    continue
                seen.add(key)
                one = {"amount": amt, "unit": unit, "text": _snippet(text, m.start(), m.end()), "date": date,
                       "rating": rating}
                if hi is not None:
                    one["max"] = hi
                if dur is not None and unit == "minutes":
                    one["per_min"] = int(dur)
                elif dur is not None:
                    one["duration_h"] = dur
                out.append(one)
    return out


def _money(x):
    return ("$%d" % int(x)) if abs(x - round(x)) < 0.005 else ("$%.2f" % x)


def mention_text(m):
    """'$15 flat', '$20-25 per day', '$8 for 2 hours', '$2 per 20 min', '$30 for events', '$5 (unit not stated)'."""
    amt = _money(m["amount"]) + (("-" + _money(m["max"])[1:]) if m.get("max") else "")
    if m.get("duration_h"):
        d = m["duration_h"]
        return "%s for %s hours" % (amt, ("%d" % d) if d == int(d) else ("%.1f" % d))
    if m.get("unit") == "minutes" and m.get("per_min"):
        return "%s per %d min" % (amt, m["per_min"])
    tail = {"flat": " flat", "day": " per day", "hour": " per hour", "half hour": " per half hour",
            "evening": " evenings", "overnight": " overnight", "weekend": " weekends", "weekday": " weekdays",
            "event": " for events", "unknown": " (unit not stated)"}.get(m["unit"], "")
    return amt + tail


def _month_text(ym):
    try:
        y, mo = ym.split("-")[:2]
        return "%s %s" % (MONTHS[int(mo) - 1], y)
    except (ValueError, IndexError):
        return ""


def summary_text(mentions, limit=4):
    """'reviews mention $15 flat (Aug 2026), $20 per day (Mar 2026)', newest first, one per distinct rate."""
    seen, bits = set(), []
    for m in sorted(mentions, key=lambda x: x.get("date") or "", reverse=True):
        t = mention_text(m)
        if t in seen:
            continue
        seen.add(t)
        mt = _month_text(m.get("date") or "")
        bits.append("%s (%s)" % (t, mt) if mt else t)
        if len(bits) >= limit:
            break
    return ("reviews mention " + ", ".join(bits)) if bits else ""


# --------------------------------------------------------------------------- API call

def _cutoff(today):
    y, mo = today.year, today.month - KEEP_MONTHS
    while mo < 1:
        y, mo = y - 1, mo + 12
    return dt.date(y, mo, min(today.day, 28))


def _pub_date(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _scrub(msg, key):
    return msg.replace(key, "***") if key else msg


def _http_error(e):
    bits = ["HTTP %d" % e.code]
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
        st = (body.get("error") or {}).get("status")
        if st:
            bits.append(str(st))
    except Exception:
        pass
    finally:
        try:
            e.close()
        except Exception:
            pass
    return " ".join(bits)


def _kind(name, types):
    if "parking_garage" in (types or []) or re.search(r"garage|parkade|underground|car ?park|indoor", name or "", re.I):
        return "garage"
    return "lot"


def review_prices(lat, lon, radius_m, warnings, key=None, today=None):
    """One Nearby Search (New) call for parking near (lat, lon) with reviews in the field mask; rates mentioned
    in reviews from the last 18 months. None without a key (silently) or on any error (with a warning)."""
    key = (key or dc.env_key("GOOGLE_PLACES_API_KEY") or "").strip()
    if not key:
        return None
    today = today or dt.date.today()
    cutoff = _cutoff(today)
    radius = max(50.0, min(50000.0, float(radius_m or 800)))
    body = {"includedTypes": INCLUDED_TYPES, "maxResultCount": 20, "rankPreference": "DISTANCE",
            "locationRestriction": {"circle": {"center": {"latitude": float(lat), "longitude": float(lon)},
                                               "radius": radius}}}
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK}
    try:
        raw = dc.fetch(ENDPOINT, data=json.dumps(body), headers=headers, timeout=20, max_age_h=0)
        reply = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except urllib.error.HTTPError as e:
        warnings.append(_scrub("Google Places reviews skipped: %s" % _http_error(e), key))
        return None
    except Exception as e:   # URLError, timeout, bad JSON
        why = type(e).__name__
        reason = getattr(e, "reason", "")
        if reason:
            why += ": %s" % reason
        warnings.append(_scrub("Google Places reviews skipped: %s" % why, key))
        return None
    if not isinstance(reply, dict):
        warnings.append("Google Places reviews skipped: unexpected reply")
        return None
    places = []
    for p in reply.get("places") or []:
        loc = p.get("location") or {}
        if loc.get("latitude") is None or loc.get("longitude") is None:
            continue
        name = ((p.get("displayName") or {}).get("text") or "").strip() or "Parking"
        mentions, recent = [], 0
        for rv in p.get("reviews") or []:
            d = _pub_date(rv.get("publishTime"))
            if d is None or d < cutoff:
                continue
            recent += 1
            ym = d.strftime("%Y-%m")
            author = ((rv.get("authorAttribution") or {}).get("displayName") or "").strip()
            txt, orig = rv.get("text") or {}, rv.get("originalText") or {}
            found = extract_mentions(txt.get("text") or "", ym, rv.get("rating"))
            translated = bool(orig.get("languageCode") and txt.get("languageCode")
                              and orig.get("languageCode") != txt.get("languageCode"))
            if orig.get("text") and orig.get("text") != txt.get("text"):
                more = extract_mentions(orig["text"], ym, rv.get("rating"))
                if not found:
                    found, translated = more, False
            for m in found:
                m["author"] = author
                m["author_url"] = (rv.get("authorAttribution") or {}).get("uri") or ""
                m["review_url"] = rv.get("googleMapsUri") or ""
                if translated:
                    m["translated"] = True
            mentions.extend(found)
        uniq, seen = [], set()
        for m in sorted(mentions, key=lambda x: x["date"], reverse=True):
            k = (m["amount"], m.get("max"), m["unit"], m.get("duration_h"), m["date"])
            if k not in seen:
                seen.add(k)
                uniq.append(m)
        la, lo = float(loc["latitude"]), float(loc["longitude"])
        places.append({"name": name, "lat": round(la, 6), "lon": round(lo, 6),
                       "address": p.get("formattedAddress") or "", "maps_url": p.get("googleMapsUri") or "",
                       "kind": _kind(name, p.get("types")), "dist_m": int(round(th.haversine_m(lat, lon, la, lo))),
                       "recent_reviews": recent, "mentions": uniq, "summary": summary_text(uniq)})
    places.sort(key=lambda x: (0 if x["mentions"] else 1, x["dist_m"]))
    return {"places": places, "source": SOURCE, "attribution": ATTRIBUTION}


# --------------------------------------------------------------------------- planner lots

_STOP = set("parking park lot lots garage the carpark car public municipal p green impark indigo precise parkade "
            "underground st street ave avenue rd road blvd dr e w n s east west north south and of at toronto inc "
            "ltd centre center entrance self paid surface visitor".split())


def _tokens(*texts):
    out = set()
    for t in texts:
        for w in re.findall(r"[a-z0-9]+", (t or "").lower().replace("&", " and ")):
            if w not in _STOP:
                out.add(w)
    return out


_STREET_TYPES = (r"st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|lane|ln|way|cres|crescent|ct|court|pl|place|"
                 r"sq|square|quay|terrace|ter|pkwy|parkway|hwy|highway|cir|circle|gate|row|trail|walk|mews")
_ADDR_RE = re.compile(r"\b(\d+[a-z]?)\s+((?:[a-z0-9']+\s+){0,3}?[a-z0-9']+)\s+(?:%s)\b" % _STREET_TYPES, re.I)


def _addr_parts(*texts):
    """({house numbers}, {street-name tokens}) of the addresses written in texts ('40 York St' -> {'40'}, {'york'})."""
    nums, streets = set(), set()
    for t in texts:
        for m in _ADDR_RE.finditer((t or "").replace("&", " and ")):
            nums.add(m.group(1).lower())
            streets |= set(re.findall(r"[a-z0-9]+", m.group(2).lower())) - _STOP
    return nums, streets


def _similar(place, lot):
    """Same lot when the place and the lot share a house number and a street name, or share a distinctive name
    token once street names and house numbers are taken out ('St. Lawrence' in both). Sharing only a street
    ('25 York St' and '40 York St') is not enough."""
    pa = (place.get("name"), (place.get("address") or "").split(",")[0])
    la = (lot.get("name"), lot.get("address"))
    pn, ps = _addr_parts(*pa)
    ln, ls = _addr_parts(*la)
    if pn & ln and ps & ls:
        return True
    strip = ps | ls | pn | ln
    a = set(w for w in _tokens(*pa) if w not in strip and not w.isdigit())
    b = set(w for w in _tokens(*la) if w not in strip and not w.isdigit())
    common = a & b
    if not common:
        return False
    return len(common) >= 2 or len(common) / float(min(len(a), len(b)) or 1) >= 0.5


NEAREST_M = 25.0   # a place with no name or address match attaches to a lot only this close, and uncontested


def match_lots(places, lots, max_m=80.0, dest=None):
    """Attach each place's mentions to the planner lot within max_m with a similar name or address (_similar),
    else to the nearest non-street lot within NEAREST_M when no other place with mentions claims that lot (sets
    review_mentions, review_summary, review_url, review_place on that lot, in place; each mention keeps its own
    "place" and "maps_url"). Returns the unmatched places that have mentions as extra candidate lots (cost None,
    confidence "review", unverified), shaped like parking.make_lot output so they can be ranked with the rest.
    dest (lat, lon) sets their walk; else the distance from the review search center is used."""
    extras = []
    c = {}
    try:
        c = dc.load_data("parking")
    except (OSError, ValueError):
        pass
    wf, mpm = c.get("walk_factor", 1.3), c.get("walk_m_per_min", 75.0)
    sm = c.get("search_min", {})
    picks = []
    for p in places or []:
        if not p.get("mentions"):
            continue
        best, close = None, None
        for lot in lots or []:
            if lot.get("kind") == "street" or lot.get("lat") is None:
                continue
            d = th.haversine_m(p["lat"], p["lon"], lot["lat"], lot["lon"])
            if d <= max_m and _similar(p, lot) and (best is None or d < best[0]):
                best = (d, lot)
            if d <= NEAREST_M and (close is None or d < close[0]):
                close = (d, lot)
        picks.append((p, best[1] if best else None, close[1] if close and not best else None))
    claims = {}
    for p, sim, near in picks:
        for lot in (sim, near):
            if lot is not None:
                claims[id(lot)] = claims.get(id(lot), 0) + 1
    for p, sim, near in picks:
        lot = sim or (near if near is not None and claims.get(id(near)) == 1 else None)
        mentions = [dict(m, place=p["name"], maps_url=p.get("maps_url") or "") for m in p["mentions"]]
        if lot is not None:
            have = lot.setdefault("review_mentions", [])
            keys = set((m["amount"], m.get("max"), m["unit"], m.get("duration_h"), m["date"]) for m in have)
            for m in mentions:
                k = (m["amount"], m.get("max"), m["unit"], m.get("duration_h"), m["date"])
                if k not in keys:
                    keys.add(k)
                    have.append(m)
            have.sort(key=lambda x: x["date"], reverse=True)
            lot["review_summary"] = summary_text(have)
            lot.setdefault("review_url", p.get("maps_url") or "")
            lot.setdefault("review_place", p["name"])
            continue
        dist = th.haversine_m(dest[0], dest[1], p["lat"], p["lon"]) if dest else float(p.get("dist_m") or 0)
        walk = int(round(dist * wf))
        kind = p.get("kind") or "lot"
        notes = "rates from Google reviews, unverified"
        if p.get("address"):
            notes = p["address"] + "; " + notes
        extras.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "walk_m": walk,
                       "walk_min": int(math.ceil(walk / mpm)), "search_min": sm.get(kind, 2), "cost": None,
                       "cost_text": "price unknown; " + p["summary"], "basis": "Google reviews, unverified",
                       "kind": kind, "source": SOURCE, "source_url": p.get("maps_url") or "",
                       "confidence": "review", "free": False, "fits_stay": True, "max_stay_min": None,
                       "capacity": None, "often_full": False, "reservation": None, "notes": notes,
                       "max_height_m": None, "per_person": False, "unverified": True, "address": p.get("address") or "",
                       "review_mentions": mentions, "review_summary": p["summary"],
                       "review_url": p.get("maps_url") or "", "review_place": p["name"], "_dist": dist, "_rule": None})
    return extras


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Parking rates mentioned in recent Google reviews (needs "
                                             "GOOGLE_PLACES_API_KEY; official Places API only).")
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("--radius", type=float, default=800.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    warnings = []
    res = review_prices(a.lat, a.lon, a.radius, warnings)
    if a.json:
        print(json.dumps({"result": res, "warnings": warnings}, indent=1))
        return 0

    def say(text):   # place names, review snippets and authors may hold control characters: print them as spaces
        print(th.safe_text(text))
    for w in warnings:
        say("warning: " + w)
    if res is None:
        if not warnings:
            say("no GOOGLE_PLACES_API_KEY; review rates skipped")
        return 0
    shown = [p for p in res["places"] if p["mentions"]]
    if not shown:
        say("no rates in recent reviews of %d parking places" % len(res["places"]))
    for p in shown:
        say("%s, %d m: %s" % (p["name"], p["dist_m"], p["summary"]))
        for m in p["mentions"][:3]:
            say("  \"%s\" (%s, %s)" % (m["text"], m.get("author") or "Google user", _month_text(m["date"])))
        if p["maps_url"]:
            say("  " + p["maps_url"])
    say(res["attribution"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
