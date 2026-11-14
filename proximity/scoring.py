"""Deterministic matching: eligibility, scores, ranking and before/after diffs.

No AI and no randomness in this file. Same inputs always give the same output.

How missing data is handled (CLAUDE.md rule 2):
- A missing value is never a match and contributes 0 to the ranking score, so
  losing data can never raise an area's score.
- `score_evaluated` is the section-6 formula over evaluated preferences only.
  It is reported next to coverage ("evaluated 5 of 6") but is not used to rank.
- Missing data on a must-have makes the area "Unverified": kept, flagged and
  ranked after every verified area.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import BEDROOM_LABELS, INTERESTS, LATE_NIGHT_NOTE, NEWARK, WEIGHTS, AreaFacts, Fact, Profile

ELIGIBLE = "Eligible"
UNVERIFIED = "Unverified"
INELIGIBLE = "Ineligible"
_STATUS_ORDER = {ELIGIBLE: 0, UNVERIFIED: 1, INELIGIBLE: 2}


@dataclass
class Component:
    key: str  # "rent", "commute:<destination>", "interest:<category>"
    kind: str  # rent | commute | interest
    label: str
    importance: str
    weight: int
    raw: Optional[float] = None  # the real value, or None when not checked
    unit: str = ""
    limit: Optional[float] = None  # the user's limit, when there is one
    score: Optional[float] = None  # 0..1, None when not evaluated
    met: Optional[bool] = None  # None when not evaluated
    # 0..1 standing against the other areas (1 = best). Used to pick the
    # "main compromise" only, never to rank.
    relative: Optional[float] = None
    fact: Optional[Fact] = None
    flag: str = ""  # why a reported value was set aside (shown in place of 'no data')

    @property
    def evaluated(self) -> bool:
        return self.score is not None


@dataclass
class AreaResult:
    area: str
    zip: str
    status: str = ELIGIBLE
    reasons: list[str] = field(default_factory=list)  # why Unverified / Ineligible
    components: list[Component] = field(default_factory=list)
    score: float = 0.0  # ranking score, missing counts as 0
    score_evaluated: Optional[float] = None  # over evaluated preferences only
    evaluated: int = 0
    total: int = 0
    met: int = 0
    missing: list[str] = field(default_factory=list)
    # Things nobody has looked up at all. Shown with the missing list; not part of the Fit.
    unchecked: list[str] = field(default_factory=list)
    rank: Optional[int] = None  # 1..N for eligible areas, None for unverified and ineligible
    rent_basis: str = ""  # "HUD" or "Zillow"
    rent_other: Optional[Fact] = None  # the second rent source, shown alongside

    def component(self, key: str) -> Optional[Component]:
        for c in self.components:
            if c.key == key:
                return c
        return None


def choose_rent(area: AreaFacts, bedroom: str) -> tuple[Optional[Fact], str, Optional[Fact]]:
    """HUD is primary. Zillow is used only when HUD is missing. Returns (fact, basis, other)."""
    hud = area.rent_hud.get(bedroom)
    if hud is not None:
        return hud, "HUD", area.rent_zillow
    if area.rent_zillow is not None:
        return area.rent_zillow, "Zillow", None
    return None, "", None


def _minmax(value: float, values: list[float], lower_is_better: bool) -> float:
    lo, hi = min(values), max(values)
    if hi == lo:
        return 1.0
    s = (value - lo) / (hi - lo)
    return 1.0 - s if lower_is_better else s


def _build_components(area: AreaFacts, areas: list[AreaFacts], profile: Profile) -> tuple[list[Component], str, Optional[Fact]]:
    comps: list[Component] = []

    # Rent headroom: (budget - rent) / budget, clipped to 0..1
    fact, basis, other = choose_rent(area, profile.bedroom)
    rent = Component(
        key="rent",
        kind="rent",
        label=f"Rent for a {BEDROOM_LABELS[profile.bedroom].lower()}",
        importance=profile.rent_importance,
        weight=WEIGHTS[profile.rent_importance],
        unit="$",
        limit=float(profile.budget),
        fact=fact,
    )
    if fact is not None:
        rent.raw = fact.value
        rent.score = min(1.0, max(0.0, (profile.budget - fact.value) / profile.budget))
        rent.met = fact.value <= profile.budget
        rents = [f.value for f in (choose_rent(a, profile.bedroom)[0] for a in areas) if f is not None]
        rent.relative = _minmax(fact.value, rents, lower_is_better=True)
    comps.append(rent)

    # Commute: lower is better, min-max across all areas that have the value
    for d in profile.destinations:
        key = (d.name, d.mode)
        c = Component(
            key=f"commute:{d.name}",
            kind="commute",
            label=f"{'Transit' if d.mode == 'transit' else 'Drive'} to {d.name}",
            importance=d.importance,
            weight=WEIGHTS[d.importance],
            unit="min",
            limit=float(d.max_minutes),
            fact=area.commute.get(key),
            flag=area.commute_flags.get(key, ""),
        )
        if c.fact is not None:
            values = [a.commute[key].value for a in areas if key in a.commute]
            c.raw = c.fact.value
            c.score = _minmax(c.raw, values, lower_is_better=True)
            c.met = c.raw <= d.max_minutes
            c.relative = c.score
        comps.append(c)

    # Interests: count of matching places in the area, min-max across areas
    for i in profile.interests:
        c = Component(
            key=f"interest:{i.category}",
            kind="interest",
            label=INTERESTS.get(i.category, i.category),
            importance=i.importance,
            weight=WEIGHTS[i.importance],
            unit="places",
            fact=area.places.get(i.category),
        )
        if c.fact is not None:
            values = [a.places[i.category].value for a in areas if i.category in a.places]
            c.raw = c.fact.value
            if max(values) == min(values):
                c.score = 1.0 if c.raw > 0 else 0.0
            else:
                c.score = _minmax(c.raw, values, lower_is_better=False)
            c.met = c.raw >= 1
            c.relative = c.score
        comps.append(c)

    return comps, basis, other


def _fmt(c: Component) -> str:
    if c.raw is None:
        return "no data"
    if c.unit == "$":
        return f"${c.raw:,.0f}"
    return f"{c.raw:.0f} {c.unit}"


def check_eligibility(components: list[Component]) -> tuple[str, list[str]]:
    """Must-haves decide eligibility. Budget is always a must-have."""
    failed: list[str] = []
    unknown: list[str] = []
    for c in components:
        hard = c.kind == "rent" or c.importance == "Must-have"
        if not hard:
            continue
        if c.raw is None:
            unknown.append(f"{c.label}: {c.flag or 'not checked / no data'}")
        elif c.kind == "rent" and c.raw > c.limit:
            failed.append(f"{c.label} is {_fmt(c)}, over your ${c.limit:,.0f} budget")
        elif c.kind == "commute" and c.raw > c.limit:
            failed.append(f"{c.label} is {_fmt(c)}, over your {c.limit:.0f} min limit")
        elif c.kind == "interest" and c.raw < 1:
            failed.append(f"{c.label}: none found in this area")
    if failed:
        return INELIGIBLE, failed + unknown
    if unknown:
        return UNVERIFIED, unknown
    return ELIGIBLE, []


def score_areas(areas: list[AreaFacts], profile: Profile) -> list[AreaResult]:
    """Score every area and return them ranked: eligible, then unverified, then ineligible."""
    results: list[AreaResult] = []
    for area in areas:
        comps, basis, other = _build_components(area, areas, profile)
        status, reasons = check_eligibility(comps)
        done = [c for c in comps if c.evaluated]
        total_w = sum(c.weight for c in comps)
        eval_w = sum(c.weight for c in done)
        weighted = sum(c.weight * c.score for c in done)
        results.append(
            AreaResult(
                area=area.area,
                zip=area.zip,
                status=status,
                reasons=reasons,
                components=comps,
                score=weighted / total_w if total_w else 0.0,
                score_evaluated=weighted / eval_w if eval_w else None,
                evaluated=len(done),
                total=len(comps),
                met=sum(1 for c in done if c.met),
                missing=[f"{c.label}: {c.flag}" if c.flag else c.label for c in comps if not c.evaluated],
                unchecked=[LATE_NIGHT_NOTE] if any(d.name == NEWARK for d in profile.destinations) else [],
                rent_basis=basis,
                rent_other=other,
            )
        )
    return rank(results)


def rank(results: list[AreaResult]) -> list[AreaResult]:
    """Eligible areas first (ranked 1..N), then unverified, then ineligible. Ties break by name."""
    ordered = sorted(results, key=lambda r: (_STATUS_ORDER[r.status], -round(r.score, 9), r.area))
    n = 0
    for r in ordered:
        # Only verified, eligible areas are matches and get a rank. Unverified
        # areas are listed after them, unranked, until their data is checked.
        if r.status == ELIGIBLE:
            n += 1
            r.rank = n
        else:
            r.rank = None
    return ordered


def top_matches(results: list[AreaResult], n: int = 3) -> list[AreaResult]:
    return [r for r in results if r.rank is not None][:n]


def no_eligible_info(results: list[AreaResult]) -> Optional[dict]:
    """When no area is verified eligible: which constraints conflict, and what to relax.

    Returns None when at least one area is eligible.
    """
    if any(r.status == ELIGIBLE for r in results):
        return None

    blocked_by: dict[str, list[tuple[str, Component]]] = {}
    sole_blocker: dict[str, list[tuple[str, Component]]] = {}
    for r in results:
        fails = []
        for c in r.components:
            hard = c.kind == "rent" or c.importance == "Must-have"
            if not hard or c.raw is None:
                continue
            over = (c.kind in ("rent", "commute") and c.raw > c.limit) or (c.kind == "interest" and c.raw < 1)
            if over:
                fails.append(c)
        for c in fails:
            blocked_by.setdefault(c.key, []).append((r.area, c))
        if len(fails) == 1 and r.status == INELIGIBLE and not any("no data" in x for x in r.reasons):
            sole_blocker.setdefault(fails[0].key, []).append((r.area, fails[0]))

    conflicts = [
        {"key": k, "label": v[0][1].label, "kind": v[0][1].kind, "limit": v[0][1].limit, "areas_ruled_out": len(v)}
        for k, v in sorted(blocked_by.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    suggestion = None
    if sole_blocker:
        # Relax the constraint that alone unlocks the most areas; ask for the smallest change.
        key, hits = sorted(sole_blocker.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
        comp = hits[0][1]
        if comp.kind in ("rent", "commute"):
            area, c = min(hits, key=lambda h: (h[1].raw, h[0]))
            suggestion = {"key": key, "label": c.label, "kind": c.kind, "limit": c.limit, "needed": c.raw, "area": area, "unlocks": len(hits)}
        else:
            suggestion = {"key": key, "label": comp.label, "kind": comp.kind, "limit": None, "needed": None, "area": hits[0][0], "unlocks": len(hits)}

    return {
        "conflicts": conflicts,
        "suggestion": suggestion,
        "unverified": [r.area for r in results if r.status == UNVERIFIED],
    }


# --------------------------------------------------------------------------
# Re-rank diff
# --------------------------------------------------------------------------

def profile_changes(before: Profile, after: Profile) -> list[dict]:
    """What the user changed, as structured items: {key, text, direction}."""
    out: list[dict] = []
    lv = WEIGHTS

    def importance_change(key: str, label: str, a: str, b: str) -> None:
        if a != b:
            verb = "raised" if lv[b] > lv[a] else "lowered"
            out.append({"key": key, "text": f"you {verb} the importance of {label} ({a} → {b})"})

    if before.budget != after.budget:
        verb = "raised" if after.budget > before.budget else "lowered"
        out.append({"key": "rent", "text": f"you {verb} your budget (${before.budget:,} → ${after.budget:,})"})
    if before.bedroom != after.bedroom:
        out.append({"key": "rent", "text": f"you changed the bedroom type ({BEDROOM_LABELS[before.bedroom]} → {BEDROOM_LABELS[after.bedroom]})"})
    importance_change("rent", "room under budget", before.rent_importance, after.rent_importance)

    b_d = {d.name: d for d in before.destinations}
    a_d = {d.name: d for d in after.destinations}
    for name in a_d:
        key = f"commute:{name}"
        if name not in b_d:
            out.append({"key": key, "text": f"you added {name} as a destination"})
            continue
        b, a = b_d[name], a_d[name]
        importance_change(key, f"the commute to {name}", b.importance, a.importance)
        if b.max_minutes != a.max_minutes:
            out.append({"key": key, "text": f"you changed the max commute to {name} ({b.max_minutes} → {a.max_minutes} min)"})
        if b.mode != a.mode:
            out.append({"key": key, "text": f"you switched travel to {name} from {b.mode} to {a.mode}"})
    for name in b_d:
        if name not in a_d:
            out.append({"key": f"commute:{name}", "text": f"you removed {name} as a destination"})

    b_i = {i.category: i for i in before.interests}
    a_i = {i.category: i for i in after.interests}
    for cat in a_i:
        key, label = f"interest:{cat}", INTERESTS.get(cat, cat).lower()
        if cat not in b_i:
            out.append({"key": key, "text": f"you added {label} as an interest"})
        else:
            importance_change(key, label, b_i[cat].importance, a_i[cat].importance)
    for cat in b_i:
        if cat not in a_i:
            out.append({"key": f"interest:{cat}", "text": f"you removed {INTERESTS.get(cat, cat).lower()} as an interest"})
    return out


def _contributions(r: AreaResult) -> dict[str, float]:
    total_w = sum(c.weight for c in r.components) or 1
    return {c.key: (c.weight * c.score / total_w if c.evaluated else 0.0) for c in r.components}


def _standing(key: str, area: str, ranked: list[AreaResult]) -> Optional[dict]:
    """Where this area's real value sits among the ranked (not ineligible) areas."""
    rows = [(r.area, r.component(key)) for r in ranked]
    rows = [(a, c) for a, c in rows if c is not None and c.raw is not None]
    mine = next((c for a, c in rows if a == area), None)
    if mine is None:
        return None
    lower_better = mine.kind in ("rent", "commute")
    better = sum(1 for _, c in rows if (c.raw < mine.raw if lower_better else c.raw > mine.raw))
    tied = sum(1 for _, c in rows if c.raw == mine.raw) - 1
    return {"position": better + 1, "of": len(rows), "tied": tied, "lower_is_better": lower_better}


def diff_rankings(before: list[AreaResult], after: list[AreaResult], profile_before: Profile, profile_after: Profile) -> dict:
    """Explain a re-rank from the difference in score components before and after."""
    changes = profile_changes(profile_before, profile_after)
    changed_keys = [c["key"] for c in changes]
    old = {r.area: r for r in before}
    ranked_after = [r for r in after if r.rank is not None]

    moves: list[dict] = []
    for r in after:
        o = old.get(r.area)
        if o is None or (o.rank == r.rank and o.status == r.status):
            continue
        move = {
            "area": r.area,
            "old_rank": o.rank,
            "new_rank": r.rank,
            "old_status": o.status,
            "new_status": r.status,
            "reasons": r.reasons,
            "old_score": o.score,
            "new_score": r.score,
            "driver": None,
        }
        if r.rank is not None and o.rank is not None:
            went_up = r.rank < o.rank
            before_c, after_c = _contributions(o), _contributions(r)
            deltas = {k: after_c.get(k, 0.0) - before_c.get(k, 0.0) for k in set(before_c) | set(after_c)}
            # Prefer the preference the user actually touched; fall back to the biggest swing.
            pool = {k: v for k, v in deltas.items() if k in changed_keys and k in after_c} or {k: v for k, v in deltas.items() if k in after_c}
            if pool:
                key = sorted(pool.items(), key=lambda kv: (-kv[1] if went_up else kv[1], kv[0]))[0][0]
                comp = r.component(key)
                move["driver"] = {
                    "key": key,
                    "label": comp.label,
                    "kind": comp.kind,
                    "raw": comp.raw,
                    "unit": comp.unit,
                    "delta": deltas[key],
                    "standing": _standing(key, r.area, ranked_after),
                }
        moves.append(move)

    moves.sort(key=lambda m: (m["new_rank"] is None, m["new_rank"] or 99, m["area"]))
    return {
        "profile_changes": changes,
        "moves": moves,
        "scores": {r.area: (old[r.area].score, r.score) for r in after if r.area in old},
        "leader": next((r.area for r in after if r.rank == 1), None),
        "top_before": [r.area for r in top_matches(before)],
        "top_after": [r.area for r in top_matches(after)],
    }
