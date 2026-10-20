# Driving mode: how `drive_plan.py` works and how to read it

`scripts/drive_plan.py` plans a drive in your own car. It is a trip-planning companion: Google Maps,
Waze or Apple Maps still do the turn-by-turn navigation. It answers what a map app does not:

- what the 407 ETR saves against what it costs;
- when to leave for a deadline, with parking and the walk counted;
- parking near the door priced for the length of the stay, verified prices ahead of similar unverified
  ones;
- where an EV should charge;
- whether to park at a station and take transit instead.

Standard library Python 3.8+. It reuses `trip_hubs.py` for geocoding, time zones, regions and holidays.

## Command

```bash
python3 scripts/drive_plan.py --origin "ORIGIN" --dest "DESTINATION" --depart "YYYY-MM-DD HH:MM" --stay-hours 3
```

| Option | Use |
|---|---|
| `--depart`, `--arrive-by` | leave at a time (`now` works), or be at the door by a deadline |
| `--tz America/Winnipeg` | the time zone for the times you give and see, for an origin outside the region files (default America/Toronto; a WARNING says when it was assumed). Arrival and parking times stay on the origin's clock |
| `--stay-hours N`, `--leave-at HH:MM` | how long the car is parked (default 2 h), or when you leave (on the trip day); prices parking and times the return |
| `--round-trip` | also prices the drive back after the stay (407 direction and time band change the toll) |
| `--transponder` | you have a 407 ETR transponder (default: you do not, so camera charges apply) |
| `--407-this-month` | no transponder, but you already drive the 407 this month, so the $5 account fee is not added |
| `--value-of-time 30` | $ per hour of your time, used for the one recommendation (default $30) |
| `--car "2022 Toyota Corolla"` | your car's official NRCan rating (gas, hybrid, plug-in or electric; premium and diesel priced as such); works offline from a bundled index of recent model years |
| `--save-car`, `--forget-car`, `--no-saved-car` | keep the car for later runs, delete it, or ignore it once. The file is `%APPDATA%/trip-planner/car.json` on Windows and `~/.config/trip-planner/car.json` elsewhere; `TRIP_PLANNER_CONFIG` overrides it (a path ending in `.json` is the file, anything else a folder). Printed paths show the home folder as `~` |
| `--car-class`, `--l-per-100km`, `--vehicle hybrid\|diesel` | fuel use without a lookup (default small SUV, NRCan medians); `--l-per-100km` wins over everything |
| `--fuel-price 1.85` | override the Ontario survey price ($/L) |
| `--ev-model NAME --soc 80` | EV presets: ioniq5, ev9, equinox, model-y, model-3, niro, mach-e, leaf (2026, NACS), leaf-plus (2019 to 2025, 62 kWh), leaf-40 (2018 to 2025, 40 kWh), generic. `--car` picks the LEAF preset by model year and trim; a 2011 to 2017 LEAF uses leaf-40 with its own 24 or 30 kWh pack, with a WARNING. A `--car` whose NRCan range and rating imply a battery more than 10% smaller than its preset's gets that smaller battery, with a WARNING. `--battery-kwh`, `--max-dc-kw`, `--kwh-per-100km`, `--nacs-adapter` override |
| `--max-walk-m 800`, `--height-m 2.1` | parking walk limit; garage clearance |
| `--park-and-ride auto\|on\|off` | also check driving to a station and taking transit (auto: GTHA destinations only, downtown Toronto or parking over $15 for the stay, a checked price when #1's has a check line; on: any destination) |
| `--weather auto\|clear\|rain\|snow`, `--temp-c` | auto uses the Open-Meteo forecast (16 days), else month normals |
| `--provider auto\|tomtom\|valhalla\|osrm` | routing engine (auto: TomTom with a key, else Valhalla, else OSRM) |
| `--json` | machine-readable output, every section |
| `--check-network` | lists the hosts it needs and whether this environment reaches them, then exits |

Addresses work like `trip_hubs.py` (the same `ADDRESS NOT PRECISE` rule). Pass `lat,lon` when you can.

## Reading the output

1. `DRIVE`, `ROUTING`: the trip, and how times were made. **Read the ROUTING line before you quote
   a time.** "Valhalla free-flow times x time-of-day traffic" means a model, not live traffic. Only
   "TomTom live traffic" is traffic-aware. With the model, tell the user to check Google Maps' live
   time before leaving.
2. `ROUTES`: A is the fastest, then the toll-free route when A has tolls, sometimes a third
   alternative. Each route shows:
   - "about X": the typical time;
   - "allow Y": a conservative planning allowance for a deadline (modelled, not a measured on-time
     rate);
   - the 407 ETR entry and exit, direction, time band and price, both with and without a transponder;
   - border bridge tolls;
   - an "unpriced toll" note when the engine reports tolls this planner does not price;
   - fuel or energy, and the total.
3. `TRADE-OFF`: **one recommendation** at the user's value of time, with minutes and dollars.
   Quote both, with their basis:
   - leaving at a time: "the 407 saves N min for $X": typical driving minutes;
   - arrive-by: "the 407 lets you leave N min later (typical drive about M min shorter) for $X". The N is
     the difference in leave-by times, which use the planning allowance, not a shorter drive;
   - arrive-by round trip: a combined figure, a later leave-by plus the drive back, as the output says.

   The user can then decide whether the time is worth the money. A route whose charging stops cannot
   be planned (or whose drive back cannot) is not recommended when another route's can; `WARNINGS`
   names it.
4. `ARRIVAL`: the leave-by time (arrive-by mode), or the expected arrival. Both include parking
   search, the walk to the door and any charging. Planning uses the "allow" time plus 5 minutes spare.
5. `PARKING`: up to five options in the text (eight in `--json`), ordered by stay fit, walk limit and
   verification tier, then price within the tier (see Parking below). The first lot is the best
   supported option, not proof of the lowest price. Read the source tag on each:
   - "official": city open data.
   - "curated": a hand-checked list of tourist spots with 2026 rates and seasons.
   - "osm": OpenStreetMap tags; a "free" there is unverified.
   - "stale": prices from data over two years old, such as Toronto's Green P carparks from 2019.

   Below the options come place rules (season, reservations), then the line saying posted signs
   override the data.
6. `PARK AND RIDE`: runs automatically only for GTHA destinations, when the destination is downtown
   Toronto or parking costs over $15 for the stay (`--park-and-ride on` forces it anywhere). When #1's
   price is unknown or has a check line (OpenStreetMap, old or unconfirmed data), the trigger and the
   "parking" in the driving total use the cheapest walkable lot that fits the stay with a checked price,
   when that is dearer, and the first line says so. If verifying puts a GTHA destination's parking over
   $15 and the section did not run, rerun with `--park-and-ride on`. When no hub beats driving it says
   so. It is the `trip_hubs.py --access car` search: drive to a station or park and ride lot, park (GO
   station parking is free, first come), take transit. Each option is costed there
   and back against driving there and back (the drive back priced at its own 407 time band and direction)
   plus parking.
7. `CHARGING` (EV only): the starting charge ("assumed", with a WARNING, when `--soc` is not given:
   90%), then stops with network, power, charge from and to, minutes and estimated cost. It also
   gives the arrival charge, whether the drive home needs a top-up, and chargers near the
   destination.
8. `INCIDENTS`: Toronto road closures along the route (City of Toronto feed). Highway closures need
   an Ontario 511 key; without one the line says to check 511on.ca.
9. `LINKS`: navigation to the chosen parking lot, not the door: Google Maps (fastest, and with
   `avoid=tolls` for the toll-free route; a route that is not the router's own pick, such as an
   alternative, the "without 407" route on a border trip, the forced 407 option or a toll-free route
   that keeps a toll bridge, gets a via point so Google follows it), Waze, Apple Maps (these two pick
   their own way). Also a Google Maps link with the departure or arrival time (desktop only,
   undocumented URL form), and the walk from the lot.
10. `WARNINGS`: every source that failed or was estimated. Disclose them.
11. `SOURCES`: data dates and credits. Quote the fuel survey week and the parking sources, and keep the
    OpenStreetMap credit (Valhalla or OSRM routes, Nominatim and Photon places) and the Open-Meteo
    credit (CC BY 4.0) when you quote the drive.

## How the numbers are made

**Driving times.** The keyless public endpoints this skill uses, the OSRM demo server and Valhalla on the
FOSSGIS servers, gave free-flow times only when tested on 2026-10-10: Valhalla returned the same time at
03:00 and 08:00. Traffic-aware times need a key; TomTom's free tier is one option (below).

Their free-flow times run slow on the 407 and on highways outside cities, so they are calibrated
first:

- Valhalla: x0.85 on the 407 and x0.88 on rural highways.
- OSRM: x0.75 and x0.85; the OSRM rural figure is an estimate.

The calibration was set against Google Maps typical ranges on 9 routes from Milton, and a test keeps
them within 15%.

Then each 2 km piece is scaled by the clock time it is driven at, and by where it is. The 407 has its
own rows, because congestion pricing keeps it moving at rush hour. Congested areas are the GTHA,
Ottawa, Kitchener-Waterloo and London (boxes in `driving/traffic.json`).

| Where | When | typical | allow |
|---|---|---|---|
| 407 ETR | weekday 07:00 to 09:30, 15:30 to 18:30 | x1.05 | x1.25 |
| 407 ETR | other times | x1.0 | x1.15 |
| congested | weekday 07:00 to 09:30, 15:30 to 18:30 | x1.45 | x2.1 |
| congested | weekday shoulders (06:00, 09:30, 14:30, 18:30 hours) | x1.3 | x1.7 |
| congested | other daytime, weekends and holidays | x1.2 | x1.45 |
| congested | 20:00 to 06:00 | x1.0 | x1.15 |
| elsewhere | daytime / night | x1.03 / x1.0 | x1.15 / x1.1 |
| elsewhere, getaway peak | leaving the GTHA Friday or a holiday eve 13:00 to 20:00; returning Sunday or a holiday Monday 12:00 to 20:00 | x1.15 | x1.4 |

The "allow" factors are a conservative planning allowance, modelled on FHWA's planning-time idea and TomTom's Toronto
congestion figures. They are a model, not measurements.

Rides to and from hubs in `trip_hubs.py` (and its `--access car` drives) use the same congested and
elsewhere rows, blended by the share of the straight line inside the areas, without the free-flow
calibration or the 407 and getaway rows (a table route has no geometry). That holds on trips planned
with the Ontario region files; elsewhere every drive uses the congested rows. Park and ride from
`drive_plan.py` passes one factor for its drive to the station.

- **Weather:** rain adds 15% and snow 40% (FHWA weather studies), and snow counts only at 1 C or
  colder. The adder applies only when the forecast is within 3 days; further out the forecast is a
  WARNING and the times are unchanged. A TomTom route leaving now gets no adder: live traffic already
  reflects the weather.
- **Border crossings:** each one adds a 30 minute planning allowance, an assumption since posted waits
  are often 15 to 60 minutes. Check the CBSA and CBP wait pages before leaving.
- **Stops on the clock:** the border wait is placed at its crossing and each charging stop at its
  charger, so the 407 time band and the traffic factors after a stop are for the clock time after it.
  A crossing missing from `tolls.json` has no known position, so its allowance is only added to the
  arrival.
- **TomTom:** with a key, times come from TomTom's traffic (live for now, historic for later dates),
  and "allow" adds 8 to 30%. The 407 entry time comes from TomTom's turn-by-turn times along the
  route. A round trip's drive back is asked from TomTom at its own departure time (one more call per
  route); if that fails, the return line says the time is reused from the drive there.
- **Daylight saving:** clock times follow the trip's time zone across a daylight-saving change: the
  drive's elapsed time is kept and the clock jumps, so an overnight drive on the change night arrives,
  enters the 407 and gets its leave-by at the real wall-clock time.
- **Cached replies:** replies are cached on disk. When a service does not answer, an older cached reply
  may stand in, and a WARNING names the service and how old the reply is. Live traffic never comes from
  an old copy; weather may be up to 6 h old, Toronto road restrictions 24 h, Ontario 511 2 h and
  OpenStreetMap parking 30 days. Error replies sent as HTTP 200 (an Overpass timeout remark, an ArcGIS
  error) are never cached, nor are replies that are not the expected JSON or CSV (a proxy or login page).

Without a TomTom key, when no route uses the 407 but the trip crosses the GTHA at peak or in a
getaway window, the planner makes one extra Valhalla request that forces the 407, so the trade-off
has a toll option to compare. With `TOMTOM_API_KEY` no forced-407 request is made: the 407 appears
only if TomTom's fastest route uses it; run again with `--provider valhalla` to see the 407
trade-off. On border trips the comparison route only discourages tolls, so it keeps the bridge, and
it is labelled "without 407".

**407 ETR.** Prices come from the official 2026 light-vehicle rate chart (12 zones, eastbound and
westbound, 8 weekday and 4 weekend time bands, effective 2026-01-01):

- **Time band:** the whole trip is priced at the band of the entry time.
- **Charges:** $1 trip toll charge on every trip; without a transponder, a $5.30 camera charge per
  trip, plus a $5 account fee in any month with a trip.
- **Holidays:** Ontario statutory holidays are priced as weekends.
- **Toll-free roads:** the provincial 407 East (Brock Rd to Hwy 35/115) has been free since
  2025-06-01, and Hwys 412 and 418 since 2022.
- **Entry and exit:** taken from the route's 407 geometry, snapped to the interchange list. The
  kilometre markers are good to about 1 km.
- **Accuracy:** an estimate from the rate chart. A 1 km error at an end is about $1 at peak rates.
  407 ETR's own calculator needs reCAPTCHA, so it is never called and these prices are not
  checked against it.
- **Later years:** dates after the 2026 rate year get a note.

**Border crossings.** Car tolls by direction are in `driving/tolls.json`: Peace, Rainbow, Whirlpool,
Lewiston-Queenston, Ambassador, Gordie Howe, the Detroit-Windsor Tunnel, Blue Water, Thousand Islands
and Ogdensburg-Prescott. A toll charged only in US dollars is converted at the Bank of Canada rate stored
in `tolls.json` and counted in the total, marked as approximate. A crossing missing from the list is
detected from the two ends' countries: the 30 minute allowance and a WARNING are added, the toll is not
priced.

**Fuel.**
- **Price:** the Ontario weekly fuel price survey (published Mondays), at the nearest of the survey's
  14 markets within 100 km of the origin, otherwise the Southern or Northern Ontario average. Outside
  Ontario, pass `--fuel-price`.
- **Consumption:** checked in this order:
  1. `--l-per-100km`.
  2. The car's own NRCan rating, from `--car` or the saved car. When several versions of a model
     differ, the median is used and the others are listed. With no model year, an old trim name moves
     to the newest rating of the same car when that trim still exists ("Honda Civic Si" gives the 2026
     Civic Sedan Si). A model is matched under another make only for a distinctive name, with a
     WARNING and at most medium confidence; a one- or two-letter name never is ("2022 Honda e" gives
     no match and Honda's model list). "RWD" or "FWD" keeps the versions named so (electric
     Performance, GT, Rally and Plaid trims count as all-wheel drive); "PowerBoost" and "i-FORCE MAX"
     pick the hybrid. A word no version carries (other than a trim grade) gives at most medium
     confidence.
  3. The class median from `--car-class`, or for a small SUV by default.

  For an electric car, `--car` gives kWh/100 km. Battery size and charging power come from the
  matching EV preset, and `--l-per-100km` is refused. When the car's NRCan range and rating imply a
  battery more than 10% smaller than the preset's (a Standard Range or RWD trim, or an EV with no
  preset), the battery is scaled to that, with a WARNING; charging power stays the preset's.
  `--battery-kwh` and `--max-dc-kw` override.

  City and highway figures are blended by the route's highway share. The result is 8% higher below
  5 C and 15% higher below -5 C; a round trip's drive back uses its own forecast temperature. The
  `CAR` line says which source was used, and says "assumed" when it is a default.
- **Fuel type:** cars rated on premium or diesel are priced with the survey's premium or diesel
  price.
- **What the total includes:** fuel only, no wear.

**EV charging.**
- **Stations:** public DC fast chargers within 2 miles of the route, from the NLR (ex-NREL)
  Alternative Fuel Stations API. That API carries the NRCan Canadian data and is queried with
  `country=all` (US and Canada; the API default is US only). Near the destination one search returns
  up to 200 stations, with a second DC-only search when that is cut short, so fast chargers are never
  crowded out by closer Level 2 posts. Records are merged per site and network, so a post never shows a
  neighbour's power. Coordinates sent to NLR are rounded to 3 decimals (about 110 m).
- **Energy use:** the NRCan highway rating, adjusted for cruising speed and temperature (winter range
  loss of roughly 30 to 40% at -7 to -25 C in the model, from the CAA winter road test with CBC and
  AAA figures).
- **Charge time:** from the car's power curve factor.
- **Stop choice:** charge only enough to reach the next good charger or the destination with a 15%
  reserve (20% below 0 C), never above 80% when a plan exists within it. When a gap after a stop is
  too long for 80%, stops charge up to 95% where the next leg needs it, with a note (charging above
  80% is slower than the times shown); when even 95% cannot bridge it, the warning says to use Level 2
  on the way. A charger reached within 3 points of the charge limit is never a stop.
- **Prices:** a station's listed price wins. Otherwise the network table is used (Ivy $0.69/kWh,
  Tesla, Electrify Canada, FLO, Petro-Canada per minute, and others; dated, mostly medium
  confidence), marked as an estimate. US networks (Electrify America, EVgo) get the generic
  estimate. A price a US station lists is in US dollars (DC and Level 2): it is shown as US$ and
  converted to CAD at the same Bank of Canada rate for the cost.
- **Home energy:** kWh used x $0.14 all-in (OEB time-of-use, estimate for 2025-11-01 to
  2026-10-31); a WARNING appears once the OEB rate period has ended.

**Parking.**
- **Sources, in order:**
  1. The curated places in `driving/parking-places-west.json` and `-east.json` (Grand Bend to
     Tobermory, Blue Mountain, Muskoka, Algonquin, Niagara, Toronto attractions, Prince Edward
     County, Kingston, Ottawa, conservation areas). They hold seasons, free fallback lots and
     reservation rules (Tobermory, the Grotto, Sandbanks, Conservation Halton).
  2. City open data, queried live: Burlington, Hamilton, Ottawa, Mississauga, Oakville, London,
     Kingston, Niagara Falls.
  3. Toronto Green P carparks (2019 prices, the last published) with quarterly occupancy, which flags
     lots that are often full.
  4. OpenStreetMap lots open to the public, in the destination's own country only: a lot across a border
     river (Goat Island, across from Table Rock) is close on a map but not walkable. Chargers near the
     destination are filtered the same way.
- **Pricing:** each lot is priced for the actual arrival time and stay length: paid hours, free
  evenings and Sundays, daily caps, minimums, free first hours ("first hour free", "2 hours free"),
  first-hour prices ("$5 1st hour, $3/hr after") within the day maximum, seasons and maximum stays. Also:
  rolling maximums (Green P "any 4 hour period" or "24 hour rolling": each block of that length costs
  at most the maximum), block prices ("$7 for 3 hours": one charge per started block; next to an hourly
  rate, "$15/24 hours" stays a cap; a stay across midnight is charged in units counted from arrival,
  not restarted at midnight), prices written before their window ("$2/hr Mon-Fri 8am-6pm;
  $1/hr Sat 9am-5pm"), free days and windows ("free Sat-Sun", "$2/hr Mon-Fri, Sat-Sun free", "free
  after 6pm and weekends", "free evenings and weekends"), OpenStreetMap day forms in rate text
  ("Mo-Fr"), and OpenStreetMap opening hours with day forms, several rules, "off" and public holidays
  ("Mo-Fr 07:00-19:00; Sa 08:00-17:00; Su off", past midnight too): a lot closed during the stay does
  not fit, and hours the script cannot read are shown in the lot's notes and on its check line. A
  night or overnight maximum with no hours is noted, never used as the day maximum, a price given only
  for some hours ("$5 after 6pm") leaves the other hours as price unknown, not free, and a rate that
  calls every paid day free is shown as price unknown, never free. One unreadable lot is left out
  with a WARNING; the rest of its source still counts.
- **OpenStreetMap searches** wait at most 60 s in total. After a timeout or a busy reply, later
  searches in the same run use only the disk cache.
- **Ranking:** lots that fit the stay come first, then lots within the walk limit. Within those, by
  verification tier:
  1. Priced lots, cheapest first, free verified lots on top. OpenStreetMap lots tagged free are
     unverified, so they count as costing $5 for the stay; lots named after a facility say "may be for
     facility users". An old or unverified price (osm, stale, low, Green P 2019, a past rate year)
     ranks as if it cost 25% more, so a verified lot up to 25% dearer comes first. Totals and the
     arrival target's price stay the real price.
  2. Priced lots that may have closed.
  3. Facility lots tagged free.
  4. Unknown price.
  5. Unknown price that may have closed.

  Then by price, confidence and walk. Walk time is 4.5 km/h over 1.3 x the straight line. When no
  off-street option (lot, garage, park and ride) made the list and one exists, the best one replaces
  the last option.
- **The arrival target** is the first lot in that order. A priced lot that may have closed therefore
  outranks an open lot with an unknown price, and can become the ARRIVAL and navigation target; the
  output then says it may have closed. The agent must check such a lot is open before recommending it
  or handing off its link. When the target lot is often full (over 85% peak occupancy), ARRIVAL names
  a fallback lot that is not flagged.
- **Arrive-by:** parking is priced from the typical arrival at the lot until the deadline plus the
  stay.
- **Not available:** commercial operators (Impark, SpotHero, Indigo, Precise ParkLink) have no open
  data, and their terms forbid scraping. Mention their apps as an alternative.
- **Green P in Toronto:** the open data is from 2019, so most prices are 2019 prices. Current weekday
  maximums are mostly higher; some weekend maximums are lower.
  - Carparks known to be closed are dropped (185 Queens Quay East in 2021, 130 Elizabeth St in
    2025).
  - Carparks opened since 2019 (for example St. Lawrence Market North, 92 Front St E) were added with
    the rates on their own pages as of 2026-10-10.
  - A 2019 carpark missing from Toronto's current occupancy data is flagged "may have closed".
    That data misses some open lots: it covers 149 of the 2019 carparks and no TTC or waterfront
    park lots, so those are never flagged, and live pages confirmed some others are open. A flagged
    carpark ranks below unflagged priced lots within the same stay-fit and walking group; it can still
    outrank a lot with an unknown price (see Ranking above).
  - Saturday specials ($1 an hour for the first 2 hours at some garages) are priced.
- **Check lines:** every option whose price is old, unverified, not confirmed or unknown, or that may
  have closed, gets a `check` field in `--json`. The text output lists the first three of these among
  the top five (always including #1 and the first may-have-closed lot) under the lots as "check before
  you go", each with:
  - the operator's page when known (Green P's pages are rendered with JavaScript, so open them in a
    browser tool);
  - a ready web search string (lot name, street, city).

  Commercial operators' sites (Impark, SpotHero) are never given as the page; use the search string.
  Verify the lot you recommend before recommending it (Verifying a lot, below).
- **Google reviews (optional key):** with `GOOGLE_PLACES_API_KEY`, one official Places API Nearby
  Search per run reads up to 5 reviews per nearby lot (Google picks them by relevance, not date) and
  keeps those from the last 18 months. It pulls out the rates the
  reviews mention and attaches them to the matching lot. Each rate shows its month, the reviewer and
  the review's own Google Maps link, and is labelled unverified. It skips tickets, fees, refunds, EV
  charging, past prices ("used to be") and reviews over 18 months old. A lot only gets another
  place's reviews when the address or a distinctive name matches. Review text is not cached, and
  Google Maps pages are never scraped.

**Verifying a lot.** Do this when the lot you would recommend, or a cheaper one above it, has a check
line. A lot flagged "may have closed" can rank high (a priced one outranks an open lot with an unknown
price) and can even be the ARRIVAL and navigation target; never recommend it, or hand off its link,
until its operator page or a search shows it is open.

1. Open the operator's own page with a tool that can fetch any URL (a plain HTTP or fetch connector, or
   a browser tool; never a stealth or anti-bot mode). The check line gives the URL when known. Green P's
   pages are rendered with JavaScript, so they need a browser tool; a plain fetch returns an empty page.
   If a page answers with a bot check, do not try to get past it: use step 2. For an unnamed
   OpenStreetMap lot the link is its map entry, not an operator page.
2. Otherwise, run a web search with the check line's search string. Green P's carpark pages show up in
   search results. For an unnamed OpenStreetMap lot with no search string, search
   `parking near <nearest street> <city> rates`, or report the lot as not checked. With
   `GOOGLE_PLACES_API_KEY` set, the script already adds rates from Google reviews of the last 18
   months; treat them as leads, not prices. Without the key, read reviews only in an ordinary search
   result, never by scraping Google Maps.
3. Report each checked price with its date and source, and say which lots you could not check.
4. When a check changes a price or the lot you recommend, the script's totals, walk, leave-by and
   navigation link no longer match. Re-total from the checked price and label the new total as
   re-totalled, give a Google Maps link to the checked lot
   (`https://www.google.com/maps/dir/?api=1&destination=LAT,LON&travelmode=driving`), and say which
   printed figures are now stale. Label checked prices as checked, not as script output.

**Park and ride.** `trip_hubs.py --access car` checks every station and park and ride lot within
driving range. It drives you there (fuel cost per km, or for an EV its energy at the home charging
rate, no pickup wait, 4 minutes to park and reach the platform), then runs the usual transit scan.
Hubs qualify when they have parking:

- GO stations: free first come, except the stations listed in `gtha.json`;
- TTC commuter lots: about $5 to $7 on weekdays (2025 press report; the TTC page lists no prices),
  free on weekends and holidays at most lots, no parking 02:00 to 05:00;
- MTO carpool and 407 park and ride stops;
- OpenStreetMap park and ride lots, and public parking beside rail stations.

The search always lists the hub cheapest there and back (drive both ways, hub parking once, fares both
ways), tagged `(cheapest there and back)`, even past `--top` or on the same train as a listed hub.

The car stays at the hub, and the script plans only the way there. Its there-and-back total doubles
the outbound fare, so it is an estimate until the return is checked. Before recommending a station, the
agent gets the expected return time and finds a trip back to the car: `trip_hubs.py --origin
DESTINATION --dest STATION --depart RETURN_TIME`, with the trip's destination as origin, the station where
the car is parked as destination and the expected return time as departure, reading its transit-only
baseline (or the operator's planner). It then checks the last departure and the lot's access hours, and
gives a return backup.

## Optional API keys

None is needed. Keys are read from the environment only. Never ask the user to paste a key into the
chat, and never write one into a file.

| Variable | Adds | Free tier | Sign up |
|---|---|---|---|
| `TOMTOM_API_KEY` | traffic-aware times (live, historic), traffic delays on the route | 20,000 routing requests a month, no card needed (TomTom's pricing page does not say what happens past the free tier) | developer.tomtom.com |
| `NLR_API_KEY` | charger search beyond the shared DEMO_KEY (documented 30 calls an hour and 50 a day per IP; the API reported 10 an hour in testing) | 1,000 an hour | developer.nlr.gov (free key signup) |
| `ON511_API_KEY` | Ontario highway closures and collisions along the route | 10 calls a minute | 511on.ca/developers/help (free account) |
| `GOOGLE_PLACES_API_KEY` | rates mentioned in Google reviews of nearby parking from the last 18 months, shown as unverified leads | review data bills in Google's top tier; needs a billing account | console.cloud.google.com (enable Places API (New)) |

Other environment variables:

| Variable | Effect |
|---|---|
| `TRIP_PLANNER_CONFIG` | where the saved car lives (see `--save-car`) |
| `TRIP_PLANNER_CACHE` | the cache folder (default `~/.cache/trip-planner`); if it cannot be created, replies are simply not cached. Geocoder results are kept 30 days |
| `TRIP_PLANNER_QUIET=1` | no progress lines on stderr; WARNINGS in the output are unchanged |
| `TRIP_HOME` | `lat,lon` or `lat,lon;label`: an origin or destination within 50 m of it is labelled (default "home") with no geocoder lookup. Other place labels are looked up with 4-decimal coordinates |

Text output shows control, line-separator and bidi-override characters from feeds, OpenStreetMap, NLR and
reviews as spaces and drops invisible ones (zero-width marks, tag characters, BOM); `--json` keeps the raw values.

## Hosts it calls (for a network allow-list)

| Job | Hosts |
|---|---|
| Geocoding | nominatim.openstreetmap.org, photon.komoot.io |
| Routing | valhalla1.openstreetmap.de, router.project-osrm.org, routing.openstreetmap.de; api.tomtom.com with a key |
| Fuel price | www.ontario.ca (redirects to prod-energy-fuel-prices.s3.amazonaws.com) |
| Car lookup (model years not in the bundled index; sent with the skill's own User-Agent, and on refusal the bundled index is used with a WARNING) | open.canada.ca, opencanada.blob.core.windows.net |
| Weather | api.open-meteo.com |
| Parking | overpass-api.de; ckan0.cf.opendata.inter.prod-toronto.ca; mapping.burlington.ca; spatialsolutions.hamilton.ca; maps.ottawa.ca; services6.arcgis.com (Mississauga); maps.oakville.ca; maps.london.ca; services1.arcgis.com and utility.arcgis.com (Kingston); services9.arcgis.com (Niagara Falls) |
| Charging | developer.nlr.gov |
| Incidents | secure.toronto.ca; 511on.ca with a key |
| Parking reviews | places.googleapis.com with a key |
| Park and ride | everything `trip_hubs.py` needs (`--check-network`) |

## No code execution, or the network is blocked

`NETWORK BLOCKED` means the sandbox cannot reach the geocoding or routing hosts. It lists every host
to allow (`--check-network` re-checks them). `TLS FAILED` is the certificate case: the hosts answer but
this Python cannot verify their certificates, so there is nothing to allow; the message gives the fix.

- **With addresses,** the script stops at geocoding and prints no parking. Its message says to pass
  `lat,lon` instead to still get the curated parking, and points here for the manual method.
- **With `lat,lon`,** the warning appears in WARNINGS and the script still prints parking from the
  curated tourist-place files and the post-2019 Green P carparks in `parking.json`, priced for an
  estimated arrival. When none is near the destination it says "no curated parking for this spot in
  the offline data", not that there is no parking.

Meanwhile, do the rest by hand and say it is manual:

- **Routes and times:** with a tool that can request any URL, ask the keyless OSRM or Valhalla route
  endpoint (`references/sources.md` section 3) for the route and a toll-free alternative, and label the
  times free-flow (no traffic). Otherwise give the user the Google Maps directions link, once with
  `avoid=tolls`, and ask them for the typical time range it shows; use the top of the range for a
  deadline, plus parking and walking. Never fetch Google Maps pages yourself.
- **407 toll:** use the zone table in `driving/tolls.json` (entry-time band, cents per km per zone,
  +$1, +$5.30 without a transponder), or send the user to 407etr.com's calculator.
- **Parking:** read the destination's entry in `driving/parking-places-*.json` (rates, season, free
  lots, reservations). Otherwise use the city's parking page, or Green P's app in Toronto.
- **Charging:** use the NRCan station locator or the car's own planner, with the energy rule above.

## Limits to disclose

- **Traffic:** without a TomTom key, times come from a model. It knows rush hours, not today's
  collision or closure.
- **Parking data:** prices change. Green P's are from 2019, and some town rates are from brochures.
  Posted signs win. OpenStreetMap "free" is unverified.
- **Tolls:** 407 tolls are an estimate from the rate chart. Billing can differ by a little at zone
  edges. Only the 407 ETR and ten listed border crossings are priced. Other tolls a routing engine
  reports (US toll roads, crossings not in the list such as Sault Ste. Marie) print as "tolls not
  priced", with a toll-free comparison and a WARNING; the OSRM fallback cannot see them.
- **EV:** charger availability and prices change. Busy sites add waits that are not modelled.
