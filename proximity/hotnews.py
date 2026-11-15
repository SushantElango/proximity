"""Hot News: what is on and what is nearby for one area, matched to the user's interests.

Only real rows are returned: curated events (each with its source URL) and
OpenStreetMap places. Gaps stay empty. Nothing here affects the Fit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import INTERESTS, LOCAL_EVENTS_LABEL, Event, Place

# How to describe places OSM has no name for, per interest.
UNNAMED_WORDS: dict[str, str] = {
    "sports": "pitches and courts",
    "park": "parks",
    "grocery": "shops",
    "gym": "gyms",
    "cafe": "cafes",
    "library": "libraries",
    "live_music": "bars and venues",
    "restaurant": "restaurants",
    "place_of_worship": "places of worship",
}
UNNAMED_ONE: dict[str, str] = {
    "sports": "pitch or court",
    "park": "park",
    "grocery": "shop",
    "gym": "gym",
    "cafe": "cafe",
    "library": "library",
    "live_music": "bar or venue",
    "restaurant": "restaurant",
    "place_of_worship": "place of worship",
}


def places_for(places: list[Place], zip_code: str, category: str) -> tuple[list[Place], int]:
    """(named places nearest first, count of unnamed places) for one area and interest.

    Unnamed places are counted, never listed: there is nothing to call them.
    """
    mine = [p for p in places if p.zip == zip_code and p.category == category]
    seen: set[str] = set()
    named: list[Place] = []
    unnamed = 0
    for p in sorted(mine, key=lambda p: (p.distance_m is None, p.distance_m or 0.0, p.name, p.osm_id)):
        if p.osm_id in seen:
            continue
        seen.add(p.osm_id)
        if p.name:
            named.append(p)
        else:
            unnamed += 1
    return named, unnamed


def unnamed_line(category: str, count: int) -> str:
    """'+ 39 unnamed pitches and courts', or '' when there are none."""
    if count <= 0:
        return ""
    if count == 1:
        return f"+ 1 unnamed {UNNAMED_ONE.get(category, 'place')}"
    return f"+ {count} unnamed {UNNAMED_WORDS.get(category, 'places')}"


def distance_text(metres: float | None) -> str:
    if metres is None:
        return "distance not known"
    return f"{metres / 1000:.1f} km from centre"


def curated_for(events: list[Event], zip_code: str, group: str, today: str) -> list[Event]:
    """Upcoming curated events for one area and group, soonest first. Undated events go last.

    `group` is an interest key or "local". `today` is an ISO date; earlier events are left out.
    """
    rows = [e for e in events if e.zip == zip_code and e.group == group and (not e.date or e.date >= today)]
    return sorted(rows, key=lambda e: (e.date == "", e.date, e.title))


@dataclass
class Section:
    key: str  # interest key, or "local"
    label: str
    events: list[Event] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    unnamed: int = 0
    has_places: bool = True  # False for "Local events", which is events only

    @property
    def empty(self) -> bool:
        return not self.events and not self.places and self.unnamed == 0


def hot_news(zip_code: str, interests: list[str], events: list[Event], places: list[Place], today: str) -> list[Section]:
    """One section per selected interest, in the user's order, then Local events (always)."""
    sections: list[Section] = []
    for key in interests:
        named, unnamed = places_for(places, zip_code, key)
        sections.append(Section(
            key=key, label=INTERESTS.get(key, key),
            events=curated_for(events, zip_code, key, today), places=named, unnamed=unnamed,
        ))
    sections.append(Section(
        key="local", label=LOCAL_EVENTS_LABEL,
        events=curated_for(events, zip_code, "local", today), has_places=False,
    ))
    return sections


def place_detail(place: Place) -> str:
    """'cricket', 'indian' ... from the OSM sport/cuisine tags, or '' when not tagged."""
    for part in place.detail.split(";"):
        key, _, value = part.partition("=")
        if key in ("sport", "cuisine") and value:
            return value.replace("_", " ").replace(";", ", ")
    return ""
