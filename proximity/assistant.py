"""One place the UI asks for profile suggestions.

The UI never talks to the keyword parser or the LLM directly. It calls these two
functions and always gets an answer back, whatever happens underneath:
AI first, keyword parser whenever the AI is unavailable, slow or off-schema.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import llm
from .models import Profile
from .refine import NOT_UNDERSTOOD, Proposal, keyword_refine, mentions_demographics, starter_profile


def propose_refine(text: str, profile: Profile, **llm_options) -> Proposal:
    """Proposed edits to the current profile for a sentence like 'Newark matters more than gyms'."""
    fallback = keyword_refine(text, profile)
    if mentions_demographics(text):
        return fallback  # fixed reply; this text is never sent to the model
    ai = llm.refine(text, profile, **llm_options)
    if ai is None:
        return fallback
    if not ai.changes:
        # The model understood nothing to change. Prefer a keyword match if there is one.
        if fallback.changes:
            return fallback
        ai.message = NOT_UNDERSTOOD
    return ai


@dataclass
class Draft:
    proposal: Proposal
    question: str = ""  # at most one clarifying question; "" when none is needed


def draft_profile(text: str, **llm_options) -> Draft:
    """A first profile from a free-text description. `changes` are relative to the starter profile."""
    fallback = keyword_refine(text, starter_profile())
    if mentions_demographics(text):
        return Draft(fallback)
    result = llm.draft(text, **llm_options)
    if result is None:
        return Draft(fallback)
    proposal, question = result
    if not proposal.changes and fallback.changes:
        return Draft(fallback)
    return Draft(proposal, question)
