# Ontario getaways: rider notes

Checked 2026-10-10. The machine-readable side is `ontario.json` (an intercity region file): the script loads it
on trips of 50 km or more that start or end in Ontario, together with the origin's own region file (for example
`gtha.json`). This file covers what the script cannot see. Re-check seasons, holiday rules and fares if this file
is more than six months old.

## Contents
1. How the script plans these trips
2. Intercity services (what is in the data, what is not)
3. Ride-hail and taxis outside the GTA
4. Holidays
5. Destinations: West (Lake Huron, London, Stratford)
6. Destinations: North-west (Georgian Bay, Bruce Peninsula)
7. Destinations: North (Muskoka, Algonquin, Simcoe)
8. Destinations: East (Kingston, Thousand Islands, Prince Edward County, Kawarthas, Ottawa)
9. Destinations: South (Niagara, Lake Erie)

## 1. How the script plans these trips

- It loads GO, VIA, Ontario Northland, FlixBus and the local systems near both ends, plus hand-entered
  timetables for operators that publish no data (Huron Shores Area Transit, PC Connect, Middlesex County
  Connect, Blue Mountain Link, Grey Transit Route 5, GOST, Huntsville and Bracebridge Transit, Corridor 11,
  Megabus east, Wolfe Island ferry, Port Hope, Brockville and Peterborough Transit, WEGO 604, Ride Norfolk).
- Long trips get a search window sized to the drive (up to 9 h). Plans that cannot reach the door by
  transit show under **FINISH WITH A RIDE**: transit to a drop point, then Uber or a local taxi.
- Hand-entered timetables carry a `verified` date and a `notes` line in `ontario.json`, and the FEEDS
  line marks them `hand-entered`. A leg marked "timetable assumed" and its WARNING mean the times are
  projected from an earlier schedule: confirm on the operator's site before relying on them. A leg
  marked "times doubtful" uses the operator's published times, but its WARNING names two that cannot
  both be right (GOST's 10:38 Guelph run at Williamsford and Chatsworth; Onex's weekend 20:50 and 21:55
  from Toronto between Milton and Kitchener): check with the operator.

## 2. Intercity services

| Operator | In the data? | Notes |
|---|---|---|
| GO Transit | Yes (GTFS, holidays encoded) | Saturday schedule on holidays. Weekend and holiday trains to Stratford (from Union, Mount Pleasant, Guelph, Kitchener) and Niagara Falls. Stratford tickets: e-ticket only. |
| VIA Rail | Yes (GTFS) | One trip per train number with fixed days; the file does not show holiday or seasonal changes and ends 2026-12-17. Corridor (Toronto-Kingston-Ottawa) schedule changes from 2026-10-25. Fares are dynamic (Escape fares from about $25 to $60): book on viarail.ca. |
| Ontario Northland coach | Yes (GTFS) | Toronto-Barrie-Orillia-Gravenhurst-Bracebridge-Huntsville-North Bay and Toronto-Parry Sound-Sudbury. The Northlander train is in the file but not in service yet (excluded). |
| FlixBus | Yes (GTFS, UTC times converted) | Toronto-London-Sarnia, Toronto-Niagara Falls, Toronto-Kingston-Gananoque-Ottawa, Toronto-Muskoka-North Bay, Toronto-Barrie-Wasaga-Collingwood-Blue Mountain-Owen Sound, Peterborough. Sauble Beach/Port Elgin only to 2026-10-19; Blue Mountain ski trips 2026-12-07 to 2027-03-21. Prices vary; book ahead. All FlixBus times in this file are local Toronto time; the feed publishes UTC (subtract 4 h from UTC times before 06:00 UTC on 2026-11-01 and 5 h from then: a coach shown at 02:00 UTC on Nov 1 leaves at 22:00 on Oct 31). |
| Megabus (Coach Canada) | Hand-entered (`megabus-on-east`) | Toronto, Pearson, Scarborough to Kingston, Brockville, Ottawa (2720/2730, some continue to Cornwall and Montreal, not modelled) and Mississauga-Pearson-Yorkdale-Port Hope-Trenton-Belleville-Napanee-Kingston (2711). From the live booking API 2026-10-10. Toronto-Niagara Megabus was reported cancelled in June 2026. Online fares only. |
| Megabus (Badder Bus) 2740 | Hand-entered (`megabus-on-west`) | Toronto Union to London Wellington Rd S and Western University, 4 daily trips each way plus 4 Thu-Mon; about $27 (range $10-49). Booking open to 2027-01-05. |
| Onex | Hand-entered (`onex`) | London-Kitchener (Sportsworld)-**Milton GO carpool lot**-Mississauga (800 Boyer)-Pearson-Toronto (44 York St); Toronto-Ottawa via Kingston (Fri-Sun); Brampton-Toronto-Niagara Falls-Buffalo Airport. From Milton: to London 10:50 Mon-Fri, 11:50 Sat-Sun, late evenings; to Toronto 03:45 and 19:05. Flex fare about $27, online only; be at the stop 10 min early. |
| Rider Express, Robert Q, Trailways | No | Not in the data (no current public timetable). Rider Express (Mississauga to London) can fill gaps; check its site. |
| Parkbus | Only the Pinery day trip (2026-10-24) | Seasonal park buses (Algonquin, Bruce Peninsula, Killbear, Arrowhead) sell out and publish dates late. |

## 3. Ride-hail and taxis outside the GTA

- **Rate regions (Uber Ontario addendum 2024-02-22).** The script prices a ride by where it starts:
  Toronto region (GTA plus Simcoe, Grey, Dufferin, Kawartha Lakes: Barrie, Collingwood, Blue Mountain,
  Owen Sound), Hamilton, Kitchener-Waterloo (with Guelph, Brantford, Oxford), London (Middlesex, Elgin,
  Lambton incl. Grand Bend and Sarnia), Niagara, Kingston, Ottawa, Windsor. **A ride that starts in one
  region and ends outside it adds $0.35/km for the whole distance** (Milton to Guelph or Hamilton, Kingston
  to Gananoque). Checked against 39 of Uber's own route averages: within about 15%.
- **No rate card, ride-hail sparse or absent:** Huron (Goderich, Bayfield), Bruce (Tobermory, Lion's Head,
  Sauble), Perth (Stratford), Muskoka (an Uber pilot since 2026-08-24, few drivers), Leeds and Grenville
  (Gananoque), Prince Edward County (uses the Uride app), Peterborough, Norfolk (Port Dover). The script
  prices these with the London card, or a local taxi estimate (about $1.90/km) where Uber does not
  operate, and says so. The destination's ride area in `ontario.json` lists local taxis with phone numbers.
- **Getting back** is the hard part in sparse areas: book the return ride ahead (Uber Reserve or a taxi)
  before leaving the gateway town.

## 4. Holidays

Statutory holidays to 2027-12 are listed in `ontario.json` (`holidays`); later years use the Ontario ESA rule
(`trip_hubs.esa_holidays`) for ride and drive traffic and station parking prices, not for the feed corrections
below. Feeds that do not mark them are corrected per feed (`holiday_service`): London Transit runs a
Sunday-like schedule; Sarnia, Stratford, Orillia and Simcoe County LINX run nothing. GO, Barrie, Owen Sound,
Kingston, Belleville, Quinte, Niagara, Midland and North Bay files already mark holidays. VIA and Ontario
Northland run their normal timetables. Rural services in the hand-entered timetables list their own holiday
dates (`except`).


## 5. Destinations: West (Lake Huron, London, Stratford)

### Grand Bend
- **Why go / best season:** Lake Huron sand beach, the Main St strip, sunsets and Huron Country Playhouse. Best late June to Labour Day; May and October are quiet shoulder months.
- **Gateways:** Sobeys Plaza hub (HSAT stop 2000) and the Welcome Centre at 81 Crescent St. From London, board at Fanshawe Rd E at Masonville (LTC 705). From Sarnia, board at Lambton College or Lambton Mall.
- **How to get there from the GTA:**
  - **Via London:** take VIA to London (Toronto 06:50 arrives 09:11, per GTFS on 2026-10-10) or FlixBus to London. Then take LTC to Masonville and HSAT Route 2 at 10:41 (arrives Grand Bend 11:48) or 18:46 (arrives 19:53).
  - **Via Sarnia:** VIA 87 reaches Sarnia at 22:24, too late for HSAT. Use FlixBus or MCC Route 4 to Sarnia, then HSAT Route 1 from Lambton College at 07:05, 13:48 or 17:58 (about 1 h 55 min).
- **Last mile:**
  - HSAT Routes 1 and 2 run daily, year-round. Route 1 runs 3 trips each way, Route 2 runs 2.
  - Fares: $6 local, $12 long distance (Sarnia or London to Grand Bend). Pay cash (bring exact change) or with an HSAT Smart Card. Walk-on.
  - The Route 2 Beach Bus (London 09:30, 11:50, 17:00, 19:20; Sat-Sun) was pre-booked online at $12. It ran 2026-06-27 to Labour Day weekend and is over for 2026. The 2027 dates (about 2027-06-26 to 09-05) are assumed from the 2026 pattern.
- **Ride-hail and taxis:**
  - Uber is sparse: Lambton Shores is covered 24/7 but waits are long. London to Grand Bend UberX averages about $84.
  - Grand Bend Taxi: 519-238-7433 (Fri-Sat until 03:00, other days until about 20:30).
  - Star Taxi: 519-238-7827.
- **Pitfalls:**
  - There is no HSAT service on Christmas Day, New Year's Day or Good Friday.
  - The Route 1 Seasonal Express was discontinued 2026-07-27.
  - Last bus to London: Masonville 18:46 is the last bus into Grand Bend. The last bus out of Grand Bend to London is 17:15.
  - Last bus to Sarnia leaves Grand Bend at 20:03 (arrives 22:00).
  - With only 2-3 trips a day, one mechanical cancellation can strand you. Check huronshoresareatransit.ca/service-alerts.
  - The London stop at University Dr at Sunset (intercity coaches at Western) is on request only. Book it 2 business days ahead at 519-243-1400 x8108.
- **Sources:** https://huronshoresareatransit.ca/schedules/route-1-sarnia-grand-bend/ ; https://huronshoresareatransit.ca/schedules/route-2-london-grand-bend-via-exeter/ ; https://huronshoresareatransit.ca/schedules/route-2-seasonal-express-bus/ ; https://huronshoresareatransit.ca/rates/

### Pinery Provincial Park
- **Why go / best season:** Dunes, oak savanna and 10 km of Lake Huron beach. Beach season is July-August. The oaks colour late, so late October is the last leaf trip of the year.
- **Gateways:** HSAT stop 1500, Lambton Heritage Museum, is across Hwy 21 from the main gate (about 8 min walk). The Visitor Centre and beaches are 3-6 km inside the park. The Parkbus drop-off is near the Park Store and Visitor Centre.
- **How to get there from the GTA:**
  - **Parkbus:** runs one date only, Sat 2026-10-24. It leaves 34 Asquith Ave (Bloor-Yonge) at 08:00 (be there by 07:45) and reaches the park at about 11:00. It leaves the park at 16:30 and is back about 19:30.
  - Parkbus fares: adult $90, student/senior $81, child $45, park admission included. Book online; you get 90% back if you cancel more than 14 days ahead.
  - **Otherwise:** take the London or Sarnia route as for Grand Bend. HSAT Route 1 passes the gate at 08:46, 15:30 and 19:39 (from Sarnia) and at 09:24, 16:07 and 20:17 (toward Sarnia).
- **Last mile:** There is no shuttle inside the park; walk or bike (HSAT racks take 2 bikes). Route 2 from London does not pass the gate. Ride HSAT to the Sobeys hub, then Route 1, or take a taxi (about 9 km).
- **Ride-hail and taxis:** Uber is sparse (Lambton Shores). Grand Bend Taxi: 519-238-7433. Star Taxi: 519-238-7827.
- **Pitfalls:**
  - Parkbus has a single 2026 autumn date, and there is no refund within 14 days.
  - Summer day-use permits sell out on weekends, so buy them online.
  - On Route 1 the last bus toward Sarnia passes the gate at 20:17.
- **Sources:** https://www.parkbus.ca/destinations/pinery ; https://huronshoresareatransit.ca/schedules/route-1-sarnia-grand-bend/

### Sarnia
- **Why go / best season:** Blue Water Bridge, Canatara Park beach, the Point Edward waterfront and the St. Clair River boardwalk. Best May to September.
- **Gateways:** Sarnia VIA station. Bayside Terminal on George St (FlixBus, MCC Route 4). Lambton College and Lambton Mall (HSAT Route 1).
- **How to get there from the GTA:**
  - VIA 87 leaves Toronto at 17:40 and reaches Sarnia at 22:24. VIA 84 leaves Sarnia at 08:40 and reaches Toronto at 13:38. That is one train each way per day.
  - FlixBus route 2702 Toronto-London-Sarnia stops at Bayside.
  - From London, MCC Route 4 runs daily: Fanshawe College 07:56, 13:02 and 18:05; downtown London (YMCA) 08:15, 13:21 and 18:24; about 2 h 10 min to Bayside. Its fare is distance-based, $5-$20.
- **Last mile:**
  - Sarnia Transit has GTFS (tld-4738). Adult cash fare is $3.25. Canatara Park is stop 7256.
  - Sarnia Transit runs no service on stat holidays (Thanksgiving, Christmas, Boxing Day and others), even though its GTFS shows trips.
- **Ride-hail and taxis:** Uber yes, 24/7. Local trips are about $10-12; Sarnia to London is about $128. Taxis were not researched.
- **Pitfalls:**
  - The only VIA train arrives at 22:24.
  - MCC Route 4 does not run on statutory or civic holidays. Its last bus to London leaves Bayside at 15:37.
  - Sarnia Transit runs nothing on 2026-10-12.
- **Sources:** https://www.middlesex.ca/schedules ; https://www.sarnia.ca/2026-holiday-schedule/ ; https://www.flixbus.ca/bus-routes/sarnia-london-on

### Bayfield
- **Why go / best season:** Historic Lake Huron village with Main St shops, a marina, a beach and sunsets. Best June to September.
- **Gateways:** HSAT stop 3200, Bayfield Town Hall on Clan Gregor Square.
- **How to get there from the GTA:** Get to Grand Bend first (see above). Then take HSAT Route 3, which runs Mon/Wed/Fri only: Grand Bend 12:00 and 14:40, arriving Bayfield 12:49 and 15:29. The London to Bayfield fare is $12; to Goderich it is $20.
- **Last mile:** HSAT Route 3 runs Mon/Wed/Fri, year-round, 2 trips each way. It stops in Bayfield northbound at 12:49 and 15:29, and southbound at 13:40 and 16:20. Cash or Smart Card, walk-on.
- **Ride-hail and taxis:** No Uber; there is no page for Bayfield or Bluewater. Goderich Taxi & Delivery: 519-524-6594 or 1-800-939-2882 (flat rates outside Goderich).
- **Pitfalls:**
  - There is no weekend transit.
  - On a Mon/Wed/Fri same-day trip you must reach Grand Bend by 12:00 or 14:40.
  - The last southbound bus (16:20) connects at Grand Bend to Route 2 at 17:15, reaching London Masonville at 18:20.
  - If a holiday falls on a Friday (2026-12-25, 2027-01-01, Good Friday 2027-03-26), there is no bus.
- **Sources:** https://huronshoresareatransit.ca/schedules/route-3-grand-bend-to-goderich-2/ ; https://municipalityofbluewater.ca/news/new-hsat-bus-stop-in-brucefield-begins-service-february-2-2026

### Goderich
- **Why go / best season:** Octagonal Courthouse Square, the Huron County Museum and Gaol, Lake Huron beaches and sunsets. Best June to September.
- **Gateways:** HSAT stop 3004, Walmart Supercentre on Hwy 8. It is about 2.5 km east of Courthouse Square. There is no intercity coach.
- **How to get there from the GTA:** As for Bayfield. Route 3 leaves Grand Bend at 12:00 and 14:40 and reaches Walmart at 13:12 and 15:52. Sarnia or London to Goderich is $20.
- **Last mile:**
  - HSAT Route 3 runs Mon/Wed/Fri only, leaving Goderich at 13:20 and 16:00.
  - From Walmart into town, walk 2.5 km or take a taxi.
- **Ride-hail and taxis:**
  - Uber is sparse: the page exists but Huron County has no rate card.
  - Goderich Taxi & Delivery: 519-524-6594. Metered in town, flat rates outside. Sun 09-17, other days until 20:00-22:00.
  - Clinton Taxi: 519-482-7433, weekdays 07-17 only (low confidence).
- **Pitfalls:**
  - The last bus out leaves Walmart at 16:00. It connects at Grand Bend 17:12 to Route 2 for London (Masonville 18:20), or to Route 1 at 20:03 for Sarnia (22:00).
  - There is no weekend transit.
- **Sources:** https://huronshoresareatransit.ca/schedules/route-3-grand-bend-to-goderich-2/ ; https://huroncounty.cioc.ca/record/GBA3752?PrintMd=on

### Stratford
- **Why go / best season:** Stratford Festival (season runs to 2026-11-01), the Avon River and the food scene. Lights On Stratford runs January to March.
- **Gateways:** Stratford VIA station. Cooper Terminal at 290 Downie St (PC Connect, Stratford Transit). For PC Connect Route 2, the Kitchener pickup is Victoria at Duke (GRT 2541) or Kitchener Central.
- **How to get there from the GTA:**
  - **VIA 87:** Toronto 17:40, arrives Stratford 19:59. VIA 84 leaves Stratford at 11:16 and reaches Toronto at 13:38.
  - **Weekdays by GO:** take the GO Kitchener line to Kitchener, then PC Connect Route 2. It leaves Kitchener Central at 07:30, 11:00 and 15:15 (Victoria at Duke 5 min later) and reaches Cooper Terminal at 08:42, 12:15 and 16:30.
  - **Saturdays:** Route 2 leaves Conestoga Mall, Waterloo, at 09:00, 11:40 and 15:30.
  - **From London:** PC Connect Route 3 leaves Masonville (LTC 704) at 07:50, 10:35 and 15:15 on weekdays, and 09:00, 11:40 and 15:30 on Saturdays. It takes about 1 h 15 min.
  - **Stratford Direct:** the Festival coach from the InterContinental at Front and Simcoe, about $42 return, for ticket holders only. It leaves at 10:00, plus 15:00 on Fri/Sat evening-show days, and returns at 17:00 (and 23:00).
- **Last mile:**
  - PC Connect fares are $6 or $12 (London or Kitchener trips likely $12). Pay exact cash or by card through the Blaise app; pre-booking is recommended.
  - Stratford Transit runs fixed routes on weekdays ($3 cash, $7 day pass, GTFS tld-4744). Weekends are on-demand only: app code "stratford" or ridestratford.ca, Sat 06:00-19:30, Sun 10:00-17:30.
- **Ride-hail and taxis:** Uber yes, 24/7. The VIA station is about $9; Stratford to Kitchener about $57; London to Stratford about $99. Taxis were not researched.
- **Pitfalls:**
  - PC Connect has no Sunday service and none on statutory or civic holidays (2026-10-12 included).
  - The last weekday PC Connect bus to Kitchener leaves at 17:21; the last Saturday bus to Waterloo leaves at 17:24. The last weekday bus to London leaves at 16:40; the last Saturday bus at 16:45.
  - Stratford Direct needs a show ticket and ends 2026-11-01. Its arrival times are not published.
  - Stratford Transit runs no service on stat holidays even though its GTFS shows trips.
- **Sources:** https://www.stratford.ca/live-here/transportation/pc-connect-transit/schedules-and-maps/ ; https://www.stratford.ca/live-here/transportation/pc-connect-transit/how-to-ride/ ; https://www.stratford.ca/live-here/transportation/transit/ ; https://www.stratfordfestival.ca/WhatsOn/BusBooking

### St. Marys
- **Why go / best season:** Stone-town heritage, the Canadian Baseball Hall of Fame and swimming at the Quarry. Best May to September.
- **Gateways:** St. Marys VIA station. PC Connect stops at the Town Hall (175 Queen St E) and Memorial Hospital (268 Maiden Ln).
- **How to get there from the GTA:**
  - **VIA 87:** Toronto 17:40, arrives St. Marys 20:27. VIA 84 leaves St. Marys at 10:46.
  - **Weekdays:** GO to Kitchener, then PC Connect Route 2 (Kitchener 07:30 arrives Town Hall 09:05; 15:15 arrives 16:50). Or PC Connect Route 3 from London Masonville at 07:50, 10:35 or 15:15 (about 40 min).
  - **Saturdays:** Route 2 from Conestoga Mall at 09:00, 11:40 or 15:30; Route 3 from London at 09:00, 11:40 or 15:30.
- **Last mile:** Town Hall is downtown, so walk. The St. Marys to Stratford fare is $6 ($5 for seniors and students).
- **Ride-hail and taxis:** Uber is sparse; the page exists but waits are longer. London to St. Marys is about $70. Taxis were not researched.
- **Pitfalls:**
  - There is no PC Connect on Sundays or holidays.
  - The last weekday bus to Kitchener leaves at 16:58; to London at 17:10. On Saturdays the last bus to Waterloo leaves at 17:00 and to London at 17:20.
- **Sources:** https://www.stratford.ca/live-here/transportation/pc-connect-transit/schedules-and-maps/ ; https://www.uber.com/global/en/r/cities/st-marys-on-ca/

### London
- **Why go / best season:** Covent Garden Market, Museum London and Springbank Park. Year-round, and the hub for Lake Huron and Lake Erie trips.
- **Gateways:**
  - The VIA station on York St is downtown.
  - FlixBus stops downtown and at Western University.
  - Megabus, OurBus and OneXBus stop at Western behind Elgin Hall.
  - Masonville (LTC 704 and 705) is where PC Connect Route 3 and HSAT Route 2 start.
- **How to get there from the GTA:**
  - VIA runs several trains a day from Toronto, for example 06:50 (arrives 09:11), 11:30, 16:15, 17:22, 17:40 and 19:45. These are per GTFS; the 2026-10-25 timetable change may move them.
  - FlixBus runs from Union Station Bus Terminal.
  - Milton to London by UberX is about $185.
- **Last mile:**
  - LTC has GTFS (mdb-2). It runs a holiday (Sunday-type) schedule on stat holidays, but the GTFS does not encode them.
  - MCC Route 3 to St. Thomas runs Mon-Sat, $5. It leaves Victoria Hospital (LTC 189) at 06:26, 09:15, 15:26 and 18:14, and White Oaks Mall 12 min later.
- **Ride-hail and taxis:** Uber and Lyft yes, 24/7. Downtown to Masonville or Western is about $13-15.
- **Pitfalls:**
  - Intercity coaches use Western University, not downtown.
  - LTC GTFS shows Thanksgiving 2026-10-12 as a normal weekday, but LTC runs its holiday schedule.
  - MCC does not run on holidays.
- **Sources:** https://www.londontransit.ca ; https://www.middlesex.ca/schedules ; https://www.viarail.ca/sites/all/files/gtfs/viarail.zip

### Port Stanley
- **Why go / best season:** Lake Erie main beach, fishing harbour, Port Stanley Festival Theatre and the Terminal Rail excursion. Best June to September.
- **Gateways:** Railway City Transit Hub, St. Thomas (RCT 100), about 15 km north. There is no public transit into Port Stanley.
- **How to get there from the GTA:** VIA or FlixBus to London, then MCC Route 3 from Victoria Hospital or White Oaks Mall to the St. Thomas Hub, Mon-Sat, $5. It arrives at the Hub at 07:11, 10:00, 16:11 and 18:59. Then take a ride from the Hub to Port Stanley.
- **Last mile:**
  - No transit. Railway City Transit (GTFS tld-4746) does not go to Port Stanley.
  - Uber from St. Thomas is about $17. London straight to Port Stanley is about $42.
- **Ride-hail and taxis:** Uber is sparse: Central Elgin is covered 24/7 with longer waits. Why Wait Taxi: 519-782-3855, 24 h, $23 flat to St. Thomas (undated listing, low confidence).
- **Pitfalls:**
  - MCC Route 3 does not run on Sundays or holidays. The last bus from the St. Thomas Hub to London leaves at 19:21.
  - Plan the ride back to St. Thomas before 19:00.
- **Sources:** https://www.middlesex.ca/schedules ; https://www.uber.com/global/en/r/cities/central-elgin-on-ca/ ; https://www.elgincounty.ca/wp-content/uploads/2026/02/TTR_ElginTMP_FinalSummary_2026-02-20.pdf

### Kincardine / Port Elgin
- **Why go / best season:** Kincardine lighthouse and the Saturday pipe band parades; Port Elgin main beach and sunsets. Best late June to August.
- **Gateways:**
  - No scheduled transit for visitors in 2026. The nearest bus is HSAT Route 3 at Goderich Walmart (Mon/Wed/Fri), about 45 km from Kincardine and 75 km from Port Elgin.
  - FlixBus's seasonal Sauble Beach, Southampton and Port Elgin stops run only until 2026-10-19, per the FlixBus GTFS.
- **How to get there from the GTA:**
  - Before 2026-10-19: FlixBus to Port Elgin; check flixbus.ca.
  - After that: drive, take a full ride from London, or take HSAT to Goderich (Mon/Wed/Fri) plus a pre-booked taxi.
- **Last mile:**
  - TOK Coachlines (Southampton, Port Elgin, Kincardine to Toronto) ended 2026-02-27.
  - SMART is specialised, registered-rider demand-response service, not for visitors.
- **Ride-hail and taxis:**
  - Uber: treat as no. Kincardine has a page but very thin supply; Saugeen Shores has no page.
  - Bruce Taxi / ECabs (Port Elgin): 519-832-2111.
  - Goderich Taxi & Delivery: 519-524-6594, flat rates outside Goderich.
- **Pitfalls:**
  - FlixBus Port Elgin service ends 2026-10-19.
  - HSAT runs Mon/Wed/Fri only, and its last bus leaves Goderich at 16:00.
  - A Grey-Bruce transit plan is under study; recheck in 2027.
- **Sources:** https://tokcoachlines.com/ ; https://www.kincardine.ca/news/posts/media-release-march-6-2026-smart-accessible-transit-expansion/ ; https://brucegrey.cioc.ca/record/BRU0359


## 6. Destinations: North-west (Georgian Bay, Bruce Peninsula)

### Blue Mountain Village and Collingwood
- **Why go / best season:** Ontario's biggest ski resort (Dec to Mar) and a summer village with Scenic Caves and the Ridge Runner coaster; Collingwood's harbour and Georgian Trail. Jun to Oct for hiking, biking and fall colour.
- **Gateways:** Blue Mountain Village FlixBus stop (inside the village); Collingwood Main Terminal, 28 Second St.
- **From the GTA by transit:**
  - FlixBus 2711 (book on flixbus.ca, dynamic fares, Toronto to Owen Sound from about $23): Union 07:15, Pearson T1 07:50, Collingwood 09:45, Blue Mountain Village 10:00. Back: Village 15:15, Collingwood 15:35, Pearson 17:40, Union 18:30 (local times; same from October into December).
  - Extra Thanksgiving-period trips: 15:45 from Union (Fri to Tue, Oct 9 to 18) and 08:15 from Owen Sound (Sat to Wed, Oct 10 to 19).
  - Ski season (Dec 7 to Mar 21, daily in Dec, selected days later): ski trips Union 10:30 to the Village (arrives 13:45) and 17:30 back (Union 20:45). Local times do not shift in winter (only the feed's UTC times move on 2026-11-01); check the booking page.
  - Weekday alternative: GO to Barrie Allandale Waterfront, LINX Route 2 to Wasaga (hourly 05:30 to 18:30, 53 min, $6 cash), LINX Route 4 to Collingwood ($2), then BLINK.
- **Last mile:**
  - Blue Mountain Link (BLINK): hourly loop from Collingwood Terminal 06:00 to 22:00, about 18 min to the Village, daily year-round; $2.25 flat (card or on board), no booking, no transfer to Colltrans in-town buses.
  - LINX Route 4 Collingwood to Wasaga: hourly, daily, $2.
  - Colltrans in-town routes: daily 06:00 to 23:00, $2.25 (not needed for the Village).
- **Ride-hail and taxis:** Uber yes (24/7, longer waits; Collingwood to the resort about CA$18). Ace Cabs / Collingwood Taxi 705-445-3300; Collingwood Cabs 705-888-3438. No published flat rates.
- **Pitfalls:**
  - FlixBus does not run 2026-10-20 or 10-21, and from Oct 22 to Nov 30 there are no Tuesday or Wednesday trips.
  - Last FlixBus toward Toronto leaves the Village about 15:15 (plus 17:30 on ski-trip days).
  - LINX Route 2 is weekdays only (none on Thanksgiving 2026-10-12), so no weekend bus from Barrie GO.
  - BLINK Christmas Day service is uncertain (town page says none; a 2025 news item says it ran); planner assumes none.
  - The old Grey Transit bus along Hwy 26 was cancelled in March 2025; older guides still show it.
- **Sources:** https://www.collingwood.ca/community-access-mobility/public-transit ; https://www.collingwood.ca/media/file/transit-schedule-2025-blink-1pdf ; https://www.flixbus.ca/bus-routes/owen-sound-toronto-on ; https://simcoe.ca/residents/linx-transit/routes-schedules/route-4/

### Wasaga Beach
- **Why go / best season:** the world's longest freshwater beach (14 km, provincial park Beach Areas 1 to 6); late June to August.
- **Gateways:** 25 45th St S (Superstore) for LINX; Wasaga Beach FlixBus stop next to it. Both are at the west end.
- **From the GTA by transit:**
  - FlixBus 2711: Union 07:15, Pearson 07:50, Wasaga about 09:30; back from Wasaga about 15:50.
  - Weekdays: GO to Barrie Allandale Waterfront, then LINX Route 2 (hourly 05:30 to 18:30, 53 min, $6 cash or about $5.40 by LINX card).
  - Daily from Collingwood: LINX Route 4 (hourly, $2).
- **Last mile:** Wasaga Beach Transit Routes 1 and 2 run 07:00 to 21:00 every 60 to 90 min ($2) but publish no stop times; the east part of Route 1 is on-demand (Blaise app or 705-446-1196, book 7 days to 90 min ahead). Beach Areas 1 and 2 are about 7 km from the bus stops, so plan a ride.
- **Ride-hail and taxis:** Uber yes (Wasaga to Collingwood about CA$26). Ace Cabs / Collingwood Taxi 705-445-3300.
- **Pitfalls:** no weekend or holiday LINX Route 2; summer weekend traffic on Mosley St and River Rd slows rides; LINX holiday rules disagree (GTFS runs Route 4 a Sunday pattern on holidays including Christmas, the county says no service), so confirm at 1-800-263-3199.
- **Sources:** https://simcoe.ca/residents/linx-transit/routes-schedules/route-2/ ; https://www.wasagabeach.com/programs-services/transit/ ; https://www.uber.com/global/en/r/cities/wasaga-beach-on-ca/

### Owen Sound
- **Why go / best season:** Harrison Park, Inglis, Jones and Indian Falls, the Bruce Trail and the harbour; May to October.
- **Gateways:** Owen Sound Transit Terminal, 1020 3rd Ave E (FlixBus, GOST, Grey Transit Route 5 and city buses all meet here).
- **From the GTA by transit:**
  - FlixBus 2711: Union 07:15, Pearson 07:50, Owen Sound 11:00; back 14:15, Pearson 17:40, Union 18:30. A day trip gives about 3 hours.
  - GO Kitchener line to Guelph Central, then GOST (daily, exact cash, $20 Guelph to Owen Sound, no booking): Guelph 10:38 to Owen Sound 12:57, Guelph 17:30 to 19:49; back Owen Sound 07:30 to Guelph 09:52, 14:30 to 17:00. Stops at Elora, Fergus, Arthur, Mount Forest, Durham, Williamsford and Chatsworth.
- **Last mile:** Owen Sound Transit city routes (Brooke, Core, Crosstown, East Bayshore): Mon to Fri 06:30 to 18:00 every 30 min, Sat 09:00 to 16:00, no Sunday or holiday service; $3.25 exact cash, day pass $9.
- **Ride-hail and taxis:** Uber sparse (no city page; do not count on it). Owen Sound 5 Star Taxi 519-378-4747; Bayshore Taxi 519-371-5555; Discount Taxi 519-371-6000; AM2PM Taxi 519-270-3434 (pre-book after 23:00). Tripsy 1-647-249-9977 is a local ride alternative.
- **Pitfalls:**
  - GOST is funded only to 2026-12-31 and does not run Christmas Day; it runs normally on other holidays.
  - The city's GOST GTFS shows the 14:30 departure as 02:30; the planner uses a corrected hand-entered timetable.
  - City buses do not run Sundays or holidays; Inglis Falls needs a taxi.
  - FlixBus gaps: none 2026-10-20/21, no Tue or Wed trips Oct 22 to Nov 30.
- **Sources:** https://www.owensound.ca/roads-transportation/guelph-owen-sound-transportation/ ; https://www.owensound.ca/roads-transportation/public-transit/city-bus-service/ ; https://www.flixbus.ca/bus-routes/owen-sound-toronto-on

### Sauble Beach
- **Why go / best season:** 11 km of sand on Lake Huron with sunsets; July and August.
- **Gateways:** Sauble Beach FlixBus stop (seasonal); Hepworth Visitor Centre, 465 Bruce St (Grey Transit Route 5 and seasonal FlixBus), about 11 km east.
- **From the GTA by transit:**
  - Until 2026-10-19 only: FlixBus 2711 continues past Owen Sound to Hepworth about 11:25 and Sauble about 11:40; the return leaves Sauble 13:35 (Union 18:30). One trip each way.
  - After that: FlixBus or GOST to Owen Sound, then Grey Transit Route 5 to Hepworth (weekdays), then a taxi.
- **Last mile:** Grey Transit Route 5 Owen Sound to Hepworth and Wiarton: Mon to Fri, three round trips (from Owen Sound 06:00, 11:15, 20:00; from Wiarton 06:44, 11:59, 20:44; Hepworth about 27 min out), $5, seat must be reserved online at greytransitroute.com or 226-910-1001 (Mon to Fri 09:00 to 16:00). No bus from Hepworth to Sauble.
- **Ride-hail and taxis:** Uber no. Bayshore Taxi (Owen Sound) 519-371-5555.
- **Pitfalls:** FlixBus Sauble, Hepworth, Southampton and Port Elgin stops end 2026-10-19 and are not in the winter timetable; Route 5 has no service on 2026-10-12 or 2026-12-25 and is funded only to 2026-12-31; a 2027 summer extension to Sauble is only under consideration.
- **Sources:** https://www.grey.ca/resident-services/grey-transit-route ; https://www.grey.ca/news/transit-service-between-owen-sound-and-wiarton-returning-september-1 ; https://www.flixbus.ca/bus-routes/owen-sound-toronto-on

### Lion's Head
- **Why go / best season:** Bruce Trail cliff-top lookout over Georgian Bay (about 7 km return), village beach and harbour; June to October, fall colour late Sep to Oct.
- **Gateways:** Wiarton Louisa St stop (end of Grey Transit Route 5); Owen Sound Transit Terminal; Parkbus drop at Lion's Head Beach (1 Forbes St) on its trip dates.
- **From the GTA by transit:** FlixBus or GOST to Owen Sound, Grey Transit Route 5 to Wiarton (weekdays; arrives 06:43, 11:58, 20:43), then about 35 km by taxi. Parkbus guided day trips (Toronto 08:30 to Lion's Head 12:30, back 17:45 to Toronto 22:30; adult $100) are sold out for 2026.
- **Last mile:** no public transit north of Wiarton.
- **Ride-hail and taxis:** Uber no. Bayshore Taxi (Owen Sound) 519-371-5555; book both directions ahead.
- **Pitfalls:** the last Route 5 bus back leaves Wiarton 20:44 on weekdays and none run weekends; a FlixBus arriving Owen Sound at 11:00 cannot make a same-day Wiarton round trip with a hike (the last FlixBus back leaves Owen Sound at 14:15); the lookout has unrailed cliff edges and the Moore St side trail is closed.
- **Sources:** https://www.parkbus.ca/destinations/lions-head ; https://www.northbrucepeninsula.ca/community/transportation ; https://www.grey.ca/resident-services/grey-transit-route

### Bruce Peninsula National Park and the Grotto
- **Why go / best season:** the Grotto sea cave and turquoise water at Indian Head Cove; June to October (Sep and Oct quietest).
- **Gateways:** Cyprus Lake Campground (Grotto trailhead, Parkbus stop); Parks Canada Visitor Centre in Tobermory (no Grotto access from there).
- **From the GTA by transit:** only Parkbus (Toronto 08:30 to Cyprus Lake 12:00, back 16:00 to Toronto 22:30; adult $100 with Grotto entry included). All 2026 dates are sold out.
- **Last mile:** no shuttle from Tobermory to Cyprus Lake.
- **Ride-hail and taxis:** Uber no; no taxi can help, because Parks Canada bans dropping visitors off on Cyprus Lake Road and walking on it.
- **Pitfalls:** Grotto parking needs a reservation May 1 to Oct 31 (4-hour slots, no changes); Cyprus Lake Campground is closed through 2026-10-31 for bear activity; no car-free option outside Parkbus dates.
- **Sources:** https://parks.canada.ca/pn-np/on/bruce/activ/emplacements-locations/parking ; https://parks.canada.ca/pn-np/on/bruce/visit/directions ; https://www.parkbus.ca/destinations/bruce

### Tobermory
- **Why go / best season:** Little Tub Harbour, glass-bottom boats to Flowerpot Island, the Chi-Cheemaun ferry to Manitoulin; June to early October.
- **Gateways:** Chi-Cheemaun ferry terminal, 8 Eliza St; nearest scheduled bus is Wiarton (about 85 km).
- **From the GTA by transit:** nothing scheduled now. The Travelling Chicken shuttle (Fri and Sun from Dufferin Mall and Brampton, 647-625-9033) ran mid-June to September 2026 only. Parkbus 2026 trips went to Cyprus Lake, not the village, and are sold out.
- **Last mile:** none; no local transit or taxi company found.
- **Ride-hail and taxis:** Uber no. Bayshore Taxi (Owen Sound) 519-371-5555 for a long-distance trip (about 115 km), availability not confirmed.
- **Pitfalls:** ferry season ends 2026-10-18 (1-800-265-3163); the airport has no taxis; from about Oct 19 to May there is no car-free way in.
- **Sources:** https://tobermory.com/visitors/get-here ; https://www.northbrucepeninsula.ca/community/transportation ; https://www.ontarioferries.com/?p=14339

### Meaford and Thornbury
- **Why go / best season:** Georgian Bay harbours, Beaver Valley orchards and cideries, Georgian Trail cycling and Thornbury restaurants; May to October (blossom in May, harvest and colour Sep to Oct).
- **Gateways:** Thornbury and Meaford FlixBus stops (both close to the town centres).
- **From the GTA by transit:** FlixBus 2711: Union 07:15, Pearson 07:50, Thornbury 10:20, Meaford 10:35; back Meaford 14:40, Thornbury 14:55, Union 18:30 (October pattern; same gaps as above).
- **Last mile:** no local transit (the Grey Transit Hwy 26 routes ended 2025-03-31); walk from the stops.
- **Ride-hail and taxis:** Uber sparse (city pages exist, few drivers; Collingwood to Meaford about CA$50). Ace Cabs / Collingwood Taxi 705-445-3300.
- **Pitfalls:** one or two FlixBus trips per direction per day and none 2026-10-20/21 or on Tue/Wed Oct 22 to Nov 30; older guides still show the cancelled Meaford to Collingwood bus.
- **Sources:** https://collingwoodtoday.ca/the-blue-mountains-and-grey-highlands/grey-county-announces-it-is-cancelling-tbm-and-two-other-transit-routes-10283820 ; https://www.uber.com/global/en/r/cities/meaford-on-ca/ ; https://www.flixbus.ca/bus-routes/owen-sound-toronto-on


## 7. Destinations: North (Muskoka, Algonquin, Simcoe)

### Gravenhurst and Muskoka Wharf
- **Why go / best season:**
  - Things to do: the Muskoka Wharf boardwalk, Muskoka Steamships cruises (Wenonah II, and RMS Segwun back from 2026-09-01), and the Muskoka Discovery Centre.
  - Best season: late June to Thanksgiving. Fall colours run from late September to mid October.
- **Gateways:**
  - Ontario Northland stop GRAVENHURST (Gravenhurst Bait & Tackle).
  - Corridor 11 stops: District Community Services Hub (141 Main St), Centennial Centre and the Town office.
  - Muskoka Airport (YQA), for the Air Canada Landline coach.
- **From the GTA:**
  - Ontario Northland 101 from Yorkdale or Union. It runs daily.
  - Northbound arrivals: 11:49 (from Yorkdale 09:00), 15:17 (from Yorkdale 12:30), 21:45 (from Union 19:00) and 02:21.
  - Southbound departures: 05:16, 09:03, 14:51 and 19:09. The 14:51 and 19:09 run through to Union.
  - Fares were not captured; check ontarionorthland.ca.
- **Last mile:**
  - **Walk:** the coach stop is about 1.3 km (17 min) from the Wharf and Discovery Centre.
  - **Corridor 11:** Mon-Fri only, 3 trips each way.
    - Northbound buses reach Gravenhurst at about 08:49-08:58, 13:05-13:11 and 17:44-17:53, then continue to Bracebridge and Huntsville.
    - Southbound buses leave Gravenhurst at about 07:10, 11:43 and 16:05 for Washago and Orillia.
    - Cash fare: Gravenhurst-Bracebridge $5, Gravenhurst-Orillia $10.
  - **Muskoka DRT:** on-demand, Mon-Fri 07:00-19:00, adult $5. Book in the Blaise app or at 705-645-2100 ext 4419, from 30 min to 30 days ahead. It is not in the timetable data.
- **Ride-hail and taxis:**
  - Uber: sparse.
  - Gravenhurst Taxi 705-687-2246 (24h).
  - Muskoka Taxi 705-687-2111 (24h; accessible vans with 24h notice).
  - Driverseat 289-502-5155 (shuttles and groups).
  - No published flat rates.
- **Pitfalls:**
  - On weekends and holidays there is no local transit; walk or take a taxi.
  - The last southbound coach leaves at 19:09.
  - Hwy 11 rock-blasting closures (Tue-Thu) delay Corridor 11.
  - Steamship sailings sell out on peak weekends.
  - The Wenonah II elevator is out of service.
- **Sources:**
  - https://www.gravenhurst.ca/visit-gravenhurst/plan-your-visit/parking-transportation/
  - https://www.muskoka.on.ca/en/community-services-and-support/corridor-11-bus.aspx
  - https://www.muskoka.on.ca/en/community-services-and-support/district-drt.aspx
  - https://www.ontarionorthland.ca/communications/schedules/300826/2026-08-30-SCHED-101-102-EN.PDF

### Bracebridge
- **Why go / best season:**
  - Things to do: Bracebridge Falls and the downtown on the Muskoka River, plus Santa's Village on the west edge of town.
  - Best season: summer and fall colours.
- **Gateways:**
  - Ontario Northland stop BRACEBRIDGE (Quality Inn, Ecclestone Dr).
  - Corridor 11 stops: McVittie Place, MAHC Hospital, Oliver's Coffee (Manitoba St) and Dollarama (Hwy 118 W).
- **From the GTA:**
  - Ontario Northland 101, daily.
  - Northbound arrivals: 12:23, 15:50, 22:00 and 02:37.
  - Southbound departures: 05:00, 08:45, 14:33 and 18:53. The 14:33 and 18:53 run through to Union.
- **Last mile:**
  - **Bracebridge Transit loop:** one 60-minute loop, year-round.
    - It leaves stop 100 on Ecclestone Dr, about 70 m from the coach stop, at :32 past each hour: Mon-Fri 07:32-17:32, Sat 08:32-17:32.
    - Adult fare $2.50, exact cash or waveCARD. No booking needed.
  - **Corridor 11:** Mon-Fri. Bracebridge-Huntsville $10, Bracebridge-Orillia $15.
- **Ride-hail and taxis:**
  - Uber: sparse (no city page).
  - Muskoka Taxi 705-687-2111.
  - Murray's Taxi 705-646-9988 and Muskokab 705-641-9901 (both low-confidence listings).
  - Hammond Transportation 705-645-5431 (charter).
  - No published flat rates.
- **Pitfalls:**
  - No buses on Sundays or holidays.
  - The bus stop at Bird Mill Mews (stop 120) is reported closed.
  - Santa's Village is about 4 km from the nearest loop stop; take a taxi.
  - Book a taxi ahead for the 05:00 and 02:37 coaches.
- **Sources:**
  - https://www.bracebridge.ca/live-here/bracebridge-transit/plan-your-trip/
  - https://www.bracebridge.ca/live-here/bracebridge-transit/fares-and-passes/
  - https://www.muskoka.on.ca/en/community-services-and-support/corridor-11-bus.aspx

### Huntsville and Arrowhead Provincial Park
- **Why go / best season:**
  - Things to do: Lions Lookout, Muskoka Heritage Place, and Arrowhead Provincial Park (Big Bend Lookout, Stubb's Falls, winter skating trail), 8 km north.
  - Best season: summer, fall colours (late September to mid October), and winter for skating.
- **Gateways:**
  - Ontario Northland HUNTSVILLE (60 Station Rd; pick-up on Main St W).
  - Huntsville Transit stop 5, Main St W at Train Station.
  - Corridor 11 stops: Community Services Hub (1 King William St), MTO and the Hospital.
- **From the GTA:**
  - Ontario Northland 101, daily.
  - Northbound arrivals: 13:05, 16:22, 22:57 and 03:22.
  - Southbound departures: 04:31, 07:58, 13:48 and 18:03. The 13:48 and 18:03 run through to Union.
- **Last mile:**
  - **Huntsville Transit:** new timetable effective 2026-10-01.
    - Eastbound (Bickley Ford to Laketree) and westbound trips start at 07:00, 08:38, 10:16, 12:14, 13:52, 15:30 and 17:08, Mon-Fri.
    - Saturday hours end at 17:30, so assume no 17:08 trip.
    - Adult fare $2.75 cash.
    - On-demand stops: call 4 h ahead on 705-787-5680.
  - **Corridor 11:** Mon-Fri, 3 trips each way. To Orillia it leaves at 05:55, 10:35 and 14:50. Huntsville-Orillia costs $20 cash.
  - **Arrowhead:** taxi only.
- **Ride-hail and taxis:**
  - Uber: sparse (no city page).
  - Al's Taxi 705-789-2374 (24h; accessible vehicle).
  - Independent Taxi 705-789-8294 (low-confidence listing).
- **Pitfalls:**
  - No buses on Sundays or holidays, and the Saturday trip list is unconfirmed.
  - Pre-book the return taxi from Arrowhead.
  - The early 04:31 coach and the late 22:57 and 03:22 arrivals have no bus connection.
- **Sources:**
  - https://www.huntsville.ca/roads-sidewalks-transit/transit/
  - https://www.huntsville.ca/roads-sidewalks-transit/transit/transit-bus-schedule-and-map/
  - https://www.huntsville.ca/roads-sidewalks-transit/transit/fares-and-passes/

### Port Carling, Bala and Rosseau (Muskoka Lakes)
- **Why go / best season:**
  - Things to do: the Port Carling locks and shops, Bala Falls and cranberry country, and Rosseau village and resorts.
  - Best season: July to Thanksgiving.
- **Gateways:** the nearest Ontario Northland stops are Bracebridge (about 23 km to Port Carling), Gravenhurst (about 27 km to Bala) and Huntsville (about 40 km to Rosseau).
- **From the GTA:** Ontario Northland 101 to Bracebridge or Gravenhurst, then a taxi.
- **Last mile:** there is no public transit at all. Corridor 11 and the DRT do not reach Muskoka Lakes. Use a taxi or a resort shuttle.
- **Ride-hail and taxis:**
  - Uber: sparse. The area is in the pilot, but expect no cars.
  - Port Carling Taxi 705-394-5038 (low-confidence listing).
  - Muskoka Wide Taxi 705-394-5944.
  - Muskoka Taxi 705-687-2111.
- **Pitfalls:**
  - The whole last leg is a 25-45 km taxi ride.
  - Taxis are thin outside summer; book ahead.
  - Rosseau is in Seguin Township (Parry Sound District).
- **Sources:**
  - https://www.muskoka.on.ca/en/community-services-and-support/district-drt.aspx
  - https://www.discovermuskoka.ca/?p=136057

### Algonquin Provincial Park (Highway 60)
- **Why go / best season:**
  - Things to do: canoe routes from Canoe Lake, the Lookout and Track & Tower trails, the Visitor Centre, moose viewing and peak fall colour.
  - Best season: mid May to mid October. Peak colour is late September to early October.
- **Gateways:**
  - Huntsville Ontario Northland stop (the West Gate is about 45 km east).
  - Parkbus pick-ups at 34 Asquith Ave (Yonge and Bloor) and the Major Mackenzie Dr / Hwy 400 park and ride.
- **From the GTA:**
  - Parkbus day trips are reservation only, with park admission included:
    - Outbound: Asquith Ave 07:30, Hwy 400 08:15; arrives Oxtongue Lake 11:00, Canoe Lake 11:25 and Lake of Two Rivers 11:35.
    - Return: Lake of Two Rivers 16:10, back at Asquith Ave about 20:30.
    - Fares: adult $85, child $42. Weekend trips: adult $155.
  - The 2026 Parkbus season is over (the last trip was 2026-10-03), and 2027 dates have not been announced.
  - Otherwise, take Ontario Northland to Huntsville, then a taxi.
- **Last mile:** no transit on Hwy 60. Algonquin Outfitters (Oxtongue Lake, 1-800-469-4948) and Four Corners include transport only with their own packages or stays.
- **Ride-hail and taxis:**
  - Uber: no.
  - Al's Taxi, Huntsville, 705-789-2374. Call for a quote.
- **Pitfalls:**
  - Parkbus sells out.
  - The Pog Lake Parkbus stop runs only until Labour Day.
  - Cell service is spotty, so arrange the return taxi before you go.
  - You need a day-use permit when not on Parkbus.
- **Sources:**
  - https://algonquin.parkbus.ca/day-trip
  - https://www.parkbus.ca/destinations/algonquin
  - https://www.omca.com/2026-algonquin-fall-bus-plan/
  - https://algonquinoutfitters.com/wp-content/uploads/2016/10/ao_trip_planning.pdf

### Barrie (Kempenfelt Bay waterfront)
- **Why go / best season:**
  - Things to do: the waterfront and Centennial Park beach, downtown dining, and connections onward to Simcoe County.
  - Best season: June to September, plus the winter festivals.
- **Gateways:**
  - Allandale Waterfront GO (GO train, GO bus 68 and LINX 2).
  - Barrie Allandale Transit Terminal (Ontario Northland and Barrie Transit).
  - Barrie South GO.
  - Barrie Downtown Hub.
- **From the GTA:**
  - GO Barrie line trains from Union on weekdays.
  - On weekends only 2 trains each way reach Allandale Waterfront: southbound 14:05 and 16:05, northbound from Union 20:58 and 22:54. All other weekend trips are GO bus 68 via Aurora GO or Hwy 407 Bus Terminal.
  - GO runs a Saturday schedule on holidays.
  - Ontario Northland coaches also stop at Allandale daily.
- **Last mile:**
  - Barrie Transit, $3.50 cash (medium confidence).
  - Walk about 1 km from Allandale Waterfront GO to Centennial Park.
  - Both are in GTFS.
- **Ride-hail and taxis:**
  - Uber: yes. UberX costs about CA$13 within town; Toronto-Barrie is about CA$95.
  - Lyft is also listed.
  - No local taxi phone was captured.
- **Pitfalls:**
  - Barrie Transit has no service on Christmas or New Year's Day, and Boxing Day runs on a Sunday schedule.
  - The Barrie GTFS ends 2026-12-26 and the GO feed ends 2026-11-27; refresh both.
- **Sources:**
  - https://www.uber.com/global/en/r/cities/barrie-on-ca/
  - https://www.gotransit.com/en/see-schedules/holiday-schedules
  - https://www.barrie.ca/government/news-notices/media-releases/barrie-holiday-season-affected-services-service-reminders-and-closures

### Orillia (Lake Couchiching, Casino Rama, Leacock Museum)
- **Why go / best season:**
  - Things to do: the Port of Orillia and Couchiching Beach, the Stephen Leacock Museum, Casino Rama, and the Mariposa Folk Festival.
  - Best season: June to September. Mariposa is in early July.
- **Gateways:**
  - Orillia Downtown Transit Terminal (Ontario Northland and all Orillia Transit routes).
  - Lakehead University (LINX 3 and 6, Corridor 11, Orillia Transit 7).
- **From the GTA:**
  - **Ontario Northland, daily:**
    - Northbound arrivals: 11:10, 12:47 (route 201), 14:42 and 21:14.
    - Southbound departures: 06:07, 09:42, 17:22 (route 202), 19:47 and 20:49 (route 202).
  - **Weekdays:** you can also take a GO train to Barrie, then LINX 3 (Barrie Georgian College to Lakehead, $4 cash).
- **Last mile:**
  - **Orillia Transit:**
    - Fare: $2 exact cash.
    - Hours: Mon-Fri 06:15-22:45, Sat 08:45-20:15, Sun 08:45-16:45.
    - No service on statutory holidays. The GTFS does not mark holidays, so planners must treat holidays as no service.
  - **Walk:** about 0.6 km to the Port of Orillia.
- **Ride-hail and taxis:**
  - Uber: yes. Downtown to Casino Rama is about CA$19; Barrie-Orillia is about CA$48.
  - Central Taxi (Midland) 705-526-2626 lists Orillia in its service area (low confidence).
- **Pitfalls:**
  - Orillia Transit does not serve Casino Rama.
  - LINX runs weekdays only.
  - Sunday local service ends at 16:45.
- **Sources:**
  - https://www.orillia.ca/roads-and-environment/getting-around/transit/transit-schedule-and-fares
  - https://simcoe.ca/residents/linx-transit/routes-schedules/route-3/
  - https://www.uber.com/global/en/r/cities/orillia-on-ca/

### Midland and Penetanguishene (Georgian Bay)
- **Why go / best season:**
  - Things to do: Sainte-Marie among the Hurons, Martyrs' Shrine, Discovery Harbour, the harbourfront murals and 30,000 Islands cruises.
  - Best season: late June to Labour Day, plus fall colours.
- **Gateways:**
  - LINX 1 at Huronia Mall (Midland) and Penetanguishene Downtown.
  - LINX 6 at Midland Georgian College.
  - Mid-Pen Transit, Downtown Midland.
- **From the GTA:**
  - There is no Ontario Northland coach to Midland.
  - Take a GO train to Barrie, then Barrie Transit or Uber (about CA$13) to Georgian College / RVH, then LINX 1. LINX 1 runs Mon-Fri, hourly from about 06:00 to 19:00, and costs $6 cash ($5.40 with a LINX card).
  - From Orillia, take LINX 6 ($6).
- **Last mile:**
  - Mid-Pen Transit (Midland and Penetanguishene routes):
    - Hours: Mon-Fri hourly about 06:25-18:45, Sat about 08:25-17:45, no Sunday.
    - Adult fare $3.25.
  - Discovery Harbour is about 0.9 km on foot from the Broad & Church stop.
  - Sainte-Marie is about 3 km east of downtown with no confirmed stop; take a taxi.
- **Ride-hail and taxis:**
  - Uber: yes. Barrie-Midland is about CA$67; Toronto-Midland is about CA$138.
  - Central Taxi Midland 705-526-2626 (24h; flat rates on request; wheelchair vans).
  - Coldwater Taxi 705-686-3444 (low-confidence listing).
- **Pitfalls:**
  - There is no weekend transit link from Barrie or Orillia.
  - LINX 1 ends at Georgian College / RVH, not Allandale GO.
  - Mid-Pen Transit has no Sunday or holiday service.
  - The Miss Midland cruise boat has left Midland; check the current 30,000 Islands cruise operator.
- **Sources:**
  - https://simcoe.ca/residents/linx-transit/routes-schedules/route-1/
  - https://simcoe.ca/residents/linx-transit/fare-information/
  - https://www.midland.ca/getting-around/mid-pen-transit/mid-pen-transit-schedules-routes/
  - https://www.uber.com/global/en/r/cities/midland-on-ca/

### Parry Sound and Killbear Provincial Park
- **Why go / best season:**
  - Things to do: the Georgian Bay 30,000 Islands biosphere, Island Queen cruises, the Fitzgerald Point lookout and rail trestle, and the pink granite beaches at Killbear.
  - Best season: June to Thanksgiving.
- **Gateways:**
  - Ontario Northland PARRY SOUND.
  - VIA South Parry (westbound Canadian) and VIA Parry Sound (eastbound).
- **From the GTA:**
  - **Ontario Northland 201, daily:**
    - Northbound: Union 10:00 arrives 14:43; Yorkdale 13:45 arrives 17:11; Yorkdale 20:15 arrives 23:31.
    - Southbound: 09:15, 16:10 and 19:16 (the 19:16 reaches Union at 23:00).
  - **VIA's Canadian:** twice a week, often late. It leaves Toronto 09:55 and reaches South Parry 14:27. Our sources disagree on the days (Wed/Sun vs Wed/Sat), so check viarail.ca.
- **Last mile:**
  - No municipal bus.
  - Walk about 2 km from the coach stop to the town dock.
  - Killbear is about 35 km by road; take a taxi.
  - Parkbus lists a Killbear day trip as "coming soon", with no dates.
- **Ride-hail and taxis:**
  - Uber: no.
  - Parry Sound Taxi 705-746-1221 or 1-800-661-1307 (24h; the only licensed taxi in town).
  - Hammond Transportation 705-746-5430 (charter and airport; low confidence).
- **Pitfalls:**
  - With a single taxi company, pre-book the Killbear return.
  - Island Queen times are unconfirmed for 2026. They historically sailed at 10:00 and 13:00, and the season ends at Thanksgiving.
- **Sources:**
  - https://centraleastontario.cioc.ca/record/PSD0383?PrintMd=on
  - https://www.ontarionorthland.ca/communications/schedules/300826/2026-08-30-SCHED-201-202-EN.PDF
  - https://www.parkbus.ca/destinations/killbear-provincial-park
  - https://www.parrysound.ca/visit-us/transportation/


## 8. Destinations: East (Kingston, Thousand Islands, Prince Edward County, Kawarthas, Ottawa)

### Kingston (downtown waterfront, Fort Henry, Wolfe Island)
- **Why go / best season:** limestone downtown, Fort Henry, Thousand Islands cruises and the free Wolfe Island ferry. Go May to October; downtown is good year-round.
- **Gateways:** VIA Kingston station, 5 km northwest of downtown. Kingston Bus Terminal (1175 John Counter Blvd), used by Megabus. FlixBus Kingston Downtown (Wellington St), Queen's University, Division St and Cataraqui Centre.
- **From the GTA by transit:**
  - VIA: about 22 to 28 trains a day call at Kingston.
  - Megabus 2720/2730 from Toronto Union Bus Terminal (via Scarborough Centre GO): 11 to 17 trips a day, about 3 h 10 min. Sampled fares CA$14.99 to 59.99, usually CA$29.99.
  - From Milton's side of the GTA, Megabus 2711 leaves Mississauga (Duke of York Blvd) daily at 12:30, 14:00 and 18:30, then Pearson T1 and Yorkdale. It reaches the Kingston terminal 4 h 05 min later and Queen's 15 min after that, for CA$50 to 65. Westbound buses leave Queen's at 06:10 and 12:10.
  - FlixBus also serves downtown Kingston.
- **Last mile:**
  - Kingston Transit (GTFS): Route 2 runs from the VIA station stop to downtown about 60 times a weekday. It runs a Sunday schedule on most holidays and has no service on Good Friday, Christmas or New Year's Day.
  - Wolfe Island ferry: free and first come. It leaves Kingston (Ontario St) at 05:40 and then every 80 min to 23:00, plus 01:00 and 02:20. It leaves Marysville at 05:00 and then every 80 min to 23:40, plus 01:40. Crossings take about 25 min (an estimate).
  - Fort Henry: about 1 km uphill walk from the Hwy 2/Hwy 15 or Royal Military College stops.
- **Ride-hail and taxis:** Uber yes (VIA station to downtown about CA$21). Amey's Taxi 613-546-1111.
- **Pitfalls:**
  - The Wolfe Island ferry was unreliable in 2026 (crew shortages in June, delays after Wolfe Islander IV returned on about Aug 31). Check Ontario 511.
  - Fort Henry and the cruises wind down at the end of October.
  - Megabus's latest buses run overnight: 23:30 from Union reaches Kingston at 02:30.
- **Sources:**
  - https://ca.megabus.com/
  - https://www.ontario.ca/page/ferry-services
  - https://www.kingstontransit.ca/schedules-and-maps/
  - https://www.uber.com/global/en/r/cities/kingston-on-ca/

### Gananoque and the Thousand Islands (Ivy Lea, Rockport)
- **Why go / best season:** Thousand Islands boat cruises and the Thousand Islands Playhouse. Go May to October.
- **Gateways:**
  - FlixBus Gananoque, at Town Hall on King St E.
  - VIA Gananoque station, 4.5 km north of town, with only 2 or 3 trains a day.
- **From the GTA by transit:**
  - FlixBus (GTFS): in the October 2026 feed, the eastbound buses reach Gananoque at 11:30 (from Union at 08:00) and 19:00 (from Pearson at 14:35). Westbound buses leave at 09:05 and 18:40. From 2026-10-20 all four run Thursday to Monday; Tuesdays have only the 09:05 westbound and Wednesdays have none.
  - Alternative: go to Kingston, then take a taxi or Uber (about CA$69, 27 min).
- **Last mile:**
  - No local transit.
  - The Gananoque Boat Line dock is 550 m from the FlixBus stop.
  - Rockport (Rockport Boat Line) is about 20 km away and Ivy Lea is closer; both are taxi only.
- **Ride-hail and taxis:** Uber sparse (drop-offs from Kingston work, local pickups are unverified). T.I Taxi 613-382-2777.
- **Pitfalls:**
  - The FlixBus stop began as a pilot, and off-season trips may become on-demand (town notice).
  - The VIA station has no taxi stand.
  - The boat lines end in late October.
  - Boldt Castle is in the US, so bring a passport.
- **Sources:**
  - https://www.gananoque.ca/town-hall/138/welcome-flixbus-i-low-cost-bus-travel-in-canada
  - https://www.cityexperiences.com/gananoque/city-cruises/
  - https://rockportcruises.com/
  - https://www.uber.com/global/en/r/routes/kingston-on-ca-to-gananoque-on-ca/

### Brockville (Aquatarium, Railway Tunnel)
- **Why go / best season:** the Aquatarium and Canada's first railway tunnel on the waterfront, plus cruises from Blockhouse Island. Go May to October; the tunnel and Aquatarium are open year-round.
- **Gateways:**
  - VIA Brockville station, a 1.2 km walk to the waterfront.
  - Megabus stop on Jefferson Dr near Tim Hortons, by Hwy 401 and about 2.5 km from the waterfront.
- **From the GTA by transit:**
  - VIA: 13 to 16 trains a day.
  - Megabus 2720/2730 from Union: Wed 4, Sat 6 and Sun 5 trips, about 4 h 15 min to 4 h 30 min, CA$29.99 to 79.99. Morning departures are 06:00 and 06:15.
  - Westbound buses leave Brockville at 08:25, 09:05, 14:45 and 19:25 (more on weekends).
- **Last mile:** Brockville Transit runs Blue, Green and Red hourly loops from Court House Transfer.
  - Hours: Mon to Fri from 07:00 (last loop 17:00); Sat from 09:00 (last loop 17:00). An evening route runs Mon to Fri 18:00 to 21:00.
  - Fare: CA$2.75 exact cash. Children under 14 ride free.
  - Red stops at the Aquatarium. Blue stops at Front Ave and Perth St, 300 m from VIA. Green passes Jefferson Dr near the Megabus stop.
- **Ride-hail and taxis:** Uber sparse (no city page, unverified). City Taxi 613-345-5888.
- **Pitfalls:**
  - No Brockville Transit on Sundays or statutory holidays, including Thanksgiving 2026-10-12. Civic Holiday service is unconfirmed.
  - The Megabus stop coordinate is approximate.
- **Sources:**
  - https://brockville.com/city-services/transit/
  - https://ca.megabus.com/
  - https://www.downtownbrockville.com/businessdirectory/city-taxi

### Prince Edward County: Picton (Bloomfield, Lake on the Mountain)
- **Why go / best season:** wineries, Picton's main street, Base31 concerts and Lake on the Mountain. Go May to October; summer weekend buses ran 2026-07-03 to 2026-10-18.
- **Gateways:** Belleville is the hub.
  - VIA Belleville station.
  - Belleville Transit Terminal (165 Pinnacle St), where County Transit starts.
  - Megabus Belleville stop at 254 Millennium Pkwy.
- **From the GTA by transit:**
  - Belleville by VIA (about 19 to 22 trains a day), FlixBus, or Megabus 2711. Megabus leaves Mississauga at 12:30, 14:00 and 18:30 and reaches Belleville 3 h 05 min later, for CA$45 to 50.
  - Then Belleville Transit routes 3A/3C from the VIA station to the terminal, and County Transit CT-1 to Picton.
- **Last mile:**
  - County Transit CT-1 (GTFS), Mon to Fri. It leaves Belleville Terminal at 05:35, 07:35, 10:05, 13:35, 15:35 and 18:05 and reaches Picton 42 min later. It leaves Picton (Metro) at 06:43, 08:43, 11:13, 14:43, 16:43 and 19:13.
  - Fares: CA$3.50 under 5 km, CA$5.00 for 5 to 19 km, CA$10.00 for 20 km and over. Pay exact cash or by Interac e-transfer to billing@quintetransit.ca.
  - County Transit on-demand: CA$5 anywhere in the County, Mon to Fri 06:15 to 18:00. Book by app or call 613-392-9640 the day before; a seat is not guaranteed.
  - Glenora ferry to Adolphustown: free and daily, but no transit reaches either dock.
- **Ride-hail and taxis:**
  - Uber no; the County promotes the Uride app instead.
  - The Taxi Guy 613-403-2766; Button's Shuttle 613-919-0595.
  - By-law maximum fares from Picton (2023, including HST): Bloomfield CA$17.60, Lake on the Mountain 19.80, Wellington 30.80, Sandbanks gates 35.20, Belleville about 60.50, Kingston 121.00.
- **Pitfalls:**
  - No weekday County Transit on Thanksgiving 2026-10-12, Christmas, New Year's Day, Family Day, Good Friday or Victoria Day.
  - The summer weekend buses (CT-2/CT-3) end Sun 2026-10-18, and that Sunday is missing from the GTFS. After that, only CT-1 on weekdays plus on-demand.
  - Winter weather and driver shortages caused cancellations in January 2026.
  - There are few taxis, so book ahead on weekends.
- **Sources:**
  - https://www.thecounty.ca/public-and-specialized-transit/
  - https://myride.quintetransit.ca/gtfs/google_transit.zip
  - https://www.thecounty.ca/wp-content/uploads/2023/09/Taxi-Fare-Rate-Schedule-per-2023-Fees-and-Charges-By-Law.pdf

### Sandbanks Provincial Park
- **Why go / best season:** freshwater dunes and beaches. Go July and August; the 2026 park season runs to 2026-10-26 (another listing says Oct 31).
- **Gateways:** Picton (County Transit CT-1) or Wellington Recreation Centre (CT-2, summer weekends only).
- **From the GTA by transit:** as for Picton. Belleville, then CT-1 to Picton, then a taxi.
- **Last mile:**
  - There is no scheduled transit into the park; the stop called "Sandbanks Medical" is in Wellington.
  - Taxi from Picton: CA$35.20 maximum to the gates, CA$55.00 to the store and campsites.
  - Weekday County Transit on-demand (CA$5) if a seat is free.
  - Or bike or walk 8 to 10 km from Wellington.
- **Ride-hail and taxis:** Uber no. Use PEC taxis (The Taxi Guy 613-403-2766, Button's Shuttle 613-919-0595).
- **Pitfalls:**
  - Pre-book the return taxi, because mobile pickup is not guaranteed.
  - On summer weekends, day use fills and walk-ins can be turned away.
  - The park season ends in late October.
- **Sources:**
  - https://www.ontarioparks.ca/park/sandbanks
  - https://www.thecounty.ca/public-and-specialized-transit/
  - https://www.thecounty.ca/wp-content/uploads/2023/09/Taxi-Fare-Rate-Schedule-per-2023-Fees-and-Charges-By-Law.pdf

### Wellington (Prince Edward County)
- **Why go / best season:** wineries, Wellington beach and Pumpkinfest (2026-10-18). Go June to October.
- **Gateways:** Wellington Recreation Centre (CT-2 terminus).
- **From the GTA by transit:** Belleville (VIA, FlixBus or Megabus), then County Transit CT-2 Summer Weekend Connector (GTFS).
  - Runs Friday evening, Saturday, and Sunday to early afternoon, 2026-07-03 to 10-18.
  - Saturday departures from Belleville Terminal: 11:01, 12:47, 15:03, 16:49, 18:35 and 20:21, arriving 53 min later.
  - Saturday departures from Wellington: 10:08, 11:54, 13:40, 15:56, 17:42, 19:28 and 21:14.
- **Last mile:** walk in the village. CT-3 loops Picton to Bloomfield hourly for transfers on weekends.
- **Ride-hail and taxis:** Uber no. PEC taxis come from Picton (maximum CA$30.80 from Picton).
- **Pitfalls:**
  - The last weekend of service is 2026-10-16 to 18, and Sunday 10-18 is missing from the GTFS.
  - Outside summer weekends, Wellington has no fixed transit (CT-1 serves Picton, Bloomfield and Belleville only).
- **Sources:**
  - https://www.thecounty.ca/public-and-specialized-transit/
  - https://myride.quintetransit.ca/gtfs/google_transit.zip

### Belleville (waterfront, gateway to PEC)
- **Why go / best season:** Bay of Quinte waterfront and the transfer hub for the County. Go May to October.
- **Gateways:**
  - VIA Belleville station.
  - FlixBus Belleville (306 North Front St area).
  - Megabus at 254 Millennium Pkwy, about 4 km from the station.
- **From the GTA by transit:**
  - VIA: about 19 to 22 trains a day.
  - FlixBus (GTFS).
  - Megabus 2711: from Mississauga at 12:30, 14:00 and 18:30 (about 3 h 05 min, CA$45 to 50). Back from Belleville at 07:15 and 13:15.
- **Last mile:** Belleville Transit (GTFS). Routes 3A/3C serve the VIA station, and the terminal is at 165 Pinnacle St.
- **Ride-hail and taxis:** Uber sparse (no city page; Uride operates). Solar Taxi 613-962-2525 and Central Taxi 613-962-5353 (dated listing).
- **Pitfalls:** no Belleville Transit on Thanksgiving 2026-10-12, Dec 25 or Dec 26. The feed is rolling, ends 2026-12-31 and needs refreshing.
- **Sources:**
  - https://metrolinx.tmix.se/gtfs/gtfs-belleville.zip
  - https://www.belleville.ca/residents/page/transit
  - https://ca.megabus.com/

### Trenton / Quinte West (National Air Force Museum, Presqu'ile)
- **Why go / best season:** the National Air Force Museum of Canada and the start of the Trent-Severn Waterway, with Presqu'ile Provincial Park nearby. Go May to October.
- **Gateways:**
  - VIA Trenton Junction, 4 to 5 calls a day.
  - FlixBus at Trenton Junction, 2 trips a day each way.
  - Megabus 2711 at Front St Variety, 442 Front St.
- **From the GTA by transit:** Megabus 2711 leaves Mississauga at 12:30, 14:00 and 18:30 and reaches Trenton 2 h 40 min later (CA$43 to 50). It comes back from Trenton at 07:35 and 13:35. VIA and FlixBus also run (GTFS).
- **Last mile:** Quinte Transit (GTFS) routes A, B and C on weekdays and Saturdays, plus route 2 between Trenton and Belleville (with a VIA stop on Telephone Rd).
- **Ride-hail and taxis:** Uber sparse (unverified). No taxi company verified.
- **Pitfalls:** no Sunday or statutory-holiday transit in Quinte West. Presqu'ile (Brighton) is taxi only.
- **Sources:**
  - https://myride.quintetransit.ca/gtfs/google_transit.zip
  - https://ca.megabus.com/

### Cobourg (Victoria Beach, downtown)
- **Why go / best season:** sandy Victoria Park beach and Victoria Hall downtown. Go June to September.
- **Gateways:**
  - VIA Cobourg station, downtown, a 1.3 km walk to the beach.
  - FlixBus Cobourg (near Northumberland Hills Hospital), about 3 km from the beach.
- **From the GTA by transit:** VIA (16 to 18 calls a day) is the best option. FlixBus runs 2 to 3 trips a day each way.
- **Last mile:**
  - Walk from VIA.
  - Cobourg Transit On-Demand (RideCo): CA$2.50; Mon to Fri 06:15 to 21:00, Sat 08:15 to 18:45, Sun 08:45 to 15:45. Book in the app or call 905-373-0582.
  - Cobourg's limited fixed route runs Mon to Fri 07:00 to 19:00 hourly; no stop times are published.
  - Port Hope Route C stops at Northumberland Mall, the Hospital and Walmart (not downtown).
- **Ride-hail and taxis:** Uber sparse (no city page). Cobourg Cab 905-373-9449; Van Air Taxi 905-373-8850 for long trips, by reservation.
- **Pitfalls:** Cobourg's holiday hours are not published. The FlixBus stop is far from the beach.
- **Sources:**
  - https://www.cobourg.ca/transit
  - https://cobourgcab.pagexl.com

### Port Hope (heritage downtown, Ganaraska River)
- **Why go / best season:** a well-preserved 19th-century main street, the Capitol Theatre, and the fall salmon run. Go May to October.
- **Gateways:**
  - VIA Port Hope, a 0.8 km walk to downtown, with only 2 to 4 trains a day.
  - Megabus at Harvey's (Hwy 401/28) and FlixBus at Rose Glen Rd, both about 2.5 km north of downtown.
- **From the GTA by transit:**
  - Megabus 2711 from Mississauga at 12:30, 14:00 and 18:30 reaches Port Hope at 14:30, 16:00 and 20:30 (CA$36 to 40). Westbound buses leave Port Hope at 08:20 and 14:20.
  - Or take VIA to Cobourg (more trains) and Route C back.
- **Last mile:**
  - Port Hope Transit Route C (Cobourg Express Shuttle) leaves Town Hall (56 Queen St) every 30 min: Mon to Fri 07:00 to 19:30, Sat 09:00 to 15:30. It reaches Northumberland Mall at +10, the Hospital at +14 and Walmart at +15.
  - Routes A and B cover town but have no published stop times.
  - Fare: CA$2.50 exact cash.
- **Ride-hail and taxis:** Uber yes (city page confirms). No taxi company verified.
- **Pitfalls:**
  - No transit on Sundays or statutory holidays, including Thanksgiving 2026-10-12.
  - Reduced hours (08:00 to 16:00) on Easter Monday, Civic Holiday, Sept 30 and Remembrance Day (no trips 10:30 to 11:30 on Remembrance Day).
  - On Dec 24 and Dec 31, service runs 07:00 to 12:00 only.
- **Sources:**
  - https://www.porthope.ca/en/living-here/transit.aspx
  - https://www.uber.com/global/en/r/cities/port-hope-on-ca/
  - https://ca.megabus.com/

### Peterborough (Lift Lock, Canadian Canoe Museum, Riverview Park and Zoo)
- **Why go / best season:** the Peterborough Lift Lock, the new Canadian Canoe Museum, and the free Riverview Park and Zoo. Go May to October, while the locks run.
- **Gateways:**
  - Peterborough Bus Terminal (190 Simcoe St), for GO route 88.
  - FlixBus at Fisher at Casino and at Byersville Tim Hortons.
- **From the GTA by transit:**
  - GO bus 88 from Durham College Oshawa GO to the Peterborough terminal and Trent University: about 22 to 23 trips a day. It runs the Saturday pattern on Thanksgiving.
  - FlixBus Toronto to Peterborough: about 4 trips a day on the Ottawa corridor.
- **Last mile:** Peterborough Transit. Fare CA$3.50 cash or HotSpot app; children 12 and under ride free.
  - Route 7 Lansdowne runs past the Lift Lock and Canoe Museum, between Willowcreek Plaza and Armour Rd, from late April to late October. It runs every 30 min on weekdays and hourly on Saturdays, Sundays and holidays.
  - Route 5 links the FlixBus casino stop and Fleming College to the terminal.
  - Route 11 Water passes Riverview Zoo on weekdays only.
- **Ride-hail and taxis:** Uber sparse (a route page from Toronto exists, about CA$167, but no city page). Capitol Taxi 705-742-4242.
- **Pitfalls:**
  - Route 7 stops serving the Lift Lock and museum in late October; the date is unpublished.
  - Sunday schedule on holidays.
  - No service on Dec 25 or Jan 1, and Christmas Eve service ends at 19:15.
  - Route 11 does not run on statutory holidays.
- **Sources:**
  - https://www.peterborough.ca/living-in-peterborough/transit/routes-and-schedules/route-7-lansdowne/
  - https://www.peterborough.ca/living-in-peterborough/transit/routes-and-schedules/route-5-the-parkway/
  - https://www.peterborough.ca/living-in-peterborough/transit/passes-and-fares/
  - https://www.gotransit.com/en/see-schedules/holiday-schedules

### Kawarthas beyond Peterborough (Lakefield, Petroglyphs, Bobcaygeon, Fenelon Falls)
- **Why go / best season:** lake country and Petroglyphs Provincial Park. Go June to September.
- **Gateways:** Peterborough Bus Terminal (GO 88). Lindsay is reachable only through Lindsay Transit, which serves the town itself.
- **From the GTA by transit:** GO 88 to Peterborough, then a taxi or ride-hail.
- **Last mile:** no fixed-route transit to Lakefield, Petroglyphs (about 55 km from Peterborough), Bobcaygeon or Fenelon Falls.
- **Ride-hail and taxis:** Uber sparse. The City of Kawartha Lakes has an Uber page (UberX trips CA$66 to 125) but pickups are slow. Capitol Taxi 705-742-4242 from Peterborough.
- **Pitfalls:** getting back is the risk, so pre-book a taxi or Uber Reserve before you go.
- **Sources:**
  - https://www.uber.com/global/en/r/cities/kawartha-lakes-on-ca/
  - https://metrolinx.tmix.se/gtfs/gtfs-kawartha.zip

### Ottawa (Parliament Hill, ByWard Market, Rideau Canal)
- **Why go / best season:** Parliament, the national museums, the ByWard Market and the Rideau Canal. Good year-round; Winterlude is in February, the Tulip Festival in May, and fall colours in October.
- **Gateways:**
  - VIA Ottawa station, 4 km east of downtown.
  - FlixBus at the VIA station, Waller St (downtown, by uOttawa) and St Laurent.
  - Megabus at Waller St at Laurier, and at St Laurent Station Platform E.
- **From the GTA by transit:**
  - VIA (GTFS).
  - FlixBus (GTFS).
  - Megabus 2730 from Union: Wed at 06:15, 12:30 and 17:00; up to 5 a day on weekends. It takes about 5 h 35 min to 5 h 50 min and costs CA$29.99 to 49.99. Some trips also start at Pearson Viscount Station.
- **Last mile:** OC Transpo (GTFS). O-Train Line 1 runs from Tremblay station, next to VIA.
- **Ride-hail and taxis:** Uber yes (about CA$15 from VIA to downtown). Taxis are plentiful.
- **Pitfalls:** OC Transpo's feed window is short (to about 2026-11-09), so refresh it. VIA's Corridor timetable changes on 2026-10-25 and the GTFS lags.
- **Sources:**
  - https://ca.megabus.com/
  - https://www.uber.com/global/en/r/cities/ottawa-on-ca/
  - https://oct-gtfs-emasagcnfmcgeham.z01.azurefd.net/public-access/GTFSExport.zip


## 9. Destinations: South (Niagara, Lake Erie)

### Niagara Falls (Clifton Hill, Table Rock, Fallsview)
- **Why go / best season:** Horseshoe Falls, Journey Behind the Falls, the boat cruises, Clifton Hill and the casinos. Best May to October; the Winter Festival of Lights runs November to January.
- **Gateways:** Niagara Falls GO/VIA station (Bridge St); Niagara Falls Bus Terminal, 4555 Erie Ave (next door); FlixBus stops at Table Rock and the Rapidsview lot.
- **From the GTA by transit:**
  - **GO train, weekends and holidays:** leaves Union 09:04, 11:09, 17:07 and 19:22; leaves Niagara Falls 11:55, 15:57, 19:55 and 22:10.
  - **GO train, weekdays:** one round trip only (Niagara Falls 06:36 to Union; Union 16:59, arriving 19:33).
  - **GO bus 12:** Burlington GO to Niagara Falls Bus Terminal hourly every day, about 06:52 to 24:01 on weekdays and from about 08:10 on weekends; CAD 14.80. From Milton, an Uber to Burlington GO averages CA$28.
  - **VIA Maple Leaf:** Toronto 08:20 to Niagara Falls 10:28; back 17:45, arriving 19:43.
  - **FlixBus:** from Union, Yorkdale or Pearson to the terminal, Table Rock or Rapidsview. A FlixBus ticket to Rapidsview includes a free WEGO ride to Table Rock.
  - **Trailways:** 4 trips a day touch Ontario.
- **Last mile:**
  - **WEGO 603 Green Line** (Rapidsview, Table Rock, Clifton Hill, GO/VIA station, Whirlpool, Floral Clock): every 15 minutes about 09:00 to 20:30 from Oct 4 to 31 2026, then every 30 minutes Nov 1 2026 to Apr 3 2027.
  - **WEGO 602 Blue Line** (Fallsview and Clifton Hill hotel loop): every 30 minutes, about 09:00 to 21:00.
  - **WEGO fares:** the 24-hour pass is CAD 16 adult, CAD 12 child aged 3 to 12; the 48-hour pass is CAD 20 / 16. Buy it online at niagaraparks.com/wego or at a Welcome Centre or the bus terminal; it is not sold in the Transit app.
  - **NRT local routes:** CAD 3.50 exact cash, Transit app or fare card, with a 2-hour transfer.
  - **Falls Incline Railway:** Fallsview to Table Rock, about CAD 3.50 to 4.00 (official pages disagree).
- **Ride-hail and taxis:** Uber yes, Lyft yes. UberX averages CA$13 to 14 within the tourist area; Milton to Niagara Falls averages CA$134 (about 74 minutes). Niagara Falls Taxi 905-357-4000.
- **Pitfalls:**
  - Weekday trains are commuter-only until spring 2027.
  - Oct 17-18 and Oct 24-25 2026: Niagara trains run only between Aldershot and Niagara Falls.
  - In the GTFS the Green Line's last departure is 20:00, but the PDF says 21:00.
  - WEGO had no service on Dec 25 and closed about 16:30 on Dec 24 last year.
  - A Megabus cancellation was reported in June 2026.
- **Sources:** https://www.niagaraparks.com/wego ; https://www.niagaraparks.com/media/2026/10/2026_WEGO_Schedule_Fall-Winter-1.pdf ; https://nowtoronto.com/news/go-transit-schedule-changes-september-2026/ ; https://www.flixbus.ca/bus/niagara-falls-on ; https://nrtransit.ca/fares/

### Niagara-on-the-Lake (Old Town, Queen St, Shaw Festival, wineries)
- **Why go / best season:** heritage Old Town, the Shaw Festival, Fort George and 20+ wineries. Best May to October; the wineries open year-round and the Icewine Festival is in January.
- **Gateways:** Floral Clock (WEGO 603 to 604 transfer); Niagara College Glendale campus (GO bus 12, NRT 40/45/50/55); Outlet Collection at Niagara (NT+ access point); FlixBus stop at the Fort George visitor centre, 40 Queen's Parade.
- **From the GTA by transit:**
  - **Via Niagara Falls:** any Niagara Falls option, then WEGO 603 north to Floral Clock (about 20 minutes), then WEGO 604.
  - **GO bus 12:** to Niagara College (hourly every day), then NT+.
  - **FlixBus:** Toronto to NOTL, 1 trip a day leaving around 09:30 to 10:00, from CA$14.48.
- **Last mile:**
  - **WEGO 604 NOTL shuttle:** Floral Clock to Queenston Heights, Laura Secord Homestead, McFarland House and Fort George. Fort George is a 7-minute walk to Queen St.
    - Runs daily Oct 4 to 31 2026: northbound hourly 10:30 to 18:30 from Floral Clock (30-minute ride); southbound hourly 11:00 to 19:00 from Fort George.
    - The WEGO pass covers it (CAD 16 for 24 hours) and no booking is needed.
    - No service Nov 1 to Apr 3. The 2026 spring (Apr 4 to May 14, hourly) and summer (May 15 to Oct 3, every 30 minutes) patterns are assumed for 2027.
  - **NT+ on-demand (NOTL zone, curb to curb):**
    - Hours: Mon-Sat 07:00 to 23:00; Sundays and holidays 10:00 to 18:00 (announced from 2026-09-06; the town page still says Sundays closed).
    - Fare: CAD 3.50 including a 2-hour transfer; exact cash, Transit app or fare card.
    - Booking: book up to 7 days ahead on the NT+ app or at 1-833-678-5463 ext 1. Pickup comes in a 15-minute window.
- **Ride-hail and taxis:**
  - Uber yes, Lyft yes. UberX averages CA$16 to 18 in town; Niagara Falls to NOTL averages CA$29 to 31.
  - Taxi numbers (all low confidence): NOTL taxi 905-468-2661, St Catharines Taxi 905-341-7811, Brock Taxi 905-935-5000.
- **Pitfalls:**
  - The 604 ends 2026-10-31. From November to March only NT+ or ride-hail reach Old Town.
  - The last 604 southbound (19:00 from Fort George) reaches Floral Clock after the last 603 southbound (about 18:45), so take the 18:00.
  - No fixed bus enters Old Town.
  - Shaw evening shows end after WEGO and NT+ stop, so budget an Uber.
- **Sources:** https://www.gotransit.com/en/partners-and-promotions/go-to-niagara-on-the-lake ; https://www.niagaraparks.com/media/2026/10/2026_WEGO_Schedule_Fall-Winter-1.pdf ; https://nrtransit.ca/microtransit/ ; https://niagaranow.com/news.phtml/sunday-transit-service-is-coming-to-niagara-on-the-lake/

### St. Catharines (Port Dalhousie, Welland Canals Centre Lock 3, downtown)
- **Why go / best season:** Port Dalhousie beach and carousel, ships passing Lock 3, and downtown around the Meridian Centre. Best May to September.
- **Gateways:** St. Catharines GO/VIA station; Downtown Terminal, 70 Carlisle St; Fairview Mall (GO bus 12).
- **From the GTA by transit:**
  - **GO train:** the same trains as Niagara Falls, about 30 minutes earlier going out and later coming back (weekends and holidays; one weekday round trip).
  - **GO bus 12:** hourly every day from Burlington GO to Fairview Mall.
  - **GO bus 11:** Aldershot or Hamilton to the downtown terminal and Brock, weekdays only.
  - **VIA:** the Maple Leaf also stops here.
- **Last mile:**
  - NRT 301/401 to Port Dalhousie (Main St at Elgin St).
  - NRT 314/414 to the Welland Canals Centre (Dunkeld Ave, about 240 m).
  - NRT 438 links the GO station.
  - All NRT routes are CAD 3.50.
- **Ride-hail and taxis:** Uber yes, Lyft yes; UberX averages about CA$15 to the GO/VIA station or Fairview Mall. Central Taxi 905-685-7343, St Catharines Taxi 905-341-7811 (both low confidence).
- **Pitfalls:**
  - There is 1 weekday train round trip, and GO bus 11 does not run on weekends.
  - Local buses run less often in the evening and on Sundays.
  - Rail outages happen (a CN derailment closed the line Mar 30 to Apr 10 2026).
- **Sources:** https://nrtransit.ca/fares/ ; https://www.uber.com/global/en/r/cities/st-catharines-on-ca/ ; https://nowtoronto.com/news/go-transit-schedule-changes-september-2026/

### Port Colborne (Nickel Beach, Sugarloaf Marina, Lock 8)
- **Why go / best season:** Lake Erie beach, ship watching at Lock 8 and Canal Days in August. Best June to September.
- **Gateways:** Welland Bus Terminal, 160 East Main St; Port Colborne City Hall, 66 Charlotte St.
- **From the GTA by transit:** reach St. Catharines (GO train or GO bus 12), then NRT 70/75 to Welland, or NRT 60/65 from Niagara Falls. From Welland take NRT 25 Port Colborne Link.
  - Mon-Sat: 16 trips each way, 06:30 to 22:25.
  - Sundays and holidays: 8 trips, 10:00 to 17:52.
- **Last mile:**
  - NRT 25 makes flag stops in town (CAD 3.50).
  - Nickel Beach is about 2 km from City Hall: walk, or book NT+ on-demand in the Port Colborne zone (CAD 3.50, same hours as NOTL).
- **Ride-hail and taxis:** Uber yes (fewer drivers than in Niagara Falls), Lyft yes; Welland to Port Colborne averages CA$25. Port Taxi 905-835-2424 (2017 listing, verify).
- **Pitfalls:**
  - On Sundays and holidays Route 25 runs only 10:00 to 17:52.
  - No fixed bus goes to Nickel Beach.
  - Canal Days brings crowds and detours.
- **Sources:** https://nrtransit.ca/microtransit/ ; https://www.uber.com/global/en/r/cities/port-colborne-on-ca/ ; https://nrtransit.ca/news/regional-sunday-and-holiday-service/

### Crystal Beach (Bay Beach), Fort Erie
- **Why go / best season:** sandy Lake Erie beach and the village of Crystal Beach near Ridgeway. Best June to September.
- **Gateways:** Fort Erie Town Hall / Leisureplex hub.
- **From the GTA by transit:** reach Niagara Falls, then take NRT 22 Fort Erie Link from the Canadian Drive Hub. Locally, NRT 751 Fort Erie Community Bus runs hourly.
  - Mon-Sat: about hourly, 06:30 to 23:21.
  - Sundays and holidays: 09:30 to 18:21.
- **Last mile:** no fixed route reaches Crystal Beach. NT+ on-demand in the Fort Erie zone (CAD 3.50, booked) links the Town Hall/Leisureplex hub with Crystal Beach and Ridgeway.
- **Ride-hail and taxis:** Uber sparse. Fort Erie has an Uber page (Fallsview Casino averages CA$42), but Crystal Beach has none, so book ahead. Central Taxi (Fort Erie) 905-994-8294 (2017 listing, verify).
- **Pitfalls:**
  - Fort Erie generates about a third of all NT+ rides, so summer waits are long.
  - NT+ ends about 23:00, and Sunday service is 10:00 to 18:00.
  - Bay Beach charges for entry in summer.
- **Sources:** https://nrtransit.ca/microtransit/ ; https://nrtransit.ca/news/fort-erie-community-bus/ ; https://forterieradio.ca/?p=9762

### Port Dover
- **Why go / best season:** Lake Erie beach, the pier and lighthouse, perch, and the Friday-the-13th motorcycle rally. Best June to September.
- **Gateways:** Brantford Bus Terminal, 64 Darling St; Simcoe Library, 92 Court St.
- **From the GTA by transit:**
  - **GO bus 15** Aldershot to Brantford: about hourly every day, CAD 10.80.
  - **Ride Norfolk Brantford route** to Simcoe: Mon-Fri only, booking required, CAD 15.
  - **Weekday chain out:** Aldershot 08:34, Brantford 09:34; Ride Norfolk leaves Brantford 10:28 and reaches Simcoe 11:13.
  - **Weekday chain back:** Ride Norfolk leaves Simcoe 15:20 and reaches Brantford 16:10; GO 16:33 reaches Aldershot 17:48.
  - From Milton, an Uber to Brantford averages CA$109.
- **Last mile:**
  - **Ride Norfolk on-demand** (Blaise Transit) from Simcoe to stop P4, Port Dover Harbour Museum, 44 Harbour St, near the beach.
  - Runs Mon-Fri 07:30 to 18:00; no weekends or holidays.
  - Fares: CAD 3 in town; out of town (over 8 km, which includes Simcoe to Port Dover) CAD 6.50; day pass CAD 8.
  - Book on the Blaise app or at 1-888-465-0783.
- **Ride-hail and taxis:** no Uber or Lyft (Norfolk is outside every Uber operating region). Courtesy Cab 519-426-9911 covers the whole county Mon-Sat 06:00 to 18:00. Norfolk Taxi & Delivery 519-428-3131 (low confidence). Port Dover to Simcoe by taxi is about CA$30 to 40 (estimate).
- **Pitfalls:**
  - No weekend or holiday transit, and Courtesy Cab is closed on Sundays.
  - There are only 2 Brantford trips each way; missing the 15:20 from Simcoe (16:30 from Brantford going south) strands you.
  - The Ride Norfolk GTFS expired 2026-10-01, so these times come from the county website.
  - The Friday 2026-11-13 rally brings closures and crowds.
- **Sources:** https://www.norfolkcounty.ca/home-property-and-neighbourhood/public-transit-ride-norfolk/ ; https://www.norfolkcounty.ca/home-property-and-neighbourhood/public-transit-ride-norfolk/on-demand-booking-stops-and-routes/ ; https://info-bhn.cioc.ca/record/SIM2507 ; https://www.norfolkcounty.ca/visiting/friday-the-13th/

### Turkey Point
- **Why go / best season:** shallow sandy beach, the provincial park and the marsh. Best June to September.
- **Gateways:** same as Port Dover (Brantford Bus Terminal, then Simcoe Library).
- **From the GTA by transit:** GO bus 15 to Brantford, then the Ride Norfolk Brantford route to Simcoe (weekdays, booked), as for Port Dover.
- **Last mile:** Ride Norfolk on-demand from Simcoe to stop TP1, 2 Turkey Point Rd (about 18 km, out-of-town fare CAD 6.50). Mon-Fri only, booking required.
- **Ride-hail and taxis:** no Uber or Lyft. Courtesy Cab 519-426-9911 (Mon-Sat 06:00 to 18:00); Norfolk Taxi & Delivery 519-428-3131.
- **Pitfalls:**
  - Beach days are usually weekends, but transit runs weekdays only.
  - The provincial park closes for the season in mid-October.
- **Sources:** https://www.norfolkcounty.ca/home-property-and-neighbourhood/public-transit-ride-norfolk/on-demand-booking-stops-and-routes/ ; https://info-bhn.cioc.ca/record/SIM2507

### Long Point (Provincial Park, Biosphere, Port Rowan)
- **Why go / best season:** a UNESCO biosphere sand spit with beach and birding. Best May to September; spring and fall migrations are the highlights.
- **Gateways:** Brantford Bus Terminal and Simcoe Library, then Port Rowan (Villages of Long Point Bay, 23 Long Point Blvd, stop PR1).
- **From the GTA by transit:** same chain as Port Dover to Simcoe (weekdays only, booked).
- **Last mile:** Ride Norfolk on-demand from Simcoe to Port Rowan PR1 (out-of-town fare CAD 6.50, Mon-Fri, booked). Nothing goes onto the causeway or into the park; the last 6 to 8 km needs a taxi or a bike.
- **Ride-hail and taxis:** no Uber or Lyft. Courtesy Cab 519-426-9911 (Mon-Sat 06:00 to 18:00); Norfolk Taxi & Delivery 519-428-3131.
- **Pitfalls:**
  - No transit reaches the park.
  - The Simcoe hub is about 45 km away.
  - The park and campgrounds are seasonal, May to October.
  - Haldimand County (Caledonia, Dunnville) has had no public transit since SOT was suspended in December 2024.
- **Sources:** https://www.norfolkcounty.ca/home-property-and-neighbourhood/public-transit-ride-norfolk/on-demand-booking-stops-and-routes/ ; https://www.cbc.ca/news/canada/hamilton/haldimand-southern-ontario-transit-suspension-1.7420253
