"""Parking search and pricing for the driving planner.

Sources, best first: curated tourist-town places (references/driving/parking-places*.json), keyless city
ArcGIS parking layers, Toronto Green P 2019 carparks joined to TPA occupancy, and OpenStreetMap through
Overpass. Every lot is priced for the actual stay with price_rule() on a normalized rule:

  {"free": bool, "hourly": $, "per_min": minutes per charge unit (60), "daily_max": $ per calendar day,
   "min_charge": $, "flat": $ per entry, "free_first_min": n, "tiers": [{"up_to_min": n, "cost": $}, {"cost": $}],
   "paid_days": [0-6], "paid_hours": ["09:00", "18:00"], "holidays_free": bool, "season": ["MM-DD", "MM-DD"],
   "off_season": "free"|"closed", "max_stay_min": n, "evening_flat": $, "evening_from": "18:00"}

Extensions used by the open-data parsers (all optional):
  "periods": [{"days": [0-6], "hours": [start, end], "holidays": bool, "hourly", "per_min", "max", "flat",
               "entry": bool, "free": bool}]  paid windows with their own rate or cap (end < start wraps
               past midnight); "fill": true charges time outside every period at the base hourly rate;
  "holidays_paid": true (holidays use the paid schedule; default: their own weekday);
  "max_stay_applies": "paid" (default when the rule has paid windows: the limit binds only inside them)
  or "always"; "no_parking": [{"days", "hours", "all_days", "season"}] (stays overlapping it do not fit;
  "days" defaults to Mon to Fri, so list all seven for a daily ban; "all_days": true keeps it on holidays;
  "season": ["MM-DD", "MM-DD"] limits it to the windows that start in those dates, e.g. a winter ban);
  "currency": "USD" (cannot price in CAD); "hours_note", "note": text;
  "flat_by_month": [12 x $] (a flat entry price that changes by month, January first; replaces "flat");
  "season" ends may be named anchors that move by year: "victoria_day", "fri_before_victoria_day",
  "labour_day", "thanksgiving", "civic_holiday", "easter_monday", "good_friday"; "season_year": 2026 (the
  season dates are that year's dates; other years get a warning note); "off_season_flat": $ (a different
  price off season); "off_season_max_stay_min": n (a time limit that still applies off season);
  "rate_year": 2026 (rates published for that year; later years get a note); "tax": 0.13 (published rates
  exclude HST); "per_person": {"adult", "child", "senior"} or true (admission per person: the cost is one
  adult unless the party is known); "per_passenger": $ (vehicle price covers the driver, plus $ for each
  passenger); "first_hour": $ (the first hour costs this, later hours the hourly rate); "fill_cap": $ (cap
  per day on time charged by "fill"); "_partial_days": [0-6] (the rate text priced only these weekdays;
  other days are unknown, not free); a period may carry "no_limit": true (max_stay does not bind in it) and
  "entry_max": [{"from", "to", "max"}] (the cap depends on the arrival time), "months": [1-12] (the window
  applies in those months only), "free_first_min": n (the first n minutes of the stay in it are free) and
  "roll_min": n (its "max" is a rolling maximum: each n-minute block of the stay from the first charged minute
  costs at most "max", across midnight, as Green P's "Any 4 hour Period" or "24 Hour Rolling Maximum").

Standard library only.
"""
from __future__ import annotations

import copy
import datetime as dt
import glob
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:   # `python -I parking.py`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import drive_common as dc  # noqa: E402

th = dc.th
log = th.log

CONF_RANK = {"official": 0, "curated": 1, "osm": 2, "low": 3, "stale": 3}
UNVERIFIED_FREE_TEXT = "free per OpenStreetMap, unverified; check signs"
FACILITY_FREE_TEXT = "free per OpenStreetMap, unverified; may be for facility users"
# an unverified free OSM lot ranks after verified priced options up to this cost for the stay
UNVERIFIED_FREE_RANK_COST = 5.0


def _today():
    """Today's date (a function so tests can pin it)."""
    return dt.date.today()
PLACES_DIR = dc.DATA_DIR
CAVEAT = "Posted signs override this data: time limits, rush-hour no-stopping, permit zones."
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def cfg():
    return dc.load_data("parking")


# --------------------------------------------------------------------------- small parsers

def hm(s):
    """'HH:MM' -> minutes since midnight ('24:00' -> 1440)."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def hm_text(mins):
    mins = int(mins) % 1440 if mins != 1440 else 1440
    return "24:00" if mins == 1440 else "%02d:%02d" % divmod(mins, 60)


def num(v):
    """A field value as a float: 3.25, '3.25', '$2.00', '4' -> float; 'n/a', '', None, 0 text -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"(\d+(?:\.\d+)?)", str(v).replace(",", ""))
    return float(m.group(1)) if m else None


def money_field(v):
    x = num(v)
    return x if x is not None and x > 0 else None


def parse_duration(v, default_unit="h"):
    """'2 hours', '3 hr limit', '90 min', '1 day', '2:30', '2' (default_unit: 'h' or 'min') -> minutes;
    'Monthly' or empty -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(float(v) * (60 if default_unit == "h" else 1))) or None
    s = str(v).strip().lower()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)          # OSM maxstay "2:30"
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) or None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|min|mins|minute|minutes|d|day|days)?\b", s)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2) or default_unit
    if unit.startswith("d"):
        return int(round(n * 1440)) or None
    return int(round(n if unit.startswith("m") else n * 60)) or None


def days_text(days):
    days = sorted(set(days))
    if len(days) == 7:
        return "daily"
    if not days:
        return "no days"
    runs, start, prev = [], days[0], days[0]
    for d in days[1:] + [None]:
        if d is not None and d == prev + 1:
            prev = d
            continue
        runs.append(DAY_ABBR[start] if start == prev else
                    ("%s, %s" if prev == start + 1 else "%s-%s") % (DAY_ABBR[start], DAY_ABBR[prev]))
        if d is not None:
            start = prev = d
    return ", ".join(runs)


MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def months_text(months):
    """[11, 12, 1, 2, 3] -> 'Nov-Mar'; [4] -> 'Apr'; a gap lists them."""
    ms = sorted(set(int(m) for m in months))
    if len(ms) == 1:
        return MONTH_ABBR[ms[0] - 1]
    for start in ms:   # a run that may wrap past December
        run = [((start - 1 + i) % 12) + 1 for i in range(len(ms))]
        if sorted(run) == ms:
            return "%s-%s" % (MONTH_ABBR[run[0] - 1], MONTH_ABBR[run[-1] - 1])
    return ", ".join(MONTH_ABBR[m - 1] for m in ms)


def dur_text(minutes):
    h, m = divmod(int(round(minutes)), 60)
    if not h:
        return "%d min" % m
    return "%d h" % h if not m else "%d h %02d" % (h, m)


def _strip_french(text):
    text = text.replace("\u00a0", " ").replace("\u00c2", "")
    return re.sub(r"\s*/\s*(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|\u00e0|\ufffd|a)(?=[\s,()]|$)",
                  "", text, flags=re.I)


# OpenStreetMap day forms ("Mo-Fr 08:00-18:00", "Sa,Su"): two letters, capitalized, followed by a day list
# separator, a time or the end, so ordinary words ("We", "To", "The") never read as days
OSM_DAY = (r"(?-i:Mo|Tu|We|Th|Fr|Sa|Su)\b(?=\s*(?:[-–,;)]|\d|$|(?:off|closed|free|PH)\b))")
DAY_WORD = (r"(?:monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sundays|"
            r"sunday|sundat|sun|%s)" % OSM_DAY)
DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
           "mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
# a day word never runs on into a longer word ("money", "monthly", "Satellite"); "Mondays" is Monday
DAYS_RE = re.compile(r"\b(?:(?P<d1>%s)s?(?![a-z])\.?(?:\s*(?:-|\u2013|to|through|thru)\s*(?P<d2>%s)\b\.?)?|(?P<all>7\s*days|daily|every\s*day|"
                     r"mon(?:day)?\s*-\s*sun(?:day)?s?|weekdays?|weekends?|holidays?|stat(?:utory)?\s+holidays?))"
                     % (DAY_WORD, DAY_WORD), re.I)
TIME = r"(?:(?:\d{1,2})(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|midnight|noon)"
HOURS_RE = re.compile(r"(?:between\s+)?(?P<a>%s)\s*(?:-|\u2013|to|and)\s*(?P<b>%s)|(?P<after>after|from)\s+(?P<c>%s)"
                      % (TIME, TIME, TIME), re.I)
RANGE_RE = re.compile(r"(?:between\s+)?(?P<a>%s)\s*(?:-|–|to|and)\s*(?P<b>%s)" % (TIME, TIME), re.I)
AFTER_RE = re.compile(r"(?P<after>after|from)\s+(?P<c>%s)" % TIME, re.I)
# the suffix form ("4 CAD/h", "5$") never starts inside a clock time or a decimal, and a "$" after a number
# is a suffix only when no amount follows it: "Mon-Sat 9:00-18:00 $2/hr" is $2, not $0 written as "00 $"
MONEY_RE = re.compile(r"(?P<pre>US\s*\$|CA?\$|C\$|\$)\s*(?P<n1>\d+(?:\.\d+)?)\s*(?P<post1>USD|CAD|CND|CDN)?"
                      r"|(?<![\d:.])(?P<n2>\d+(?:\.\d+)?)\s*(?P<post2>USD|CAD|CND|CDN|\$(?!\s*\d))", re.I)
UNIT_RE = re.compile(r"\s*(?:/|per|an?|for|each)?\s*(?P<n>\d+(?:\.\d+)?|1/2|half(?:\s+an?)?)?\s*"
                     r"(?P<u>minutes|minute|mins|min|hours|hour|hrs|hr|h|days|day|daily|nights|night|overnight|eve(?:ning)?s?|"
                     r"month(?:ly)?|maximum|max|flat(?:\s*rate)?|entry|car|vehicle)\b\.?(?:\s*(?P<s>\d+)\s*s(?:ec)?\b)?", re.I)
LIMIT_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*-?\s*(?P<u>hr|hrs|hour|hours|h|min|mins|minute|minutes)\.?\s*(?:parking\s*)?"
                      r"(?:limit|max(?:imum)?)\b|(?:limit|max(?:imum)?\s*stay)\s*(?:of\s*)?(?P<n2>\d+)\s*(?P<u2>h|hr|hrs|hours?|min)"
                      r"|\bmax(?:imum)?\.?\s*(?:of\s*)?(?P<n3>\d+(?:\.\d+)?)\s*(?P<u3>hours?|hrs?|h|min(?:ute)?s?)\b", re.I)
FIRST_HOUR_RE = re.compile(r"(?P<pre>US\s*\$|CA?\$|C\$|\$)\s*(?P<a>\d+(?:\.\d+)?)\s*(?:for\s+)?(?:the\s+)?first\s+(?:hour|hr|h)\b"
                           r"[\s,;.]*(?:then\s+)?(?:\$\s*)?(?P<b>\d+(?:\.\d+)?)\s*(?:/|per|for)?\s*(?:each\s+)?"
                           r"(?:additional|addt?'?l|extra|subsequent)\s*(?:hour|hr|h)\b", re.I)
FREE_FIRST_RE = re.compile(r"first\s+(?P<n>\d+|one|two|half)?\s*(?P<u>hours?|hrs?|h|min(?:ute)?s?)\s*(?:is\s+)?free", re.I)
EVENING_RE = re.compile(r"\b(eve|evening|night|overnight)\b", re.I)
CLAUSE_SEP = re.compile(r"[,;|]|//")    # what ends a clause of rate text ("$2/hr Mon-Fri 8am-6pm; $1/hr Sat ...")
# what may stand between "free" and its days: "free Sat-Sun", "free on weekends", "free evenings and weekends"
FREE_GAP_RE = re.compile(r"^[\s:,\-]*(?:on\s+)?(?:(?:eve|evenings?|nights?|overnight)\s*(?:,|and|&)\s*)?(?:on\s+)?$", re.I)


def _clock(tok, ap_hint=None):
    """One time token -> (minutes, has_ampm)."""
    t = tok.strip().lower().replace(".", "")
    if t == "midnight":
        return 0, True
    if t == "noon":
        return 720, True
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    ap = ap or ap_hint
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    return h * 60 + mi, bool(m.group(3))


def _ampm(tok):
    t = tok.lower().replace(".", "")
    return "pm" if "pm" in t else ("am" if "am" in t else None)


def parse_hours(a, b):
    """Two time tokens -> (start_min, end_min) or None when they do not look like clock times."""
    sa, sb = a.strip().lower(), b.strip().lower()
    clocky = lambda s: (":" in s) or _ampm(s) or s in ("midnight", "noon")
    if not (clocky(sa) or clocky(sb)):
        return None
    ea, _ = _clock(sb)
    if sb == "midnight":
        ea = 1440
    if _ampm(sa) or ":" in sa and int(sa.split(":")[0]) > 12 or sa in ("midnight", "noon"):
        sa_min, _ = _clock(sa)
    else:
        hint = _ampm(sb)
        sa_min, _ = _clock(sa, hint)
        if hint == "pm" and sa_min > ea:
            sa_min, _ = _clock(sa, "am")
    if ea == 1439:
        ea = 1440
    if sa_min >= 1440 or ea > 1440:
        return None
    return sa_min, ea


def find_hours(text):
    """The first clock range in text ("9am - 6pm", "between 5 AM and 3 PM") as (start_min, end_min), else an
    "after 6pm" / "from 18:00" start running to midnight, else None."""
    for m in RANGE_RE.finditer(text or ""):
        rng = parse_hours(m.group("a"), m.group("b"))
        if rng:
            return rng
    for m in AFTER_RE.finditer(text or ""):
        c = m.group("c")
        if _ampm(c) or ":" in c or c.strip().lower() in ("midnight", "noon"):
            return _clock(c)[0], 1440
    return None


def _days_of(m):
    """DAYS_RE match -> (set of weekdays, holidays flag)."""
    if m.group("all"):
        w = m.group("all").lower()
        if w.startswith("holiday") or w.startswith("stat"):
            return set(), True
        if w.startswith("weekday"):
            return set(range(5)), False
        if w.startswith("weekend"):
            return {5, 6}, False
        return set(range(7)), False
    d1 = DAY_MAP[m.group("d1").lower()[:3]]
    if not m.group("d2"):
        return {d1}, False
    d2 = DAY_MAP[m.group("d2").lower()[:3]]
    out, d = set(), d1
    while True:
        out.add(d)
        if d == d2:
            break
        d = (d + 1) % 7
    return out, False


def _tokens(text):
    """Scan rate text into ordered tokens: (kind, start, end, value)."""
    toks, used = [], []

    def free_span(a, b):
        return all(b <= x or a >= y for x, y in used)

    for m in MONEY_RE.finditer(text):
        n = float(m.group("n1") or m.group("n2"))
        cur = "USD" if "US" in ((m.group("pre") or "") + (m.group("post1") or "") + (m.group("post2") or "")).upper() else "CAD"
        u = UNIT_RE.match(text, m.end())
        end, unit, per = m.end(), None, None
        if u and u.group("u"):
            unit = u.group("u").lower()
            q = (u.group("n") or "").lower()
            per = 0.5 if q in ("1/2",) or q.startswith("half") else (float(q) if q else None)
            if u.group("s") and unit.startswith("min"):
                per = (per or 1.0) + int(u.group("s")) / 60.0
            end = u.end()
        prev = text[max(0, m.start() - 12):m.start()].lower()
        if unit is None and re.search(r"\bmax(?:imum)?\.?\s*(?:of\s*)?:?\s*$", prev):
            unit = "max"     # "$2/hr, max $10", "maximum $10": the day maximum, written before the amount
        # "8.00 CAD/24 hour max": the amount is a cap, not a price per block of hours
        cap = bool(re.match(r"\s*(?:max(?:imum)?|cap)\b", text[end:], re.I))
        after = re.split(r"[,;$/]", text[end:end + 14])[0]
        event = bool(re.search(r"\bevents?\b", after, re.I) or re.search(r"\bevents?\s*(?:rate|price|parking)?\s*:?\s*$", prev, re.I))
        toks.append(("money", m.start(), end, {"amount": n, "unit": unit, "per": per, "cur": cur, "event": event, "cap": cap,
                                               "evening": bool(EVENING_RE.search(text[m.start():end + 20]) or EVENING_RE.search(prev))}))
        used.append((m.start(), end))
    for m in FREE_FIRST_RE.finditer(text):
        q = (m.group("n") or "1").lower()
        n = {"one": 1, "two": 2, "half": 0.5}.get(q) or float(q)
        mins = n if m.group("u").lower().startswith("min") else n * 60
        toks.append(("free_first", m.start(), m.end(), int(mins)))
        used.append((m.start(), m.end()))
    for m in LIMIT_RE.finditer(text):
        if not free_span(m.start(), m.end()):
            continue
        if m.group("n"):
            n, u = m.group("n"), m.group("u")
        elif m.group("n2"):
            n, u = m.group("n2"), m.group("u2")
        else:
            n, u = m.group("n3"), m.group("u3")
        mins = float(n) if u.lower().startswith("m") else float(n) * 60
        toks.append(("limit", m.start(), m.end(), int(mins)))
        used.append((m.start(), m.end()))
    # ranges first ("from 9am - 5pm" is a range, not "after 9am"), then "after 6pm" / "from 6pm"
    for m in list(RANGE_RE.finditer(text)) + list(AFTER_RE.finditer(text)):
        if not free_span(m.start(), m.end()):
            continue
        if m.re is AFTER_RE:
            if not (_ampm(m.group("c")) or ":" in m.group("c")):
                continue
            st, _ = _clock(m.group("c"))
            rng = (st, 1440)
        else:
            rng = parse_hours(m.group("a"), m.group("b"))
            if not rng:
                continue
        toks.append(("hours", m.start(), m.end(), rng))
        used.append((m.start(), m.end()))
    for m in DAYS_RE.finditer(text):
        if not free_span(m.start(), m.end()):
            continue
        toks.append(("days", m.start(), m.end(), _days_of(m)))
    for m in re.finditer(r"\bfree\b", text, re.I):
        if free_span(m.start(), m.end()):
            toks.append(("free", m.start(), m.end(), None))
    for m in re.finditer(r"no\s+overnight", text, re.I):
        toks.append(("no_overnight", m.start(), m.end(), None))
    toks.sort(key=lambda t: t[1])
    # merge day lists such as "Sat, Sun, Holidays" or "Saturday - Sunday & Holidays"
    out, parts = [], []
    for t in toks:
        if (t[0] == "days" and out and out[-1][0] == "days"
                and re.match(r"^[\s,/&+]*(?:and)?[\s,/&+]*$", text[out[-1][2]:t[1]], re.I)):
            d0, h0 = out[-1][3]
            d1, h1 = t[3]
            out[-1] = ("days", out[-1][1], t[2], (d0 | d1, h0 or h1))
            parts[-1].append(t)
        else:
            out.append(t)
            parts.append([t])
    # "$2/hr Mon-Fri, Sat-Sun free", "$1.50/hr Mon-Sat, Sun free": a list holding weekdays, then a weekend or
    # holiday list right before "free", is a paid list followed by a free one, not one list of free days
    for k in range(len(out) - 2, -1, -1):
        t, nxt = out[k], out[k + 1]
        if t[0] != "days" or len(parts[k]) < 2 or nxt[0] != "free" or not re.match(r"^[\s\-:,]*$", text[t[2]:nxt[1]]):
            continue
        first, rest = parts[k][0], parts[k][1:]
        if any(d < 5 for d in first[3][0]) and not any(d < 5 for r in rest for d in r[3][0]):
            rd = set().union(*(r[3][0] for r in rest))
            out[k:k + 1] = [first, ("days", rest[0][1], rest[-1][2], (rd, any(r[3][1] for r in rest)))]
    return out


def _qualifiers(text, toks):
    """Days tokens that qualify a price rather than open a paid window: "$18 a day weekdays",
    "Weekdays $15, Weekends $8", "$8 evenings and weekends". Returns {days token index: money token index}.
    A days token next to hours ("Mon-Sat 9am-6pm $2/hr") or a 'free' word is a window, not a qualifier."""
    out, claimed = {}, set()

    def eligible(i):
        t = toks[i]
        days, _hol = t[3]
        if not days or len(days) == 7:
            return False
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        return not ((nxt and nxt[0] in ("hours", "free")) or (prev and prev[0] == "hours"))

    def money_has_window(j):
        nxt = toks[j + 1] if j + 1 < len(toks) else None
        return bool(nxt and nxt[0] == "hours")

    # pass 1: days written just before their price ("Weekdays $15")
    for i, t in enumerate(toks):
        if t[0] != "days" or not eligible(i) or i + 1 >= len(toks):
            continue
        nxt = toks[i + 1]
        if nxt[0] == "money" and re.match(r"^[\s:]*$", text[t[2]:nxt[1]]) and not money_has_window(i + 1):
            out[i] = i + 1
            claimed.add(i + 1)
    # pass 2: days written just after their price ("$18 a day weekdays", "$8 evenings and weekends")
    for i, t in enumerate(toks):
        if t[0] != "days" or i in out or not eligible(i) or not i:
            continue
        prev = toks[i - 1]
        if prev[0] == "money" and i - 1 not in claimed and not money_has_window(i - 1) \
                and re.match(r"^[\s,:;]*(?:and|on|&)?[\s,:;]*$", text[prev[2]:t[1]], re.I):
            out[i] = i - 1
            claimed.add(i - 1)
    return out


def _short_rate(toks, i):
    """True when the text has a price per hour or per minute (not per 3 or more hours) besides token i."""
    for j, tk in enumerate(toks):
        if j != i and tk[0] == "money":
            u, per = tk[3]["unit"], tk[3]["per"]
            if u and (u.startswith("min") or (u in ("h", "hr", "hrs", "hour", "hours") and not (per and per >= 3))):
                return True
    return False


def parse_rate_text(text):
    """Free-text parking rates from open data or OSM `charge` -> (partial normalized rule, notes).
    Handles "$2.00", "$0.25/15 min", "$0.25/4 min 18s", "Mon-Sat, 9am-6pm $2.00/hr 3hr limit",
    "08:00am - 06:00pm (Mon - Sat)", "$6 Day (7am-6pm), $5 Eve (6pm-midnight)", "$2/hr;$5 max",
    "First hour FREE. $1 per 1/2 hour. $9 Daily.", "Mon-Fri, 8:30 -18:00, $0.50/7.5 min, $19 Max, ...
    // Sat,Sun,Holidays - Free", "$18 a day weekdays" (other days unknown, not free), "Weekdays $15, Weekends $8",
    "$3/h, $12 max, free after 6pm", "$20 per day, $10 after 5pm", "$3/hr, $10 after 5pm" ($3/h until 17:00),
    "$5 first hour, $2 each additional hour", "$2.25/hour, max 3 hours", "$2/hr Mon-Fri 8am-6pm; $1/hr Sat
    9am-5pm" (each price before its window), "$12 Day Max, $5 Night Max" (a night maximum without hours is
    noted, not used), "$2/hr Mon-Fri, Sat-Sun free" (a paid day list, then the free one), "$2/hr 8am-6pm, free
    evenings and weekends". Unknown parts are left out; an empty dict means nothing was understood, or that the
    text calls every paid day free ("$2/hr, free Mon-Sun")."""
    if not text or not str(text).strip() or str(text).strip().lower() in ("n/a", "na", "none", "-"):
        return {}, []
    text = _strip_french(str(text))
    rule, notes, periods = {}, [], []
    fh = FIRST_HOUR_RE.search(text)
    if fh:   # "$5 first hour, $2 each additional hour": hourly $2 with the first hour at $5
        rule["hourly"], rule["per_min"], rule["first_hour"] = float(fh.group("b")), 60, float(fh.group("a"))
        text = text[:fh.start()] + " " * (fh.end() - fh.start()) + text[fh.end():]
    toks = _tokens(text)
    qual = _qualifiers(text, toks)
    qual_money = {m: d for d, m in qual.items()}
    qperiods = []
    hours_first = None
    cur_days, cur_hol = None, False
    pending = []          # hours-first periods waiting for their days
    last = None
    skip = set()
    free_days = set()     # weekdays the text calls free ("$2/hr, free Sat-Sun")
    free_evenings = False  # "free evenings and weekends"
    night_max = []        # "$10 overnight", "$5 Night Max" with no window: never the day maximum
    last_p, last_p_end = None, 0   # the latest window and where its clock range ends in the text
    for i, (kind, a, b, val) in enumerate(toks):
        if i in skip:
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if kind == "days":
            days, hol = val
            if i in qual:
                continue
            after_free = toks[i + 2] if i + 2 < len(toks) else None
            if nxt and nxt[0] == "free" and re.match(r"^[\s\-:,]*$", text[b:nxt[1]]) \
                    and not (after_free and after_free[0] == "days"
                             and re.match(r"^[\s:,\-]*(?:on\s+)?$", text[nxt[2]:after_free[1]], re.I)):
                # "Sat-Sun free", "Sat,Sun,Holidays - Free": unpaid days (in "Mon-Fri, free Sat-Sun" the free
                # word belongs to the days after it)
                free_days |= days
                if hol:
                    rule["holidays_free"] = True
                continue
            if hours_first is None:
                hours_first = False
            if hours_first and pending:
                for p in pending:
                    p["days"] = sorted(days) if days else p.get("days")
                    if hol:
                        p["holidays"] = True
                pending = []
            else:
                cur_days, cur_hol = (sorted(days) if days else None), hol
                if hol and not days and periods:
                    for p in periods:
                        p["holidays"] = True
        elif kind == "free":
            # "free after 6pm", "free 6pm-8am": a free window, not a paid one
            if nxt and nxt[0] == "hours" and re.match(r"^[\s:,\-]*$", text[b:nxt[1]]):
                rng = nxt[3]
                p = {"hours": [hm_text(rng[0]), hm_text(rng[1])], "free": True}
                if cur_days is not None:
                    p["days"] = list(cur_days)
                periods.append(p)
                skip.add(i + 1)
                # "free after 6pm and weekends": the days joined to the window by "and" are free all day
                t2 = toks[i + 2] if i + 2 < len(toks) else None
                if t2 and t2[0] == "days" and re.match(r"^\s*,?\s*(?:and|&)\s+(?:on\s+)?(?:all\s+day\s+(?:on\s+)?)?$",
                                                      text[nxt[2]:t2[1]], re.I):
                    free_days |= t2[3][0]
                    if t2[3][1]:
                        rule["holidays_free"] = True
                    skip.add(i + 2)
            # "free Sat-Sun", "free on weekends and holidays": unpaid days (with no clock window after them)
            elif nxt and nxt[0] == "days" and FREE_GAP_RE.match(text[b:nxt[1]]) \
                    and not (i + 2 < len(toks) and toks[i + 2][0] == "hours"
                             and re.match(r"^[\s:,\-]*$", text[nxt[2]:toks[i + 2][1]])):
                if re.search(r"\b(?:eve|evening|night|overnight)s?\b", text[b:nxt[1]], re.I):
                    free_evenings = True
                free_days |= nxt[3][0]
                if nxt[3][1]:
                    rule["holidays_free"] = True
                skip.add(i + 1)
            # "free Sat-Sun 9am-5pm": a free window on those days, paid outside it
            elif nxt and nxt[0] == "days" and re.match(r"^[\s:,\-]*(?:on\s+)?$", text[b:nxt[1]], re.I) \
                    and i + 2 < len(toks) and toks[i + 2][0] == "hours" \
                    and re.match(r"^[\s:,\-]*$", text[nxt[2]:toks[i + 2][1]]):
                rng = toks[i + 2][3]
                p = {"days": sorted(nxt[3][0]), "hours": [hm_text(rng[0]), hm_text(rng[1])], "free": True}
                if nxt[3][1]:
                    p["holidays"] = True
                periods.append(p)
                skip.update((i + 1, i + 2))
        elif kind == "hours":
            if hours_first is None:
                hours_first = True
            p = {"hours": [hm_text(val[0]), hm_text(val[1])]}
            if not hours_first and cur_days is not None:
                p["days"] = list(cur_days)
            if not hours_first and cur_hol:
                p["holidays"] = True
            if hours_first:
                pending.append(p)
            # "$6 Day (8am-6pm)" or "$10 after 5pm": a price written just before its window
            if last is not None and last.get("_forward"):
                for k in ("max", "flat", "hourly", "per_min", "roll_min"):
                    if k in last:
                        p[k] = last[k]
                if last.get("evening"):
                    rule["fill"] = True
                if last.get("_after"):
                    p["_after"] = True
                last = None
            periods.append(p)
            last_p, last_p_end = p, b
        elif kind == "money":
            if val["cur"] == "USD":
                rule["currency"] = "USD"
            u, per, amt = val["unit"], val["per"], val["amount"]
            if val.get("event"):
                notes.append("event rate %s not used" % dc.money(amt))
                continue
            item = {}
            if u is None or u in ("flat", "flat rate", "entry", "car", "vehicle"):
                if u is None and amt >= 40:
                    notes.append("unclear amount %s" % dc.money(amt))
                    continue
                item["flat"] = amt
            elif u.startswith("min"):
                per = per or 1.0
                item["hourly"], item["per_min"] = round(amt * 60.0 / per, 4), per
            elif u in ("h", "hr", "hrs", "hour", "hours"):
                if per and per >= 3 and (val.get("cap") or _short_rate(toks, i)):
                    # "8.00 CAD/24 hour max", or next to an hourly rate ("$2/hr, $15/24 hours", "$3/hr; $12 per
                    # 12 hours"): a maximum, per day for 24 hours, else for each block of that many hours
                    item["max"] = amt
                    if per < 24:
                        item["roll_min"] = int(round(per * 60))
                elif per and per >= 3:
                    # "$7 for 3 hours", "$6.00/12 hours" on their own: a block price, one charge per started block
                    item["hourly"], item["per_min"] = round(amt / per, 4), per * 60
                else:
                    per = per or 1.0
                    item["hourly"], item["per_min"] = round(amt / per, 4), per * 60
            elif u.startswith("day") or u == "daily" or u.startswith("max") or u in ("night", "nights", "overnight") or u.startswith("eve"):
                item["max"] = amt
            elif u.startswith("month"):
                continue
            item["evening"] = val["evening"] or bool(u and (u.startswith("eve") or "night" in u))
            # this amount itself is a night or evening price (its unit or the word right next to it)
            night = bool(u and (u.startswith("eve") or u in ("night", "nights", "overnight"))) \
                or bool(re.match(r"\s*(?:max(?:imum)?\s*)?(?:rate\s*)?(?:overnight|night|evening)s?\b", text[b:], re.I)) \
                or bool(re.search(r"\b(?:overnight|night|evening)s?\s*(?:max(?:imum)?|rate)?\s*:?\s*$", text[max(0, a - 20):a], re.I))
            if i in qual_money:     # the price applies on the qualifying days only
                days, hol = toks[qual_money[i]][3]
                qp = {"days": sorted(days), "hours": ["00:00", "24:00"]}
                if hol:
                    qp["holidays"] = True
                for k in ("hourly", "per_min", "max", "flat", "roll_min"):
                    if k in item:
                        qp[k] = item[k]
                qperiods.append(qp)
                continue
            # "$6 Day (8am-6pm)", "$5 Max Overnight Rate (Sun-Thu 6pm-8am)" or "$10 after 5pm": the window follows
            forward = False
            for j in (i + 1, i + 2):
                if j < len(toks) and toks[j][0] == "money":
                    break
                if j < len(toks) and toks[j][0] == "hours":
                    seg = text[b:toks[j][1]]
                    if j == i + 1 and re.match(r"^\s*(?:after|from)\b", text[toks[j][1]:], re.I) and re.match(r"^\s*$", seg):
                        forward = True
                        item["_after"] = True
                    else:
                        forward = "(" in seg and len(seg) <= 45 and "," not in seg.split("(")[0]
                        # "$2/hr Mon-Fri 8am-6pm; $1/hr Sat 9am-5pm", "$3/hr 7am-7pm, $8 flat 7pm-7am": a price
                        # written before a window in the same clause is that window's price, unless the clause
                        # opened with a window still waiting for its price ("Mon-Fri 8am-6pm $2/hr Sat ...")
                        waiting = last_p is not None and not last_p.get("free") and not any(
                            k in last_p for k in ("hourly", "flat", "max")) and not CLAUSE_SEP.search(text[last_p_end:a])
                        if not forward and not waiting and not CLAUSE_SEP.search(seg) \
                                and (j == i + 1 or toks[i + 1][0] == "days"):
                            forward = True
                            item["evening"] = night     # only this amount's own word, not a neighbour's
                    break
            if forward:
                item["_forward"] = True
                last = item
                continue
            target = periods[-1] if periods else None
            if target is not None and (target.get("free") or ("hourly" in item and target.get("hourly") is not None)):
                target = None
            if target is None:
                if "hourly" in item:
                    if "hourly" in rule:
                        if not (rule.get("first_hour") and rule["hourly"] == item["hourly"]):
                            notes.append("second rate %s/h ignored" % dc.money(item["hourly"]))
                    else:
                        rule["hourly"], rule["per_min"] = item["hourly"], item["per_min"]
                if "max" in item and item.get("roll_min"):
                    rule["_roll_max"] = (item["max"], item["roll_min"])   # turned into a rolling period below
                elif "max" in item and night:
                    night_max.append(item["max"])
                elif "max" in item:
                    rule["daily_max"] = item["max"] if "daily_max" not in rule else min(rule["daily_max"], item["max"])
                if "flat" in item:
                    rule["flat"] = item["flat"]
            else:
                for k in ("hourly", "per_min", "max", "flat", "roll_min"):
                    if k in item:
                        target[k] = item[k]
                if item.get("evening") and ("max" in item or "flat" in item):
                    rule["fill"] = True
        elif kind == "limit":
            rule["max_stay_min"] = val
            # "$1.5/Hr Monday-Sunday 2 Hr Max", "2 Hr Parking Maximum (Monday - Sunday)": the limit binds all day
            prev = toks[i - 1] if i else None
            for t, gap in ((prev, text[prev[2]:a] if prev else ""), (nxt, text[b:nxt[1]] if nxt else "")):
                if t and t[0] == "days" and len(t[3][0]) == 7 and re.match(r"^[\s(),:\-]*$", gap):
                    rule["max_stay_applies"] = "always"
        elif kind == "free_first":
            rule["free_first_min"] = val
        elif kind == "no_overnight":
            notes.append("no overnight parking")
            rule.setdefault("no_parking", []).append({"days": list(range(7)), "hours": ["02:00", "06:00"], "all_days": True})
    if cur_hol and not periods:
        rule["holidays_paid"] = True
    # "9:30am - 3:30pm + 6:00pm - 7:30pm": the gap between two paid windows of the same days is a
    # peak-hour no-parking window
    gaps = []
    paid_periods = [p for p in periods if not p.get("free")]
    for x, y in zip(paid_periods, paid_periods[1:]):
        if x.get("days") == y.get("days") and x.get("days") is not None and hm(x["hours"][1]) < hm(y["hours"][0]) \
                and hm(y["hours"][0]) - hm(x["hours"][1]) <= 180 and hm(x["hours"][1]) >= 14 * 60:
            gaps.append({"days": x["days"], "hours": [x["hours"][1], y["hours"][0]]})
    if gaps:
        rule.setdefault("no_parking", []).extend(gaps)
        notes.append("no parking %s (peak hours)" % ", ".join("%s-%s" % tuple(g["hours"]) for g in gaps))
    # "$20 per day, $10 after 5pm": the day price covers the rest of the day
    after = [p for p in periods if p.pop("_after", False)]
    if len(after) == 1 and (after[0].get("flat") is not None or after[0].get("max") is not None) \
            and (rule.get("daily_max") is not None or rule.get("flat") is not None) and hm(after[0]["hours"][0]) > 0:
        p0 = {"hours": ["00:00", after[0]["hours"][0]], "max": rule.get("daily_max") if rule.get("daily_max") is not None
              else rule.get("flat")}
        if after[0].get("days") is not None:
            p0["days"] = list(after[0]["days"])
        periods.insert(periods.index(after[0]), p0)
        rule.pop("flat", None)
    elif after and rule.get("hourly") is not None and len(after) == len(periods) \
            and all(p.get("flat") is not None or p.get("max") is not None for p in after):
        rule["fill"] = True      # "$3/hr, $10 after 5pm": $3/h until 17:00, then the evening price
    free_only = periods and all(p.get("free") for p in periods)
    if free_only:
        p = periods[0]
        if len(periods) == 1 and p.get("days") is None and p["hours"][1] == "24:00" and hm(p["hours"][0]) > 0:
            rule["paid_hours"] = ["00:00", p["hours"][0]]      # "free after 6pm": paid until 18:00
            periods = []
        else:
            rule["fill"] = True                                  # paid outside the free windows
    if qperiods:
        covered = set().union(*(set(p["days"]) for p in qperiods)) | free_days
        if not periods and len(covered) < 7 and not rule.get("paid_hours"):
            rule["_partial_days"] = sorted(covered)
            notes.append("price given for %s only" % days_text(covered))
        periods = periods + qperiods
    if periods:
        days_given = any("days" in p for p in periods)
        for p in periods:
            if "days" not in p and days_given:
                p["days"] = list(range(7))
        if len(periods) == 1 and not any(k in periods[0] for k in ("max", "flat", "free")) and not periods[0].get("holidays"):
            p = periods[0]
            rule["paid_hours"] = p["hours"]
            if "days" in p:
                rule["paid_days"] = p["days"]
            if "hourly" in p and "hourly" not in rule:
                rule["hourly"], rule["per_min"] = p["hourly"], p.get("per_min", 60)
        else:
            # one price for every listed window is also the base rate ("$2/hr Mon-Fri 8am-6pm, Sat 9am-5pm")
            rates = set((p.get("hourly"), p.get("per_min")) for p in periods if not p.get("free") and p.get("hourly") is not None)
            if "hourly" not in rule and len(rates) == 1:
                rule["hourly"], rule["per_min"] = list(rates)[0]
            rule["periods"] = periods
            if cur_hol and not any(p.get("holidays") for p in periods):
                rule["holidays_paid"] = True
    elif cur_days is not None:
        rule["paid_days"] = list(cur_days)
    if free_days and (rule.get("periods") or any(k in rule for k in ("hourly", "flat", "daily_max"))):
        # the free days are taken out of every paid window ("$2/hr Mon-Fri, free Sat-Sun" stays Mon-Fri)
        if rule.get("periods"):
            for p in rule["periods"]:
                if not p.get("free") and not (p.get("days") is None and p.get("holidays")):
                    p["days"] = [d for d in (p.get("days") if p.get("days") is not None else range(7)) if d not in free_days]
            rule["periods"] = [p for p in rule["periods"] if p.get("free") or p.get("days") or p.get("holidays")]
            if not rule["periods"]:
                rule.pop("periods")
                rule["paid_days"] = []
        else:
            rule["paid_days"] = [d for d in (rule.get("paid_days") if rule.get("paid_days") is not None else range(7))
                                 if d not in free_days]
    if night_max:
        if any(k in rule for k in ("hourly", "flat", "daily_max")) or rule.get("periods"):
            notes.append("night maximum %s not used (its hours are not given)" % dc.money(min(night_max)))
        else:
            rule["daily_max"] = min(night_max)     # "$10 overnight" alone: the only price given
    # "$2/hr, free Mon-Sun": a price whose every paid day the text also calls free contradicts itself, so the
    # lot is price unknown (never $0)
    if any(k in rule for k in ("hourly", "flat", "daily_max")) or any(
            p.get("hourly") is not None or p.get("flat") is not None or p.get("max") is not None for p in rule.get("periods") or []):
        free_said = set(free_days)
        for k, t in enumerate(toks):      # days written right after "free" in the same clause
            if t[0] == "days" and k and toks[k - 1][0] == "free" and not CLAUSE_SEP.search(text[toks[k - 1][2]:t[1]]):
                free_said |= t[3][0]
        nonfree = [p for p in rule.get("periods") or [] if not p.get("free") and not (p.get("days") is None and p.get("holidays"))]
        if nonfree:
            paid = set().union(*(set(p.get("days") if p.get("days") is not None else range(7)) for p in nonfree))
        elif rule.get("periods"):
            paid = None       # only free windows: the base rate covers the rest of the week
        else:
            paid = set(rule["paid_days"]) if rule.get("paid_days") is not None else None
        if paid is not None and free_said and paid <= free_said:
            return {}, notes + ["free days cover every paid day; price not used"]
    if free_evenings and not (rule.get("paid_hours") or rule.get("periods")):
        # with a paid window ("$2/hr 8am-6pm") the evening is already unpaid; without one its hours are unknown
        notes.append("free evenings (hours not given)")
    roll = rule.pop("_roll_max", None)
    if roll:
        if rule.get("periods"):
            for p in rule["periods"]:
                if not p.get("free") and p.get("flat") is None and p.get("max") is None:
                    p["max"], p["roll_min"] = roll
        else:
            rule["periods"] = [{"days": rule.pop("paid_days") if rule.get("paid_days") is not None else list(range(7)),
                                "hours": rule.pop("paid_hours", None) or ["00:00", "24:00"],
                                "max": roll[0], "roll_min": roll[1]}]
    if rule.get("flat") is not None and rule.get("hourly") is not None:
        rule.pop("flat")   # "$2/hr ... $10" style: the bare amount is an evening or event price
        notes.append("extra flat amount ignored")
    return rule, notes


def free_text_flags(text):
    """FREE_PARKING style text ("Mon-Fri after 6pm, Sat, Sun, Holidays") -> {"holidays_free": True} when
    holidays are listed as free."""
    if text and re.search(r"holiday", str(text), re.I):
        return {"holidays_free": True}
    return {}


def free_text_windows(text):
    """FREE_PARKING text -> (free windows [(weekday set, start_min, end_min)], holidays_free, reserved spans).
    "Mon-Fri after 6pm, Sat, Sun, Holidays" -> Mon-Fri 18:00-24:00, Sat and Sun all day, holidays;
    "Midnight - 9 am; Reserved Parking 4 pm - Midnight" -> every day 00:00-09:00 free, 16:00-24:00 reserved."""
    wins, reserved, hol = [], [], False
    if not text or str(text).strip().lower() in ("n/a", "na", "none", "-"):
        return wins, hol, reserved
    for seg in re.split(r"[;,]", _strip_french(str(text))):
        seg = seg.strip()
        if not seg:
            continue
        days, seg_hol = set(), False
        for m in DAYS_RE.finditer(seg):
            d, h = _days_of(m)
            days |= d
            seg_hol = seg_hol or h
        hol = hol or seg_hol
        hr = find_hours(seg)
        if re.search(r"reserved", seg, re.I):
            if hr:
                reserved.append((days or set(range(7)), hr[0], hr[1]))
            continue
        if not days and seg_hol:
            continue
        if not days and not hr:
            continue
        wins.append((days or set(range(7)), hr[0] if hr else 0, hr[1] if hr else 1440))
    return wins, hol, reserved


def osm_charge_rule(tags):
    """OSM fee/charge/maxstay -> (rule or None, notes)."""
    fee = (tags.get("fee") or "").strip().lower()
    notes = []
    rule = None
    if fee == "no":
        rule = {"free": True}
    charge = tags.get("charge")
    if charge and fee != "no":
        r, notes = parse_rate_text(charge)
        if r and (any(k in r for k in ("hourly", "flat", "daily_max", "tiers"))
                  or any(p.get("hourly") is not None or p.get("flat") is not None or p.get("max") is not None
                         for p in r.get("periods") or [])):
            rule = r
        else:
            notes.append("charge tag %r" % charge[:60])
    # OSM maxstay should carry a unit; a bare number is read as minutes
    ms = parse_duration(tags.get("maxstay"), default_unit="min") if tags.get("maxstay") not in (None, "no", "unlimited") else None
    if ms:
        rule = dict(rule or {})
        rule["max_stay_min"] = ms
        if set(rule) == {"max_stay_min"}:
            rule["_unpriced"] = True
    return rule, notes


def parse_height(v):
    """'2.1', '2.1 m', '2.0 m max', "6'8\"" -> metres or None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    m = re.match(r"(\d+)\s*'\s*(\d+)?", s)
    if m:
        return round(int(m.group(1)) * 0.3048 + int(m.group(2) or 0) * 0.0254, 2)
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------- pricing

def _easter(year):
    """Easter Sunday (Gregorian, anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return dt.date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def _nth_monday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(0 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def anchor_date(tok, year):
    """A season end or free day for `year`: 'MM-DD' or a named anchor (victoria_day, fri_before_victoria_day,
    labour_day, thanksgiving, civic_holiday, family_day, good_friday, easter_monday)."""
    t = str(tok).strip().lower()
    m = re.match(r"^(\d{2})-(\d{2})$", t)
    if m:
        try:
            return dt.date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:          # 02-29 in a common year
            return dt.date(year, int(m.group(1)), 28)
    if t in ("victoria_day", "fri_before_victoria_day"):
        d = dt.date(year, 5, 24)
        while d.weekday() != 0:
            d -= dt.timedelta(days=1)
        return d - dt.timedelta(days=3) if t.startswith("fri") else d
    if t == "labour_day":
        return _nth_monday(year, 9, 1)
    if t == "thanksgiving":
        return _nth_monday(year, 10, 2)
    if t == "civic_holiday":
        return _nth_monday(year, 8, 1)
    if t == "family_day":
        return _nth_monday(year, 2, 3)
    if t == "good_friday":
        return _easter(year) - dt.timedelta(days=2)
    if t == "easter_monday":
        return _easter(year) + dt.timedelta(days=1)
    raise ValueError("unknown date anchor %r" % tok)


def _in_season(day, season):
    a, b = anchor_date(season[0], day.year), anchor_date(season[1], day.year)
    return a <= day <= b if a <= b else (day >= a or day <= b)


def _season_text(season, year=None):
    year = year or _today().year
    def one(s):
        return anchor_date(s, year).strftime("%b %d").replace(" 0", " ")
    named = any(not re.match(r"^\d{2}-\d{2}$", str(s)) for s in season)
    return "%s to %s%s" % (one(season[0]), one(season[1]), (" %d" % year) if named else "")


def free_day_dates(spec, years):
    """Place-level extra free days ('civic_holiday', '09-30', '2027-08-02') -> set of ISO dates for `years`."""
    out = set()
    for s in spec or []:
        s = str(s)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            out.add(s)
            continue
        for y in years:
            try:
                out.add(anchor_date(s, y).isoformat())
            except ValueError:
                pass
    return out


def _periods(rule):
    if rule.get("periods"):
        return rule["periods"]
    return [{"days": rule.get("paid_days") if rule.get("paid_days") is not None else list(range(7)),
             "hours": rule.get("paid_hours") or ["00:00", "24:00"], "max": rule.get("daily_max")}]


def _windows(rule, start, end, holidays):
    """Paid windows overlapping [start, end): (period, w_start, w_end, day). Days outside the rule's season
    have no windows."""
    out = []
    pers = _periods(rule)
    any_hol = any(p.get("holidays") for p in pers)
    season = rule.get("season")
    d = start.date() - dt.timedelta(days=1)
    while d <= end.date():
        if season and not _in_season(d, season):
            d += dt.timedelta(days=1)
            continue
        is_hol = d.isoformat() in holidays
        if is_hol and any_hol:
            todays = [p for p in pers if p.get("holidays")]
        elif is_hol and rule.get("holidays_free") and not rule.get("holidays_paid"):
            todays = []
        else:
            todays = [p for p in pers if d.weekday() in (p.get("days") if p.get("days") is not None else range(7))
                      and not (any_hol and p.get("holidays") and p.get("days") is None)]
        for p in todays:
            if p.get("months") and d.month not in p["months"]:
                continue
            a, b = hm(p["hours"][0]), hm(p["hours"][1])
            ws = dt.datetime.combine(d, dt.time()) + dt.timedelta(minutes=a)
            we = dt.datetime.combine(d, dt.time()) + dt.timedelta(minutes=b if b > a else b + 1440)
            if we > start and ws < end:
                out.append((p, ws, we, d))
        d += dt.timedelta(days=1)
    return out


def describe_rule(rule, year=None):
    """The rule in words, e.g. '$2.00/h Mon-Sat 09:00-18:00, $18.00 day max; free holidays'."""
    if not rule:
        return "no price data"
    if rule.get("free"):
        s = "free"
    elif rule.get("tiers"):
        parts = []
        for t in rule["tiers"]:
            parts.append(("%s up to %s" % (dc.money(t["cost"]) if t["cost"] else "free", dur_text(t["up_to_min"])))
                         if t.get("up_to_min") else "%s longer" % dc.money(t["cost"]))
        s = ", ".join(parts)
    elif rule.get("flat") is not None and rule.get("hourly") is None and not rule.get("periods"):
        s = "%s per entry" % dc.money(rule["flat"])
        if rule.get("paid_days") is not None or rule.get("paid_hours"):   # "$10 flat; free after 6pm and weekends"
            ph = rule.get("paid_hours") or ["00:00", "24:00"]
            pd = rule.get("paid_days") if rule.get("paid_days") is not None else range(7)
            s += " " + ("%s %s-%s" % (days_text(pd), ph[0], ph[1]) if list(ph) != ["00:00", "24:00"] else days_text(pd))
    else:
        bits = []
        if rule.get("first_hour") is not None and rule.get("hourly") is not None:
            bits.append("%s first hour, then %s/h" % (dc.money(rule["first_hour"]), dc.money(rule["hourly"])))
        elif rule.get("hourly") is not None:
            per = rule.get("per_min") or 60
            if per > 60 and float(per) % 60 == 0:      # a block price: one charge per started block
                bits.append("%s per %s" % (dc.money(rule["hourly"] * per / 60.0), dur_text(per)))
            else:
                unit = "" if per == 60 else " (%s per %g min)" % (dc.money(rule["hourly"] * per / 60.0), round(per, 2))
                bits.append("%s/h%s" % (dc.money(rule["hourly"]), unit))
        pers = rule.get("periods")
        if pers:
            for p in pers[:4]:
                w = "%s %s-%s" % (days_text(p.get("days") if p.get("days") is not None else range(7)), p["hours"][0], p["hours"][1])
                if p["hours"][0] == p["hours"][1]:
                    w = "%s from %s, 24 h" % (days_text(p.get("days") if p.get("days") is not None else range(7)), p["hours"][0])
                if p.get("holidays") and p.get("days") is None:
                    w = "holidays %s-%s" % tuple(p["hours"])
                elif p.get("holidays"):
                    w += " and holidays"
                if p.get("months"):
                    w += " " + months_text(p["months"])
                if p.get("free_first_min"):
                    w += " (first %s free)" % dur_text(p["free_first_min"])
                if p.get("free"):
                    w += " free"
                elif p.get("flat") is not None:
                    w += " %s flat" % dc.money(p["flat"])
                else:
                    if p.get("hourly") is not None and p.get("hourly") != rule.get("hourly"):
                        w += " %s/h" % dc.money(p["hourly"])
                    if p.get("max") is not None and p.get("roll_min"):
                        w += " (max %s per %s)" % (dc.money(p["max"]), dur_text(p["roll_min"]))
                    elif p.get("max") is not None:
                        w += " (max %s)" % dc.money(p["max"])
                bits.append(w)
        elif rule.get("paid_days") is not None or rule.get("paid_hours"):
            ph = rule.get("paid_hours") or ["00:00", "24:00"]
            pd = rule.get("paid_days") if rule.get("paid_days") is not None else range(7)   # "$2/hr 00:00-24:00": daily
            bits.append("%s %s-%s" % (days_text(pd), ph[0], ph[1]) if list(ph) != ["00:00", "24:00"] else days_text(pd))
        if rule.get("daily_max") is not None:
            bits.append("%s day max" % dc.money(rule["daily_max"]))
        if rule.get("flat") is not None:
            bits.append("%s per entry" % dc.money(rule["flat"]))
        if rule.get("evening_flat") is not None:
            bits.append("%s after %s" % (dc.money(rule["evening_flat"]), rule.get("evening_from") or "18:00"))
        if rule.get("min_charge"):
            bits.append("min %s" % dc.money(rule["min_charge"]))
        s = ", ".join(bits) or "paid, rate unknown"
    extra = []
    it = rule.get("intro")
    if it and it.get("hourly") is not None and it.get("minutes"):
        win = " (entry %s-%s)" % (it["from"], it["to"]) if it.get("from") and it.get("to") else ""
        extra.append("%s special %s/h for the first %s%s" % (
            days_text(it["days"]) if it.get("days") is not None else "daily", dc.money(it["hourly"]),
            dur_text(it["minutes"]), win))
    if rule.get("free_first_min"):
        extra.append("first %s free" % dur_text(rule["free_first_min"]))
    if rule.get("max_stay_min"):
        extra.append("%s max" % dur_text(rule["max_stay_min"]))
    if rule.get("holidays_free") and not rule.get("free"):
        extra.append("free on holidays")
    if rule.get("season") and list(rule["season"]) != ["01-01", "12-31"]:
        extra.append("paid %s" % _season_text(rule["season"], year))
        if rule.get("off_season_flat") is not None:
            extra.append("%s off season" % dc.money(rule["off_season_flat"]))
    return s + ("; " + ", ".join(extra) if extra else "")


def _longest_run(ivs):
    """Longest stretch in minutes of the merged intervals (touching ones join)."""
    best, cur_a, cur_b = 0.0, None, None
    for a, b in sorted(x for x in ivs if x[1] > x[0]):
        if cur_b is not None and a <= cur_b + dt.timedelta(seconds=30):
            cur_b = max(cur_b, b)
        else:
            if cur_b is not None:
                best = max(best, (cur_b - cur_a).total_seconds() / 60.0)
            cur_a, cur_b = a, b
    if cur_b is not None:
        best = max(best, (cur_b - cur_a).total_seconds() / 60.0)
    return best


def price_rule(rule, arrive, stay_min, holidays=None):
    """Cost in CAD (float) of parking under a normalized rule, or None when the rule cannot price this stay.
    Also returns why: (cost, basis_text, fits_stay). A rule's "tax" is added on top."""
    cost, basis, fits = _price_core(rule, arrive, stay_min, holidays)
    ic = _intro_cost(rule, arrive, stay_min, holidays)
    if ic is not None and cost is not None and ic < cost - 0.005:
        it = rule["intro"]
        cost = ic
        basis = "%s special: %s/h for the first %s, then regular rates (%s)" % (
            days_text(it["days"]) if it.get("days") is not None else "daily", dc.money(it["hourly"]),
            dur_text(it["minutes"]), describe_rule(rule, arrive.year))
    if cost and rule and rule.get("tax"):
        cost = round(cost * (1.0 + float(rule["tax"])) + 1e-9, 2)
        basis += " + %g%% HST" % round(float(rule["tax"]) * 100, 2)
    return cost, basis, fits


def _intro_cost(rule, arrive, stay_min, holidays=None):
    """An intro rate ("intro": {"days", "from", "to", "hourly", "minutes"}, e.g. Green P's Saturday special
    "$1.00 each hour for the first 2 hours, regular rates thereafter", entry 05:00-18:00): the first `minutes` at
    `hourly` (per started hour) plus the regular price of the rest of the stay. None when it does not apply; the
    caller keeps the lower of this and the regular price (the day maximum still caps a long stay)."""
    it = (rule or {}).get("intro")
    if not it or it.get("hourly") is None or not it.get("minutes"):
        return None
    if arrive.weekday() not in (it.get("days") if it.get("days") is not None else range(7)):
        return None
    if arrive.date().isoformat() in (holidays or set()) and not it.get("holidays"):
        return None
    tod = arrive.hour * 60 + arrive.minute
    if not (hm(it.get("from") or "00:00") <= tod < (hm(it.get("to") or "24:00") or 1440)):
        return None
    first = min(float(stay_min), float(it["minutes"]))
    cost = math.ceil(first / 60.0 - 1e-9) * float(it["hourly"])
    rest = float(stay_min) - float(it["minutes"])
    if rest > 0:
        base = dict(rule)
        base.pop("intro", None)
        rc, _b, _f = _price_core(base, arrive + dt.timedelta(minutes=int(it["minutes"])), rest, holidays)
        if rc is None:
            return None
        cost += rc
    return round(cost + 1e-9, 2)


def _price_core(rule, arrive, stay_min, holidays=None):
    holidays = holidays or set()
    if not rule or rule.get("_unpriced"):
        fits = not (rule and rule.get("max_stay_min") and stay_min > rule["max_stay_min"])
        return None, "no price data", fits
    stay_min = max(1, int(round(stay_min)))
    end = arrive + dt.timedelta(minutes=stay_min)
    desc = describe_rule(rule, arrive.year)
    max_stay = rule.get("max_stay_min")
    fits = True
    stay_days, d = [], arrive.date()
    while d <= (end - dt.timedelta(minutes=1)).date():
        stay_days.append(d)
        d += dt.timedelta(days=1)
    for np in rule.get("no_parking") or []:
        d = arrive.date() - dt.timedelta(days=1)
        while d <= end.date():
            if d.weekday() in np.get("days", range(5)) and (np.get("all_days") or d.isoformat() not in holidays) \
                    and (not np.get("season") or _in_season(d, np["season"])):
                a_m, b_m = hm(np["hours"][0]), hm(np["hours"][1])
                a = dt.datetime.combine(d, dt.time()) + dt.timedelta(minutes=a_m)
                b = dt.datetime.combine(d, dt.time()) + dt.timedelta(minutes=b_m if b_m > a_m else b_m + 1440)
                if a < end and b > arrive:
                    fits = False
            d += dt.timedelta(days=1)
    season = rule.get("season")
    if season:
        inside = [x for x in stay_days if _in_season(x, season)]
        stext = _season_text(season, arrive.year)
        if rule.get("off_season", "free") == "closed" and len(inside) < len(stay_days):
            return None, "closed off season (open %s)" % stext, False
        if not inside:
            oms = rule.get("off_season_max_stay_min")
            lim = ""
            if oms:
                lim = "; %s max off season" % dur_text(oms)
                if stay_min > oms:
                    fits = False
            if rule.get("off_season_flat") is not None:
                return float(rule["off_season_flat"]), "%s off season (the %s rate does not apply)%s" % (
                    dc.money(rule["off_season_flat"]), stext, lim), fits
            return 0.0, "free off season (paid %s)%s" % (stext, lim), fits
    if rule.get("currency", "CAD") != "CAD":
        return None, "price in %s: %s" % (rule["currency"], desc), fits
    fbm = rule.get("flat_by_month")
    if isinstance(fbm, list) and len(fbm) == 12 and fbm[arrive.month - 1] is not None:
        rule = dict(rule, flat=float(fbm[arrive.month - 1]))
        desc = "%s per entry in %s (price set by month)" % (dc.money(rule["flat"]), arrive.strftime("%B"))
    pdays = rule.get("_partial_days")
    if pdays is not None:
        other = [x for x in stay_days if x.weekday() not in pdays
                 and not (x.isoformat() in holidays and rule.get("holidays_free"))]
        if other:
            return None, "price published for %s only (%s)" % (days_text(pdays), desc), fits
    has_window = bool(rule.get("periods") or rule.get("paid_days") is not None or rule.get("paid_hours"))
    limit_paid = rule.get("max_stay_applies", "paid") == "paid" and has_window

    def limit_ok(wins, extra=()):
        if not max_stay:
            return True
        if limit_paid:
            ivs = [(max(ws, arrive), min(we, end)) for p, ws, we, _d in wins if not p.get("free") and not p.get("no_limit")]
            return _longest_run(ivs + list(extra)) <= max_stay + 0.5
        return stay_min <= max_stay

    hol_free = bool(rule.get("holidays_free") and not rule.get("holidays_paid"))
    all_hol = hol_free and all(x.isoformat() in holidays for x in stay_days)
    if rule.get("free"):
        if max_stay and stay_min > max_stay:
            fits = False
        return 0.0, desc, fits
    if rule.get("free_first_min") and stay_min <= rule["free_first_min"]:
        if max_stay and stay_min > max_stay:
            fits = False
        return 0.0, "free for the first %s (%s)" % (dur_text(rule["free_first_min"]), desc), fits
    # evening flat rate: the hourly rate stops at the evening start, so the evening is never charged twice
    if rule.get("evening_flat") is not None and rule.get("hourly") is not None and not rule.get("periods"):
        ef = rule.get("evening_from") or "18:00"
        ph = rule.get("paid_hours")
        if not ph:
            rule = dict(rule, paid_hours=["00:00", ef])
        elif hm(ph[0]) < hm(ph[1]) and hm(ph[1]) > hm(ef) and hm(ph[0]) < hm(ef):
            rule = dict(rule, paid_hours=[ph[0], ef])
    if rule.get("tiers"):
        if not has_window and all_hol:
            return 0.0, "free on holidays (%s)" % desc, fits
        if has_window and not _windows(rule, arrive, end, holidays):
            return 0.0, "free at this time (%s)" % desc, fits
        if max_stay and stay_min > max_stay:
            fits = False
        for t in rule["tiers"]:
            if t.get("up_to_min") is None or stay_min <= t["up_to_min"]:
                return float(t["cost"]), "%s for %s (%s)" % (dc.money(t["cost"]), dur_text(stay_min), desc), fits
        return None, "stay longer than the price table (%s)" % desc, fits
    hourly = rule.get("hourly")
    priced_periods = any(p.get("flat") is not None or p.get("hourly") is not None or p.get("free") or p.get("max") is not None
                         for p in rule.get("periods") or [])
    if hourly is None and rule.get("flat") is None and not priced_periods:
        if rule.get("daily_max") is not None:
            wins = _windows(rule, arrive, end, holidays)
            fits = fits and limit_ok(wins)
            if not has_window and not season:
                # "$5/day": one price per started 24 h
                if all_hol:
                    return 0.0, "free on holidays (%s)" % desc, fits
                n = max(1, int(math.ceil(stay_min / 1440.0 - 1e-9)))
                return float(rule["daily_max"]) * n, "%s per day (%s)" % (dc.money(rule["daily_max"]), desc), fits
            # one day price for each calendar day the stay touches (in season, not a free holiday)
            days = sorted(set(w[3] for w in wins))
            if not days:
                return 0.0, "free at this time (%s)" % desc, fits
            c = float(rule["daily_max"]) * len(days)
            return c, "%s per %sday (%s)" % (dc.money(rule["daily_max"]), "paid " if has_window else "", desc), fits
        return None, "paid, rate unknown (%s)" % desc, fits
    charge_start = arrive + dt.timedelta(minutes=rule.get("free_first_min") or 0)
    wins = _windows(rule, arrive, end, holidays)
    if rule.get("flat") is not None and hourly is None and not rule.get("periods"):
        if has_window and not wins:
            return 0.0, "free at this time (%s)" % desc, fits
        if not has_window and all_hol:
            return 0.0, "free on holidays (%s)" % desc, fits
        fits = fits and limit_ok(wins)
        if rule.get("evening_flat") is not None:
            ef = hm(rule.get("evening_from") or "18:00")
            if arrive.hour * 60 + arrive.minute >= ef:
                return float(rule["evening_flat"]), "evening rate (%s)" % desc, fits
        return float(rule["flat"]), desc, fits
    total, per_day, paid_min = 0.0, {}, 0.0
    covered, done_to, entries, rolling = [], None, {}, {}
    arr_tod = arrive.hour * 60 + arrive.minute
    for p, ws, we, d in sorted(wins, key=lambda w: w[1]):
        if p.get("flat") is not None and p.get("entry"):
            covered.append((max(ws, arrive), min(we, end)))
            entries.setdefault(d, []).append((p, ws, we))
            continue
        a, b = max(ws, charge_start), min(we, end)
        if p.get("free_first_min"):   # e.g. Stratford winter Saturdays: the first 4 h in the window are free
            a = max(a, max(ws, arrive) + dt.timedelta(minutes=p["free_first_min"]))
        if done_to is not None and a < done_to:
            a = done_to   # overlapping windows (Green P day 7-19 and night 17-7): each minute is charged once
        done_to = max(done_to or b, b)
        covered.append((max(ws, arrive), min(we, end)))
        mins = (b - a).total_seconds() / 60.0
        if mins <= 0 or p.get("free"):
            continue
        if p.get("flat") is not None:
            c = float(p["flat"])
        else:
            cap = p.get("max")
            for em in p.get("entry_max") or []:
                if d == arrive.date() and ws <= arrive and hm(em["from"]) <= arr_tod < hm(em["to"]):
                    cap = em["max"]
            h = p.get("hourly", hourly)
            if h is None:
                if cap is None:
                    return None, "paid, rate unknown for part of the stay (%s)" % desc, fits
                c = float(cap)           # only a day or evening price is known: charge it
            else:
                per = p.get("per_min", rule.get("per_min") or 60) or 60
                if p.get("roll_min") and cap is not None:
                    # "Any 4 hour Period" maximum: priced below over the whole stay, not per calendar day
                    rolling.setdefault((float(cap), int(p["roll_min"]), h, per), []).append((a, b))
                    paid_min += mins
                    continue
                c = math.ceil(mins / per - 1e-9) * h * per / 60.0
                if cap is not None:
                    c = min(c, float(cap))
                paid_min += mins
        per_day[d] = per_day.get(d, 0.0) + c
    # rolling maximums: each block of roll_min minutes from the first charged minute costs at most the maximum
    # (14 h at $4/h with an any-4-hour $12 maximum: $12 + $12 + $12 + $8)
    for (cap, roll, h, per), ivs in rolling.items():
        t0 = min(x[0] for x in ivs)
        blocks = {}
        for a, b in ivs:
            while a < b:
                k = int((a - t0).total_seconds() // (roll * 60))
                seg = min(b, t0 + dt.timedelta(minutes=roll * (k + 1)))
                blocks[k] = blocks.get(k, 0.0) + (seg - a).total_seconds() / 60.0
                a = seg
        for k, mins in blocks.items():
            day = (t0 + dt.timedelta(minutes=roll * k)).date()
            per_day[day] = per_day.get(day, 0.0) + min(math.ceil(mins / per - 1e-9) * h * per / 60.0, cap)
    # entry-priced windows ("Flat Rate (5am - 3pm)", "If entering between 3 PM and 2 AM"): one charge per day,
    # the latest window holding the arrival, else the first one the stay reaches
    for d, lst in entries.items():
        inside = [x for x in lst if x[1] <= arrive < x[2]]
        pick = max(inside, key=lambda x: x[1]) if inside else min(lst, key=lambda x: x[1])
        if pick[2] > charge_start:
            per_day[d] = per_day.get(d, 0.0) + float(pick[0]["flat"])
    gap_ivs = []
    if rule.get("fill") and hourly is not None:
        # time outside every listed window is charged at the base rate
        cur = charge_start
        for a, b in sorted(covered):
            if a > cur:
                gap_ivs.append((cur, min(a, end)))
            cur = max(cur, b)
        if cur < end:
            gap_ivs.append((cur, end))
        by_day = {}
        for a, b in gap_ivs:
            while a < b:
                nxt = dt.datetime.combine(a.date() + dt.timedelta(days=1), dt.time())
                seg_end = min(b, nxt)
                if not season or _in_season(a.date(), season):
                    by_day[a.date()] = by_day.get(a.date(), 0.0) + (seg_end - a).total_seconds() / 60.0
                a = seg_end
        per = rule.get("per_min") or 60
        for d, mins in by_day.items():
            if mins <= 0:
                continue
            c = math.ceil(mins / per - 1e-9) * hourly * per / 60.0
            if rule.get("fill_cap") is not None:
                c = min(c, float(rule["fill_cap"]))
            per_day[d] = per_day.get(d, 0.0) + c
            paid_min += mins
    fits = fits and limit_ok(wins, gap_ivs)
    for d, c in per_day.items():
        if rule.get("periods") and rule.get("daily_max") is not None:
            c = min(c, float(rule["daily_max"]))
        total += c
    if rule.get("first_hour") is not None and hourly is not None and paid_min > 0:
        total += max(0.0, float(rule["first_hour"]) - hourly)    # the first hour was charged at the hourly rate
    if rule.get("evening_flat") is not None:
        ef = hm(rule.get("evening_from") or "18:00")
        d = arrive.date()
        while d <= end.date():
            es = dt.datetime.combine(d, dt.time()) + dt.timedelta(minutes=ef)
            ee = dt.datetime.combine(d, dt.time()) + dt.timedelta(days=1)
            paid_day = rule.get("paid_days") is None or d.weekday() in rule["paid_days"]
            if paid_day and es < end and ee > arrive and not (d.isoformat() in holidays and rule.get("holidays_free")) \
                    and (not season or _in_season(d, season)):
                total += float(rule["evening_flat"])
            d += dt.timedelta(days=1)
    if total <= 0:
        when = "Sat" if arrive.weekday() == 5 else ("Sun" if arrive.weekday() == 6 else arrive.strftime("%a"))
        if arrive.date().isoformat() in holidays:
            when = "holiday"
        return 0.0, "free at this time (%s; %s)" % (when, desc), fits
    if rule.get("min_charge") and total < rule["min_charge"]:
        total = float(rule["min_charge"])
    total = round(total + 1e-9, 2)
    return total, "%s paid (%s)" % (dur_text(paid_min), desc) if paid_min else desc, fits


# --------------------------------------------------------------------------- lots

def _walk(lat, lon, la, lo):
    c = cfg()
    m = th.haversine_m(lat, lon, la, lo) * c.get("walk_factor", 1.3)
    return int(round(m)), int(math.ceil(m / c.get("walk_m_per_min", 75.0)))


def no_parking_hit(rule, arrive, stay_min, holidays=None):
    """'HH:MM to HH:MM' of the first no-parking window the stay [arrive, arrive + stay_min) overlaps, or None.
    Same day, holiday and season logic as price_rule."""
    holidays = set(holidays or ())
    end = arrive + dt.timedelta(minutes=stay_min)
    for np in (rule or {}).get("no_parking") or []:
        d = arrive.date() - dt.timedelta(days=1)
        while d <= end.date():
            if d.weekday() in np.get("days", range(5)) and (np.get("all_days") or d.isoformat() not in holidays) \
                    and (not np.get("season") or _in_season(d, np["season"])):
                a_m, b_m = hm(np["hours"][0]), hm(np["hours"][1])
                a = dt.datetime.combine(d, dt.time()) + dt.timedelta(minutes=a_m)
                b = dt.datetime.combine(d, dt.time()) + dt.timedelta(minutes=b_m if b_m > a_m else b_m + 1440)
                if a < end and b > arrive:
                    return "%s to %s" % (np["hours"][0], np["hours"][1])
            d += dt.timedelta(days=1)
    return None


def make_lot(name, lat, lon, kind, rule, source, source_url, confidence, dest, arrive, stay_min, holidays,
             capacity=None, notes="", reservation=None, downtown=False, max_height=None, often_full=False, party=None):
    """One priced parking option. party: {"adults", "children", "seniors"} or None (then per-person admission
    is priced for one adult and says so)."""
    walk_m, walk_min = _walk(dest[0], dest[1], lat, lon)
    sm = cfg().get("search_min", {})
    search = sm.get("downtown_street", 8) if kind == "street" and downtown else sm.get(kind, 2)
    cost, basis, fits = price_rule(rule, arrive, stay_min, holidays)
    r = rule or {}
    per_person = bool(r.get("per_person"))
    if cost is None:
        text = "price unknown, check signs"
        if basis.startswith("closed off season"):
            text = "closed for the season"
        elif r.get("currency") and r["currency"] != "CAD":
            text = "price in %s, check signs" % r["currency"]
    elif cost == 0:
        text = "free"
    elif per_person:
        pp = r["per_person"] if isinstance(r["per_person"], dict) else {"adult": cost}
        if party:
            cost = round(cost * (party.get("adults") or 0) + float(pp.get("child") or 0) * (party.get("children") or 0)
                         + float(pp.get("senior") or cost) * (party.get("seniors") or 0) + 1e-9, 2)
            text = "%s admission for your party (per person)" % dc.money(cost)
        else:
            others = ", ".join("%s %s" % (k, dc.money(pp[k])) for k in ("child", "senior") if pp.get(k) is not None)
            text = "%s per adult (admission, per person%s)" % (dc.money(cost), "; " + others if others else "")
    elif r.get("per_passenger"):
        n = sum(int((party or {}).get(k) or 0) for k in ("adults", "children", "seniors"))
        if party and n:
            cost = round(cost + float(r["per_passenger"]) * max(0, n - 1) + 1e-9, 2)
            text = "%s for your party (%s)" % (dc.money(cost), dur_text(stay_min))
        else:
            text = "%s for the driver + %s per passenger (%s)" % (dc.money(cost), dc.money(r["per_passenger"]), dur_text(stay_min))
    else:
        text = "%s for %s" % (dc.money(cost), dur_text(stay_min))
    note_bits = [n.rstrip(". ") for n in [notes, r.get("hours_note"), r.get("note")] if n]
    if r.get("season_year") and arrive.year != int(r["season_year"]):
        note_bits.append("season dates are the %d dates; check the %d dates" % (int(r["season_year"]), arrive.year))
    if r.get("rate_year") and arrive.year > int(r["rate_year"]):
        note_bits.append("%d rates; check the %d rates" % (int(r["rate_year"]), arrive.year))
    old_rate = None
    if r.get("rate_year") and arrive.year > int(r["rate_year"]):
        old_rate = "%d price" % int(r["rate_year"])
    unfit = None
    if not fits:
        hit = no_parking_hit(r, arrive, stay_min, holidays)
        if hit:
            unfit = "no parking %s during your stay" % hit
        elif r.get("max_stay_min"):
            unfit = "max stay %s, shorter than your stay" % dur_text(r["max_stay_min"])
    return {"name": name, "lat": round(lat, 6), "lon": round(lon, 6), "walk_m": walk_m, "walk_min": walk_min,
            "unfit_reason": unfit,
            "search_min": search, "cost": cost, "cost_text": text, "basis": basis, "kind": kind, "source": source,
            "source_url": source_url, "confidence": confidence, "free": cost == 0.0, "fits_stay": bool(fits),
            "max_stay_min": r.get("max_stay_min"), "capacity": capacity, "often_full": bool(often_full),
            "reservation": reservation, "notes": "; ".join(note_bits), "max_height_m": max_height,
            "per_person": per_person, "unverified": False, "maybe_closed": False,
            "_dist": th.haversine_m(dest[0], dest[1], lat, lon), "_rule": rule, "_old_rate": old_rate}


# --------------------------------------------------------------------------- curated places

def load_places(warnings=None):
    out = []
    for path in sorted(glob.glob(os.path.join(PLACES_DIR, "parking-places*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for p in data.get("places") or []:
                p.setdefault("_file", os.path.basename(path))
                out.append(p)
        except (OSError, ValueError) as e:
            if warnings is not None:
                warnings.append("curated parking file %s unreadable (%s)" % (os.path.basename(path), e))
    return out


def _res_entry(name, rv, arrive, warnings, season_ok=True):
    """A reservation note for the output, or None when it does not apply on the arrival date. rv: {"text", "url",
    "lead_days" (booking opens N days ahead), "min_lead_days" (must book at least N days ahead), "max_lead_days",
    "window": ["MM-DD", "MM-DD"] (when reservations are needed), "year": the year the text was written for}."""
    if not season_ok:
        return None
    win = rv.get("window")
    if win and not _in_season(arrive.date(), win):
        return None
    text = rv.get("text") or "reservation required"
    if rv.get("year") and arrive.year != int(rv["year"]):
        text += " (%d details; check the %d rules)" % (int(rv["year"]), arrive.year)
    out = {"name": name, "text": text, "url": rv.get("url") or "", "lead_days": rv.get("lead_days") or 0}
    ahead = (arrive.date() - _today()).days
    if rv.get("min_lead_days"):
        out["min_lead_days"] = rv["min_lead_days"]
        if ahead < int(rv["min_lead_days"]) and (not rv.get("year") or arrive.year == int(rv["year"])):
            warnings.append("%s: reservations close at midnight %d day%s before the visit; a same-day visit will be "
                            "turned away" % (name, int(rv["min_lead_days"]), "" if int(rv["min_lead_days"]) == 1 else "s"))
    if rv.get("max_lead_days"):
        out["max_lead_days"] = rv["max_lead_days"]
        if ahead > int(rv["max_lead_days"]):
            opens = arrive.date() - dt.timedelta(days=int(rv["max_lead_days"]))
            out["text"] += " Booking opens %s." % opens.isoformat()
    return out


def curated(lat, lon, arrive, stay_min, holidays, radius_m, warnings, party=None):
    """Lots and rules of every curated place whose match radius holds the destination. Lots closed for the
    season are left out (a rule says so); reservations show only when they apply on the arrival date."""
    lots, rules, res, sources = [], [], [], []
    end = arrive + dt.timedelta(minutes=stay_min)
    for p in load_places(warnings):
        m = p.get("match") or {}
        try:
            d = th.haversine_m(lat, lon, float(m["lat"]), float(m["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d > float(m.get("radius_km", 3)) * 1000.0:
            continue
        season = p.get("season") or {}
        src = p.get("source") or ""
        sources.append("%s (curated, %s)" % (p.get("name", p.get("id")), p.get("source_date") or "") + (": " + src if src else ""))
        place_in_season = True
        if season.get("from") and season.get("to") and (season["from"], season["to"]) != ("01-01", "12-31"):
            sp = [season["from"], season["to"]]
            place_in_season = _in_season(arrive.date(), sp)
            if not place_in_season:
                off = season.get("off_season", "free")
                rules.append("%s: off season, %s rates do not apply%s" % (
                    p.get("name"), _season_text(sp, arrive.year),
                    " (lots closed)" if off == "closed" else " (posted signs still rule)"))
            elif season.get("note"):
                rules.append("%s: season %s: %s" % (p.get("name"), _season_text(sp, arrive.year), season["note"]))
            if season.get("year") and arrive.year != int(season["year"]):
                rules.append("%s: season dates are the %d dates; check the %d dates" % (
                    p.get("name"), int(season["year"]), arrive.year))
        for r in p.get("rules") or []:
            rules.append("%s: %s" % (p.get("name"), r) if not str(r).lower().startswith(str(p.get("name", "")).lower()) else r)
        rv = p.get("reservation") or {}
        if rv.get("required") or rv.get("text"):
            e = _res_entry(p.get("name"), rv, arrive, warnings, place_in_season)
            if e:
                res.append(e)
        hol = set(holidays)
        if p.get("free_days"):    # town-listed free days on top of the statutory holidays
            hol |= free_day_dates(p["free_days"], sorted({arrive.year, end.year}))
        for lot in p.get("lots") or []:
            try:
                la, lo = float(lot["lat"]), float(lot["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            kind = lot.get("kind") or "lot"
            dist = th.haversine_m(lat, lon, la, lo)
            if dist > radius_m and kind not in ("park", "park_ride", "resort"):
                continue
            rule = copy.deepcopy(lot.get("rule") or {})
            if rule and season.get("from") and "season" not in rule and not rule.get("free") and kind != "park":
                rule["season"] = [season["from"], season["to"]]
                rule.setdefault("off_season", season.get("off_season", "free"))
                if season.get("off_season_max_stay_min"):
                    rule.setdefault("off_season_max_stay_min", season["off_season_max_stay_min"])
            # a lot's booking applies in its own season; a park lot ignores the town's paid season
            lot_in_season = (not rule.get("season") or _in_season(arrive.date(), rule["season"])) \
                and (place_in_season or kind == "park")
            lrv = lot.get("reservation")
            if isinstance(lrv, dict):
                rd = lrv
                e = _res_entry(lot.get("name"), rd, arrive, warnings, lot_in_season) \
                    if (rd.get("required") or rd.get("text")) else None
                lrv = None
                if e:
                    res.append(e)
                    lrv = e["text"]
            elif lrv and not lot_in_season:
                lrv = None      # e.g. a Parks Canada booking outside the reservation season
            out = make_lot(lot.get("name") or "Lot", la, lo, kind, rule or None, "%s (curated)" % p.get("name"),
                           lot.get("source") or src, lot.get("confidence") or p.get("confidence") or "curated", (lat, lon),
                           arrive, stay_min, hol, capacity=lot.get("capacity"), notes=lot.get("notes") or "",
                           reservation=lrv or None, often_full=bool(lot.get("often_full")), party=party)
            if out["basis"].startswith("closed off season"):
                rules.append("%s: %s is %s" % (p.get("name"), out["name"], out["basis"].replace("closed off season", "closed for the season")))
                continue
            lots.append(out)
    return lots, rules, res, sources


# --------------------------------------------------------------------------- ArcGIS layers

def _geom_point(g, dest):
    if not g:
        return None
    if "x" in g and "y" in g:
        return g["y"], g["x"]
    if g.get("rings"):
        ring = g["rings"][0]
        pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)
    if g.get("paths"):
        poly = [(p[1], p[0]) for path in g["paths"] for p in path]
        if len(poly) == 1:
            return poly[0]
        cum = dc.cum_dist(poly)
        along, _off, i = dc.project(poly, cum, dest[0], dest[1])
        seg = cum[i + 1] - cum[i]
        t = 0.0 if seg <= 0 else (along - cum[i]) / seg
        return (poly[i][0] + t * (poly[i + 1][0] - poly[i][0]), poly[i][1] + t * (poly[i + 1][1] - poly[i][1]))
    return None


def _fmt_name(tpl, attrs):
    if "{" not in tpl:
        v = attrs.get(tpl)
        return str(v).strip() if v not in (None, "") and tpl in attrs else tpl
    def rep(m):
        v = attrs.get(m.group(1))
        v = "" if v in (None, "None") else str(v).strip()
        return v.title() if v.isupper() and len(v) > 3 else v
    s = re.sub(r"\{([^}]+)\}", rep, tpl)
    s = re.sub(r"\(\s*to\s*\)|\(\s*\)", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,")
    return s


def layer_rule(layer, attrs):
    """Normalized rule (or None when the layer has no price for this feature) and notes."""
    f = layer.get("fields") or {}
    rule = copy.deepcopy(layer.get("defaults") or {})
    notes = []
    priced = False
    texts = []
    for key in f.get("rate_text") or []:
        v = attrs.get(key)
        if v not in (None, "") and str(v).strip():
            texts.append(str(v).strip())
    if texts:
        r, n = parse_rate_text(" ".join(texts))
        notes += n
        if r.get("periods") is not None or r.get("paid_hours") or r.get("paid_days") is not None:
            for k in ("periods", "paid_hours", "paid_days"):
                rule.pop(k, None)
        rule.update(r)
        priced = any(k in r for k in ("hourly", "flat", "daily_max", "tiers")) or any(
            p.get("hourly") is not None or p.get("flat") is not None or p.get("max") is not None for p in r.get("periods") or [])
    if f.get("schedule"):
        r, n = parse_rate_text(attrs.get(f["schedule"]))
        if r:
            for k in ("periods", "paid_hours", "paid_days"):
                rule.pop(k, None)
            rule.update(r)
    if f.get("free_text"):
        ft = attrs.get(f["free_text"])
        rule.update(free_text_flags(ft))
        free_wins, _hol, reserved = free_text_windows(ft)
        for days, a, b in reserved:     # "Reserved Parking 4 pm - Midnight": the lot is not public then
            rule.setdefault("no_parking", []).append({"days": sorted(days), "hours": [hm_text(a), hm_text(b)], "all_days": True})
            notes.append("reserved parking %s-%s" % (hm_text(a), hm_text(b)))
        if free_wins and not (rule.get("periods") or rule.get("paid_hours") or rule.get("paid_days") is not None):
            # no paid schedule ("n/a"): paid whenever the free text does not say free
            groups = {}
            for wd in range(7):
                spans = sorted((a, b) for days, a, b in free_wins if wd in days)
                paid, cur = [], 0
                for a, b in spans:
                    if a > cur:
                        paid.append((cur, a))
                    cur = max(cur, b)
                if cur < 1440:
                    paid.append((cur, 1440))
                for iv in paid:
                    groups.setdefault(iv, []).append(wd)
            rule["periods"] = [{"days": wds, "hours": [hm_text(a), hm_text(b)]}
                               for (a, b), wds in sorted(groups.items(), key=lambda x: (x[1][0], x[0]))]
            if not rule["periods"]:
                rule.pop("periods")
                rule["free"] = True
            notes.append("paid hours taken as the times the free-parking text does not cover")
    for key, out in (("hourly", "hourly"), ("daily_max", "daily_max"), ("min_charge", "min_charge"), ("evening_flat", "evening_flat")):
        if f.get(key):
            v = money_field(attrs.get(f[key]))
            if v is not None:
                rule[out] = v
                if key == "hourly":
                    rule.setdefault("per_min", 60)
                if key in ("hourly", "daily_max"):
                    priced = True
    if f.get("max_stay"):
        ms = parse_duration(attrs.get(f["max_stay"]))
        if ms:
            rule["max_stay_min"] = ms
    if f.get("days_count"):
        n = int(num(attrs.get(f["days_count"])) or 0)
        if n in (5, 6, 7):
            days = list(range(n))
            if rule.get("periods"):
                rule["periods"] = [p for p in rule["periods"] if any(d in days for d in p.get("days", range(7)))]
            else:
                rule["paid_days"] = days
    if f.get("rush"):
        v = str(attrs.get(f["rush"]) or "").lower()
        np = []
        if v in ("both", "7-9"):
            np.append({"days": [0, 1, 2, 3, 4], "hours": ["07:00", "09:00"]})
        if v in ("both", "4-6"):
            np.append({"days": [0, 1, 2, 3, 4], "hours": ["16:00", "18:00"]})
        if np:
            rule["no_parking"] = list(rule.get("no_parking") or []) + np
            notes.append("no parking weekdays %s" % " and ".join("%s-%s" % tuple(x["hours"]) for x in np))
    if f.get("paid_flag"):
        v = str(attrs.get(f["paid_flag"]) or "").strip().lower()
        if v == "free":
            rule = {"free": True}
            priced = True
    if f.get("note"):
        v = attrs.get(f["note"])
        if v not in (None, "") and not re.search(r"staff|resid|varies", str(v), re.I):
            notes.append(str(v).strip())
    if f.get("peak_flag") and re.search(r"\dPH", str(attrs.get(f["peak_flag"]) or "")):
        np = list(rule.get("no_parking") or [])
        for p in _periods(rule):
            days = [d for d in (p.get("days") if p.get("days") is not None else range(5)) if d < 5]
            a, b = hm(p["hours"][0]), hm(p["hours"][1])
            if days and 14 * 60 <= b <= 16 * 60 + 30:
                np.append({"days": days, "hours": [p["hours"][1], "17:30"]})
            if days and 9 * 60 <= a <= 10 * 60:
                np.append({"days": days, "hours": ["07:00", p["hours"][0]]})
        if np:
            rule["no_parking"] = np
            notes.append("peak-hour no parking outside the paid hours on weekdays (about %s)"
                         % ", ".join("%s-%s" % tuple(x["hours"]) for x in np))
    ov = layer.get("overrides")
    if ov:
        key = str(attrs.get(ov["field"]))
        if key in ov.get("values", {}):
            for k, v in ov["values"][key].items():
                if v is None:
                    rule.pop(k, None)
                else:
                    rule[k] = v
    if not priced:
        return None, notes
    return rule, notes


def _arcgis_query(layer, lat, lon, radius_m, warnings):
    url = layer["url"].rstrip("/") + "/query?" + urllib.parse.urlencode({
        "geometry": "%.5f,%.5f" % (lon, lat), "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "distance": str(int(radius_m)), "units": "esriSRUnit_Meter",
        "outFields": "*", "returnGeometry": "true", "outSR": "4326", "f": "json"})
    try:
        data = dc.fetch_json(url, max_age_h=cfg().get("arcgis_max_age_h", 168), min_interval=0.3, timeout=30,
                             validate=_arcgis_check)
    except dc.FetchError as e:   # an {"error": ...} reply: not cached
        warnings.append("%s parking layer returned an error (%s)" % (layer["city"], th.safe_text(str(e))))
        return []
    except Exception as e:  # noqa: BLE001 (network or JSON failure: warn and go on)
        warnings.append("%s parking layer unavailable (%s); using other sources" % (layer["city"], _err(e)))
        return []
    if not isinstance(data, dict) or data.get("error"):
        warnings.append("%s parking layer returned an error (%s)" % (layer["city"], th.safe_text((data or {}).get("error", {}).get("message", "?"))
                                                                    if isinstance(data, dict) else "bad reply"))
        return []
    return data.get("features") or []


def _skip_note(warnings, source, skipped):
    """One warning for the lots of a source left out because their data could not be read (the other lots of the
    source are still listed)."""
    if skipped:
        warnings.append("%d %s parking lot%s left out: unreadable data (%s)" % (
            len(skipped), source, "" if len(skipped) == 1 else "s", type(skipped[0]).__name__))


def _err(e):
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %d" % e.code
    return type(e).__name__ + (": %s" % getattr(e, "reason", "") if getattr(e, "reason", "") else "")


def _feature_date(v):
    """An ArcGIS date field (epoch ms, or 'YYYY-MM...' text) -> date or None."""
    if isinstance(v, (int, float)) and v > 0:
        try:
            return dt.datetime.fromtimestamp(v / 1000.0, dt.timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    m = re.match(r"^(\d{4})-(\d{2})", str(v or ""))
    return dt.date(int(m.group(1)), int(m.group(2)), 1) if m else None


def _stale(layer, today, feat_date=None):
    """True when a priced layer's data (the feature's own edit date when known, else the layer's) is over
    24 months old."""
    if layer.get("confidence") == "stale":
        return True
    f = layer.get("fields") or {}
    if not any(f.get(k) for k in ("hourly", "daily_max", "rate_text", "paid_flag")):
        return False   # location-only layers do not go stale the way prices do
    if layer.get("confirmed"):
        return False
    d = feat_date or _feature_date(layer.get("edited"))
    if d:
        return (today.year - d.year) * 12 + today.month - d.month > 24
    return False


def city_lots(lat, lon, arrive, stay_min, holidays, radius_m, height_m, warnings):
    lots, sources, skipped = [], [], []
    for layer in cfg().get("city_layers") or []:
        if not th.in_bbox(lat, lon, layer["bbox"]):
            continue
        feats = _arcgis_query(layer, lat, lon, radius_m, warnings)
        n = 0
        f = layer.get("fields") or {}
        for ft in feats:
            try:
                attrs = ft.get("attributes") or {}
                if any(str(attrs.get(k)).strip() not in vals for k, vals in (layer.get("filter") or {}).items()):
                    continue
                if any(re.search(rx, str(attrs.get(k) or ""), re.I) for k, rx in (layer.get("exclude") or {}).items()):
                    continue
                pt = _geom_point(ft.get("geometry"), (lat, lon))
                if not pt:
                    continue
                kind = layer.get("kind", "lot")
                tpl = f.get("name") or "Parking"
                for kr in layer.get("kind_rules") or []:
                    if re.search(kr["match"], str(attrs.get(kr["field"]) or "")):
                        kind, tpl = kr.get("kind", kind), kr.get("name", tpl)
                if f.get("kind_field"):
                    kind = (layer.get("kind_map") or {}).get(str(attrs.get(f["kind_field"]) or "").strip(), kind)
                mh = parse_height(attrs.get(f["maxheight"])) if f.get("maxheight") else None
                if height_m and mh and height_m > mh:
                    continue
                rule, notes = layer_rule(layer, attrs)
                name = _fmt_name(tpl, attrs)
                if len(name) < 8:
                    name = {"street": "Paid street parking", "garage": "Parking garage"}.get(kind, "Parking lot")
                addr = attrs.get(f["address"]) if f.get("address") else None
                if addr and str(addr).strip() and str(addr).strip().lower() not in name.lower():
                    name = "%s, %s" % (name, str(addr).strip())
                cap = None
                if f.get("capacity"):
                    cap = int(num(attrs.get(f["capacity"])) or 0) or None
                link = attrs.get(f["url"]) if f.get("url") else None
                fdate = _feature_date(attrs.get(f["edited"])) if f.get("edited") else None
                conf = "stale" if _stale(layer, arrive.date(), fdate) else layer.get("confidence", "official")
                if conf == "stale":
                    notes.append("data last edited %s" % (fdate.strftime("%Y-%m") if fdate else (layer.get("edited") or "long ago")))
                lot = make_lot(name, pt[0], pt[1], kind, rule, layer.get("source_name") or layer["city"],
                               link or layer.get("url_link") or layer.get("source"), conf, (lat, lon), arrive, stay_min,
                               holidays, capacity=cap, notes="; ".join(notes), downtown=layer.get("downtown"),
                               max_height=mh)
                if link and re.match(r"https?://", str(link)):
                    lot["_website"] = str(link)   # the lot's own page in the layer
                if conf == "stale":
                    lot["_stale_text"] = "old price (%s)" % (notes[-1] if notes else "stale data")
                lots.append(lot)
                n += 1
            except Exception as e:  # noqa: BLE001 (one unreadable lot must not drop the rest of the source)
                skipped.append(e)
                log("parking: skipped one %s lot: %r" % (layer["city"], e))
        if n:
            sources.append("%s: %s" % (layer.get("source_name") or layer["city"], layer.get("source")))
    _skip_note(warnings, "city parking layer", skipped)
    return lots, sources


# --------------------------------------------------------------------------- Toronto Green P

def _gp_days(text):
    """Green P title or row -> (weekday set, holidays flag). Fixes the 'nday - Sunday' typos in the 2019 data."""
    t = re.sub(r"^\s*(?:n|on)day\b", "Monday", _strip_french(text or ""), flags=re.I)
    days, hol = set(), bool(re.search(r"holiday", t, re.I))
    for m in DAYS_RE.finditer(t):
        d, h = _days_of(m)
        days |= d
        hol = hol or h
    return days, hol


GP_SPAN_RE = re.compile(r"\b(?P<d1>%s)\.?\s*(?P<t1>%s)\s*(?:-|–|to)\s*(?P<d2>%s)\.?\s*(?P<t2>%s)" % (DAY_WORD, TIME, DAY_WORD, TIME), re.I)


def _gp_span(text):
    """A Green P row window written as a day and time range ("Sat 7am - Mon 7am") -> [(days, [start, end])]
    periods in the rule's form (end == start is 24 h from the start, end < start wraps past midnight), or None.
    Sat 7am to Mon 7am is Sat and Sun, each 07:00 to 07:00 the next morning."""
    m = GP_SPAN_RE.search(_strip_french(text or ""))
    if not m:
        return None
    if not all(_ampm(m.group(k)) or ":" in m.group(k) or m.group(k).strip().lower() in ("midnight", "noon") for k in ("t1", "t2")):
        return None
    d1, d2 = DAY_MAP[m.group("d1").lower()[:3]], DAY_MAP[m.group("d2").lower()[:3]]
    t1, t2 = _clock(m.group("t1"))[0], _clock(m.group("t2"))[0]
    span = ((d2 - d1) % 7) * 1440 + t2 - t1
    if span <= 0:
        span += 7 * 1440
    full, rest = divmod(span, 1440)
    out = []
    if full:
        out.append((sorted((d1 + i) % 7 for i in range(full)), [hm_text(t1), hm_text(t1)]))
    if rest:
        out.append(([(d1 + full) % 7], [hm_text(t1), hm_text((t1 + rest) % 1440)]))
    return out


def _gp_roll(text):
    """'Maximum (Any 4 hour Period)', '24 Hour Rolling Maximum', 'Maximum per 12 hour period' -> the block length
    in minutes (each block of the stay costs at most the maximum), else None."""
    t = text or ""
    if not re.search(r"\bany\b|\bper\s*\d|rolling", t, re.I):
        return None
    m = re.search(r"(\d+)\s*-?\s*(?:hours?|hrs?|h)\b", t, re.I)
    return int(m.group(1)) * 60 if m and 0 < int(m.group(1)) <= 48 else None


def _greenp_rule(cp):
    """Green P 2019 carpark -> normalized rule (half-hour rate with day/night maximums, flat or entry rates,
    free windows, CLOSED times and NO PARKING addenda)."""
    half = num(cp.get("rate_half_hour"))
    rule = {"per_min": 30}
    if half:
        rule["hourly"] = half * 2
    details = cp.get("rate_details") or {}
    info = []
    for per in details.get("periods") or []:
        title = _strip_french(per.get("title") or "")
        days, hol = _gp_days(title)
        info.append((per, title, days, hol))
    named, intro = set(), None
    for per, title, days, _hol in info:
        if re.search(r"special", title, re.I):
            # "SATURDAY SPECIAL": "$1.00 EACH HOUR FOR THE FIRST 2 HOURS - REGULAR RATES THEREAFTER" (notes only)
            mm = re.search(r"\$\s*(\d+(?:\.\d+)?)\s*(?:each|per|an?)\s*(?:hour|hr)s?\s*for\s+the\s+first\s+(\d+)\s*(?:hours?|hrs?)",
                           " ".join(_strip_french(n) for n in per.get("notes") or []), re.I)
            if mm and days:
                intro = {"days": sorted(days), "hourly": float(mm.group(1)), "minutes": int(mm.group(2)) * 60}
                for a in details.get("addenda") or []:
                    t = _strip_french(a)
                    if re.search(r"entry", t, re.I) and find_hours(t):
                        a_, b_ = find_hours(t)
                        intro["from"], intro["to"] = hm_text(a_), hm_text(b_)
            continue
        if not re.search(r"free parking", title, re.I):
            named |= days
    rest = sorted(set(range(7)) - named) or list(range(7))
    periods, no_parking, maxes = [], [], []
    all7 = list(range(7))
    for per, title, days, hol in info:
        if re.search(r"special", title, re.I):
            continue
        if re.search(r"free parking", title, re.I):
            # rows: "Monday - Friday" / "AFTER 3pm", "Saturday & Sunday" / "ALL DAY"
            for r in per.get("rates") or []:
                when, rate = r.get("when") or "", r.get("rate") or ""
                d2, h2 = _gp_days(when)
                if not d2 and not h2:
                    continue
                rng = None if re.search(r"all\s*day", rate, re.I) else (find_hours(rate) or find_hours(when))
                rng = rng or (0, 1440)
                p = {"days": sorted(d2) if d2 else None, "hours": [hm_text(rng[0]), hm_text(rng[1])], "free": True}
                if h2:
                    p["holidays"] = True
                periods.append(p)
            continue
        pdays = sorted(days) if days else (None if hol else rest)
        # rates written in the title: "Day Maximum until 6 PM - $30.00 (Entering 7 AM - 10 AM)", "Day maximum (7 am - 6 pm) - $7"
        ems = []
        for line in re.split(r"[\r\n]+", title):
            mm = re.search(r"\$\s*(\d+(?:\.\d+)?)", line)
            amt = float(mm.group(1)) if mm else None
            ent = re.search(r"entering\s+(.*?)\)", line, re.I)
            if amt is not None and ent and find_hours(ent.group(1)):
                a, b = find_hours(ent.group(1))
                until = re.search(r"until\s+(%s)" % TIME, line, re.I)
                ems.append({"from": hm_text(a), "to": hm_text(b), "max": amt,
                            "until": _clock(until.group(1))[0] if until else 18 * 60})
            elif amt is not None and find_hours(line):
                a, b = find_hours(line)
                periods.append({"days": pdays, "hours": [hm_text(a), hm_text(b)], "max": amt})
                maxes.append(amt)
            elif re.search(r"no\s+(?:day\s+)?max", line, re.I) and find_hours(line):
                a, b = find_hours(line)
                periods.append({"days": pdays, "hours": [hm_text(a), hm_text(b)]})
        if ems:
            start = min(hm(e["from"]) for e in ems)
            p = {"days": pdays, "hours": [hm_text(start), hm_text(max(e["until"] for e in ems))],
                 "max": max(e["max"] for e in ems), "entry_max": [{k: e[k] for k in ("from", "to", "max")} for e in ems]}
            periods.append(p)
            maxes.append(p["max"])
        for r in per.get("rates") or []:
            when, rate = (r.get("when") or ""), (r.get("rate") or "").strip()
            if re.search(r"please|monthly permit|main level|event", when, re.I):
                continue
            span = _gp_span(when)       # "Maximum per 12 hour period (Sat 7am - Mon 7am)": one window, not two days
            rd, rhol = (set(), bool(re.search(r"holiday", when, re.I))) if span else _gp_days(when)
            rdays = sorted(rd) if rd else pdays
            rng = (0, 1440) if span else find_hours(when)
            if rng is None and re.search(r"any\s*\d+\s*h|per\s*\d+\s*h|rolling|24 ?h", when, re.I):
                rng = (0, 1440)
            free = bool(re.match(r"free", rate, re.I))
            if rng is None:
                if free or (rd or rhol):    # "Weekend and Statutory Holidays: Free" covers the whole day
                    rng = (0, 1440)
                else:
                    continue
            wins = span or [(rdays, [hm_text(rng[0]), hm_text(rng[1])])]
            if re.search(r"closed", rate, re.I):
                for wd, hours in wins:
                    no_parking.append({"days": wd if wd is not None else all7, "hours": hours})
                continue
            p = {}
            if hol or rhol:
                p["holidays"] = True
            amt = num(rate)
            if free:
                p["free"] = True
            elif re.search(r"no\s+(?:day\s+)?max", rate + " " + when, re.I):
                pass
            elif amt is None:
                continue
            elif re.search(r"flat|entering|evening rate", when, re.I) or not half:
                p["flat"], p["entry"] = amt, True
            else:
                p["max"] = amt
                maxes.append(amt)
                roll = _gp_roll(when)
                if roll:
                    p["roll_min"] = roll   # "Any 4 hour Period": each 4 h block of the stay costs at most the max
            for wd, hours in wins:
                periods.append(dict(p, days=wd, hours=hours))
    for a in details.get("addenda") or []:
        t = _strip_french(a)
        rng = find_hours(t)
        if not rng or (intro and re.search(r"entry", t, re.I)):
            continue
        if re.search(r"no parking", t, re.I):
            no_parking.append({"days": all7, "hours": [hm_text(rng[0]), hm_text(rng[1])], "all_days": True})
        elif re.search(r"hours of operation", t, re.I) and rng != (0, 1440):
            no_parking.append({"days": all7, "hours": [hm_text(rng[1] % 1440), hm_text(rng[0])], "all_days": True})
    for p in periods:
        if p.get("days") is None:
            p.pop("days")
    if no_parking:
        rule["no_parking"] = no_parking
    if periods:
        rule["periods"] = periods
        rule["fill"] = bool(half)
        if half and maxes:
            rule["fill_cap"] = max(maxes)   # time outside the listed windows never costs more than a day maximum
    elif not half:
        return None
    if intro and half:
        rule["intro"] = intro
    return rule


def _cp_id(v):
    """Green P carpark number as a plain string: 'CP043', '043', 43 -> '43'."""
    s = str(v if v is not None else "").upper().replace("CP", "").strip()
    return s.lstrip("0") or s


def occupancy_index(recs):
    """TPA occupancy records -> ({carpark: (peak %, 'YYYY Qn')} for the latest quarter, {carparks in any
    quarter of the reply}, 'YYYY-YYYY' years covered)."""
    latest = max(((r.get("Year") or "", r.get("Quarter") or "") for r in recs), default=None)
    occ, seen, yrs = {}, set(), set()
    for r in recs:
        cpn = _cp_id(r.get("Car Park Number"))
        if not cpn:
            continue
        seen.add(cpn)
        if r.get("Year"):
            yrs.add(str(r["Year"]))
        if (r.get("Year"), r.get("Quarter")) == latest:
            pct = num(r.get("Average Daily Peak Occupancy %"))
            if pct is not None:
                occ[cpn] = (pct, "%s %s" % latest)
    years = ""
    if yrs:
        years = min(yrs) if len(yrs) == 1 else "%s-%s" % (min(yrs), max(yrs))
    return occ, seen, years


def maybe_closed(cp, seen, t=None):
    """A Green P 2019 carpark that is in no quarter of the occupancy feed. TTC commuter lots and waterfront park
    lots are never in the feed, so their absence says nothing (parking.json toronto.occupancy_about); a carpark in
    toronto.open (its own Green P page was live when checked) counts as seen."""
    t = t or cfg()["toronto"]
    cid = _cp_id(cp.get("id"))
    if cp.get("is_ttc") or cid in seen or cid in set(_cp_id(x.get("id")) for x in t.get("open") or []):
        return False
    ex = t.get("occupancy_exempt_re")
    return not (ex and re.search(ex, cp.get("address") or "", re.I))


def _greenp_check(body):
    """dc.fetch validate for the Green P 2019 file: a JSON object with a carparks list."""
    data = json.loads(body.decode("utf-8"))
    return None if isinstance(data, dict) and isinstance(data.get("carparks"), list) else "no carpark list in the reply"


def _ckan_check(body):
    """dc.fetch validate for a CKAN datastore_search reply: {"success": false, "error": ...} comes with HTTP 200
    on some errors; a usable reply has a records list."""
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or data.get("success") is False:
        err = data.get("error") if isinstance(data, dict) else None
        return "error reply%s" % (" (%s)" % str(err.get("message") if isinstance(err, dict) else err)[:100] if err else "")
    if not isinstance((data.get("result") or {}).get("records"), list):
        return "no records in the reply"
    return None


def toronto_lots(lat, lon, arrive, stay_min, holidays, radius_m, height_m, warnings):
    t = cfg()["toronto"]
    if not th.in_bbox(lat, lon, t["bbox"]):
        return [], []
    try:
        data = json.loads(dc.fetch(t["greenp_url"], max_age_h=t.get("greenp_max_age_h", 720), timeout=60,
                                   validate=_greenp_check).decode("utf-8"))
        cps = data.get("carparks") or []
    except Exception as e:  # noqa: BLE001
        warnings.append("Green P 2019 carpark data unavailable (%s)" % (e if isinstance(e, dc.FetchError) else _err(e)))
        closed = set(_cp_id(x.get("id")) for x in t.get("closed") or [])
        new = new_carpark_lots(lat, lon, arrive, stay_min, holidays, radius_m, height_m, {}, closed, t)
        return new, (["Green P carparks opened after 2019 (rates from each Green P page, %s)" % (
            t.get("verified") or "curated")] if new else [])
    occ, seen, years = {}, set(), ""
    try:
        od = dc.fetch_json(t["occupancy_url"], max_age_h=t.get("occupancy_max_age_h", 168), timeout=60,
                           validate=_ckan_check)
        recs = (od.get("result") or {}).get("records") or []
        occ, seen, years = occupancy_index(recs)
    except Exception as e:  # noqa: BLE001
        warnings.append("TPA parking occupancy feed unavailable (%s)" % (e if isinstance(e, dc.FetchError) else _err(e)))
    # a carpark missing from every quarter of the feed may have closed; only judged on a full feed reply
    judge = len(seen) >= int(t.get("occupancy_min_carparks", 50))
    closed = set(_cp_id(x.get("id")) for x in t.get("closed") or [])
    lots, skipped = [], []
    for cp in cps:
        try:
            if _cp_id(cp.get("id")) in closed:
                continue   # known closed (parking.json toronto.closed, with the operator's notice)
            try:
                la, lo = float(cp["lat"]), float(cp["lng"])
            except (KeyError, TypeError, ValueError):
                continue
            if th.haversine_m(lat, lon, la, lo) > radius_m:
                continue
            mh = num(cp.get("max_height"))
            if height_m and mh and height_m > mh:
                continue
            rule = _greenp_rule(cp)
            kind = "park_ride" if cp.get("is_ttc") else ("garage" if cp.get("carpark_type") == "garage" else "lot")
            o = occ.get(_cp_id(cp.get("id")))
            notes = [t["greenp_note"]]
            full = False
            if o:
                full = o[0] > t.get("full_pct", 85)
                notes.append("peak occupancy %d%% (%s)" % (o[0], o[1]))
            gone = judge and maybe_closed(cp, seen, t)
            if gone:
                notes.insert(0, t.get("maybe_closed_note", "not in Toronto's %s parking occupancy data, so it may have "
                                      "closed; check before driving there") % (years or "current"))
            lot = make_lot("Green P %s" % ((cp.get("address") or "").strip() or cp.get("id")), la, lo, kind, rule, "Green P 2019 open data",
                           cp.get("slug") or t["greenp_source"], "stale", (lat, lon), arrive, stay_min, holidays,
                           capacity=int(num(cp.get("capacity")) or 0) or None, notes="; ".join(notes),
                           max_height=mh, often_full=full)
            lot["cost_text"] += " (2019 data)"    # free and unknown results come from the same stale data
            lot["_greenp"] = {"id": _cp_id(cp.get("id")), "address": (cp.get("address") or "").strip()}
            if gone:
                lot["maybe_closed"] = True
            lots.append(lot)
        except Exception as e:  # noqa: BLE001 (one unreadable lot must not drop the rest of the source)
            skipped.append(e)
            log("parking: skipped one Green P lot: %r" % e)
    _skip_note(warnings, "Green P 2019", skipped)
    new = new_carpark_lots(lat, lon, arrive, stay_min, holidays, radius_m, height_m, occ, closed, t)
    lots += new
    srcs = ["Green P carparks 2019 (Toronto open data, prices stale): %s" % t["greenp_source"]]
    if new:
        srcs.append("Green P carparks opened after 2019 (rates from each Green P page, %s)" % (
            t.get("verified") or "curated"))
    if occ:
        srcs.append("TPA parking occupancy (latest quarter): %s" % t["occupancy_source"])
    return lots, srcs


def new_carpark_lots(lat, lon, arrive, stay_min, holidays, radius_m, height_m, occ, closed, t=None):
    """Green P carparks in the occupancy feed but not in the 2019 file (parking.json toronto.new_carparks: the feed
    has no coordinates, so they are curated with the rates from each carpark's own page). Without a rule the lot
    is listed as 'price unknown, opened after 2019' with its page as the check URL."""
    t = t or cfg()["toronto"]
    out = []
    for cp in t.get("new_carparks") or []:
        cid = _cp_id(cp.get("id"))
        if cid in closed:
            continue
        try:
            la, lo = float(cp["lat"]), float(cp["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if th.haversine_m(lat, lon, la, lo) > radius_m:
            continue
        mh = cp.get("max_height_m")
        if height_m and mh and height_m > mh:
            continue
        rule = cp.get("rule")
        notes = []
        if cp.get("note"):
            notes.append(cp["note"])
        o = occ.get(cid)
        full = False
        if o:
            full = o[0] > t.get("full_pct", 85)
            notes.append("peak occupancy %d%% (%s)" % (o[0], o[1]))
        asof = (rule or {}).get("as_of") or cp.get("verified") or t.get("verified")
        lot = make_lot("Green P %s" % (cp.get("address") or cid).strip(), la, lo, cp.get("kind") or "garage", rule,
                       "Green P carpark page (curated %s)" % asof, cp.get("url") or t["greenp_source"], "curated",
                       (lat, lon), arrive, stay_min, holidays, capacity=cp.get("capacity"), notes="; ".join(notes),
                       max_height=mh, often_full=full)
        if rule:
            lot["cost_text"] += " (Green P page, %s)" % asof
        else:
            lot["cost_text"] = "price unknown, opened after 2019"
        lot["_greenp"] = {"id": cid, "address": (cp.get("address") or "").strip(), "current": bool(rule)}
        if cp.get("url"):
            lot["_website"] = cp["url"]
        out.append(lot)
    return out


# --------------------------------------------------------------------------- OpenStreetMap

OVERPASS_BUSY_TEXT = "OpenStreetMap parking search failed (HTTP"
OVERPASS_TIMEOUT_TEXT = "OpenStreetMap parking search failed (timed out"
OVERPASS_MAX_WAIT_S = 60     # all attempts and the wait between them for one search


def _overpass_check(body):
    """dc.fetch validate for Overpass: None for a usable reply, else why not. Overpass sends a query that ran
    out of time or memory as HTTP 200 with a "remark" and no (or partial) elements."""
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        return "bad reply"
    remark = str(data.get("remark") or "")
    if "error" in remark.lower() or "timed out" in remark.lower():
        return "Overpass: %s" % remark[:120]
    return None


def _arcgis_check(body):
    """dc.fetch validate for an ArcGIS query: an {"error": ...} reply comes with HTTP 200."""
    data = json.loads(body)
    if not isinstance(data, dict):
        return "bad reply"
    if data.get("error"):
        err = data["error"]
        return str((err.get("message") if isinstance(err, dict) else err) or "error")[:120]
    return None


def _timed_out(e):
    """True for a socket timeout, raw or wrapped in a URLError, and for an Overpass "timed out" remark."""
    if isinstance(e, socket.timeout) or isinstance(getattr(e, "reason", None), socket.timeout):
        return True
    return isinstance(e, dc.FetchError) and "timed out" in str(e).lower()


def _overpass(query, warnings, max_age_h=24):
    """Overpass reply or None. Busy (HTTP 429/406/504): wait up to 30 s and retry once; the attempts and the wait
    together stay within OVERPASS_MAX_WAIT_S (overpass.max_wait_s). When an earlier search in the same run already
    failed busy or timed out (the run shares `warnings`), skip the call and its wait and use the disk cache only.
    A reply with an error remark is a failure: it is never cached and gives a warning."""
    oc = cfg()["overpass"]
    url = oc["url"]
    if any(str(w).startswith((OVERPASS_BUSY_TEXT, OVERPASS_TIMEOUT_TEXT)) for w in warnings):
        log("parking: Overpass failed earlier in this run; using the disk cache only")
        return _cached_reply(url, query)
    budget = float(oc.get("max_wait_s", OVERPASS_MAX_WAIT_S))
    t0 = time.time()
    for attempt in (0, 1):
        try:
            return dc.fetch_json(url, data={"data": query}, max_age_h=max_age_h, min_interval=1.0,
                                 timeout=max(5.0, min(40.0, budget - (time.time() - t0))),
                                 stale_max_h=oc.get("stale_max_h", 720), validate=_overpass_check)
        except urllib.error.HTTPError as e:
            if e.code in (429, 406, 504) and attempt == 0:   # busy: both slots taken, or the queue timed out
                wait = min(30.0, budget - (time.time() - t0) - 10.0)   # keep 10 s for the retry itself
                if wait > 0:
                    log("parking: Overpass busy (HTTP %d), waiting %d s" % (e.code, round(wait)))
                    time.sleep(wait)
                    continue
            warnings.append("OpenStreetMap parking search failed (%s); results may miss free lots" % _err(e))
        except Exception as e:  # noqa: BLE001
            if _timed_out(e):     # as good as busy: later searches in this run skip the network
                warnings.append("%s%s); results may miss free lots" % (
                    OVERPASS_TIMEOUT_TEXT, " on the Overpass server" if isinstance(e, dc.FetchError) else ""))
            elif isinstance(e, dc.FetchError):
                warnings.append("OpenStreetMap parking search failed (%s); results may miss free lots" % th.safe_text(str(e)))
            else:
                warnings.append("OpenStreetMap parking search failed (%s); results may miss free lots" % _err(e))
        return None
    return None


def _cached_reply(url, query):
    """A cached Overpass reply for this query (fresh or stale), without touching the network. A cached error
    reply (from before replies were checked) is not used."""
    try:
        path = dc._cache_path(url, urllib.parse.urlencode({"data": query}).encode("utf-8"))
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                body = f.read()
            if not _overpass_check(body):
                return json.loads(body)
    except (OSError, ValueError, AttributeError):
        pass
    return None


COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
PUBLIC_ACCESS = ("yes", "public", "permissive")


def osm_name(tags, kind, lat, lon, la, lo):
    """A readable name for an OSM lot: its name, else operator and address, else a direction. 'Public' only
    when the access tag says so; an untagged lot says 'access not tagged'."""
    if tags.get("name"):
        return tags["name"]
    addr = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    what = {"garage": "garage", "park_ride": "park and ride lot", "street": "street parking"}.get(kind, "lot")
    access = (tags.get("access") or "").strip().lower()
    if tags.get("operator"):
        return "%s %s%s" % (tags["operator"], what, (", " + addr) if addr else "")
    pre = "Public %s" % what if access in PUBLIC_ACCESS else what[0].upper() + what[1:]
    post = "" if access in PUBLIC_ACCESS else " (access not tagged)"
    if addr:
        return "%s, %s%s" % (pre, addr, post)
    ang = math.degrees(math.atan2((lo - lon) * math.cos(math.radians(lat)), la - lat)) % 360
    return "%s %s of the destination%s" % (pre, COMPASS[int((ang + 22.5) // 45) % 8], post)


def opening_window(text):
    """Simple OSM opening_hours "06:00-23:00" (every day) -> ("06:00", "23:00"); anything else -> None."""
    m = re.match(r"^\s*(?:Mo-Su\s+)?(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s*$", text or "")
    if not m or m.group(1) == "00:00" and m.group(2) in ("24:00", "23:59"):
        return None
    return m.group(1), m.group(2)


def open_for_stay(window, arrive, stay_min):
    """True when the stay [arrive, arrive + stay_min) lies inside one opening of a daily window ("06:00", "23:00");
    a window that ends at or before its start runs past midnight ("20:00", "06:00": 20:00 to 06:00 next day)."""
    a, b = hm(window[0]), hm(window[1])
    end = arrive + dt.timedelta(minutes=stay_min)
    for back in (1, 0):   # the opening that began yesterday evening, then today's
        d0 = dt.datetime.combine(arrive.date() - dt.timedelta(days=back), dt.time())
        ws = d0 + dt.timedelta(minutes=a)
        we = d0 + dt.timedelta(minutes=b if b > a else b + 1440)
        if ws <= arrive and end <= we:
            return True
    return False


def facility_free(tags):
    """A fee=no OSM lot that is probably for the users of a facility (a 2017 City of Ottawa import pattern):
    access permissive rather than yes/public, or a ref or source:date with no name."""
    access = (tags.get("access") or "").strip().lower()
    return access == "permissive" or bool((tags.get("ref") or tags.get("source:date")) and not tags.get("name"))


def city_of(lat, lon):
    """The city of a point for search strings: Toronto inside its bbox, else the city of a configured city layer
    whose bbox holds it, else ''."""
    c = cfg()
    if th.in_bbox(lat, lon, c["toronto"]["bbox"]):
        return "Toronto"
    for layer in c.get("city_layers") or []:
        if layer.get("city") and th.in_bbox(lat, lon, layer["bbox"]):
            return layer["city"]
    return ""


def osm_lots(lat, lon, arrive, stay_min, holidays, radius_m, height_m, warnings, country=None):
    """OpenStreetMap lots near the point. With `country` (ISO 3166-1 alpha-2 of the destination) only lots in
    that country are returned: near a border river a lot on the other side is close as the crow flies but not
    walkable (Goat Island across from Table Rock)."""
    oc = cfg()["overpass"]
    if country:
        q = ('[out:json][timeout:%d];area["ISO3166-1"="%s"][admin_level=2]->.c;'
             'nwr["amenity"="parking"](around:%d,%.5f,%.5f)(area.c);out center tags;'
             % (oc.get("timeout_s", 25), country.upper()[:2], int(radius_m), lat, lon))
    else:
        q = ('[out:json][timeout:%d];nwr["amenity"="parking"](around:%d,%.5f,%.5f);out center tags;'
             % (oc.get("timeout_s", 25), int(radius_m), lat, lon))
    data = _overpass(q, warnings, oc.get("max_age_h", 24))
    if not data:
        return [], []
    public = set(oc.get("public_access") or ["", "yes", "public", "permissive"])
    lots, skipped = [], []
    for el in data.get("elements") or []:
        try:
            tags = el.get("tags") or {}
            if (tags.get("access") or "").strip().lower() not in public:
                continue
            if el.get("type") == "node":
                la, lo = el.get("lat"), el.get("lon")
            else:
                c = el.get("center") or {}
                la, lo = c.get("lat"), c.get("lon")
            if la is None or lo is None:
                continue
            mh = parse_height(tags.get("maxheight")) if tags.get("maxheight") not in (None, "none", "default") else None
            if height_m and mh and height_m > mh:
                continue
            pk_ = (tags.get("parking") or "").lower()
            kind = "lot"
            if pk_ in ("multi-storey", "underground", "rooftop"):
                kind = "garage"
            elif pk_ in ("street_side", "lane", "on_street"):
                kind = "street"
            if tags.get("park_ride") and tags.get("park_ride") != "no":
                kind = "park_ride"
            rule, notes = osm_charge_rule(tags)
            unverified = bool(rule and rule.get("free"))
            facility = unverified and facility_free(tags)
            name = osm_name(tags, kind, lat, lon, la, lo)
            if facility and tags.get("operator") and not tags.get("name"):
                addr = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
                name = "%s facility lot%s" % (tags["operator"], (", " + addr) if addr else "")
            if (tags.get("fee") or "").lower() == "yes" and rule is None:
                notes.append("paid (fee=yes), no rate in OSM")
            if tags.get("operator") and tags["operator"] not in name:
                notes.append("operator %s" % tags["operator"])
            conf, stale_yr = "osm", None
            yr = re.search(r"\b(19|20)(\d\d)\b", tags.get("charge") or "")
            if yr and int(yr.group(0)) <= arrive.year - 2:
                conf, stale_yr = "stale", yr.group(0)
                notes.append("price tagged as of %s" % yr.group(0))
            elif rule is not None and not rule.get("free"):
                for k in ("check_date:charge", "check_date:fee", "check_date", "source:date"):
                    cd = re.match(r"\s*((?:19|20)\d\d)", tags.get(k) or "")
                    if cd:
                        if int(cd.group(1)) <= arrive.year - 2:
                            conf, stale_yr = "stale", cd.group(1)
                            notes.append("price checked %s" % cd.group(1))
                        break
            oh = opening_window(tags.get("opening_hours"))
            lot = make_lot(name, la, lo, kind, rule, "OpenStreetMap", "https://www.openstreetmap.org/%s/%s" % (el.get("type"), el.get("id")),
                           conf, (lat, lon), arrive, stay_min, holidays,
                           capacity=int(num(tags.get("capacity")) or 0) or None, notes="; ".join(notes), max_height=mh)
            if unverified and lot["cost"] == 0:
                lot["cost_text"] = FACILITY_FREE_TEXT if facility else UNVERIFIED_FREE_TEXT
                lot["unverified"] = True
                lot["_unverified_free"] = "facility" if facility else "plain"
            if not tags.get("name"):
                lot["_generic_name"] = True
            if stale_yr:
                lot["_stale_text"] = "old price (%s)" % stale_yr
            lot["_street"] = (tags.get("addr:street") or "").strip()
            lot["_addr"] = " ".join(x.strip() for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x and x.strip())
            lot["_city"] = (tags.get("addr:city") or "").strip() or city_of(lat, lon)
            site = tags.get("website") or tags.get("contact:website") or tags.get("url")
            if site and re.match(r"https?://", site):
                lot["_website"] = site
            if oh:
                lot["notes"] = "; ".join([n for n in [lot["notes"], "open %s-%s" % oh] if n])
                if not open_for_stay(oh, arrive, stay_min):
                    lot["fits_stay"] = False
                    why = "open %s to %s only" % oh
                    lot["unfit_reason"] = "%s; %s" % (lot["unfit_reason"], why) if lot.get("unfit_reason") else why
            lots.append(lot)
        except Exception as e:  # noqa: BLE001 (one unreadable lot must not drop the rest of the source)
            skipped.append(e)
            log("parking: skipped one OpenStreetMap lot: %r" % e)
    _skip_note(warnings, "OpenStreetMap", skipped)
    return lots, ["OpenStreetMap via Overpass, %s" % oc.get("attribution", "(c) OpenStreetMap contributors")]


# --------------------------------------------------------------------------- merge and rank

def _own_schedule(x):
    """1 when a street lot's hours come from layer defaults (an hours_note), 0 when they come from its own data."""
    return 1 if x["kind"] == "street" and (x.get("_rule") or {}).get("hours_note") else 0


def dedupe(lots, within_m=60.0):
    """Lots from several sources within `within_m` of each other are one lot: keep the best confidence among
    the priced ones (or the best overall), prefer a street source whose hours are its own (a pay machine) over
    one built from layer defaults, never let a same-confidence 'free' hide a paid reading of the same block,
    keep a real name, and merge capacity and the often-full flag."""
    groups = []
    for lot in lots:
        for g in groups:
            if any(o["source"] != lot["source"] and (o["kind"] == lot["kind"] or "street" not in (o["kind"], lot["kind"]))
                   and th.haversine_m(lot["lat"], lot["lon"], o["lat"], o["lon"]) <= within_m for o in g):
                g.append(lot)
                break
        else:
            groups.append([lot])
    out = []
    for g in groups:
        priced = [x for x in g if x["cost"] is not None]
        pool = priced or g
        best = dict(min(pool, key=lambda x: (CONF_RANK.get(x["confidence"], 9), _own_schedule(x), x["_dist"])))
        if best["cost"] == 0 and not best.get("_unverified_free"):
            paid = [x for x in priced if x["cost"] > 0 and x["confidence"] == best["confidence"]]
            if paid:   # e.g. a street segment priced to 17:30 by default, a pay machine on the block to 19:30
                top = max(paid, key=lambda x: x["cost"])
                for k in ("cost", "cost_text", "basis", "free"):
                    best[k] = top[k]
                best["notes"] = "; ".join([n for n in [best.get("notes"), "priced from %s" % top["name"]] if n])
        caps = [x["capacity"] for x in g if x.get("capacity")]
        if caps:
            best["capacity"] = max(caps)
        best["often_full"] = any(x.get("often_full") for x in g)
        if best["kind"] == "street" and best["fits_stay"]:
            bad = [x for x in g if not x["fits_stay"] and x["confidence"] == best["confidence"]]
            if bad:   # a pay machine on the same block knows about peak-hour no-parking
                best["fits_stay"] = False
                best["notes"] = "; ".join([n for n in [best.get("notes"), bad[0].get("notes")] if n])
        if best.get("_generic_name"):
            named = [x for x in g if not x.get("_generic_name")]
            if named:
                best["name"] = named[0]["name"]
                best.pop("_generic_name", None)
        others = sorted(set(x["source"] for x in g if x["source"] != best["source"]))
        if others:
            best["notes"] = "; ".join([n for n in [(best.get("notes") or "").rstrip(". "), "also in " + ", ".join(others)] if n])
        out.append(best)
    return out


def rank_key(lot):
    """Fitting lots first, walkable before far, then by tier: 0 priced (an unverified free OSM lot sorts as if it
    cost just over $5, so a verified option up to $5 comes first), 1 priced but may have closed (a Green P 2019
    carpark missing from the occupancy feed: absence is a weak signal, so it stays ahead of the tiers below), 2 an
    unverified free lot that looks like a facility lot, 3 unknown price, 4 unknown price and may have closed;
    within a tier by cost, then confidence and walk."""
    priced = lot["cost"] is not None
    uv = lot.get("_unverified_free")
    if not priced:
        tier = 4 if lot.get("maybe_closed") else 3
    elif uv == "facility":
        tier = 2
    else:
        tier = 1 if lot.get("maybe_closed") else 0
    cost = lot["cost"] if priced else 0.0
    if uv == "plain":
        cost = UNVERIFIED_FREE_RANK_COST + 0.005
    return (0 if lot["fits_stay"] else 1, 1 if lot.get("_far") else 0, tier,
            cost, CONF_RANK.get(lot["confidence"], 9), lot["walk_m"])


def rank(lots, c=None):
    """The options in rank_key order, at most max_options, with at most max_unknown unknown prices, max_not_fitting
    lots that do not fit the stay and max_street street segments; a lot skipped by one cap never uses up another.
    When the list holds no off-street option (a lot, garage, park or park and ride) and one exists, the best
    ranked one is added, in place of the last option when the list is full, so a garage is never crowded out by
    street segments."""
    c = c or cfg()
    lots = sorted(lots, key=rank_key)
    out, unknown, unfit, street, seen = [], 0, 0, 0, set()
    cap = c.get("max_options", 8)
    for lot in lots:
        key = (lot["name"].lower(), lot["cost"])
        if key in seen and lot["kind"] == "street":
            continue
        if lot["cost"] is None and unknown >= c.get("max_unknown", 2):
            continue
        if not lot["fits_stay"] and unfit >= c.get("max_not_fitting", 2):
            continue
        if lot["kind"] == "street" and street >= c.get("max_street", 3):
            continue
        unknown += lot["cost"] is None
        unfit += not lot["fits_stay"]
        street += lot["kind"] == "street"
        seen.add(key)
        out.append(lot)
        if len(out) >= cap:
            break
    if out and all(x["kind"] == "street" for x in out):
        off = next((x for x in lots if x["kind"] != "street"), None)
        if off is not None:
            if len(out) >= cap:
                out.pop()
            out.append(off)
    return out


_RULES = {}   # (name, lat, lon) -> pricing rule of the options returned, for stay_fits()


def _search_text(s):
    return re.sub(r"\s+", " ", re.sub(r"[()\"',;:]", " ", s or "")).strip()


BRAND_RE = re.compile(r"\b(?:impark|indigo|precise(?:\s+parklink)?|target\s+park|easypark|honk|parkade|"
                      r"municipal|public|private|parking|park|lot|garage|car\s*park|underground|surface|visitor|"
                      r"customer|the|and|access|not|tagged|of|destination|[nsew]{1,2})\b", re.I)


def _brand_only(name):
    """True when a name is only an operator brand or generic words ('Impark', 'Indigo parking', 'SP+ garage')."""
    rest = BRAND_RE.sub(" ", re.sub(r"\bsp\+", " ", name or "", flags=re.I))
    return not re.search(r"[a-z0-9]", rest, re.I)


def _join(*bits):
    """Search words from several parts, each part left out when the earlier ones already hold it."""
    out = []
    for b in bits:
        b = _search_text(b)
        if b and b.lower() not in " ".join(out).lower():
            out.append(b)
    return " ".join(out)


def greenp_search(gp):
    """'Green P Carpark 043 2 Church Street St. Lawrence Garage' (the number zero-padded as Green P writes it)."""
    tpl = cfg()["toronto"].get("greenp_search") or "Green P Carpark %03d %s"
    try:
        return tpl % (int(gp["id"]), _search_text(gp["address"]))
    except (TypeError, ValueError):
        return "Green P Carpark %s %s" % (gp["id"], _search_text(gp["address"]))


def check_hint(lot):
    """What to verify before driving to a lot, or None when its price is official or curated and current:
    {"why": "may have closed" | "2019 price" | "price unknown" | "price unknown, opened after 2019" |
    "free per OpenStreetMap, unverified" | "price per OpenStreetMap, unverified" | "old price (...)",
    "url": the operator's own page or None, "search": a web search string or None}. Green P: the url is the
    carpark's page from the 2019 data (43, 283 and the toronto.open pages are live; some 2019 addresses are a 404
    now, so the search is the fallback); the page is rendered with JavaScript (toronto.page_fetch_note). Other
    lots: the search names the lot, its street and city ('<name> <street> Toronto parking rates'); a brand-only
    lot (Impark, Indigo, ...) gets a search only with its street, an unnamed one 'parking near <street> <city>'."""
    gp = lot.get("_greenp")
    if lot.get("maybe_closed"):
        why = "may have closed"
    elif lot.get("cost") is None:
        if str(lot.get("basis") or "").startswith("closed off season"):
            return None
        why = "price unknown, opened after 2019" if gp and gp.get("current") is False else "price unknown"
    elif gp and not gp.get("current"):
        why = "2019 price"
    elif lot.get("unverified"):
        why = "free per OpenStreetMap, unverified"
    elif lot.get("confidence") == "stale":
        why = lot.get("_stale_text") or "old price"
    elif lot.get("_old_rate"):
        why = lot["_old_rate"]
    elif lot.get("confidence") == "osm":
        why = "price per OpenStreetMap, unverified"
    else:
        return None
    url = lot.get("_website")
    if not url and lot.get("confidence") in ("official", "curated") and re.match(r"https?://", lot.get("source_url") or ""):
        url = lot["source_url"]
    addr, city = lot.get("_addr") or "", lot.get("_city") or ""
    if gp:
        if not url and re.match(r"https?://parking\.greenp\.com/carpark/", lot.get("source_url") or ""):
            url = lot["source_url"]
        search = greenp_search(gp)
    elif lot.get("_generic_name"):
        # an unnamed OSM lot: the map link, and a search by its street when OSM has one
        url = url or lot.get("source_url")
        street = lot.get("_street") or addr
        search = "parking near %s rates" % _join(street, city) if street else None
    elif _brand_only(lot.get("name")):
        url = url or lot.get("source_url")
        search = "%s parking rates" % _join(lot.get("name"), addr, city) if addr else None
    else:
        search = "%s parking rates" % _join(lot.get("name"), addr, city)
    return {"why": why, "url": url or None, "search": search}


def check_list(options, top=5, limit=3):
    """The first `limit` of the first `top` options that need checking: [{"n": 1-based rank, "name", "why",
    "url", "search"}], in rank order. Past the limit it still names #1 (the arrival target) and the first lot
    that may have closed, so neither is left unchecked."""
    options = options or []
    hints = [dict(n=i, name=o.get("name"), **o["check"]) for i, o in enumerate(options[:top], 1) if o.get("check")]
    out = hints[:limit]
    must = [h for h in hints if h["n"] == 1][:1]
    must += [h for h in hints if options[h["n"] - 1].get("maybe_closed")][:1]
    for h in must:
        if h not in out:
            out.append(h)
    return sorted(out, key=lambda h: h["n"])


def check_text(items):
    """One line for the text output: '#1 Green P ...: 2019 price, search "..."; #3 ...: price unknown, https://...'."""
    out = []
    for c in items or []:
        how = c.get("url") or ('search "%s"' % c["search"] if c.get("search") else "")
        if c.get("url") and c.get("search"):
            how = '%s or search "%s"' % (c["url"], c["search"])
        out.append("#%d %s: %s%s" % (c["n"], c.get("name") or "parking", c["why"], ", " + how if how else ""))
    return "; ".join(out)


def _public(lot):
    out = {k: v for k, v in lot.items() if not k.startswith("_")}
    out["check"] = check_hint(lot)
    if lot.get("_greenp"):
        out["greenp_id"] = lot["_greenp"]["id"]
    if lot.get("_far"):
        out["beyond_walk"] = True   # a curated park, shuttle or free lot listed past the walk radius
    _RULES[(out.get("name"), out.get("lat"), out.get("lon"))] = lot.get("_rule")
    return out


def stay_fits(option, arrive, stay_min, holidays=None):
    """Whether a returned option holds the car from `arrive` for stay_min minutes under its own rules (a time
    limit tied to the paid hours does not bind in free hours). None when the rule is not known."""
    key = (option.get("name"), option.get("lat"), option.get("lon"))
    if key not in _RULES:
        return None
    rule = _RULES[key]
    if not rule:
        ms = option.get("max_stay_min")
        return not (ms and stay_min > ms)
    try:
        return bool(price_rule(rule, arrive, stay_min, holidays)[2])
    except Exception:  # noqa: BLE001
        return None


def find_parking(lat, lon, arrive, stay_min, opts, warnings):
    """Cheapest parking near (lat, lon) for a stay from `arrive` (naive local datetime) for stay_min minutes.
    opts: "max_walk_m" (default 800; widen to 1500 when nothing free or priced is found, and say so),
    "height_m" (vehicle height or None), "holidays" (set of 'YYYY-MM-DD'), "region" (region dict or None),
    "party" ({"adults", "children", "seniors"} or None: prices per-person admission for the party),
    "offline" (True: the curated files only, no network sources).
    Returns {"options": [lot, ...] (at most 8, ranked), "place_rules": [str] (season, reservations, free
    fallback lots of a curated place), "reservations": [{"name", "text", "url", "lead_days"}], "sources": [str],
    "radius_m": int, "caveat": "Posted signs override this data: time limits, rush-hour no-stopping, permit zones."}
    A lot's "per_person" flag marks admission priced per person (one adult unless the party is given),
    "unverified" marks an OpenStreetMap fee=no lot that no official source confirms, "maybe_closed" a Green P
    2019 carpark missing from the current occupancy data and not in toronto.open (ranked after the other priced
    lots, ahead of facility lots and unknown prices; see rank_key), "greenp_id" the Green P carpark number, and
    "check" what to verify before going (check_hint); the top-level "check" lists the first three of the top five
    that need it, plus #1 and the first lot that may have closed when they fall past three (check_list).
    The search widens to 1500 m when no walkable lot is priced, fits the stay and is not flagged."""
    opts = opts or {}
    c = cfg()
    holidays = set(opts.get("holidays") or ())
    max_walk = int(opts.get("max_walk_m") or c.get("default_max_walk_m", 800))
    wide = max(int(c.get("widen_walk_m", 1500)), max_walk)
    height = opts.get("height_m")
    party = opts.get("party")
    # one fetch at the wide radius (straight line); filter by walking distance afterwards
    fetch_r = int(wide / c.get("walk_factor", 1.3)) + 100
    all_lots, sources, place_rules, reservations = [], [], [], []
    lots, rules, res, srcs = curated(lat, lon, arrive, stay_min, holidays, fetch_r, warnings, party=party)
    all_lots += lots
    place_rules += rules
    reservations += res
    sources += srcs
    for fn in () if opts.get("offline") else (city_lots, toronto_lots, osm_lots):   # offline: curated files only
        try:
            extra = {"country": opts.get("country")} if fn is osm_lots and opts.get("country") else {}
            lots, srcs = fn(lat, lon, arrive, stay_min, holidays, fetch_r, height, warnings, **extra)
        except Exception as e:  # noqa: BLE001 (a parser bug must not sink the plan)
            warnings.append("parking source %s failed (%s)" % (fn.__name__, e))
            log("parking: %s failed: %r" % (fn.__name__, e))
            lots, srcs = [], []
        all_lots += lots
        sources += srcs
    always = ("park", "park_ride", "resort")

    def within(r):
        out = [x for x in all_lots if x["walk_m"] <= r or (x["kind"] in always and x["source"].endswith("(curated)"))]
        for x in out:   # curated park, shuttle and resort lots past the walk rank after the walkable ones
            x["_far"] = x["walk_m"] > r
        return out

    def usable(x):
        return x["cost"] is not None and x["fits_stay"] and not x.get("maybe_closed")

    radius = max_walk
    pool = dedupe(within(radius), c.get("dedupe_m", 60))
    if not any(usable(x) for x in pool) and wide > max_walk:
        flagged = any(x["cost"] is not None and x["fits_stay"] and x.get("maybe_closed") for x in pool)
        radius = wide
        pool = dedupe(within(radius), c.get("dedupe_m", 60))   # the flagged walkable lots stay in the pool
        if flagged:
            warnings.append("priced lots within %d m may have closed; widened the search to %d m" % (max_walk, wide))
        elif any(x["cost"] is not None for x in pool):
            warnings.append("no free or priced parking within %d m walk; widened the search to %d m" % (max_walk, wide))
        else:
            warnings.append("no parking with a known price within %d m walk; check signs on arrival" % wide)
    else:
        # a curated free lot a little past the walk limit is worth listing when the walkable ones are expensive
        fit = [x["cost"] for x in pool if usable(x) and not x.get("_far")]
        cheapest = min(fit) if fit else None
        saving_min = float(c.get("free_alt_min_saving", 10.0))
        if cheapest is not None and cheapest > saving_min and not any(x["cost"] == 0 and not x.get("_unverified_free")
                                                                      and not x.get("_far") for x in pool):
            have = set(id(x) for x in pool)
            extra = []
            for x in sorted(all_lots, key=lambda y: y["walk_m"]):
                if id(x) in have or not x["source"].endswith("(curated)") or x["cost"] != 0 or not x["fits_stay"] \
                        or not (max_walk < x["walk_m"] <= wide):
                    continue
                x["_far"] = True
                pool.append(x)
                extra.append(x)
            if extra:
                warnings.append("free parking a longer walk away saves %s over the cheapest closer option: %s" % (
                    dc.money(cheapest), ", ".join("%s (%d min, %d m)" % (x["name"], x["walk_min"], x["walk_m"]) for x in extra[:3])))
    options = [_public(x) for x in rank(pool, c)]
    seen = set()
    reservations = [r for r in reservations if not (r["name"], r["text"]) in seen and not seen.add((r["name"], r["text"]))]
    return {"options": options, "place_rules": place_rules, "reservations": reservations,
            "sources": sources, "radius_m": radius, "caveat": c.get("caveat") or CAVEAT, "check": check_list(options)}


def main(argv=None):
    """Smoke test: parking.py LAT LON "YYYY-MM-DD HH:MM" STAY_H [MAX_WALK_M]."""
    import sys
    a = argv if argv is not None else sys.argv[1:]
    if len(a) < 4:
        raise SystemExit('usage: parking.py LAT LON "YYYY-MM-DD HH:MM" STAY_HOURS [MAX_WALK_M]')
    warnings = []
    arrive = dt.datetime.strptime(a[2], "%Y-%m-%d %H:%M")
    res = find_parking(float(a[0]), float(a[1]), arrive, float(a[3]) * 60, {
        "max_walk_m": int(a[4]) if len(a) > 4 else 800, "holidays": dc.holidays_for()}, warnings)
    print("PARKING    %s stay from %s, radius %d m" % (dur_text(float(a[3]) * 60), arrive.strftime("%a %Y-%m-%d %H:%M"), res["radius_m"]))
    for r in res["place_rules"]:
        print("  rule: %s" % r)
    for r in res["reservations"]:
        print("  reserve: %s: %s %s" % (r["name"], r["text"], r["url"]))
    for i, o in enumerate(res["options"], 1):
        print("  %d. %s: %s, %d m walk (%s, %s)%s%s" % (i, o["name"], o["cost_text"], o["walk_m"], o["confidence"], o["source"],
                                                     "" if o["fits_stay"] else ", DOES NOT FIT THE STAY",
                                                     ", often full" if o["often_full"] else ""))
        print("     %s%s" % (o["basis"], ("; " + o["notes"]) if o["notes"] else ""))
    if res.get("check"):
        print("     check: " + check_text(res["check"]))
    print("     " + res["caveat"])
    for w in warnings:
        print("WARNING    " + w)
    print("SOURCES    " + "; ".join(res["sources"]))


if __name__ == "__main__":
    main()
