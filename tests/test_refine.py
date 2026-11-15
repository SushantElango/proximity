"""Keyword parser and diff/apply tests. No network, no AI."""
import pytest

from proximity.models import Profile, demo_profile
from proximity.refine import (
    DEMOGRAPHIC_REPLY,
    KEYWORD,
    NOT_UNDERSTOOD,
    diff_profiles,
    keyword_refine,
    mentions_demographics,
    starter_profile,
)

NEWARK = "Newark Penn Station"
CAMPUS = "Rutgers College Ave"


def dest(profile, name):
    return next(d for d in profile.destinations if d.name == name)


def interest(profile, cat):
    return next((i for i in profile.interests if i.category == cat), None)


def test_x_matters_more_than_y_shifts_importance():
    base = demo_profile()  # Newark: Important, gyms: Nice
    p = keyword_refine("Getting to Newark matters more than gyms", base)
    assert p.source == KEYWORD and p.message == ""
    assert dest(p.profile, NEWARK).importance == "Must-have"
    assert interest(p.profile, "gym").importance == "Nice"  # already the lowest level
    assert [c.key for c in p.changes] == [f"dest:{NEWARK}:importance"]
    assert p.changes[0].old == "Important" and p.changes[0].new == "Must-have" and p.changes[0].reason


def test_x_matters_less_than_y_goes_the_other_way():
    p = keyword_refine("live music matters less than rent", demo_profile())
    assert interest(p.profile, "live_music").importance == "Nice"
    assert p.profile.rent_importance == "Must-have"


@pytest.mark.parametrize("text,amount", [
    ("budget $1,800", 1800), ("my budget is 1450", 1450), ("I can pay up to 1700", 1700),
    ("Actually I could stretch to $1,800 if it's walkable to music venues", 1800),
])
def test_budget_phrases(text, amount):
    p = keyword_refine(text, demo_profile())
    assert p.profile.budget == amount
    assert any(c.key == "budget" and c.new == f"${amount:,}" for c in p.changes)


def test_silly_budget_is_ignored():
    assert keyword_refine("budget $50", demo_profile()).profile.budget == 1600
    assert keyword_refine("budget $90000", demo_profile()).profile.budget == 1600


def test_car_phrases_switch_travel_mode():
    drive = keyword_refine("I have a car", demo_profile())
    assert all(d.mode == "drive" for d in drive.profile.destinations)
    assert len(drive.changes) == 2 and all(c.key.endswith(":mode") for c in drive.changes)
    back = keyword_refine("actually no car", drive.profile)
    assert all(d.mode == "transit" for d in back.profile.destinations)
    assert keyword_refine("no car", demo_profile()).changes == []  # already on transit


def test_commute_to_place_under_n_minutes():
    p = keyword_refine("commute to Newark under 45 min", demo_profile())
    assert dest(p.profile, NEWARK).max_minutes == 45
    added = keyword_refine("commute to Busch within 30 minutes", demo_profile())
    assert dest(added.profile, "Rutgers Busch").max_minutes == 30
    assert any(c.key == "dest:Rutgers Busch:present" for c in added.changes)


def test_like_love_and_dont_care():
    assert interest(keyword_refine("I like coffee", demo_profile()).profile, "cafe").importance == "Nice"
    assert interest(keyword_refine("I love parks", demo_profile()).profile, "park").importance == "Important"
    gone = keyword_refine("I don't care about gyms", demo_profile())
    assert interest(gone.profile, "gym") is None
    assert gone.changes[0].new == "removed"


def test_not_understood_changes_nothing():
    p = keyword_refine("the weather is nice today", demo_profile())
    assert p.changes == [] and p.message == NOT_UNDERSTOOD
    assert p.profile == demo_profile()


@pytest.mark.parametrize("text", [
    "I want a safe area", "somewhere with lots of Indians", "good for families", "a place with young professionals",
    "low crime please", "an Indian community", "where people like me live", "not too many students living there",
])
def test_demographic_requests_are_refused_and_change_nothing(text):
    assert mentions_demographics(text)
    p = keyword_refine(text, demo_profile())
    assert p.message == DEMOGRAPHIC_REPLY
    assert p.changes == [] and p.profile == demo_profile()


@pytest.mark.parametrize("text", [
    "I love Indian restaurants", "Getting to Newark matters more than gyms", "budget $1,800", "I like jazz bars",
])
def test_places_and_cuisine_are_not_mistaken_for_demographics(text):
    assert not mentions_demographics(text)


def test_demographic_part_is_refused_but_the_rest_still_applies():
    p = keyword_refine("budget $1,700 and a safe area", demo_profile())
    assert p.message == DEMOGRAPHIC_REPLY
    assert [c.key for c in p.changes] == ["budget"]


def test_diff_lists_every_changed_field_with_old_and_new():
    old = demo_profile()
    new = old.model_copy(deep=True)
    new.budget = 1800
    new.destinations[1].importance = "Must-have"
    new.interests = [i for i in new.interests if i.category != "gym"]
    changes = diff_profiles(old, new, {"budget": "because"})
    assert [(c.key, c.old, c.new) for c in changes] == [
        ("budget", "$1,600", "$1,800"),
        (f"dest:{NEWARK}:importance", "Important", "Must-have"),
        ("interest:gym:present", "Nice", "removed"),
    ]
    assert changes[0].reason == "because"
    assert diff_profiles(old, old) == []


def test_applying_a_proposal_gives_a_valid_profile_and_leaves_the_original_alone():
    base = demo_profile()
    p = keyword_refine("Getting to Newark matters more than gyms", base)
    assert base == demo_profile()  # the proposal never edits the current profile in place
    applied = Profile(**p.profile.model_dump())  # what Apply stores
    assert [c.key for c in diff_profiles(base, applied)] == [c.key for c in p.changes]
    assert keyword_refine("budget $1,800", applied).profile.destinations[1].importance == "Must-have"  # refines stack


def test_landing_text_builds_on_the_starter_profile():
    p = keyword_refine("No car, budget $1,400, commute to College Ave under 30 min, I love live music", starter_profile())
    assert p.profile.budget == 1400
    assert dest(p.profile, CAMPUS).max_minutes == 30 and dest(p.profile, CAMPUS).mode == "transit"
    assert interest(p.profile, "live_music").importance == "Important"


def test_first_description_adds_what_is_mentioned_but_an_edit_does_not():
    text = "classes on College Ave, I work in Newark, a gym would be nice but I don't care about parks"
    d = keyword_refine(text, starter_profile(), draft=True)
    assert [x.name for x in d.profile.destinations] == [CAMPUS, NEWARK]
    assert interest(d.profile, "gym").importance == "Nice" and interest(d.profile, "park") is None
    # The same words as an edit to an existing profile add nothing new.
    assert keyword_refine("I work in Newark", starter_profile()).changes == []
    drive = keyword_refine("I have a car and study at Busch", starter_profile(), draft=True)
    assert [(x.name, x.mode) for x in drive.profile.destinations] == [("Rutgers Busch", "drive")]


@pytest.mark.parametrize(
    "text", ["make transit more important", "Make transit more important.", "transit matters more", "make my commute more important"]
)
def test_make_transit_more_important_raises_the_transit_trips(text):
    base = demo_profile()  # College Ave: Must-have (already top), Newark: Important, both by transit
    p = keyword_refine(text, base)
    assert p.message == ""
    assert dest(p.profile, NEWARK).importance == "Must-have"
    assert dest(p.profile, CAMPUS).importance == "Must-have"
    assert [c.key for c in p.changes] == [f"dest:{NEWARK}:importance"]


def test_naming_a_place_moves_only_that_place():
    p = keyword_refine("make the commute to Newark less important", demo_profile())
    assert dest(p.profile, NEWARK).importance == "Nice"
    assert dest(p.profile, CAMPUS).importance == "Must-have"


def test_make_interest_or_rent_more_important():
    p = keyword_refine("make rent more important", demo_profile())
    assert p.profile.rent_importance == "Must-have"
    p = keyword_refine("prioritize gyms", demo_profile())
    assert interest(p.profile, "gym").importance == "Important"


def test_already_at_the_limit_says_so_instead_of_not_understood():
    p = keyword_refine("make gyms less important", demo_profile())  # gyms are already Nice
    assert not p.changes
    assert p.message != NOT_UNDERSTOOD and "lowest" in p.message and "Gyms" in p.message


@pytest.mark.parametrize("text", ["I want lower rent", "lower commute times please", "raise my budget to $1,800"])
def test_lower_or_raise_about_a_value_does_not_change_importance(text):
    base = demo_profile()
    p = keyword_refine(text, base)
    assert p.profile.rent_importance == base.rent_importance
    assert [d.importance for d in p.profile.destinations] == [d.importance for d in base.destinations]


def test_lower_the_importance_of_x_still_works():
    p = keyword_refine("lower the importance of getting to Newark", demo_profile())
    assert dest(p.profile, NEWARK).importance == "Nice"
