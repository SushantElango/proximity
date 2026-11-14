# Proximity — Build Brief for Claude (HackPrinceton Fall 2026)

You are my coding partner for a 36-hour hackathon. I'm Sushant, the team lead. Read this whole file before writing any code. It is the source of truth for the project. If anything here conflicts with what I say later in chat, ask me which one wins.

---

## 1. Hard facts

- **Event:** HackPrinceton Fall 2026. Hacking ends **Sun Nov 15, 8:00am ET**. We submit by **7:00am**.
- **Demo:** live and in person, **2 minutes maximum**. Judges score what they see working.
- **Repo:** public GitHub repo created during the hackathon. The README is part of what we show.
- **Team:** 2 people, both at their first hackathon.
  - Sushant: Python, SQL, data, pitching. Not a frontend or ML engineer.
  - Siddhesh: Python, Streamlit, basic Flask/HTML.
- **Stack: Python + Streamlit.** No React and no custom ML. Don't add a framework unless I approve it.

## 2. What we are building

**Proximity** ("Proximity is power") is a website that helps someone moving to an unfamiliar area find neighborhoods that fit their life, not just their budget.

- **Scope:** students and early-career renters moving near Rutgers–New Brunswick, NJ. There are 8 candidate areas (section 5).
- **Core question:** "Where can I live the life I want, within my practical limits?"
- **Industry feedback we are acting on:**
  - CoStar's CEO said nobody has built a way to *ask questions of a neighborhood*. He said to focus on the human side (preferences, taste, the feeling of "you understood me") and to stay away from pure numbers.
  - A mentor said to use code for everything that can be calculated and AI only for interpreting language.

## 3. Non-negotiable rules

1. **Fair housing.** Never show, score or filter by who lives in an area: no race, religion, national origin, family status, household composition, income of residents or "demographic" breakdowns. We match on **places, amenities, events, travel and rent only**. Places of worship and cultural or religious events count as places and events. Show them when the user asks for them. Never present them as describing residents.
2. **Never invent data.** Every number shown must come from a data file or a calculation. Missing values display as "Not checked / no data". A missing value never counts as a match.
3. **Label every data type** in the UI: `Live` (API), `Dataset` (with source and date), `Estimate`, `Demo data`.
4. **AI explains; code decides.** Eligibility, scores and rankings are deterministic Python. The LLM only:
   - (a) turns free text into a structured profile the user can edit
   - (b) writes explanations using only the computed facts passed to it
5. **Check docs before using a library or API.** Check the current official docs for the version you're using. If you can't check, say so and mark the code `# UNVERIFIED`.
6. **The demo must never break.** Every external call (LLM, Ticketmaster, Overpass) needs a timeout and a cached or offline fallback. The app must run fully offline from cached files with `DEMO_MODE=1`.

## 4. User flow and screens

### Stage 1: Landing page
- Logo, the tagline "Proximity is power", and one large text box: "Describe the life you want to live."
- Example placeholder: *"Rutgers junior, classes Mon/Wed/Fri mornings on College Ave, work in Newark until 10:30pm Tue/Thu, no car, $1,600/month for a 1-bedroom, love jazz bars, a gym, and cricket."*
- Button: **Find my neighborhood**.

### Stage 2: Explore view (3 columns, inspired by the Google Maps layout)
| Left: **Prompt Zone** | Center: **Map** | Right: **Hot News** |
|---|---|---|
| Profile the user can edit: budget, bedroom type, destinations, travel mode, max commute, interests, and importance per item (Must-have / Important / Nice). A text box to refine the profile ("make transit more important"). | All 8 areas plotted; the **top 3 matches numbered 1–3** and highlighted. Click an area to select it. Below the map: 3 recommendation cards. | For the selected area × the user's interests: upcoming events (Ticketmaster, labeled Live), interest places (OSM, labeled Dataset), and curated local events (labeled Demo data or Manually collected, with source link). |

### Recommendation card (one per top-3 area)
- Area name and rank, plus "Meets X of Y evaluated priorities"
- **Why it matched:** 2–3 bullets, each tied to a number
- **Main compromise:** 1 bullet
- **Missing / not checked:** a list
- Rent estimate with its source and date

### Re-rank interaction (central to the demo)
When the user changes a weight or a constraint:
- recompute the scores
- show a short "What changed" note, for example: *"Metuchen moved from #3 to #1 because you raised transit importance; its transit time to Newark (38 min) is the shortest of the eligible areas."*
- the explanation comes from the difference in score components before and after

### Compare (simple)
A side-by-side table for 2–3 selected areas, with the same rows and units: rent, commute per destination, interest-place counts, missing fields.

## 5. Data (files in `data/`, collected by Siddhesh, plus scripts)

Candidate areas (ZIP is the join key):

| Area | ZIP |
|---|---|
| New Brunswick | 08901 |
| Highland Park | 08904 |
| Piscataway | 08854 |
| Edison | 08817 |
| Metuchen | 08840 |
| North Brunswick | 08902 |
| East Brunswick | 08816 |
| Somerset (Franklin Twp) | 08873 |

Expected files. If a file is missing, build against this schema with an obviously fake placeholder row marked `demo=1`, so the code doesn't block on data.

| File | Columns | Source |
|---|---|---|
| `areas.csv` | area, zip, lat, lon | Census ZCTA Gazetteer (internal point) |
| `rent_hud.csv` | zip, fmr_0br, fmr_1br, fmr_2br, source, as_of | HUD FY2026 Small Area FMRs |
| `rent_zillow.csv` | zip, zori, month, source | Zillow Observed Rent Index (ZIP) |
| `commute.csv` | zip, destination, mode (transit/drive), minutes, depart_time, checked_on, source | Manual Google Maps lookups |
| `places.csv` | zip, category, name, lat, lon, osm_id | OpenStreetMap via Overpass (script: `scripts/fetch_osm.py`) |
| `events_curated.csv` | zip_or_area, interest, title, date, venue, url, collected_on | Manually collected from real web pages; every row has a URL |
| `.env` | `TICKETMASTER_API_KEY`, `LLM_API_KEY` | Never commit. Add `.env.example`. |

Destinations: Rutgers College Ave campus, Rutgers Busch campus, Newark Penn Station.

Interest → OSM category mapping (verify tags on the OSM wiki before relying on them):
- grocery → shop=supermarket, shop=convenience
- gym → leisure=fitness_centre
- park → leisure=park
- cafe → amenity=cafe
- library → amenity=library
- jazz/live music → amenity=bar, amenity=pub, amenity=music_venue, amenity=nightclub
- sports → leisure=sports_centre, leisure=pitch (with sport=*)
- cuisine (e.g., Indian) → amenity=restaurant + cuisine=*; shop=supermarket for ethnic groceries if tagged
- place of worship (only if the user asks) → amenity=place_of_worship

Ticketmaster: Discovery API v2, `/discovery/v2/events.json`, filtered by area location + radius + classification (Music / Sports). Check the current docs for the geo parameter: `latlong` is marked deprecated in favor of `geoPoint`. Cache responses to `data/cache/`.

## 6. Matching method (deterministic, `proximity/scoring.py`)

1. **Eligibility (must-haves).**
   - Rent: chosen-bedroom estimate ≤ budget. Use HUD as primary, Zillow as secondary, and show both if both exist.
   - Commute: minutes ≤ the user's max for each must-have destination.
   - Missing data for a must-have → status `Unverified`. The area is not eliminated, but it's clearly flagged and ranked after verified areas.
   - If nothing is eligible, say which constraints conflict and suggest which one to relax.
2. **Score.** For each preference *i* with weight *w_i* (Must-have=3, Important=2, Nice=1), compute *s_i* in [0,1]:
   - commute: lower is better, min-max normalized across areas
   - rent headroom: (budget − rent)/budget, clipped
   - interest place counts within the area: min-max normalized
   - Score = Σ w_i·s_i / Σ w_i, **over evaluated preferences only**. Report coverage separately ("evaluated 5 of 6").
3. **Explanation inputs.** Return a dict per area: components, raw values, sources, missing fields. Return the before/after difference for re-ranks.
4. **Tests.** Unit tests in `tests/` covering:
   - the same inputs give the same ranking
   - missing data never raises a score
   - a weight change moves the ranking in the expected direction
   - the no-eligible-area message appears when it should

## 7. LLM layer (`proximity/llm.py`)

- `parse_profile(text) -> Profile`: returns JSON matching a Pydantic `Profile` schema. Show the result in the Prompt Zone for the user to confirm or edit.
- **Ask one clarifying question** only when a vague word would change the result. Example: "peaceful" could mean less nightlife, less traffic or more green space.
- `explain(area_facts) -> str`: uses only the facts passed in. No outside knowledge about places.
- **Fallback:** if there's no key, a timeout or invalid JSON, use a keyword parser and template explanations. The app must still work.
- **Ask me which LLM provider/key we have before writing this module.**

## 8. Project structure
```
proximity/
  app.py              # Streamlit entry
  proximity/
    data.py           # load + validate CSVs, data labels
    scoring.py        # eligibility, scores, diffs
    llm.py            # parse_profile, explain, fallbacks
    hotnews.py        # Ticketmaster + places + curated events, cached
    ui/               # landing, explore, compare components
  scripts/fetch_osm.py
  data/ (+ data/cache/)
  tests/
  README.md  .env.example  requirements.txt (pinned versions)
```

## 9. Build order (finish each layer before starting the next; keep `main` runnable)

| # | Layer | Done when | Timebox |
|---|---|---|---|
| L1 | Data loading + scoring + Explore view with a manual profile form, map, 3 cards, re-rank with "What changed" | Demo persona → 3 explained results → change a weight → visible reasoned re-rank | 4h |
| L2 | Hot News panel (OSM places + curated events, then Ticketmaster live) | Selecting an area shows interest-matched items, all labeled | 3h |
| L3 | Landing page + LLM profile parsing + clarifying question + LLM explanations | Free text → editable profile → results | 3h |
| L4 | Compare table, polish, README, demo mode | Full demo runs offline | 2h |

Feature freeze is **Sun 12:00am**. After that: bug fixes, README, demo video, rehearsal only.

## 10. How I want you to work

- **Plan, then build.** Before each layer, give me a short plan (files, functions, risks) and wait for my OK.
- Small, working increments. After each one, tell me exactly how to run and check it, then suggest a git commit message.
- When something takes 50% longer than its timebox, stop and offer three options: cut it, fake it with labeled demo data, or push through.
- Don't silently change scope, the stack, or the rules in section 3.
- Write a clear README as we go: problem, demo GIF/screens, how it works, data sources with dates, limitations, how to run.
- If you're unsure what I want, ask **one** question.

## 11. Demo script (what everything serves)

1. "Proximity is power." Introduce Priya, a Rutgers junior who works late in Newark and has no car.
2. Type her description → the profile appears → correct one interpretation.
3. 3 matches on the map. Open #1: why it matched, its compromise, what's missing.
4. Hot News for that area: jazz nights and cricket clubs near her.
5. "Make transit more important" → the ranking changes, with the reason shown.
6. Close: next steps to visit the shortlisted areas, plus the business direction (demand side, routed to listings).

**Start now:** confirm you've read this, list anything ambiguous (max 5 items), then propose the L1 plan.
