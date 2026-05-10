"""Brain-view projection compiler — emits the structured payload that
Atelier's `Brain.tsx` connectome consumes.

Produces one canonical JSON combining:
  - 8 anatomical regions (density, color_temp, last_fired_ts, attribution)
  - 22 directed fibers across 7 pathway kinds (input / output / monitor /
    reafference / consolidation / gating / bridge). Fibers tagged
    `backing_data: "real" | "synthetic"` so the UI can render honestly.
  - Up to N hypothesis pills, each with `source_inference_key`,
    `firing_pattern`, `verdict`, `top_evidence`, `sufficient_data`.
  - 30-day (configurable) timeline of region firings per day.

See omnigraph↔atelier Turn 14 for the design rationale.
"""
from __future__ import annotations
import datetime as _dt
import json
import re
from typing import Iterable

from . import register
from .base import ProjectionCompiler, VaultState


# ----------------------------------------------------------------------
# Region topology
# ----------------------------------------------------------------------

REGION_ORDER = (
    "prefrontal",
    "motor",
    "sensory",
    "anterior_cingulate",
    "hippocampus",
    "amygdala",
    "brainstem",
    "corpus_callosum",
)

REGION_ATTRIBUTION = {
    "prefrontal": "shared",
    "motor": "ai",
    "sensory": "shared",
    "anterior_cingulate": "shared",
    "hippocampus": "founder",
    "amygdala": "founder",
    "brainstem": "ai",
    "corpus_callosum": "shared",
}


# Pair → fiber kind (22 directed edges, 7 kinds) — matches mockup topology.
# `backing_data` declares whether the edge is driven by real aggregates
# or is a UI metaphor rendered at constant weight.
FIBERS: list[dict] = [
    # bridges — corpus callosum cross-region hub
    {"from": "corpus_callosum", "to": "prefrontal",        "kind": "bridge",        "backing": "real"},
    {"from": "corpus_callosum", "to": "motor",              "kind": "bridge",        "backing": "real"},
    {"from": "corpus_callosum", "to": "sensory",            "kind": "bridge",        "backing": "real"},
    {"from": "corpus_callosum", "to": "hippocampus",        "kind": "bridge",        "backing": "real"},
    {"from": "corpus_callosum", "to": "amygdala",           "kind": "bridge",        "backing": "real"},
    {"from": "corpus_callosum", "to": "anterior_cingulate", "kind": "bridge",        "backing": "real"},
    # inputs
    {"from": "sensory",         "to": "prefrontal",         "kind": "input",         "backing": "real"},
    {"from": "sensory",         "to": "hippocampus",        "kind": "input",         "backing": "real"},
    {"from": "sensory",         "to": "amygdala",           "kind": "input",         "backing": "real"},
    # consolidation (memory replay)
    {"from": "hippocampus",     "to": "prefrontal",         "kind": "consolidation", "backing": "real"},
    {"from": "hippocampus",     "to": "motor",              "kind": "consolidation", "backing": "real"},
    # gating (affect → planning)
    {"from": "amygdala",        "to": "prefrontal",         "kind": "gating",        "backing": "real"},
    {"from": "amygdala",        "to": "anterior_cingulate", "kind": "gating",        "backing": "real"},
    # monitor (into ACC)
    {"from": "prefrontal",      "to": "anterior_cingulate", "kind": "monitor",       "backing": "synthetic"},
    {"from": "motor",           "to": "anterior_cingulate", "kind": "monitor",       "backing": "synthetic"},
    {"from": "brainstem",       "to": "anterior_cingulate", "kind": "monitor",       "backing": "synthetic"},
    # outputs (out of motor)
    {"from": "motor",           "to": "brainstem",          "kind": "output",        "backing": "synthetic"},
    {"from": "motor",           "to": "corpus_callosum",    "kind": "output",        "backing": "synthetic"},
    # reafference (motor→sensory loop)
    {"from": "motor",           "to": "sensory",            "kind": "reafference",   "backing": "synthetic"},
    # additional inputs to ACC + prefrontal
    {"from": "sensory",         "to": "anterior_cingulate", "kind": "input",         "backing": "real"},
    {"from": "brainstem",       "to": "prefrontal",         "kind": "input",         "backing": "real"},
    {"from": "brainstem",       "to": "sensory",            "kind": "input",         "backing": "real"},
]


# ----------------------------------------------------------------------
# Time window helpers
# ----------------------------------------------------------------------

def _parse_ts(s: str) -> float | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _now_iso() -> str:
    try:
        return _dt.datetime.now(_dt.UTC).replace(tzinfo=None).isoformat() + "Z"
    except AttributeError:  # pragma: no cover
        return _dt.datetime.utcnow().isoformat() + "Z"


# ----------------------------------------------------------------------
# Region signal extraction from global_profile
# ----------------------------------------------------------------------

def _region_signals(gp: dict, window_days: int) -> dict[str, dict]:
    """Compute per-region {density, color_temp, last_fired_ts} from gp.

    Mapping:
      prefrontal        ← Decisions, Stances
      motor             ← artifacts + mention_types(decision_made, shipped, deployed)
      sensory           ← mention_type "first_introduction" events
      anterior_cingulate← concern_lifecycle entries (latent + resolved)
      hippocampus       ← idea_resurrection + rules_collected
      amygdala          ← affect_events (valence distribution)
      brainstem         ← placeholder (ToolCall aggregate not yet extracted)
      corpus_callosum   ← cross_provider_bleed multi-project entities
    """
    out = {r: {"count": 0, "valence_sum": 0.0, "last_ts": ""} for r in REGION_ORDER}

    decisions = gp.get("inference_p3_decision_load_bearing") or []
    out["prefrontal"]["count"] += sum(int(d.get("sessions_referenced") or 0) for d in decisions)

    concern_life = gp.get("inference_p5_concern_lifecycle") or []
    out["anterior_cingulate"]["count"] += sum(
        int(c.get("raised_count") or 0) + int(c.get("resolved_count") or 0)
        for c in concern_life
    )

    resurrection = gp.get("inference_idea_resurrection") or []
    out["hippocampus"]["count"] += len(resurrection) * 3
    rules = gp.get("rules_collected") or []
    out["hippocampus"]["count"] += len(rules)

    affect_events = gp.get("affect_events") or []
    out["amygdala"]["count"] += len(affect_events)
    # valence mapping — rough: positive/confident = +1, frustrated/negative = -1
    for a in affect_events:
        v = (a.get("valence") or "").lower()
        if v in ("positive", "confident", "excited"):
            out["amygdala"]["valence_sum"] += 1
        elif v in ("frustrated", "negative", "concerned", "urgent"):
            out["amygdala"]["valence_sum"] -= 1

    bleed = gp.get("inference_p6_cross_provider_bleed") or []
    out["corpus_callosum"]["count"] += sum(int(b.get("provider_count") or 0) for b in bleed)

    # Sensory: approximate via entity_frequency's first-seen density (top items)
    top_ents = gp.get("entity_frequency_top30") or []
    out["sensory"]["count"] += sum(int(e.get("events") or 0) for e in top_ents[:10])

    # Motor + Brainstem placeholders (no direct extraction yet). Use
    # entity_frequency providers crossings as weak proxy for motor and
    # mention_events providers distribution for brainstem. When the
    # dedicated aggregators land, swap in.
    out["motor"]["count"] += max(len(top_ents) // 3, 0)
    out["brainstem"]["count"] += len(gp.get("drift_recurrence_by_trigger") or [])

    # Normalize density globally (min/max over region counts)
    counts = [out[r]["count"] for r in REGION_ORDER]
    hi = max(counts) if counts else 0
    hi = hi or 1
    regions = []
    # Last-fired: use latest timestamp seen across events for each region approximated
    # as latest ts in any relevant inference list; fallback to now.
    now_iso = _now_iso()
    for r in REGION_ORDER:
        density = round(out[r]["count"] / hi, 3)
        temp = 0.0
        if r == "amygdala":
            n = max(len(affect_events), 1)
            temp = max(-1.0, min(1.0, out[r]["valence_sum"] / n))
        regions.append({
            "id": r,
            "density": density,
            "color_temp": round(temp, 3),
            "last_fired_ts": now_iso,  # placeholder; v0.4.1 will derive per-region
            "attribution": REGION_ATTRIBUTION[r],
        })
    return {r["id"]: r for r in regions}


# ----------------------------------------------------------------------
# Fiber weight/active derivation
# ----------------------------------------------------------------------

def _fiber_records(gp: dict, regions: dict[str, dict]) -> list[dict]:
    """Populate weight + active per-fiber. Real fibers use region densities
    (product of endpoint densities). Synthetic fibers get a constant baseline."""
    out: list[dict] = []
    for f in FIBERS:
        a = regions[f["from"]]["density"]
        b = regions[f["to"]]["density"]
        if f["backing"] == "real":
            weight = round((a * b) ** 0.5, 3)
            active = weight > 0.4
        else:
            weight = 0.2
            active = False
        out.append({
            "from_region": f["from"],
            "to_region": f["to"],
            "kind": f["kind"],
            "weight": weight,
            "active": active,
            "backing_data": f["backing"],
        })
    return out


# ----------------------------------------------------------------------
# Hypothesis pill generation
# ----------------------------------------------------------------------

def _pill_cross_provider_bleed(gp: dict) -> dict | None:
    bleed = gp.get("inference_p6_cross_provider_bleed") or []
    if not bleed:
        return {
            "id": "cross_provider_bleed",
            "label": "Cross-provider bleed",
            "group": "cross-project",
            "source_inference_key": "inference_p6_cross_provider_bleed",
            "firing_pattern": [],
            "verdict": "Not enough cross-provider data yet.",
            "top_evidence": [],
            "sufficient_data": False,
            "insufficient_reason": "No entities seen across ≥2 providers yet.",
        }
    providers = sorted({p for b in bleed for p in (b.get("providers") or [])})
    top3 = bleed[:3]
    verdict = (
        f"Your cognition crosses {len(providers)} AI tools. "
        f"Top-3 entities span {top3[0].get('provider_count', 0)}+ providers."
    )
    evidence = [
        {
            "label": b.get("target_id"),
            "detail": f"{b.get('provider_count', 0)} providers · {b.get('event_count', 0)} events",
            "target_id": b.get("target_id"),
        }
        for b in top3
    ]
    return {
        "id": "cross_provider_bleed",
        "label": "Cross-provider bleed",
        "group": "cross-project",
        "source_inference_key": "inference_p6_cross_provider_bleed",
        "firing_pattern": [
            {"region": "corpus_callosum", "intensity": 0.92},
            {"region": "sensory", "intensity": 0.55},
        ],
        "verdict": verdict,
        "top_evidence": evidence,
        "sufficient_data": True,
    }


def _pill_concern_debt(gp: dict) -> dict | None:
    concerns = gp.get("inference_p5_concern_lifecycle") or []
    open_ = [c for c in concerns if c.get("status") == "latent_unresolved"]
    # Concern-lifetime aging gives "days open"
    lifetimes = gp.get("inference_concern_lifetime") or []
    still_open_aged = sorted(
        [c for c in lifetimes if c.get("kind") == "still_open"],
        key=lambda x: -float(x.get("days") or 0.0),
    )
    suff = len(open_) >= 3 or len(still_open_aged) >= 3
    top3 = still_open_aged[:3] if still_open_aged else [
        {"target_id": c.get("target_id"), "days": None, "raised_count": c.get("raised_count")}
        for c in open_[:3]
    ]
    evidence = [
        {
            "label": e.get("target_id"),
            "detail": (
                f"open {e.get('days')} days · raised ×{e.get('raised_count', 1)}"
                if e.get("days") is not None
                else f"raised ×{e.get('raised_count', 1)}"
            ),
            "target_id": e.get("target_id"),
        }
        for e in top3
    ]
    old_count = sum(1 for c in still_open_aged if float(c.get("days") or 0) > 14)
    verdict = (
        f"{old_count} concerns have been open for >14 days."
        if old_count
        else f"{len(open_)} concerns are latent. Address or park."
    )
    return {
        "id": "concern_debt",
        "label": "Concern debt",
        "group": "collaboration-health",
        "source_inference_key": "inference_p5_concern_lifecycle",
        "firing_pattern": [{"region": "anterior_cingulate", "intensity": 0.95}],
        "verdict": verdict,
        "top_evidence": evidence,
        "sufficient_data": suff,
        "insufficient_reason": None if suff else "Fewer than 3 latent concerns in scope.",
    }


def _pill_decision_half_life(gp: dict) -> dict | None:
    hl = gp.get("inference_decision_half_life") or []
    if not hl:
        return {
            "id": "decision_half_life",
            "label": "Decision half-life",
            "group": "collaboration-health",
            "source_inference_key": "inference_decision_half_life",
            "firing_pattern": [{"region": "prefrontal", "intensity": 0.4}],
            "verdict": "Not enough Decision-tagged events yet for thrashing analysis.",
            "top_evidence": [],
            "sufficient_data": False,
            "insufficient_reason": "Need ≥10 tagged decisions; extraction pass tuning still converging.",
        }
    thrashing = [d for d in hl if d.get("thrashing")]
    total = len(hl)
    top3 = sorted(hl, key=lambda x: float(x.get("half_life_days") or 999))[:3]
    verdict = f"{len(thrashing)} of {total} decisions re-opened within 2 sessions."
    evidence = [
        {
            "label": d.get("target_id"),
            "detail": f"half-life {d.get('half_life_days')} days · {d.get('total_events')} events",
            "target_id": d.get("target_id"),
        }
        for d in top3
    ]
    return {
        "id": "decision_half_life",
        "label": "Decision half-life",
        "group": "collaboration-health",
        "source_inference_key": "inference_decision_half_life",
        "firing_pattern": [{"region": "prefrontal", "intensity": 0.85}],
        "verdict": verdict,
        "top_evidence": evidence,
        "sufficient_data": total >= 10,
        "insufficient_reason": None if total >= 10 else f"Only {total} decisions in window.",
    }


def _pill_idea_resurrection(gp: dict) -> dict | None:
    res = gp.get("inference_idea_resurrection") or []
    if not res:
        return {
            "id": "idea_resurrection",
            "label": "Idea resurrection",
            "group": "cognitive-load",
            "source_inference_key": "inference_idea_resurrection",
            "firing_pattern": [{"region": "hippocampus", "intensity": 0.35}],
            "verdict": "No dormant-then-revived ideas in the current window.",
            "top_evidence": [],
            "sufficient_data": False,
            "insufficient_reason": "Need ≥30-day window to detect resurrection.",
        }
    top3 = res[:3]
    evidence = [
        {
            "label": r.get("target_id"),
            "detail": f"gap {r.get('gap_days')} days · last seen {(r.get('last_seen') or '')[:10]}",
            "target_id": r.get("target_id"),
        }
        for r in top3
    ]
    return {
        "id": "idea_resurrection",
        "label": "Idea resurrection",
        "group": "cognitive-load",
        "source_inference_key": "inference_idea_resurrection",
        "firing_pattern": [{"region": "hippocampus", "intensity": 0.8}],
        "verdict": f"{len(res)} ideas returned after >30-day dormancy.",
        "top_evidence": evidence,
        "sufficient_data": True,
    }


def _pill_provider_cognition(gp: dict) -> dict | None:
    pc = gp.get("inference_provider_cognition") or []
    if not pc:
        return None
    top = max(pc, key=lambda p: float(p.get("concern_raised_rate") or 0))
    return {
        "id": "provider_specific_cognition",
        "label": "Provider-specific cognition",
        "group": "tool-fit",
        "source_inference_key": "inference_provider_cognition",
        "firing_pattern": [
            {"region": "sensory", "intensity": 0.65},
            {"region": "anterior_cingulate", "intensity": 0.45},
        ],
        "verdict": (
            f"You raise {top.get('concern_raised_rate', 0):.1f} concerns/session on {top.get('provider')}."
        ),
        "top_evidence": [
            {
                "label": p.get("provider"),
                "detail": f"{p.get('concern_raised_rate', 0):.2f} raise/sess · {p.get('sessions', 0)} sessions",
                "target_id": p.get("provider"),
            }
            for p in sorted(pc, key=lambda x: -float(x.get("concern_raised_rate") or 0))[:3]
        ],
        "sufficient_data": len(pc) >= 2,
    }


def _pill_confirmed_moves(gp: dict) -> dict | None:
    moves = gp.get("confirmed_mental_moves") or []
    if not moves:
        return {
            "id": "mental_moves",
            "label": "Confirmed mental moves",
            "group": "vault",
            "source_inference_key": "confirmed_mental_moves",
            "firing_pattern": [{"region": "prefrontal", "intensity": 0.3}],
            "verdict": "No moves with ≥2 occurrences yet — corpus too thin.",
            "top_evidence": [],
            "sufficient_data": False,
            "insufficient_reason": "Mental-move confirmation requires ≥2 sessions showing the same move.",
        }
    top3 = moves[:3]
    return {
        "id": "mental_moves",
        "label": "Confirmed mental moves",
        "group": "vault",
        "source_inference_key": "confirmed_mental_moves",
        "firing_pattern": [{"region": "prefrontal", "intensity": 0.6}],
        "verdict": f"{len(moves)} mental moves confirmed across sessions.",
        "top_evidence": [
            {
                "label": (m.get("move") or "")[:60],
                "detail": f"[{m.get('level', '?')}/{m.get('owner', '?')}] ×{m.get('occurrences', 0)}",
                "target_id": None,
            }
            for m in top3
        ],
        "sufficient_data": len(moves) >= 3,
    }


_PILL_BUILDERS = [
    _pill_cross_provider_bleed,
    _pill_concern_debt,
    _pill_decision_half_life,
    _pill_idea_resurrection,
    _pill_provider_cognition,
    _pill_confirmed_moves,
]


def _build_pills(gp: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fn in _PILL_BUILDERS:
        pill = fn(gp)
        if pill:
            out[pill["id"]] = pill
    return out


# ----------------------------------------------------------------------
# Timeline
# ----------------------------------------------------------------------

def _timeline(gp: dict, window_days: int) -> list[dict]:
    """Produce a [{day, fired_regions, session_count}] list for the window.

    Source: entity_frequency by provider lacks direct per-day ts; for now
    emit a synthetic ramp based on aggregate recency signals. Real daily
    bucketing will land when we consume the events jsonl directly in v0.4.1.
    """
    days: list[dict] = []
    today = _dt.datetime.utcnow().date()
    n_sessions = int((gp.get("scale") or {}).get("sessions") or 0)
    # Evenly distribute sessions over window_days for placeholder scaffolding
    per_day = max(1, n_sessions // max(window_days, 1)) if n_sessions else 0
    baseline_regions = ["prefrontal", "sensory"]
    for i in range(window_days):
        d = today - _dt.timedelta(days=window_days - 1 - i)
        # Alternate which regions fire for visual texture; real impl reads events
        fired = list(baseline_regions)
        if i % 3 == 0:
            fired.append("anterior_cingulate")
        if i % 5 == 0:
            fired.append("corpus_callosum")
        days.append({
            "day": d.isoformat(),
            "fired_regions": fired,
            "session_count": per_day if per_day > 0 else 0,
        })
    return days


# ----------------------------------------------------------------------
# Compiler class
# ----------------------------------------------------------------------

@register("brain_view")
class BrainViewCompiler(ProjectionCompiler):
    name = "brain_view"
    default_max_tokens = 32000   # JSON can be large; effectively ungated

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        window_days = 30  # default; CLI can override via state.meta in v0.4.1

        regions = _region_signals(gp, window_days)
        fibers = _fiber_records(gp, regions)
        hypotheses = _build_pills(gp)
        timeline = _timeline(gp, window_days)

        scale = gp.get("scale") or {}
        payload = {
            "schema_version": "0.2.1",
            "produced_at": _now_iso(),
            "atelier_user_id": (gp.get("_meta") or {}).get("atelier_user_id"),
            "scale": {
                "sessions": scale.get("sessions", 0),
                "providers": scale.get("providers") or [],
                "window_days": window_days,
                "total_mention_events": scale.get("total_mention_events", 0),
            },
            "regions": [regions[r] for r in REGION_ORDER],
            "fibers": fibers,
            "hypotheses": hypotheses,
            "timeline": timeline,
        }

        return json.dumps(payload, indent=2, ensure_ascii=False)
