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

import math
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
    Place,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLACEHOLDER_SUBDIR = "placeholder"

# A transit time above this is treated as a likely lookup error, not a fact.
UNUSUAL_TRANSIT_MINUTES = 120
UNUSUAL_TEXT = "Unusual: please verify"

# Names used in collected files -> the destination names the app uses.
DESTINATION_ALIASES: dict[str, str] = {
    "Rutgers College Ave campus": "Rutgers College Ave",
    "Rutgers Busch campus": "Rutgers Busch",
}

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


def _depart(value: str) -> str:
    """'2026-11-17T08:30:00-05:00' -> 'Tue 08:30'. Plain times like '22:30' pass through."""
    if "T" not in value:
        return value
    try:
        d = pd.Timestamp(value)
    except (ValueError, TypeError):
        return value
    return f"{d:%a} {d:%H:%M}"


def _column_value(df: pd.DataFrame, column: str) -> str:
    """The latest non-blank value of an optional column, or '' when the column is absent."""
    if column not in df.columns:
        return ""
    values = sorted(v for v in df[column].dropna().astype(str).str.strip().unique() if v)
    return values[-1] if values else ""


def _label(demo: int, real_label: str) -> str:
    return LABEL_DEMO if int(demo) == 1 else real_label


def build_area_facts(bundle: DataBundle) -> list[AreaFacts]:
    """One AreaFacts per row of areas.csv, with every available fact attached."""
    t = bundle.tables
    areas: list[AreaFacts] = []

    # A category we have rows for was checked everywhere we have place data,
    # so an area with none of it has a real count of 0. Anything else is unknown.
    # Both are read before de-duplicating, so an area that keeps no rows still counts as checked.
    checked_categories = sorted(t["places.csv"]["category"].dropna().unique())
    zips_with_places = set(t["places.csv"]["zip"].unique())
    places = nearest_area_only(t["places.csv"], t["areas.csv"])

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
        # Anything that is not a number ("Not found", blanks) becomes missing, never a value.
        crows["minutes_num"] = pd.to_numeric(crows["minutes"], errors="coerce")
        crows = crows[crows["minutes_num"].notna()]
        crows["destination"] = crows["destination"].replace(DESTINATION_ALIASES)
        for (dest, mode), grp in crows.groupby(["destination", "mode"]):
            # Several departure times: use the slowest, so we never over-promise.
            r = grp.sort_values("minutes_num").iloc[-1]
            note = f"departing {_depart(_text(r['depart_time']))}" if _text(r["depart_time"]) else ""
            if len(grp) > 1:
                note = (note + "; " if note else "") + f"slowest of {len(grp)} lookups"
            if mode == "transit" and float(r["minutes_num"]) > UNUSUAL_TRANSIT_MINUTES:
                # Not used as a number anywhere. The area is unverified on this until checked.
                facts.commute_flags[(dest, mode)] = (
                    f"{UNUSUAL_TEXT} ({float(r['minutes_num']):.0f} min reported by {_text(r['source']) or 'unknown source'})"
                )
                continue
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
                    source="PLACEHOLDER - not real data" if is_demo else (_column_value(places, "source") or "OpenStreetMap via Overpass"),
                    as_of=_column_value(places, "fetched_on"),
                    note=(f"within {float(radius):,.0f} m of the area's centre point" if (radius := _column_value(places, "radius_m")) else ""),
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


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_area_only(places: pd.DataFrame, areas: pd.DataFrame) -> pd.DataFrame:
    """Keep each place once, under the area whose centre point is nearest.

    The search circles of neighbouring areas overlap, so the same OSM object can be
    fetched for two areas. Rows with no osm_id or no coordinates cannot be matched
    up and are kept as they are. A tie goes to the lower ZIP, so the result is stable.
    """
    if places.empty:
        return places
    centres = {a["zip"]: (_num(a["lat"]), _num(a["lon"])) for _, a in areas.iterrows()}
    best: dict[tuple[str, str], tuple[float, str]] = {}  # (category, osm_id) -> (distance, zip)
    distances: dict = {}
    for idx, r in places.iterrows():
        osm_id = _text(r["osm_id"])
        lat, lon = _num(r["lat"]), _num(r["lon"])
        clat, clon = centres.get(r["zip"], (None, None))
        if not osm_id or None in (lat, lon, clat, clon):
            continue
        d = (_distance_m(clat, clon, lat, lon), r["zip"])
        distances[idx] = d
        key = (_text(r["category"]), osm_id)
        if key not in best or d < best[key]:
            best[key] = d
    keep = [
        idx not in distances or distances[idx] == best[(_text(r["category"]), _text(r["osm_id"]))]
        for idx, r in places.iterrows()
    ]
    return places[keep]


def build_places(bundle: DataBundle) -> list[Place]:
    """Every row of places.csv as a Place, with its distance from the area's centre point."""
    areas = bundle.tables["areas.csv"]
    df = nearest_area_only(bundle.tables["places.csv"], areas)
    centres = {a["zip"]: (_num(a["lat"]), _num(a["lon"])) for _, a in areas.iterrows()}
    source = _column_value(df, "source") or "OpenStreetMap via Overpass"
    fetched = _column_value(df, "fetched_on")
    places: list[Place] = []
    for _, r in df.iterrows():
        lat, lon = _num(r["lat"]), _num(r["lon"])
        clat, clon = centres.get(r["zip"], (None, None))
        known = None not in (lat, lon, clat, clon)
        demo = int(r["demo"]) == 1
        places.append(Place(
            zip=r["zip"],
            category=_text(r["category"]),
            name=_text(r["name"]),
            osm_id=_text(r["osm_id"]),
            distance_m=_distance_m(clat, clon, lat, lon) if known else None,
            detail=_text(r["detail"]) if "detail" in df.columns else "",
            osm_tag=_text(r["osm_tag"]) if "osm_tag" in df.columns else "",
            label=LABEL_DEMO if demo else LABEL_DATASET,
            source="PLACEHOLDER - not real data" if demo else source,
            as_of=fetched,
            lat=lat,
            lon=lon,
        ))
    return places
