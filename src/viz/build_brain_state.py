"""Build brain_state.json from OmniGraph artifacts.

Takes global_profile.json + Vault + events and assembles the BrainState
contract consumed by the frontend (atelier/apps/brain-viz-draft/).

The global_profile.json schema (from stage2_aggregate.py) uses these keys:
- scale: {sessions, providers, total_mention_events, total_deltas}
- entity_frequency_top30: [{target_id, type, events, providers}]
- inference_p1_convergence_vs_abandonment: list of inference results
- inference_p3_decision_load_bearing: list of decision entries
- inference_p5_concern_lifecycle: list of {target, status, duration_sessions}
- inference_p6_cross_provider_bleed: list of cross-provider data
- confirmed_mental_moves: list
- candidate_mental_moves_single_session: list
- entity_frequency_top30: list of top entities
- drift_recurrence_by_trigger: list of {trigger, count}
- inference_idea_resurrection: list
- inference_decision_half_life: list
- inference_concern_lifetime: list
- inference_provider_cognition: list of {provider, sessions, concern_raised_rate, ...}
- rules_collected: list
- rules_collected_count: int
- affect_events: list of {marker, owner, trigger, ...}
- stances_collected_count: int
- meta: {latest_timestamp, earliest_timestamp, project_count, providers}

Usage:
  python -m viz.build_brain_state --state pilot/qwen --out pilot/viz/brain_state.json
  python -m viz.build_brain_state --atelier-root ~/informed-vibes/atelier --user-id default --out brain_state.json
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any

# Region mapping: schema type → anatomical region
REGION_MAP: dict[str, str] = {
    "Decisions": "prefrontal",
    "Plans": "prefrontal",
    "Stances": "prefrontal",
    "Project": "prefrontal",
    "Actions": "motor",
    "Commits": "motor",
    "MentionEvent": "sensory",
    "MentalMove": "sensory",
    "Concept": "anterior_cingulate",
    "Concern": "anterior_cingulate",
    "Rule": "anterior_cingulate",
    "Drift": "amygdala",
    "Affect": "amygdala",
    "ToolCall": "brainstem",
    "MCPInvocation": "brainstem",
    "Tool": "brainstem",
    "CrossProject": "corpus_callosum",
    "CrossProvider": "corpus_callosum",
    "Resurrection": "hippocampus",
    "Language": "sensory",
    "Product": "sensory",
    "Artifact": "sensory",
    "Agent": "sensory",
}

REGION_IDS = [
    "prefrontal",
    "motor",
    "hippocampus",
    "amygdala",
    "brainstem",
    "sensory",
    "anterior_cingulate",
    "corpus_callosum",
]

# Fiber connections between regions (hand-authored, anatomically plausible)
FIBER_CONNECTIONS: list[tuple[str, str]] = [
    ("prefrontal", "motor"),
    ("prefrontal", "anterior_cingulate"),
    ("prefrontal", "corpus_callosum"),
    ("prefrontal", "hippocampus"),
    ("motor", "brainstem"),
    ("motor", "anterior_cingulate"),
    ("sensory", "prefrontal"),
    ("sensory", "motor"),
    ("hippocampus", "amygdala"),
    ("hippocampus", "prefrontal"),
    ("amygdala", "anterior_cingulate"),
    ("amygdala", "prefrontal"),
    ("brainstem", "motor"),
    ("corpus_callosum", "prefrontal"),
    ("corpus_callosum", "hippocampus"),
    ("anterior_cingulate", "prefrontal"),
    ("anterior_cingulate", "amygdala"),
    ("sensory", "hippocampus"),
    ("brainstem", "corpus_callosum"),
    ("prefrontal", "sensory"),
]


def _build_regions(global_profile: dict[str, Any]) -> list[dict]:
    """Derive per-region density, color_temp, last_fired from global_profile."""
    regions: dict[str, dict] = {
        rid: {"density": 0.0, "color_temp": 0.0, "last_fired_ts": 0, "type_counts": {}}
        for rid in REGION_IDS
    }

    # Count event types from entity_frequency_top30
    entity_freq = global_profile.get("entity_frequency_top30", [])
    if isinstance(entity_freq, list):
        for entry in entity_freq:
            if not isinstance(entry, dict):
                continue
            target_type = entry.get("type", "Concept")
            region = REGION_MAP.get(target_type, "sensory")
            count = entry.get("events", entry.get("count", 1))
            regions[region]["type_counts"][target_type] = regions[region]["type_counts"].get(target_type, 0) + count

    # Also count from inference_p1 (abandonment data → amygdala)
    p1 = global_profile.get("inference_p1_convergence_vs_abandonment", [])
    if isinstance(p1, list):
        for item in p1:
            if isinstance(item, dict):
                status = item.get("status", item.get("convergence", ""))
                if status == "abandoned":
                    regions["amygdala"]["type_counts"]["Affect"] = regions["amygdala"]["type_counts"].get("Affect", 0) + 1
                elif status == "active":
                    regions["prefrontal"]["type_counts"]["Decisions"] = regions["prefrontal"]["type_counts"].get("Decisions", 0) + 1

    # Count from inference_p3 (decisions → prefrontal)
    p3 = global_profile.get("inference_p3_decision_load_bearing", [])
    if isinstance(p3, list):
        for item in p3:
            if isinstance(item, dict):
                lb = item.get("load_bearing", item.get("loadBearing", False))
                if lb:
                    regions["prefrontal"]["type_counts"]["Decisions"] = regions["prefrontal"]["type_counts"].get("Decisions", 0) + 1

    # Count concerns from inference_p5
    p5 = global_profile.get("inference_p5_concern_lifecycle", [])
    if isinstance(p5, list):
        for item in p5:
            if isinstance(item, dict):
                status = item.get("status", "unknown")
                regions["anterior_cingulate"]["type_counts"]["Concern"] = regions["anterior_cingulate"]["type_counts"].get("Concern", 0) + 1
                if status == "latent_unresolved":
                    regions["anterior_cingulate"]["type_counts"]["Concern"] = regions["anterior_cingulate"]["type_counts"].get("Concern", 0)

    # Count affect events
    affect = global_profile.get("affect_events", [])
    if isinstance(affect, list):
        for item in affect:
            if isinstance(item, dict):
                marker = item.get("marker", "concern")
                if marker == "frustration":
                    regions["amygdala"]["type_counts"]["Affect"] = regions["amygdala"]["type_counts"].get("Affect", 0) + 1
                elif marker == "concern":
                    regions["anterior_cingulate"]["type_counts"]["Concern"] = regions["anterior_cingulate"]["type_counts"].get("Concern", 0) + 1

    # Count mental moves
    confirmed = global_profile.get("confirmed_mental_moves", [])
    if isinstance(confirmed, list):
        regions["sensory"]["type_counts"]["MentalMove"] = len(confirmed)

    # Count rules
    rules = global_profile.get("rules_collected", [])
    if isinstance(rules, list):
        regions["anterior_cingulate"]["type_counts"]["Rule"] = len(rules)

    # Count stances (decisions)
    stances = global_profile.get("stances_collected_count", 0)
    if isinstance(stances, int) and stances > 0:
        regions["prefrontal"]["type_counts"]["Decisions"] = regions["prefrontal"]["type_counts"].get("Decisions", 0) + stances

    # Count sessions
    scale = global_profile.get("scale", {})
    session_count = scale.get("sessions", 0)

    # Derive project count from entity_frequency if meta doesn't have it
    meta = global_profile.get("meta", {})
    project_count = meta.get("project_count", 0)
    if not isinstance(project_count, int) or project_count == 0:
        # Count unique projects from entity_frequency
        entity_freq = global_profile.get("entity_frequency_top30", [])
        if isinstance(entity_freq, list):
            project_count = sum(1 for e in entity_freq if isinstance(e, dict) and e.get("type") == "Project")

    # Derive density from event counts using log scale
    for rid, rdata in regions.items():
        total = sum(rdata["type_counts"].values())
        max_possible = session_count * 0.5
        if total > 0:
            rdata["density"] = min(1.0, math.log1p(total) / math.log1p(max(max_possible, 1)))
        else:
            # Minimum base density for regions with no direct data
            rdata["density"] = 0.05

        # Color temp: positive = warm (concerns, affect), negative = cool (plans, tools)
        warm = rdata["type_counts"].get("Concern", 0) + rdata["type_counts"].get("Affect", 0)
        cool = rdata["type_counts"].get("Plans", 0) + rdata["type_counts"].get("ToolCall", 0)
        total_types = warm + cool
        rdata["color_temp"] = (warm - cool) / max(total_types, 1)
        rdata["type_counts"] = dict(sorted(rdata["type_counts"].items(), key=lambda x: -x[1]))

    # last_fired_ts from meta
    meta = global_profile.get("meta", {})
    latest_ts = meta.get("latest_timestamp", 0)
    for rid in REGION_IDS:
        regions[rid]["last_fired_ts"] = latest_ts

    return [
        {
            "id": rid,
            "density": round(regions[rid]["density"], 3),
            "color_temp": round(regions[rid]["color_temp"], 3),
            "last_fired_ts": regions[rid]["last_fired_ts"],
        }
        for rid in REGION_IDS
    ]


def _build_fibers(regions: list[dict]) -> list[dict]:
    """Build fiber connections between regions, weighted by co-occurrence."""
    fibers = []
    for from_r, to_r in FIBER_CONNECTIONS:
        src = next((r for r in regions if r["id"] == from_r), None)
        tgt = next((r for r in regions if r["id"] == to_r), None)
        if src and tgt:
            weight = (src["density"] * tgt["density"]) ** 0.5
            fibers.append({
                "from_region": from_r,
                "to_region": to_r,
                "weight": round(weight, 3),
                "active": weight > 0.15,
            })
    return fibers


def _build_hypotheses(global_profile: dict[str, Any]) -> dict:
    """Run the 10 diagnostic hypotheses over the data."""
    scale = global_profile.get("scale", {})
    session_count = scale.get("sessions", 0)
    meta = global_profile.get("meta", {})
    entity_freq = global_profile.get("entity_frequency_top30", [])

    # Count entities by type
    type_counts: dict[str, int] = {}
    if isinstance(entity_freq, list):
        for entry in entity_freq:
            if isinstance(entry, dict):
                ttype = entry.get("type", "Concept")
                count = entry.get("events", entry.get("count", 1))
                type_counts[ttype] = type_counts.get(ttype, 0) + count

    # Count decisions, concerns, drifts
    decisions = meta.get("stances_collected_count", 0)
    if not isinstance(decisions, int):
        decisions = 0
    concerns = len(global_profile.get("inference_p5_concern_lifecycle", []))
    if not isinstance(concerns, int):
        concerns = 0
    rules = global_profile.get("rules_collected_count", 0)
    if not isinstance(rules, int):
        rules = 0
    confirmed_moves = len(global_profile.get("confirmed_mental_moves", []))
    mental_moves = confirmed_moves
    affect_events = len(global_profile.get("affect_events", []))
    drift_events = global_profile.get("total_deltas", 0)
    if not isinstance(drift_events, int):
        drift_events = 0
    provider_data = global_profile.get("inference_provider_cognition", [])
    if not isinstance(provider_data, list):
        provider_data = []
    providers = [p.get("provider", "") for p in provider_data if isinstance(p, dict)]

    # --- Hypothesis 1: Rehearsal vs commitment ---
    half_life = global_profile.get("inference_decision_half_life", [])
    if isinstance(half_life, list):
        short_half_life = sum(1 for h in half_life if isinstance(h, dict) and h.get("half_life_sessions", 999) <= 2)
    else:
        short_half_life = 0

    # Use stances as fallback for decision count
    decision_count = max(decisions, len(half_life) if isinstance(half_life, list) else 0)
    short_pct = (short_half_life / max(decision_count, 1)) * 100
    h1_intensity = min(1.0, short_pct / 50)
    h1_verdict = (
        "Many decisions are being re-opened within 2 sessions."
        if short_pct > 30
        else "Decisions hold — good commitment discipline."
        if short_pct < 15
        else f"Moderate revision rate: {short_pct:.0f}% of decisions revisited quickly."
    )
    h1_evidence = []
    if short_half_life > 0:
        h1_evidence.append({
            "label": f"{short_half_life} decisions",
            "detail": f"Revised within 2 sessions ({short_pct:.0f}% of all decisions)",
        })

    # --- Hypothesis 2: Concern debt ---
    concern_debt_count = concerns
    h2_intensity = min(1.0, concern_debt_count / 10)
    h2_verdict = (
        f"{concern_debt_count} concerns open without resolution."
        if concern_debt_count > 5
        else f"{concern_debt_count} unresolved concerns — manageable debt."
        if concern_debt_count > 0
        else "No outstanding concern debt."
    )
    h2_evidence = []
    p5 = global_profile.get("inference_p5_concern_lifecycle", [])
    if isinstance(p5, list):
        for c in p5[:3]:
            if isinstance(c, dict):
                h2_evidence.append({
                    "label": c.get("target", c.get("target_id", "unknown")),
                    "detail": c.get("status", "Unresolved"),
                })

    # --- Hypothesis 3: Affect-precedes-abandonment ---
    p1 = global_profile.get("inference_p1_convergence_vs_abandonment", [])
    if isinstance(p1, list):
        abandon_matches = sum(1 for item in p1 if isinstance(item, dict) and item.get("status") == "abandoned")
    else:
        abandon_matches = 0
    h3_intensity = min(1.0, abandon_matches / 3)
    h3_verdict = (
        "Amygdala pattern matches prior abandonment signature."
        if abandon_matches > 0
        else "No abandonment pattern detected."
    )
    h3_evidence = []
    if abandon_matches > 0:
        h3_evidence.append({"label": f"{abandon_matches} matches", "detail": "Affect pattern consistent with past abandonment"})

    # --- Hypothesis 4: Drift rate by session time ---
    drift_by_trigger = global_profile.get("drift_recurrence_by_trigger", [])
    if not isinstance(drift_by_trigger, list):
        drift_by_trigger = []
    # Use trigger-based drift as proxy for time-based drift
    self_catch = 0
    tool_failure = 0
    for item in drift_by_trigger:
        if isinstance(item, dict):
            trigger = item.get("trigger", "")
            count = item.get("count", 0)
            if trigger == "self_catch":
                self_catch = count
            elif trigger == "tool_failure":
                tool_failure = count
    # Use self_catch (evening) vs tool_failure (morning) as proxy
    drift_ratio = self_catch / max(tool_failure, 1)
    h4_intensity = min(1.0, (drift_ratio - 1) / 2)
    h4_verdict = (
        f"Self-catch drift ({self_catch}) is {drift_ratio:.1f}× tool-failure drift ({tool_failure})."
        if drift_ratio > 1.2
        else f"Drift patterns are balanced (self-catch: {self_catch}, tool-failure: {tool_failure})."
    )
    h4_evidence = [
        {"label": "Self-catch drift", "detail": str(self_catch)},
        {"label": "Tool-failure drift", "detail": str(tool_failure)},
    ]

    # --- Hypothesis 5: Concurrent project pressure ---
    project_count = meta.get("project_count", 0)
    if not isinstance(project_count, int):
        project_count = len(meta.get("providers", []))  # fallback: use provider count as proxy
    if not isinstance(project_count, int) or project_count == 0:
        # Derive from entity_frequency as final fallback
        entity_freq = global_profile.get("entity_frequency_top30", [])
        if isinstance(entity_freq, list):
            project_count = sum(1 for e in entity_freq if isinstance(e, dict) and e.get("type") == "Project")
    h5_intensity = min(1.0, project_count / 5)
    h5_verdict = (
        f"Above-3 projects active — pressure detected ({project_count} projects)."
        if project_count > 3
        else f"{project_count} active project(s) — within healthy range."
    )
    h5_evidence = [{"label": "Active projects", "detail": str(project_count)}]

    # --- Hypothesis 6: Tool storm ---
    tool_count = type_counts.get("Tool", 0) + type_counts.get("ToolCall", 0)
    h6_intensity = min(1.0, tool_count / 100)
    h6_verdict = (
        "High tool-call density — potential tool storm."
        if tool_count > 50
        else f"Tool-call density normal ({tool_count} tool mentions)."
    )
    h6_evidence = [{"label": "Tool mentions", "detail": str(tool_count)}]

    # --- Hypothesis 7: Cross-pollination vs bleed ---
    p6 = global_profile.get("inference_p6_cross_provider_bleed", [])
    if not isinstance(p6, list):
        p6 = []
    cross_count = len(p6)
    h7_intensity = min(1.0, cross_count / 20)
    h7_verdict = (
        f"{cross_count} cross-provider events — check for productive vs leaky."
        if cross_count > 5
        else "Low cross-provider activity."
    )
    h7_evidence = [{"label": "Cross-provider events", "detail": str(cross_count)}]

    # --- Hypothesis 8: Provider-specific cognition ---
    h8_intensity = min(1.0, len(providers) / 5)
    h8_verdict = (
        f"Active across {len(providers)} providers — patterns may differ."
        if len(providers) > 1
        else f"Single provider ({providers[0] if providers else 'unknown'}). Multi-provider data needed."
    )
    h8_evidence = [{"label": "Providers", "detail": ", ".join(providers) if providers else "none"}]

    # --- Hypothesis 9: Rule firing under pressure ---
    rule_count = rules + mental_moves
    h9_intensity = min(1.0, rule_count / 50)
    h9_verdict = (
        f"{rule_count} rules/mental moves extracted — pattern tracking available."
        if rule_count > 5
        else f"Minimal rule extraction ({rule_count}). More sessions needed."
    )
    h9_evidence = [{"label": "Rules", "detail": str(rules)}, {"label": "Mental moves", "detail": str(mental_moves)}]

    # --- Hypothesis 10: Silent failure ---
    deprecated = 0
    # Count latent unresolved concerns as "silent failures"
    if isinstance(p5, list):
        deprecated = sum(1 for item in p5 if isinstance(item, dict) and item.get("status") == "latent_unresolved")
    h10_intensity = min(1.0, deprecated / 5)
    h10_verdict = (
        f"{deprecated} concerns remain latent/unresolved — potential silent failures."
        if deprecated > 0
        else "No outstanding silent failures."
    )
    h10_evidence = [{"label": "Latent concerns", "detail": str(deprecated)}]

    all_hypotheses = {
        "rehearsal_vs_commitment": {
            "id": "rehearsal_vs_commitment",
            "label": "Rehearsal vs Commitment",
            "group": "collab",
            "firing_pattern": [{"region": "prefrontal", "intensity": round(h1_intensity, 3)}],
            "verdict": h1_verdict,
            "top_evidence": h1_evidence,
            "sufficient_data": decision_count > 3,
        },
        "concern_debt": {
            "id": "concern_debt",
            "label": "Concern Debt",
            "group": "collab",
            "firing_pattern": [{"region": "anterior_cingulate", "intensity": round(h2_intensity, 3)}],
            "verdict": h2_verdict,
            "top_evidence": h2_evidence,
            "sufficient_data": concerns > 2,
        },
        "affect_before_abandonment": {
            "id": "affect_before_abandonment",
            "label": "Affect → Abandonment",
            "group": "collab",
            "firing_pattern": [{"region": "amygdala", "intensity": round(h3_intensity, 3)}],
            "verdict": h3_verdict,
            "top_evidence": h3_evidence,
            "sufficient_data": session_count > 10,
        },
        "drift_by_session_time": {
            "id": "drift_by_session_time",
            "label": "Drift by Time of Day",
            "group": "cogload",
            "firing_pattern": [
                {"region": "prefrontal", "intensity": round(min(1.0, drift_ratio / 3), 3)},
                {"region": "motor", "intensity": round(min(1.0, self_catch / 50), 3)},
            ],
            "verdict": h4_verdict,
            "top_evidence": h4_evidence,
            "sufficient_data": session_count > 5,
        },
        "concurrent_project_pressure": {
            "id": "concurrent_project_pressure",
            "label": "Project Pressure",
            "group": "cogload",
            "firing_pattern": [{"region": "prefrontal", "intensity": round(h5_intensity, 3)}],
            "verdict": h5_verdict,
            "top_evidence": h5_evidence,
            "sufficient_data": project_count > 0,
        },
        "tool_storm": {
            "id": "tool_storm",
            "label": "Tool Storm",
            "group": "cogload",
            "firing_pattern": [{"region": "brainstem", "intensity": round(h6_intensity, 3)}],
            "verdict": h6_verdict,
            "top_evidence": h6_evidence,
            "sufficient_data": session_count > 3,
        },
        "cross_pollination_vs_bleed": {
            "id": "cross_pollination_vs_bleed",
            "label": "Cross-Pollination",
            "group": "cross",
            "firing_pattern": [{"region": "corpus_callosum", "intensity": round(h7_intensity, 3)}],
            "verdict": h7_verdict,
            "top_evidence": h7_evidence,
            "sufficient_data": cross_count > 0,
        },
        "provider_specific_cognition": {
            "id": "provider_specific_cognition",
            "label": "Provider Cognition",
            "group": "cross",
            "firing_pattern": [{"region": "sensory", "intensity": round(h8_intensity, 3)}],
            "verdict": h8_verdict,
            "top_evidence": h8_evidence,
            "sufficient_data": len(providers) > 0,
        },
        "rule_firing_under_pressure": {
            "id": "rule_firing_under_pressure",
            "label": "Rule Firing",
            "group": "toolfit",
            "firing_pattern": [
                {"region": "prefrontal", "intensity": round(h9_intensity, 3)},
                {"region": "anterior_cingulate", "intensity": round(h9_intensity * 0.7, 3)},
            ],
            "verdict": h9_verdict,
            "top_evidence": h9_evidence,
            "sufficient_data": rule_count > 3,
        },
        "silent_failure": {
            "id": "silent_failure",
            "label": "Silent Failure",
            "group": "toolfit",
            "firing_pattern": [{"region": "prefrontal", "intensity": round(h10_intensity, 3)}],
            "verdict": h10_verdict,
            "top_evidence": h10_evidence,
            "sufficient_data": decision_count > 2,
        },
    }

    return all_hypotheses


def _build_timeline(global_profile: dict[str, Any]) -> list[dict]:
    """Build temporal event stream from global_profile metadata."""
    meta = global_profile.get("meta", {})
    sessions = global_profile.get("sessions", [])
    scale = global_profile.get("scale", {})

    # If we have session-level data, build timeline from that
    if isinstance(sessions, list) and len(sessions) > 0:
        timeline = []
        for s in sessions[-50:]:
            ts = s.get("timestamp", s.get("date", 0))
            if ts:
                fired = []
                types = s.get("types", s.get("target_types", []))
                if isinstance(types, list):
                    for t in types:
                        region = REGION_MAP.get(t, "sensory")
                        if region not in fired:
                            fired.append(region)
                elif isinstance(types, dict):
                    for ttype in types:
                        region = REGION_MAP.get(ttype, "sensory")
                        if region not in fired:
                            fired.append(region)
                timeline.append({"ts": ts, "fired_regions": fired})
        return timeline

    # Fallback: single entry from meta
    latest_ts = meta.get("latest_timestamp", 0)
    if latest_ts:
        return [{"ts": latest_ts, "fired_regions": list(REGION_IDS[:3])}]
    return []


def build_brain_state(
    global_profile: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a BrainState from a global_profile.json dict.

    This is the core function — takes OmniGraph's aggregate output and
    produces the single JSON contract the frontend consumes.
    """
    regions = _build_regions(global_profile)
    return {
        "regions": regions,
        "fibers": _build_fibers(regions),
        "hypotheses": _build_hypotheses(global_profile),
        "timeline": _build_timeline(global_profile),
        "meta": {
            "sessions": global_profile.get("scale", {}).get("sessions", 0),
            "entities": len(global_profile.get("entity_frequency_top30", [])),
            "built_at": 0,
        },
    }


def build_brain_state_from_dir(
    state_dir: Path | str,
    out_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build brain_state.json from a directory containing global_profile.json.

    Args:
        state_dir: Directory with global_profile.json (e.g., pilot/qwen/, pilot/full/)
        out_path: Where to write brain_state.json. If None, returns dict only.

    Returns:
        The BrainState dict.
    """
    state_dir = Path(state_dir)
    gp_path = state_dir / "global_profile.json"
    if not gp_path.exists():
        raise FileNotFoundError(f"global_profile.json not found in {state_dir}")

    gp = json.loads(gp_path.read_text())
    brain_state = build_brain_state(gp)
    brain_state["meta"]["built_at"] = 0

    if out_path:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(brain_state, indent=2, default=str))
        print(f"✅ brain_state → {out}")

    return brain_state
