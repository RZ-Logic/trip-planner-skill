# Greater Toronto and Hamilton Area (GTHA): rider notes

Verified 2026-10-09 and 2026-10-10 (service counts from GO's GTFS feed published 2026-10-05). Feed list,
stop map and ride pricing live in `gtha.json`. This file covers what the script cannot tell you, and
gives enough data to plan by hand when the script cannot run. Re-check fares, on-demand hours and LRT
status if this file is more than six months old.

Trips beyond the GTHA (Lake Huron, Georgian Bay, Muskoka, Kingston, Niagara and more) also load
`ontario.json`; its notes are in `ontario.md`.

## Contents
1. Car-reachable GO hubs (weekday and weekend service)
2. On-demand services (not in any GTHA timetable feed)
3. Fares, transfers and ride-hail prices (with a GO fare table)
4. Service-pattern pitfalls
5. Live times and official planners
6. Feed gaps and quirks

## 1. Car-reachable GO hubs

Free MTO carpool lots at highway interchanges are the hubs map apps rarely suggest. All are fine for
ride-hail drop-off (open surface lots). "Every 30" is the typical gap from 08:00 to 20:00.

**Route 40 is signed 40, 40C, 40H or 40M, all day.** They are one route: every variant stops at
Dundas@407, Trafalgar@407, Square One, Pearson and the 407 Terminal. 40C and 40M continue past Richmond
Hill to Unionville GO; 40H and 40M add Humber College North. On a Saturday about half the departures
at Trafalgar@407 are 40C, overnight trips included. Tell riders "board any 40 (40, 40C, 40H, 40M)
toward Richmond Hill or Unionville" (or toward Hamilton for the other way).

| Hub (GO stop id, fare zone) | Weekday GO routes | Saturday and Sunday | Notes |
|---|---|---|---|
| Trafalgar Rd @ Hwy 407 P&R (02163, z13), Oakville | 22, 40, 41, 43, 47, 56 | 40 and 47 only. 40 toward Square One / Pearson / 407 Terminal every 30, 07:45 to 22:45; 47 toward Square One / 407 Terminal every 30, 05:24 to 23:52; both also every 30 toward Hamilton. Overnight a 40 or 40C about every 1 to 2 h. | 368 spaces. Square One in 13 to 23 min. Also an Oakville Ride On-Demand stop. |
| Bronte Rd @ Hwy 407 P&R (00138, z14), Oakville | 41, 47, 47G | 47 only, every 30: toward 407 Terminal 05:16 to 23:44, toward Hamilton 07:41 to 02:11. | 82 spaces. 47G ends at Erin Mills Transitway. Oakville Ride On-Demand hub since 2026-09-06. |
| Regional Rd 25 @ Hwy 401 P&R (02151, z24), Milton | 22, 25, 29, 48 | 25 and 29, each hourly both ways. 25 toward Square One 05:58 to 23:58; 29 toward Kipling via Square One 07:26 to 23:21. | 190 spaces. Served by Milton Transit. Square One in 35 to 45 min (29 about 35; 25 about 39; 25L via Toronto Premium Outlets about 45). |
| Dundas St @ Hwy 407 P&R (00139, z16), Burlington | 12, 40, 41, 43, 47 | 12, 40, 47. Route 12 (to Niagara Falls) starts here hourly. | Route 40 runs overnight too. |
| Hurontario St @ Hwy 407 P&R (00354, z22) | 33, 48 | 33 only, every 2 h (toward York Mills 08:10 to 22:10). | Brampton/Mississauga border. |
| Hwy 407 Bus Terminal (02673/02674, z19), Vaughan | 30, 40, 41, 47, 48, 52, 56 | 30, 40, 47, 52, 65, 68. 40 and 47 every 30. | At TTC Line 1 Highway 407 station. No free parking; drop-off only. |
| Square One / Mississauga City Centre (00132/00133, z20) | 19, 21, 25, 29, 40, 41, 47, 56, 94 | 19, 21, 25, 29, 40, 47, 94. 21 toward Union about every 30, 05:47 to 01:20. | Next to MiWay City Centre Transit Terminal. 40 and 94 overnight. |
| Winston Churchill Transitway (02635/02636, z21) | 25, 29 | 25, 29, each hourly both ways. | 300 free spaces. MiWay 36, 45, 109, 135. |
| Erin Mills Transitway (02214 to 02216, z21) | 25, 29, 41, 47, 56 | 25, 29 hourly; 47 every 30. | 300 spaces, 24 h, drop-off area. MiWay 46, 48, 109, 110. Not the same place as Erin Mills Town Centre. |
| Milton GO (ML / 00194, z24) | Train MI to Union 06:00 to 08:30 only (10 trains). Bus 21, 22, 27 | No trains. Bus 21 to Union hourly, 04:55 to 00:25. | Bus 21 to Square One takes about 70 min. |
| Meadowvale, Streetsville, Erindale GO | Train MI peak only. Bus 21 | Bus 21 only. | MiWay connections at each. |
| Bramalea GO (BE / 02728, 08041, z32) | Train KI both ways all day. Bus 30, 31, 32, 41, 47, 48, 56 | Train KI to Union every 30, 08:00 to 23:00. Bus 30, 31, 47. | Major 407-corridor bus hub. |
| Bronte GO, Appleby GO (BO / 00264, z14) | Train LW both ways all day | Train LW every 30 on normal weekends. **Many weekends in fall 2026 have no trains here (track work): check alerts.** | Last LW toward Union about 23:20. |

## 2. On-demand services (not in any GTHA timetable feed)

None of the GTHA GTFS feeds include on-demand zones, so the script never sees these. Check them by
hand when the origin is inside a zone: they can replace the ride leg for a transit fare.

| Service | Zones and hours | Booking | Reaches GO? |
|---|---|---|---|
| Milton Transit OnDemand (Spare) | 401 Industrial, Boyne, Derry Green. Zone boundaries are only published as map images; sources describe Boyne as between Louis St. Laurent Ave and Britannia Rd (some add Tremaine Rd and James Snow Pkwy), so confirm an address in the app. Weekdays 05:18 to 23:48, Sat 07:00 to 19:53, Sun 07:30 to 19:53. No service on statutory holidays. Book at least 40 min before service ends. | Milton Transit OnDemand app, Transit app, bookondemand.miltontransit.ca, 905-875-5417 | Milton GO is the transfer point in all zones. |
| Oakville Ride On-Demand (Spare) | North zone incl. Trafalgar/407 carpool lot: Mon-Fri 06:30 to 21:00, Sat 10:00 to 17:30, Sun 10:00 to 17:00, holidays 08:00 to 20:00. Palermo West/Bronte Creek incl. Bronte GO and Bronte 407 P&R: Mon-Fri 06:00 to 21:00. Southeast and Falgarwood zones serve Oakville GO. | Oakville Ride On-Demand app, 905-815-2020 | Yes: Oakville GO, Bronte GO, Trafalgar/407, Bronte/407. |
| Oakville Late Night | One trip, 23:40 Mon-Fri, Oakville GO to home. | App | Starts at Oakville GO. |
| YRT On-Request (Via) | Several zones incl. GO-station zones (e.g. Aurora GO peak hours). | YRT On-Request app, 1-888-762-2950 | Yes, the GO zones. |
| DRT On Demand (Via) | Rural Durham and urban stops more than a 10 min walk from a route. | DRT On Demand app, 1-866-247-0055 | Not stated. |
| Brampton Argo / Caledon pilots | Downtown Brampton pilot (weekdays 05:30 to 21:30), Caledon pilot from 2026-09-14. | Argo Transit app | Not confirmed. |

Not general public: Halton Hills ActiVan and Milton Access+ require registration. Burlington and
Halton Hills have no general on-demand service.

## 3. Fares, transfers and ride-hail prices

**GO adult single fares (CAD)**, from GO's fare tables (feed 2026-10-05). Same price both ways; any trip
within one zone is 4.40. Square One and Mississauga City Centre are the same GO stop. Pearson is
"Pearson Airport Terminal 1" (zone 31, buses 40 and 94).

| From hub (zone) | Union (02) | Square One (20) | 407 Terminal (19) | Bramalea (32) | Milton (24) | Oakville (13) | Pearson (31) |
|---|---|---|---|---|---|---|---|
| Trafalgar@407 (13) | 9.70 | 6.65 | 11.05 | 9.55 | 6.75 | 4.40 | 8.65 |
| Bronte@407, Bronte GO (14) | 11.10 | 8.75 | 12.05 | 11.75 | 8.10 | 4.40 | 11.10 |
| RR25@401, Milton GO (24) | 12.25 | 9.05 | 11.95 | 9.45 | 4.40 | 6.75 | 10.05 |
| Dundas@407 (16) | 12.30 | 10.05 | 13.40 | 12.95 | 9.65 | 5.90 | 12.35 |
| Hurontario@407 (22) | 10.40 | 6.85 | 9.10 | 6.05 | 5.30 | 7.50 | 8.85 |
| Hwy 407 Terminal (19) | 6.15 | 8.80 | 4.40 | 8.05 | 11.95 | 11.05 | 7.55 |
| Square One (20) | 7.55 | 4.40 | 8.80 | 5.90 | 9.05 | 6.65 | 5.30 |
| Winston Churchill TW, Erin Mills TW (21) | 9.80 | 4.40 | 9.05 | 8.75 | 6.30 | 6.10 | 7.45 |
| Bramalea GO (32) | 9.55 | 5.90 | 8.05 | 4.40 | 9.45 | 9.55 | 4.40 |

- **Paying by PRESTO, credit/debit tap or a phone wallet** takes 15.7% off GO fares (GO's page says
  "save 15%"). GO e-tickets do not count for One Fare. Children 12 and under ride free.
- **Weekend Day Pass** $10, online, unlimited GO travel on Saturdays, Sundays and holidays. It beats
  any single fare over about $10, or a round trip over $5 each way. It is an e-ticket, so TTC, MiWay,
  Brampton, YRT and DRT legs with it are not free under One Fare; Milton accepts GO e-tickets for its
  free GO connection.
- **Local adult fares 2026** (PRESTO / bank-card tap / cash): MiWay 3.50 / 4.50 / 4.50; Brampton
  3.55 / 4.75 / 4.75; Oakville 3.50 / 4.00 / 4.00; Burlington 2.85 / 3.75 / 3.75; HSR 2.85 / 3.75 /
  3.75; YRT 4.24 / 4.65 / 4.65; DRT 3.84 / 4.85 / 4.85; TTC 3.30 / 3.30 / 3.35; Milton 4.25 cash or
  3.85 Token Transit app. `gtha.json` holds these as `card`, `contactless` and `cash`; the script picks
  one with `--payment`. A bank-card tap gets the same transfers as PRESTO but often the cash price.
- **Milton Transit has no PRESTO or card readers.** Pay cash, a ticket or Token Transit; a PRESTO or
  bank card is only *shown* to the driver as proof for the free GO connection.

**Transfers (checked against the official pages 2026-10-10).** The script applies these
(`transfer_rules` in `gtha.json`), and each free leg in its output says which rule made it free.
"Card" means the **same** PRESTO card, PRESTO in a phone wallet, or bank credit/debit card tapped on
every leg; cash, PRESTO tickets and GO e-tickets never get One Fare. A window runs from the fare you
paid: a free transfer never restarts it.

- **"Free for 3 hours" is true only from a GO tap.** A one-way GO trip (any GO bus and train changes)
  is one fare for 3 hours from tap-on, and local buses after it are free within those 3 hours.
- **Every local agency gives 2 hours** from the first tap or fare (MiWay, Brampton, Milton, Oakville,
  Burlington, HSR, YRT, DRT), unlimited rides on that agency, any direction (YRT: the trip must end
  within the 2 hours). **TTC** gives 2 hours only to PRESTO and bank cards; a cash paper transfer
  covers one continuous one-way TTC trip.
- **Local bus, then GO**: the local fare is credited back when you tap GO within 2 hours of the first
  local tap (TTC, MiWay, Brampton, YRT, DRT, Oakville, Burlington; HSR takes its fare off the GO fare).
  A new 3-hour window then starts at the GO tap.
- **GO, then local bus**: free within 3 hours of the GO tap on TTC, MiWay, Brampton, YRT, DRT,
  Oakville, Burlington and HSR. Oakville, Burlington, YRT and DRT also accept a GO paper ticket;
  Brampton only when you come from a GO train station (Mount Pleasant, Downtown Brampton, Bramalea,
  Malton, Lisgar).
- **TTC and the 905 partners** (MiWay, Brampton, YRT, DRT): free both ways within 2 hours of the
  first tap. Tap again when leaving a TTC bus in Mississauga or north of Steeles.
- **Local to local** within 2 hours: MiWay and Brampton, MiWay and Oakville, Oakville and Burlington,
  Burlington and HSR, YRT and Brampton (anywhere), DRT and YRT (one way; pay again on the return).
  HSR and Oakville honour each other only by PRESTO or card. MiWay or Brampton to Milton works only
  on Milton route 21 (Argentia stops, Lisgar GO, Brampton's Zum Steeles stop at Winston Churchill):
  show your card and ask for a Milton transfer; Milton's paper transfer works the other way. Any
  other pair (e.g. MiWay and YRT) is a new fare. Paying cash, you hold the paper transfer of the
  agency you paid, and the next agency must accept that one.
- **Milton and GO**: the Milton ride is free whenever it connects to or from a GO train or bus at
  Milton GO (bus loop or Drew Centre) or the Hwy 401 / RR25 park and ride, at any hour. Show your
  PRESTO card, bank card, or a dated GO ticket, pass or e-ticket to the driver. Milton-only trips pay
  the normal fare. (Milton runs its timed GO connections on weekdays 05:30 to 10:00 and 15:00 to
  20:00; that is a service pattern, not a fare limit.)
- **UP Express** is not in One Fare: UP and GO are separate fares.

**Ride-hail prices.** `gtha.json` uses Uber's Ontario rate card (Toronto region = Toronto, Halton,
Peel) plus 13% HST with no uplift. Checked 2026-10-10 against Uber's own UberX one-month averages on
12 GTA routes: Uber's averages were 1.02x our estimates on average, individual routes 0.90x to 1.19x. Off-peak quotes are usually at
or a little below the estimate, Wait & Save 10 to 25% lower, surge higher. Trips that start in one
region and end in another (for example Halton to Hamilton or Guelph) add about $0.35/km; the script
applies this, and prices Hamilton and Guelph pickups on their own regional cards (from `ontario.json`).
Airport pickups use different rates, which the script does not model.

## 4. Service-pattern pitfalls

- **Milton GO line trains run weekdays only, peak direction only**: 10 trains to Union 06:00 to 08:30,
  10 back arriving 16:40 to 20:10. All other Milton-corridor service is GO bus 21 (hourly on weekends)
  and 27.
- **Weekday-only GO bus routes**: 22, 32, 38, 41, 43, 48, 56. See the weekend column in section 1.
- **GO uses the Saturday schedule on statutory holidays** (confirmed for Thanksgiving 2026-10-12).
  Milton OnDemand does not run on holidays.
- **Weekend track work.** Lakeshore West weekends in fall 2026 often have no trains at Bronte and
  Appleby (shuttle buses instead), and other lines have weekend closures too. GO's timetable data lags
  its service alerts by weeks, so for any weekend trip check https://www.gotransit.com/en/service-updates
  and trust the alert over the timetable.
- **Overnight**: GO route 40 (signed 40 or 40C) is the backbone along the 403/407 (about every 1 to
  2 h overnight at Trafalgar@407). GO 94 runs overnight on the Mississauga Transitway. TTC Blue Night routes run about
  01:30 to 05:30.
- **Hazel McCallion (Hurontario) LRT is not open in 2026.** No passenger date; 2028 or later. Never
  route via it.

## 5. Live times and official planners

- **GO live departures for any stop or station**:
  `https://www.gotransit.com/en/find-a-station-or-stop/<stop id>/routes-departures` (e.g. `02163` for
  Trafalgar@407, `ML` for Milton GO). Needs a browser with JavaScript. It shows only the next 5
  departures; later ones sit behind "Load More", and the route number behind each row's "Show
  details". Use it for status and delays of the chosen bus; take the planned times from the script,
  Transitous or the route's timetable.
- **Where's My Bus** is not a web page: text 89848 or call 1-888-438-6646 with the stop id.
- For trips days ahead, use published timetables: GO's PDFs per route at
  https://www.gotransit.com/en/trip-planning/seeschedules/full-schedules (check the effective date).
  MiWay's schedule pages need a browser with JavaScript.
- GO trip planner: https://www.gotransit.com/en/plan-your-trip
- GO service updates: https://www.gotransit.com/en/service-updates
- MiWay schedules and planner: https://www.mississauga.ca/miway-transit/schedules/
- Milton Transit Tracker (live buses): https://milton.tmix.se/anyride/
- Milton transit pages moved on 2026-10-07 to https://www.milton.ca/parking-transportation/transit/
- Triplinx is shut down. Do not send users there.
- GTFS-realtime with no key: MiWay https://www.miapp.ca/GTFS_RT/TripUpdate/TripUpdates.pb, Milton
  https://metrolinx.tmix.se/gtfs-realtime-milton/tripupdates.pb. GO has no public realtime feed in
  the catalogs.

## 6. Feed gaps and quirks

- **Milton Transit's feed is hosted by Metrolinx's vendor**, not on milton.ca:
  `https://metrolinx.tmix.se/gtfs/gtfs-milton.zip`. The same host serves about a dozen other small
  Ontario agencies (`gtfs-<town>.zip`), e.g. Orangeville, Orillia, Cornwall, Sarnia, Stratford.
- **Oakville and Brampton** publish only through ArcGIS (`www.arcgis.com/sharing/rest/content/items/<id>/data`),
  a shared platform admins rarely allowlist. Neither has another direct URL (Brampton's old brampton.ca
  link is gone). The MobilityDatabase mirror (`mdb-725`, `mdb-1994`) trailed the ArcGIS file by one
  day in August and September 2026, so `gtha.json` lists the mirror first and ArcGIS as the fallback.
- **Burlington** replaces its file about a month before each service period, so the official URL can
  miss today. The script falls back to older copies automatically and says so.
- **Oakville, Brampton, TTC and YRT** files end around 2026-10-31 / 11-01. If a WARNING says a feed
  has no service on the trip date, the replacement has not been published yet.
- **Grand River Transit** (Waterloo Region): official feed unreachable on 2026-10-09. Use the GRT
  planner.
- **Halton Hills** has no fixed-route local transit. Georgetown and Acton are served only by GO.
- **Brampton** declares the America/New_York time zone. It keeps the same clock as Toronto.
- **Stop map**: `gtha.json` stores each feed's stop cells (about 2 km squares). Rebuild with
  `python tools/build_coverage.py` from the repository when an agency adds service in a new area.
