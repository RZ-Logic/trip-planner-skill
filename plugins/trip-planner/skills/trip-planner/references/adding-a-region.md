# Supporting a new city

The script works anywhere without a region file: when no file covers the origin it discovers feeds
in the MobilityDatabase catalog automatically. A region file makes answers faster, more complete
and better priced. Write one when a city will be planned often.

## 1. Find the feeds

```bash
python scripts/trip_hubs.py --discover --origin "ORIGIN" --dest "DESTINATION" --radius-km 30
```

Check the list against the agencies that actually serve both ends of typical trips:
- Every local bus agency at both ends, plus the regional rail/bus operator.
- If a local agency is missing, search `"<agency> GTFS"`, its open-data portal, and vendor hosts
  (trilliumtransit.com, tmix.se, passio3.com, remix.com, ArcGIS items). See `sources.md`.
- National or intercity feeds (listed separately by `--discover`) are only worth adding if trips
  use them.

## 2. Check each feed

For each URL, confirm the file has service on upcoming dates. A plan run prints each feed's date
range in the `FEEDS` line and a WARNING when a feed has no service on the trip date. Some agencies
publish the *next* period early (Burlington, Niagara); add an older copy from the catalog's version
history as a fallback URL.

## 3. Write `references/regions/<id>.json`

```json
{
  "id": "example",
  "name": "Example metro area",
  "timezone": "America/Chicago",
  "country": "US",
  "dst_rule": "north_america",
  "utc_std_offset_hours": -6,
  "bbox": {"min_lat": 0, "max_lat": 0, "min_lon": 0, "max_lon": 0},
  "pickup_wait_min": 6,
  "notes_file": "example.md",
  "feeds": [
    {"id": "metro", "name": "Metro Transit",
     "urls": ["https://agency.example/gtfs.zip", "https://files.mobilitydatabase.org/mdb-0000/latest.zip"],
     "bbox": {"min_lat": 0, "max_lat": 0, "min_lon": 0, "max_lon": 0}}
  ],
  "ride_pricing": {"label": "UberX estimate", "currency": "USD", "base": 3.0, "per_km": 0.65,
                   "per_min": 0.30, "min_fare": 8.0, "booking_fee": 3.0, "tax": 0.0, "uplift": 1.0,
                   "source": "where the numbers came from, and the date"},
  "flat_fares": {"Metro Transit": {"card": 2.0, "cash": 2.5, "currency": "USD", "note": "adult"}},
  "transfer_rules": {
    "verified": "YYYY-MM-DD",
    "same_agency": {"Metro Transit": {"window_min": 120, "payment": ["card", "cash"]}},
    "pairs": [
      {"from": ["Commuter Rail"], "to": ["Metro Transit"], "window_min": 180, "payment": ["card"],
       "label": "free local ride within 3 h of the rail tap"}
    ]
  }
}
```

Rules:
- `name` in each feed is the label shown in output and the key for `flat_fares`.
- Region `bbox` decides when the file is used: when the origin is inside it (its rate card, time
  zone and fares lead) or the destination is inside it (its feeds load too). Feed `bbox` (from the
  catalog or the feed's `stops.txt`) decides which feeds load for a trip.
- `country` (ISO 3166-1 alpha-2, such as `CA` or `US`): a region file never claims a point that reverse
  geocoding (town level) places in another country, so a box may reach across a border. Such ends get feed
  discovery and a generic ride estimate, and final rides from across the border are left out. Without
  `country`, or offline, the box alone decides.
- `dst_rule` is `north_america`, `eu` or omitted (no daylight saving). It is only used when Python
  has no time zone database (common on Windows).
- `ride_pricing.booking_fee_tiers` (optional) is a list of `[max_km, fee]` pairs. Keep `uplift` at
  1.0 unless real quotes or the operator's published route averages say otherwise (see
  `sources.md` section 4); one quote is not enough to set it.
- `transfer_rules` (optional) takes free and credited transfers off the fare. Agency names match a
  feed's `name` or the GTFS `agency_name`. `same_agency`: a second fare on the same agency within
  `window_min` of the first boarding is free. `pairs`: a fare on a `to` agency is free (`action`
  `free`, the default) or the earlier `from` fare is credited back (`refund_from`, for "your local
  fare is refunded when you tap the train"), within `window_min` of the earlier boarding. Optional
  limits: `payment` (`card`, `cash`), `days` (0 = Monday), `hours` (`[["05:30", "10:00"], ...]`,
  local time of the transfer), `at_stops` (stop-name substrings at the transfer point) and `routes`
  (route short names). `paper_only` (agency names) lists agencies with no card readers on board
  (Milton Transit): a card rider pays them in cash, and a leg boarded on their paper transfer follows
  the cash rules at the next change. Without this block, multi-agency fares print
  `(before transfer discounts)`.
- Mark a broken feed with `"skip": true` and a `notes` explanation; the script warns when it matters.

### Keys for long trips and rural areas (see `ontario.json`)

- `"intercity": true`: an intercity region file. It is loaded with the origin's own region file on trips
  of 50 km or more that start or end inside its `bbox`.
- `holidays`: `[{"date": "YYYY-MM-DD", "name": "...", "stat": true}]`. A feed whose file does not mark them
  gets `"holiday_service": "none" | "sunday" | "saturday"` (default `as_feed`: trust the file), applied on
  the region's statutory dates (`"stat": false` dates are skipped) or, better, on the feed's own
  `holiday_dates` list (agencies differ: one runs on Easter Monday, another stops on Easter Sunday).
- `exclude_routes`: route ids, short or long names to drop from a feed (a train in the file that is not
  running yet).
- `board_buffer_min`: minutes a rider must be at the stop before a coach or train leaves (VIA, FlixBus,
  Megabus and Ontario Northland use 10). Transfers, book-by times and backups all count it, and the leg
  says "be at the stop by".
- Feeds whose `agency_timezone` keeps another clock (FlixBus publishes UTC) are shifted to the region's
  time automatically.
- **Hand-entered timetables** for operators with no public GTFS: a feed with `manual` instead of `urls`:
  `{"agency", "url", "phone", "timezone", "services": {ID: {"days": "1111100", "start", "end", "except": [dates],
  "extra": [dates]}}, "routes": [{"route", "name", "type", "trips": [{"service", "headsign", "stops":
  [[name, lat, lon, "HH:MM"], ...]}]}]}`. The script builds a small GTFS file from it. Add `verified`,
  `source` and `notes` (season, booking, anything assumed). Leave out request-only stops.
- `ride_pricing_areas`: ordered `[{"name", "boxes": [bbox, ...], "pricing": {rate card}}]`; the first area
  holding the pickup prices the ride, and `out_of_area_per_km` applies when the ride ends outside its
  boxes. `ride_pricing_outside`: the card for pickups in no area. `taxi_pricing_default`: `{base,
  per_km}` for places without ride-hail.
- `ride_areas`: `[{"name", "lat", "lon", "radius_km", "uber": "yes" | "sparse" | "no", "taxis": [{"name",
  "phone", "note"}], "taxi_rate": {base, per_km} | null, "taxi_wait_min": n, "note"}]`: what a final ride
  near each destination can use. `"uber": "no"` prices final rides as a taxi and allows 30 minutes for it
  to arrive (`taxi_wait_min` overrides).
- `tools/build_coverage.py` builds the stop maps (and boxes for hand-entered feeds); re-run it after
  changing feeds.

## 4. Write `references/regions/<id>.md`

Cover what timetables cannot show: car-friendly hubs (park and rides, stations with drop-off),
on-demand services with zones and hours, fare and transfer rules, peak-only or weekday-only lines,
projects not yet open, live-departure pages, and known feed quirks. Date every fact.

## 5. Test

Run a known trip at a few times (weekday peak, weekday night, weekend) and compare against the
operator's planner and `--crosscheck`. Add the case to `tests/` if it caught a bug.

## 6. Driving data for a new region (optional)

`drive_plan.py` works anywhere for routes, parking from OpenStreetMap and weather, and for EV
chargers in the US and Canada (NLR, queried with `country=all`). These files make it better in a region:

- `references/driving/traffic.json` `areas`: boxes for the congested cities, so peak factors apply
  there and not on rural roads.
- `references/driving/tolls.json`: the 407 ETR model (zone, direction and time band, priced at the
  entry time) and border bridges and tunnels, with a price per direction and a `canada_side`/`us_side`
  style pair of points to tell the direction. Other toll roads are not read from this file: the
  planner flags them as unpriced, and pricing one needs code in `tolls.py`.
- `references/driving/costs.json` `fuel`: a fuel price source and market coordinates (outside
  Ontario the user passes `--fuel-price`).
- `references/driving/parking.json` `city_layers`: open-data parking layers that answer an ArcGIS
  near-point query, with field mappings to the normalized rule (see `parking.py`).
- `references/driving/parking-places-*.json`: hand-checked tourist spots. Record the season, the
  free lots, the reservation rules, and lots located from OSM, never guessed. Put lot hours and
  no-overnight rules in the lot's rule as `no_parking` (for example
  `"no_parking": [{"days": [0, 1, 2, 3, 4, 5, 6], "hours": ["00:00", "06:00"], "all_days": true}]`;
  `"days"` defaults to Monday to Friday, `"all_days": true` keeps it on holidays, and
  `"season": ["MM-DD", "MM-DD"]` limits a winter ban), not only in its notes: notes do not affect
  ranking, so a lot that closes during the stay would still count as fitting it.
- `park_and_ride` in the region file, for `trip_hubs.py --access car`: which station agencies have
  free parking and the exceptions. An agency's `stations` list is closed: at its stations OpenStreetMap
  lots never create a hub off the list or beat the listed rule (TTC: Old Mill is not a hub, Wilson keeps
  its weekday rate). Agencies without a list (GO) still use OpenStreetMap lots.
- Parking rule periods in the driving data may carry `"roll_min": n`, which makes the period's `max` a
  rolling maximum: each n-minute block of the stay, from the first charged minute and across midnight,
  costs at most that much (`parking.py` docstring has the full rule schema).

Date every price and keep the source URL with it.
