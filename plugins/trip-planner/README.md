# trip-planner plugin

Plans trips that combine an Uber, Lyft or taxi ride with public transit by checking every transit
hub within ride range, then comparing ride-only, ride plus transit and transit-only on arrival time
and cost. It also plans drives in your own car (`drive_plan.py`):
- fastest against toll-free with the 407 ETR toll priced;
- a leave-by time;
- parking near the door priced for the stay, verified prices ahead of similar unverified ones;
- EV charging stops;
- parking at a station to take transit instead.

Contains one Agent Skill: `skills/trip-planner`.

Standard library Python 3.8+, no API keys needed (TomTom, NLR and Ontario 511 keys are optional). Live
mode downloads public GTFS timetables and uses the free OSRM, Valhalla, Nominatim and Transitous services,
plus open parking, fuel-price, weather and charger data for driving.

See the repository README for install instructions and examples. The code is MIT licensed; bundled data keeps its own licence, listed with each attribution statement in [`skills/trip-planner/NOTICE`](skills/trip-planner/NOTICE).
