"""Explore view, three columns: Prompt Zone | Map | selected match + Hot News."""
from __future__ import annotations

from html import escape

import pandas as pd
import pydeck as pdk
import streamlit as st

from .. import explain as ex
from ..models import (
    BEDROOM_LABELS,
    DESTINATIONS,
    IMPORTANCE_LEVELS,
    INTERESTS,
    LABEL_DATASET,
    LABEL_DEMO,
    LABEL_ESTIMATE,
    LABEL_LIVE,
    AreaFacts,
    DestinationPref,
    Fact,
    InterestPref,
    Profile,
    demo_profile,
)
from ..scoring import (
    ELIGIBLE,
    UNVERIFIED,
    AreaResult,
    diff_rankings,
    no_eligible_info,
    score_areas,
    top_matches,
)

ACCENT = "#D9480F"
_BADGE_COLOR = {LABEL_LIVE: "green", LABEL_DATASET: "blue", LABEL_ESTIMATE: "violet", LABEL_DEMO: "orange"}

_TOP_COLOR = [217, 72, 15]
_OTHER_COLOR = [70, 100, 150]
_UNVERIFIED_COLOR = [150, 150, 150]
_INELIGIBLE_COLOR = [200, 200, 200]

MAP_HEIGHT = 600
PANEL_HEIGHT = 640  # side panels scroll inside this height so the page fits one screen


def badge(label: str) -> str:
    """Markdown badge for a data label. Every number in the UI gets one."""
    return f":{_BADGE_COLOR.get(label, 'gray')}-badge[{label}]"


def fact_caption(fact: Fact | None) -> str:
    if fact is None:
        return "Not checked / no data"
    parts = [badge(fact.label)]
    if fact.source:
        parts.append(fact.source)
    if fact.as_of:
        parts.append(f"as of {fact.as_of}")
    if fact.note:
        parts.append(fact.note)
    return " · ".join(parts)


def _rank_title(rank: int | None, name: str, size: str = "1.15rem") -> None:
    """Area name with the same numbered dot the map uses."""
    dot = (
        f'<span style="display:inline-flex;align-items:center;justify-content:center;width:1.9rem;height:1.9rem;'
        f'border-radius:50%;background:{ACCENT};color:#fff;font-weight:700;font-size:0.95rem;flex:none">{rank}</span>'
        if rank is not None else ""
    )
    st.html(
        f'<div style="display:flex;align-items:center;gap:0.6rem">{dot}'
        f'<span style="font-size:{size};font-weight:700;line-height:1.2">{escape(name)}</span></div>'
    )


# --------------------------------------------------------------------------
# Left: Prompt Zone
# --------------------------------------------------------------------------

def _importance(label: str, default: str, key: str, visible: bool = True) -> str:
    value = st.segmented_control(
        label, IMPORTANCE_LEVELS, default=default, required=True, key=key,
        label_visibility="visible" if visible else "collapsed",
    )
    return value or default


def profile_form(available_interests: list[str]) -> Profile:
    base = demo_profile()
    base_dest = {d.name: d for d in base.destinations}
    base_int = {i.category: i for i in base.interests}

    st.markdown("### Your profile")
    st.caption("Starts as Priya, our demo renter. Change anything and your matches update.")

    with st.container(border=True):
        st.markdown("**:material/payments: Rent**")
        c1, c2 = st.columns(2)
        budget = c1.number_input("Budget ($/mo)", min_value=500, max_value=6000, value=base.budget, step=50, key="budget")
        bedroom = c2.selectbox(
            "Bedrooms", list(BEDROOM_LABELS), index=list(BEDROOM_LABELS).index(base.bedroom),
            format_func=BEDROOM_LABELS.get, key="bedroom",
        )
        rent_importance = _importance("Room under budget matters", base.rent_importance, "rent_importance")
        st.caption("Your budget is always a hard limit.")

    destinations: list[DestinationPref] = []
    with st.container(border=True):
        st.markdown("**:material/commute: Where you need to get to**")
        for name in DESTINATIONS:
            d = base_dest.get(name)
            if st.checkbox(name, value=d is not None, key=f"dest_on_{name}"):
                a, b = st.columns(2)
                mode = a.selectbox(
                    "Travel by", ["transit", "drive"], index=0 if (d is None or d.mode == "transit") else 1,
                    key=f"dest_mode_{name}", format_func=str.capitalize,
                )
                max_minutes = b.number_input(
                    "Max minutes", min_value=5, max_value=180, value=d.max_minutes if d else 45, step=5,
                    key=f"dest_max_{name}",
                )
                importance = _importance(
                    f"Importance of {name}", d.importance if d else "Important", f"dest_imp_{name}", visible=False,
                )
                destinations.append(
                    DestinationPref(name=name, mode=mode, max_minutes=int(max_minutes), importance=importance)
                )

    interests: list[InterestPref] = []
    with st.container(border=True):
        st.markdown("**:material/local_activity: What you want nearby**")
        options = [k for k in INTERESTS if k != "place_of_worship"] + ["place_of_worship"]
        chosen = st.multiselect(
            "Interests", options, default=[i.category for i in base.interests],
            format_func=INTERESTS.get, key="interests", label_visibility="collapsed",
        )
        for cat in chosen:
            default = base_int[cat].importance if cat in base_int else "Nice"
            importance = _importance(INTERESTS[cat], default, f"int_imp_{cat}")
            if cat not in available_interests:
                st.caption("Not checked / no data for this interest yet. It will not count as a match.")
            interests.append(InterestPref(category=cat, importance=importance))

    return Profile(
        budget=int(budget), bedroom=bedroom, rent_importance=rent_importance,
        destinations=destinations, interests=interests,
    )


# --------------------------------------------------------------------------
# Map
# --------------------------------------------------------------------------

def _map(results: list[AreaResult], facts: dict[str, AreaFacts], selected: str | None, height: int = MAP_HEIGHT) -> str | None:
    """Draw all areas. Returns the area the user clicked, if any."""
    top = {r.area: r.rank for r in top_matches(results)}
    rows = []
    for r in results:
        f = facts[r.area]
        if f.lat is None or f.lon is None:
            continue
        if r.area in top:
            color, radius = _TOP_COLOR, 16
        elif r.status == ELIGIBLE:
            color, radius = _OTHER_COLOR, 9
        elif r.status == UNVERIFIED:
            color, radius = _UNVERIFIED_COLOR, 9
        else:
            color, radius = _INELIGIBLE_COLOR, 8
        rows.append({
            "area": r.area, "lat": f.lat, "lon": f.lon, "color": color, "radius": radius,
            "number": str(top[r.area]) if r.area in top else "",
            "status": r.status if r.rank is None else f"#{r.rank} · {r.status}",
            "line": [20, 20, 20] if r.area == selected else [255, 255, 255],
            "line_width": 4 if r.area == selected else 2,
        })
    if not rows:
        st.info("No area coordinates available, so the map cannot be drawn.")
        return None

    df = pd.DataFrame(rows)
    layers = [
        pdk.Layer(
            "TextLayer", id="names", data=df, get_position="[lon, lat]", get_text="area",
            get_size=13, get_color=[30, 30, 30], get_pixel_offset=[0, 30],
            background=True, get_background_color=[255, 255, 255, 225], background_padding=[5, 3],
            get_text_anchor="'middle'", get_alignment_baseline="'center'",
        ),
        pdk.Layer(
            "ScatterplotLayer", id="areas", data=df, get_position="[lon, lat]",
            get_fill_color="color", get_radius="radius", radius_units=pdk.types.String("pixels"),
            stroked=True, get_line_color="line", get_line_width="line_width",
            line_width_units=pdk.types.String("pixels"),
            pickable=True, parameters={"depthTest": False},
        ),
        pdk.Layer(
            "TextLayer", id="numbers", data=df[df["number"] != ""], get_position="[lon, lat]",
            get_text="number", get_size=18, get_color=[255, 255, 255], font_weight=700,
            get_text_anchor="'middle'", get_alignment_baseline="'center'", parameters={"depthTest": False},
        ),
    ]
    deck = pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(
            latitude=float(df["lat"].mean()), longitude=float(df["lon"].mean()), zoom=11.1,
        ),
        layers=layers,
        tooltip={"text": "{area}\n{status}"},
    )
    event = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object", key="area_map", height=height)
    try:
        picked = event.selection.objects.get("areas", [])
        return picked[0]["area"] if picked else None
    except (AttributeError, KeyError, IndexError, TypeError):
        return None  # selection not available: the card buttons still work


# --------------------------------------------------------------------------
# Right: the selected match, with < > to step through matches in order
# --------------------------------------------------------------------------

def _select(area: str) -> None:
    st.session_state["selected_area"] = area


def _match_nav(selected: AreaResult, ranked: list[AreaResult]) -> None:
    """< Match 2 of 6 > : steps through the matches in rank order."""
    idx = next((i for i, r in enumerate(ranked) if r.area == selected.area), None)
    prev_col, mid, next_col = st.columns([1, 3, 1], vertical_alignment="center")
    if idx is None:
        prev_area = None
        next_area = ranked[0].area if ranked else None
        label = "Not a match"
    else:
        prev_area = ranked[idx - 1].area if idx > 0 else None
        next_area = ranked[idx + 1].area if idx + 1 < len(ranked) else None
        label = f"Match {idx + 1} of {len(ranked)}"
    prev_col.button(
        "", icon=":material/chevron_left:", key="match_prev", help="Previous match", use_container_width=True,
        disabled=prev_area is None, on_click=_select, args=(prev_area,),
    )
    mid.html(f'<div style="text-align:center;font-weight:600">{escape(label)}</div>')
    next_col.button(
        "", icon=":material/chevron_right:", key="match_next", help="Next match", use_container_width=True,
        disabled=next_area is None, on_click=_select, args=(next_area,),
    )


def _rent_block(r: AreaResult) -> None:
    rent = r.component("rent")
    if rent is None or rent.fact is None:
        st.markdown("**Rent estimate:** Not checked / no data")
        return
    basis = "HUD Fair Market Rent" if r.rent_basis == "HUD" else "Zillow Observed Rent Index"
    st.markdown(f"**Rent estimate: ${rent.raw:,.0f}/mo** · {basis}")
    st.caption(fact_caption(rent.fact))
    if r.rent_other is not None:
        st.caption(f"Zillow index: ${r.rent_other.value:,.0f} · {fact_caption(r.rent_other)}")
    elif r.rent_basis == "HUD":
        st.caption("Zillow index: Not checked / no data")


def _match_card(r: AreaResult) -> None:
    _rank_title(r.rank, r.area, size="1.3rem")
    st.progress(min(1.0, max(0.0, r.score)), text=f"**Match {r.score * 100:.0f}/100** · {ex.headline(r).lower()}")
    if r.status != ELIGIBLE:
        (st.warning if r.status == UNVERIFIED else st.error)(ex.status_note(r))

    st.markdown("**:green[:material/check_circle:] Why it matched**")
    bullets = ex.why_matched(r)
    st.markdown("\n".join(f"- {b}" for b in bullets) if bullets else "- No evaluated priority was met.")

    st.markdown("**:orange[:material/balance:] Main compromise**")
    st.markdown(f"- {ex.main_compromise(r)}")

    st.markdown("**:gray[:material/help:] Missing / not checked**")
    st.markdown("\n".join(f"- {m}" for m in r.missing) if r.missing else "- Nothing missing for your priorities")

    _rent_block(r)

    with st.expander("Every priority, with its source"):
        st.caption(f"ZIP {r.zip} · {r.status} · {ex.coverage(r).lower()}")
        for c in r.components:
            if c.met is None:
                st.markdown(f":gray[:material/help:] {c.label}: **Not checked / no data** · :gray[{c.importance}]")
                continue
            icon = ":green[:material/check_circle:]" if c.met else ":red[:material/cancel:]"
            st.markdown(f"{icon} {ex._sentence(c)} · :gray[{c.importance}]")
            st.caption(fact_caption(c.fact))


def _hot_news(r: AreaResult) -> None:
    st.markdown("**:material/local_fire_department: Hot News**")
    st.caption(
        f"Events and places in {r.area} that match your interests will show here in the next build. "
        "Nothing appears until it comes from a real source."
    )


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

_PAGE_CSS = """
<style>
/* Use the full window width, like a map app. */
.stMainBlockContainer { padding-left: 1.5rem; padding-right: 1.5rem; padding-bottom: 1rem; max-width: 100%; }
</style>
"""


def render(areas: list[AreaFacts], issues: list[str]) -> None:
    st.html(_PAGE_CSS)
    facts = {a.area: a for a in areas}
    available_interests = sorted({cat for a in areas for cat in a.places})

    for issue in issues:
        st.warning(f"Data issue: {issue}")

    left, center, right = st.columns([1.05, 2.5, 1.15], gap="medium")

    with left:
        with st.container(height=PANEL_HEIGHT, border=False):
            profile = profile_form(available_interests)

    results = score_areas(areas, profile)
    ranked = [r for r in results if r.rank is not None]

    # Re-rank: compare with the profile from the previous run.
    current = profile.model_dump()
    previous = st.session_state.get("last_profile")
    if previous is not None and previous != current:
        before_profile = Profile(**previous)
        before = score_areas(areas, before_profile)
        st.session_state["change_notes"] = ex.what_changed(diff_rankings(before, results, before_profile, profile))
        st.session_state["selected_area"] = None  # a new ranking starts back at match #1
    st.session_state["last_profile"] = current

    names = [r.area for r in results]
    if st.session_state.get("selected_area") not in names:
        st.session_state["selected_area"] = ranked[0].area if ranked else (names[0] if names else None)
    selected = next((r for r in results if r.area == st.session_state["selected_area"]), None)

    with center:
        notes = st.session_state.get("change_notes")
        if notes:
            st.info("**What changed**\n\n" + "\n".join(f"- {n}" for n in notes), icon=":material/swap_vert:")
        info = no_eligible_info(results)
        if info is not None:
            st.error("\n\n".join(ex.no_eligible_message(info)), icon=":material/block:")

        # Shrink the map a little when the note is showing, so the page still fits one screen.
        clicked = _map(results, facts, st.session_state["selected_area"], height=MAP_HEIGHT - 120 if notes else MAP_HEIGHT)
        # The map remembers its selection across reruns, so act only on a new click.
        if clicked != st.session_state.get("last_map_click"):
            st.session_state["last_map_click"] = clicked
            if clicked and clicked != st.session_state["selected_area"]:
                st.session_state["selected_area"] = clicked
                st.rerun()
        legend = "Numbered orange = top 3 · blue = other matches · grey = unverified or not eligible. Click an area to open it."
        if any(a.demo for a in areas):
            legend = f"{badge(LABEL_DEMO)} Placeholder numbers, not real. " + legend
        st.caption(legend)

    with right:
        with st.container(height=PANEL_HEIGHT, border=False):
            if selected is not None:
                with st.container(border=True):
                    _match_nav(selected, ranked)
                    _match_card(selected)
                with st.container(border=True):
                    _hot_news(selected)

    with st.expander("See all 8 areas in a table"):
        table = pd.DataFrame([
            {
                "Rank": f"#{r.rank}" if r.rank else "—",
                "Area": r.area,
                "Status": r.status,
                "Match": f"{r.score * 100:.0f}/100",
                "Priorities": f"meets {r.met} of {r.evaluated} evaluated ({r.total} total)",
                "Note": "; ".join(r.reasons),
            }
            for r in results
        ])
        st.dataframe(table, hide_index=True, use_container_width=True)
