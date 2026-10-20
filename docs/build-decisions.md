# Build decisions and lessons learned

Ten non-obvious choices, the alternatives rejected, and the real trips that forced several of them. Back to the [README](../README.md) or the [architecture](architecture.md).

## 1. A backward profile scan, not a router queried per hub

The obvious build calls a journey planner from every candidate hub. Around Milton that is 635 hubs (the README example), which means 635 rate-limited calls per question to a free service. Instead the skill downloads the GTFS feeds once and runs a single profile connection scan backwards from the destination. Every stop gets its full "leave at t, arrive at f(t)" function, and each hub's best connection is a lookup. Transitous is still queried, but only to cross-check the top three.

## 2. Driving joined the same skill instead of becoming a second one

A separate driving skill would have been cleaner to read. It was rejected because "how do I get to Grand Bend on Monday?" should compare driving, ride plus transit and park and ride in one answer. Two skills would compete for that question, and the agent would answer with whichever fired. The driving planner also reuses the transit engine's geocoding, regions, time zones and holidays, and park and ride *is* the transit engine with a car. Context cost is handled with progressive disclosure: a short section in `SKILL.md` and the detail in `references/driving.md`.

## 3. The 407 got its own traffic rows

The first time-of-day model multiplied every congested-area road by the same rush-hour factor, 407 included. At 07:30 to Markham it said the 407 saved 7 minutes. Google said 40 to 60 minutes via the 407 against 60 to 120 avoiding tolls. Congestion pricing exists to keep the 407 moving, so the model now gives it its own rows (x1.05 typical at peak instead of x1.45). The same trip now shows about a 29 minute saving.

## 4. Free-flow times were calibrated against an outside reference and pinned by a test

A review found keyless drive times 30 to 80% too long. Valhalla's "free-flow" time was already close to Google's typical time, and the traffic factors were stacked on top. The fix measured nine routes against Google's ranges and calibrated per road class (x0.85 on the 407, x0.88 on rural highways). Then a regression test was added that fails if any of the nine leaves Google's range by more than 15%.

## 5. A closed lot ranked first, so stale data got a freshness signal and a verify step

Planning a Sunday at Toronto Island, the planner's top pick was a Green P carpark that closed in 2021. Toronto's Green P open data is from 2019 and was never refreshed. Dropping old lots outright would have deleted open ones. The current occupancy feed covers only 149 of the 252 old carparks and misses every TTC and waterfront lot, so absence there is a weak signal. So:

- known closures are dropped;
- lots missing from current data are flagged "may have closed" and ranked below the unflagged priced lots (a priced flagged lot still outranks one with an unknown price, so the output marks it);
- the "check before you go" line lists the first three flagged lots among the top five, plus #1 and the first lot that may have closed; in `--json` every flagged option carries a `check` field;
- `SKILL.md` tells the agent to verify the recommended lot on its official or curated page, else by web search, and never to hand off a flagged lot's link until it is shown to be open; an OpenStreetMap website tag, which anyone can edit, is shown only as "OSM-supplied, unverified", never as the page to check.

## 6. A default car is an assumption, so the output says so and the agent asks

Fuel cost first used a small-SUV median. That's fine for one user who knows it, and wrong for everyone else using the skill. Asking for L/100 km fails because nobody knows theirs. So the agent asks for the year, make and model, and `--car` looks the model up in NRCan's official ratings. The ratings are bundled for offline use, priced on premium or diesel when the car needs it, and switch to EV mode for an electric car. `--save-car` remembers the answer. When the user declines, the `CAR` line says "assumed" and the agent must say so too.

## 7. Only legitimate data paths, even when a scraper would work

GasBuddy, Impark, SpotHero and Google Maps all have the prices a planner wants, and their terms forbid scraping. The live Green P site answers scripts with bot-protection pages. 407 ETR's calculator sits behind reCAPTCHA. The skill uses the official alternatives instead:

- the Ontario fuel survey;
- open city data;
- the published 407 rate chart, computed locally;
- Google's Places API with the user's own key.

Everything else becomes a link or a search string. To check one lot, the agent may open its operator page as a person would (one page, an ordinary browser tool, or a web search result), never a scraper or a stealth mode. When Green P's own pages turned out to be readable with a stealth browser, that instruction was removed from the skill. The same rule covers the NRCan car-rating download: it sends the skill's own User-Agent, not a browser's, and if open.canada.ca refuses it the planner uses the bundled index with a WARNING.

Three bundled timetables did not follow this path. Onex and Megabus publish no GTFS, so on 2026-10-10 their coach timetables (`onex`, `megabus-on-east` and `megabus-on-west` in `ontario.json`) were built by automated searches of the operators' booking systems: Onex's Betterez booking API, with the key its cart page uses, and the Megabus journey API, for every date over about two weeks. The operators' terms were not confirmed to allow this. The owner kept these timetables by decision (2026-10-12). The skill never calls these systems at run time; each feed's notes and source list say how it was built, and `references/sources.md` lists the three. Every other hand-entered timetable was typed in from the operator's public pages or published files
(`ride-norfolk-brantford` also takes its holiday rules from its expired 2025-26 GTFS).

## 8. A blocked sandbox degrades to what can still be said

The first claude.ai test failed with all 25 timetable hosts blocked, and claude.ai's own fetch tool refused the URLs the manual method needed. Both planners now print `NETWORK BLOCKED` with the exact hosts to allow, plus `--check-network`. Given `lat,lon`, the driving planner still prints the curated tourist-place parking and the post-2019 Green P carparks it carries offline; given an address it stops at geocoding and says to pass `lat,lon`. `SKILL.md` tells the agent to use any connector that can fetch arbitrary URLs.

## 9. One GO tap credits every local fare before it, kept as is

With PRESTO's One Fare, a local fare is credited back when the rider taps onto GO within 2 hours. The planner credits every separately paid local fare in those 2 hours, not only the last one (MiWay, then YRT, then GO prices $0, $0, $9.05). What PRESTO really does with two paid local fares before one GO tap is unconfirmed. The case is rare: agencies that meet almost all have their own co-fare rules, so two separately paid local fares in a row hardly occur, and when they do the plan reads one local fare too cheap. The owner kept the behaviour on 2026-10-20 (review item gem-G2A), and a test pins it.

## 10. A rolling parking maximum counts blocks from entry, free hours included, kept as is

A Green P maximum "per 12 hour period" or for "Any 4 hour Period" is priced in consecutive blocks counted on the clock from the first charged minute, the way a garage clock bills. Free hours inside a block still use up its time. With $4 an hour, a $9 maximum per 12 hours from 08:00 to 18:00 on weekends and free nights, Saturday 14:00 to Sunday 16:00 prices $26; a reset each day would give $18. A sliding window or a daily reset are other readings of the same sign, and this one errs high. The bundled data has no such rule; only live Green P text with a free gap inside a rolling window reaches it. The owner kept the behaviour on 2026-10-20 (review item gem-G3), and tests pin both this case and 14 h at $4 with a $12 any-4-hour maximum ($44).
