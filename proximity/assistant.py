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


CLARIFY_QUESTION = "Where do you need to commute to, and how often?"


def draft_profile(text: str, **llm_options) -> Draft:
    """A first profile from a free-text description. `changes` are relative to the starter profile.

    If the description names nowhere the person needs to get to, the draft comes
    back with one clarifying question. It is the only question the app ever asks.
    """
    fallback = keyword_refine(text, starter_profile(), draft=True)
    if mentions_demographics(text):
        return Draft(fallback)
    result = llm.draft(text, **llm_options)
    proposal = fallback
    question = ""
    if result is not None:
        ai, question = result
        if ai.changes or not fallback.changes:
            proposal = ai
        else:
            question = ""
    if not proposal.profile.destinations:
        question = question or CLARIFY_QUESTION  # the required field is missing, AI or not
    else:
        question = ""
    return Draft(proposal, question)


def answer_question(text: str, answer: str, **llm_options) -> Draft:
    """Re-draft with the person's answer added. Never asks a second question."""
    combined = (text.strip() + " " + answer.strip()) if answer.strip() else text
    draft = draft_profile(combined, **llm_options)
    draft.question = ""
    return draft
