"""Gemini layer: free text -> proposed profile. It proposes; it never scores or ranks.

SDK: google-genai 3.3.0 (checked 2026-11-14 against the installed package's own
field docs). Structured JSON output is supported: GenerateContentConfig takes
response_mime_type="application/json" plus response_schema, and HttpOptions.timeout
is in milliseconds. We still parse and validate the JSON ourselves, strictly.

Every call:
- is answered from data/cache/llm_cache.json when the same input was seen before,
- otherwise has an 8 second limit, enforced by our own wall clock (the SDK deadline is 10 s,
  the lowest the API accepts),
- returns None on ANY problem (no key, timeout, bad JSON, unknown field, value out
  of range). The caller then uses the keyword parser. Nothing here can raise.

The API key is read from the environment / .env only. It is never logged.
Set DEMO_MODE=1 to forbid network calls entirely (cache or keyword parser only).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import BEDROOM_LABELS, DESTINATIONS, INTERESTS, DestinationPref, InterestPref, Profile
from .refine import AI, Proposal, diff_profiles, keyword_refine, mentions_demographics, starter_profile

MODEL = "gemini-3.5-flash"
# Used in order, only when the model before reports its quota is used up or is unavailable.
# Checked 2026-11-14 with client.models.list(): each has its own free-tier quota of 20 requests a day.
FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite")
TIMEOUT_S = 8.0  # what the user waits, at most
# The API refuses a deadline under 10 s ("Minimum allowed deadline is 10s"), so the SDK gets 10 s
# and our own wall clock cuts the wait off at TIMEOUT_S.
SDK_TIMEOUT_MS = 10_000
CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "cache" / "llm_cache.json"

_Importance = Literal["Must-have", "Important", "Nice"]
_Bedroom = Literal["shared2br", "0br", "1br", "2br"]
_Mode = Literal["transit", "drive"]


# ---- what we ask the API for: plain types only, so the schema is widely supported ----
class _ApiDestination(BaseModel):
    name: str
    mode: str
    max_minutes: int
    importance: str


class _ApiInterest(BaseModel):
    category: str
    importance: str


class _ApiReason(BaseModel):
    field: str
    reason: str


class _ApiOut(BaseModel):
    budget: int | None = None
    bedroom: str
    rent_importance: str
    destinations: list[_ApiDestination]
    interests: list[_ApiInterest]
    reasons: list[_ApiReason]
    question: str
    not_used: list[str] = Field(default_factory=list)


# ---- what we accept: strict. Unknown fields or out-of-range values reject the whole answer ----
class _Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    mode: _Mode
    max_minutes: int = Field(ge=5, le=180)
    importance: _Importance


class _Interest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    importance: _Importance


class _Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(max_length=80)
    reason: str = Field(max_length=200)


class LLMOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget: int | None = Field(default=None, ge=500, le=6000)
    bedroom: _Bedroom
    rent_importance: _Importance
    destinations: list[_Destination] = Field(max_length=len(DESTINATIONS))
    interests: list[_Interest] = Field(max_length=len(INTERESTS))
    reasons: list[_Reason] = Field(default_factory=list, max_length=20)
    question: str = Field(default="", max_length=200)
    not_used: list[str] = Field(default_factory=list, max_length=20)


class LLMRejected(Exception):
    """The model's answer was not usable. Never shown to the user; triggers the fallback."""


def validate(raw: str) -> LLMOut:
    """Strictly parse the model's text. Raises LLMRejected for anything off-schema."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise LLMRejected("not JSON") from exc
    try:
        out = LLMOut.model_validate(data)
    except ValidationError as exc:
        raise LLMRejected("schema") from exc
    names = [d.name for d in out.destinations]
    cats = [i.category for i in out.interests]
    if any(n not in DESTINATIONS for n in names) or len(set(names)) != len(names):
        raise LLMRejected("unknown or repeated destination")
    if any(c not in INTERESTS for c in cats) or len(set(cats)) != len(cats):
        raise LLMRejected("unknown or repeated interest")
    # Fair housing: the model must not talk about residents either.
    spoken = " ".join([r.reason for r in out.reasons] + [out.question] + out.not_used)
    if mentions_demographics(spoken):
        raise LLMRejected("demographic wording in output")
    return out


def to_profile(out: LLMOut) -> Profile:
    return Profile(
        budget=out.budget,
        bedroom=out.bedroom,
        rent_importance=out.rent_importance,
        destinations=[DestinationPref(**d.model_dump()) for d in out.destinations],
        interests=[InterestPref(**i.model_dump()) for i in out.interests],
    )


def _reason_map(out: LLMOut) -> dict[str, str]:
    """Model field names -> the change keys diff_profiles uses."""
    mapped: dict[str, str] = {}
    for r in out.reasons:
        f = r.field.strip()
        low = f.lower()
        if low.startswith("destination:"):
            name = f.split(":", 1)[1].strip()
            match = next((d for d in DESTINATIONS if d.lower() == name.lower()), None)
            if match:
                mapped[f"dest:{match}"] = r.reason.strip()
        elif low.startswith("interest:"):
            mapped[f"interest:{low.split(':', 1)[1].strip()}"] = r.reason.strip()
        elif low in ("budget", "bedroom", "rent_importance"):
            mapped[low] = r.reason.strip()
    return mapped


# ------------------------------------------------------------------ prompts
_RULES = f"""You help a renter describe what they need from a neighborhood. You only fill in or edit a profile.
Code, not you, scores and ranks neighborhoods. Never mention scores, rankings or specific neighborhoods.

The profile has exactly these fields:
- budget: whole dollars per month, 500 to 6000, or null if the person did not give an amount.
- bedroom: one of {list(BEDROOM_LABELS)} (shared2br = a room in a shared 2-bedroom, 0br = studio).
- rent_importance: how much room under budget matters. One of "Nice", "Important", "Must-have".
- destinations: places the person must travel to. name must be one of {DESTINATIONS}.
  mode is "transit" (no car) or "drive". max_minutes is 5 to 180. importance is "Nice", "Important" or "Must-have".
- interests: category must be one of {list(INTERESTS)}. importance is "Nice", "Important" or "Must-have".
  (live_music covers jazz, bars and concerts; sports covers cricket and courts.)
- reasons: one entry per field you set or changed. field is "budget", "bedroom", "rent_importance",
  "destination:<name>" or "interest:<category>". reason is one short plain sentence, at most 18 words,
  that quotes or paraphrases what the person said.
- question: normally "".
- not_used: brief plain sentences for details that cannot be represented or checked. Use [] when none.
  Mention unsupported work/job/office destinations, a minutes limit with no supported destination,
  a requested mile/radius distance (places use the fixed 1.5 km search radius), and houses (only shared room,
  studio, 1-bed and 2-bed are supported). Never guess shop or restaurant names; do not list unmatched words.

Importance levels in order: Nice < Important < Must-have.

Proximity matches on places, travel time, rent and events only. Never use, infer or mention who lives in an
area: no race, religion, nationality, age, family status, income or safety of residents. If the person asks
for that, ignore that part and change nothing for it.

Reply with JSON only."""

_REFINE = (
    _RULES
    + """

TASK: edit the current profile to reflect the person's new message.
Change only what the message asks for. Copy every other field exactly as it is.
"X matters more than Y" means raise X one level and, if Y is above "Nice", lower Y one level.
If the message gives no usable instruction, return the profile unchanged with reasons []."""
)

_DRAFT = (
    _RULES
    + """

TASK: write a first profile from the person's description.
Only include destinations and interests the person mentions. Do not invent any.
If no budget is given return null. If they mention sharing or roommates use bedroom "shared2br"; if no housing
type is given use "shared2br". No car means mode "transit". Use rent_importance "Important" unless they say how much saving money matters.
A trip they make most days (for example classes) is "Must-have"; if no time limit is given use max_minutes 40.
A trip they make only a few days a week is "Important"; if no time limit is given use max_minutes 80.
A liking they stress ("love") is "Important"; other likings are "Nice".
If the description names no place they need to travel to, set question to one short question asking where
they need to commute to and how often. Otherwise question is ""."""
)


last_failure = ""  # why the most recent call fell back; for debugging only, never shown to users


def _note_failure(reason: str) -> None:
    """Remember why a call failed, with anything key-like removed."""
    global last_failure
    last_failure = re.sub(r"(AQ\.|AIza)[A-Za-z0-9_.\-]{8,}", "<key>", reason)[:300]


# -------------------------------------------------------------------- cache
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").strip().lower())


def cache_key(kind: str, text: str, base: Profile) -> str:
    blob = json.dumps([kind, _norm(text), base.model_dump()], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _read_cache(path: Path = CACHE_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(key: str, entry: dict, path: Path = CACHE_FILE) -> None:
    data = _read_cache(path)
    data[key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------- client
_client = None


def get_client():
    """A Gemini client, or None when there is no key or the SDK is missing."""
    global _client
    if _client is not None:
        return _client
    if os.environ.get("DEMO_MODE") == "1":
        return None
    try:
        from dotenv import load_dotenv
        from google import genai
        from google.genai import types

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            return None
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=SDK_TIMEOUT_MS))
        return _client
    except Exception:  # missing package, bad key format: the app carries on without AI
        return None


_exhausted: set[str] = set()  # models that answered "quota exceeded" in this run; skipped afterwards


def _config(model: str, system: str):
    try:
        from google.genai import types

        # Measured 2026-11-14 on gemini-3.5-flash: default thinking ~6 s per call, "low" ~2 s.
        thinking = (
            types.ThinkingConfig(thinking_budget=0) if model.startswith("gemini-2.")
            else types.ThinkingConfig(thinking_level="low")
        )
        return types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=_ApiOut,
            temperature=0,
            thinking_config=thinking,
        )
    except Exception:  # a stand-in client in tests does not need the real config object
        return None


def _generate(client, system: str, user: str) -> str:
    """Ask MODEL; if its quota is used up or it is unavailable, try the fallback models in turn."""
    last: Exception | None = None
    for model in (MODEL, *FALLBACK_MODELS):
        if model in _exhausted:
            continue
        try:
            return client.models.generate_content(model=model, contents=user, config=_config(model, system)).text
        except Exception as exc:
            last = exc
            text = str(exc)
            if "429" in text or "RESOURCE_EXHAUSTED" in text or "404" in text or "NOT_FOUND" in text:
                _exhausted.add(model)  # the free tier allows 20 requests a day per model
                continue
            if "503" in text or "UNAVAILABLE" in text:
                continue  # busy right now: try the next model, but do not rule this one out
            raise
    raise last if last is not None else RuntimeError("every model's quota is used up")


def _ask(kind: str, system: str, user: str, text: str, base: Profile, client, timeout: float, cache_path: Path,
         save: bool) -> Optional[LLMOut]:
    """Cache first, then the API with a hard time limit. None on any failure."""
    key = cache_key(kind, text, base)
    cached = _read_cache(cache_path).get(key)
    if cached is not None:
        try:
            return validate(json.dumps(cached["output"]))
        except (LLMRejected, KeyError, TypeError):
            pass  # a bad cache entry is ignored, not trusted
    client = client if client is not None else get_client()
    if client is None:
        return None
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        raw = pool.submit(_generate, client, system, user).result(timeout=timeout)
        out = validate(raw)
    except FutureTimeout:
        _note_failure(f"timeout after {timeout:g}s")
        return None
    except Exception as exc:  # network, quota, bad JSON, schema: all handled the same way
        _note_failure(f"{type(exc).__name__}: {exc}")
        return None
    finally:
        pool.shutdown(wait=False)
    if save:
        _write_cache(key, {"kind": kind, "text": text, "output": out.model_dump()}, cache_path)
    return out


# --------------------------------------------------------------- public API
def refine(text: str, profile: Profile, client=None, timeout: float = TIMEOUT_S, cache_path: Path = CACHE_FILE,
           save: bool = False) -> Optional[Proposal]:
    """AI-proposed edits to the profile, or None to tell the caller to use the keyword parser."""
    if mentions_demographics(text):
        return None  # such text is never sent to the model; the keyword path gives the fixed reply
    user = f"Current profile:\n{profile.model_dump_json()}\n\nThe person's new message:\n{text.strip()[:600]}"
    out = _ask("refine", _REFINE, user, text, profile, client, timeout, cache_path, save)
    if out is None:
        return None
    explicit_budget = keyword_refine(text, starter_profile(), draft=True).profile.budget
    if explicit_budget is None:
        out = out.model_copy(update={"budget": profile.budget})
    new = to_profile(out)
    return Proposal(profile=new, changes=_with_reasons(profile, new, out), source=AI, not_used=out.not_used)


def draft(text: str, client=None, timeout: float = TIMEOUT_S, cache_path: Path = CACHE_FILE,
          save: bool = False) -> Optional[tuple[Proposal, str]]:
    """(AI draft profile, clarifying question or '') from a description, or None for the fallback."""
    if mentions_demographics(text):
        return None
    base = starter_profile()
    user = f"The person's description:\n{text.strip()[:1200]}"
    out = _ask("draft", _DRAFT, user, text, base, client, timeout, cache_path, save)
    explicit_budget = keyword_refine(text, base, draft=True).profile.budget
    if out is not None and explicit_budget is None:
        out = out.model_copy(update={"budget": None})
    if out is None and base.budget is None:
        # Older cached answers are only safe to reuse when the description contains a parsed amount.
        if explicit_budget is not None:
            legacy_base = base.model_copy(update={"budget": 1500})
            cached = _read_cache(cache_path).get(cache_key("draft", text, legacy_base))
            if cached is not None:
                try:
                    legacy = validate(json.dumps(cached["output"]))
                    if legacy.budget == explicit_budget:
                        out = legacy
                except (LLMRejected, KeyError, TypeError):
                    pass
    if out is None:
        return None
    new = to_profile(out)
    return Proposal(
        profile=new, changes=_with_reasons(base, new, out), source=AI, not_used=out.not_used,
    ), out.question.strip()


def _with_reasons(old: Profile, new: Profile, out: LLMOut):
    changes = diff_profiles(old, new, _reason_map(out))
    for c in changes:
        if not c.reason:
            c.reason = "Suggested from what you wrote"
    return changes


# ------------------------------------------------------- demo cache warm-up
# The first sentence of each pair is the opener; the second follows it. The "live music" opener
# changes who is #1 on the current data; the "gyms" one does not.
DEMO_OPENERS = ["Getting to Newark matters more than live music", "Getting to Newark matters more than gyms"]
# The demo line that changes who is #1. Not cached: with DEMO_MODE=1 the keyword parser handles it.
DEMO_RERANK = "I need a grocery store nearby"
DEMO_FOLLOW_UP = "Actually I could stretch to $1,800 if it's walkable to music venues"
DEMO_REFINES = [
    "Getting to Newark matters more than gyms",
    "Actually I could stretch to $1,800 if it's walkable to music venues",
]


def warm_demo_cache() -> None:
    """Ask the real API once for each demo input and save the answers, so the demo runs offline.

    Run:  python -m proximity.llm
    Covers the landing description, then both refines in order, starting from
    Priya's example profile and from the AI draft of her description.
    """
    from .models import demo_profile
    from .ui.landing import EXAMPLE

    result = draft(EXAMPLE, save=True)
    print("landing draft:", "ok" if result else f"FAILED ({last_failure})")
    bases = [("Priya's example", demo_profile())]
    if result:
        bases.append(("AI draft", result[0].profile))
    for name, base in bases:
        for opener in DEMO_OPENERS:
            current = base
            for text in (opener, DEMO_FOLLOW_UP):
                proposal = refine(text, current, save=True)
                print(f"{name} -> {text[:46]!r}:", f"{len(proposal.changes)} change(s)" if proposal else f"FAILED ({last_failure})")
                if proposal is None:
                    break
                current = proposal.profile


if __name__ == "__main__":
    warm_demo_cache()
