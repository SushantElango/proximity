# Proximity

**Proximity is power.** Find neighborhoods that fit your life, not just your budget.
Built at HackPrinceton Fall 2026 for students and early-career renters moving near Rutgers–New Brunswick, NJ.

> Status: layer 1 of 4. The numbers in `data/` are currently **placeholders** (every row is marked `demo=1` and shown as "Demo data" in the app). They are not real.

## What works now

- A profile form (budget, bedroom type, destinations, travel mode, max commute, interests, importance per item).
- 8 candidate areas on a map, with the top 3 matches numbered.
- A card per match: why it matched, the main compromise, what is missing, and the rent estimate with its source.
- Change any setting and the ranking recomputes, with a "What changed" note that gives the reason.

## How matching works

Code decides, with no AI involved in scoring:

1. **Eligibility.** Rent must fit the budget, and every must-have commute must be within its limit. If a must-have has no data, the area is shown as *Unverified* and ranked after all verified areas.
2. **Score.** Each priority gets a 0 to 1 score (commute and place counts are compared across areas, rent by room under budget), weighted Must-have 3, Important 2, Nice 1.
3. **Missing data** never counts as a match and never raises a score. The app reports how many priorities were evaluated.

We match on places, travel time and rent only. Nothing in the app describes who lives in an area.

## Run it

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python scripts/make_demo_data.py            # only if data/ is empty
.venv/Scripts/python -m streamlit run app.py
.venv/Scripts/python -m pytest -q
```

## Data sources

| File | Source | Status |
|---|---|---|
| `areas.csv` | Census ZCTA Gazetteer | placeholder |
| `rent_hud.csv` | HUD FY2026 Small Area FMRs | placeholder |
| `rent_zillow.csv` | Zillow Observed Rent Index | placeholder |
| `commute.csv` | Manual Google Maps lookups | placeholder |
| `places.csv` | OpenStreetMap via Overpass | placeholder |

## Limitations

- Placeholder data until the collected files are added.
- The map background needs an internet connection; the markers and everything else work offline.
- Events ("Hot News"), free-text profiles and the compare table are not built yet.
