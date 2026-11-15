"""The SerpApi fetch script must never leak the key and must not spend searches on cached areas."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fetch_serpapi_events.py"
spec = importlib.util.spec_from_file_location("fetch_serpapi_events", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

KEY = "test-key-123"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def test_queries_follow_the_docs_example_form():
    assert mod.query_for("Highland Park", "events") == "Events in Highland Park, NJ"
    assert mod.query_for("Metuchen", "live_music") == "Live music events in Metuchen, NJ"


def test_scrub_removes_the_key():
    assert KEY not in mod.scrub(f"failed at https://x/search.json?api_key={KEY}", KEY)


def test_load_env_file_skips_comments_and_quotes(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# note\nSERPAPI_API_KEY="abc"\n\nOTHER=1\n', encoding="utf-8")
    assert mod.load_env_file(f) == {"SERPAPI_API_KEY": "abc", "OTHER": "1"}
    assert mod.load_env_file(tmp_path / "missing") == {}


def test_fetch_caches_without_the_key_and_reuses_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CACHE", tmp_path)
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        # a response that echoes the key back, which must not reach the cache file
        return FakeResponse(200, {"events_results": [{"title": "Jazz night"}], "echo": params["api_key"]})

    monkeypatch.setattr(mod.requests, "get", fake_get)
    first, cached_first = mod.fetch("Metuchen", "08840", "live_music", KEY, refresh=False)
    second, cached_second = mod.fetch("Metuchen", "08840", "live_music", KEY, refresh=False)

    assert (cached_first, cached_second) == (False, True)
    assert len(calls) == 1  # second call came from the cache
    saved = (tmp_path / "serpapi_08840_live_music.json").read_text(encoding="utf-8")
    assert KEY not in saved
    assert second["events_results"][0]["title"] == "Jazz night"


def test_rejected_key_stops_the_run_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CACHE", tmp_path)
    monkeypatch.setattr(mod.requests, "get", lambda url, params, timeout: FakeResponse(401, {"error": "bad key"}))
    with pytest.raises(SystemExit):
        mod.fetch("Metuchen", "08840", "sports", KEY, refresh=False)
    assert list(tmp_path.iterdir()) == []


def test_summarize_counts_ticket_links_and_handles_a_missing_list():
    payload = {"events_results": [
        {"title": "A", "date": {"start_date": "Nov 21", "when": "Sat, Nov 21"}, "ticket_info": [{"link": "https://t/1"}]},
        {"title": "B", "date": {}, "ticket_info": []},
    ]}
    s = mod.summarize(payload)
    assert (s["events"], s["with_ticket_link"]) == (2, 1)
    empty = mod.summarize({"error": "no results"})
    assert empty["events"] == 0 and empty["note"] == "no results"
