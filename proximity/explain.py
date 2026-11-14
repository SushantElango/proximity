"""Template explanations built only from computed facts.

This is the no-LLM path (and the fallback once an LLM is added in L3). Every
sentence here is assembled from numbers in an AreaResult or a diff. Nothing is
looked up or assumed about a place.
"""
from __future__ import annotations

from .scoring import INELIGIBLE, UNVERIFIED, AreaResult, Component


def fmt_value(c: Component) -> str:
    if c.raw is None:
        return "Not checked / no data"
    if c.unit == "$":
        return f"${c.raw:,.0f}"
    if c.unit == "min":
        return f"{c.raw:.0f} min"
    return f"{c.raw:.0f}"


def _sentence(c: Component) -> str:
    """One short line for a priority, leading with its number (markdown bold)."""
    if c.kind == "rent":
        gap = c.limit - c.raw
        side = f"${gap:,.0f} under budget" if gap >= 0 else f"${-gap:,.0f} over budget"
        return f"**{fmt_value(c)}/mo** rent, {side}"
    if c.kind == "commute":
        mode, dest = c.label.split(" to ", 1)
        by = "by transit" if mode == "Transit" else "by car"
        side = "limit" if c.raw <= c.limit else "over your limit of"
        return f"**{fmt_value(c)}** {by} to {dest} ({side} {c.limit:.0f})"
    n = int(c.raw)
    return f"**{n if n else 'No'}** {c.label.lower()} in this area"


def _compromise(r: AreaResult) -> Component | None:
    """The evaluated priority where this area stands worst against the others, by weight."""
    done = [c for c in r.components if c.evaluated]
    if not done:
        return None
    worst = sorted(done, key=lambda c: (-(c.weight * (1 - c.relative)), c.key))[0]
    return worst if worst.weight * (1 - worst.relative) > 0 else None


def why_matched(r: AreaResult, limit: int = 3) -> list[str]:
    """Up to 3 bullets: the met priorities that added most to the score."""
    skip = _compromise(r)
    good = [c for c in r.components if c.evaluated and c.met and c is not skip]
    good.sort(key=lambda c: (-(c.weight * c.relative), -c.weight, c.key))
    return [_sentence(c) for c in good[:limit]]


def main_compromise(r: AreaResult) -> str:
    if not any(c.evaluated for c in r.components):
        return "Nothing could be evaluated for this area."
    worst = _compromise(r)
    if worst is None:
        return "No compromise among the priorities we could evaluate."
    text = _sentence(worst)
    if worst.met:
        text += ". Most other areas do better on this"
    return text


def headline(r: AreaResult) -> str:
    return f"Meets {r.met} of {r.evaluated} evaluated priorities"


def coverage(r: AreaResult) -> str:
    return f"Evaluated {r.evaluated} of {r.total} priorities"


def _standing_text(driver: dict) -> str:
    s = driver["standing"]
    if not s or driver["raw"] is None:
        return ""
    kind, raw = driver["kind"], driver["raw"]
    if kind == "commute":
        what, best, worst = f"its {driver['label'].lower()} ({raw:.0f} min)", "shortest", "longest"
        what = what.replace("to rutgers", "to Rutgers").replace("to newark penn station", "to Newark Penn Station").replace("busch", "Busch").replace("college ave", "College Ave")
    elif kind == "rent":
        what, best, worst = f"its rent estimate (${raw:,.0f})", "lowest", "highest"
    else:
        what, best, worst = f"its count of {driver['label'].lower()} ({raw:.0f})", "highest", "lowest"
    n = s["of"]
    if n < 2:
        return f"{what} is the only one we have among the eligible areas"
    tie = "tied for " if s["tied"] else ""
    if s["position"] == 1:
        return f"{what} is {tie}the {best} of the {n} eligible areas with data"
    if s["position"] + s["tied"] == n:
        return f"{what} is {tie}the {worst} of the {n} eligible areas with data"
    return f"{what} ranks {s['position']} of {n} eligible areas with data"


def _supports(driver: dict, went_up: bool) -> bool:
    """Only quote the standing when it actually explains the direction of the move."""
    s = driver.get("standing")
    if not s or driver["raw"] is None or s["of"] < 2:
        return False
    top_half = s["position"] <= s["of"] / 2
    return top_half if went_up else not top_half


def what_changed(diff: dict, limit: int = 3) -> list[str]:
    """Short notes for a re-rank, each tied to a number."""
    changes = diff["profile_changes"]
    if not changes:
        return []
    because = " and ".join(c["text"] for c in changes[:2])
    interesting = set(diff["top_before"]) | set(diff["top_after"])
    moves = [m for m in diff["moves"] if m["area"] in interesting]
    dropped = [m["area"] for m in moves if m["new_rank"] is None]
    # Areas that dropped out come first: they usually explain the other moves.
    moves.sort(key=lambda m: (m["new_rank"] is not None, m["new_rank"] or 0, m["area"]))

    notes: list[str] = []
    for m in moves[:limit]:
        area, old, new = m["area"], m["old_rank"], m["new_rank"]
        if new is None:
            reason = m["reasons"][0] if m["reasons"] else "it no longer meets a must-have"
            notes.append(f"**{area}** (was #{old}) is no longer eligible because {because}: {reason}.")
            continue
        if old is None:
            notes.append(f"**{area}** is now eligible at #{new} because {because}.")
            continue
        went_up = new < old
        text = f"**{area}** moved {'up' if went_up else 'down'} from #{old} to #{new}"
        if m["driver"] and _supports(m["driver"], went_up):
            text += f" because {because}; {_standing_text(m['driver'])}"
        elif went_up and dropped:
            text += f" because {' and '.join(dropped)} dropped out"
        else:
            text += f" because {because}"
        if m["new_status"] == UNVERIFIED and m["old_status"] != UNVERIFIED:
            text += f". It is now unverified ({m['reasons'][0]})"
        notes.append(text + ".")
    if not notes:
        notes.append(f"The top 3 stayed the same after {because}. Scores shifted, but not enough to change the order.")
    return notes


def no_eligible_message(info: dict) -> list[str]:
    """Which constraints conflict and which one to relax."""
    lines = ["No area meets all of your must-haves with verified data."]
    for c in info["conflicts"]:
        if c["kind"] == "rent":
            lines.append(f"- Your ${c['limit']:,.0f} budget rules out {c['areas_ruled_out']} area(s).")
        elif c["kind"] == "commute":
            lines.append(f"- {c['label']} within {c['limit']:.0f} min rules out {c['areas_ruled_out']} area(s).")
        else:
            lines.append(f"- {c['label']} as a must-have rules out {c['areas_ruled_out']} area(s).")
    s = info["suggestion"]
    if s:
        if s["kind"] == "rent":
            lines.append(f"**Try relaxing:** raise your budget to ${s['needed']:,.0f}. That makes {s['area']} eligible.")
        elif s["kind"] == "commute":
            lines.append(f"**Try relaxing:** allow {s['needed']:.0f} min for {s['label'].lower()}. That makes {s['area']} eligible.")
        else:
            lines.append(f"**Try relaxing:** change {s['label']} from Must-have to Important. That makes {s['area']} eligible.")
    elif info["conflicts"]:
        lines.append("**Try relaxing:** these limits conflict with each other. No single change makes an area eligible, so loosen the one that matters least to you.")
    if info["unverified"]:
        lines.append(f"Shown below as unverified (a must-have has no data): {', '.join(info['unverified'])}.")
    return lines


def status_note(r: AreaResult) -> str:
    if r.status == UNVERIFIED:
        return "Unverified: " + "; ".join(r.reasons)
    if r.status == INELIGIBLE:
        return "Not eligible: " + "; ".join(r.reasons)
    return ""
