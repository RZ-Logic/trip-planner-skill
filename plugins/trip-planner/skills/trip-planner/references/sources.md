# Sources: which website or dataset for which job

Verified 2026-10-09 to 2026-10-11; each table row gives its own check date where it has one. Prefer official and keyless sources. Respect each service's usage policy:
the free public services below are best-effort and rate-limited.

## Contents
1. Finding timetable data (GTFS) for any city
2. Routing and second opinions
3. Driving time and geocoding
4. Ride-hail prices and booking links
5. Live departures and disruptions
6. On-demand transit
7. Map apps: what they can and cannot do
8. Driving: traffic, tolls, fuel, parking, charging, incidents

## 1. Finding timetable data (GTFS) for any city

| Source | URL | Key | Notes |
|---|---|---|---|
| MobilityDatabase catalog (start here) | https://files.mobilitydatabase.org/feeds_v2.csv | No | About 4,600 GTFS entries worldwide, about 2,800 of them active (the script skips deprecated, inactive and development ones). Use `urls.latest` (a keyless mirror, `https://files.mobilitydatabase.org/<id>/latest.zip`), not `urls.direct_download`. Filter by bounding box *overlapping the ride area*, not by point-in-box (that misses neighbouring agencies). Older versions: `https://mobilitydatabase.org/feeds/gtfs/<id>`. `trip_hubs.py --discover` does all of this. |
| Transitland atlas | https://github.com/transitland/transitland-atlas | No | Lists some feeds and realtime URLs the catalog misses. The REST API needs a free key. |
| Vendor hosts | trilliumtransit.com, tmix.se, passio3.com, remix.com, rideralerts.com, ArcGIS `/sharing/rest/content/items/<id>/data` | No | Small agencies often publish through a vendor, so a search for the town's own domain finds nothing. Search `"<agency> GTFS"` plus these hosts. |
| US NTD GTFS weblinks | https://data.transportation.gov/api/views/2u7n-ub22/rows.csv?accessType=DOWNLOAD | No | Since Report Year 2023, FTA requires every NTD reporter with fixed-route service to publish a public GTFS feed (some have waivers; links can go stale). |
| Germany | https://download.gtfs.de/germany/free/latest.zip | No | Whole country, daily, CC BY 4.0. Large: pass it with `--feed`. Check its end date. |
| France | https://transport.data.gouv.fr/api/datasets?type=public-transit | No | National access point. |
| UK (England buses) | https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/all/ | No | 500 MB; regional files exist. |
| Netherlands | https://gtfs.ovapi.nl/nl/gtfs-nl.zip | No | Send an identifying User-Agent. |
| Switzerland | https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020/permalink | No | Yearly timetable. |
| Norway | https://storage.googleapis.com/marduk-production/outbound/gtfs/rb_norway-aggregated-gtfs.zip | No | Daily. |
| Australia (Victoria) | https://data.ptv.vic.gov.au/downloads/gtfs.zip | No | NSW needs a free key. |
| Statistics Canada transit network database | statcan.gc.ca catalogue 23260003 | No | Static GTFS (stops, routes, trips, stop times) for about 150 Canadian agencies in one product (edition 2025001, released 2025-01-31). Too old for current times; useful to find agencies and stop locations. |

## 2. Routing and second opinions

| Source | URL | Key | Notes |
|---|---|---|---|
| Transitous (MOTIS) | https://api.transitous.org/api/v6/plan | No | Global open router built on the same public feeds. **Terms** ([usage policy](https://transitous.org/api/), checked 2026-10-11): open-source, non-commercial projects only; best effort, and Transitous may stop serving any consumer; no published rate (the script spaces its calls 1 s apart). Contact the Transitous team before routing (`/plan`) at volume or across many users. Send a User-Agent (the script's names the app, version and contact). Show a visible link to https://transitous.org/sources/ (the script's `SECOND OPINION` output does). `trip_hubs.py --crosscheck` (optional, up to 4 `/plan` calls) queries transit only from the origin and transit from each of the top 3 hubs. |

**Transitous requests by hand** (times in UTC with a Z). Send them with a tool that can request any URL
(an HTTP, fetch or scraping connector, or a browser tool); built-in web-fetch tools often refuse URLs you
build yourself (claude.ai's does), and then the user has to open the prefilled URL.
- Transit only from an origin:
  `https://api.transitous.org/api/v6/plan?fromPlace=LAT,LON&toPlace=LAT,LON&time=YYYY-MM-DDTHH:MM:SSZ&detailedLegs=false&numItineraries=3`
  (add `&arriveBy=true` for a deadline).
- From a hub (default for every hub query):
  `https://api.transitous.org/api/v6/plan?fromPlace=LAT,LON&toPlace=LAT,LON&time=YYYY-MM-DDTHH:MM:SSZ&detailedLegs=false&numItineraries=1`
  with the hub's coordinates and the time you reach it. Do not add `maxTransfers=0` to make replies
  smaller: it returns direct trips only, and the [MOTIS API definition](https://github.com/motis-project/motis/blob/master/openapi.yaml)
  warns that low transfer limits can exclude the best journey. Shrink what you read instead. Add it only
  for a user who wants direct service, next to an unrestricted query. A stop id works as `fromPlace` too
  (`ca-on-Metrolinx-gotransit_02163`); prefixes are not predictable, so look one up with
  `https://api.transitous.org/api/v6/map/stops?min=S,W&max=N,E` (each result's `stopId`) or
  `https://api.transitous.org/api/v1/geocode?text=NAME&type=STOP&place=LAT,LON`.
- `detailedLegs=false` drops leg geometry and turn steps but not `intermediateStops`, so every stop of
  every leg is still listed and `numItineraries=3` can run 30 to 60 KB. Read only the start and end
  times and, per leg, mode, times, from, to, route and headsign. If a code tool is available, save the
  reply to a file and extract those fields; otherwise keep the itinerary count low.
- Transitous may return more itineraries than asked, including detours that take hours (for example
  via a downtown terminal). Ignore any itinerary far slower than the best one.
- Do not use `preTransitModes=CAR_DROPOFF`: it is experimental, can return 450 KB replies, and often
  plans a disguised ride-all-the-way trip. Enumerate hubs with the script or the region notes and query
  each hub instead.

Operator trip planners (for example GO, MiWay) are authoritative for that operator's own legs.

## 3. Driving time and geocoding

| Source | URL | Key | Notes |
|---|---|---|---|
| OSRM demo | https://router.project-osrm.org/table/v1/driving/... | No | Coordinates are lon,lat. Free-flow times (no traffic). Max about 1 request per second. |
| FOSSGIS OSRM | https://routing.openstreetmap.de/routed-car/table/v1/driving/... | No | Same API; fallback. 1 request per second, attribution required. |
| Valhalla (FOSSGIS) | https://valhalla1.openstreetmap.de/sources_to_targets | No | POST JSON; models turn costs, slightly slower times than OSRM. |
| OSRM route (manual method) | https://router.project-osrm.org/route/v1/driving/LON1,LAT1;LON2,LAT2?overview=false&alternatives=3 | No | One route's free-flow `duration` (seconds) and `distance` (metres) per alternative. For a toll-free route use Valhalla's `/route` with `"costing_options":{"auto":{"exclude_tolls":true}}`. |
| Nominatim | https://nominatim.openstreetmap.org/search | No | 1 request per second, identifying User-Agent, cache results. Often returns only the street for suburban house numbers. |
| Photon | https://photon.komoot.io/api/ | No | Often finds the house when Nominatim only finds the street. The script sends Nominatim's street as `lat`/`lon` bias parameters and does not use a house more than 5 km from it. |

If geocoding returns only a street, the origin can be off by kilometres, which changes which hubs
are in range. Ask the user for a dropped pin or `lat,lon`.

## 4. Ride-hail prices and booking links

- **There is no official public price API.** Uber's price endpoint needs approval and its terms
  forbid price comparison; Lyft's developer program is closed. Do not scrape app internals.
- Estimate with the region's rate card (`ride_pricing` in the region JSON), then tell the user to
  confirm in the app. Upfront prices already include sales tax. Off-peak they sit close to the
  published rate card plus tax (GTHA, checked 2026-10-10: Uber's own averages ran 0.90x to 1.19x of it,
  about 1.02x on average; Uber's one-month average per route, and Uber does not say which hours or
  surcharges it includes); surge raises them.
- **Calibration source**: Uber publishes average UberX prices per city pair (past month):
  `https://www.uber.com/global/en/r/routes/<from>-<state>-<country>-to-<to>-<state>-<country>/`
  (e.g. `milton-on-ca-to-mississauga-on-ca`). Use several pages to fit or check a region's rate card;
  never scrape the app.
- Prefilled Uber link (rider logs in to see the price):
  `https://m.uber.com/looking?pickup=<url-encoded JSON {"latitude","longitude","addressLine1"}>&drop[0]=<same>`.
  The script prints one per option.
- Uber app link: `uber://riderequest?pickup[latitude]=..&pickup[longitude]=..&dropoff[latitude]=..&dropoff[longitude]=..`
- Lyft app link: `lyft://ridetype?id=lyft&pickup[latitude]=..&pickup[longitude]=..&destination[latitude]=..&destination[longitude]=..`
  (Lyft no longer books on desktop web.)
- Generic estimate where no rate card exists: North America about 9 + 1.2 per km all-in (floor
  about 12, local currency); Europe base 1.5 to 3, 0.9 to 1.5 per km, 0.20 to 0.40 per minute.
- Surge: about 1.0 off-peak, 1.1 to 1.2 weekday evenings and late weekend nights, 1.3 to 1.5 weekday
  AM peak and bar close, 1.5 to 2.5+ for events, storms and holidays.

## 5. Live departures and disruptions

- Find realtime feeds in the same catalog: rows with `data_type=gtfs_rt` and `static_reference`
  pointing at the static feed id; `entity_type` `tu` = trip updates, `sa` = alerts.
- Decoding needs the `gtfs-realtime-bindings` package, so in practice check the operator's live
  departures page or app for the chosen stop.
- Times from static GTFS are scheduled times. Say so when no live check was possible.

## 6. On-demand transit

Fewer than 100 feeds worldwide publish on-demand zones (GTFS-Flex), so assume they are missing.
To detect a service: search `"<town>" on-demand transit` (also "on-request", "microtransit"), check
the agency's schedules page, and look for apps by Spare, Via or RideCo. Exclude registration-only
paratransit. List on-demand services as a possible first leg with hours and zone, never with a
computed arrival time.

## 7. Map apps: what they can and cannot do

Tested 2026-10-11 on the Toronto web planners (Milton GO Station to Square One, Tue 21:00), plus each
app's documentation, and 2026-10-12 on the phone apps (Milton Community Park to Square One, weekday 21:00).

| App | Ride + transit in one plan? | Agent-openable link |
|---|---|---|
| Google Maps | **Yes in the phone app** (2026-10-12): "mixed modes" offered two plans, a Lyft ride to the Bronte Rd @ Hwy 407 Park & Ride then the GO 47, and a Lyft ride to Trafalgar Rd then the GO 40C. The price shown ($8.75 and $6.65) was only the GO single-ticket fare: no Lyft ride, and no PRESTO discount (PRESTO pays $7.38 for the first). The plan the script ranked first (MiWay 109 from Winston Churchill at Eglinton) was not offered. No in the Toronto web test (2026-10-11): transit options were bus-only, with no ride tab. | `https://www.google.com/maps/dir/?api=1&origin=LAT,LON&destination=LAT,LON&travelmode=transit` (or `driving`). No documented departure-time parameter. |
| Apple Maps | No: ride booking is a separate option that hands off to the ride app. | `https://maps.apple.com/?saddr=..&daddr=..&dirflg=r` (legacy) |
| Transit app | Yes in certain cities: Transit+ combines ride-hail, bikeshare and microtransit with transit (help article updated 2026-07-02). Ontario example: Durham Region Transit On Demand. No in the GTHA phone test (2026-10-12): other options were bike or Uber all the way. | No documented deep link |
| Citymapper | No in the Toronto web test or the phone test (2026-10-12): Lyft and Uber appear only as cabs on their own. Combined "Cars + Transit" routes were documented in 2016 for other cities. Covers 115 regions. | `https://citymapper.com/directions?startcoord=LAT,LON&endcoord=LAT,LON` (documented; `arrival_time` optional) |
| Moovit | No in the Toronto web test: transit only, and the "Personal Cars" filter planned no drive leg. Uber was added to its trip planner in 2016 (Toronto listed); current status unknown. Covers 3,500 cities. | `moovit://directions?orig_lat=..&orig_lon=..&dest_lat=..&dest_lon=..&date=ISO8601` (app only) |

Use map apps to check a single leg (drive to the hub, transit from the hub), never to choose the hub or
to price a ride-plus-transit plan.

## 8. Driving: traffic, tolls, fuel, parking, charging, incidents

Used by `drive_plan.py`; how each is applied is in `driving.md`. "Checked" is the date the linked page or
endpoint was read or called. In Notes, **Terms** marks a provider's policy (linked), and **Observed** marks
what a request did on the checked date, which is not a policy statement.

| Job | Source (evidence) | Access | Checked | Notes |
|---|---|---|---|---|
| Traffic-aware routing | TomTom Routing API ([calculateRoute docs](https://docs.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route), [pricing](https://docs.tomtom.com/pricing)) | free key, 20,000 requests a month, no card | 2026-10-10; live 2026-10-11 | `computeTravelTimeFor=all` gives live, historic and free-flow times; `departAt` or `arriveAt`; `avoid=tollRoads`; no toll prices |
| Other keyed routing | HERE v8 ([pricing](https://www.here.com/get-started/pricing)), Mapbox ([Directions](https://docs.mapbox.com/api/navigation/directions/)), Google Routes ([computeRoutes](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes), [RouteModifiers](https://developers.google.com/maps/documentation/routes/reference/rest/v2/RouteModifiers)) | keys | 2026-10-10 | not used by the script. HERE prices tolls with transponder fares but needs a card; Google Routes has no driving arrive-by and no 407 pass |
| Keyless routing | Valhalla on FOSSGIS ([API reference](https://valhalla.github.io/valhalla/api/route/api-reference/)), OSRM demo ([usage policy](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server)) | keyless, about 1 request per second | 2026-10-10 | **Observed:** these two endpoints returned free-flow times only (Valhalla gave the same time at 03:00 and 08:00); Valhalla `exclude_tolls` gave a toll-free route; OSRM refused `exclude=toll`, so `alternatives=3` is used |
| 407 ETR tolls | [407 ETR light-vehicle rate chart](https://www.407etr.com/en/rate-chart-light) | page | 2026-10-10 | **Observed:** the calculator API asks for reCAPTCHA, so it is never called |
| Other tolls | [407 ETR rates page](https://www.407etr.com/en/rates) (407 East is provincial and free); bridge and tunnel operators' pages, one per bridge in `driving/tolls.json` | pages | 2026-10-10 | 412 and 418 have been free since 2022 |
| Gas prices | [Ontario fuel price survey](https://data.ontario.ca/en/dataset/fuels-price-survey-information) CSV, weekly, 14 markets; [Statistics Canada 18-10-0001-01](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810000101) as a monthly cross-check | keyless | 2026-10-10 | **Terms:** [GasBuddy](https://www.gasbuddy.com/disclaimer) forbids scraping, so it is not used |
| Consumption | [NRCan Fuel Consumption Ratings](https://open.canada.ca/data/dataset/98f1a129-f628-4ce4-b24d-6f16bf24dd64) (gas, plug-in and battery-electric CSVs) | open data | 2026-10-10 | bundled index for 2010 to 2026 in `driving/vehicles-index.json`; class medians in `driving/costs.json`. Other years are downloaded from open.canada.ca (opencanada.blob.core.windows.net) with the skill's own User-Agent; if that is refused, the bundled index is used with a WARNING |
| Weather | [Open-Meteo forecast](https://open-meteo.com/en/docs) | keyless, 16 days | 2026-10-10 | rain and snow adders (applied within 3 days), temperature for fuel and EV range |
| Parking, Toronto | [Green P carparks 2019](https://open.toronto.ca/dataset/green-p-parking/), [parking occupancy](https://open.toronto.ca/dataset/parking-occupancy/) (quarterly) | keyless | 2026-10-10 | **Observed:** the live Green P site returned bot-protection pages to scripts; its carpark pages render with JavaScript |
| Parking, cities | ArcGIS layers of Burlington, Hamilton, Ottawa, Mississauga, Oakville, London, Kingston and Niagara Falls (each layer URL in `driving/parking.json`; each city's open data licence applies) | keyless near-point query | 2026-10-10 | Kingston and Niagara Falls give locations only; Oakville and London data date from 2018 |
| Parking, anywhere | OpenStreetMap via Overpass ([usage notes](https://wiki.openstreetmap.org/wiki/Overpass_API)) | keyless; identifying User-Agent | 2026-10-10 | **Observed:** curl's default User-Agent got HTTP 406 and a second parallel query got 429. Only about 13% of Ontario lots carry a fee tag: good for free lots, weak for prices |
| Parking, tourist spots | towns, Niagara Parks, Ontario Parks, Parks Canada, conservation authorities (source URL per place and lot in `driving/parking-places-*.json`) | pages, curated | 2026-10-10 | check seasons and reservation lead times |
| Parking, commercial | Impark, SpotHero, ParkWhiz/Arrive, Parkopedia, HONK, Indigo, Precise ParkLink | none open | 2026-10-10 | **Terms:** [Impark](https://impark.com/terms-of-use/) forbids robots and scraping; SpotHero's robots.txt disallows `/api/`; [ParkWhiz/Arrive](https://developer.arrive.com/getting_started) is partner-only. **Observed:** no public API found for HONK or Indigo; the Precise ParkLink lot finder returned 403. Link out |
| Parking, verify one lot | the operator's own lot page (Green P carpark pages appear in web search; Precise ParkLink lot pages open directly) | pages, one at a time | 2026-10-11 | use search results, a browser tool or the Green P app; never stealth modes |
| Parking, review leads | Google Places API (New) [Nearby Search](https://developers.google.com/maps/documentation/places/web-service/nearby-search) with reviews | key with billing | 2026-10-10 | **Terms:** follow Google's [attribution and caching policies](https://developers.google.com/maps/documentation/places/web-service/policies); never scrape Google Maps. Reviews are leads, not prices |
| Park and ride | [GO free parking](https://www.gotransit.com/en/free-parking-at-go-stations), [TTC parking](https://www.ttc.ca/riding-the-ttc/parking), MTO carpool lots (found by transit stop names containing "park & ride", "park and ride" or "carpool"; not queried from 511), OSM `park_ride` | mixed | 2026-10-10 | GO publishes no machine-readable lot list |
| EV chargers | NLR (ex-NREL) [Alternative Fuel Stations API](https://developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/) ([rate limits](https://developer.nlr.gov/docs/rate-limits/)), queried with `country=all` (US and Canada; the API default is US only) | DEMO_KEY: documented 30 an hour and 50 a day per IP; free key 1,000 an hour | 2026-10-10; rate limits 2026-10-11 | US and Canada only. **Observed:** the API reported a DEMO_KEY limit of 10 (X-Ratelimit-Limit, 2026-10-11); the old `developer.nrel.gov` host no longer resolved; Open Charge Map returned 403 without a key; OSM charging data is sparse |
| Incidents | [City of Toronto road restrictions](https://open.toronto.ca/dataset/road-restrictions/) | keyless | 2026-10-10 | **Observed:** the feed contains invalid JSON escapes; filter client-side |
| Incidents, highways | Ontario 511 API ([developer help](https://511on.ca/developers/help)) | free key, 10 calls a minute | 2026-10-10 | **Terms:** [robots.txt](https://511on.ca/robots.txt) disallows the site's internal JSON, so it is not used |
| Navigation handoff | [Google Maps URLs](https://developers.google.com/maps/documentation/urls/get-started), [Waze deep links](https://developers.google.com/waze/deeplinks), [Apple Maps unified URLs](https://developer.apple.com/documentation/mapkit/unified-map-urls) | no key | 2026-10-10 | **Observed:** Google's departure-time URL form is undocumented; it worked on desktop web only |

