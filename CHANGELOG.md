# Changelog

Releases of the trip-planner skill. Before shipping, each release was reviewed by fresh-context reviewer
agents (separate sessions with no part in the build) or an outside GPT review; agent findings were
re-checked by a verifier before anything was fixed. Counts are given where they were recorded.

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
