# Proximity

**Proximity is power.** Find neighborhoods that fit your life, not just your budget.
Built at HackPrinceton Fall 2026 for students and early-career renters moving near Rutgers–New Brunswick, NJ.

> Status: layer 1 of 4. Rent, area locations and events are real data. **Commute times and place counts are still placeholders**, shown as "Demo data" in the app, and are not real.

## What works now

- A profile form (budget, bedroom type, destinations, travel mode, max commute, interests, importance per item).
- 8 candidate areas on a map, with the top 3 matches numbered.
- A card per match: why it matched, the main compromise, what is missing, and the rent estimate with its source.
- Change any setting and the ranking recomputes, with a "What changed" note that gives the reason.

## How matching works

Code decides, with no AI involved in scoring:

1. **Eligibility.** Rent must fit the budget, and every must-have commute must be within its limit. If a must-have has no data, the area is shown as *Unverified* and ranked after all verified areas.
2. **Fit.** Each priority gets a 0 to 1 value (commute and place counts are compared across the 8 areas, rent by room under budget), weighted Must-have 3, Important 2, Nice 1. The result is shown as "Fit 56 · relative to the 8 areas". It ranks these areas against each other; it is not an absolute grade.
3. **Priorities met** is reported separately ("Meets 5 of 6 evaluated priorities").
4. **Missing data** never counts as met and never raises the Fit.

We match on places, travel time and rent only. Nothing in the app describes who lives in an area.

## Run it

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m streamlit run app.py
.venv/Scripts/python -m pytest -q
```

## Data sources

| File | Source | Status |
|---|---|---|
| `data/areas.csv` | Census ZCTA Gazetteer (internal point) | real |
| `data/rent_hud.csv` | HUD FY2026 Small Area Fair Market Rents, dated 2026-05-21 | real |
| `data/rent_zillow.csv` | Zillow Observed Rent Index (smoothed, all homes plus multifamily), 2026-09 | real |
| `data/events_curated.csv` | Collected by hand from venue and town pages on 2026-11-14; every row links to its source | real |
| `data/commute.csv` | Manual Google Maps lookups | not collected yet; `data/placeholder/commute.csv` is used |
| `data/places.csv` | OpenStreetMap via Overpass | not collected yet; `data/placeholder/places.csv` is used |

The app reads `data/<file>.csv` first and falls back to `data/placeholder/<file>.csv` only when the real file is absent. Anything from the placeholder folder is labeled "Demo data".

"Room in a shared 2-bed" is an estimate: the HUD 2-bedroom rent split in half.

## Limitations

- Commute times and place counts are placeholders until those files are collected, so the ranking is not real yet.
- The map background needs an internet connection; the markers and everything else work offline.
- Events ("Hot News"), free-text profiles and the compare table are not built yet.
