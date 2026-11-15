"""One place the UI asks for profile suggestions.

The UI never talks to the keyword parser or the LLM directly. It calls these two
functions and always gets a Proposal back, whatever happens underneath.
"""
from __future__ import annotations

from .models import Profile
from .refine import Proposal, keyword_refine, starter_profile


def propose_refine(text: str, profile: Profile) -> Proposal:
    """Proposed edits to the current profile for a sentence like 'Newark matters more than gyms'."""
    return keyword_refine(text, profile)


def draft_profile(text: str) -> Proposal:
    """A first profile from a free-text description. `changes` are relative to the starter profile."""
    return keyword_refine(text, starter_profile())
