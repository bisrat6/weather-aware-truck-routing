# Assumptions

Where `brief.pdf` is ambiguous, the risk engine follows these defaults.

## Bands

Bands are half-open `[lo, hi)` unless noted. Each shared edge belongs to the higher band.

| Factor | Low | Moderate | High | Severe | No Travel |
|---|---|---|---|---|---|
| Wind (mph) | < 25 | [25, 35) | [35, 45) | [45, 55) | >= 55 |
| Rain (in/hr) | < 0.10 | [0.10, 0.25) | [0.25, 0.50) | [0.50, 1.00] | > 1.00 |
| Snow (in/hr) | < 0.5 | [0.5, 1.0) | [1.0, 2.0) | [2.0, 3.0] | > 3.0 |

1. **Shared table edges go to the higher band** (rain 0.25/0.50, snow 1.0/2.0). The brief lists them in two adjacent columns, and the safer reading is the higher level.
2. **Half-open wind bands.** The brief's integer ranges (25-34, 35-44, 45-54) leave gaps for fractional speeds such as 34.5, and half-open bands close them.
3. **Rain 1.00 and snow 3.0 are inclusive Severe.** This is the one deliberate exception to half-open bands. The brief says "> 1.00" and "> 3.0" for No Travel, so the edge values themselves are Severe.
4. **Wind >= 55 is No Travel.** The brief's glyph is "≥" (verified in the PDF text).

## Load rules

5. **Load is in pounds**, as the brief says "lb". Whether it means gross or cargo weight is unspecified, so the value is used as given.
6. **Load comparisons are strict** (`>`), as in the brief. A load of exactly 30,000 or 40,000 lb does not trigger a rule.
7. **Load rules only escalate.** The final level is the max of the wind, rain, snow and load-rule levels, and a rule never lowers a level.
8. **45 <= wind < 55 and load > 30,000 lb gives No Travel.**
9. **35 <= wind < 45 and load > 40,000 lb gives at least Severe.** It only fires inside the High wind band, so it escalates High to Severe and can never create No Travel.

## Inputs

10. **Reject NaN, infinity and negative values** with `ValueError`, because they are not physical weather or load readings.
11. **A load of exactly 0 is valid**, so an empty truck can be assessed.
12. **Rain and snow are independent.** There is no combining, so moderate rain plus moderate snow does not become High.

## Reasons

13. **Every non-LOW level gets a reason.** The format is `"<factor> <value> <unit>: <Level>"`. Load escalations append the load, for example `"wind 45.0 mph, load 34,000 lb: No Travel"`. Tests check that the reason names the factor and the level, not the exact string.

14. **`wind_level` includes load escalation.** The load rules are conditions on wind speed, so `CheckpointRisk.wind_level` is the wind band raised by any load rule. This keeps `level == max(wind_level, rain_level, snow_level)` true. It is an API design choice, not something the brief states.

## Not decided here

15. **Wind is treated as a single value.** The brief doesn't say whether it means sustained wind or gusts, and that choice belongs to the later weather-fetching work.
16. **Snow rate is treated as a single value in in/hr.** The brief doesn't say whether it means liquid-equivalent or depth.

## Routing (`backend/routing/`)

17. **`distance_m` / `duration_s` are the provider's totals; checkpoints use the cumulative arrays only.** Mapbox rounds annotation values, so `cum_*[-1]` can differ slightly from the totals. Checkpoint logic reads only `cum_dist_m` / `cum_dur_s`, so `sum(owned_mi)` equals `cum_dist_m[-1]` in miles exactly (to float precision).
18. **`TRUCK_TIME_FACTOR` (default 1.1) multiplies durations only**, both annotation durations and the route total. Distances are untouched. It is a parameter of `get_routes` / `parse_routes`, and `config/settings.py` reads it from the environment.
19. **A leg's annotations are usable only if** both lists exist, each has exactly `coordinates - 1` entries, and every entry is a finite non-negative number. Otherwise that leg gets proportional durations: the provider's leg duration (times the factor) is split in proportion to geodesic segment length, and distances are scaled to the provider's leg distance. One bad leg makes the whole route `eta_method="proportional"`.
20. **Multi-leg responses.** The two-waypoint request used by `get_routes` returns one leg per route. If a response ever has several legs, the geometry is split using the legs' annotation counts (a leg with k entries covers k segments); if the counts do not add up, the whole route is treated as one proportional leg. Joined legs share their joint point, which is dropped once.
21. **`join_routes` assumes the end of one piece is the start of the next** and always drops the next piece's first point. It does not check that the two points are close.
22. **Checkpoint placement.** Points sit at 0, interval, 2 x interval, ... plus the destination, so the last gap is shorter than the interval unless the route length is an exact multiple (within 1e-9). If that gives more than `max_points`, the interval is coarsened to `total / (max_points - 1)`, which yields exactly `max_points` evenly spaced points.
23. **Interpolation is linear in lat/lon** between route points. That is accurate for road geometry (segments are short) but not for routes that cross the antimeridian.
24. **Errors never contain the access token.** Network exceptions from `requests` are not chained, because their text includes the full URL with `access_token`.
25. **The distance check is not wired into `get_routes`.** `check_route_distance` returns a result (default tolerance 5%) and callers decide what to do with a mismatch.
26. **Hand-built fixture.** `tests/fixtures/mapbox_handbuilt_*.json` uses the real response shape with synthetic numbers. No real Mapbox response has been recorded or tested yet.

## Weather (`backend/weather/`)

27. **Open-Meteo `/v1/forecast`, one request for many points.** Requested with `wind_speed_unit=mph`, `precipitation_unit=inch`, `timezone=UTC` and `forecast_days` sized to the latest hour needed (1-16). The client verifies the units the API echoes back (wind `mp/h`, precipitation `inch`) and rejects a response that is not UTC, so a unit mix-up fails loudly instead of changing risk levels.
28. **Variables: `wind_speed_10m`, `rain`, `showers`, `snowfall`.** Wind is the sustained 10 m wind; gusts (`wind_gusts_10m`) are not requested, as the project guide says. Rain is `rain + showers`. `precipitation` is not used (it already includes snow) and neither is `snow_depth`.
29. **Snow is `snowfall` in inches of snow per hour, not liquid water.** Open-Meteo documents it as "snowfall amount of the preceding hour" in cm, or inches with `precipitation_unit=inch`. The brief's snow bands are written in "in/hr", so this is the natural reading, but the brief does not say whether it means snow depth or liquid-equivalent.
30. **Hourly precipitation values cover the preceding hour.** Open-Meteo stamps `rain`, `showers` and `snowfall` with the end of the hour they describe (wind is instantaneous). Following the guide, a checkpoint uses the nearest forecast hour, so the value stamped 11:00 (rain from 10:00 to 11:00) is used for an ETA of 10:30-11:29. That is a half-hour offset in how precipitation lines up with the ETA; it is accepted, not corrected.
31. **ETA to hour: nearest hour, exactly :30:00 rounds up.** `10:29:59` uses 10:00 and `10:30:00` uses 11:00. Timezone-aware ETAs are converted to UTC first; naive ETAs are rejected.
32. **Missing data is flagged, never zero.** A null (or non-numeric, negative or non-finite) value is stored as missing. A checkpoint with any missing variable, including a null `showers` with a real `rain`, comes back as a `MissingWeather` flag naming the variables, and `WeatherService.weather_at` raises `MissingWeatherDataError`. Hours before today 00:00 UTC or beyond the 16-day window are flagged without a request.
33. **`rain + showers` is rounded to 6 decimals.** Float addition puts `0.01 + 0.09` at 0.09999999999999999, just under the 0.10 band edge. Rounding removes that noise and moves no real value.
34. **Grid cache.** Coordinates snap to a 0.1 degree cell (nearest centre, halves round up) and Open-Meteo is asked for the cell centre, so one cached value serves every point in the cell. Entries are keyed by (cell, UTC hour), live 45 minutes, and the cache holds at most 50,000 entries (oldest, expired first, dropped beyond that). It is thread-safe and its clock is injectable.
35. **Requests over 100 points are split.** The Open-Meteo docs do not state a per-request cap, so 100 is a conservative choice, not a documented limit.
36. **Model selection is left to Open-Meteo's default (`best_match`).** The model actually used for a point is not recorded.

## Analysis (`backend/analysis/`)

37. **Missing weather has no risk level.** It is flagged on its checkpoint and counted in the summary; its owned miles are not assigned to any risk bucket or included in the distance-weighted average risk. This avoids treating unknown weather as Low.
38. **Average risk uses known-weather miles only**, weighted by `owned_mi` on the 0-4 risk scale. If every checkpoint is missing, average risk and maximum level are `None`.
39. **Ranking tie handling.** No Travel routes sort after every route without No Travel; among blocked routes, exact No Travel miles are compared first, followed by Severe and High miles rounded to whole miles, average risk rounded to 2 decimals, then exact duration seconds. Stable input order resolves exact ties. A route with no known-weather miles has unknown average risk and sorts after routes with a calculable average.
40. **Route summary duration uses the normalized route's provider duration** (already truck-factor adjusted by routing); checkpoint ETAs use the cumulative segment duration offsets.
41. **Incomplete-weather routes rank after every complete-weather route**, before the existing No Travel, Severe, High, average-risk, and duration ordering. Routes with missing data are still compared by those existing criteria against other incomplete routes. If every route has missing data, the same risk ordering applies; when every checkpoint on a route is missing, its risk buckets are empty and average risk is unknown, so it ties on those unavailable criteria and duration decides after earlier criteria. Missing owned miles stay separate from risk buckets and are reported with the missing-checkpoint count in the recommendation reason.
42. **API load range is 0–100,000 lb inclusive.** The brief does not specify an input range; this range accepts an empty load and common cargo weights while rejecting implausible entries.
43. **API departure window starts at 00:00 UTC today and ends at exactly 7 days from request time.** The weather service exposes hours from the start of the current UTC day; future departures are kept inside the project guide's 7-day window.
44. **Geocoding uses Mapbox Geocoding v5** with the server-side `MAPBOX_TOKEN`, because the project guide does not select a geocoding provider. At most five compact location results are returned.
45. **Heatmap point cap is 150.** Route points are sampled about every 10 miles, deduplicated using the existing 0.1-degree weather grid, and if necessary evenly reduced while retaining corridor endpoints. This matches the guide's "near 150" limit and bounds the maximum response to 7,350 hourly risk values. Heatmap hour zero uses the existing nearest-UTC-hour rule; missing point-hour weather is returned as `null`.
46. **ETA stability compares the base checkpoint risk with risk at ETA −2 h and +2 h.** A flip means any change among Low, Moderate, High, Severe, and No Travel. Both shifted weather samples are included in the same batched weather lookup; its forecast horizon is sized through the latest shifted ETA. `unstable` is `true` if any assessed shifted level differs from the base level, even when the other shift is missing; `null` only when no flip is known and a shifted level is missing (or the base itself is missing); `false` only when both shifted levels were assessed and match the base. Stability is descriptive only and does not affect ranking or recommendation.
