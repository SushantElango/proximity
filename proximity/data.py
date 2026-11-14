"""Load and validate the CSVs in data/, and turn them into per-area facts.

Rules this module enforces:
- A missing file, column or value becomes "no data". It never becomes a number.
- Every fact carries a label (Dataset / Estimate / Demo data), source and date.
- Rows with demo=1 are placeholders and are always labeled "Demo data".
- A real file in data/ always wins. If it is absent, the matching file in
  data/placeholder/ is used and every row is forced to demo=1. So dropping
  the real commute.csv into data/ switches the app over with no code change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .models import (
    EVENT_TAG_GROUPS,
    LABEL_DATASET,
    LABEL_DEMO,
    LABEL_ESTIMATE,
    SHARED_2BR_NOTE,
    AreaFacts,
    Event,
    Fact,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLACEHOLDER_SUBDIR = "placeholder"

# file -> required columns (CLAUDE.md section 5). `demo` is optional, default 0.
SCHEMAS: dict[str, list[str]] = {
    "areas.csv": ["area", "zip", "lat", "lon"],
    "rent_hud.csv": ["zip", "fmr_0br", "fmr_1br", "fmr_2br", "source", "as_of"],
    "rent_zillow.csv": ["zip", "zori", "month", "source"],
    "commute.csv": ["zip", "destination", "mode", "minutes", "depart_time", "checked_on", "source"],
    "places.csv": ["zip", "category", "name", "lat", "lon", "osm_id"],
    "events_curated.csv": ["zip_or_area", "interest", "title", "date", "venue", "url", "collected_on"],
}

# What each table feeds, in words, for the "placeholder in use" notice.
TABLE_WORDS: dict[str, str] = {
    "areas.csv": "area locations",
    "rent_hud.csv": "HUD rent",
    "rent_zillow.csv": "Zillow rent",
    "commute.csv": "commute times",
    "places.csv": "place counts",
    "events_curated.csv": "events",
}


@dataclass
class DataBundle:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)  # shown in the UI, never hidden
    placeholders: list[str] = field(default_factory=list)  # tables containing demo=1 rows


def _clean_zip(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.split(".").str[0].str.zfill(5)


def load_table(name: str, data_dir: Path = DATA_DIR) -> tuple[pd.DataFrame, list[str]]:
    """Read one CSV. Returns an empty table (with the right columns) on any problem."""
    columns = SCHEMAS[name]
    empty = pd.DataFrame(columns=columns + ["demo"])
    path = data_dir / name
    from_placeholder = False
    if not path.exists():
        path = data_dir / PLACEHOLDER_SUBDIR / name
        from_placeholder = True
        if not path.exists():
            return empty, [f"{name}: file not found, treated as no data"]
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=True)
    except Exception as exc:  # unreadable file must not crash the demo
        return empty, [f"{name}: could not be read ({exc}), treated as no data"]

    df.columns = [c.strip() for c in df.columns]
    issues: list[str] = []
    missing = [c for c in columns if c not in df.columns]
    if missing:
        issues.append(f"{name}: missing columns {missing}, those values are treated as no data")
        for c in missing:
            df[c] = pd.NA
    if "demo" not in df.columns:
        df["demo"] = "0"
    df["demo"] = pd.to_numeric(df["demo"], errors="coerce").fillna(0).astype(int)
    if from_placeholder:
        df["demo"] = 1  # anything from data/placeholder/ is never presented as real
    if "zip" in df.columns:
        df = df[df["zip"].notna()].copy()
        df["zip"] = _clean_zip(df["zip"])
    for c in df.columns:
        if df[c].dtype == object or str(df[c].dtype).startswith("str"):
            df[c] = df[c].str.strip()
    return df, issues


def load_all(data_dir: Path = DATA_DIR) -> DataBundle:
    bundle = DataBundle()
    for name in SCHEMAS:
        df, issues = load_table(name, data_dir)
        bundle.tables[name] = df
        bundle.issues.extend(issues)
        if len(df) and int(df["demo"].max()) == 1:
            bundle.placeholders.append(name)
    return bundle


def _num(value) -> float | None:
    """Parse a number, returning None for blanks or junk. Never guesses."""
    n = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(n) else float(n)


def _text(value) -> str:
    return "" if pd.isna(value) else str(value)


def _label(demo: int, real_label: str) -> str:
    return LABEL_DEMO if int(demo) == 1 else real_label


def build_area_facts(bundle: DataBundle) -> list[AreaFacts]:
    """One AreaFacts per row of areas.csv, with every available fact attached."""
    t = bundle.tables
    areas: list[AreaFacts] = []

    places = t["places.csv"]
    # A category we have rows for was checked everywhere we have place data,
    # so an area with none of it has a real count of 0. Anything else is unknown.
    checked_categories = sorted(places["category"].dropna().unique())
    zips_with_places = set(places["zip"].unique())

    for _, a in t["areas.csv"].iterrows():
        z = a["zip"]
        facts = AreaFacts(
            area=_text(a["area"]),
            zip=z,
            lat=_num(a["lat"]),
            lon=_num(a["lon"]),
            demo=int(a["demo"]) == 1,
        )

        hud = t["rent_hud.csv"]
        for _, r in hud[hud["zip"] == z].iterrows():
            for bedroom in ("0br", "1br", "2br"):
                value = _num(r[f"fmr_{bedroom}"])
                if value is not None:
                    facts.rent_hud[bedroom] = Fact(
                        value=value,
                        label=_label(r["demo"], LABEL_DATASET),
                        source=_text(r["source"]),
                        as_of=_text(r["as_of"]),
                    )
            # A room in a shared 2-bed: half the 2-bed rent. An estimate, labeled as one.
            two_bed = _num(r["fmr_2br"])
            if two_bed is not None:
                facts.rent_hud["shared2br"] = Fact(
                    value=float(round(two_bed / 2)),
                    label=_label(r["demo"], LABEL_ESTIMATE),
                    source=_text(r["source"]),
                    as_of=_text(r["as_of"]),
                    label_detail=SHARED_2BR_NOTE,
                )

        zil = t["rent_zillow.csv"]
        zrows = zil[zil["zip"] == z].sort_values("month")
        if len(zrows):
            r = zrows.iloc[-1]  # latest month
            value = _num(r["zori"])
            if value is not None:
                facts.rent_zillow = Fact(
                    value=float(round(value)),  # whole dollars
                    label=_label(r["demo"], LABEL_DATASET),
                    source=_text(r["source"]),
                    as_of=_text(r["month"]),
                    note="covers all bedroom sizes, not bedroom-specific",
                )

        com = t["commute.csv"]
        crows = com[com["zip"] == z].copy()
        crows["minutes_num"] = pd.to_numeric(crows["minutes"], errors="coerce")
        crows = crows[crows["minutes_num"].notna()]
        for (dest, mode), grp in crows.groupby(["destination", "mode"]):
            # Several departure times: use the slowest, so we never over-promise.
            r = grp.sort_values("minutes_num").iloc[-1]
            note = f"departing {_text(r['depart_time'])}" if _text(r["depart_time"]) else ""
            if len(grp) > 1:
                note = (note + "; " if note else "") + f"slowest of {len(grp)} lookups"
            facts.commute[(dest, mode)] = Fact(
                value=float(r["minutes_num"]),
                label=_label(r["demo"], LABEL_ESTIMATE),
                source=_text(r["source"]),
                as_of=_text(r["checked_on"]),
                note=note,
            )

        if z in zips_with_places:
            mine = places[places["zip"] == z]
            for cat in checked_categories:
                rows = mine[mine["category"] == cat]
                all_cat = places[places["category"] == cat]
                is_demo = int(all_cat["demo"].max()) == 1
                facts.places[cat] = Fact(
                    value=float(len(rows)),
                    label=_label(1 if is_demo else 0, LABEL_DATASET),
                    source="PLACEHOLDER - not real data" if is_demo else "OpenStreetMap via Overpass",
                )

        areas.append(facts)
    return areas


def build_events(bundle: DataBundle) -> tuple[list[Event], list[str]]:
    """Curated events, keyed to an area ZIP. Rows without a URL or a known area are dropped and reported."""
    df = bundle.tables.get("events_curated.csv")
    areas = bundle.tables["areas.csv"]
    known_zips = set(areas["zip"])
    name_to_zip = {str(n).strip().lower(): z for n, z in zip(areas["area"], areas["zip"])}
    events: list[Event] = []
    issues: list[str] = []
    if df is None:
        return events, issues
    for i, r in df.iterrows():
        title, url = _text(r["title"]), _text(r["url"])
        where = _text(r["zip_or_area"])
        candidate = where.split(".")[0].zfill(5) if where.replace(".", "").isdigit() else name_to_zip.get(where.lower())
        if candidate not in known_zips:
            issues.append(f"events_curated.csv row {i + 2}: area '{where}' not recognised, row skipped")
            continue
        if not title or not url.lower().startswith(("http://", "https://")):
            issues.append(f"events_curated.csv row {i + 2}: missing title or source URL, row skipped")
            continue
        date = pd.to_datetime(_text(r["date"]), errors="coerce")
        events.append(Event(
            zip=candidate,
            group=EVENT_TAG_GROUPS.get(_text(r["interest"]).lower(), "local"),
            title=title,
            date="" if pd.isna(date) else date.strftime("%Y-%m-%d"),
            venue=_text(r["venue"]),
            url=url,
            collected_on=_text(r["collected_on"]),
            label=LABEL_DEMO if int(r["demo"]) == 1 else "Manually collected",
        ))
    events.sort(key=lambda e: (e.date == "", e.date, e.title))
    return events, issues
