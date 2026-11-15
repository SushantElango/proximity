"""Turn a sentence into proposed profile edits, shown as a diff the user applies.

This file is the keyword/rule parser: no AI, no network. It is the floor the
app always has, and the fallback whenever the LLM is slow, down or wrong.

Nothing here scores or ranks. It only proposes a new Profile; scoring.py decides.

Fair housing: a request about who lives in an area is never turned into a
profile change. It gets DEMOGRAPHIC_REPLY instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    BEDROOM_LABELS,
    DESTINATIONS,
    IMPORTANCE_LEVELS,
    INTERESTS,
    DestinationPref,
    InterestPref,
    Profile,
)

KEYWORD = "Keyword match"
AI = "AI suggestion"

DEMOGRAPHIC_REPLY = (
    "Proximity doesn't filter by who lives in an area. I can use commute, budget, places and events instead."
)
NOT_UNDERSTOOD = "I couldn't map that — edit your profile directly."
AT_LIMIT = "Already at the {end} level, so nothing changed: {items}."

BUDGET_RANGE = (500, 6000)
MINUTES_RANGE = (5, 180)

# ---------------------------------------------------------------- vocabulary
DEST_WORDS: dict[str, list[str]] = {
    "Newark Penn Station": ["newark"],
    "Rutgers College Ave": ["college ave", "college avenue", "campus", "class", "classes", "rutgers"],
    "Rutgers Busch": ["busch"],
}
INTEREST_WORDS: dict[str, list[str]] = {
    "gym": ["gym", "gyms", "fitness", "workout", "working out"],
    "live_music": [
        "live music", "music", "jazz", "bars", "bar", "pub", "pubs", "entertainment",
        "brewery", "breweries", "venues", "nightlife", "concerts",
    ],
    "sports": ["sports", "sport", "cricket", "courts", "pitches"],
    "grocery": ["grocery", "groceries", "supermarket", "supermarkets"],
    "park": ["park", "parks", "green space"],
    "cafe": ["cafe", "cafes", "coffee"],
    "library": ["library", "libraries"],
    "restaurant": ["restaurant", "restaurants", "eating out", "food"],
    "place_of_worship": ["place of worship", "places of worship", "temple", "church", "mosque", "synagogue", "gurdwara"],
}
RENT_WORDS = ["rent", "cheap", "price", "cost", "saving money", "save money", "affordable"]
# Words for "my trips" in general: they reach every destination in the profile ("transit" only the transit ones).
TRAVEL_WORDS = ["commute", "commutes", "commuting", "travel", "travel time", "getting around", "trips"]
TRANSIT_WORDS = ["transit", "public transport", "public transportation", "bus", "buses", "train", "trains"]
# "make X more important", "X matters less", "prioritize X", "give X more weight"
_PRIORITY = re.compile(
    r"\b(?:(more|less)\s+(?:important|weight|priority|of a priority)|matters?\s+(more|less)|"
    r"(prioriti[sz]e|focus on|care more about|(?:raise|increase|bump up) (?:the )?(?:importance|priority|weight))|"
    r"(deprioriti[sz]e|care less about|(?:lower|decrease|reduce) (?:the )?(?:importance|priority|weight)))\b"
)

# People, not places. Any of these means the request is about who lives somewhere.
_GROUPS = (
    r"indians?|asians?|whites?|blacks?|hispanics?|latinos?|latinas?|jews|jewish|muslims?|christians?|hindus?|sikhs?|"
    r"chinese|koreans?|desis?|africans?|arabs?|americans?|immigrants?|foreigners?|gays?|lgbtq?\+?|seniors|elderly|"
    r"retirees|singles|couples"
)
_DEMOGRAPHIC_PATTERNS = [
    r"\bsafe(r|st|ty)?\b", r"\bcrime\b", r"\bdangerous\b", r"\bsketchy\b", r"\bbad (area|neighbou?rhood)\b",
    r"\bfamil(y|ies)\b", r"\bkids?\b", r"\bchildren\b",
    r"\bdemographics?\b", r"\bdivers(e|ity)\b", r"\bpeople like me\b", r"\bwho lives?\b",
    r"\b(rich|poor|wealthy|low[- ]income|high[- ]income|upscale|working[- ]class)\b",
    r"\byoung professionals\b", r"\bstudents? (live|living|neighbou?rhood|area)\b",
    rf"\b(lots|plenty|many|more|mostly|full) of ({_GROUPS})\b",
    rf"\b({_GROUPS}) (people|community|communities|families|neighbou?rhoods?|areas?|population|residents|neighbou?rs)\b",
    rf"\b(indians|asians|whites|blacks|hispanics|latinos|jews|muslims|christians|hindus|sikhs)\b",
]


def mentions_demographics(text: str) -> bool:
    """True when the text asks about residents rather than places, travel or rent."""
    low = text.lower()
    return any(re.search(p, low) for p in _DEMOGRAPHIC_PATTERNS)


# -------------------------------------------------------------------- diff
@dataclass
class Change:
    key: str  # stable id, e.g. "dest:Newark Penn Station:importance"
    field: str  # what the user sees in the Field column
    old: str
    new: str
    reason: str = ""


@dataclass
class Proposal:
    profile: Profile  # the profile if every change is applied
    changes: list[Change] = field(default_factory=list)
    source: str = KEYWORD
    message: str = ""  # shown above the table (refusal, or "couldn't map that")
    not_used: list[str] = field(default_factory=list)


def _level(importance: str) -> int:
    return IMPORTANCE_LEVELS.index(importance)


def _shift(importance: str, by: int) -> str:
    return IMPORTANCE_LEVELS[max(0, min(len(IMPORTANCE_LEVELS) - 1, _level(importance) + by))]


def diff_profiles(old: Profile, new: Profile, reasons: dict[str, str] | None = None) -> list[Change]:
    """Every field that differs, in a fixed order. `reasons` maps a change key (or its prefix) to a reason."""
    reasons = reasons or {}
    out: list[Change] = []

    def add(key: str, label: str, a: str, b: str) -> None:
        if a == b:
            return
        reason = reasons.get(key, "")
        if not reason:  # fall back to a reason given for the whole item, e.g. "dest:Newark Penn Station"
            reason = next((v for k, v in reasons.items() if key.startswith(k + ":")), "")
        out.append(Change(key=key, field=label, old=a, new=b, reason=reason))

    old_budget = f"${old.budget:,}" if old.budget is not None else "not set"
    new_budget = f"${new.budget:,}" if new.budget is not None else "not set"
    add("budget", "Budget", old_budget, new_budget)
    add("bedroom", "Housing", BEDROOM_LABELS[old.bedroom], BEDROOM_LABELS[new.bedroom])
    add("rent_importance", "Room under budget", old.rent_importance, new.rent_importance)

    od = {d.name: d for d in old.destinations}
    nd = {d.name: d for d in new.destinations}
    for name in DESTINATIONS:
        a, b = od.get(name), nd.get(name)
        if a is None and b is None:
            continue
        if a is None:
            add(f"dest:{name}:present", f"{name}", "not included",
                f"added · {b.mode} · max {b.max_minutes} min · {b.importance}")
        elif b is None:
            add(f"dest:{name}:present", f"{name}", f"{a.mode} · max {a.max_minutes} min · {a.importance}", "removed")
        else:
            add(f"dest:{name}:importance", f"{name}: importance", a.importance, b.importance)
            add(f"dest:{name}:max_minutes", f"{name}: max commute", f"{a.max_minutes} min", f"{b.max_minutes} min")
            add(f"dest:{name}:mode", f"{name}: travel by", a.mode, b.mode)

    oi = {i.category: i for i in old.interests}
    ni = {i.category: i for i in new.interests}
    for cat in list(oi) + [c for c in ni if c not in oi]:
        a, b = oi.get(cat), ni.get(cat)
        label = INTERESTS.get(cat, cat)
        if a is None:
            add(f"interest:{cat}:present", label, "not included", f"added · {b.importance}")
        elif b is None:
            add(f"interest:{cat}:present", label, a.importance, "removed")
        else:
            add(f"interest:{cat}:importance", f"{label}: importance", a.importance, b.importance)
    return out


# ------------------------------------------------------------------ parser
def _find(words: dict[str, list[str]], text: str) -> list[str]:
    """Keys whose words appear in the text, in the order they appear."""
    hits: list[tuple[int, str]] = []
    for key, options in words.items():
        positions = [m.start() for w in options for m in re.finditer(rf"\b{re.escape(w)}\b", text)]
        if positions:
            hits.append((min(positions), key))
    return [k for _, k in sorted(hits)]


# Words that name College Ave itself. The rest of its list ("campus", "classes", "rutgers") is generic.
_COLLEGE_AVE_ONLY = ["college ave", "college avenue"]


def _find_dests(text: str) -> list[str]:
    """Destinations named in the text. "Busch campus" or "classes at Busch" means Busch only:
    a generic word does not also add College Ave when another Rutgers campus is named."""
    found = _find(DEST_WORDS, text)
    if "Rutgers Busch" in found and "Rutgers College Ave" in found:
        if not any(re.search(rf"\b{re.escape(w)}\b", text) for w in _COLLEGE_AVE_ONLY):
            found.remove("Rutgers College Ave")
    return found


_BED = r"(?:[- ]?(?:bed(?:room)?s?|br|bd)\b)"


def _housing(text: str) -> str | None:
    """The housing type named in the text, as a BEDROOM_LABELS key, or None when none is named."""
    shared = re.search(r"\b(?:shar(?:e|ed|ing)|roommates?|housemates?|flatmates?|room in)\b", text) is not None
    if re.search(rf"\b(?:2|two){_BED}", text):
        return "shared2br" if shared else "2br"
    if re.search(rf"\b(?:1|one|single){_BED}", text):
        return "1br"
    if re.search(r"\b(?:studio|efficiency)\b", text):
        return "0br"
    return "shared2br" if shared else None


def _entities(text: str) -> list[tuple[str, str]]:
    """('dest', name) / ('interest', key) / ('rent', '') mentioned in a piece of text."""
    out = [("dest", d) for d in _find_dests(text)] + [("interest", i) for i in _find(INTEREST_WORDS, text)]
    if any(re.search(rf"\b{re.escape(w)}\b", text) for w in RENT_WORDS):
        out.append(("rent", ""))
    return out


class _Editor:
    """Works on a copy of the profile and records one reason per change key."""

    def __init__(self, profile: Profile) -> None:
        self.p = profile.model_copy(deep=True)
        self.reasons: dict[str, str] = {}

    def _dest(self, name: str) -> DestinationPref | None:
        return next((d for d in self.p.destinations if d.name == name), None)

    def _interest(self, cat: str) -> InterestPref | None:
        return next((i for i in self.p.interests if i.category == cat), None)

    def _default_mode(self) -> str:
        modes = {d.mode for d in self.p.destinations}
        return "drive" if modes == {"drive"} else "transit"

    def shift(self, kind: str, key: str, by: int, reason: str) -> None:
        if kind == "rent":
            self.p.rent_importance = _shift(self.p.rent_importance, by)
            self.reasons["rent_importance"] = reason
        elif kind == "dest":
            d = self._dest(key)
            if d is None:
                if by > 0:
                    self.p.destinations.append(DestinationPref(name=key, mode=self._default_mode(), importance="Important"))
                    self.reasons[f"dest:{key}"] = reason
            else:
                d.importance = _shift(d.importance, by)
                self.reasons[f"dest:{key}"] = reason
        else:
            i = self._interest(key)
            if i is None:
                if by > 0:
                    self.p.interests.append(InterestPref(category=key, importance="Important"))
                    self.reasons[f"interest:{key}"] = reason
            else:
                i.importance = _shift(i.importance, by)
                self.reasons[f"interest:{key}"] = reason

    def set_interest(self, cat: str, importance: str | None, reason: str) -> None:
        existing = self._interest(cat)
        if importance is None:
            if existing is not None:
                self.p.interests = [i for i in self.p.interests if i.category != cat]
                self.reasons[f"interest:{cat}"] = reason
        elif existing is None:
            self.p.interests.append(InterestPref(category=cat, importance=importance))
            self.reasons[f"interest:{cat}"] = reason
        elif _level(existing.importance) < _level(importance):
            existing.importance = importance
            self.reasons[f"interest:{cat}"] = reason


def keyword_refine(text: str, profile: Profile, draft: bool = False) -> Proposal:
    """Rule-based edits for the phrasings we promise to handle. Unknown text changes nothing.

    With draft=True (a first description, not an edit) every destination and interest the
    person mentions is added, even without "I like" or a time limit.
    """
    raw = text.strip()
    low = " " + re.sub(r"\s+", " ", raw.lower()) + " "
    low = low.replace("’", "'")
    ed = _Editor(profile)
    quoted = f'You said "{raw}"' if len(raw) <= 90 else "From what you wrote"
    refused = mentions_demographics(raw)
    not_used: list[str] = []
    recognized = bool(_entities(low))

    location = re.search(
        r"\b(?:work|job|office)\s+in\s+(.+?)(?=\s+(?:looking for|want|need|seeking|and|but|within|under|"
        r"maximum|max|at most|up to|no more than)\b|[,.;!?]|$)",
        raw,
        re.IGNORECASE,
    )
    if location:
        place = re.sub(r"\s+", " ", location.group(1)).strip()
        if place and not _find_dests(place.lower()):
            not_used.append(
                f"Commute to {place}: I can only measure trips to Rutgers College Ave, Rutgers Busch and Newark Penn Station."
            )

    # 1. "X matters more/less than Y"
    m = re.search(r"(.*?)\b(more|less)\b(?:\s+important)?\s+than\b(.*)", low)
    compared = False
    if m:
        left, direction, right = _entities(m.group(1)), m.group(2), _entities(m.group(3))
        if left and right:
            compared = True
            up, down = (left, right) if direction == "more" else (right, left)
            for kind, key in up:
                ed.shift(kind, key, +1, f"{quoted}, so this moves up one level")
            for kind, key in down:
                ed.shift(kind, key, -1, f"{quoted}, so this moves down one level")

    # 1b. "make X more/less important" (no "than"): move every named item one level
    p = None if compared else _PRIORITY.search(low)
    at_limit = ""
    if p:
        down = bool(p.group(4)) or "less" in (p.group(1), p.group(2))
        targets = _entities(low)
        # "transit"/"commute" with no place named means every trip ("transit": the trips made by transit)
        if not any(kind == "dest" for kind, _ in targets):
            if any(re.search(rf"\b{re.escape(w)}\b", low) for w in TRANSIT_WORDS):
                by_transit = [d.name for d in ed.p.destinations if d.mode == "transit"]
                targets += [("dest", n) for n in by_transit or [d.name for d in ed.p.destinations]]
            elif any(re.search(rf"\b{re.escape(w)}\b", low) for w in TRAVEL_WORDS):
                targets += [("dest", d.name) for d in ed.p.destinations]
        level = "down" if down else "up"
        for kind, key in targets:
            ed.shift(kind, key, -1 if down else +1, f"{quoted}, so this moves {level} one level")
        if targets:
            names = list(dict.fromkeys(
                "Room under budget" if kind == "rent" else INTERESTS.get(key, key) for kind, key in targets
            ))
            at_limit = AT_LIMIT.format(items=", ".join(names), end="lowest" if down else "highest")

    # 2. budget
    b = (
        re.search(r"\bbudget\b[^$\d]{0,20}\$?\s?(\d[\d,]{2,})", low)
        or re.search(r"\b(?:pay|spend|afford|stretch|go)\b[^$\d]{0,20}\bto\s+\$?\s?(\d[\d,]{2,})", low)
        or re.search(r"\$\s?(\d[\d,]{2,})", low)
    )
    if b:
        recognized = True
        amount = int(b.group(1).replace(",", ""))
        if BUDGET_RANGE[0] <= amount <= BUDGET_RANGE[1]:
            ed.p.budget = amount
            ed.reasons["budget"] = f"You gave ${amount:,} as what you can pay"

    # 2b. housing type: "a 1-bedroom", "studio", "2 bed with my partner", "sharing with a roommate"
    housing = _housing(low)
    if housing is not None:
        recognized = True
        if housing != ed.p.bedroom:
            ed.p.bedroom = housing
            ed.reasons["bedroom"] = f"You asked for: {BEDROOM_LABELS[housing].lower()}"

    # 3. car
    if re.search(r"\b(no car|don't have a car|do not have a car|without a car|carless|sold my car)\b", low):
        recognized = True
        for d in ed.p.destinations:
            if d.mode != "transit":
                d.mode = "transit"
                ed.reasons[f"dest:{d.name}:mode"] = "You have no car, so this trip is by transit"
    elif re.search(r"\b(have a car|got a car|i drive|with a car|by car|own a car|bought a car)\b", low):
        recognized = True
        for d in ed.p.destinations:
            if d.mode != "drive":
                d.mode = "drive"
                ed.reasons[f"dest:{d.name}:mode"] = "You have a car, so this trip is by car"

    # 4. "commute to PLACE under N min"
    t = re.search(
        r"\b(?:(?:under|within|less than|max|maximum|at most|up to|no more than)\s+)?"
        r"(\d{1,3})\s*(?:min|mins|minutes)\b",
        low,
    )
    if t:
        minutes = int(t.group(1))
        places = _find_dests(low)
        if places and MINUTES_RANGE[0] <= minutes <= MINUTES_RANGE[1]:
            recognized = True
            for name in places:
                d = ed._dest(name)
                if d is None:
                    ed.p.destinations.append(
                        DestinationPref(name=name, mode=ed._default_mode(), max_minutes=minutes, importance="Important")
                    )
                    ed.reasons[f"dest:{name}"] = f"You asked for {name} within {minutes} min"
                elif d.max_minutes != minutes:
                    d.max_minutes = minutes
                    ed.reasons[f"dest:{name}:max_minutes"] = f"You asked for {name} within {minutes} min"
        elif not places:
            not_used.append(f"{minutes}-minute limit: no supported destination to apply it to.")

    if re.search(r"\b(?:mile|miles|radius)\b", low):
        not_used.append("Distance radius: places are counted within 1.5 km (about 1 mile) of each area's centre only.")
    if re.search(r"\bhouse\b", low):
        not_used.append("House: I can match a shared room, studio, 1-bed or 2-bed. Pick one under Housing.")

    # 5. likes and dislikes (not when the sentence was a comparison, which rule 1 handled)
    dislike = None
    if not compared:
        dislike = re.search(r"\b(don't care about|do not care about|not interested in|don't need|no need for|hate|skip)\b(.*)", low)
        if dislike:
            for cat in _find(INTEREST_WORDS, dislike.group(2)):
                ed.set_interest(cat, None, "You said you don't care about this")
        like = re.search(r"\b(love|need|really like|must have)\b(.*)", low)
        if like and not dislike:
            for cat in _find(INTEREST_WORDS, like.group(2)):
                ed.set_interest(cat, "Important", "You said you love this")
        elif not dislike:
            like = re.search(r"\b(like|enjoy|into|want|walkable to|close to|near)\b(.*)", low)
            if like:
                for cat in _find(INTEREST_WORDS, like.group(2)):
                    ed.set_interest(cat, "Nice", "You said you like this")

    # 6. first description: include whatever places and interests were mentioned
    if draft:
        unwanted = _find(INTEREST_WORDS, dislike.group(2)) if (not compared and dislike) else []
        walkable = re.search(r"\bwalkable\b|\bwalking distance\b|\bwalk to\b", low) is not None
        for name in _find_dests(low):
            if ed._dest(name) is None:
                ed.p.destinations.append(DestinationPref(name=name, mode=ed._default_mode(), importance="Important"))
                ed.reasons[f"dest:{name}"] = f"You mentioned {name}"
        if re.search(r"\b(have a car|got a car|i drive|with a car|by car|own a car|bought a car)\b", low) and not re.search(
            r"\b(no car|don't have a car|do not have a car|without a car)\b", low
        ):
            for d in ed.p.destinations:
                d.mode = "drive"
        for cat in _find(INTEREST_WORDS, low):
            if cat not in unwanted and ed._interest(cat) is None:
                importance = "Important" if walkable else "Nice"
                ed.set_interest(cat, importance, "You mentioned this")

    changes = diff_profiles(profile, ed.p, ed.reasons)
    if not not_used and not recognized:
        not_used.append("Some of your description didn't match anything I can check.")
    message = ""
    if refused:
        message = DEMOGRAPHIC_REPLY
    elif not changes:
        message = at_limit or NOT_UNDERSTOOD
    return Proposal(profile=ed.p, changes=changes, source=KEYWORD, message=message, not_used=not_used)


def starter_profile() -> Profile:
    """A neutral starting point for someone who describes themselves on the landing page."""
    return Profile(budget=None, bedroom="shared2br", rent_importance="Important", destinations=[], interests=[])
