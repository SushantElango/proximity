"""Map geometry: fit the view to the areas and place name labels so nothing overlaps.

Pure maths, no Streamlit. Labels are positioned in screen pixels at the map's
starting zoom. A label is only drawn where its box touches no pin and no other
label; if no such spot exists the label is dropped and the hover tooltip carries
the name instead. (Zooming far out by hand can still bring things together.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TILE = 512  # deck.gl world size at zoom 0
PIN_RADIUS = 16  # the largest pin we draw, in pixels
CHAR_WIDTH = 8.0  # monospace label font at 13px, rounded up
LABEL_HEIGHT = 20.0
LABEL_PAD_X = 6.0
GAP = 4.0  # clear space kept between a label and anything else


def project(lat: float, lon: float, zoom: float) -> tuple[float, float]:
    """Web Mercator: latitude/longitude to world pixels at this zoom."""
    scale = TILE * 2 ** zoom
    x = (lon + 180.0) / 360.0 * scale
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * scale
    return x, y


def fit_view(points: list[tuple[float, float]], width: int, height: int, padding: int = 70) -> tuple[float, float, float]:
    """Centre and zoom that show every (lat, lon) inside a width x height map."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    x0, y0 = project(max(lats), min(lons), 0)
    x1, y1 = project(min(lats), max(lons), 0)
    span_x, span_y = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    zoom = min(math.log2((width - 2 * padding) / span_x), math.log2((height - 2 * padding) / span_y))
    zoom = math.floor(min(zoom, 13.0) * 10) / 10  # round down so everything stays in view
    return (max(lats) + min(lats)) / 2, (max(lons) + min(lons)) / 2, zoom


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    w: float
    h: float

    def hits_box(self, o: "Box") -> bool:
        return abs(self.cx - o.cx) * 2 < self.w + o.w + 2 * GAP and abs(self.cy - o.cy) * 2 < self.h + o.h + 2 * GAP

    def hits_circle(self, x: float, y: float, r: float) -> bool:
        dx = max(abs(x - self.cx) - self.w / 2, 0.0)
        dy = max(abs(y - self.cy) - self.h / 2, 0.0)
        return dx * dx + dy * dy < (r + GAP) ** 2


def label_width(text: str) -> float:
    return len(text) * CHAR_WIDTH + 2 * LABEL_PAD_X


def _candidates(w: float) -> list[tuple[float, float]]:
    """Offsets of the label centre from its pin, best-looking first."""
    v = PIN_RADIUS + GAP + LABEL_HEIGHT / 2 + 2
    h = PIN_RADIUS + GAP + w / 2 + 2
    return [
        (0, v), (0, -v), (h, 0), (-h, 0),
        (h, v), (-h, v), (h, -v), (-h, -v),
        (0, 2 * v), (0, -2 * v),
    ]


def place_labels(pins: list[tuple[str, float, float]], zoom: float) -> dict[str, tuple[float, float]]:
    """name -> (dx, dy) pixel offset for its label. Names with no free spot are left out.

    `pins` is a list of (name, lat, lon). Pins are placed in the order given, so
    pass the most important areas first.
    """
    xy = {name: project(lat, lon, zoom) for name, lat, lon in pins}
    placed: dict[str, Box] = {}
    offsets: dict[str, tuple[float, float]] = {}
    for name, _, _ in pins:
        x, y = xy[name]
        w = label_width(name)
        for dx, dy in _candidates(w):
            box = Box(x + dx, y + dy, w, LABEL_HEIGHT)
            if any(box.hits_circle(px, py, PIN_RADIUS) for px, py in xy.values()):
                continue
            if any(box.hits_box(other) for other in placed.values()):
                continue
            placed[name] = box
            offsets[name] = (dx, dy)
            break
    return offsets


def overlaps(pins: list[tuple[str, float, float]], zoom: float, offsets: dict[str, tuple[float, float]]) -> list[str]:
    """Every pin-on-label or label-on-label overlap, for tests. Empty means clean."""
    xy = {name: project(lat, lon, zoom) for name, lat, lon in pins}
    boxes = {n: Box(xy[n][0] + dx, xy[n][1] + dy, label_width(n), LABEL_HEIGHT) for n, (dx, dy) in offsets.items()}
    found = []
    for n, box in boxes.items():
        for other, (px, py) in xy.items():
            if box.hits_circle(px, py, PIN_RADIUS - GAP):  # true overlap, without the safety gap
                found.append(f"pin {other} covers label {n}")
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if abs(boxes[a].cx - boxes[b].cx) * 2 < boxes[a].w + boxes[b].w and abs(boxes[a].cy - boxes[b].cy) * 2 < boxes[a].h + boxes[b].h:
                found.append(f"labels {a} and {b} overlap")
    return found
