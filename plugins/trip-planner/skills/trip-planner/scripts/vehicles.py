"""Look a car up in Natural Resources Canada's Fuel Consumption Ratings, and keep a saved car profile.

Standard library only. Python 3.8+.

    lookup("2022 Toyota Corolla", warnings)  -> dict or None
    profile_path() / load_profile() / save_profile(car) / forget_profile()

Lookups use the bundled index references/driving/vehicles-index.json (model years 2010 to 2026, built
from the NRCan CSVs on open.canada.ca), so they work offline. With allow_download=True a year or model the
index lacks is fetched from the live NRCan CSV (cached 30 days through drive_common.fetch).

Index row layout (per year, per make): [model, engine_l, fuel, kind, city, hwy, comb, class_idx, n, *extra]
  fuel  X regular, Z premium, D diesel (L/100 km); B electricity (kWh/100 km)
  kind  g gas, h hybrid, d diesel, p plug-in hybrid (gasoline-mode figures), e battery electric
  n     NRCan rows with these exact figures (transmissions folded together)
  extra e: [range_km]; p: [electric kWh/100 km, electric range km]
"""
from __future__ import annotations

import csv
import datetime as dt
import difflib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.normpath(os.path.join(HERE, "..", "references", "driving", "vehicles-index.json"))

DATASET_ID = "98f1a129-f628-4ce4-b24d-6f16bf24dd64"
DATASET_URL = "https://open.canada.ca/data/en/dataset/" + DATASET_ID
PACKAGE_API = "https://open.canada.ca/data/api/action/package_show?id=" + DATASET_ID
# The download identifies itself with the skill's own User-Agent (trip_hubs.USER_AGENT), never a browser
# one. open.canada.ca redirects (302) each CSV to a signed opencanada.blob.core.windows.net URL that urllib
# follows. If the site's firewall refuses the request, the lookup falls back to the bundled index with a
# warning; it never retries in disguise.
DOWNLOAD_HOSTS = ("open.canada.ca", "opencanada.blob.core.windows.net")
DOWNLOAD_HEADERS = {
    "Accept": "text/csv,application/json;q=0.9,text/html;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-CA,en;q=0.9",
}
CACHE_HOURS = 720.0   # 30 days

FUEL_NAME = {"X": "regular", "Z": "premium", "D": "diesel", "B": "electricity"}
KIND_NAME = {"g": "gas", "h": "hybrid", "d": "diesel", "p": "phev", "e": "ev"}
KIND_CODE = dict((v, k) for k, v in KIND_NAME.items())
KIND_TEXT = {"gas": "gas", "hybrid": "hybrid", "diesel": "diesel", "phev": "plug-in hybrid", "ev": "electric"}
DEFAULT_KIND_ORDER = "ghdpe"   # with no kind in the text: plain gas first, then hybrid, diesel, PHEV, EV

MAKE_ALIASES = {
    "chevy": "chevrolet", "chev": "chevrolet", "vw": "volkswagen", "volks": "volkswagen",
    "mercedes": "mercedesbenz", "benz": "mercedesbenz", "merc": "mercedesbenz", "mb": "mercedesbenz",
    "landrover": "landrover", "rangerover": None, "alfa": "alfaromeo", "rolls": "rollsroyce",
    "aston": "astonmartin", "infinity": "infiniti", "hyundia": "hyundai", "toyata": "toyota",
    "mitsu": "mitsubishi", "caddy": "cadillac", "lambo": "lamborghini", "porshe": "porsche",
    "subie": "subaru", "dodgeram": "ram",
}
MODEL_ALIASES = {"f150": "f150", "f250": "f250superduty", "crv": "crv", "hrv": "hrv", "rav": "rav4",
                 "3series": None, "civicsi": "civicsi", "roguesport": "qashqai"}
# aliases that are another name for the car, not a spelling: the lookup says which name it used
MODEL_RENAMES = {"roguesport": ("Rogue Sport", "Qashqai")}
# Ford EcoBoost engine sizes (turbo); 'ecoboost' keeps these and drops the V8s and the naturally aspirated V6s
ECOBOOST_L = (1.0, 1.5, 1.6, 2.0, 2.3, 2.7, 3.0, 3.5)
# generation codes inside a model name ('Wrangler JK Unlimited', 'Grand Cherokee WK'): skipped in matching
GEN_CODES = {"jk", "jl", "wk", "wk2"}
# performance trims that are separate cars even when a drive word comes first ('Grand Cherokee 4X4 SRT')
PERF_WORDS = {"srt", "srt8", "trackhawk", "hellcat", "trx", "shelby", "raptor"}
# words that make a separate model wherever they sit after the name ('Cooper SE Countryman ALL4')
SEPARATE_WORDS = PERF_WORDS | {"clubman", "countryman", "paceman"}
# a designator such as '300e' or '450h': when no rating carries it, the car is not in the ratings
DESIGNATOR = re.compile(r"^\d{2,3}[a-z]{1,2}$")

DRIVE_WORDS = {"awd", "4wd", "4x4", "quattro", "4motion", "xdrive", "4matic", "4matic+", "all4", "e4orce",
               "shawd", "awdi", "allwheel", "4x4i"}
TWO_WD_WORDS = {"2wd", "fwd", "rwd", "4x2", "sdrive"}
# electric trims that are dual or tri motor (all-wheel drive) without saying so ('Model 3 Performance',
# 'Mustang Mach-E GT Performance Edition')
EV_AWD_WORDS = {"performance", "gt", "rally", "plaid"}
KIND_WORDS = {"hybrid": "hybrid", "hev": "hybrid", "hybride": "hybrid",
              "plugin": "phev", "phev": "phev", "prime": "phev", "energi": "phev", "4xe": "phev",
              "ehybrid": "phev",
              "electric": "ev", "ev": "ev", "bev": "ev", "electrique": "ev",
              "diesel": "diesel", "tdi": "diesel", "duramax": "diesel", "ecodiesel": "diesel",
              "powerstroke": "diesel", "bluetec": "diesel", "crd": "diesel",
              "powerboost": "hybrid", "iforcemax": "hybrid",
              "gas": "gas", "gasoline": "gas", "petrol": "gas"}
# Words that end a model's family name: drive, powertrain, body and trim words. "Corolla Hybrid AWD" and
# "Corolla Hatchback" belong to the Corolla family; "Corolla Cross" does not.
VARIANT_WORDS = DRIVE_WORDS | TWO_WD_WORDS | {
    "hybrid", "hev", "plugin", "phev", "electric", "ev", "prime", "energi", "ehybrid", "4xe", "recharge",
    "ffv", "diesel", "tdi", "turbo", "ecoboost", "v6", "v8", "v12", "sil", "stopstart",
    "sedan", "hatchback", "hatch", "coupe", "convertible", "cabriolet", "cabrio", "roadster", "wagon",
    "2door", "3door", "4door", "5door", "2dr", "4dr", "spyder", "spider", "targa", "crew", "cab", "cargo",
    "passenger", "van", "chassis", "ext", "extended", "long", "range", "standard", "std", "swb", "lwb",
    "le", "se", "xle", "xse", "sel", "sle", "slt", "lt", "ls", "ltz", "lx", "ex", "exl", "touring",
    "limited", "platinum", "premium", "base", "trd", "pro", "offroad", "lariat", "xlt", "denali", "at4",
    "z71", "rst", "wt", "sr", "sr5", "sv", "sl", "sx", "gt", "gts", "gtline", "rs", "ss", "nismo",
    "performance", "plaid", "luxury", "preferred", "essential", "ultimate", "edition", "woodland",
    "trailsport", "trailhawk", "rubicon", "sahara", "laredo", "overland", "summit", "import", "i",
    "mud", "terrain", "tire", "tires", "wheels", "wheel", "package", "pack", "dual", "single", "motor",
    "tri", "quad", "awdpro", "with", "w", "cylinder", "deactivation", "mode", "1mode", "2mode", "3mode",
}
# trim grades only: the same car under the plain name ('Corolla LE' is a Corolla); body, powertrain and
# performance words are not ('A5 Cabriolet', 'Vantage GT')
GRADE_WORDS = {"le", "se", "xle", "xse", "sel", "sle", "slt", "lt", "ls", "ltz", "lx", "ex", "exl", "touring",
               "limited", "platinum", "premium", "base", "luxury", "preferred", "essential", "ultimate", "sr5",
               "sv", "sl", "sx", "lariat", "xlt", "edition"}
STOP_WORDS = {"my", "a", "an", "the", "car", "is", "i", "drive", "have", "own", "year", "with",
              "and", "of", "in", "vehicle", "truck", "suv", "it", "its", "our", "use", "using", "litre",
              "liter", "engine", "cyl", "trim", "version", "new", "used", "old", "edition", "-", "inch", "inches"}
# A word after the name that makes another model, not a trim: 'Corolla Cross', 'Civic Type R',
# 'F-150 Raptor', 'Mustang Mach-E'. Single letters or digits ('Elantra N', 'Prius c', 'IONIQ 5') too.
SUBMODEL_WORDS = {"cross", "type", "raptor", "lightning", "mache", "mach", "sport", "evoque", "velar", "signia",
                  "im", "ia", "xl", "crosstour", "sportwagen", "alltrack", "gti", "wrx", "sti", "evolution",
                  "connect", "trx", "hellcat", "srt", "srt8", "shelby", "classic", "hd", "superduty",
                  "solara", "cabrio", "gr", "nitro", "trackhawk", "clubman", "countryman", "paceman"}
BMW_SERIES = re.compile(r"^([1-8])series$")

CLASS_TO_CAR_CLASS = [
    ("pickup", "pickup"), ("sport utility vehicle: small", "small-suv"),
    ("sport utility vehicle: standard", "suv"), ("minivan", "suv"), ("van", "suv"),
    ("special purpose", "suv"), ("full-size", "midsize"), ("mid-size", "midsize"),
    ("station wagon: mid-size", "midsize"), ("station wagon", "compact"), ("compact", "compact"),
    ("subcompact", "compact"), ("minicompact", "compact"), ("two-seater", "compact"),
]

_INDEX = None
_SEARCH = None
_TRIED_DOWNLOAD = set()


# --------------------------------------------------------------------------- text helpers

def _norm_token(tok):
    return re.sub(r"[.\-'\u2019_]", "", tok.lower())


def _make_key(make):
    """Lookup key of a make: 'Alfa Romeo' -> 'alfaromeo', 'Mercedes-Benz' -> 'mercedesbenz'."""
    return _norm_token(make).replace(" ", "")


def tokens(text):
    """Model or query text -> normalized tokens: lower case, hyphens, dots and apostrophes dropped,
    split on spaces, slashes, commas and brackets. 'F-150' -> 'f150', 'CR-V' -> 'crv', 'ID.4' -> 'id4'."""
    out = []
    for raw in re.split(r"[\s/,;:()\[\]\"]+", text or ""):
        t = _norm_token(raw)
        if t:
            out.append(t)
    return out


def family_tokens(model):
    """The family part of an NRCan model name: tokens up to the first drive, body, trim or powertrain
    word or bracket. 'RAV4 Hybrid AWD' -> ['rav4'], 'Corolla Cross AWD' -> ['corolla', 'cross']."""
    fam = []
    for raw in re.split(r"[\s/,]+", model.strip()):
        if not raw:
            continue
        if raw.startswith("(") or raw.startswith("["):
            break
        t = _norm_token(raw)
        if not t:
            continue
        if fam and (t in VARIANT_WORDS or (raw.isupper() and raw.isalpha() and 2 <= len(raw) <= 3
                                           and not t.isdigit())):
            break
        fam.append(t)
    return fam or tokens(model)[:1]


def family_display(model):
    """Original spelling of the family part ('IONIQ 5', 'F-150', 'Model Y')."""
    n = len(family_tokens(model))
    words, out = [w for w in re.split(r"\s+", model.strip()) if w], []
    for w in words:
        if len(out) >= n:
            break
        if _norm_token(w):
            out.append(w)
    return " ".join(out) or model.strip()


def car_class(nrcan_class, comb=None):
    """NRCan vehicle class -> costs.json consumption class key."""
    c = (nrcan_class or "").lower()
    if c == "sport utility vehicle":   # pre-2012 rows do not split small and standard
        return "small-suv" if comb is not None and comb < 10.5 else "suv"
    for k, v in CLASS_TO_CAR_CLASS:
        if c.startswith(k):
            return v
    return None


def _wmedian(pairs):
    """Median of (value, weight) pairs, weights as repeat counts."""
    vals = []
    for v, w in pairs:
        if v is not None:
            vals.extend([v] * max(1, int(w)))
    if not vals:
        return None
    vals.sort()
    m = len(vals) // 2
    x = vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2.0
    return round(x, 1)


# --------------------------------------------------------------------------- NRCan CSV parsing

def decode_csv(raw):
    """NRCan CSV bytes -> text. Most files are UTF-8 (some with a BOM); MY2025 is Windows-1252
    ('Coup\\xe9'), so fall back to cp1252."""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _num(s):
    try:
        return float(str(s).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


LEXUS_H = re.compile(r"\b\d{3}h\b", re.I)


def detect_kind(make, model, fuel, trans, city, hwy, year):
    """Kind code for a row of the conventional-fuel files: d diesel, h hybrid, g gas.
    NRCan has no hybrid flag. A row is a hybrid when its name says so ('Hybrid', Lexus '350h'), when the
    model line is hybrid-only (Prius, Insight, CR-Z, Ioniq, Niro, Sienna 2021+, Venza 2021+, Crown,
    Camry 2025+, RAV4 2026+, Sequoia 2023+, Land Cruiser 2024+), or when it has a CVT and rates no worse in
    the city than on the highway, which only a full hybrid does (2026 Honda Prelude)."""
    if fuel == "D":
        return "d"
    toks = tokens(model)
    mk = _make_key(make)
    if "hybrid" in toks or "hev" in toks or LEXUS_H.search(model):
        return "h"
    first = toks[0] if toks else ""
    if first in ("prius", "insight", "ioniq", "crz") or (mk == "kia" and first == "niro"):
        return "h"
    if mk == "toyota":
        if (first == "sienna" and year >= 2021) or (first == "venza" and year >= 2021) or first == "crown" \
                or (first == "camry" and year >= 2025) or (first == "rav4" and year >= 2026) \
                or (first == "sequoia" and year >= 2023) \
                or (toks[:2] == ["land", "cruiser"] and year >= 2024):
            return "h"
    if city is not None and hwy and (trans or "").upper().startswith("AV") and city <= hwy and city < 9.0:
        return "h"
    return "g"


class LayoutError(ValueError):
    """An NRCan CSV whose header this version cannot read (a renamed or missing column)."""


def parse_csv(raw, min_year=None, max_year=None):
    """One NRCan ratings CSV (conventional, battery-electric or plug-in hybrid layout) -> list of dicts
    {year, make, model, cls, engine, fuel, kind, city, hwy, comb, range_km, e_kwh, e_range}.
    E85 (E) rows of flex-fuel cars and natural-gas (N) rows are dropped: the same car has a gasoline row.
    Raises LayoutError when the header lacks a column the figures need."""
    text = decode_csv(raw)
    rows = list(csv.reader(io.StringIO(text)))
    hi = None
    for i, r in enumerate(rows[:20]):
        low = [c.strip().lower() for c in r]
        if "make" in low and "model" in low:
            hi = i
            break
    if hi is None:
        raise LayoutError("no header row with Make and Model columns")
    head = [c.strip().lower() for c in rows[hi]]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        for n in names:
            for i, h in enumerate(head):
                if h.startswith(n):
                    return i
        return None

    c_year, c_make, c_model, c_cls = col("model year"), col("make"), col("model"), col("vehicle class")
    c_eng, c_trans = col("engine size (l)"), col("transmission")
    if col("fuel type 2") is not None:
        layout = "phev"
        c_fuel = col("fuel type 2")
        c_city, c_hwy, c_comb = col("city (l/100 km)"), col("highway (l/100 km)"), col("combined (l/100 km)")
        c_le, c_r1 = col("combined le/100 km"), col("range 1 (km)")
    elif col("city (kwh/100 km)") is not None:
        layout = "bev"
        c_fuel = col("fuel type")
        c_city, c_hwy, c_comb = col("city (kwh/100 km)"), col("highway (kwh/100 km)"), \
            col("combined (kwh/100 km)")
        c_rng = col("range (km)")
    else:
        layout = "gas"
        c_fuel = col("fuel type")
        c_city, c_hwy, c_comb = col("city (l/100 km)"), col("highway (l/100 km)"), col("combined (l/100 km)")
    need = (("Model year", c_year), ("Make", c_make), ("Model", c_model), ("City", c_city), ("Highway", c_hwy),
            ("Combined", c_comb))
    missing = [n for n, c in need if c is None]
    if missing:
        raise LayoutError("no %s column in the %s layout" % (", ".join(missing), layout))
    out = []
    for r in rows[hi + 1:]:
        if len(r) <= max(c_year, c_make, c_model, c_city, c_hwy, c_comb):
            continue
        try:
            year = int(r[c_year].strip())
        except ValueError:
            continue   # footnotes and blank lines
        if (min_year and year < min_year) or (max_year and year > max_year):
            continue
        make = re.sub(r"\s+", " ", r[c_make]).strip()
        model = re.sub(r"\s+", " ", r[c_model]).strip()
        model = re.sub(r"-I$", " (Import)", model)          # Tesla 'Model Y Long Range-I'
        model = re.sub(r"\bAWD-I\b", "AWD (Import)", model)
        cls = (r[c_cls].strip() if c_cls is not None else "")
        city, hwy, comb = _num(r[c_city]), _num(r[c_hwy]), _num(r[c_comb])
        if comb is None:
            continue
        fuel = (r[c_fuel].strip().upper() if c_fuel is not None else "")[:1]
        d = {"year": year, "make": make, "model": model, "cls": cls, "city": city, "hwy": hwy, "comb": comb,
             "engine": _num(r[c_eng]) if c_eng is not None else None, "range_km": None, "e_kwh": None,
             "e_range": None}
        if layout == "bev":
            d.update(fuel="B", kind="e", engine=None)
            d["range_km"] = _num(r[c_rng]) if c_rng is not None else None
        elif layout == "phev":
            if fuel not in ("X", "Z", "D"):
                fuel = "X"
            d.update(fuel=fuel, kind="p")
            if c_le is not None:
                m = re.search(r"([\d.]+)\s*kWh", r[c_le])
                d["e_kwh"] = float(m.group(1)) if m else None
            d["e_range"] = _num(r[c_r1]) if c_r1 is not None else None
        else:
            if fuel not in ("X", "Z", "D"):
                continue
            trans = r[c_trans].strip() if c_trans is not None else ""
            d.update(fuel=fuel, kind=detect_kind(make, model, fuel, trans, city, hwy, year))
        out.append(d)
    return out


def compact_rows(rows, classes):
    """Parsed rows -> {year: {make: [index rows]}}, folding rows with identical figures (usually the
    same car with another transmission) into one with a count. `classes` is extended in place."""
    out, seen = {}, {}
    for d in rows:
        if d["cls"] not in classes:
            classes.append(d["cls"])
        ci = classes.index(d["cls"])
        key = (d["year"], d["make"], d["model"], d["engine"], d["fuel"], d["kind"], d["city"], d["hwy"],
               d["comb"], ci, d["range_km"], d["e_kwh"], d["e_range"])
        if key in seen:
            seen[key][8] += 1
            continue
        eng = d["engine"]
        row = [d["model"], eng, d["fuel"], d["kind"], d["city"], d["hwy"], d["comb"], ci, 1]
        if d["kind"] == "e":
            row.append(int(d["range_km"]) if d["range_km"] else None)
        elif d["kind"] == "p":
            row += [d["e_kwh"], int(d["e_range"]) if d["e_range"] else None]
        seen[key] = row
        out.setdefault(str(d["year"]), {}).setdefault(d["make"], []).append(row)
    return out


# --------------------------------------------------------------------------- index and search structures

def set_index(data):
    """Use `data` (an index dict, or a path to one) instead of the bundled file; None reloads the bundle.
    Tests use this to run on fixture rows."""
    global _INDEX, _SEARCH
    if isinstance(data, str):
        with open(data, encoding="utf-8") as f:
            data = json.load(f)
    _INDEX = data
    _SEARCH = None
    _TRIED_DOWNLOAD.clear()


def load_index():
    global _INDEX
    if _INDEX is None:
        with open(INDEX_PATH, encoding="utf-8") as f:
            _INDEX = json.load(f)
    return _INDEX


class _Row(object):
    __slots__ = ("year", "make", "model", "engine", "fuel", "kind", "city", "hwy", "comb", "cls", "n",
                 "range_km", "e_kwh", "e_range", "toks", "core", "fam", "famkey", "drive")

    def __init__(self, year, make, r, classes):
        self.year, self.make = year, make
        self.model, self.engine, self.fuel, self.kind = r[0], r[1], r[2], r[3]
        self.city, self.hwy, self.comb = r[4], r[5], r[6]
        self.cls = classes[r[7]] if isinstance(r[7], int) and r[7] < len(classes) else str(r[7])
        self.n = r[8] if len(r) > 8 else 1
        self.range_km = r[9] if self.kind == "e" and len(r) > 9 else None
        self.e_kwh = r[9] if self.kind == "p" and len(r) > 9 else None
        self.e_range = r[10] if self.kind == "p" and len(r) > 10 else None
        self.toks = tokens(self.model)
        self.core = tokens(re.split(r"[(\[]", self.model, maxsplit=1)[0])   # the name less any bracket ('(19" Wheels)')
        self.fam = family_tokens(self.model)
        self.famkey = "".join(self.fam)
        t = set(self.toks)
        # None: the name gives no drive ('Model 3 RWD' is 2wd, 'Model 3 Performance' awd, 'Civic' None)
        self.drive = "awd" if t & DRIVE_WORDS or (self.kind == "e" and t & EV_AWD_WORDS) else \
            "2wd" if t & TWO_WD_WORDS else None


def _search():
    """Rows plus lookup tables built from the index once per process."""
    global _SEARCH
    if _SEARCH is not None:
        return _SEARCH
    idx = load_index()
    classes = idx.get("classes") or []
    rows, makes, prefix = [], {}, {}
    for year, by_make in (idx.get("rows") or {}).items():
        for make, lst in by_make.items():
            mk = _make_key(make)
            makes.setdefault(mk, make)
            for r in lst:
                row = _Row(int(year), make, r, classes)
                i = len(rows)
                rows.append(row)
                acc = ""
                for t in row.toks:
                    acc += t
                    prefix.setdefault((mk, acc), []).append(i)
                # also without generation codes: 'Wrangler JK Unlimited' answers 'wranglerunlimited'
                if any(t in GEN_CODES for t in row.toks[1:]):
                    acc = ""
                    for j, t in enumerate(row.toks):
                        if j and t in GEN_CODES:
                            continue
                        acc += t
                        lst2 = prefix.setdefault((mk, acc), [])
                        if not lst2 or lst2[-1] != i:
                            lst2.append(i)
    fams = {}
    for i, row in enumerate(rows):
        fams.setdefault(row.famkey, set()).add(_make_key(row.make))
    _SEARCH = {"rows": rows, "makes": makes, "prefix": prefix, "families": fams,
               "accs": set(k[1] for k in prefix), "years": sorted(set(r.year for r in rows))}
    return _SEARCH


# --------------------------------------------------------------------------- query parsing

def parse_query(text):
    """Free text -> {year, engine, kind, drive, make_key, model_tokens, raw_tokens}."""
    s = _search()
    q = {"year": None, "engine": None, "kind": None, "drive": None, "make": None, "model": [], "text": text}
    t = (text or "").lower().replace("\u2013", " ").replace("\u2014", " ")
    t = re.sub(r"['\u2019]s\b", "", t)   # "Toyota's"
    t = re.sub(r"\bplug[\s-]+in\b", "plugin", t)
    t = re.sub(r"\b(all|four)[\s-]+wheel([\s-]+drive)?\b", "awd", t)
    t = re.sub(r"\b(front|rear|two)[\s-]+wheel([\s-]+drive)?\b", "2wd", t)
    t = re.sub(r"\bpower[\s-]+stroke\b", "powerstroke", t)
    t = re.sub(r"\bpower[\s-]+boost\b", "powerboost", t)
    t = re.sub(r"\bi[\s-]*force[\s-]+max\b", "iforcemax", t)   # the hybrid; plain 'i-FORCE' is the gas engine
    t = re.sub(r"\b(\d)\s*(series)\b", r"\1series", t)
    raw = [w for w in re.split(r"[\s,;:()\[\]\"/]+", t) if w]
    rest = []
    year4 = [w for w in raw if re.match(r"^(19[89]\d|20[0-4]\d)$", w)]
    after_engine = False
    for w in raw:
        if q["year"] is None and year4 and w == year4[0]:
            q["year"] = int(w)
            after_engine = False
            continue
        m = re.match(r"^(\d\.\d)\s*[lt]?$", w)   # '3.6L', '3.6', '2.0T'
        if m and q["engine"] is None:
            q["engine"] = float(m.group(1))
            after_engine = True
            continue
        if w == "l" and after_engine:   # '3.6 L'
            after_engine = False
            continue
        after_engine = False
        m = re.match(r"^'(\d\d)$", w)
        if m and q["year"] is None:
            q["year"] = 2000 + int(m.group(1))
            continue
        rest.append(w)
    toks = [_norm_token(w) for w in rest]
    toks = [x for x in toks if x]
    # a bare 2-digit year ('19 civic') when no 4-digit one was given, unless it ends a model name ('CX 30')
    if q["year"] is None:
        for i, tk in enumerate(toks):
            if re.match(r"^\d\d$", tk) and 10 <= int(tk) <= 30:
                if i and (toks[i - 1] + tk) in s["accs"]:
                    continue
                q["year"] = 2000 + int(tk)
                q["year2"] = tk
                del toks[i]
                break
    # make: two-word makes first, then single words and aliases
    makes = s["makes"]
    i, out = 0, []
    while i < len(toks):
        tk = toks[i]
        two = tk + toks[i + 1] if i + 1 < len(toks) else None
        if q["make"] is None and two and (two in makes or MAKE_ALIASES.get(two)):
            q["make"] = two if two in makes else MAKE_ALIASES[two]
            i += 2
            continue
        if q["make"] is None and (tk in makes or MAKE_ALIASES.get(tk)):
            q["make"] = tk if tk in makes else MAKE_ALIASES[tk]
            i += 1
            continue
        out.append(tk)
        i += 1
    model = []
    for j, tk in enumerate(out):
        if tk == "ecoboost":
            q["ecoboost"] = True
            continue
        if tk == "new" and j + 1 < len(out) and ("new" + out[j + 1]) in s["accs"]:
            model.append(tk)   # 'New Wrangler' is NRCan's name for the 2018 JL; elsewhere 'new' is just a word
            continue
        if tk in KIND_WORDS and not (tk == "prime" and not model):
            q["kind"] = q["kind"] if q["kind"] == "phev" else KIND_WORDS[tk]
            continue
        if tk in DRIVE_WORDS:
            q["drive"] = "awd"
            q["drive_word"] = "4WD" if tk in ("4wd", "4x4") else "AWD"
            continue
        if tk in TWO_WD_WORDS:
            q["drive"] = "2wd"
            q["drive_word"] = "RWD" if tk == "rwd" else "FWD" if tk == "fwd" else "2WD"
            q["drive_tok"] = tk
            continue
        if tk in STOP_WORDS:
            continue
        model.append(tk)
    q["model"] = model
    return q


# --------------------------------------------------------------------------- matching

def _rows_for_key(key, make):
    s = _search()
    if make:
        return list(s["prefix"].get((make, key), []))
    out = []
    for mk in s["makes"]:
        out.extend(s["prefix"].get((mk, key), []))
    return out


def _match_model(q, warnings):
    """Row ids whose model matches the query's model tokens, how they matched, and leftover tokens.
    Tiers: longest token prefix (exact family preferred), make + model glued ('Mazda 3' -> Mazda3), all
    tokens contained anywhere, then a close spelling. A make that has no such model is dropped and the
    search repeated across makes ('Dodge Ram 1500' -> Ram 1500)."""
    ids, how, left = _match_model_in(q, warnings)
    s = _search()
    model = list(q["model"])
    # a misspelled make ('Toyta Corola'): only the first word, only when it is no model name itself
    if not ids and not q["make"] and len(model) > 1 and len(model[0]) >= 3 and not model[0].isdigit() \
            and model[0] not in s["families"]:
        close = difflib.get_close_matches(model[0], list(s["makes"]), n=1, cutoff=0.8)
        if close:
            w2 = []
            q.update(make=close[0], model=model[1:])
            ids, how, left = _match_model_in(q, w2)
            if ids:
                warnings.append("read '%s' as %s" % (model[0], s["makes"][close[0]]))
                warnings.extend(w2)
                q["_low"] = True
                return ids, how, left
            q.update(make=None, model=model)
    # leading words that are no part of the name ('white 2019 civic', "my wife's rav4"): drop them in turn,
    # still within the make when one was given
    if not ids and len(model) > 1:
        for i in range(1, len(model)):
            if all(t.isdigit() for t in model[i:]):
                break
            w2 = []
            q["model"] = model[i:]
            ids, how, left = _match_model_in(q, w2)
            if ids:
                warnings.append("ignored '%s': no NRCan model name starts with it" % " ".join(model[:i]))
                warnings.extend(w2)
                return ids, how, left
        q["model"] = model
        ids, how, left = [], "none", model
    # another make: only for a distinctive name ('1500', 'G80', 'Sprinter'), never a letter or two ('2022 Honda
    # e' is no Mercedes-Benz E), never silently and never with high confidence
    name = "".join(q["model"])
    if not ids and q["make"] and q["model"] and (len(name) >= 3 or (len(name) == 2 and re.search(r"\d", name))):
        q2 = dict(q, make=None)
        w2 = []
        ids, how, left = _match_model_in(q2, w2)
        q["_broad"] = q2.get("_broad")
        q["_key"] = q2.get("_key")
        if ids and how != "fuzzy":
            s = _search()
            warnings.append("no %s '%s' in NRCan ratings; matched %s %s"
                            % (s["makes"].get(q["make"], q["make"]), " ".join(q["model"]),
                               s["rows"][ids[0]].make, family_display(s["rows"][ids[0]].model)))
            q["make"] = None
            q["_other_make"] = True
        else:
            ids, how, left = [], "none", q["model"]
    return ids, how, left


def _perf_after(words, allow):
    """A performance trim among the words after the name ('4X4 SRT', 'SH-AWD Type S'), unless the query named it."""
    ts = []
    for w in words:
        if w.startswith("(") or w.startswith("["):
            break
        t = _norm_token(w)
        if t:
            ts.append(t)
    for j, t in enumerate(ts):
        if t in SEPARATE_WORDS and t not in allow:
            return True
        if t == "type" and j + 1 < len(ts) and ts[j + 1] in ("r", "s") and "type" not in allow:
            return True
    return False


def _is_base(model, key, allow=()):
    """True when `model` is the `key` model itself plus trim, drive or body words ('RAV4 Hybrid AWD',
    'EV6 Wind', 'Corolla (1-mode)', 'Cooper 3 Door'), False for another model that starts the same
    ('Corolla Cross', 'Cooper Countryman') or a performance trim ('Grand Cherokee 4X4 SRT') unless `allow`
    (the query's words) names it."""
    acc = ""
    words = [w for w in re.split(r"[\s/]+", model.strip()) if w]
    for j, w in enumerate(words):
        if w.startswith("(") or w.startswith("["):
            return acc == key
        t0 = _norm_token(w)
        if j and t0 in GEN_CODES and acc != key and not key.startswith(acc + t0):
            continue   # 'Wrangler JK Unlimited' is the 'wranglerunlimited'
        acc += t0
        if acc == key:
            rest = words[j + 1:]
            if _perf_after(rest, set(allow)):
                return False
            if not rest:
                return True
            nxt = rest[0]
            if nxt.startswith("(") or nxt.startswith("["):
                return True
            t = _norm_token(nxt)
            if t in VARIANT_WORDS or t in GEN_CODES:
                return True
            if t.isdigit() and len(rest) > 1 and _norm_token(rest[1]) in ("door", "doors", "dr"):
                return True   # 'Cooper 3 Door' is the Cooper hatch
            return not (len(t) == 1 or t in SUBMODEL_WORDS)
        if len(acc) > len(key):
            return False
    return False


def _words_fit(res, kinds):
    """For a no-year match `res` (see _match_model_in): the year lookup() will use (the newest of the rows it
    keeps) has one of `kinds` (the powertrain of the longer name) and every leftover word is a trim grade
    (GRADE_WORDS) or a name word (two or more characters, not a number) of such a row that year."""
    rows = _search()["rows"]
    pool, broad = res[0], res[3]
    target = max(rows[i].year for i in broad)
    if not any(abs(rows[i].year - target) <= 2 for i in pool):   # lookup() widens to every prefix row
        pool = broad
    newest = max(rows[i].year for i in pool)
    yr = [rows[i] for i in pool if rows[i].year == newest and rows[i].kind in kinds]
    return bool(yr) and all(t in STOP_WORDS or t in GRADE_WORDS or
                            (len(t) >= 2 and not t.isdigit() and any(_mentions(r, t) for r in yr)) for t in res[2])


def _match_model_in(q, warnings):
    s = _search()
    rows, model, make = s["rows"], q["model"], q["make"]
    if make and make not in s["makes"]:
        make = None
    if not model:
        return [], "none", []
    if make == "bmw" or (make is None and any(BMW_SERIES.match(t) for t in model)):
        m = BMW_SERIES.match(model[0])
        if m:   # '3 series' -> every 3xx model of that year
            ids = [i for i, r in enumerate(rows) if _make_key(r.make) == "bmw"
                   and re.match(r"^%s\d\d[a-z]*$" % m.group(1), r.toks[0]) and r.kind != "e"]
            if ids:
                return ids, "series", model[1:]
    # longest name first, but only when it has a rating in the year wanted: otherwise a shorter name that has
    # that year wins and the extra words narrow its variants ('2020 Corolla LE': 'Corolla LE' is a 2019 name;
    # '2023 Civic Si': later years say 'Civic Sedan Si'). With no exact year anywhere, the longest name rated
    # within 2 years, else the longest name. With no year, see below.
    exact = near = far = None
    found = []
    allow = set(model)
    for k in range(len(model), 0, -1):
        key = "".join(model[:k])
        alias = MODEL_ALIASES.get(key)
        key = alias or key
        ids = _rows_for_key(key, make)
        if not ids and make:
            ids = _rows_for_key(make + key, make)    # 'Mazda 3' -> 'mazda3'
            if ids:
                key = make + key
        if not ids:
            continue
        base = [i for i in ids if _is_base(rows[i].model, key, allow)]
        res = ((base or ids), ("family" if base else "prefix"), model[k:], ids, key, "".join(model[:k]))
        if not q["year"]:
            found.append(res)
            continue
        if any(rows[i].year == q["year"] for i in ids):
            exact = res
            break
        if near is None and any(abs(rows[i].year - q["year"]) <= 2 for i in ids):
            near = res
        if far is None:
            far = res
    if found:
        # no year: the newest rating wins. An old trim name ('Civic Si', rated to 2015; 'Corolla LE', to 2019)
        # gives way to the name it is a trim of when that is rated later and the extra words still fit its
        # newest year ('Civic Sedan Si' in 2026; 'LE' is a trim grade); never to another model ('Prius c',
        # 'F-150 Lightning', 'Grand Cherokee SRT') or a version the newer name lacks ('Giulia Quadrifoglio').
        exact = found[0]
        if len(found) > 1:   # one word shorter only: 'Range Rover P400' never becomes 'Range'
            res = found[1]
            kinds = set(rows[i].kind for i in exact[0])
            if max(rows[i].year for i in res[3]) > max(rows[i].year for i in exact[3]) \
                    and _words_fit(res, kinds) and all(_is_base(rows[i].model, res[4]) for i in exact[3]):
                exact = res
    first = exact or near or far
    if first:
        q["_broad"], q["_key"] = first[3], first[4]
        if first[5] in MODEL_RENAMES:
            warnings.append("%s is rated under its Canadian name, %s" % MODEL_RENAMES[first[5]])
        return first[0], first[1], first[2]
    # every query token somewhere in the model name, brackets aside ('Mach-E' -> 'Mustang Mach-E')
    want = set(model)
    ids = [i for i, r in enumerate(rows) if (not make or _make_key(r.make) == make)
           and want <= set(r.core)]
    if not ids and len(model) > 1 and not model[0].isdigit():
        want = set(model[:1])
        ids = [i for i, r in enumerate(rows) if (not make or _make_key(r.make) == make)
               and want <= set(r.core)]
        if ids:
            q["_contains_first"] = True
            return ids, "contains", model[1:]
    if ids:
        return ids, "contains", []
    # close spelling of a family name ('Corola', 'Silverdo')
    fams = s["families"]
    cand = [f for f, mks in fams.items() if not make or make in mks]
    for k in range(len(model), 0, -1):
        key = "".join(model[:k])
        close = difflib.get_close_matches(key, cand, n=1, cutoff=0.8)
        if close:
            ids = [i for i, r in enumerate(rows) if r.famkey == close[0]
                   and (not make or _make_key(r.make) == make)]
            if ids:
                warnings.append("no NRCan model is spelled '%s'; matched '%s'"
                                % (" ".join(model[:k]), family_display(rows[ids[0]].model)))
                return ids, "fuzzy", model[k:]
    return [], "none", model


def _suggest(q):
    """Closest makes or model families for a failed lookup."""
    s = _search()
    words = q["model"] or tokens(q["text"])
    out = []
    if q["make"] and q["make"] in s["makes"]:
        mk = q["make"]
        fams = sorted(f for f, mks in s["families"].items() if mk in mks)
        for f in difflib.get_close_matches("".join(words), fams, n=4, cutoff=0.5):
            if f not in out:
                out.append(f)
        if not out:
            disp = {}
            newest = max(r.year for r in s["rows"] if _make_key(r.make) == mk)
            for r in s["rows"]:
                if _make_key(r.make) == mk and r.year == newest:
                    disp.setdefault(r.famkey, family_display(r.model))
            return "%s models: %s" % (s["makes"][mk], ", ".join(sorted(disp.values())[:12]))
        disp = {}
        for r in s["rows"]:
            if r.famkey in out and _make_key(r.make) == mk:
                disp.setdefault(r.famkey, "%s %s" % (r.make, family_display(r.model)))
        return "closest: " + ", ".join(disp[f] for f in out if f in disp)
    for w in words:
        mk = difflib.get_close_matches(w, list(s["makes"]), n=2, cutoff=0.7)
        out += [s["makes"][m] for m in mk]
        fm = difflib.get_close_matches(w, list(s["families"]), n=3, cutoff=0.7)
        for f in fm:
            r = next(r for r in s["rows"] if r.famkey == f)
            out.append("%s %s" % (r.make, family_display(r.model)))
    seen = []
    for o in out:
        if o not in seen:
            seen.append(o)
    return ("closest: " + ", ".join(seen[:6])) if seen else \
        "write it as YEAR MAKE MODEL, for example \"2022 Toyota Corolla\""


def _label_has_kind(label, kind):
    """The label already names the powertrain ('RAV4 Prime', 'Golf TDI', 'XC40 Recharge', 'Kona Electric')."""
    want = KIND_NAME[kind]
    for t in tokens(label):
        if KIND_WORDS.get(t) == want:
            return True
        if kind in "pe" and t == "recharge":
            return True
        if kind == "p" and t == "plugin":
            return True
    return False


def _kind_suffix(kind, picked, kinds_present, label="", explicit=False):
    """Powertrain word for the label, in a form the lookup reads back ('2016 BMW i3 Electric',
    'i3 REx Plug-in Hybrid'); none when the label already names it. A gas car gets none; an electric or hybrid
    only line gets none unless its own names carry the word or the query named it."""
    if kind == "g" or _label_has_kind(label, kind):
        return ""
    names = " ".join(r.model.lower() for r in picked)
    other = len(kinds_present) > 1 or explicit
    if kind == "h":
        return " Hybrid" if ("hybrid" in names or other) else ""
    if kind == "e":
        if re.search(r"\bev\b", names) and not re.search(r"\belectric\b", names):
            return " EV"
        return " Electric" if (re.search(r"\belectric\b", names) or other) else ""
    if kind == "p":
        for w, lab in (("prime", " Prime"), ("phev", " PHEV"), ("energi", " Energi"), ("4xe", " 4xe"),
                       ("e-hybrid", " E-Hybrid")):
            if re.search(r"\b%s\b" % w, names):
                return lab
        return " Plug-in Hybrid"
    if kind == "d":
        return " Diesel" if (re.search(r"diesel|\btdi\b", names) or other) else ""
    return ""


def _display_prefix(model, key):
    """Original words of `model` that spell the matched key: ('Golf GTI Autobahn', 'golfgti') -> 'Golf GTI'."""
    acc, out = "", []
    for w in re.split(r"\s+", model.strip()):
        n = _norm_token(w.strip("()"))
        if not n:
            continue
        if out and n in GEN_CODES and not key.startswith(acc + n):
            out.append(w)   # 'Wrangler JK Unlimited' spells 'wranglerunlimited'
            continue
        acc += n
        out.append(w)
        if len(acc) >= len(key):
            break
    return " ".join(out) if acc == key else None


def _label(year, picked, kinds_present, engine=None, drive=None, words=None, key=None, explicit=False):
    """'2019 Ford F-150 3.5L'. `drive` is the word to append ('AWD', '4WD') when the text narrowed it;
    `words` are trim words that narrowed it ('GTI'); `key` the matched name when it is longer than the
    family ('Camry XSE'); `explicit` when the query named the powertrain."""
    r0 = picked[0]
    fams = sorted(set(family_display(r.model) for r in picked), key=len)
    name = fams[0]
    common = os.path.commonprefix([f.split() for f in fams])
    if common and len(common) < len(name.split()):   # 'Range Rover Velar P250' and '... P340': the shared name
        name = " ".join(common)
    if key:
        name = _display_prefix(r0.model, key) or name
    lab = "%d %s %s" % (year, r0.make, name)
    for w in words or []:
        if w.lower() not in lab.lower():
            lab += " " + w
    lab += _kind_suffix(r0.kind, picked, kinds_present, lab, explicit)
    if engine:
        lab += " %.1fL" % engine
    if drive:
        lab += " " + drive
    return lab


def _source_for(year, kind):
    idx = load_index()
    want = {"e": "bev", "p": "phev"}.get(kind, "gas")
    for f in idx.get("files") or []:
        y = f.get("years") or [0, 0]
        if f.get("type") == want and y[0] <= year <= y[1]:
            return f.get("url")
    return DATASET_URL


# --------------------------------------------------------------------------- live download fallback

def download_headers(dc=None):
    """Request headers for the NRCan download: the skill's own identifying User-Agent, never a browser one."""
    if dc is None:
        import drive_common as dc
    h = {"User-Agent": dc.th.USER_AGENT}
    h.update(DOWNLOAD_HEADERS)
    return h


def download_url_ok(url):
    """True for an https URL on one of DOWNLOAD_HOSTS: resource URLs from the package listing are checked
    before anything is downloaded."""
    try:
        u = urllib.parse.urlsplit(url or "")
        host = (u.hostname or "").lower()
    except ValueError:
        return False
    return u.scheme.lower() == "https" and host in DOWNLOAD_HOSTS


def _download_warning(what, e):
    """One WARNING line for a failed NRCan download. A refusal (HTTP 401, 403, 429) is named as such; either
    way the lookup goes on with the bundled index."""
    code = getattr(e, "code", None)
    if isinstance(e, urllib.error.HTTPError):
        try:
            e.close()
        except Exception:  # noqa: BLE001   (Python 3.8: an HTTPError without a body raises on close)
            pass
    if code in (401, 403, 429):
        why = "NRCan refused the request (HTTP %s)" % code
    elif code:
        why = "HTTP %s" % code
    else:
        why = str(getattr(e, "reason", None) or e) or e.__class__.__name__
    try:
        ys = _search()["years"]
        span = " (model years %d to %d)" % (ys[0], ys[-1]) if ys else ""
    except Exception:   # noqa: BLE001
        span = ""
    return "%s (%s); using the bundled ratings index%s" % (what, why, span)


def _download_year(year, warnings):
    """Fetch the NRCan CSVs that cover `year` and merge their rows into the in-memory index: rows of
    `year` that the index lacks, plus every year of a multi-year file that the index has no rows for
    (the 1995-2014 file fills 1995 to 2009 in one go). True when rows were added."""
    if year in _TRIED_DOWNLOAD:
        return False
    _TRIED_DOWNLOAD.add(year)
    try:
        import drive_common as dc
    except Exception as e:   # noqa: BLE001
        warnings.append("live NRCan lookup unavailable (%s)" % e)
        return False
    hdrs = download_headers(dc)
    try:
        pkg = json.loads(dc.fetch(PACKAGE_API, headers=hdrs, timeout=30, max_age_h=CACHE_HOURS,
                                  allowed_hosts=DOWNLOAD_HOSTS))
        res = (pkg.get("result") or {}).get("resources") or []
    except Exception as e:   # noqa: BLE001
        warnings.append(_download_warning("could not reach NRCan ratings on open.canada.ca for %d" % year, e))
        return False
    urls = []
    for r in res:
        url = r.get("url") or ""
        name = url.rsplit("/", 1)[-1].lower()
        if "en" not in (r.get("language") or ["en"]) or not name.endswith(".csv") or "original" in name:
            continue
        m = re.match(r"^my(\d{4})(?:-(\d{4}))?-(fuel-consumption|battery-electric|plug-in-hybrid)", name)
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a <= year <= b:
            if download_url_ok(url):   # the package lists the files; only NRCan's own hosts are fetched
                urls.append(url)
            else:
                warnings.append("skipped NRCan ratings file %s: its link is not on open.canada.ca"
                                % dc.th.safe_text(url.rsplit("/", 1)[-1]))
    if not urls:
        warnings.append("NRCan has no ratings file for model year %d yet" % year)
        return False
    idx = load_index()
    classes = idx.setdefault("classes", [])
    present = set((idx.get("rows") or {}).keys())
    added = 0
    for url in urls:
        try:
            raw = dc.fetch(url, headers=hdrs, timeout=60, max_age_h=CACHE_HOURS, allowed_hosts=DOWNLOAD_HOSTS)
        except Exception as e:   # noqa: BLE001
            warnings.append(_download_warning("could not download %s" % url.rsplit("/", 1)[-1], e))
            continue
        try:
            comp = compact_rows(parse_csv(raw), classes)
        except Exception as e:   # noqa: BLE001  a changed layout must not end the lookup
            warnings.append(_download_warning("could not read %s, its layout changed" % url.rsplit("/", 1)[-1], e))
            continue
        got_years = []
        for ys, makes in comp.items():
            if ys != str(year) and ys in present:
                continue
            got_years.append(int(ys))
            _TRIED_DOWNLOAD.add(int(ys))
            by_make = idx.setdefault("rows", {}).setdefault(ys, {})
            have = set()
            for mk, lst in by_make.items():
                for r in lst:
                    have.add((mk, r[0], r[1], r[2], r[4], r[5], r[6]))
            for mk, lst in makes.items():
                for r in lst:
                    if (mk, r[0], r[1], r[2], r[4], r[5], r[6]) not in have:
                        by_make.setdefault(mk, []).append(r)
                        added += 1
        if got_years:
            f = {"type": "bev" if "battery" in url else "phev" if "plug-in" in url else "gas",
                 "years": [min(got_years), max(got_years)], "url": url}
            idx.setdefault("files", []).insert(0, f)
    global _SEARCH
    _SEARCH = None
    return added > 0


# --------------------------------------------------------------------------- lookup

def lookup(text, warnings, allow_download=True):
    """Free text such as '2022 Toyota Corolla', 'corolla 2022', '2023 RAV4 Hybrid', 'Tesla Model Y 2024',
    '2019 F-150 3.5L' or 'Honda Civic' -> the car's NRCan rating, or None (warnings say why).

    Picks the exact model year, else the nearest within 2 years (with a warning); with no year, the newest.
    Hybrid, plug-in, electric and diesel words pick that powertrain; without them plain gas wins and the
    other powertrains are listed in "alternatives". Engine size (3.5L) and AWD/4WD words narrow the
    variants. Plug-in hybrids return gasoline-mode L/100 km. Figures are the median across the variants
    left, with their combined min and max in "range"."""
    if warnings is None:
        warnings = []
    text = (text or "").strip()
    if not text:
        warnings.append("no car given")
        return None
    q = parse_query(text)
    s = _search()
    idx_years = s["years"]
    if q["year"] and allow_download and idx_years and \
            q["year"] not in idx_years and 1995 <= q["year"] <= dt.date.today().year + 1:
        if _download_year(q["year"], warnings):
            q = parse_query(text)
            s = _search()
    ids, how, left = _match_model(q, warnings)
    if not ids and allow_download and q["model"]:
        # only a year the bundled index lacks is worth a download; a typo in an indexed year is not
        y = q["year"] or (idx_years[-1] if idx_years else None)
        if y and y not in idx_years and _download_year(y, warnings):
            q = parse_query(text)
            s = _search()
            ids, how, left = _match_model(q, warnings)
    if not ids:
        what = " ".join(q["model"]) or text
        mk = s["makes"].get(q["make"]) if q["make"] else None
        if mk and not q["model"]:
            warnings.append("'%s' names a make but no model; %s" % (text, _suggest(q)))
        else:
            warnings.append("no NRCan rating matched '%s'%s (searched %d to %d); %s"
                            % (what, (" by " + mk) if mk else "", idx_years[0] if idx_years else 0,
                               idx_years[-1] if idx_years else 0, _suggest(q)))
        return None
    rows = s["rows"]
    pool = [rows[i] for i in ids]
    # several makes share the name: keep the one with the most rows
    makes = {}
    for r in pool:
        makes[r.make] = makes.get(r.make, 0) + 1
    if len(makes) > 1 and not q["make"]:
        best = max(makes, key=lambda m: makes[m])
        warnings.append("'%s' matches %s; used %s" % (" ".join(q["model"]), ", ".join(sorted(makes)), best))
        pool = [r for r in pool if r.make == best]
    # the exact family ('XC60') may be an old name while newer years carry a suffix ('XC60 B5'): when the
    # exact rows are far from the year wanted and the wider match is not, use the wider match
    broad = q.get("_broad") or []
    if how == "family" and len(broad) > len(ids):
        brows = [rows[i] for i in broad if rows[i].make == pool[0].make]
        target = q["year"] or max(r.year for r in brows)
        if not any(abs(r.year - target) <= 2 for r in pool) and any(abs(r.year - target) <= 2 for r in brows):
            pool, how = brows, "prefix"
    pool_all = pool

    # powertrain asked for: keep it (across years) when the family has it
    want = KIND_CODE.get(q["kind"]) if q["kind"] else None
    if want:
        def near_year(rs):
            return not q["year"] or any(abs(r.year - q["year"]) <= 2 for r in rs)
        got = [r for r in pool if r.kind == want]
        if want == "h" and not (got and near_year(got)):
            ph = [r for r in pool if r.kind == "p"]
            if ph and near_year(ph):
                got = ph
                warnings.append("no regular %s hybrid near that year; used the plug-in hybrid"
                                % family_display(ph[0].model))
        if not got:   # 'F-150 electric' -> 'F-150 Lightning': widen to every model starting with the name
            wide = []
            key = "".join(q["model"][:len(q["model"]) - len(left)]) if q["model"] else ""
            if key:
                wide = [rows[i] for i in _rows_for_key(key, _make_key(pool[0].make))]
            got = [r for r in wide if r.kind == want]
            if got:
                q["_key"] = None    # the label names the sibling model, not the words typed
        if got and q["year"] and not any(r.year == q["year"] for r in got) and q.get("_key"):
            # none of that powertrain in the year asked: a sibling of the same make and year may have it
            # ('2019 Ram 1500 diesel' -> '1500 Classic EcoDiesel')
            sib = [rows[i] for i in _rows_for_key(q["_key"], _make_key(pool[0].make))]
            sib = [r for r in sib if r.kind == want and r.year == q["year"]]
            if sib:
                warnings.append("no %d %s %s %s in NRCan; used the %d %s" % (
                    q["year"], pool[0].make, family_display(pool[0].model), KIND_TEXT[q["kind"]], q["year"],
                    sorted(set(family_display(r.model) for r in sib), key=len)[0]))
                got, q["_key"] = sib, None
        if got and near_year(got):
            pool = got
        else:
            if got:
                warnings.append("NRCan lists the %s %s for %s only; used the %s"
                                % (q["kind"], family_display(got[0].model),
                                   _span(sorted(set(r.year for r in got))), KIND_NAME[_pick_kind(pool)]))
            else:
                warnings.append("no %s version of %s in NRCan ratings; used the %s"
                                % (q["kind"], family_display(pool[0].model),
                                   KIND_NAME[_pick_kind(pool)]))
            want = None

    # model year
    years = sorted(set(r.year for r in pool))
    year = q["year"]
    year_note = None
    if year is None:
        year = years[-1]
        warnings.append("no model year given; used %d, the newest NRCan rating for this model" % year)
        year_note = "newest"
    elif year not in years:
        if allow_download and idx_years and year >= idx_years[-1] - 1 and _download_year(year, warnings):
            return lookup(text, warnings, allow_download=False)
        near = sorted(years, key=lambda y: (abs(y - year), -y))[0]
        if abs(near - year) > 2:
            warnings.append("NRCan has %s ratings for %s only, not %d"
                            % (family_display(pool[0].model), _span(years), year))
            return None
        if want and any(r.year == year for r in pool_all):   # the model has that year, not that powertrain
            warnings.append("no %d %s %s %s in NRCan; used the %d %s" % (
                year, pool[0].make, family_display(pool_all[0].model), KIND_TEXT[q["kind"]], near,
                KIND_TEXT[q["kind"]]))
        else:
            warnings.append("NRCan has no %d %s; used the %d rating" % (year, family_display(pool[0].model), near))
        year_note = "near"
        year = near
    yrows = [r for r in pool if r.year == year]
    # powertrains of this model in this year, for labels and alternatives (the base family only)
    yall = [r for r in pool_all if r.year == year]
    if want and not [r for r in yall if r.kind == want]:
        yall = yall + yrows
    kinds_present = sorted(set(r.kind for r in yall), key=DEFAULT_KIND_ORDER.index)
    kind = want if want in kinds_present else _pick_kind(yrows, q.get("_key"))
    picked = [r for r in yrows if r.kind == kind]
    if not want:
        # a word that only another powertrain's name carries picks that powertrain ('XC60 Recharge' is the
        # T8 plug-in); 'Recharge' alone means electrified
        for tk in left:
            if tk in STOP_WORDS or any(_mentions(r, tk) for r in picked):
                continue
            other = [r for r in yrows if r.kind != kind and _mentions(r, tk)]
            if not other and tk == "recharge":
                other = [r for r in yrows if r.kind in "pe"]
            if other:
                kind = "e" if tk == "recharge" and any(r.kind == "e" for r in other) else _pick_kind(other)
                picked = [r for r in yrows if r.kind == kind]
                break

    # narrowing: engine size, drive, then any leftover trim words that some variants carry
    notes, engine, drive, words = [], None, None, []
    if q["engine"] is not None:
        e = [r for r in picked if r.engine is not None and abs(r.engine - q["engine"]) < 0.05]
        if e:
            picked, engine = e, q["engine"]
        else:
            engs = sorted(set(r.engine for r in picked if r.engine))
            warnings.append("no %.1fL engine in the %d %s ratings (%s); used all engines"
                            % (q["engine"], year, family_display(picked[0].model),
                               ", ".join("%.1fL" % x for x in engs) or "none listed"))
    if q.get("ecoboost"):
        e = [r for r in picked if "ecoboost" in r.toks]
        if not e and _make_key(picked[0].make) == "ford":
            e = [r for r in picked if r.engine is not None and any(abs(r.engine - x) < 0.05 for x in ECOBOOST_L)]
        if e and len(e) < len(picked):
            picked = e
            notes.append("ecoboost")
        if e:
            words.append("EcoBoost")
        else:
            warnings.append("ignored 'ecoboost': no %d %s variant is an EcoBoost" % (year, family_display(
                picked[0].model)))
    if q["drive"]:
        if q["drive"] == "awd":
            d = [r for r in picked if r.drive == "awd"]
        else:   # the word typed ('RWD') in the name, else any 2WD word, else a name with no drive word
            d = ([r for r in picked if q.get("drive_tok") in r.toks] or [r for r in picked if r.drive == "2wd"]
                 or [r for r in picked if r.drive is None])
        if d:
            picked, drive = d, q.get("drive_word") or q["drive"].upper()
        else:
            warnings.append("no %s variant listed; used all" % q["drive"].upper())
    ignored = []
    for tk in left:
        if tk in STOP_WORDS:
            continue
        t = [r for r in picked if _mentions(r, tk)]
        if t:
            if len(t) < len(picked):
                picked = t
                notes.append(tk)
            for w in re.split(r"[\s/]+", t[0].model):   # name the word in the label either way
                if _norm_token(w) == tk or (_norm_token(w).startswith(tk) and len(tk) >= 3):
                    words.append(w.strip("()"))
                    break
        else:
            ignored.append(tk)
    desig = [tk for tk in ignored if DESIGNATOR.match(tk) and not re.match(r"^\d\dt$", tk)]
    if desig:   # '2022 Lexus UX 300e': a model NRCan does not list, not a trim of the UX 200
        warnings.append("no NRCan rating for the %d %s %s %s; the %d ratings list %s" % (
            year, picked[0].make, family_display(picked[0].model).split(" ")[0], desig[0], year,
            ", ".join(sorted(set(family_display(r.model) for r in yall))[:6])))
        return None
    if ignored and q.get("_contains_first") and len(ignored) == len([t for t in left if t not in STOP_WORDS]):
        # only one loose word matched and none of the rest fits: no real match
        warnings.append("no NRCan rating matched '%s'" % " ".join(q["model"]))
        return None
    if ignored:
        warnings.append("ignored '%s': no %d %s variant mentions it"
                        % (" ".join(ignored), year, family_display(picked[0].model)))
    loose = [tk for tk in ignored if tk not in GRADE_WORDS]   # a trim grade is the same car; another word may not be

    nvar = sum(r.n for r in picked)
    combs = [r.comb for r in picked]
    city = _wmedian((r.city, r.n) for r in picked)
    hwy = _wmedian((r.hwy, r.n) for r in picked)
    comb = _wmedian((r.comb, r.n) for r in picked)
    fuel_w = {}
    for r in picked:
        fuel_w[r.fuel] = fuel_w.get(r.fuel, 0) + r.n
    fuel = max(fuel_w, key=lambda f: fuel_w[f])
    cls_w = {}
    for r in picked:
        cls_w[r.cls] = cls_w.get(r.cls, 0) + r.n
    cls = max(cls_w, key=lambda c: cls_w[c])
    picked_txt = "one variant" if nvar == 1 else "median of %d variants" % nvar
    if notes:
        picked_txt += " matching '%s'" % " ".join(notes)
    if len(fuel_w) > 1:
        picked_txt += "; %s fuel for %d of %d" % (FUEL_NAME[fuel], fuel_w[fuel], nvar)
    out = {
        "query": text,
        "label": _label(year, picked, kinds_present, engine, drive, words, key=q.get("_key"),
                        explicit=bool(want)),
        "year": year, "make": picked[0].make, "model": family_display(picked[0].model),
        "class": cls, "car_class": car_class(cls, comb), "kind": KIND_NAME[kind],
        "fuel": FUEL_NAME.get(fuel, "regular"),
        "unit": "kWh/100 km" if kind == "e" else "L/100 km",
        "city": city, "hwy": hwy, "comb": comb, "variants": nvar,
        "range": [min(combs), max(combs)], "picked": picked_txt,
        "variant_names": sorted(set(r.model for r in picked))[:8],
        "engines": sorted(set(r.engine for r in picked if r.engine)),
        "alternatives": [], "range_km": None,
        "source": "NRCan Fuel Consumption Ratings, model year %d" % year,
        "source_url": _source_for(year, kind),
        "confidence": "high",
    }
    if kind == "e":
        rk = [(r.range_km, r.n) for r in picked if r.range_km]
        out["range_km"] = int(_wmedian(rk)) if rk else None
    if kind == "p":
        ek = [(r.e_kwh, r.n) for r in picked if r.e_kwh]
        er = [(r.e_range, r.n) for r in picked if r.e_range]
        out["electric_kwh_100km"] = _wmedian(ek) if ek else None
        out["electric_range_km"] = int(_wmedian(er)) if er else None
        out["picked"] += ", gasoline mode of a plug-in hybrid (battery driving not counted)"
        if out["electric_range_km"]:
            out["picked"] += "; about %d km electric when charged" % out["electric_range_km"]
    # alternatives: other powertrains, other models the text also fits, other engines
    alts = []
    for k in kinds_present:
        if k != kind:
            alts.append(_label(year, [r for r in yall if r.kind == k], kinds_present))
    if how in ("prefix", "contains", "series", "fuzzy"):
        fams = {}
        for r in pool:
            if r.year == year and r.kind == kind:
                fams.setdefault(r.famkey, []).append(r)
        if len(fams) > 1:
            for f, lst in sorted(fams.items(), key=lambda kv: -len(kv[1])):
                lab = _label(year, lst, kinds_present)
                if lab != out["label"]:
                    alts.append(lab)
    if engine is None and len(out["engines"]) > 1:
        for e in out["engines"]:
            alts.append(_label(year, [r for r in picked if r.engine == e], kinds_present, e, drive, words,
                               key=q.get("_key"), explicit=bool(want)))
    # the same name in another generation that year ('2018 Wrangler Unlimited': JK and the 'New Wrangler' JL)
    gen = []
    if q.get("_key") and q["model"][:1] != ["new"]:
        for r in s["rows"]:
            if r.year == year and r.make == picked[0].make and r.toks[:1] == ["new"] and r.kind == kind \
                    and _is_base(r.model, "new" + q["_key"]):
                gen.append(r)
    if gen:
        lab = _label(year, gen, kinds_present, key="new" + q["_key"])
        warnings.append("NRCan lists two generations for %d; used the %s, the %s is under also matched"
                        % (year, out["label"], lab))
        alts.insert(0, lab)
    seen = []
    for a in alts:
        if a not in seen and a != out["label"]:
            seen.append(a)
    out["alternatives"] = seen[:5]
    # confidence
    spread = max(combs) - min(combs)
    tight = spread <= (2.0 if kind == "e" else 1.0)
    if how == "fuzzy" or q.get("_low") or year_note == "near" and abs(year - (q["year"] or year)) >= 2 or \
            spread > (6.0 if kind == "e" else 3.0):
        out["confidence"] = "low"
    elif year_note or not tight or how in ("contains", "series") or gen or q.get("_other_make") or loose:
        out["confidence"] = "medium"
    return out


def _pick_kind(rows, key=None):
    """Plain gas first, then hybrid, diesel. Among plug-ins and electrics only ('2016 BMW i3': 'i3' and
    'i3 REx'), the row named exactly as asked, else the electric."""
    have = set(r.kind for r in rows)
    for k in "ghd":
        if k in have:
            return k
    if key:
        exact = [r for r in rows if r.famkey == key or "".join(r.core) == key]
        if exact:
            return _pick_kind(exact)
    for k in "ep":
        if k in have:
            return k
    return rows[0].kind


def _mentions(r, tk):
    """The row's name carries the query word (or a word starting with it, from 3 letters)."""
    return tk in r.toks or (len(tk) >= 3 and any(x.startswith(tk) for x in r.toks))


def _span(years):
    return "%d to %d" % (years[0], years[-1]) if len(years) > 1 else str(years[0])


# --------------------------------------------------------------------------- saved car profile

def profile_path():
    """TRIP_PLANNER_CONFIG (a file path ending in .json, or a folder that gets car.json, created on save if
    needed), else %APPDATA%/trip-planner/car.json on Windows, else ~/.config/trip-planner/car.json."""
    env = os.environ.get("TRIP_PLANNER_CONFIG", "").strip()
    if env:
        env = os.path.expanduser(env)
        if env.endswith(("/", "\\", os.sep)) or os.path.isdir(env) or os.path.splitext(env)[1].lower() != ".json":
            return os.path.normpath(os.path.join(env.rstrip("/\\") or env, "car.json"))
        return env
    if os.name == "nt" and os.environ.get("APPDATA"):
        return os.path.join(os.environ["APPDATA"], "trip-planner", "car.json")
    return os.path.join(os.path.expanduser("~"), ".config", "trip-planner", "car.json")


def shown_path(path):
    """`path` for printing: the home folder becomes '~', so output and logs never carry the user's account
    name."""
    if not path:
        return path
    home = os.path.expanduser("~")
    p = os.path.normpath(path)
    if home and home != "~":
        h = os.path.normpath(home)
        if os.path.normcase(p) == os.path.normcase(h):
            return "~"
        if os.path.normcase(p).startswith(os.path.normcase(h) + os.sep):
            return "~" + p[len(h):]
    return p


def load_profile(warnings=None):
    """The saved car (dict) or None. A corrupt or unreadable file gives a warning and None, never a crash."""
    path = profile_path()
    if not os.path.exists(path):
        return None
    msg = None
    try:
        with open(path, encoding="utf-8-sig") as f:   # a file saved by Notepad may start with a BOM
            data = json.load(f)
        if isinstance(data, dict) and data:
            return data
        msg = "saved car file %s holds no car; ignored it" % shown_path(path)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        msg = "saved car file %s is unreadable (%s); ignored it. Save the car again to replace it" \
              % (shown_path(path), e.__class__.__name__)
    if warnings is not None:
        warnings.append(msg)
    else:
        sys.stderr.write("warning: %s\n" % msg)
    return None


def save_profile(car_dict):
    """Write the car (a lookup result plus any overrides such as l_per_100km, battery_kwh, ev_model) with
    today's date as "saved". Atomic: a temp file in the same folder, then a rename. Returns the path."""
    path = profile_path()
    data = dict(car_dict or {})
    data["saved"] = dt.date.today().isoformat()
    data.setdefault("profile_version", 1)
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def forget_profile():
    """Delete the saved car. True when a file was removed."""
    path = profile_path()
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    offline = "--offline" in argv
    argv = [a for a in argv if a != "--offline"]
    if not argv:
        sys.stderr.write("usage: vehicles.py [--offline] \"YEAR MAKE MODEL\"\n")
        return 2
    w = []
    car = lookup(" ".join(argv), w, allow_download=not offline)
    print(json.dumps({"car": car, "warnings": w}, indent=1))
    return 0 if car else 1


if __name__ == "__main__":
    sys.exit(main())
