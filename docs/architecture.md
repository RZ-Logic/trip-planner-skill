# Architecture

How trip-planner is built: where the model is and is not involved, what each component does, the stack, and the properties the code and the agent instructions aim for. Where a property is only an instruction to
the agent, this page says so. For the decisions behind it, see [build-decisions.md](build-decisions.md). Back to the [README](../README.md).

## Where is the AI?

Adapted from the *"Where is the AI?"* table in [FinAgent OS](https://github.com/RZ-Logic/finagent-os). Six stages are deterministic code. The three judgment stages belong to the agent. The agent does not calculate the plan's numbers. It does arithmetic in two labelled cases only: totals of figures the scripts printed (both directions of a trip, or a re-total after a checked price), and the manual method when the scripts cannot run (the documented rate-card and 407 zone formulas applied to sourced inputs, labelled as manual estimates). When it verifies a price or a departure on a live page, it reports that fact separately, with its date and source.

| # | Stage | Deterministic? | What it does |
|---|---|:-:|---|
| 1 | **Pin down the request** | ❌ agent | Origin, destination, time, priority, payment, and for drives the car. Asks once; if the user declines, states the assumption the script prints. |
| 2 | **Geocoding** | ✅ | Nominatim, then Photon biased to Nominatim's street; a Photon house more than 5 km from that street is not used. A house number that matches only the street stops the run (`ADDRESS NOT PRECISE`), because a point a kilometre off changes which hubs are in range. |
| 3 | **Data selection** | ✅ | Region files, bundled stop maps, MobilityDatabase discovery. Region files claim only points in their own country (reverse geocoded at town level). Region feeds whose stored stop map has no stops near the trip are never downloaded. Discovered feeds are skipped without a download when their catalog box is out of reach of both ends; others are downloaded (at most 60 per run) and dropped when no stop is near the trip. Town-name matches must be in the trip's country. Bundled data (stop maps, hand-entered timetables, curated tourist-place parking and the post-2019 Green P carparks, the car index) keeps working when the network is blocked (transit: see Works offline below). |
| 4 | **Enumeration** | ✅ | One backward profile connection scan over GTFS; every hub in ride or drive range checked within the search area. For drives: fastest, toll-free and alternative routes, plus every parking source near the door. |
| 5 | **Times** | ✅ | OSRM or Valhalla free-flow times through a calibrated time-of-day model, or TomTom live traffic with a key. Plan times use a conservative planning allowance. Hub rides use the same rows. |
| 6 | **Pricing** | ✅ | Uber rate cards by region, fares with transfer rules, 407 ETR tolls by zone and entry band, fuel from the car's NRCan rating and the weekly fuel survey, parking for the actual arrival and stay, EV charging. |
| 7 | **Ranking** | ✅ | Dominance (cheaper *and* earlier), money plus time at a stated value of time, parking tiers that sink unverified and possibly closed lots. |
| **8** | **Gap closing and verification** | **❌ agent** | Missing feeds, on-demand services, live departures, stale parking prices. Bounded by explicit steps in `SKILL.md`: the operator's own page, else a web search, and never a scrape the operator's terms forbid. |
| **9** | **The answer** | **❌ agent** | One recommendation, a short table, the backup, the coverage, and every `WARNING` disclosed. |

> **The principle:** A model that computes prices will eventually invent one. The scripts calculate the estimates. The agent instructions forbid inventing any number the scripts did not print or a live page did not show, and editing the scripts to get an answer; they allow arithmetic only for labelled totals of printed figures (both directions of a trip, or a re-total after a checked price), or for a labelled manual estimate from the documented formulas when the scripts cannot run. They require any checked fact to carry its date and source. This is an instruction contract, not a technical boundary: no client enforces it.

## What ships

```
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│   "Get me to Bay St by 9:00 tomorrow, I'm driving, parked all day."        │
│                                                                            │
│     Agent: pins down the car ("2022 Corolla"), the stay (9 h)              │
│       │                                                                    │
│       ▼                                                                    │
│     drive_plan.py                                                          │
│       ├─ routes      fastest via 407 ETR, toll-free route, alternatives    │
│       ├─ tolls       407 zone x direction x entry band, camera charge      │
│       ├─ car         NRCan rating, Ontario fuel survey price               │
│       ├─ parking     city data, Green P, OSM, curated; priced for the stay │
│       ├─ arrival     allow time + parking search + walk + spare            │
│       └─ park+ride   trip_hubs.py --access car: every GO lot in range      │
│       │                                                                    │
│       ▼                                                                    │
│     Agent: checks the flagged lots, then one recommendation:               │
│       "Take the toll-free 401/427/Gardiner route, leave by 07:03. Or drive │
│        4.4 km to Milton GO, free parking, the 07:45 train: about $21.74    │
│        there and back against $31.87 driving and parking."                 │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### Component overview

| Component | What it is | Role of the model |
|-----------|-----------|:-:|
| **Hub finder** ([`trip_hubs.py`](../plugins/trip-planner/skills/trip-planner/scripts/trip_hubs.py)) | GTFS loader and backward profile connection scan; ride pricing, fares and transfer rules, final-ride legs, Transitous cross-check, `--access car` for park and ride. | None |
| **Region files** ([`references/regions/`](../plugins/trip-planner/skills/trip-planner/references/regions/)) | GTHA: 13 working feeds (Grand River Transit is listed but skipped until a current file is available), transfer rules, Uber rate cards, GO parking rules. Ontario getaways: 39 feeds including 20 hand-entered timetables, 52 ride areas, 48 destinations, holiday corrections. | None |
| **Driving planner** ([`drive_plan.py`](../plugins/trip-planner/skills/trip-planner/scripts/drive_plan.py)) | The driving CLI: routes, trade-off, arrival, parking, park and ride, charging, incidents, links, `--json`. | None |
| **Routing and traffic** ([`drive_route.py`](../plugins/trip-planner/skills/trip-planner/scripts/drive_route.py), [`traffic.json`](../plugins/trip-planner/skills/trip-planner/references/driving/traffic.json)) | Valhalla and OSRM keyless, calibrated against Google's typical ranges; TomTom live with a key; separate 407 rows; getaway-peak rule; border waits. | None |
| **Toll engine** ([`tolls.py`](../plugins/trip-planner/skills/trip-planner/scripts/tolls.py), [`tolls.json`](../plugins/trip-planner/skills/trip-planner/references/driving/tolls.json)) | 407 ETR 2026 rate chart: 12 zones, 2 directions, 12 time bands (288 cells), 41 interchanges, holiday pricing. 10 border crossings priced by direction. | None |
| **Parking** ([`parking.py`](../plugins/trip-planner/skills/trip-planner/scripts/parking.py) and data) | 13 open-data layers in 8 cities, Toronto Green P with occupancy, OpenStreetMap, and 54 hand-checked tourist places with 167 located lots. Each lot is priced for the actual stay, with a check line when its price is old or unverified. | None |
| **Review leads** ([`place_reviews.py`](../plugins/trip-planner/skills/trip-planner/scripts/place_reviews.py)) | Optional: rates quoted in Google reviews of nearby parking from the last 18 months (up to 5 per place, picked by Google for relevance), via the official Places API, labelled unverified. | None |
| **EV charging** ([`ev_charging.py`](../plugins/trip-planner/skills/trip-planner/scripts/ev_charging.py)) | Stations along the route (NLR, NRCan data), a temperature-aware energy model, greedy stops, network price table, return-trip check. 10 car presets. | None |
| **Car lookup** ([`vehicles.py`](../plugins/trip-planner/skills/trip-planner/scripts/vehicles.py)) | NRCan Fuel Consumption Ratings: a bundled index of 17,520 rows (model years 2010 to 2026, 56 makes), older years downloaded on demand. Saved car profile. | None |
| **Agent instructions** ([`SKILL.md`](../plugins/trip-planner/skills/trip-planner/SKILL.md), [`references/`](../plugins/trip-planner/skills/trip-planner/references/)) | The one rule, which planner to run, the reading order of the output, the verify steps, the answer format, and the manual method for when code cannot run. | The whole layer |
| **Tests** ([`tests/`](../tests/)) | Offline unit tests, run by the CI workflow (`.github/workflows/tests.yml`) on pushes to main and on pull requests, with no API keys and a network guard (`tools/ci/sitecustomize.py`) that refuses every non-loopback connection and fails the run if a test tries one, plus one opt-in live acceptance test (`TRIP_PLANNER_LIVE=1`). Reviewer-found defects each have a regression test. Doc-contract tests check that the flags, output labels, versions, `references/` paths and zip contents the docs name match the code. | None |

## Stack

| Layer | Tool | Why |
|-------|------|-----|
| Timetables | Agency GTFS feeds, [MobilityDatabase](https://mobilitydatabase.org) catalog | Public, machine-readable, the same data every transit app uses. Region files pin mirrors and correct known feed quirks. |
| Transit routing | Own profile connection scan, Python stdlib | One backward scan answers every hub at once. A router queried per hub would need hundreds of calls. |
| Second opinion | [Transitous](https://transitous.org) (MOTIS) | An independent router to cross-check the top hubs. |
| Geocoding | Nominatim, Photon | Free OpenStreetMap geocoders at 1 request per second, cached 30 days, with a strict rule for house numbers. |
| Drive times | OSRM, Valhalla (FOSSGIS); TomTom with a key | Keyless by default; Valhalla's `exclude_tolls` gives a true toll-free route; TomTom adds live and historic traffic. |
| Tolls | 407 ETR published rate chart, computed locally | The official calculator needs reCAPTCHA, so it is never called. |
| Fuel | NRCan Fuel Consumption Ratings, Ontario weekly fuel price survey | Official ratings per model, official weekly prices per market. |
| Parking | City ArcGIS layers, City of Toronto open data, Overpass, curated files | The open sources the planner supports (eight cities' layers, Toronto, OpenStreetMap, 54 curated places); commercial operators forbid scraping, so they get a link. |
| Charging | NLR (ex-NREL) Alternative Fuel Stations API | Carries NRCan's Canadian station data; queried with `country=all` (US and Canada; the API default is US only). |
| Weather | Open-Meteo | Keyless forecast; rain and snow adders only within 3 days. |
| Packaging | [Agent Skills](https://agentskills.io) (`SKILL.md`) | One folder runs in Claude Code, claude.ai, the Claude API, Codex, Gemini CLI, Cursor and Copilot. |

**No infrastructure novelty.** Everything here is public data, free services and the Python standard library. The contribution is the application layer:

- the every-hub enumeration and the park-and-ride search;
- the calibrated time-of-day model;
- the 407 pricing model;
- parking priced for the actual stay, with honest freshness labels;
- an agent contract that keeps every number out of the model's hands, except labelled totals of printed figures (including a re-total after a checked price) and a labelled manual estimate when the scripts cannot run.

## Engineering properties

| Principle | How it works |
|-----------|--------------|
| **Every hub in the search area, not the suggested one** | One backward profile scan gives every stop in range its full arrival profile, and every hub is read from it. Hubs are hidden only for a stated reason (past `--top`, on the same vehicle as a listed hub at no lower ride + fare total beyond the 5% margin, too little gain, too late to book, past the routing request cap), and the output counts them in its `NOT SHOWN` and WARNING lines. Final-ride drop points past the cap are named in WARNINGS; every train line's stop is always tried. Enforced by the code. |
| **The model does not calculate the plan** | Times, fares, tolls, fuel, parking prices and charging estimates come from the scripts. `SKILL.md` limits the agent to asking, verifying against live pages, explaining, totalling printed figures (both directions of a trip, or after a checked price), and manual estimates from the documented formulas when the scripts cannot run, each labelled; verified facts are reported with date and source, and the agent says which script figures they make stale. This is an instruction, not something the code can enforce. |
| **An estimate says it is one** | `ROUTING` says free-flow model or live traffic. `CAR` says given, saved or assumed. Parking carries official, curated, osm or stale, plus "may have closed" and a check line. A default never reads like a fact. |
| **Fails loud, never silent** | Every failed source becomes a `WARNING`; a blocked sandbox gets `NETWORK BLOCKED` with the hosts to allow; nothing crashes the plan. |
| **Polite to free services** | One request per second to OSRM, Valhalla and Nominatim; an identifying User-Agent; disk caches; one Overpass query at a time. |
| **Keys stay out of output** | API keys come from environment variables. TomTom and Ontario 511 require the key in the request URL, so it is sent there, but it is never printed, logged or used in a cache file name; NLR and Google Places get it in a header. |
| **Only legitimate data paths** | No scraping of sites whose terms forbid it (Google Maps, Impark, SpotHero, GasBuddy), no reCAPTCHA calculators, no stealth modes past bot protection; even the NRCan car-rating download sends the skill's own User-Agent and falls back to the bundled index if refused. Official APIs or a link. |
| **Works offline where it can** | Stop maps, hand-entered timetables, curated tourist-place parking, the post-2019 Green P carparks and the 17,520-row car index ship in the skill. A sandbox with no network still plans a trip whose timetables are all hand-entered or cached (pass `lat,lon` and `--routing estimate`), labelled `OFFLINE`; otherwise `NETWORK BLOCKED` lists the hosts and `--timetable` prints the bundled timetables. |
| **Portable** | Python 3.8+ standard library only; a built-in time-zone table for Windows Python, which has no tz database; one folder for every Agent Skills client. |
