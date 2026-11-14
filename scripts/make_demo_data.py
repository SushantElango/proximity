"""Write PLACEHOLDER commute and place data into data/placeholder/.

The app reads data/<file>.csv first. Only when that file is absent does it fall
back to data/placeholder/<file>.csv, and then every row shows as "Demo data".
So when the real data/commute.csv or data/places.csv arrives, the app switches
over by itself and these files are ignored.

The numbers here are round and made up on purpose. Some are left out so the
"no data" paths are exercised.

Run:  python scripts/make_demo_data.py
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "placeholder"
SRC = "PLACEHOLDER - not real data"

# area, zip, approx lat, approx lon (rough map positions only)
AREAS = [
    ("New Brunswick", "08901", 40.49, -74.45),
    ("Highland Park", "08904", 40.50, -74.43),
    ("Piscataway", "08854", 40.55, -74.46),
    ("Edison", "08817", 40.52, -74.39),
    ("Metuchen", "08840", 40.54, -74.36),
    ("North Brunswick", "08902", 40.44, -74.48),
    ("East Brunswick", "08816", 40.43, -74.41),
    ("Somerset", "08873", 40.50, -74.50),
]

# zip -> minutes by transit: (College Ave, Busch, Newark Penn). None = not checked.
TRANSIT = {
    "08901": (10, 20, 80),
    "08904": (15, 25, 70),
    "08854": (25, 10, 75),
    "08817": (25, 30, 60),
    "08840": (30, 35, 40),
    "08902": (30, 40, None),
    "08816": (40, 50, 80),
    "08873": (35, 30, 85),
}
DRIVE = {
    "08901": (5, 10, 40),
    "08904": (10, 10, 40),
    "08854": (15, 5, 45),
    "08817": (15, 15, 35),
    "08840": (20, 20, 30),
    "08902": (15, 20, 45),
    "08816": (20, 25, 45),
    "08873": (15, 15, 50),
}
DESTS = ["Rutgers College Ave", "Rutgers Busch", "Newark Penn Station"]
DEPART = {"Rutgers College Ave": "08:30", "Rutgers Busch": "08:30", "Newark Penn Station": "22:30"}

# category -> count per zip, in AREAS order
PLACES = {
    "live_music": (4, 2, 1, 3, 4, 1, 2, 1),
    "gym":        (3, 1, 3, 4, 2, 3, 3, 2),
    "sports":     (2, 1, 5, 4, 2, 3, 3, 4),
    "grocery":    (4, 3, 4, 5, 3, 4, 5, 3),
    "park":       (3, 4, 5, 4, 3, 3, 5, 4),
    "cafe":       (6, 4, 2, 3, 4, 2, 3, 1),
    "library":    (2, 1, 2, 2, 1, 1, 1, 1),
    "restaurant": (8, 5, 4, 7, 5, 4, 5, 3),
}


def _safe_to_write(path: Path) -> bool:
    if not path.exists():
        return True
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return all(str(r.get("demo", "0")).strip() == "1" for r in rows)


def _write(name: str, header: list[str], rows: list[list]) -> None:
    path = DATA / name
    if not _safe_to_write(path):
        print(f"SKIP {name}: contains real rows, not overwriting")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header + ["demo"])
        w.writerows([r + [1] for r in rows])
    print(f"wrote {name}: {len(rows)} placeholder rows")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)

    commute = []
    for mode, table in (("transit", TRANSIT), ("drive", DRIVE)):
        for z, mins in table.items():
            for dest, m in zip(DESTS, mins):
                if m is not None:
                    commute.append([z, dest, mode, m, DEPART[dest], "2026-11-14", SRC])
    _write(
        "commute.csv",
        ["zip", "destination", "mode", "minutes", "depart_time", "checked_on", "source"],
        commute,
    )

    places = []
    n = 0
    for cat, counts in PLACES.items():
        for (_, z, lat, lon), count in zip(AREAS, counts):
            for i in range(count):
                n += 1
                # fixed small offsets so the points spread out a little
                dlat = ((n * 7) % 11 - 5) * 0.002
                dlon = ((n * 5) % 13 - 6) * 0.002
                places.append([z, cat, f"Demo {cat} {i + 1}", round(lat + dlat, 4), round(lon + dlon, 4), f"demo-{n}"])
    _write("places.csv", ["zip", "category", "name", "lat", "lon", "osm_id"], places)


if __name__ == "__main__":
    main()
