# Proximity

**Proximity is power.** Find neighborhoods that fit your life, not just your budget.
Built at HackPrinceton Fall 2026 for students and early-career renters moving near Rutgers–New Brunswick, NJ.

> Status: layer 1 of 4. All data in the app is real: rent, area locations, commute times, events and place counts. `data/placeholder/` is only a fallback if a file goes missing.

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
| `data/commute.csv` | TravelTime API and manual Google Maps lookups, checked 2026-11-14, departing Tue 08:30; each row keeps its own source | real |
| `data/places.csv` | OpenStreetMap via the Overpass API, fetched 2026-11-14 by `scripts/fetch_osm.py`: everything within 1,500 m of each area's centre point | real |

The app reads `data/<file>.csv` first and falls back to `data/placeholder/<file>.csv` only when the real file is absent. Anything from the placeholder folder is labeled "Demo data".

"Room in a shared 2-bed" is an estimate: the HUD 2-bedroom rent split in half.

## Limitations

- "In an area" means within 1,500 m of the area's centre point, not the real ZIP boundary. Neighbouring circles overlap, so a place can count for two areas, and an area whose centre point is away from its downtown is undercounted.
- Sports facilities are mostly unnamed pitches in OpenStreetMap, so that count is large and coarse.
- Commute lookups all depart on a weekday morning. A late-night trip home from Newark has not been checked, and the app says so on every area.
- Commute values that are not numbers ("Not found") count as missing. Transit times over 120 minutes are flagged "Unusual: please verify" and are not used.
- The map background needs an internet connection; the markers and everything else work offline.
- Events ("Hot News"), free-text profiles and the compare table are not built yet.
