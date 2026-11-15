"""Shared data shapes: the user's Profile and the facts we hold per area.

Fair housing: nothing here describes who lives in an area. Facts are rent,
travel time and counts of places only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

Importance = Literal["Must-have", "Important", "Nice"]
IMPORTANCE_LEVELS: list[str] = ["Nice", "Important", "Must-have"]
WEIGHTS: dict[str, int] = {"Must-have": 3, "Important": 2, "Nice": 1}

Bedroom = Literal["shared2br", "0br", "1br", "2br"]
BEDROOM_LABELS: dict[str, str] = {
    "shared2br": "Room in a shared 2-bed",
    "0br": "Studio",
    "1br": "1 bedroom",
    "2br": "2 bedroom",
}
# Shown next to the Estimate label wherever the shared-room rent appears.
SHARED_2BR_NOTE = "2-bed rent split in half"

Mode = Literal["transit", "drive"]

DESTINATIONS: list[str] = [
    "Rutgers College Ave",
    "Rutgers Busch",
    "Newark Penn Station",
]

# Interest key -> label shown to the user. Keys match `category` in places.csv.
INTERESTS: dict[str, str] = {
    "grocery": "Grocery & convenience stores",
    "gym": "Gyms",
    "park": "Parks",
    "cafe": "Cafes",
    "library": "Libraries",
    "live_music": "Live music venues",
    "sports": "Sports facilities",
    "restaurant": "Restaurants",
    "place_of_worship": "Places of worship",
}

# Data labels required on everything shown in the UI (CLAUDE.md rule 3).
LABEL_LIVE = "Live"
LABEL_DATASET = "Dataset"
LABEL_ESTIMATE = "Estimate"
LABEL_DEMO = "Demo data"
LABEL_MANUAL = "Manually collected"

# Tag in events_curated.csv -> the group it is shown under. "local" is not an
# interest the user picks; it is always shown as "Local events".
EVENT_TAG_GROUPS: dict[str, str] = {
    "jazz": "live_music",
    "live_music": "live_music",
    "fitness": "gym",
    "sports": "sports",
    "culture": "local",
    "community": "local",
}
LOCAL_EVENTS_LABEL = "Local events"

# Commute lookups all depart on a weekday morning. Priya's trip home from her
# late shift has not been looked up, and the app says so on every area.
NEWARK = "Newark Penn Station"
LATE_NIGHT_NOTE = "Late-night trip home from Newark: not checked"


class DestinationPref(BaseModel):
    name: str
    mode: Mode = "transit"
    max_minutes: int = Field(default=45, ge=1)
    importance: Importance = "Important"


class InterestPref(BaseModel):
    category: str
    importance: Importance = "Nice"


class Profile(BaseModel):
    budget: int | None = Field(default=None, ge=1)
    bedroom: Bedroom = "1br"
    # When a budget is set, this controls how much headroom matters.
    rent_importance: Importance = "Important"
    destinations: list[DestinationPref] = []
    interests: list[InterestPref] = []


@dataclass(frozen=True)
class Fact:
    """One value plus where it came from. `value` is never invented."""

    value: float
    label: str  # one of the LABEL_* constants
    source: str = ""
    as_of: str = ""
    note: str = ""
    label_detail: str = ""  # shown with the label, e.g. "Estimate: 2-bed rent split in half"
    as_of_word: str = "as of"


@dataclass(frozen=True)
class Event:
    """One manually collected event. Every event has a source URL."""

    zip: str
    group: str  # an interest key, or "local"
    title: str
    date: str  # ISO date, or "" when not given
    venue: str
    url: str
    collected_on: str
    label: str = LABEL_MANUAL


@dataclass
class AreaFacts:
    area: str
    zip: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    demo: bool = False
    # bedroom key -> HUD rent
    rent_hud: dict[str, Fact] = field(default_factory=dict)
    # Zillow ZORI covers all bedroom sizes together
    rent_zillow: Optional[Fact] = None
    # (destination, mode) -> minutes
    commute: dict[tuple[str, str], Fact] = field(default_factory=dict)
    # (destination, mode) -> why a reported value was set aside, e.g. an implausible time.
    # A flagged value is never used: it counts as missing until someone verifies it.
    commute_flags: dict[tuple[str, str], str] = field(default_factory=dict)
    # category -> count of places. A missing key means "not checked", not zero.
    places: dict[str, Fact] = field(default_factory=dict)


def demo_profile() -> Profile:
    """Priya, the demo persona from the pitch script."""
    return Profile(
        budget=1600,
        bedroom="shared2br",
        rent_importance="Important",
        destinations=[
            DestinationPref(name="Rutgers College Ave", mode="transit", max_minutes=40, importance="Must-have"),
            DestinationPref(name="Newark Penn Station", mode="transit", max_minutes=80, importance="Important"),
        ],
        interests=[
            InterestPref(category="live_music", importance="Important"),
            InterestPref(category="gym", importance="Nice"),
            InterestPref(category="sports", importance="Nice"),
        ],
    )


@dataclass(frozen=True)
class Place:
    """One OpenStreetMap place near an area. `name` is blank when OSM has none."""

    zip: str
    category: str  # an interest key
    name: str
    osm_id: str  # "node/123", "way/456" ...
    distance_m: Optional[float] = None  # from the area's centre point
    detail: str = ""  # e.g. "sport=cricket"
    osm_tag: str = ""  # e.g. "leisure=pitch"
    label: str = LABEL_DATASET
    source: str = ""
    as_of: str = ""
    lat: Optional[float] = None  # the place's own position, for the map
    lon: Optional[float] = None

    @property
    def url(self) -> str:
        """Link to the place on openstreetmap.org, or '' for placeholder rows."""
        kind, _, number = self.osm_id.partition("/")
        if kind in ("node", "way", "relation") and number.isdigit():
            return f"https://www.openstreetmap.org/{kind}/{number}"
        return ""
