"""LLM layer tests. The client is always a stand-in: no test makes a real API call."""
import json
import time

import pytest

from proximity import assistant, llm
from proximity.models import demo_profile
from proximity.refine import AI, DEMOGRAPHIC_REPLY, KEYWORD, NOT_UNDERSTOOD

NEWARK = "Newark Penn Station"
TEXT = "Getting to Newark matters more than gyms"


class FakeClient:
    """Looks enough like genai.Client: client.models.generate_content(...).text"""

    def __init__(self, reply=None, error=None, delay=0.0):
        self.reply, self.error, self.delay = reply, error, delay
        self.calls = 0
        self.models = self

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return type("Response", (), {"text": self.reply})()


def answer(**overrides) -> str:
    """A valid model answer: Priya's profile with Newark raised to Must-have."""
    out = demo_profile().model_dump()
    out["destinations"][1]["importance"] = "Must-have"
    out["reasons"] = [{"field": f"destination:{NEWARK}", "reason": "You said Newark matters more than gyms."}]
    out["question"] = ""
    out.update(overrides)
    return json.dumps(out)


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "llm_cache.json"


def test_valid_answer_becomes_an_ai_proposal_with_the_models_reason(cache):
    p = assistant.propose_refine(TEXT, demo_profile(), client=FakeClient(answer()), cache_path=cache)
    assert p.source == AI
    assert [(c.key, c.old, c.new) for c in p.changes] == [(f"dest:{NEWARK}:importance", "Important", "Must-have")]
    assert p.changes[0].reason == "You said Newark matters more than gyms."


@pytest.mark.parametrize("bad", [
    "this is not json",
    "",
    json.dumps({"budget": 1600}),  # fields missing
    answer(score=99),  # unknown field
    answer(budget=99999),  # out of range
    answer(budget="lots"),  # wrong type
    answer(bedroom="mansion"),  # not an allowed value
    answer(destinations=[{"name": "Hoboken", "mode": "transit", "max_minutes": 30, "importance": "Nice"}]),
    answer(destinations=[{"name": NEWARK, "mode": "teleport", "max_minutes": 30, "importance": "Nice"}]),
    answer(destinations=[{"name": NEWARK, "mode": "transit", "max_minutes": 999, "importance": "Nice"}]),
    answer(interests=[{"category": "casinos", "importance": "Nice"}]),
    answer(interests=[{"category": "gym", "importance": "Critical"}]),
    answer(reasons=[{"field": "budget", "reason": "This area has lots of young professionals."}]),
])
def test_invalid_answers_are_discarded_and_the_keyword_parser_is_used(bad, cache):
    client = FakeClient(bad)
    assert llm.refine(TEXT, demo_profile(), client=client, cache_path=cache) is None
    p = assistant.propose_refine(TEXT, demo_profile(), client=client, cache_path=cache)
    assert p.source == KEYWORD
    assert [c.key for c in p.changes] == [f"dest:{NEWARK}:importance"]


def test_timeout_falls_back_to_keywords_without_waiting_for_the_model(cache):
    slow = FakeClient(answer(), delay=1.0)
    start = time.time()
    p = assistant.propose_refine(TEXT, demo_profile(), client=slow, cache_path=cache, timeout=0.1)
    assert time.time() - start < 0.8
    assert p.source == KEYWORD and p.changes


def test_api_errors_fall_back_to_keywords(cache):
    for error in (RuntimeError("503 unavailable"), ConnectionError("no network"), ValueError("quota")):
        p = assistant.propose_refine(TEXT, demo_profile(), client=FakeClient(error=error), cache_path=cache)
        assert p.source == KEYWORD and p.changes


def test_no_key_means_keywords_only(cache, monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda: None)
    p = assistant.propose_refine(TEXT, demo_profile(), cache_path=cache)
    assert p.source == KEYWORD and p.changes


@pytest.mark.parametrize("text", ["somewhere safe", "an area with lots of Indians", "good for families"])
def test_demographic_requests_never_reach_the_model(text, cache):
    client = FakeClient(answer())
    p = assistant.propose_refine(text, demo_profile(), client=client, cache_path=cache)
    assert client.calls == 0
    assert p.message == DEMOGRAPHIC_REPLY and p.changes == []
    assert assistant.draft_profile(text, client=client, cache_path=cache).proposal.message == DEMOGRAPHIC_REPLY
    assert client.calls == 0


def test_the_prompts_contain_no_demographic_request_and_forbid_resident_data():
    for prompt in (llm._REFINE, llm._DRAFT):
        assert "Never use, infer or mention who lives" in prompt
        assert "scores and ranks" in prompt  # the model is told it does not rank


def test_model_cannot_change_scores_only_profile_fields():
    # The accepted schema has profile fields, reasons and a question. Nothing else gets through.
    assert set(llm.LLMOut.model_fields) == {
        "budget", "bedroom", "rent_importance", "destinations", "interests", "reasons", "question",
    }


def test_unchanged_answer_with_no_keyword_match_says_not_understood(cache):
    same = demo_profile().model_dump() | {"reasons": [], "question": ""}
    p = assistant.propose_refine("hmm, not sure", demo_profile(), client=FakeClient(json.dumps(same)), cache_path=cache)
    assert p.changes == [] and p.message == NOT_UNDERSTOOD


def test_cached_answers_work_offline(cache):
    first = llm.refine(TEXT, demo_profile(), client=FakeClient(answer()), cache_path=cache, save=True)
    assert first is not None and cache.exists()
    broken = FakeClient(error=ConnectionError("offline"))
    again = llm.refine("  getting to NEWARK matters more than gyms ", demo_profile(), client=broken, cache_path=cache)
    assert again is not None and broken.calls == 0  # served from the cache, spacing and case ignored
    assert [c.key for c in again.changes] == [c.key for c in first.changes]
    # A different profile is a different question, so the cache does not answer it.
    other = demo_profile()
    other.budget = 1700
    assert llm.refine(TEXT, other, client=broken, cache_path=cache) is None


def test_a_corrupt_cache_entry_is_ignored(cache):
    key = llm.cache_key("refine", TEXT, demo_profile())
    cache.write_text(json.dumps({key: {"output": {"budget": -5}}}), encoding="utf-8")
    p = llm.refine(TEXT, demo_profile(), client=FakeClient(answer()), cache_path=cache)
    assert p is not None and p.source == AI


def test_draft_returns_a_profile_and_at_most_one_question(cache):
    out = demo_profile().model_dump() | {"destinations": [], "reasons": [], "question": "Where do you need to commute to, and how often?"}
    d = assistant.draft_profile("I like music and have $1,600", client=FakeClient(json.dumps(out)), cache_path=cache)
    assert d.proposal.source == AI and d.proposal.profile.budget == 1600
    assert d.question == "Where do you need to commute to, and how often?"
    fallback = assistant.draft_profile("budget $1,400, I love parks", client=FakeClient("nope"), cache_path=cache)
    assert fallback.proposal.source == KEYWORD
    assert fallback.question == assistant.CLARIFY_QUESTION  # no destination given, so the one question is asked
    assert fallback.proposal.profile.budget == 1400


def test_shipped_demo_cache_answers_the_demo_inputs_without_a_client(monkeypatch):
    """The three demo inputs must work with no network and no key."""
    from proximity.ui.landing import EXAMPLE

    monkeypatch.setattr(llm, "get_client", lambda: None)
    landing = llm.draft(EXAMPLE)
    assert landing is not None, "Priya's landing description is not cached"
    for base in (demo_profile(), landing[0].profile):
        first = llm.refine(TEXT, base)
        assert first is not None and first.changes, "first demo refine is not cached"
        second = llm.refine("Actually I could stretch to $1,800 if it's walkable to music venues", first.profile)
        assert second is not None and second.profile.budget == 1800, "second demo refine is not cached"


def test_one_clarifying_question_when_no_destination_is_given_and_never_a_second(cache):
    none = demo_profile().model_dump() | {"destinations": [], "reasons": [], "question": ""}
    client = FakeClient(json.dumps(none))
    first = assistant.draft_profile("I have $1,600 and love live music", client=client, cache_path=cache)
    assert first.question == assistant.CLARIFY_QUESTION  # asked even though the model did not ask
    # The answer names a place: the keyword path alone can use it, and no further question is asked.
    again = assistant.answer_question("I have $1,600 and love live music", "classes on College Ave, under 30 min",
                                      client=FakeClient("not json"), cache_path=cache)
    assert again.question == ""
    assert [d.name for d in again.proposal.profile.destinations] == ["Rutgers College Ave"]
    # Even if the answer is empty and there is still no destination, the app does not ask again.
    skipped = assistant.answer_question("I have $1,600 and love live music", "", client=client, cache_path=cache)
    assert skipped.question == ""


def test_no_question_when_the_description_already_names_a_destination(cache):
    d = assistant.draft_profile("classes on College Ave, budget $1,500", client=FakeClient("not json"), cache_path=cache)
    assert d.question == "" and d.proposal.profile.destinations


def test_the_cached_opener_that_changes_number_one_works_offline(monkeypatch):
    """'Getting to Newark matters more than live music' is the demo line that re-sorts the ranking."""
    from proximity.data import build_area_facts, load_all
    from proximity.scoring import score_areas

    monkeypatch.setattr(llm, "get_client", lambda: None)
    proposal = llm.refine(llm.DEMO_OPENERS[0], demo_profile())
    assert proposal is not None and proposal.source == AI, "the re-sorting demo sentence is not cached"
    assert all(c.reason for c in proposal.changes)
    areas = build_area_facts(load_all())
    before = next(r.area for r in score_areas(areas, demo_profile()) if r.rank == 1)
    after = next(r.area for r in score_areas(areas, proposal.profile) if r.rank == 1)
    assert before != after  # with today's data files, this sentence changes who is #1
