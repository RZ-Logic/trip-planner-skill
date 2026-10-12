"""Store a small stop map in each region file feed, so trips can skip far-away feeds without
downloading them (the TTC feed alone is 84 MB).

The map is the list of grid cells (CELL_DEG degrees square, about 2 km) that contain at least one stop.
Re-run when an agency adds service in a new area:

    python tools/build_coverage.py                 # all region files
    python tools/build_coverage.py gtha            # one region
"""
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import trip_hubs as th  # noqa: E402

CELL_DEG = 0.02


def dumps(o, ind=0):
    """JSON with nested objects indented and lists of plain values (a timetable row, a box) on one line, so
    hand-entered timetables stay readable and small."""
    pad, inner = "  " * ind, "  " * (ind + 1)
    if isinstance(o, dict):
        if not o:
            return "{}"
        return "{\n" + ",\n".join("%s%s: %s" % (inner, json.dumps(k), dumps(v, ind + 1)) for k, v in o.items()) \
            + "\n" + pad + "}"
    if isinstance(o, list):
        if all(not isinstance(x, (dict, list)) for x in o) and len(json.dumps(o)) <= 160:
            return json.dumps(o)
        if not o:
            return "[]"
        return "[\n" + ",\n".join(inner + dumps(x, ind + 1) for x in o) + "\n" + pad + "]"
    return json.dumps(o)


def build(region_path):
    with open(region_path, encoding="utf-8") as fh:
        region = json.load(fh)
    for f in region.get("feeds", []):
        if f.get("skip"):
            continue
        path, err = None, None
        if f.get("manual"):   # a hand-entered timetable: build its small GTFS zip and map that
            try:
                path = th.manual_feed_zip(f)
            except Exception as e:  # noqa: BLE001
                err = e
        for url in [] if f.get("manual") else f["urls"]:
            try:
                path = th.fetch_url(f["id"], url, 72, False)
                break
            except Exception as e:  # noqa: BLE001
                err = e
        if path is None:
            print("  %-12s FAILED (%s); keeping any previous map" % (f["id"], err))
            continue
        if f.get("manual"):
            pts = [(st[1], st[2]) for r in f["manual"]["routes"] for t in r["trips"] for st in t["stops"]]
            f["bbox"] = {"min_lat": round(min(p[0] for p in pts), 5), "max_lat": round(max(p[0] for p in pts), 5),
                         "min_lon": round(min(p[1] for p in pts), 5), "max_lon": round(max(p[1] for p in pts), 5)}
        cells = sorted(th.stop_cells(path, CELL_DEG))
        rb = region.get("bbox")
        if rb:   # a continental feed (FlixBus) only needs its cells near the region (1 degree of margin)
            cells = [c for c in cells if rb["min_lat"] - 1 <= c[0] * CELL_DEG <= rb["max_lat"] + 1
                     and rb["min_lon"] - 1 <= c[1] * CELL_DEG <= rb["max_lon"] + 1]
        if not cells:
            print("  %-12s no stops near the region; not mapped" % f["id"])
            continue
        if not f.get("bbox"):   # no catalog box: use the mapped stops
            f["bbox"] = {"min_lat": round(min(c[0] for c in cells) * CELL_DEG, 4),
                         "max_lat": round((max(c[0] for c in cells) + 1) * CELL_DEG, 4),
                         "min_lon": round(min(c[1] for c in cells) * CELL_DEG, 4),
                         "max_lon": round((max(c[1] for c in cells) + 1) * CELL_DEG, 4)}
        f["coverage"] = {"cell_deg": CELL_DEG, "built": dt.date.today().isoformat(),
                         "cells": ";".join("%d,%d" % c for c in cells)}
        print("  %-12s %4d cells" % (f["id"], len(cells)))
    with open(region_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(region) + "\n")
    print("wrote %s (%.0f KB)" % (region_path, os.path.getsize(region_path) / 1024.0))


def main():
    regions_dir = os.path.join(SKILL, "references", "regions")
    wanted = set(sys.argv[1:])
    for name in sorted(os.listdir(regions_dir)):
        if name.endswith(".json") and (not wanted or name[:-5] in wanted):
            print(name)
            build(os.path.join(regions_dir, name))


if __name__ == "__main__":
    main()
