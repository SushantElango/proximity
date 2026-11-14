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
    eligible_ranks = [x.rank for x in ranked if x.status == ELIGIBLE]
    assert r.rank > max(eligible_ranks)


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


def test_scoring_does_not_mutate_inputs():
    areas = make_areas()
    snapshot = copy.deepcopy(areas)
    score_areas(areas, make_profile())
    assert areas == snapshot
