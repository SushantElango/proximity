"""Fetch interest places from OpenStreetMap (Overpass API) into data/places.csv.

What counts as "in an area": anything within RADIUS_M metres of the area's
centre point from data/areas.csv (the Census ZCTA internal point). That is a
circle, not the real ZIP boundary, and circles of neighbouring areas overlap, so
one place can count for two areas. The radius is written into every row.

Run:  python scripts/fetch_osm.py            # uses cached responses when present
      python scripts/fetch_osm.py --refresh  # ask Overpass again

Docs checked 2026-11-14:
- Endpoint and POST form field `data`: https://wiki.openstreetmap.org/wiki/Overpass_API
- Settings, around filter, nwr, regex, union, `out center`:
  https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL
- Usage policy (identify the app, no parallel requests, cache, 429/504, wait
  30 s before retrying): https://dev.overpass-api.de/overpass-doc/en/preface/commons.html
- Each tag below was checked on its wiki.openstreetmap.org/wiki/Tag:... page.
  None is deprecated. amenity=music_venue is "in use", not approved, so few
  places carry it; bars with music are usually amenity=bar + live_music=yes.

The response shape (node lat/lon, way/relation `center`) was not stated in the
pages read, so parse() checks it and raises a clear error if it differs.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"

ENDPOINT = "https://overpass-api.de/api/interpreter"
USER_AGENT = "proximity-neighborhood-match/0.1 (student hackathon project)"
RADIUS_M = 1500
TIMEOUT_S = 60

# interest category -> list of (key, [values]). From CLAUDE.md section 5.
CATEGORY_TAGS: dict[str, list[tuple[str, list[str]]]] = {
    "grocery": [("shop", ["supermarket", "convenience"])],
    "gym": [("leisure", ["fitness_centre"])],
    "park": [("leisure", ["park"])],
    "cafe": [("amenity", ["cafe"])],
    "library": [("amenity", ["library"])],
    "live_music": [("amenity", ["bar", "pub", "music_venue", "nightclub"])],
    "sports": [("leisure", ["sports_centre", "pitch"])],
    "restaurant": [("amenity", ["restaurant"])],
    "place_of_worship": [("amenity", ["place_of_worship"])],
}
# Extra tags kept for later use (e.g. sport=cricket, cuisine=indian).
DETAIL_KEYS = ["sport", "cuisine", "live_music", "religion"]

FIELDS = ["zip", "category", "name", "lat", "lon", "osm_id", "osm_tag", "detail", "radius_m", "fetched_on", "source"]


def build_query(lat: float, lon: float) -> str:
    """One query per area covering every category."""
    by_key: dict[str, set[str]] = {}
    for rules in CATEGORY_TAGS.values():
        for key, values in rules:
            by_key.setdefault(key, set()).update(values)
    parts = [
        f'  nwr(around:{RADIUS_M},{lat},{lon})["{key}"~"^({"|".join(sorted(values))})$"];'
        for key, values in sorted(by_key.items())
    ]
    return f"[out:json][timeout:{TIMEOUT_S}];\n(\n" + "\n".join(parts) + "\n);\nout center;"


def fetch(zip_code: str, lat: float, lon: float, refresh: bool) -> dict:
    """Return the Overpass JSON for one area, from cache when possible."""
    cache_file = CACHE / f"overpass_{zip_code}_{RADIUS_M}m.json"
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    query = build_query(lat, lon)
    for attempt in range(4):
        try:
            resp = requests.post(
                ENDPOINT, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S + 30
            )
        except requests.RequestException as exc:
            print(f"  {zip_code}: request failed ({exc}); waiting 30 s")
            time.sleep(30)
            continue
        if resp.status_code == 200:
            payload = resp.json()
            payload["_fetched_on"] = date.today().isoformat()
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        # 429 = rate limited, 504 = server too busy for this query. Policy: wait 30 s.
        print(f"  {zip_code}: HTTP {resp.status_code}; waiting 30 s (attempt {attempt + 1} of 4)")
        time.sleep(30)
    raise RuntimeError(f"Overpass did not answer for {zip_code}. Nothing was written for this area.")


def categories_for(tags: dict) -> list[tuple[str, str]]:
    """(category, 'key=value') for every category this element belongs to."""
    out = []
    for category, rules in CATEGORY_TAGS.items():
        for key, values in rules:
            if tags.get(key) in values:
                out.append((category, f"{key}={tags[key]}"))
    return out


def parse(payload: dict, zip_code: str) -> list[dict]:
    if "elements" not in payload:
        raise ValueError(f"Unexpected Overpass response for {zip_code}: no 'elements' key")
    rows = []
    fetched_on = payload.get("_fetched_on", "")
    for el in payload["elements"]:
        tags = el.get("tags", {})
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            raise ValueError(f"Unexpected Overpass element shape (no coordinates): {el.get('type')}/{el.get('id')}")
        detail = ";".join(f"{k}={tags[k]}" for k in DETAIL_KEYS if k in tags)
        for category, osm_tag in categories_for(tags):
            rows.append({
                "zip": zip_code,
                "category": category,
                "name": tags.get("name", ""),  # blank when OSM has no name; never made up
                "lat": lat,
                "lon": lon,
                "osm_id": f"{el['type']}/{el['id']}",
                "osm_tag": osm_tag,
                "detail": detail,
                "radius_m": RADIUS_M,
                "fetched_on": fetched_on,
                "source": "OpenStreetMap via Overpass API",
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore cached responses and query Overpass again")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)

    with (DATA / "areas.csv").open(newline="", encoding="utf-8") as f:
        areas = list(csv.DictReader(f))

    all_rows: list[dict] = []
    for i, a in enumerate(areas):
        zip_code = a["zip"].strip().zfill(5)
        cached = (CACHE / f"overpass_{zip_code}_{RADIUS_M}m.json").exists() and not args.refresh
        if i and not cached:
            time.sleep(3)  # one request at a time, with a pause, per the usage policy
        payload = fetch(zip_code, float(a["lat"]), float(a["lon"]), args.refresh)
        rows = parse(payload, zip_code)
        print(f"{a['area']:<18} {zip_code}  {len(rows):>4} places" + ("  (cached)" if cached else ""))
        all_rows.extend(rows)

    # Written only after every area succeeded, so the file is never half-filled.
    all_rows.sort(key=lambda r: (r["zip"], r["category"], r["name"], r["osm_id"]))
    with (DATA / "places.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote data/places.csv: {len(all_rows)} rows, radius {RADIUS_M} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
