# Changelog

Releases of the trip-planner skill. Before shipping, each release was reviewed by fresh-context reviewer
agents (separate sessions with no part in the build) or an outside GPT review; agent findings were
re-checked by a verifier before anything was fixed. Counts are given where they were recorded.

## Unreleased

**Review:** a ten-area review of 0.9.8 (reviewers, then one adversarial verifier per area) produced 110
verified findings. They are fixed in batches below, test first; IDs in brackets refer to that review.

### Batch 1: critical and high

- **EV battery by trim.** A looked-up EV whose NRCan range and rating imply a battery more than 10%
  smaller than its preset's (RWD, Standard Range, Light trims) plans with that smaller battery and a
  WARNING asking for --battery-kwh; fast charging stays the preset's. The 2017 to 2021 IONIQ Electric is
  no longer planned as an IONIQ 5, and an EV with no preset gets a battery scaled the same way.
  [ev-vehicles-1, ev-vehicles-2]
- **EV routes and the drive back.** A route whose charging (or drive back) cannot be planned is not
  recommended when another route's can. Round trips that need a destination charge plan the drive back
  from the charge the note advises (with a single-stall first charger's 20% reserve), show it as
  "(charged at the destination)", and price the top-up at the home rate instead of $0.
  [ext-1, ev-vehicles-3, ext-5, gem-G1S]
- **Unpriced tolls.** Tolls the routing engine reports that are not priced here (US toll roads, crossings
  missing from the list) no longer print as "No tolls": the route is marked "tolls not priced", the
  toll-free comparison is kept, and TRADE-OFF says so. A Canada-US drive over an unlisted crossing gets the
  30 min border allowance and a passport reminder. adding-a-region.md no longer says toll roads can be
  added to tolls.json. [drive-core-1, architecture-product-6]
- **Time zones.** New --tz flag for origins on another clock (Kenora: --tz America/Winnipeg); an origin
  outside the region files with no --tz gets a WARNING that Toronto time was assumed. [drive-core-2]
- **US border cities.** Detroit, Buffalo and other US places inside the Ontario boxes are no longer
  treated as Ontario: their feeds are discovered, rides use a generic estimate instead of Ontario Uber
  rates with HST, cross-border trips get a warning and final rides from across the border are left out.
  The country comes from a town-level lookup; offline, the boxes decide as before. [hubs-pricing-io-1]
- **No ride-hail at the origin.** Trips starting where the region file says there is no ride-hail
  (Sauble Beach, Tobermory) price the first ride and ride-all-the-way as a local taxi with a 30 min wait
  and no Uber link; a sparse origin gets a book-ahead warning. [hubs-pricing-io-2, architecture-product-7]
- **Book-by in traffic.** Depart-mode book-by times, the stretch connection, the arrive-by backup,
  finish-with-a-ride options and the short first ride are timed in the traffic at the pickup, not at the
  start time, so a book-by that falls into rush hour no longer misses the train. [hubs-engine-1]
- **Parking rate text.** A price written before its window charges that window; a night maximum with no
  hours no longer replaces the day maximum; "$2/hr Mon-Fri, Sat-Sun free" is paid on weekdays (a rate
  that frees every paid day shows as price unknown, never $0); "free evenings and weekends" is no longer
  inverted; "$3/hr, $10 after 5pm" charges the daytime rate. [parking-1, parking-2, parking-verifier-1,
  parking-verifier-2]
- **OSM opening hours.** Day forms, several rules, "off" and public holidays ("Mo-Fr 07:00-19:00; Su
  off") are read, so a lot closed during the stay no longer fits; hours that cannot be read show in the
  lot's notes and check line. [parking-5]

### Batch 2: transit engine and fares

- **GTFS fares.** Feeds priced from their own fare table honour transfers 1 or 2 and transfer_duration
  (durations under 30 min are read as a unit error; an unreadable value charges every leg "(before
  transfer discounts)"). A Niagara local bus next to a WEGO line no longer hides the $16 WEGO pass. Fares
  in two currencies are listed per currency and not added up. A local fare credited back at a GO tap no
  longer gives later legs its transfer window. New tests pin the GO fare path (15.7% card discount,
  cheapest rule, route rules, per-leg sums). [hubs-pricing-io-3, ext-4, ext-12, gem-G2B, tests-ci-eng-2]
- **Timetable reading.** Stop times that jump back past midnight are read as after midnight (with a
  warning) and other backward times skip the trip, so a bus can no longer arrive before it leaves. UTC
  feeds (FlixBus) show the right hour on the evening before a clock change. "Every N min" service is
  planned with the longest wait and shown as "every N min from X". A bus boarded inside the time window
  keeps its later stops. Station-level transfers.txt rules apply to every platform. With no region file,
  a UTC feed no longer sets the trip clock. [hubs-engine-4, hubs-engine-2, hubs-engine-7,
  hubs-engine-verifier-1, hubs-engine-5, hubs-engine-8]
- **Network messages.** A failed HTTPS certificate check stops with TLS FAILED and the fix instead of
  "address not found"; NETWORK BLOCKED names the cause it saw (DNS, timeout, proxy, refused). Blocked
  timetable hosts give NETWORK BLOCKED naming them, not "no transit departures" with a raw list.
  [hubs-pricing-io-4, hubs-pricing-io-5]
- **Geocoding and discovery.** The Photon fallback is biased to Nominatim's street and a house match more
  than 5 km away is refused; the label keeps the province. Discovery skips feeds that cannot be near the
  trip before downloading them (other-country town matches, out-of-reach catalog areas; at most 60 per
  run). [hubs-pricing-io-7, hubs-pricing-io-8]
- **Other.** The Transitous second opinion is asked with the plan's safety margin, so a bus inside the
  margin is not reported as a better connection. Final rides are timed from the drop point to the
  destination, not the reverse. trip_hubs output credits OpenStreetMap on a SOURCES line.
  [hubs-pricing-io-6, hubs-engine-6, ext-2, hubs-pricing-io-10]

### Batch 3: driving and EV

- **Stops on the clock.** Border waits and EV charging stops are placed on the drive's clock at the
  crossing or charger, so the 407 band and later traffic factors use the time after the stop (a 30 min
  wait before the 407 no longer prices the earlier, cheaper band). A crossing missing from the toll list
  has no position and is still only added to the arrival. [drive-core-3]
- **Links follow the recommendation.** A recommended route the router would not pick by itself (the
  "without 407" border route, the forced 407 option, an alternative) gets a Google via point, and a
  toll-free route that keeps a toll bridge no longer gets avoid=tolls. [drive-core-5]
- **Park and ride.** The station cheapest there and back is always listed, tagged "(cheapest there and
  back)". On one-way trips the comparison prices the drive back at its own 407 band and direction and no
  longer doubles a one-way border toll. [ext-8, drive-core-4]
- **EV charging.** When 80% cannot bridge a gap after a stop, stops may charge up to 95% where the next
  leg needs it (noted as slower than shown); when even that fails the advice is Level 2 on the way, not
  "leave with more charge". CHARGING shows the starting charge, marked "assumed" with a WARNING when the
  90% default is used. A listed price takes the DC rate when Level 2 and DC share a comma or "and" list,
  and an idle fee is no longer read as the charging rate. [ev-vehicles-5, ev-vehicles-6, ev-vehicles-7]

## 0.9.8 (2026-10-12)

**Review:** a code, test and security review by Sonnet and Opus agents raised 80 findings. Verifiers
confirmed 72, confirmed 8 in part, rejected none and found 11 more. All were fixed except one
data-source question, kept as is by decision. A review of the fixes found 7 more problems, fixed
before release.

- **Data freshness and safety.** A failed request never passes an old cached reply off as live traffic;
  any other cached stand-in is named in WARNINGS with its age. Error replies sent with HTTP 200 are not
  cached. An unwritable cache folder no longer crashes a run. API key headers are dropped on redirects
  to another host, and car-rating downloads stay on NRCan's hosts.
- **Parking.** Green P day-and-time ranges ("Sat 7am - Mon 7am") and rolling "any N hour" maximums are
  priced correctly. Rate text now reads free days and windows, OpenStreetMap day forms ("Mo-Fr"), clock
  times before a price, "max $X" and block prices ("$7 for 3 hours"). Opening hours past midnight work,
  and an off-street option is always kept in the list.
- **Fares and rides.** A leg boarded on a Milton Transit paper transfer follows the cash rules (Milton 21
  to Brampton to MiWay: $7.75, was $4.25). Arrive-by rides and final rides are priced at the traffic of
  their own start time. The TTC station list is closed to OpenStreetMap lots.
- **EV.** Fast chargers near the destination are never crowded out by closer Level 2 posts; no false
  "infeasible" plans from a charger reached near the charge limit; records merge per network; US Level 2
  prices show in US$; LEAF presets by model year and trim, including the 40 kWh car.
- **Car lookup.** A lookup with no year uses the newest trim; no silent match under another make; makes
  with spaces (Alfa Romeo, Land Rover) work; a car.json saved with a BOM loads.
- **Hub finder.** Canada/* time zones work without tzdata; a NOT SHOWN line counts hidden hubs; a
  warning when the window crosses a daylight-saving change; feed URLs with keys are redacted; control
  characters in feed data are not printed; download size caps.
- **Tests.** 850 tests, independent of today's date and time; every test file uses a temporary saved-car
  file and cache; CI fails any run that tries the network.
- **Docs.** SKILL.md cut to about 4,500 tokens, with the manual method in references/manual-method.md;
  plugin listings no longer promise cheap parking; `.env.example` and key setup instructions.

## 0.9.7 (2026-10-11)

**Review:** a third GPT review of the docs, 3 findings, all fixed.

- The number rule allows labelled manual-method arithmetic (rate card, 407 zone table) besides
  re-totals.
- The park-and-ride return check runs `trip_hubs.py` from the destination back to the station.
- The sentence on how flagged lots rank was narrowed to what the code does.

## 0.9.6 (2026-10-11)

**Review:** a GPT review of five docs (9 findings) and a three-lens agent audit of every doc claim against
the code and sources (44 confirmed), all fixed.

- Chargers across the US and Canada (NLR `country=all`); US listed prices converted to CAD.
- NRCan downloads send the skill's own User-Agent.
- Transitous attribution in the second opinion; `--crosscheck` off by default.
- Parking and destination chargers limited to the destination's country.
- Lots say why they do not fit the stay; 43 curated lots got structured no-parking hours.

## 0.9.5 (2026-10-11)

- The arrive-by trade-off says "lets you leave N min later", with the typical drive difference beside it.
- Uber accuracy stated per route: Uber's published averages were 1.02x the estimates on average, 0.90x to
  1.19x per route, across 12 GTHA routes.
- Real-trip origins in code, tests and docs replaced by a public stand-in point.

## 0.9.4 (2026-10-11)

**Review:** an outside GPT review of the README.

- README reworked user-first, with docs/architecture.md and docs/build-decisions.md.
- CI workflow on Python 3.8 and 3.12, Linux and Windows.
- "95% reliable" replaced by "conservative planning allowance".
- Map-app test of the Toronto web planners.

## 0.9.3 (2026-10-11)

- Park-and-ride options ranked by money plus time at your value of time; the cheapest is still shown.
- Tests never see the developer's real API keys.

## 0.9.2 (2026-10-11)

- `--car "YEAR MAKE MODEL"` looks up the NRCan Fuel Consumption Ratings (bundled index for 2010 to
  2026); premium and diesel priced as such; electric cars switch to EV mode.
- `--save-car`, `--forget-car`, `--no-saved-car`.

## 0.9.1 (2026-10-10)

- Parking verification: Green P lots known to be closed are dropped, possibly closed ones flagged, lots
  opened since 2019 added, and "check before you go" lines.
- Optional Google Places review leads through the official API.

## 0.9.0 (2026-10-10)

**Review:** a 22-agent review raised 107 findings; 104 were fixed.

- Driving mode, `drive_plan.py`: keyless routing with a calibrated time-of-day traffic model (TomTom with a
  key), 407 ETR tolls, border bridges, parking priced for the stay, fuel, weather, EV charging and park
  and ride.

## Before 0.9.0

The ride + transit hub finder (`trip_hubs.py`) and the GTHA and Ontario region files. Not itemized here.
