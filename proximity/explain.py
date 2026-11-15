"""Template explanations built only from computed facts.

This is the no-LLM path (and the fallback once an LLM is added in L3). Every
sentence here is assembled from numbers in an AreaResult or a diff. Nothing is
looked up or assumed about a place.
"""
from __future__ import annotations

import math

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
        if c.limit is None:
            return f"**{fmt_value(c)}/mo** rent estimate (information only)"
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


def _best_among(r: AreaResult, c: Component, matches: list[AreaResult]) -> bool:
    """True when no other eligible match beats this area on this priority (ties count as best)."""
    others = [m.component(c.key) for m in matches if m.area != r.area]
    values = [o.raw for o in others if o is not None and o.evaluated and o.raw is not None]
    if c.raw is None or not values:
        return False
    if c.kind == "interest":
        return c.raw >= max(values)
    return c.raw <= min(values)  # rent and commute: lower is better


def _compromise(r: AreaResult, matches: list[AreaResult] | None = None) -> Component | None:
    """The evaluated priority where this area stands worst against the others, by weight.

    With `matches` (the eligible areas), a priority on which this area is best or tied-best
    among them is never called its compromise: the card would contradict "What changed".
    """
    done = [c for c in r.components if c.evaluated]
    if matches:
        done = [c for c in done if not _best_among(r, c, matches)]
    if not done:
        return None
    worst = sorted(done, key=lambda c: (-(c.weight * (1 - c.relative)), c.key))[0]
    return worst if worst.weight * (1 - worst.relative) > 0 else None


def why_matched(r: AreaResult, limit: int = 2, matches: list[AreaResult] | None = None) -> list[str]:
    """The strongest met rent/commute priorities, then every selected interest this area has.

    An interest with a count of zero or no data did not match, so it is not listed here:
    the card shows it under "Not matched / not checked". The main compromise is never listed either.
    """
    skip = _compromise(r, matches)
    good = [c for c in r.components if c.kind != "interest" and c.evaluated and c.met and c is not skip]
    good.sort(key=lambda c: (-(c.weight * c.relative), -c.weight, c.key))
    lines = [_sentence(c) for c in good[:limit]]
    for c in r.components:
        if c.kind != "interest" or c is skip or not c.evaluated or not c.met:
            continue
        lines.append(_sentence(c))
    return lines


def main_compromise(r: AreaResult, matches: list[AreaResult] | None = None) -> str:
    """The priority where this area stands worst. `matches` are the eligible areas, for rent wording."""
    if not any(c.evaluated for c in r.components):
        return "Nothing could be evaluated for this area."
    worst = _compromise(r, matches)
    if worst is None:
        return "No compromise among the priorities we could evaluate."
    if worst.kind == "rent" and worst.met:
        top_matches = [m for m in (matches or []) if m.rank is not None and m.rank <= 3]
        rents = [m.component("rent").raw for m in top_matches if m.component("rent") and m.component("rent").raw is not None]
        is_most_expensive_top_match = (
            r.rank is not None and r.rank <= 3 and len(rents) > 1 and worst.raw >= max(rents)
        )
        ending = "it's the most expensive of your top matches" if is_most_expensive_top_match else "it's the main compromise"
        return (
            f"Rent: {fmt_value(worst)}/mo is within your ${worst.limit:,.0f} budget, "
            f"but {ending}."
        )
    text = _sentence(worst)
    if worst.met:
        # Only claim "most areas do better" when that is true of the numbers.
        text += ". Most other areas do better on this" if worst.relative < 0.5 else ". This is its weakest point"
    return text


def fit_or_dash(r: AreaResult) -> str:
    """Ineligible areas have no Fit to show."""
    return "Fit —" if r.status == INELIGIBLE else fit(r.score)


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


def fit(score: float) -> str:
    """The fit score as shown everywhere: a whole number out of 100."""
    return f"Fit {score * 100:.0f}"


def fit_scope(n_areas: int) -> str:
    return f"relative to the {n_areas} areas"


def _supports(driver: dict, went_up: bool) -> bool:
    """Only quote the standing when it actually explains the direction of the move."""
    s = driver.get("standing")
    if not s or driver["raw"] is None or s["of"] < 2:
        return False
    top_half = s["position"] <= s["of"] / 2
    return top_half if went_up else not top_half


def what_changed(diff: dict, limit: int = 4) -> list[str]:
    """Notes for a re-rank, each tied to a number. Always returns at least one line."""
    changes = diff["profile_changes"]
    if not changes:
        return ["Your profile changed, but nothing that affects the ranking."]
    because = " and ".join(c["text"] for c in changes[:2])
    if len(changes) > 2:
        because += f" (and {len(changes) - 2} more change{'s' if len(changes) > 3 else ''})"

    moves = list(diff["moves"])
    dropped = [m["area"] for m in moves if m["new_rank"] is None and m["old_rank"] is not None]
    # Areas that dropped out come first: they usually explain the other moves.
    moves.sort(key=lambda m: (m["new_rank"] is not None, m["new_rank"] or 0, m["area"]))

    if not moves:
        note = f"The order stayed the same after {because}."
        leader = diff.get("leader")
        if leader and leader in diff.get("scores", {}):
            note += f" **{leader}** is still #1."
        return [note]

    notes: list[str] = []
    for m in moves[:limit]:
        area, old, new = m["area"], m["old_rank"], m["new_rank"]
        if new is None and old is None:
            continue
        if new is None:
            reason = m["reasons"][0] if m["reasons"] else "it no longer meets a must-have"
            state = "is now unverified" if m["new_status"] == UNVERIFIED else "is no longer eligible"
            notes.append(f"**{area}** (was #{old}) {state} because {because}: {reason}.")
            continue
        if old is None:
            notes.append(f"**{area}** is now a match at #{new} because {because}.")
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
    others = [m["area"] for m in moves[limit:]]
    if others:
        names = others[0] if len(others) == 1 else ", ".join(others[:-1]) + " and " + others[-1]
        notes.append(f"{names} also changed position.")
    return notes or [f"The order stayed the same after {because}."]


def compare_rows(matches: list[AreaResult]) -> list[dict]:
    """Side-by-side rows for the given areas: one row per priority, same units in every column.

    Each row is {"": row label, <area name>: value, ...}. A value that was not
    checked reads "Not checked / no data"; it is never left blank or guessed.
    """
    if not matches:
        return []
    rows: list[dict] = [{"": "Rank"} | {r.area: f"#{r.rank}" if r.rank else "Not ranked" for r in matches}]
    by_area = {r.area: {c.key: c for c in r.components} for r in matches}
    seen: list[tuple[str, str, str]] = []  # (key, label, kind) in the first area's order, then any extras
    for r in matches:
        for c in r.components:
            if all(c.key != k for k, _, _ in seen):
                seen.append((c.key, c.label, c.kind))
    for key, label, kind in seen:
        row = {"": "Rent estimate ($/mo)" if kind == "rent" else label}
        for r in matches:
            c = by_area[r.area].get(key)
            row[r.area] = (c.flag or fmt_value(c)) if c is not None else "Not checked / no data"
        rows.append(row)
    rows.append({"": "Priorities met"} | {r.area: f"{r.met} of {r.evaluated} evaluated" for r in matches})
    rows.append({"": "Missing / not checked"} | {r.area: "; ".join(r.missing + r.unchecked) or "Nothing" for r in matches})
    return rows


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
            lines.append(f"**Try relaxing:** allow {math.ceil(s['needed'])} min for {s['label'][0].lower() + s['label'][1:]}. That makes {s['area']} eligible.")
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
