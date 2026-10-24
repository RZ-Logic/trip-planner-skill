# Changelog

Releases of the trip-planner skill. Before shipping, each release was reviewed by fresh-context reviewer
agents (separate sessions with no part in the build) or an outside GPT review; agent findings were
re-checked by a verifier before anything was fixed. Counts are given where they were recorded.

## 1.0.0 (2026-10-24)

**Review:** an outside AI review of 0.9.9 (Muse) made 173 claims; verifiers checked each one against the code
at 988dc8a (42% true, 26% true but documented design, 25% partly, 6% false). The issues it found real are
fixed here, test first; numbers in brackets are Muse's item numbers. Two data notes need new source data,
not code: 20 of the 39 Ontario feeds are hand-entered timetables (fresh-1; marked and warned since 0.9.9),
and 43 of the 54 curated parking places carry no rate year (fresh-2; the driving data age warning below now
covers them). Its false items were not applied: the 407 flat_toll_max cap, the currency change to EV
network estimates, the --provider change, redirect query stripping and refusing an old GTFS cache. Tests: 1241 at 0.9.9, 1298 now, on Python 3.8 and 3.14. Drive
times are unchanged, except that an EV plan can stop at a different charger where a site mixes fast and
slow posts; transit times change only at untimed stops (below); the prices below can change which hub or
lot ranks first.

- **Hub parking prices like driving mode.** A park-and-ride hub's OSM charge tag is read with parking.py's
  rate parser for the car parked at the trip's start, so a weekday-only "$2/hr" tag is free on Saturday
  instead of $8 for 4 h. [9.c]
- **Error replies are not cached.** A Valhalla reply with no trip, or an OSRM reply whose code is not "Ok",
  sent with HTTP 200 is no longer cached for 24 h and replayed. A Toronto restrictions reply with no
  "Closure" list warns that closures were not checked instead of reading as none. [7]
- **Parking prices.** A periods rule with an evening flat rate no longer charges the evening twice. A stay
  partly in season prices its off-season part with off_season_rule or off_season_flat (it was free), is
  price unknown with off_season "unknown", and a flat price per entry is the arrival day's. Per-passenger
  fees and per-person child and senior prices get the rule's tax, as the vehicle price does; no shipped
  rule combines them, so shipped prices are unchanged. [4]
- **Fast chargers counted per power level.** One 350 kW stall beside four 50 kW posts is one fast port, not
  five, so such a site is no longer a "good" charger (100 kW, 2 ports). [5]
- **Preflight.** --check-network and the preflight report a 5xx page or a proxy's own error page as
  blocked, not reachable; a 4xx from the host still counts as an answer. [12.x]
- **Zone fares.** A feed's fare rules that use contains_id are still not read, and the run now warns that
  fares on trips they cover may be missing or wrong. [12.p]
- **Guards.** --temp-c rejects nan and inf like every other number flag [11]. A missing or broken bundled
  JSON file (costs, traffic, tolls, parking or a region file) stops drive_plan, trip_hubs and parking.py
  with one line naming the file; the EV and parking layers still carry on without theirs [11, 12.w]. An
  NLR record without a longitude is skipped [5]. With no usd_cad rate in tolls.json, US charger prices are
  still counted at par but the run warns [5]. An arrive-by leave-by time whose step back did not settle is
  flagged in WARNINGS when it still arrives late [11]. parking.py's command line prints the stale-cache
  warnings that drive_plan already printed [7].
- **Docs.** The README licence badge says code MIT, data see NOTICE; the plugin README lists the Google
  Places key among the optional keys; architecture.md counts 10 car presets plus a generic fallback; the
  0.9.9 line below says where 3.14 ran; the 407 account fee text follows tolls.json; driving.md says a 407
  piece whose ends snap to the same interchange is dropped. [docs-struct-5, pkg-2, docs-disc-2,
  docs-disc-4, 11]
- **Reply size.** drive_common.fetch refuses a reply over 100 MB instead of reading any size (the largest
  cached in testing is 3.6 MB). [11]
- **Cleanup.** parking.py uses trip_hubs' Easter and nth-Monday helpers instead of its own copies [9.a];
  trip_hubs.osm_lots, which only parses Overpass elements, is now parse_osm_lots, so it no longer shares a
  name with parking.osm_lots [9.b]; drive_plan's copy of trip_hubs' defaults for an "older trip_hubs" is
  gone (it had drifted and could not run) [9.k]. No change to results.
- **Google review rates.** A hotel price ("$90 for the hotel"), a fee on top named other ways ("$3 credit
  card processing fee", "$4 service charge", "$3 extra") and an older price ("back in 2023 it was $12", "$10
  two years ago") are no longer quoted as parking rates; "$15 parking fee", dropped before as a fee on top,
  now counts [10.a to 10.e]. A nearer weekday or weekend word beats an event word elsewhere in the sentence
  [10.m]. Reviews quoting the same rate are counted, "$15 per day (Sep 2026, 3 reviews)", instead of reading
  as one [10.h]. park_and_ride places are searched with the other Table A parking types [10.j]. Review
  quotes stay unverified and never become a cost.
- **Untimed stops placed by distance.** A stop the feed gives no time is placed by the feed's
  shape_dist_traveled, else by straight-line distance between stops, instead of evenly by stop count
  (stop count stays the fallback). Of 151 feeds cached in testing, 10 have untimed stops; the only Canadian
  one is VIA Rail's Winnipeg to Churchill line (up to 12 min at Budd). [12.a]
- **Driving data age.** A run warns once the bundled driving files (costs, tolls, traffic, parking, ev.json
  for an EV) were last checked more than six months ago, as region files already do; the README says the
  data is as of 2026-10-10. [fresh-3, docs-struct-3]
- **Feed errors.** A bug in trip_hubs.py while loading a feed is reported as one ("an error in
  trip_hubs.py, not in the feed", with the file and line), not as a broken feed. [12.v]
- **Park and ride contract.** A test runs the real car-mode plan and checks every key drive_plan's park and
  ride step reads. [9.l]
- **407 fee note.** tolls.py prints the account fee with its cents instead of rounding it ($5 today).

## 0.9.9 (2026-10-20)

**Review:** a ten-area review of 0.9.8 (reviewers, then one adversarial verifier per area) produced 110
verified findings, fixed in seven batches below, test first; IDs in brackets refer to that review. A second
review of the fixes (7 reviewers, 3 verifiers that reproduced every finding) confirmed 31 more problems, all
fixed. Six owner decisions were settled. Still partly open: data-integrity-12 (one 407 km-post conflict),
parking-10 (Blue Mountain P2 hours not published) and ten bundled coverage grids with no licence found
(listed in NOTICE). Tests: 850 at 0.9.8, 1241 now, run locally on Python 3.8 and 3.14 (CI runs 3.8 and 3.12).

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

### Batch 4: parking and curated data

- **Parking money.** "2 hours free", "free for the first 2 hours" and "1st hour" are read, and a
  first-hour price stays within the day maximum. Billing increments and block prices no longer restart at
  midnight. A rate that prices only some hours ("$5 after 6pm") shows price unknown outside them instead
  of free. [parking-9, parking-6, parking-7]
- **Curated lots.** Wellington Rotary Beach is priced on weekday statutory holidays as on summer
  weekends. Daily park fees (Fort George, Sandbanks, Bronte Creek, Sibbald Point, Tobermory, Wasaga Beach,
  Pinery) are charged per day of a multi-day stay. White Water Walk keeps the Niagara Parks overnight ban.
  Cobourg's waterfront lots carry their low-season rate (new off_season_rule key). Spencer Gorge charges
  its reservation fees and 4 h limit only from Sep 26 to Nov 8 and is price unknown outside it (the
  off-window fee is not published). [ext-14, ext-15, ext-16, data-integrity-1, data-integrity-4, parking-8]
- **Verification and ranking.** Low-confidence prices get a "price not confirmed" check line. Curated lots
  that only OpenStreetMap calls free are shown as unverified free. An old or unverified price ranks as if it
  cost 25% more, so a verified lot up to 25% dearer comes first; the docs describe this instead of
  "best-verified lots first". [ext-11, data-integrity-7, parking-4, docs-contract-2]
- **Offline Toronto.** Offline, downtown Toronto lists the bundled post-2019 Green P carparks with 2026
  rates, and an empty offline result says the offline data has no parking there. [parking-3]
- **Rural fares.** Distance- and zone-based rural fares (HSAT, PC Connect, GOST, LINX, Corridor 11,
  Middlesex Route 4) print as "about X" with the operator's fare scale; LINX Route 4 gets its own $2 fare.
  [data-integrity-6]

### Batch 5: docs, agent contract and security

- **Hub ranking counts the fare.** Ride-mode BEST TRADE-OFFS compares ride + fare, --min-saving applies
  to the total, a hub that riding all the way beats on cost and time is not listed, and an unknown or
  approximate fare counts as at least the dearest exact fare in the run. A same-vehicle hub with a lower
  total is no longer hidden. --all lines and JSON all_options carry each hub's fare.
  [architecture-product-2, docs-contract-3]
- **Ride traffic by area.** Hub rides use the same traffic.json rows as drives: outside the GTHA, Ottawa,
  Kitchener-Waterloo and London boxes the lighter rows apply (x1.03 by day, was the x1.45 Toronto rush
  hour), the weekday shoulders x1.3, and holidays the daytime row. [architecture-product-3,
  data-integrity-10]
- **Finish with a ride.** Each train line's stop nearest the destination is always tried, and a WARNING
  names stops that were not tried. [architecture-product-8]
- **Hand-entered and projected timetables.** Hand-entered feeds are marked on the FEEDS line; legs whose
  times are projected from an earlier schedule are marked "timetable assumed" with a WARNING; a region file
  verified more than six months ago raises a WARNING. Legs whose published times are faster than the road
  allows (GOST's 10:38 Guelph run, checked against the operator's page on 2026-10-19; two Onex weekend
  trips) are marked "times doubtful"; the operators' times are kept. [architecture-product-4,
  data-integrity-8]
- **Offline transit.** A lat,lon trip with --routing estimate whose timetables are all hand-entered or
  cached plans offline with a warning instead of stopping at NETWORK BLOCKED; NETWORK BLOCKED never lists
  an empty set of domains. [docs-contract-4]
- **Terms of service.** Check lines never point the agent at Impark or SpotHero pages taken from
  OpenStreetMap; the docs no longer recommend Scrapling and forbid stealth or anti-bot modes.
  [docs-contract-5, security-tos-9]
- **Park-and-ride trigger.** The automatic trigger and the "vs driving + parking" total no longer trust an
  unverified, old or unknown price on the top lot; they use the cheapest walkable lot with a checked price
  when that costs more, and say why. [docs-contract-verifier-1]

### Batch 6: test strength and engineering

- **Stronger tests.** New tests pin calendar_dates.txt added service (including Peterborough's
  Thanksgiving services), the GO fare path (batch 2), 407 money with and without a transponder and the
  round-trip return toll, rain and snow in the leave-by time, plan()'s booking and arrival margins, and
  the parking minimum charge (the old test passed without it) and overnight windows begun the evening
  before. Each was shown failing under a mutant of the code it guards. [tests-ci-eng-1, tests-ci-eng-3,
  tests-ci-eng-4, tests-ci-eng-5, tests-ci-eng-7]
- **Holidays.** One Ontario ESA holiday rule is shared by trip_hubs, drive_common and tolls; hub plans
  treat statutory holidays after the region lists end (from 2028) as holidays, for ride and car traffic
  and TTC commuter lots. [tests-ci-eng-6]
- **Malformed rows.** A malformed stop_times row (HH:MM, a line cut short) is skipped and counted in one
  warning instead of dropping the whole feed. [tests-ci-eng-10]
- **Network guard and pacing.** The CI network guard switches off proxy settings, so a local proxy can no
  longer carry a test's request out. Both planners share one per-host pacing clock, so Nominatim stays at
  one request per second. [tests-ci-eng-8, tests-ci-eng-9]

### Batch 7: low severity

- **Drive clock and TomTom.** Arrivals, 407 entry times (so the toll band) and leave-by times follow the
  wall clock across a daylight-saving change. With a TomTom key, the 407 entry time comes from TomTom's
  turn-by-turn times, not an even spread by distance (older cached TomTom replies are not reused). 13
  Hwy 407 interchange points were re-taken from OpenStreetMap; York-Durham Line to Whites Rd still
  disagrees with its published km posts and is noted in tolls.json. The drive back prices fuel and
  home-charged EV energy at its own forecast temperature. [drive-core-7, drive-core-8,
  data-integrity-12, ext-13]
- **Car lookup and EV.** "RWD"/"FWD" keeps only those versions, and electric Performance, GT, Rally and
  Plaid trims count as all-wheel drive; "PowerBoost" and "i-FORCE MAX" pick the hybrid, and an unmatched
  word caps confidence at medium. No pointless fast top-up near the start when charging later is no
  slower. EV park and ride prices the drive to the station at the home rate. [ev-vehicles-4,
  ev-vehicles-8, ev-vehicles-9, gem-G6A]
- **Park and ride and the 407.** A park-and-ride search that times out stops making requests. SKILL.md
  has the agent ask about --407-this-month (the $5 account fee can flip the 407 verdict). The README
  calibration table is computed from the current model and pinned by a test. [gem-G5B, docs-contract-6,
  drive-core-9]
- **Hub finder.** No false "NO service" warning after midnight when last night's trips are used.
  --footpath-m 0 no longer crashes, --radius-km must be above 0, negative waits are refused. Walking
  transfers far north of a feed's other stops are no longer missed. Totals within the 5% margin count as
  equal wherever they fall (no bucket edges). A bank-card tap where the agency takes none is priced at
  the cash fare. The Uber accuracy figures are described as a one-off check whose route list is not
  published, and same-vehicle hubs are listed only beyond the 5% margin. [hubs-engine-9, hubs-engine-10,
  hubs-pricing-io-12, hubs-engine-11, hubs-engine-3, hubs-pricing-io-11, architecture-product-10]
- **Network, cache and text safety.** NETWORK BLOCKED points to references/manual-method.md. A catalog or
  JSON/CSV reply that is not the expected format (proxy or login pages with HTTP 200) is never cached, and
  --refresh re-downloads the catalog. Text output drops invisible Unicode. Geocoding is cached on disk for
  30 days. Overpass retries close the error they drop. [docs-contract-8, hubs-pricing-io-9,
  security-tos-5, security-tos-3, security-tos-7, gem-G4]
- **Attribution and privacy.** Drive plans credit OpenStreetMap and Open-Meteo (CC BY 4.0) on the SOURCES
  line, and transit plans list each discovered feed's licence page. A lat,lon label lookup asks Nominatim
  at street level with 3 decimals, so a home pin no longer gets its house number back. [security-tos-6,
  security-tos-8]
- **Parking and region data.** Times without am/pm ("8:00-6:00") read as 8am to 6pm. Bluffer's Park and
  Jack Darling charge on holidays like weekends, and Bluffer's weekday evenings at the published $6
  maximum (an upper bound). Places with 2026 fees get a check note on later trips, and EV plans past the
  OEB rate period warn. Residual projected timetables are flagged. Notes files match the data.
  [parking-11, data-integrity-9, parking-10, data-integrity-11, data-integrity-5, data-integrity-13]
- **Docs against code.** A test checks every flag, label, version and reference path the docs name, and
  the zip contents. README coverage numbers match the published example, the unmeasured "under a second"
  is gone, and the Bay St row is labelled as the stand-in example. [docs-contract-9, docs-contract-7,
  docs-contract-11]

### Follow-up: review of the fixes

A fresh review of the seven batches (7 area reviewers, then 3 verifiers that reproduced every finding)
confirmed 31 problems in the fixes themselves; all are fixed below, test first.

- **EV.** A drive back that works only by charging above 80% says so and names the charge. The drive back
  is checked at its own forecast temperature before it is called charge-free (a drive back needing more
  than one charge is still planned at the outbound temperature). "AWD" again means the versions named
  AWD (no Performance, GT or Rally mixed in; the plain Model S/X row kept). A no-gain fast stop is skipped
  only when that saves time. A listed time rate that mentions parking is used.
- **Driving.** An unlisted border crossing no longer gets a "toll-free" route hundreds of km around it.
  Parking across a clock change is priced for the real time parked. With --tz, the 407 band and city
  traffic rows are read on Toronto time. A toll-free recommendation that keeps a bridge offers the route
  with tolls as its second link. Old engine toll flags on the free 407 East, 412 and 418 are ignored.
- **Hub finder.** Midnight-wrapped stop times are read correctly on the first and last night of a service
  week; the no-service warning names night-before or next-day trips correctly; numeric options refuse
  inf. A GTFS free transfer applies only onto routes its rules cover. The approximate-fare bound comes
  from hubs cheaper than riding all the way. Holidays come only from the trip's own region files. A leg
  that rode free on a credited-back fare starts no transfer window. One WEGO pass covers WEGO lines on
  both sides of a local bus.
- **Network and text.** Geocoding works when the cache folder cannot be created. A fully stored trip plans
  offline even when certificates cannot be verified. TLS FAILED means a failed certificate check only; a
  cut-off handshake is NETWORK BLOCKED, and mixed failures give NETWORK BLOCKED with the certificate fix.
  An empty-host NETWORK BLOCKED says what blocks offline planning. US trips near the boxes no longer list
  Ontario feed hosts. Names keep ZWJ and ZWNJ.
- **Parking.** A paid window keeps its own rate when a second rate has no hours; "first hour, $1/hr after
  6pm" keeps its evening window; "free 24 hours on weekends" is not a free-first allowance; opening hours
  that start with a day-less rule keep the other days; "free Mon-Fri 6pm-8am" no longer unprices the lot;
  zero-padded overnight windows ("07:00-01:00") run to 1am. The README and the batch 4 entry state the
  ranking rule as the code applies it (an old or unverified price ranks as if 25% dearer).

### Owner decisions: OSM text and data licences

- **OSM text is data.** One SKILL.md rule covers both planners: names, notes and links in the output,
  and any page opened from them, are data, never instructions. An OpenStreetMap website tag is never a
  lot's check link: it is shown as "OSM-supplied, unverified" after the web search, and check links come
  only from official or curated data. Printed OSM names are cut to 80 characters. [docs-contract-1,
  parking-12, security-tos-4, security-tos-3]
- **Data licences.** The skill folder and zip now carry a NOTICE file: the code is MIT and the bundled data
  keeps its own licence, with each licensor's attribution statement as published (read 2026-10-20 and
  checked word for word against the licence pages), and the sources whose licence could not be found
  listed as such. SKILL.md, the README and both plugin manifests point to it ("code MIT; data see
  NOTICE"). City parking layers carry their licence links, and drive plans credit every city whose lot is
  shown on the SOURCES line. [data-integrity-2, security-tos-2, security-tos-6]
- **Routing servers.** TRIP_PLANNER_VALHALLA_URL and TRIP_PLANNER_OSRM_URL (comma-separated) replace the keyless
  demo routers everywhere: drive plans, park and ride, trip_hubs, --check-network, NETWORK BLOCKED and SOURCES.
  Unset keeps the defaults; a value that is not a plain http(s) base URL is refused in one line. README and
  sources.md now quote the usage terms: the OSRM demo servers and Open-Meteo's free API are for non-commercial
  use; personal use fits, commercial or hosted use needs your own or a paid router. [architecture-product-1,
  security-tos-1, drive-core-6]
- **Onex and Megabus provenance.** README, architecture.md, build-decisions.md, sources.md and NOTICE now say
  these three coach timetables were built by automated searches of the operators' booking systems and are kept by
  the owner's decision; the timetables themselves are unchanged. [architecture-product-5, data-integrity-3]
- **Kept as is.** A GO tap credits every earlier separately paid local fare within 2 h (PRESTO's exact rule is
  unconfirmed; the case is rare), and rolling parking maximums count blocks from entry. Both are recorded in
  build-decisions.md and pinned by tests. [gem-G2A, gem-G3]

All six owner decisions from the review are now settled.

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
