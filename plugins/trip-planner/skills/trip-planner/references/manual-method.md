# Manual method: no code execution, or the network is blocked

Use this when `trip_hubs.py` cannot run, or prints `NETWORK BLOCKED`. Do Steps 1 to 3 of `SKILL.md` by
hand, label every number a manual estimate, and say the list is manual and may be incomplete. For
drives, follow the manual method in `driving.md` instead.

1. **Pick a tool that can request any URL.** Prefer a connector or MCP tool that makes plain HTTP
   requests (an HTTP, fetch or scraping connector such as Scrapling, or a browser-automation tool).
   Built-in web-fetch tools often refuse URLs you build yourself (claude.ai's does): they open pages
   that came from a web search or the user, not API calls like the Transitous ones. If no such tool is
   loaded, say so and suggest the user enable one, or allow the domains the NETWORK BLOCKED message
   listed, then continue with steps 2 to 4 meanwhile.
2. **Plan from the files first.** The region notes list the hubs, routes and seasons. With code
   execution but no network, `--timetable list` and `--timetable ID` print the hand-entered timetables
   (rural buses, Onex, Megabus, WEGO and more) offline. Use web search to reach operators' schedule and
   departure pages, which a restricted fetch tool can then open.
3. **Query Transitous from each candidate hub** (when a tool allows it) with the request format in
   `sources.md` section 2. Per hub ask `numItineraries=1` with no `maxTransfers` limit: `maxTransfers=0`
   returns direct trips only and can hide a faster trip with a change. Replies list every intermediate
   stop, so shrink what you read (times, route, headsign), not the search. If the user prefers direct
   service, run a direct query as well and show both with their trade-off. Ignore itineraries that take
   far longer than the others (detours via a downtown terminal). With no tool that can call it, give
   the user the prefilled URL to open, or skip it and say so. Keep the attribution link to
   https://transitous.org/sources/ when you quote it.
4. **Price each ride** from `ride_pricing` in the region JSON (base + per km + per min + booking fee,
   then tax; `sources.md` section 4 has the method, and a generic rate where no region file exists).
   Take ride times from the keyless OSRM route endpoint in `sources.md` section 3 when your tool can
   call it, and label them free-flow (no traffic). Otherwise give the user a Google Maps driving link to
   read the time themselves; never fetch Google Maps pages yourself. Label the prices manual estimates,
   and give the user the prefilled Uber link format from `sources.md` section 4 to confirm.
