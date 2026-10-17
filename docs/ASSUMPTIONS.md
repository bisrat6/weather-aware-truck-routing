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

## Not decided here

14. **Wind is treated as a single value.** The brief doesn't say whether it means sustained wind or gusts, and that choice belongs to the later weather-fetching work.
15. **Snow rate is treated as a single value in in/hr.** The brief doesn't say whether it means liquid-equivalent or depth.
