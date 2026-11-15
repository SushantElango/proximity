"""Landing page: describe your situation, or load Priya's example."""
from __future__ import annotations

import streamlit as st

from ..assistant import draft_profile
from ..models import demo_profile
from ..refine import AI, DEMOGRAPHIC_REPLY, NOT_UNDERSTOOD, starter_profile
from . import session

PITCH = "Find where to live by how you actually live"
EXAMPLE = (
    "Rutgers junior sharing a 2-bed, classes on College Ave, I work in Newark until 10:30pm Tue/Thu, "
    "no car, $1,600 a month, love live music, a gym and sports."
)
AI_DRAFT_LABEL = "AI draft — check and edit before results."
KEYWORD_DRAFT_LABEL = "Draft from your words — check and edit before results."


def _reset_results() -> None:
    for key in ("last_profile", "change_notes", "selected_area", "proposal", "last_map_click"):
        st.session_state.pop(key, None)


def _try_priya() -> None:
    """Loads Priya instantly. No API call."""
    _reset_results()
    session.set_profile(demo_profile())
    session.go(session.EXPLORE)


def _describe() -> None:
    text = (st.session_state.get("landing_text") or "").strip()
    st.session_state["landing_message"] = ""
    if not text:
        st.session_state["landing_message"] = "Write a sentence or two first, or try Priya's example."
        return
    proposal = draft_profile(text)
    if proposal.message == DEMOGRAPHIC_REPLY and not proposal.changes:
        st.session_state["landing_message"] = DEMOGRAPHIC_REPLY
        return
    _reset_results()
    if not proposal.changes:
        session.set_profile(starter_profile(), NOT_UNDERSTOOD)
    else:
        label = AI_DRAFT_LABEL if proposal.source == AI else KEYWORD_DRAFT_LABEL
        if proposal.message:
            label = f"{proposal.message} {label}"
        session.set_profile(proposal.profile, label)
    st.session_state["draft_changes"] = [c.__dict__ for c in proposal.changes]
    st.session_state["draft_source"] = proposal.source
    session.go(session.EXPLORE)


def render(accent: str) -> None:
    _, mid, _ = st.columns([1, 2.2, 1])
    with mid:
        st.html(
            '<div style="margin-top:8vh;text-align:center">'
            '<div style="font-size:3.4rem;font-weight:800;letter-spacing:-0.04em;line-height:1">'
            f'Proximity<span style="color:{accent}">.</span></div>'
            f'<div style="font-size:1.35rem;margin-top:0.8rem;opacity:0.8">{PITCH}</div>'
            '<div style="font-size:0.95rem;margin-top:0.4rem;opacity:0.6">'
            "Neighborhoods near Rutgers–New Brunswick, matched on commute, rent, places and events.</div></div>"
        )
        st.text_area(
            "Describe your situation", key="landing_text", height=130,
            placeholder=f"For example: {EXAMPLE}",
        )
        a, b = st.columns(2)
        a.button("Find my neighborhood", type="primary", use_container_width=True, on_click=_describe)
        b.button("Try Priya's example", use_container_width=True, on_click=_try_priya)
        message = st.session_state.get("landing_message")
        if message:
            st.warning(message)
        st.caption(
            "We match on places, travel time and rent only. Proximity never uses who lives in an area."
        )
