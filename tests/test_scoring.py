"""Scoring tests (CLAUDE.md section 6.4). Uses small hand-made facts, not the CSVs."""
import copy

from proximity.models import (
    LABEL_DEMO,
    AreaFacts,
    DestinationPref,
    Fact,
    InterestPref,
    Profile,
)
from proximity.scoring import (
    ELIGIBLE,
    INELIGIBLE,
    UNVERIFIED,
    diff_rankings,
    no_eligible_info,
    score_areas,
    top_matches,
)

NEWARK = "Newark Penn Station"
CAMPUS = "Rutgers College Ave"


def f(v):
    return Fact(value=v, label=LABEL_DEMO, source="test")


def area(name, rent, campus, newark, gyms):
    a = AreaFacts(area=name, zip=name[:5])
    if rent is not None:
        a.rent_hud["1br"] = f(rent)
    if campus is not None:
        a.commute[(CAMPUS, "transit")] = f(campus)
    if newark is not None:
        a.commute[(NEWARK, "transit")] = f(newark)
    if gyms is not None:
        a.places["gym"] = f(gyms)
    return a


def make_areas():
    return [
        area("Near Campus", 1500, 10, 70, 3),
        area("Near Train", 1500, 30, 40, 2),
        area("Middle", 1450, 20, 60, 1),
        area("Pricey", 1900, 15, 45, 4),
    ]


def make_profile(newark_importance="Nice"):
    return Profile(
        budget=1600,
        bedroom="1br",
        rent_importance="Nice",
        destinations=[
            DestinationPref(name=CAMPUS, mode="transit", max_minutes=35, importance="Important"),
            DestinationPref(name=NEWARK, mode="transit", max_minutes=90, importance=newark_importance),
        ],
        interests=[InterestPref(category="gym", importance="Nice")],
    )


def by_area(results):
    return {r.area: r for r in results}


def test_same_inputs_give_same_ranking():
    first = score_areas(make_areas(), make_profile())
    second = score_areas(make_areas(), make_profile())
    reordered = score_areas(list(reversed(make_areas())), make_profile())
    assert [r.area for r in first] == [r.area for r in second] == [r.area for r in reordered]
    assert [r.score for r in first] == [r.score for r in second]


def test_missing_data_never_raises_a_score():
    profile = make_profile()
    full = by_area(score_areas(make_areas(), profile))
    # Remove each fact from each area in turn. Its score must not go up.
    for idx in range(len(make_areas())):
        for strip in ("rent", "campus", "newark", "gym"):
            areas = make_areas()
            a = areas[idx]
            if strip == "rent":
                a.rent_hud.clear()
            elif strip == "campus":
                a.commute.pop((CAMPUS, "transit"))
            elif strip == "newark":
                a.commute.pop((NEWARK, "transit"))
            else:
                a.places.pop("gym")
            after = by_area(score_areas(areas, profile))[a.area]
            assert after.score <= full[a.area].score + 1e-12, (a.area, strip)
            assert after.evaluated == full[a.area].evaluated - 1
            assert len(after.missing) == 1


def test_missing_value_is_not_a_match_and_missing_must_have_is_unverified():
    areas = make_areas()
    areas[0].rent_hud.clear()  # Near Campus loses its rent
    results = by_area(score_areas(areas, make_profile()))
    r = results["Near Campus"]
    assert r.status == UNVERIFIED
    assert r.component("rent").met is None and r.component("rent").score is None
    # Unverified areas rank after every verified eligible area.
    ranked = score_areas(areas, make_profile())
    assert r.rank is None  # only eligible areas are ranked
    statuses = [x.status for x in ranked]
    assert statuses.index(UNVERIFIED) > max(i for i, s in enumerate(statuses) if s == ELIGIBLE)
    assert [x.rank for x in ranked if x.status == ELIGIBLE] == [1, 2]


def test_weight_change_moves_ranking_in_expected_direction():
    areas = make_areas()
    low = score_areas(areas, make_profile("Nice"))
    high = score_areas(areas, make_profile("Must-have"))
    # Near Train has the shortest Newark commute, so raising its weight must help it.
    assert by_area(high)["Near Train"].rank < by_area(low)["Near Train"].rank
    assert by_area(high)["Near Train"].score > by_area(low)["Near Train"].score
    # Near Campus has the longest, so it must not improve.
    assert by_area(high)["Near Campus"].rank >= by_area(low)["Near Campus"].rank

    diff = diff_rankings(low, high, make_profile("Nice"), make_profile("Must-have"))
    move = next(m for m in diff["moves"] if m["area"] == "Near Train")
    assert move["new_rank"] < move["old_rank"]
    assert move["driver"]["key"] == f"commute:{NEWARK}"
    assert move["driver"]["raw"] == 40
    assert move["driver"]["standing"]["position"] == 1
    assert diff["profile_changes"][0]["key"] == f"commute:{NEWARK}"


def test_over_budget_area_is_ineligible_and_unranked():
    r = by_area(score_areas(make_areas(), make_profile()))["Pricey"]
    assert r.status == INELIGIBLE
    assert r.rank is None
    assert "over your $1,600 budget" in r.reasons[0]
    assert "Pricey" not in [x.area for x in top_matches(score_areas(make_areas(), make_profile()))]


def test_no_eligible_area_message_appears_when_it_should():
    areas = make_areas()
    assert no_eligible_info(score_areas(areas, make_profile())) is None

    tight = make_profile()
    tight.budget = 1400  # every area is over budget
    info = no_eligible_info(score_areas(areas, tight))
    assert info is not None
    assert info["conflicts"][0]["key"] == "rent"
    assert info["conflicts"][0]["areas_ruled_out"] == 4
    # Cheapest way out: the budget that unlocks the cheapest area.
    assert info["suggestion"]["key"] == "rent"
    assert info["suggestion"]["needed"] == 1450
    assert info["suggestion"]["area"] == "Middle"


def test_missing_budget_keeps_rent_informational_and_out_of_scoring():
    areas = make_areas()
    areas[0].rent_hud.clear()
    profile = make_profile()
    profile.budget = None
    profile.rent_importance = "Must-have"

    results = score_areas(areas, profile)
    assert all(r.status == ELIGIBLE for r in results)
    for r in results:
        rent = r.component("rent")
        assert rent is not None and rent.score is None and rent.met is None and rent.weight == 0
        assert r.evaluated == 3 and r.total == 3
        assert "Budget: not given — rent shown for information only." in r.missing

    changed_rents = copy.deepcopy(areas)
    for item in changed_rents:
        item.rent_hud["1br"] = f(10000)
    assert [(r.area, r.score) for r in results] == [
        (r.area, r.score) for r in score_areas(changed_rents, profile)
    ]

    from proximity.explain import compare_rows, main_compromise, why_matched

    result = by_area(results)["Near Campus"]
    text = " ".join(why_matched(result) + [main_compromise(result)])
    assert "under budget" not in text and "your $" not in text
    rent_row = next(row for row in compare_rows(results) if row[""] == "Rent estimate ($/mo)")
    assert rent_row["Near Campus"] == "Not checked / no data"
    assert rent_row["Near Train"] == "$1,500"


def test_budget_none_is_safe_in_profile_diffs_and_re_rank_notes():
    from proximity.explain import what_changed
    from proximity.scoring import profile_changes

    areas = make_areas()
    without = make_profile()
    without.budget = None
    with_budget = without.model_copy(update={"budget": 1600})
    assert "you set your budget to $1,600" in profile_changes(without, with_budget)[0]["text"]
    assert "you removed your $1,600 budget" in profile_changes(with_budget, without)[0]["text"]
    diff = diff_rankings(
        score_areas(areas, without), score_areas(areas, with_budget), without, with_budget,
    )
    assert any("budget" in line for line in what_changed(diff))


def test_scoring_does_not_mutate_inputs():
    areas = make_areas()
    snapshot = copy.deepcopy(areas)
    score_areas(areas, make_profile())
    assert areas == snapshot


def test_what_changed_always_gives_a_note_for_any_priority_change():
    from proximity.explain import what_changed

    areas = make_areas()
    base = make_profile()
    variants = []
    for change in ("budget", "rent_importance", "campus_max", "gym_importance", "drop_gym", "newark_importance"):
        p = make_profile()
        if change == "budget":
            p.budget = 1650
        elif change == "rent_importance":
            p.rent_importance = "Important"
        elif change == "campus_max":
            p.destinations[0].max_minutes = 40
        elif change == "gym_importance":
            p.interests[0].importance = "Important"
        elif change == "drop_gym":
            p.interests = []
        else:
            p.destinations[1].importance = "Must-have"
        variants.append(p)
    for p in variants:
        notes = what_changed(diff_rankings(score_areas(areas, base), score_areas(areas, p), base, p))
        assert len(notes) >= 1 and all(n.strip() for n in notes)
        assert "you " in notes[0]  # names what the user changed


def test_what_changed_names_the_areas_it_has_no_room_to_explain():
    from proximity.explain import what_changed

    areas, base = make_areas(), make_profile()
    p = make_profile()
    p.destinations[1].importance = "Must-have"
    p.interests = []
    diff = diff_rankings(score_areas(areas, base), score_areas(areas, p), base, p)
    assert len(diff["moves"]) >= 2, "this profile change should move at least two areas"
    notes = what_changed(diff, limit=1)
    assert notes[-1].endswith("also changed position.") and "other area" not in notes[-1]
    explained = [m["area"] for m in diff["moves"] if f"**{m['area']}**" in notes[0]]
    for m in diff["moves"]:
        if m["area"] not in explained:
            assert m["area"] in notes[-1]


def test_hot_news_without_upcoming_events_shows_one_empty_state(monkeypatch):
    from types import SimpleNamespace

    from proximity.hotnews import Section
    from proximity.ui import explore

    captions = []
    fake = SimpleNamespace(markdown=lambda *a, **k: None, divider=lambda: None, caption=captions.append)
    monkeypatch.setattr(explore, "st", fake)
    explore._hot_news(SimpleNamespace(area="Test"), [Section(key="gym", label="Gyms")])
    assert captions == ["No upcoming events collected for this area yet."]


def test_compare_rows_have_the_same_rows_for_every_area_and_never_a_blank():
    from proximity.explain import compare_rows

    areas = make_areas() + [area("No Gym Data", 1400, 12, 50, None)]
    matches = top_matches(score_areas(areas, make_profile()), n=5)
    rows = compare_rows(matches)
    names = [r.area for r in matches]
    assert "No Gym Data" in names
    for row in rows:
        assert list(row) == [""] + names
        assert all(str(v).strip() for v in row.values())
    labels = [row[""] for row in rows]
    assert labels[0] == "Rank" and "Rent estimate ($/mo)" in labels and labels[-1] == "Missing / not checked"
    gym = next(row for row in rows if "gym" in row[""].lower())
    assert gym["No Gym Data"] == "Not checked / no data"
    assert compare_rows([]) == []


def test_why_matched_lists_a_count_for_every_selected_interest():
    from proximity.explain import why_matched
    from proximity.models import INTERESTS

    areas = make_areas()
    for a in areas:
        a.places["cafe"] = f(0)  # checked, none found
    profile = make_profile()
    profile.interests = [
        InterestPref(category="gym"), InterestPref(category="cafe"), InterestPref(category="library"),
    ]
    lines = why_matched(by_area(score_areas(areas, profile))["Near Campus"])
    assert any("**3** gyms" in x for x in lines)
    assert any("**No** cafes" in x for x in lines)  # a zero is shown, not hidden
    assert any(INTERESTS["library"] in x and "no data" in x for x in lines)  # never a made-up count


def test_flagged_commute_makes_a_must_have_unverified_with_the_flag_as_reason():
    areas = make_areas()
    a = areas[0]
    a.commute.pop((CAMPUS, "transit"))
    a.commute_flags[(CAMPUS, "transit")] = "Unusual: please verify (157 min reported by test)"
    profile = make_profile()
    profile.destinations[0].importance = "Must-have"
    r = by_area(score_areas(areas, profile))[a.area]
    assert r.status == UNVERIFIED
    assert "Unusual: please verify" in r.reasons[0]
    assert r.component(f"commute:{CAMPUS}").score is None
    assert any("Unusual: please verify" in m for m in r.missing)


def test_late_night_note_is_listed_for_every_area_when_newark_is_a_destination():
    from proximity.models import LATE_NIGHT_NOTE

    results = score_areas(make_areas(), make_profile())
    assert all(r.unchecked == [LATE_NIGHT_NOTE] for r in results)
    no_newark = make_profile()
    no_newark.destinations = no_newark.destinations[:1]
    assert all(r.unchecked == [] for r in score_areas(make_areas(), no_newark))


def test_ineligible_fit_is_a_dash_and_met_rent_compromise_is_explicit():
    from proximity.explain import fit_or_dash, main_compromise

    areas = [
        area("Cheap", 1200, 20, 40, 3),
        area("Dear", 1550, 20, 40, 3),
        area("Too Dear", 1900, 20, 40, 3),
    ]
    profile = make_profile()
    profile.rent_importance = "Must-have"
    results = score_areas(areas, profile)
    got = by_area(results)
    matches = [r for r in results if r.status == ELIGIBLE]
    assert fit_or_dash(got["Too Dear"]) == "Fit —"
    assert fit_or_dash(got["Cheap"]).startswith("Fit ") and "—" not in fit_or_dash(got["Cheap"])
    assert main_compromise(got["Dear"], matches) == (
        "Rent: $1,550/mo is within your $1,600 budget, but it's the most expensive of your top matches."
    )
