"""Session state for the profile: one applied profile, plus the widgets that edit it.

- st.session_state["profile"] is the profile the ranking uses (the "applied" one).
- The widgets in the profile panel hold a pending copy. It becomes the applied
  profile only when the user clicks Apply.
- Anything that sets a profile from code (Priya's example, a landing draft, an
  applied refine) goes through set_profile(), which also loads the widgets.
  Call it only from a button callback or before the widgets are drawn.
"""
from __future__ import annotations

import streamlit as st

from ..models import DESTINATIONS, INTERESTS, Profile

LANDING, EXPLORE = "landing", "explore"

# Defaults for controls of things that are not in the profile yet.
DEST_DEFAULTS = {"mode": "transit", "max": 45, "imp": "Important"}
INTEREST_DEFAULT = "Nice"


def widget_values(profile: Profile) -> dict:
    """Widget key -> value for a profile."""
    v: dict = {
        "bedroom": profile.bedroom,
        "budget": profile.budget,
        "rent_importance": profile.rent_importance,
        "interests": [i.category for i in profile.interests],
    }
    dests = {d.name: d for d in profile.destinations}
    for name in DESTINATIONS:
        d = dests.get(name)
        v[f"dest_on_{name}"] = d is not None
        v[f"dest_mode_{name}"] = d.mode if d else DEST_DEFAULTS["mode"]
        v[f"dest_max_{name}"] = d.max_minutes if d else DEST_DEFAULTS["max"]
        v[f"dest_imp_{name}"] = d.importance if d else DEST_DEFAULTS["imp"]
    chosen = {i.category: i for i in profile.interests}
    for cat in INTERESTS:
        v[f"int_imp_{cat}"] = chosen[cat].importance if cat in chosen else INTEREST_DEFAULT
    return v


def load_widgets(profile: Profile) -> None:
    for key, value in widget_values(profile).items():
        st.session_state[key] = value


def set_profile(profile: Profile, draft_label: str = "") -> None:
    """Make this the applied profile and show it in the panel."""
    st.session_state["profile"] = profile.model_dump()
    st.session_state["draft_label"] = draft_label
    load_widgets(profile)


def applied_profile() -> Profile | None:
    data = st.session_state.get("profile")
    return Profile(**data) if data else None


def stage() -> str:
    return st.session_state.get("stage", LANDING)


def go(to: str) -> None:
    st.session_state["stage"] = to
