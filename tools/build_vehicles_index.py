"""Rebuild references/driving/vehicles-index.json from Natural Resources Canada's Fuel Consumption Ratings.

Lists the dataset through the open.canada.ca CKAN API (package_show), downloads the English ratings CSVs
(conventional, battery-electric and plug-in hybrid) with the skill's own User-Agent, parses them with
vehicles.parse_csv (the same parser the live fallback uses) and writes the compact index. Re-run when NRCan
publishes a new model year:

    python tools/build_vehicles_index.py                  # reuse files already downloaded
    python tools/build_vehicles_index.py --download       # fetch the package listing and every CSV again
    python tools/build_vehicles_index.py --min-year 2010  # first model year kept (default 2010)

Downloads are kept in the skill's cache folder (TRIP_PLANNER_CACHE, else the default cache), under
nrcan-ratings/, never in the repo. Standard library only.
"""
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import drive_common as dc  # noqa: E402
import vehicles as V  # noqa: E402

OUT = os.path.join(SKILL, "references", "driving", "vehicles-index.json")
BUILT_BY = "tools/build_vehicles_index.py"


def get(url):
    """One download with the skill's identifying headers; only NRCan's own hosts."""
    if not V.download_url_ok(url):
        raise SystemExit("refusing to download %s: not on open.canada.ca" % url)
    return dc.fetch(url, headers=V.download_headers(dc), timeout=120, allowed_hosts=V.DOWNLOAD_HOSTS)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    download = "--download" in argv
    min_year = 2010
    if "--min-year" in argv:
        min_year = int(argv[argv.index("--min-year") + 1])
    raw_dir = os.path.join(dc.th.cache_dir(), "nrcan-ratings")
    os.makedirs(raw_dir, exist_ok=True)
    pkg_path = os.path.join(raw_dir, "package.json")
    if download or not os.path.exists(pkg_path):
        with open(pkg_path, "wb") as f:
            f.write(get(V.PACKAGE_API))
    with open(pkg_path, encoding="utf-8") as f:
        pkg = json.load(f)["result"]
    files, rows = [], []
    for r in pkg["resources"]:
        url = r.get("url") or ""
        name = url.rsplit("/", 1)[-1]
        if "en" not in (r.get("language") or []) or not name.endswith(".csv") or name.startswith("original"):
            continue
        m = re.match(r"^my(\d{4})(?:-(\d{4}))?-(fuel-consumption|battery-electric|plug-in-hybrid)", name)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if b < min_year:
            continue
        path = os.path.join(raw_dir, name)
        if download or not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(get(url))
        with open(path, "rb") as f:
            try:
                parsed = V.parse_csv(f.read(), min_year=min_year)
            except V.LayoutError as e:
                raise SystemExit("%s: %s; update vehicles.parse_csv for the new layout" % (name, e))
        typ = {"battery-electric": "bev", "plug-in-hybrid": "phev"}.get(m.group(3), "gas")
        files.append({"type": typ, "years": [max(a, min_year), b], "url": url,
                      "modified": (r.get("last_modified") or r.get("created") or "")[:10], "rows": len(parsed)})
        rows += parsed
    classes = []
    comp = V.compact_rows(rows, classes)
    out_rows = {}   # rows sorted within each make for stable diffs
    for y in sorted(comp):
        out_rows[y] = {}
        for mk in sorted(comp[y]):
            out_rows[y][mk] = sorted(comp[y][mk], key=lambda r: (r[0], r[1] or 0, r[2], r[6]))
    years = sorted(int(y) for y in out_rows)
    idx = {
        "about": "NRCan Fuel Consumption Ratings, compact index for trip-planner vehicles.py. Row: "
                 "[model, engine_l, fuel X|Z|D|B, kind g|h|d|p|e, city, hwy, comb, class_idx, n, extra]; "
                 "L/100 km, or kWh/100 km for kind e; extra e: [range_km], p: [electric kWh/100 km, electric "
                 "range km] with city/hwy/comb the gasoline-mode figures; n = NRCan rows with identical figures "
                 "(transmissions folded). Kind h is inferred (name, hybrid-only lines, CVT with city <= hwy).",
        "source": "Natural Resources Canada, Fuel Consumption Ratings (Open Government Licence - Canada)",
        "dataset": V.DATASET_URL,
        "api": V.PACKAGE_API,
        "dataset_modified": (pkg.get("metadata_modified") or "")[:10],
        "built": dt.date.today().isoformat(),
        "built_by": BUILT_BY,
        "years": [years[0], years[-1]],
        "files": files,
        "classes": classes,
        "rows": out_rows,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(idx, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")
    n = sum(len(v) for y in out_rows.values() for v in y.values())
    print("rows in %d, index rows %d, %d bytes, model years %d to %d" % (len(rows), n, os.path.getsize(OUT),
                                                                         years[0], years[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
