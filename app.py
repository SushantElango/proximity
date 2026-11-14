"""Proximity: Streamlit entry point.  Run with:  streamlit run app.py"""
import streamlit as st

from proximity.data import build_area_facts, load_all
from proximity.ui import explore

st.set_page_config(page_title="Proximity", page_icon="📍", layout="wide", initial_sidebar_state="collapsed")


@st.cache_data(show_spinner=False)
def load():
    bundle = load_all()
    return build_area_facts(bundle), bundle.issues


st.html(
    '<div style="display:flex;align-items:baseline;gap:0.9rem;flex-wrap:wrap">'
    '<span style="font-size:1.8rem;font-weight:800;letter-spacing:-0.03em;line-height:1">'
    f'Proximity<span style="color:{explore.ACCENT}">.</span></span>'
    '<span style="font-size:1rem;opacity:0.7">Proximity is power. Find neighborhoods near '
    "Rutgers–New Brunswick that fit your life, not just your budget.</span></div>"
)

areas, issues = load()
if not areas:
    st.error("No areas found. Add data/areas.csv (or run: python scripts/make_demo_data.py).")
    for issue in issues:
        st.warning(f"Data issue: {issue}")
    st.stop()

explore.render(areas, issues)
