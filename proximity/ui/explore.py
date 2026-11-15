"""Explore view, three columns: Prompt Zone | Map | selected match + Hot News."""
from __future__ import annotations

from datetime import date, datetime
from html import escape

import pandas as pd
import pydeck as pdk
import streamlit as st

from .. import explain as ex
from ..assistant import propose_refine
from ..llm import DEMO_RERANK
from ..refine import AI
from . import session
from ..hotnews import Section, distance_text, hot_news, place_detail, unnamed_line
from ..maplayout import fit_view, place_labels
from ..models import (
    BEDROOM_LABELS,
    DESTINATIONS,
    IMPORTANCE_LEVELS,
    INTERESTS,
    LABEL_DATASET,
    LABEL_DEMO,
    LABEL_ESTIMATE,
    LABEL_LIVE,
    LABEL_MANUAL,
    LOCAL_EVENTS_LABEL,
    AreaFacts,
    DestinationPref,
    Event,
    Fact,
    InterestPref,
    Place,
    Profile,
    demo_profile,
)
from ..scoring import (
    ELIGIBLE,
    INELIGIBLE,
    UNVERIFIED,
    AreaResult,
    diff_rankings,
    no_eligible_info,
    score_areas,
    top_matches,
)
from . import landing

ACCENT = "#D9480F"
_BADGE_COLOR = {
    LABEL_LIVE: "green", LABEL_DATASET: "blue", LABEL_ESTIMATE: "violet", LABEL_DEMO: "orange", LABEL_MANUAL: "gray",
}

_TOP_COLOR = [217, 72, 15]
_OTHER_COLOR = [70, 100, 150]
_UNVERIFIED_COLOR = [150, 150, 150]
_INELIGIBLE_COLOR = [200, 200, 200]

MAP_HEIGHT = 600
HOT_NEWS_TOP = 3  # items shown per list before "show all"
NOTE_SHRINK = 150  # the map gives up this much height while "What changed" is showing

PERSONA_SUMMARY = (
    "Rutgers junior sharing a 2-bed, classes on College Ave, works in Newark until 10:30pm Tue/Thu, "
    "no car, $1,600."
)
PANEL_HEIGHT = 640  # side panels scroll inside this height so the page fits one screen


def badge(label: str, detail: str = "") -> str:
    """Markdown badge for a data label. Every number in the UI gets one."""
    text = f"{label}: {detail}" if detail else label
    return f":{_BADGE_COLOR.get(label, 'gray')}-badge[{text}]"


def fact_caption(fact: Fact | None) -> str:
    if fact is None:
        return "Not checked / no data"
    parts = [badge(fact.label, fact.label_detail)]
    if fact.source:
        parts.append(fact.source)
    if fact.as_of:
        parts.append(f"{fact.as_of_word} {fact.as_of}")
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

def _importance(label: str, key: str, visible: bool = True) -> str:
    value = st.segmented_control(
        label, IMPORTANCE_LEVELS, required=True, key=key,
        label_visibility="visible" if visible else "collapsed",
    )
    return value or "Important"


def _apply_pending(data: dict) -> None:
    """Apply button: the edited profile becomes the one the ranking uses."""
    st.session_state["profile"] = data
    st.session_state["draft_label"] = ""
    st.session_state.pop("proposal", None)


def _undo_pending() -> None:
    session.load_widgets(session.applied_profile())


def _start_over() -> None:
    session.go(session.LANDING)


def _suggest(applied: Profile) -> None:
    """Refine box: sentence -> proposed changes, kept in session until applied or cancelled."""
    text = (st.session_state.get("refine_text") or "").strip()
    if not text:
        return
    proposal = propose_refine(text, applied)
    st.session_state["proposal"] = {
        "text": text,
        "base": applied.model_dump(),
        "profile": proposal.profile.model_dump(),
        "changes": [c.__dict__ for c in proposal.changes],
        "source": proposal.source,
        "message": proposal.message,
    }


def _apply_proposal() -> None:
    data = st.session_state.pop("proposal", None)
    if data:
        session.set_profile(Profile(**data["profile"]))
        st.session_state["refine_text"] = ""


def _cancel_proposal() -> None:
    st.session_state.pop("proposal", None)


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def _proposal_box(applied: Profile) -> bool:
    """The diff for the last refine request, with Apply / Cancel. Returns True when shown."""
    data = st.session_state.get("proposal")
    if not data:
        return False
    if data["base"] != applied.model_dump():  # the profile moved on; the diff no longer applies
        st.session_state.pop("proposal", None)
        return False
    with st.container(border=True):
        colour = "violet" if data["source"] == AI else "gray"
        st.markdown(f"**Proposed changes** for “{_cell(data['text'])}” :{colour}-badge[{data['source']}]")
        if data["message"]:
            st.warning(data["message"])
        if data["changes"]:
            rows = ["| Field | Old → New | Reason |", "|---|---|---|"]
            for c in data["changes"]:
                rows.append(f"| {_cell(c['field'])} | {_cell(c['old'])} → **{_cell(c['new'])}** | {_cell(c['reason']) or '—'} |")
            st.markdown("\n".join(rows))
            a, b, _ = st.columns([1, 1, 3])
            a.button("Apply", type="primary", use_container_width=True, key="proposal_apply", on_click=_apply_proposal)
            b.button("Cancel", use_container_width=True, key="proposal_cancel", on_click=_cancel_proposal)
        else:
            st.button("Dismiss", key="proposal_cancel", on_click=_cancel_proposal)
    return True


def profile_form(available_interests: list[str], applied: Profile) -> None:
    """The editable profile. Edits are pending until the user clicks Apply."""
    defaults = session.widget_values(applied)

    def ensure(key: str) -> None:
        # Streamlit forgets a widget's value while it is not on screen; restore it from the applied profile.
        if key not in st.session_state:
            st.session_state[key] = defaults[key]

    top, restart = st.columns([3, 1.4], vertical_alignment="bottom")
    top.markdown("### Your profile")
    restart.button("Start over", key="start_over", on_click=_start_over, use_container_width=True)
    head = st.container()  # filled in below, once the pending profile is known

    st.session_state.setdefault("explore_prompt", st.session_state.get("last_prompt", ""))
    with st.expander("Your description", expanded=True):
        st.text_area("Your description", key="explore_prompt", label_visibility="collapsed", height=110)
        st.button(
            "Update my matches", type="primary", use_container_width=True,
            key="update_matches", on_click=landing.update_from_explore,
        )
        st.caption("This replaces the profile edits below.")
        if st.session_state.get("landing_message"):
            st.warning(st.session_state["landing_message"])
        landing.render_clarification()

    with st.form("refine_form", border=True):
        st.text_input(
            "Refine in your own words", key="refine_text",
            placeholder=DEMO_RERANK,
        )
        if st.form_submit_button("Suggest changes", use_container_width=True):
            _suggest(applied)

    with st.expander("Rent", expanded=True, icon=":material/payments:"):
        ensure("bedroom")
        bedroom = st.selectbox("Housing", list(BEDROOM_LABELS), format_func=BEDROOM_LABELS.get, key="bedroom")
        ensure("budget")
        budget = st.number_input("Budget ($/mo)", min_value=500, max_value=6000, step=50, key="budget")
        ensure("rent_importance")
        rent_importance = _importance("Room under budget matters", "rent_importance")
        st.caption("Your budget is always a hard limit.")

    destinations: list[DestinationPref] = []
    with st.expander("Where you need to get to", expanded=False, icon=":material/commute:"):
        for name in DESTINATIONS:
            ensure(f"dest_on_{name}")
            if st.checkbox(name, key=f"dest_on_{name}"):
                a, b = st.columns(2)
                ensure(f"dest_mode_{name}")
                mode = a.selectbox("Travel by", ["transit", "drive"], key=f"dest_mode_{name}", format_func=str.capitalize)
                ensure(f"dest_max_{name}")
                max_minutes = b.number_input("Max minutes", min_value=5, max_value=180, step=5, key=f"dest_max_{name}")
                ensure(f"dest_imp_{name}")
                importance = _importance(f"Importance of {name}", f"dest_imp_{name}", visible=False)
                destinations.append(
                    DestinationPref(name=name, mode=mode, max_minutes=int(max_minutes), importance=importance)
                )

    interests: list[InterestPref] = []
    with st.expander("What you want nearby", expanded=False, icon=":material/local_activity:"):
        options = [k for k in INTERESTS if k != "place_of_worship"] + ["place_of_worship"]
        ensure("interests")
        chosen = st.multiselect(
            "Interests", options, format_func=INTERESTS.get, key="interests", label_visibility="collapsed",
        )
        for cat in chosen:
            ensure(f"int_imp_{cat}")
            importance = _importance(INTERESTS[cat], f"int_imp_{cat}")
            if cat not in available_interests:
                st.caption("Not checked / no data for this interest yet. It will not count as a match.")
            interests.append(InterestPref(category=cat, importance=importance))

    pending = Profile(
        budget=int(budget), bedroom=bedroom, rent_importance=rent_importance,
        destinations=destinations, interests=interests,
    )
    with head:
        label = st.session_state.get("draft_label")
        if label:
            st.info(label, icon=":material/edit_note:")
        elif applied == demo_profile():
            st.markdown(f"**Priya:** {PERSONA_SUMMARY}")
        if pending != applied:
            st.caption("You have edits that are not applied yet.")
            a, b = st.columns(2)
            a.button(
                "Apply", type="primary", use_container_width=True, key="pending_apply",
                on_click=_apply_pending, args=(pending.model_dump(),),
            )
            b.button("Undo edits", use_container_width=True, key="pending_undo", on_click=_undo_pending)


# --------------------------------------------------------------------------
# Map
# --------------------------------------------------------------------------

def _pin_tip(r: AreaResult) -> tuple[str, str]:
    """Tooltip lines: rank, or the reason the pin is grey."""
    if r.status == ELIGIBLE:
        return f"#{r.rank}", ex.headline(r)
    if r.status == UNVERIFIED:
        return "Unverified", "; ".join(r.reasons)
    return "Not eligible", "; ".join(r.reasons)


def _map(results: list[AreaResult], facts: dict[str, AreaFacts], selected: str | None, height: int = MAP_HEIGHT) -> str | None:
    """Draw all areas. Returns the area the user clicked, if any."""
    top = {r.area for r in top_matches(results)}
    located = [r for r in results if facts[r.area].lat is not None and facts[r.area].lon is not None]
    if not located:
        st.info("No area coordinates available, so the map cannot be drawn.")
        return None

    # Fit to a deliberately small map so every pin is in view on a laptop, and
    # place the name labels where no pin or other label is in the way.
    lat0, lon0, zoom = fit_view([(facts[r.area].lat, facts[r.area].lon) for r in located], 600, MAP_HEIGHT - NOTE_SHRINK)
    offsets = place_labels([(r.area, facts[r.area].lat, facts[r.area].lon) for r in located], zoom)

    rows = []
    for r in located:
        f = facts[r.area]
        if r.area in top:
            color, radius = _TOP_COLOR, 16
        elif r.status == ELIGIBLE:
            color, radius = _OTHER_COLOR, 13
        elif r.status == UNVERIFIED:
            color, radius = _UNVERIFIED_COLOR, 13
        else:
            color, radius = _INELIGIBLE_COLOR, 8
        rows.append({
            "area": r.area, "lat": f.lat, "lon": f.lon, "color": color, "radius": radius,
            # Same numbers as the < > arrows. Unverified areas are not ranked, so they show "?".
            "number": str(r.rank) if r.rank is not None else ("?" if r.status == UNVERIFIED else ""),
            "number_size": 18 if r.area in top else 14,
            "tip1": _pin_tip(r)[0],
            "tip2": _pin_tip(r)[1],
            "label": r.area if r.area in offsets else "",
            "offset": list(offsets.get(r.area, (0, 0))),
            "line": [20, 20, 20] if r.area == selected else [255, 255, 255],
            "line_width": 4 if r.area == selected else 2,
        })

    df = pd.DataFrame(rows)
    layers = [
        pdk.Layer(
            "TextLayer", id="names", data=df[df["label"] != ""], get_position="[lon, lat]", get_text="label",
            get_size=13, get_color=[30, 30, 30], get_pixel_offset="offset",
            background=True, get_background_color=[255, 255, 255, 235], background_padding=[5, 3],
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
            get_text="number", get_size="number_size", get_color=[255, 255, 255], font_weight=700,
            get_text_anchor="'middle'", get_alignment_baseline="'center'", parameters={"depthTest": False},
        ),
    ]
    deck = pdk.Deck(
        # A basemap with no place names, so the only labels are ours.
        map_style=pdk.map_styles.CARTO_LIGHT_NO_LABELS,
        initial_view_state=pdk.ViewState(latitude=lat0, longitude=lon0, zoom=zoom),
        layers=layers,
        tooltip={
            "html": "<b>{area}</b><br/>{tip1}<br/>{tip2}",
            "style": {"backgroundColor": "#1F2328", "color": "#fff", "fontSize": "13px", "borderRadius": "6px", "maxWidth": "280px"},
        },
    )
    event = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object", key="area_map", height=height)
    try:
        picked = event.selection.objects.get("areas", [])
        return picked[0]["area"] if picked else None
    except (AttributeError, KeyError, IndexError, TypeError):
        return None  # selection not available: the arrows still work


# --------------------------------------------------------------------------
# Right: the selected match, with < > to step through matches in order
# --------------------------------------------------------------------------

def _select(area: str) -> None:
    st.session_state["selected_area"] = area


def _match_nav(selected: AreaResult, matches: list[AreaResult]) -> None:
    """< Match 2 of 3 > : steps through the eligible matches in rank order."""
    idx = next((i for i, r in enumerate(matches) if r.area == selected.area), None)
    prev_col, mid, next_col = st.columns([1, 3, 1], vertical_alignment="center")
    if idx is None:
        prev_area = None
        next_area = matches[0].area if matches else None
        label = "Unverified area" if selected.status == UNVERIFIED else "Not a match"
    else:
        prev_area = matches[idx - 1].area if idx > 0 else None
        next_area = matches[idx + 1].area if idx + 1 < len(matches) else None
        label = f"Match {idx + 1} of {len(matches)}"
    prev_col.button(
        "", icon=":material/chevron_left:", key="match_prev", help="Previous match", use_container_width=True,
        disabled=prev_area is None, on_click=_select, args=(prev_area,),
    )
    mid.html(f'<div style="text-align:center;font-weight:600">{escape(label)}</div>')
    next_col.button(
        "", icon=":material/chevron_right:", key="match_next", help="Next match" if idx is not None else "Go to match 1",
        use_container_width=True, disabled=next_area is None, on_click=_select, args=(next_area,),
    )


def _unverified_list(unverified: list[AreaResult], selected: AreaResult | None) -> None:
    """Areas a must-have could not be checked for. Listed apart from the matches, unranked."""
    st.markdown("**:gray[:material/help:] Unverified**")
    st.caption("A must-have has no usable data for these, so they are not ranked.")
    for r in unverified:
        current = selected is not None and r.area == selected.area
        st.button(
            r.area, key=f"unverified_{r.area}", use_container_width=True, disabled=current,
            on_click=_select, args=(r.area,),
        )
        st.caption("; ".join(r.reasons))


def _rent_block(r: AreaResult) -> None:
    rent = r.component("rent")
    if rent is None or rent.fact is None:
        st.markdown("**Rent estimate:** Not checked / no data")
        return
    if r.rent_basis != "HUD":
        basis = "Zillow Observed Rent Index"
    elif rent.fact.label_detail:
        basis = "half of the HUD 2-bed Fair Market Rent"
    else:
        basis = "HUD Fair Market Rent"
    st.markdown(f"**Rent estimate: ${rent.raw:,.0f}/mo** · {basis}")
    st.caption(fact_caption(rent.fact))
    if r.rent_other is not None:
        st.caption(f"Zillow index: ${r.rent_other.value:,.0f} · {fact_caption(r.rent_other)}")
    elif r.rent_basis == "HUD":
        st.caption("Zillow index: Not checked / no data")


def _match_card(r: AreaResult, n_areas: int, matches: list[AreaResult]) -> None:
    _rank_title(r.rank, r.area, size="1.3rem")
    # No score is shown: areas are presented as a ranking only.
    st.markdown(f"**{ex.headline(r)}**")
    if r.status != ELIGIBLE:
        (st.warning if r.status == UNVERIFIED else st.error)(ex.status_note(r))

    st.markdown("**:green[:material/check_circle:] Why it matched**")
    bullets = ex.why_matched(r)
    st.markdown("\n".join(f"- {b}" for b in bullets) if bullets else "- No evaluated priority was met.")
    counts = [c.fact for c in r.components if c.kind == "interest" and c.fact is not None]
    if counts:
        st.caption("Place counts: " + fact_caption(counts[0]))

    st.markdown("**:orange[:material/balance:] Main compromise**")
    st.markdown(f"- {ex.main_compromise(r, matches)}")

    st.markdown("**:gray[:material/help:] Missing / not checked**")
    not_checked = r.missing + r.unchecked
    st.markdown("\n".join(f"- {m}" for m in not_checked) if not_checked else "- Nothing missing for your priorities")

    _rent_block(r)

    with st.expander("Every priority, with its source"):
        st.caption(f"ZIP {r.zip} · {r.status} · {ex.coverage(r).lower()}")
        for c in r.components:
            if c.met is None:
                st.markdown(f":gray[:material/help:] {c.label}: **{c.flag or 'Not checked / no data'}** · :gray[{c.importance}]")
                continue
            icon = ":green[:material/check_circle:]" if c.met else ":red[:material/cancel:]"
            st.markdown(f"{icon} {ex._sentence(c)} · :gray[{c.importance}]")
            st.caption(fact_caption(c.fact))


def _priority_breakdown(r: AreaResult) -> None:
    """Show every selected priority as met, not met, or not checked."""
    st.markdown("**Matched**")
    matched = [c for c in r.components if c.met is True]
    if not matched:
        st.caption("No priorities matched.")
    for c in matched:
        st.markdown(f"- {ex._sentence(c)} · :gray[{c.importance}]")
        st.caption(fact_caption(c.fact))

    st.markdown("**Not matched / not checked**")
    unmatched = [c for c in r.components if c.met is not True]
    if not unmatched:
        st.caption("No unmatched priorities.")
    for c in unmatched:
        if c.met is False:
            st.markdown(f"- {ex._sentence(c)} · :gray[{c.importance}]")
        else:
            detail = c.flag or "Not checked / no data"
            st.markdown(f"- {c.label}: {detail} · :gray[{c.importance}]")
        st.caption(fact_caption(c.fact))


def _event_date(iso: str) -> str:
    if not iso:
        return "Date not given"
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d:%a} {d:%b} {d.day}"


def _md(text: str) -> str:
    """Keep a title from breaking a markdown link."""
    return text.replace("[", "(").replace("]", ")")


def _event_line(e: Event) -> str:
    venue = f" · {e.venue}" if e.venue else ""
    return f"[{_md(e.title)}]({e.url})  \n:gray[{_event_date(e.date)}{venue}]"


def _place_line(p: Place) -> str:
    name = f"[{_md(p.name)}]({p.url})" if p.url else _md(p.name)
    detail = place_detail(p)
    extra = f"{detail} · " if detail else ""
    return f"{name}  \n:gray[{extra}{distance_text(p.distance_m)}]"


def _show(lines: list[str], more_label: str) -> None:
    """First HOT_NEWS_TOP lines, the rest behind an expander."""
    for line in lines[:HOT_NEWS_TOP]:
        st.markdown(line)
    if len(lines) > HOT_NEWS_TOP:
        with st.expander(more_label):
            for line in lines[HOT_NEWS_TOP:]:
                st.markdown(line)


EMPTY_SECTION_TEXT = "None found within 1.5 km."


def _hot_news(r: AreaResult, sections: list[Section]) -> None:
    """Events and places for this area, per interest. Every item links to its source."""
    st.markdown(f"**:material/local_fire_department: Hot News in {r.area}**")
    for i, s in enumerate(sections):
        if i:
            st.divider()
        st.markdown(f"**{s.label}**")

        if s.empty and s.has_places:
            st.caption(EMPTY_SECTION_TEXT)
            continue

        if s.events:
            _show([_event_line(e) for e in s.events], f"Show all {len(s.events)} events")
            collected = sorted({e.collected_on for e in s.events if e.collected_on})
            st.caption(
                f"{badge(s.events[0].label)} events, each linked to its source page"
                + (f" · collected {collected[-1]}" if collected else "")
            )
        else:
            st.caption("No curated events yet.")

        if not s.has_places:
            continue
        if s.places:
            _show([_place_line(p) for p in s.places], f"Show all {len(s.places)} named places")
        line = unnamed_line(s.key, s.unnamed)
        if line:
            st.markdown(f":gray[{line}]")
        if s.places or s.unnamed:
            first = s.places[0] if s.places else None
            st.caption(
                f"{badge(first.label if first else LABEL_DATASET)} places · "
                + (first.source if first else "OpenStreetMap via Overpass API")
                + (f" · as of {first.as_of}" if first and first.as_of else "")
                + " · within 1.5 km of the area centre, each linked to its map page"
            )
        else:
            st.caption("No places found within 1.5 km.")


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

_PAGE_CSS = """
<style>
/* Use the full window width, like a map app. */
.stMainBlockContainer { padding-left: 1.5rem; padding-right: 1.5rem; padding-bottom: 1rem; max-width: 100%; }
</style>
"""


def render(
    areas: list[AreaFacts], events: list[Event], places: list[Place], issues: list[str], placeholders: list[str],
) -> None:
    st.html(_PAGE_CSS)
    facts = {a.area: a for a in areas}
    available_interests = sorted({cat for a in areas for cat in a.places})

    for issue in issues:
        st.warning(f"Data issue: {issue}")

    left, center, right = st.columns([1.05, 2.5, 1.15], gap="medium")

    profile = session.applied_profile()
    if profile is None:  # reached the results without choosing a profile: start as Priya
        session.set_profile(demo_profile())
        profile = demo_profile()

    with left:
        with st.container(height=PANEL_HEIGHT, border=False):
            profile_form(available_interests, profile)

    results = score_areas(areas, profile)
    matches = [r for r in results if r.status == ELIGIBLE]
    unverified = [r for r in results if r.status == UNVERIFIED]

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
        first = matches or unverified
        st.session_state["selected_area"] = first[0].area if first else (names[0] if names else None)
    selected = next((r for r in results if r.area == st.session_state["selected_area"]), None)

    with center:
        proposing = _proposal_box(profile)
        notes = st.session_state.get("change_notes")
        if notes:
            st.info("**What changed**\n\n" + "\n".join(f"- {n}" for n in notes), icon=":material/swap_vert:")
        info = no_eligible_info(results)
        if info is not None:
            st.error("\n\n".join(ex.no_eligible_message(info)), icon=":material/block:")

        # Shrink the map a little when the note is showing, so the page still fits one screen.
        clicked = _map(results, facts, st.session_state["selected_area"], height=MAP_HEIGHT - NOTE_SHRINK if (notes or proposing) else MAP_HEIGHT)
        # The map remembers its selection across reruns, so act only on a new click.
        if clicked != st.session_state.get("last_map_click"):
            st.session_state["last_map_click"] = clicked
            if clicked and clicked != st.session_state["selected_area"]:
                st.session_state["selected_area"] = clicked
                st.rerun()
        legend = "Numbers match the arrows on the right · orange = top 3 · blue = other matches · grey ? = unverified · plain grey = not eligible. Hover a pin for why; click to open it."
        if placeholders:
            legend = f"{badge(LABEL_DEMO)} Placeholders, not real: {', '.join(placeholders)}. " + legend
        st.caption(legend)

    with right:
        with st.container(height=PANEL_HEIGHT, border=False):
            if selected is not None:
                with st.container(border=True):
                    _match_nav(selected, matches)
                    _match_card(selected, len(results), matches)
                if unverified:
                    with st.container(border=True):
                        _unverified_list(unverified, selected)
                with st.container(border=True):
                    _priority_breakdown(selected)
                with st.container(border=True):
                    sections = hot_news(
                        selected.zip, [i.category for i in profile.interests], events, places, date.today().isoformat(),
                    )
                    _hot_news(selected, sections)

    compared = sorted(top_matches(results), key=lambda r: r.rank or 0)
    if len(compared) > 1:
        with st.expander(f"Compare your top {len(compared)} matches side by side", icon=":material/compare_arrows:"):
            st.dataframe(pd.DataFrame(ex.compare_rows(compared)), hide_index=True, use_container_width=True)
            st.caption("Same priorities and units in every column. Sources and dates are on each match's card.")

    with st.expander("See all 8 areas in a table"):
        table = pd.DataFrame([
            {
                "Rank": f"#{r.rank}" if r.rank else "—",

                "Area": r.area,
                "Status": r.status,
                "Priorities": f"meets {r.met} of {r.evaluated} evaluated ({r.total} total)",
                "Note": "; ".join(r.reasons),
            }
            for r in results
        ])
        st.dataframe(table, hide_index=True, use_container_width=True)
