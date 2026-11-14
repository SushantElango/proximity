"""No pin may cover a label, and all areas must be in view."""
from proximity.data import build_area_facts, load_all
from proximity.maplayout import fit_view, overlaps, place_labels, project


def real_pins():
    return [(a.area, a.lat, a.lon) for a in build_area_facts(load_all())]


def test_no_pin_covers_a_label_for_the_real_areas():
    pins = real_pins()
    assert len(pins) == 8
    for width, height in [(600, 440), (700, 600), (900, 600)]:
        _, _, zoom = fit_view([(lat, lon) for _, lat, lon in pins], width, height)
        offsets = place_labels(pins, zoom)
        assert overlaps(pins, zoom, offsets) == []
        assert len(offsets) >= 6  # nearly every area keeps a visible name


def test_crowded_pins_drop_labels_rather_than_overlap():
    pins = [(f"Area number {i}", 40.5 + i * 0.0002, -74.4) for i in range(14)]
    offsets = place_labels(pins, 11.0)
    assert overlaps(pins, 11.0, offsets) == []
    assert len(offsets) < len(pins)


def test_fit_view_keeps_every_area_inside_the_map():
    pins = real_pins()
    width, height = 600, 440
    lat, lon, zoom = fit_view([(la, lo) for _, la, lo in pins], width, height)
    cx, cy = project(lat, lon, zoom)
    for _, la, lo in pins:
        x, y = project(la, lo, zoom)
        assert abs(x - cx) < width / 2 - 30 and abs(y - cy) < height / 2 - 30
