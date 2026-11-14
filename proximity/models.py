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

Bedroom = Literal["0br", "1br", "2br"]
BEDROOM_LABELS: dict[str, str] = {"0br": "Studio", "1br": "1 bedroom", "2br": "2 bedroom"}

Mode = Literal["transit", "drive"]

DESTINATIONS: list[str] = [
    "Rutgers College Ave",
    "Rutgers Busch",
    "Newark Penn Station",
]

# Interest key -> label shown to the user. Keys match `category` in places.csv.
INTERESTS: dict[str, str] = {
    "grocery": "Grocery stores",
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


class DestinationPref(BaseModel):
    name: str
    mode: Mode = "transit"
    max_minutes: int = Field(default=45, ge=1)
    importance: Importance = "Important"


class InterestPref(BaseModel):
    category: str
    importance: Importance = "Nice"


class Profile(BaseModel):
    budget: int = Field(ge=1)
    bedroom: Bedroom = "1br"
    # Budget is always a hard limit. This sets how much room under budget matters.
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
    # category -> count of places. A missing key means "not checked", not zero.
    places: dict[str, Fact] = field(default_factory=dict)


def demo_profile() -> Profile:
    """Priya, the demo persona from the pitch script."""
    return Profile(
        budget=1600,
        bedroom="1br",
        rent_importance="Important",
        destinations=[
            DestinationPref(name="Rutgers College Ave", mode="transit", max_minutes=35, importance="Must-have"),
            DestinationPref(name="Newark Penn Station", mode="transit", max_minutes=80, importance="Important"),
        ],
        interests=[
            InterestPref(category="live_music", importance="Important"),
            InterestPref(category="gym", importance="Nice"),
            InterestPref(category="sports", importance="Nice"),
        ],
    )
