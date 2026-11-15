"""Fetch upcoming events per area from SerpApi's Google Events API into data/cache/.

Why a cache file and not a live call inside the app: the demo must run offline
(CLAUDE.md rule 6) and the free plan allows only 250 searches a month. Run this
once on a laptop that has the key, commit the cache files, and the app reads them.

Run:  python scripts/fetch_serpapi_events.py --only "Highland Park,Metuchen"   # test two towns
      python scripts/fetch_serpapi_events.py                                    # all areas, 3 searches each
      python scripts/fetch_serpapi_events.py --refresh                          # ignore cached files

The key comes from the SERPAPI_API_KEY environment variable or from .env (never
committed). It is removed from everything this script prints or saves.

Docs checked 2026-11-14 (https://serpapi.com/google-events-api):
- engine=google_events, q, api_key. Put the place inside q, e.g. "Events in Austin, TX".
  `location` is optional and is not used here.
- Each item in events_results has title, date (start_date, when), venue, address,
  ticket_info (source, link, link_type), event_location_map and price. There is no
  description field.
- A repeated search with the same query and parameters within one hour is cached
  by SerpApi and does not count toward the monthly limit.

Not stated on the page read, so not assumed: the endpoint path (taken from
SerpApi's usual search.json form), the exact date format, and what an empty result
looks like. This script therefore saves the raw response and prints a summary. It
does not write any CSV yet.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"

ENDPOINT = "https://serpapi.com/search.json"  # UNVERIFIED: path not stated on the docs page read
ENGINE = "google_events"
KEY_NAME = "SERPAPI_API_KEY"
TIMEOUT_S = 30
PAUSE_S = 1.0  # free plan: 50 searches per hour, so go one at a time
# topic -> words used in the search. "events" is the broad form from SerpApi's docs
# example ("Events in Austin, TX"); the narrower ones can come back empty for small towns.
TOPICS = {"events": "events", "live_music": "live music events", "sports": "sports events"}


def load_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines from a .env file. Blank lines and # comments are skipped."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def get_key() -> str:
    return os.environ.get(KEY_NAME) or load_env_file(ROOT / ".env").get(KEY_NAME, "")


def scrub(text: str, key: str) -> str:
    """Remove the key from text. Error messages from requests can include the full URL."""
    return text.replace(key, "[key removed]") if key else text


def query_for(area: str, topic: str) -> str:
    return f"{TOPICS[topic].capitalize()} in {area}, NJ"


def cache_file(zip_code: str, topic: str) -> Path:
    return CACHE / f"serpapi_{zip_code}_{topic}.json"


def fetch(area: str, zip_code: str, topic: str, key: str, refresh: bool) -> tuple[dict, bool]:
    """Return (response, from_cache). Only HTTP 200 responses are written to the cache."""
    path = cache_file(zip_code, topic)
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8")), True
    params = {"engine": ENGINE, "q": query_for(area, topic), "api_key": key}
    try:
        resp = requests.get(ENDPOINT, params=params, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise RuntimeError(scrub(f"request failed for {area} / {topic}: {exc}", key)) from None
    if resp.status_code in (401, 403):
        raise SystemExit(f"SerpApi refused the key (HTTP {resp.status_code}). Check {KEY_NAME} in .env.")
    if resp.status_code != 200:
        try:
            message = resp.json().get("error", "")
        except ValueError:
            message = resp.text[:200]
        raise RuntimeError(scrub(f"HTTP {resp.status_code} for {area} / {topic}: {message}", key))
    payload = resp.json()
    payload["_fetched_on"] = date.today().isoformat()
    payload["_query"] = params["q"]
    text = scrub(json.dumps(payload, indent=1), key)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return json.loads(text), False


def summarize(payload: dict) -> dict:
    """Counts and a few sample lines. Never invents a field that is missing."""
    events = payload.get("events_results")
    if events is None:
        return {"events": 0, "with_ticket_link": 0, "samples": [], "note": str(payload.get("error", "no events_results in the response"))}
    with_link = sum(1 for e in events if any(t.get("link") for t in (e.get("ticket_info") or [])))
    samples = []
    for e in events[:3]:
        when = e.get("date")
        when = (when.get("start_date"), when.get("when")) if isinstance(when, dict) else (str(when), "")
        samples.append(f"{e.get('title', '(no title)')} | {when[0]} | {when[1]}")
    return {"events": len(events), "with_ticket_link": with_link, "samples": samples, "note": ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore cached files and search again")
    ap.add_argument("--only", default="", help='comma-separated area names, e.g. "Highland Park,Metuchen"')
    args = ap.parse_args()

    key = get_key()
    if not key:
        print(f"No {KEY_NAME} found. Add it to .env (one line: {KEY_NAME}=your_key) and run again.")
        return 2

    with (DATA / "areas.csv").open(newline="", encoding="utf-8") as f:
        areas = list(csv.DictReader(f))
    wanted = {n.strip().lower() for n in args.only.split(",") if n.strip()}
    if wanted:
        areas = [a for a in areas if a["area"].lower() in wanted]
        if not areas:
            print("None of those names match data/areas.csv.")
            return 2

    live_searches = failures = 0
    for a in areas:
        zip_code = a["zip"].strip().zfill(5)
        for topic in TOPICS:
            will_search = args.refresh or not cache_file(zip_code, topic).exists()
            if will_search and live_searches:
                time.sleep(PAUSE_S)
            try:
                payload, cached = fetch(a["area"], zip_code, topic, key, args.refresh)
            except RuntimeError as exc:
                print(f"{a['area']:<18} {topic:<10} FAILED: {exc}")
                failures += 1
                continue
            live_searches += 0 if cached else 1
            s = summarize(payload)
            print(f"{a['area']:<18} {topic:<10} {s['events']:>3} events, {s['with_ticket_link']} with a ticket link"
                  + ("  (cached)" if cached else "") + (f"  [{s['note']}]" if s["note"] else ""))
            for line in s["samples"]:
                print(f"    {line}")

    print(f"\n{live_searches} new search(es) used. Raw responses are in data/cache/serpapi_*.json.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
