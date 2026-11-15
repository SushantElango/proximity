"""Proximity: Streamlit entry point.  Run with:  streamlit run app.py"""
import streamlit as st

from proximity.data import TABLE_WORDS, build_area_facts, build_events, build_places, load_all
from proximity.ui import explore, landing, session

st.set_page_config(page_title="Proximity", page_icon="📍", layout="wide", initial_sidebar_state="collapsed")


@st.cache_data(show_spinner=False)
def load():
    bundle = load_all()
    events, event_issues = build_events(bundle)
    placeholders = [TABLE_WORDS[name] for name in bundle.placeholders]
    return build_area_facts(bundle), events, build_places(bundle), bundle.issues + event_issues, placeholders


areas, events, places, issues, placeholders = load()
if not areas:
    st.error("No areas found. Add data/areas.csv.")
    for issue in issues:
        st.warning(f"Data issue: {issue}")
    st.stop()

if session.stage() == session.LANDING:
    landing.render(explore.ACCENT)
    st.stop()

st.html(
    '<div style="display:flex;align-items:baseline;gap:0.9rem;flex-wrap:wrap">'
    '<span style="font-size:1.8rem;font-weight:800;letter-spacing:-0.03em;line-height:1">'
    f'Proximity<span style="color:{explore.ACCENT}">.</span></span>'
    f'<span style="font-size:1rem;opacity:0.7">{landing.PITCH}.</span></div>'
)

explore.render(areas, events, places, issues, placeholders)
