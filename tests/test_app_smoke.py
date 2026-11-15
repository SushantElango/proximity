"""Whole-app smoke tests with Streamlit's AppTest (no browser needed).

Run with:  DEMO_MODE=1 pytest tests/test_app_smoke.py -v
Each test runs app.py for real, clicks widgets, and fails on any exception.
"""
import os
from pathlib import Path

os.environ.setdefault("DEMO_MODE", "1")  # no live API calls during tests

from streamlit.testing.v1 import AppTest

from proximity.ui.explore import BEDROOM_LABELS

APP = str(Path(__file__).resolve().parent.parent / "app.py")
TIMEOUT = 30


def start() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception, at.exception
    return at


def click(at: AppTest, label: str) -> AppTest:
    """Click the first button with this label, rerun, and fail on any error."""
    matches = [b for b in at.button if b.label == label]
    assert matches, f"No button labelled {label!r}. Found: {[b.label for b in at.button]}"
    matches[0].click().run()
    assert not at.exception, f"Clicking {label!r} raised: {at.exception}"
    return at


def open_priya() -> AppTest:
    at = click(start(), "Try Priya's example")
    assert at.session_state["stage"] == "explore"
    return at


def test_landing_page_loads():
    at = start()
    assert [b.label for b in at.button][:2] == ["Find my neighborhood", "Try Priya's example"]
    assert any("Hi, good to have you here." in element.value for element in at.html)


def test_priya_example_opens_results():
    at = open_priya()
    assert not at.error, [e.value for e in at.error]
    assert at.session_state["last_prompt"]
    assert at.text_area(key="explore_prompt").value == at.session_state["last_prompt"]


def test_free_text_does_not_crash():
    at = start()
    text = "Rutgers junior, no car, $1,600/month for a 1-bedroom, love jazz bars and a gym."
    at.text_area(key="landing_text").set_value(text)
    click(at, "Find my neighborhood")
    assert at.session_state["last_prompt"] == text
    assert at.text_area(key="explore_prompt").value == text
    assert any("Results from your description — edit anything on the left." in el.value for el in at.info)
    assert not any("check and edit before results" in el.value for el in at.info)


def test_explore_description_updates_the_profile():
    at = open_priya()
    text = "I work in Newark, no car, $1,800/month and I need a gym."
    at.text_area(key="explore_prompt").set_value(text).run()
    click(at, "Update my matches")
    assert at.session_state["last_prompt"] == text
    assert at.session_state["profile"]["budget"] == 1800
    assert any(i["category"] == "gym" for i in at.session_state["profile"]["interests"])


def test_explore_description_asks_the_same_clarifying_question_as_landing():
    at = open_priya()
    before = at.session_state["profile"]
    text = "I want a gym nearby."
    at.text_area(key="explore_prompt").set_value(text).run()
    click(at, "Update my matches")

    assert at.session_state["clarify"]["text"] == text
    assert at.session_state["profile"] == before
    assert any("Where do you need to commute to, and how often?" in str(el.value) for el in at.markdown)

    at.text_input(key="clarify_answer").set_value("Classes on College Ave").run()
    click(at, "Continue")
    assert "clarify" not in at.session_state
    assert any(d["name"] == "Rutgers College Ave" for d in at.session_state["profile"]["destinations"])


def test_explore_shows_not_used_items_under_banner_and_in_match_card():
    at = start()
    text = "I work in Newark, I want grocery stores, and I need a 5 mile radius."
    at.text_area(key="landing_text").set_value(text)
    click(at, "Find my neighborhood")

    warnings = "\n".join(str(element.value) for element in at.warning)
    body = "\n".join(str(element.value) for element in at.markdown)
    assert "I couldn't use:" in warnings
    assert "Distance radius" in warnings
    assert "Distance radius" in body


def test_description_without_budget_leaves_budget_empty_and_rent_informational():
    at = start()
    text = "I have classes on College Ave and I love music."
    at.text_area(key="landing_text").set_value(text)
    click(at, "Find my neighborhood")

    assert at.session_state["profile"]["budget"] is None
    assert at.number_input(key="budget").value is None
    assert at.segmented_control(key="rent_importance").disabled
    body = "\n".join(str(element.value) for element in at.markdown)
    captions = "\n".join(element.value for element in at.caption)
    assert "Optional — add one to filter by rent." in captions
    assert "Budget: not given — rent shown for information only." in body
    assert "Rent estimate:" in body
    assert ":violet-badge[Estimate]" in body
    assert "under budget" not in body.lower()
    assert "your $" not in body.lower()


def test_match_card_groups_each_long_source_once_in_collapsed_expander():
    at = open_priya()
    body = "\n".join(str(element.value) for element in at.markdown)
    captions = "\n".join(element.value for element in at.caption)
    assert "Sources and dates" in [element.label for element in at.expander]
    assert ":violet-badge[Estimate]" in body
    assert ":blue-badge[Dataset]" in body
    for source in (
        "HUD FY2026 SAFMR",
        "OpenStreetMap via Overpass API",
        "Zillow ZORI (Smoothed): All Homes Plus Multifamily",
        "within 1,500 m of the area's centre point",
    ):
        assert body.count(source) == 1
        assert source not in captions
    assert body.count("Zillow index: $") == 1


def test_selected_area_shows_matched_and_unmatched_priorities_above_hot_news():
    at = open_priya()
    values = [element.value for element in at.markdown]
    assert any("Matched" in str(value) for value in values)
    assert any("Not matched / not checked" in str(value) for value in values)
    body = "\n".join(str(value) for value in values)
    assert body.index("Not matched / not checked") < body.index("Hot News")
    assert "Grocery & convenience stores" in at.multiselect(key="interests").options


def test_edison_hot_news_shows_events_only_when_none_are_available():
    at = open_priya()
    at.session_state["selected_area"] = "Edison"
    at.run()

    body = "\n".join(str(element.value) for element in at.markdown)
    captions = "\n".join(element.value for element in at.caption)
    assert "Hot News in Edison" in body
    assert "No upcoming events collected for this area yet." in captions
    assert "No curated events yet." not in captions
    assert "Show all" not in "\n".join(str(element.value) for element in at.button)
    assert not any(name in body for name in ("ShopRite", "Sunoco", "Wawa", "99 Ranch Market", "H Mart"))


def test_empty_text_does_not_crash():
    click(start(), "Find my neighborhood")


def test_every_explore_button_is_safe_to_click():
    """Click each enabled button on the results page once, from a fresh Priya session."""
    labels = [(b.label, b.key) for b in open_priya().button if not b.disabled]
    assert labels
    for label, key in labels:
        at = open_priya()
        button = next(b for b in at.button if (b.label, b.key) == (label, key))
        button.click().run()
        assert not at.exception, f"Button {label!r} (key={key}) raised: {at.exception}"


def test_changing_each_profile_widget_is_safe():
    at = open_priya()
    at.number_input(key="budget").set_value(1200).run()
    assert not at.exception, at.exception

    # Dropdowns show labels but store raw values, so set the raw values.
    for key in [box.key for box in at.selectbox]:
        values = list(BEDROOM_LABELS) if key == "bedroom" else ["transit", "drive"]
        for value in values:
            at.selectbox(key=key).set_value(value).run()
            assert not at.exception, f"{key} = {value!r} raised: {at.exception}"

    for key in [box.key for box in at.checkbox]:
        box = at.checkbox(key=key)
        box.set_value(not box.value).run()
        assert not at.exception, f"Toggling {key} raised: {at.exception}"

    # Changes are pending until applied: click whatever buttons the edits revealed.
    for label in [b.label for b in at.button if b.label.startswith("Apply") and not b.disabled]:
        click(at, label)


def test_refine_make_transit_more_important_offers_apply_and_explains():
    at = open_priya()
    at.text_input[0].set_value("make transit more important")
    click(at, "Suggest changes")
    click(at, "Apply")
    notes = " ".join(str(el.value) for el in [*at.info, *at.success, *at.warning, *at.markdown])
    assert "What changed" in notes and "Newark Penn Station" in notes
