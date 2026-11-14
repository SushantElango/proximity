"""Loader tests: placeholder fallback, labels, the shared 2-bed estimate and curated events."""
from proximity.data import build_area_facts, build_events, load_all
from proximity.models import LABEL_DATASET, LABEL_DEMO, LABEL_ESTIMATE, LABEL_MANUAL

AREAS = "area,zip,lat,lon\nAlpha,08901,40.5,-74.4\nBeta,08904,40.6,-74.5\n"
HUD = "zip,fmr_0br,fmr_1br,fmr_2br,source,as_of\n08901,1700,1900,2381,HUD test,2026-05-21\n"
ZILLOW = "zip,zori,month,source\n08901,2819.91,2026-09,Zillow test\n"
COMMUTE = "zip,destination,mode,minutes,depart_time,checked_on,source\n08901,Newark Penn Station,transit,{m},22:30,2026-11-14,{src}\n"
EVENTS = (
    "zip_or_area,interest,title,date,venue,url,collected_on\n"
    "08901,jazz,Jazz Night,2026-12-04,Hall,https://example.org/a,2026-11-14\n"
    "Beta,fitness,Yoga,2026-11-17,Center,https://example.org/b,2026-11-14\n"
    "08901,community,Fair,2026-11-20,Park,https://example.org/c,2026-11-14\n"
    "08901,sports,No Link Game,2026-11-21,Arena,,2026-11-14\n"
    "99999,sports,Elsewhere,2026-11-21,Arena,https://example.org/d,2026-11-14\n"
)


def write(tmp_path, name, text, placeholder=False):
    folder = tmp_path / "placeholder" if placeholder else tmp_path
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def setup(tmp_path):
    write(tmp_path, "areas.csv", AREAS)
    write(tmp_path, "rent_hud.csv", HUD)
    write(tmp_path, "rent_zillow.csv", ZILLOW)
    write(tmp_path, "events_curated.csv", EVENTS)


def test_placeholder_commute_is_used_and_labeled_demo(tmp_path):
    setup(tmp_path)
    write(tmp_path, "commute.csv", COMMUTE.format(m=99, src="fake"), placeholder=True)
    bundle = load_all(tmp_path)
    fact = build_area_facts(bundle)[0].commute[("Newark Penn Station", "transit")]
    assert fact.value == 99
    assert fact.label == LABEL_DEMO  # forced, even though the file has no demo column
    assert "commute.csv" in bundle.placeholders
    assert "rent_hud.csv" not in bundle.placeholders


def test_real_commute_file_takes_over_automatically(tmp_path):
    setup(tmp_path)
    write(tmp_path, "commute.csv", COMMUTE.format(m=99, src="fake"), placeholder=True)
    write(tmp_path, "commute.csv", COMMUTE.format(m=42, src="Google Maps"))
    bundle = load_all(tmp_path)
    fact = build_area_facts(bundle)[0].commute[("Newark Penn Station", "transit")]
    assert fact.value == 42
    assert fact.label == LABEL_ESTIMATE
    assert "commute.csv" not in bundle.placeholders


def test_real_rent_is_labeled_dataset_and_zillow_is_whole_dollars(tmp_path):
    setup(tmp_path)
    alpha, beta = build_area_facts(load_all(tmp_path))
    assert alpha.rent_hud["1br"].value == 1900
    assert alpha.rent_hud["1br"].label == LABEL_DATASET
    assert alpha.rent_zillow.value == 2820
    assert beta.rent_hud == {} and beta.rent_zillow is None  # no row: no data, not a guess


def test_shared_two_bed_is_half_the_two_bed_rent_and_an_estimate(tmp_path):
    setup(tmp_path)
    shared = build_area_facts(load_all(tmp_path))[0].rent_hud["shared2br"]
    assert shared.value == round(2381 / 2)
    assert shared.label == LABEL_ESTIMATE
    assert shared.label_detail == "2-bed rent split in half"


def test_events_are_grouped_and_bad_rows_are_dropped(tmp_path):
    setup(tmp_path)
    events, issues = build_events(load_all(tmp_path))
    by_title = {e.title: e for e in events}
    assert set(by_title) == {"Jazz Night", "Yoga", "Fair"}
    assert by_title["Jazz Night"].group == "live_music"
    assert by_title["Yoga"].group == "gym" and by_title["Yoga"].zip == "08904"  # matched by area name
    assert by_title["Fair"].group == "local"
    assert all(e.label == LABEL_MANUAL and e.url.startswith("https://") for e in events)
    assert len(issues) == 2  # the row with no URL and the row for an unknown area
