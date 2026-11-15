"""Landing page: describe your situation, or load Priya's example."""
from __future__ import annotations

import streamlit as st

from ..assistant import answer_question, draft_profile
from ..models import demo_profile
from ..refine import AI, DEMOGRAPHIC_REPLY, NOT_UNDERSTOOD, starter_profile
from . import session

PITCH = "Find where to live by how you actually live"
# Priya's own words. This exact text is cached, so the landing demo works offline.
EXAMPLE = (
    "I'm a Rutgers junior sharing a 2-bed with a roommate. I have classes on College Ave and I work in Newark "
    "until 10:30pm on Tuesdays and Thursdays, and I don't have a car. I can pay $1,600 a month, and I love "
    "live music, plus a gym and somewhere to play sports."
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


def _open_results(proposal) -> None:
    _reset_results()
    st.session_state.pop("clarify", None)
    if not proposal.changes:
        session.set_profile(starter_profile(), NOT_UNDERSTOOD)
    else:
        label = AI_DRAFT_LABEL if proposal.source == AI else KEYWORD_DRAFT_LABEL
        if proposal.message:
            label = f"{proposal.message} {label}"
        session.set_profile(proposal.profile, label)
    session.go(session.EXPLORE)


def _describe() -> None:
    text = (st.session_state.get("landing_text") or "").strip()
    st.session_state["landing_message"] = ""
    st.session_state.pop("clarify", None)
    if not text:
        st.session_state["landing_message"] = "Write a sentence or two first, or try Priya's example."
        return
    draft = draft_profile(text)
    if draft.proposal.message == DEMOGRAPHIC_REPLY and not draft.proposal.changes:
        st.session_state["landing_message"] = DEMOGRAPHIC_REPLY
        return
    if draft.question:
        # One question, once. The answer is added to the description and the draft is redone.
        st.session_state["clarify"] = {"text": text, "question": draft.question}
        st.session_state["clarify_answer"] = ""
        return
    _open_results(draft.proposal)


def _answer() -> None:
    pending = st.session_state.get("clarify")
    if not pending:
        return
    answer = (st.session_state.get("clarify_answer") or "").strip()
    draft = answer_question(pending["text"], answer)
    if draft.proposal.message == DEMOGRAPHIC_REPLY and not draft.proposal.changes:
        st.session_state["landing_message"] = DEMOGRAPHIC_REPLY
        return
    _open_results(draft.proposal)


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
        pending = st.session_state.get("clarify")
        if pending:
            with st.container(border=True):
                st.markdown(f"**One quick question:** {pending['question']}")
                st.text_input(
                    "Your answer", key="clarify_answer", label_visibility="collapsed",
                    placeholder="For example: classes on College Ave most days, by bus",
                )
                c, d = st.columns(2)
                c.button("Continue", type="primary", use_container_width=True, key="clarify_go", on_click=_answer)
                d.button("Skip", use_container_width=True, key="clarify_skip", on_click=_answer, help="Carry on without answering")
        message = st.session_state.get("landing_message")
        if message:
            st.warning(message)
        st.caption(
            "We match on places, travel time and rent only. Proximity never uses who lives in an area."
        )
