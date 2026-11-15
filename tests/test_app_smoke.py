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


def test_priya_example_opens_results():
    at = open_priya()
    assert not at.error, [e.value for e in at.error]
    assert at.session_state["last_prompt"]
    assert at.text_area(key="explore_prompt").value == at.session_state["last_prompt"]


def test_free_text_does_not_crash():
    at = start()
    text = "Rutgers junior, no car, $1,600/month for a 1-bedroom, love jazz bars and a gym."
    at.text_area(key="landing_text").set_value(
        text
    ).run()
    click(at, "Find my neighborhood")
    assert at.session_state["last_prompt"] == text
    assert at.text_area(key="explore_prompt").value == text


def test_explore_description_updates_the_profile():
    at = open_priya()
    text = "I work in Newark, no car, $1,800/month and I need a gym."
    at.text_area(key="explore_prompt").set_value(text).run()
    click(at, "Update my matches")
    assert at.session_state["last_prompt"] == text
    assert at.session_state["profile"]["budget"] == 1800
    assert any(i["category"] == "gym" for i in at.session_state["profile"]["interests"])


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
