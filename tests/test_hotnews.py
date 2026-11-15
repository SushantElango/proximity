"""Hot News tests: only real rows, matched to interests, every item traceable to a source."""
from proximity.data import build_places, load_all
from proximity.hotnews import curated_for, distance_text, hot_news, places_for, unnamed_line
from proximity.models import LABEL_DATASET, LABEL_MANUAL, Event, Place


def place(zip_code, category, name, osm_id, dist):
    return Place(zip=zip_code, category=category, name=name, osm_id=osm_id, distance_m=dist, source="OSM test")


def event(zip_code, group, title, date):
    return Event(zip=zip_code, group=group, title=title, date=date, venue="Hall", url=f"https://example.org/{title}", collected_on="2026-11-14")


PLACES = [
    place("08840", "sports", "", "way/1", 100),
    place("08840", "sports", "Far Field", "way/2", 900),
    place("08840", "sports", "Near Courts", "way/3", 200),
    place("08840", "sports", "", "way/4", 300),
    place("08840", "sports", "Near Courts", "way/3", 200),  # same OSM element twice
    place("08840", "gym", "Iron Gym", "node/5", 400),
    place("08901", "sports", "Other Town Pitch", "way/6", 50),
]
EVENTS = [
    event("08840", "live_music", "Jazz Fest", "2026-11-21"),
    event("08840", "live_music", "Old Gig", "2026-11-01"),
    event("08840", "local", "Parade", "2026-11-29"),
    event("08854", "sports", "Rutgers Game", "2026-11-16"),
    event("08840", "gym", "Undated Class", ""),
]
TODAY = "2026-11-14"


def test_places_are_named_first_nearest_first_and_unnamed_are_counted():
    named, unnamed = places_for(PLACES, "08840", "sports")
    assert [p.name for p in named] == ["Near Courts", "Far Field"]  # nearest first, no duplicate, no other area
    assert unnamed == 2
    assert unnamed_line("sports", unnamed) == "+ 2 unnamed pitches and courts"
    assert unnamed_line("sports", 0) == ""


def test_every_place_has_a_source_link_and_a_distance():
    named, _ = places_for(PLACES, "08840", "sports")
    assert all(p.url.startswith("https://www.openstreetmap.org/way/") for p in named)
    assert all(p.label == LABEL_DATASET for p in named)
    assert distance_text(named[0].distance_m) == "0.2 km from centre"
    assert place("08840", "gym", "Fake", "demo-7", 10).url == ""  # placeholder ids never become links


def test_curated_events_are_upcoming_only_and_soonest_first():
    assert [e.title for e in curated_for(EVENTS, "08840", "live_music", TODAY)] == ["Jazz Fest"]
    assert [e.title for e in curated_for(EVENTS, "08840", "gym", TODAY)] == ["Undated Class"]
    assert curated_for(EVENTS, "08840", "sports", TODAY) == []  # nothing is invented to fill the gap


def test_rutgers_games_show_under_sports_for_piscataway():
    sections = {s.key: s for s in hot_news("08854", ["sports"], EVENTS, PLACES, TODAY)}
    assert [e.title for e in sections["sports"].events] == ["Rutgers Game"]
    assert sections["sports"].events[0].label == LABEL_MANUAL


def test_sections_follow_the_users_interests_and_local_events_is_always_there():
    sections = hot_news("08840", ["live_music", "sports"], EVENTS, PLACES, TODAY)
    assert [s.key for s in sections] == ["live_music", "sports", "local"]
    by_key = {s.key: s for s in sections}
    assert [e.title for e in by_key["local"].events] == ["Parade"]
    assert by_key["local"].has_places is False
    assert by_key["sports"].unnamed == 2 and len(by_key["sports"].places) == 2
    # Gym was not selected, so its place and event are not shown anywhere.
    assert all(p.category != "gym" for s in sections for p in s.places)
    assert all(e.group != "gym" for s in sections for e in s.events)
    # An area with nothing still gets its sections, all empty.
    empty = hot_news("08873", [], EVENTS, PLACES, TODAY)
    assert [s.key for s in empty] == ["local"] and empty[0].empty


def test_real_places_file_loads_with_distances_inside_the_fetch_radius():
    places = build_places(load_all())
    assert len(places) > 100
    # A large park can touch the 1,500 m circle while its own centre lies a little outside it.
    assert all(p.distance_m is not None and p.distance_m <= 2500 for p in places)
    assert sum(p.distance_m <= 1500 for p in places) / len(places) > 0.95
    assert all(p.label == LABEL_DATASET and p.url for p in places)
