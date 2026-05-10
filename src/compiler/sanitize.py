"""Sugar-ladder sanitization for global_profile / BrainState / compiled output.

Level 0 "none"             — no change (default)
Level 1 "named_stripped"   — project / entity names → deterministic pseudonyms;
                             structure and relationships preserved
Level 2 "entities_removed" — target_ids dropped entirely; keep only mental moves,
                             rules, drifts, concern-lifecycle *shapes* (counts,
                             valence distributions) without identifiable targets
Level 3 "aggregated"       — highest sugar: only population-level stats

Any compiler can run over a sanitized `global_profile` to produce a shareable
projection. Pseudonyms are stable per-corpus (same input → same output).
"""
from __future__ import annotations
import hashlib
from copy import deepcopy
from typing import Any

VALID_LEVELS = ("none", "named_stripped", "entities_removed", "aggregated")


def _stable_pseudonym(token: str, salt: str = "omnigraph") -> str:
    h = hashlib.blake2s(f"{salt}:{token}".encode(), digest_size=4).hexdigest()
    # Use "proj_<hash>" or "tool_<hash>" when the type is known; generic elsewhere.
    return f"entity-{h}"


def _alias_map(global_profile: dict) -> dict[str, str]:
    """Build a stable alias map from observed target_ids."""
    seen: set[str] = set()
    for section in (
        global_profile.get("entity_frequency_top30") or [],
        global_profile.get("inference_p6_cross_provider_bleed") or [],
        global_profile.get("inference_p3_decision_load_bearing") or [],
        global_profile.get("inference_p5_concern_lifecycle") or [],
        global_profile.get("inference_idea_resurrection") or [],
        global_profile.get("inference_decision_half_life") or [],
        global_profile.get("inference_concern_lifetime") or [],
        global_profile.get("inference_p1_convergence_vs_abandonment") or [],
    ):
        for item in section:
            if isinstance(item, dict):
                tid = item.get("target_id")
                if isinstance(tid, str):
                    seen.add(tid)
    # Type-biased prefix for readability
    types: dict[str, str] = {}
    for item in global_profile.get("entity_frequency_top30") or []:
        if isinstance(item, dict):
            tid = item.get("target_id")
            if isinstance(tid, str):
                types[tid] = item.get("type") or "Entity"
    out = {}
    for tid in sorted(seen):
        prefix = (types.get(tid) or "Entity").lower()[:4] or "ent"
        h = hashlib.blake2s(tid.encode(), digest_size=3).hexdigest()
        out[tid] = f"{prefix}-{h}"
    return out


def _rewrite_target_fields(items: list, alias: dict[str, str]) -> list:
    out = []
    for it in items:
        if not isinstance(it, dict):
            out.append(it); continue
        new = dict(it)
        for key in ("target_id", "from_target", "to_target"):
            if key in new and isinstance(new[key], str):
                new[key] = alias.get(new[key], "entity-unknown")
        # related_entities lists
        if isinstance(new.get("related_entities"), list):
            new["related_entities"] = [
                alias.get(x, "entity-unknown") if isinstance(x, str) else x
                for x in new["related_entities"]
            ]
        out.append(new)
    return out


def sanitize_global_profile(global_profile: dict, level: str = "none") -> dict:
    if level not in VALID_LEVELS:
        raise ValueError(f"unknown sanitize level {level!r}; have {VALID_LEVELS}")
    if level == "none":
        return deepcopy(global_profile)

    gp = deepcopy(global_profile)

    if level == "named_stripped":
        alias = _alias_map(gp)
        entity_sections = [
            "entity_frequency_top30",
            "inference_p1_convergence_vs_abandonment",
            "inference_p3_decision_load_bearing",
            "inference_p5_concern_lifecycle",
            "inference_p6_cross_provider_bleed",
            "inference_idea_resurrection",
            "inference_decision_half_life",
            "inference_concern_lifetime",
        ]
        for s in entity_sections:
            if isinstance(gp.get(s), list):
                gp[s] = _rewrite_target_fields(gp[s], alias)
        # Rules may name targets in applies_to — genericize.
        for r in gp.get("rules_collected") or []:
            if isinstance(r, dict) and isinstance(r.get("applies_to"), str):
                if r["applies_to"] in alias:
                    r["applies_to"] = alias[r["applies_to"]]
        return gp

    if level == "entities_removed":
        # Keep mental moves, rules (with applies_to stripped), drifts.
        # Drop entity-named sections outright.
        kept = {
            "scale": gp.get("scale") or {},
            "confirmed_mental_moves": gp.get("confirmed_mental_moves") or [],
            "rules_collected": [
                {**{k: v for k, v in (r or {}).items() if k != "applies_to"},
                 "applies_to": "—"}
                for r in (gp.get("rules_collected") or [])
            ],
            "drift_recurrence_by_trigger": gp.get("drift_recurrence_by_trigger") or [],
            "candidate_mental_moves_single_session": gp.get("candidate_mental_moves_single_session") or [],
            "inference_provider_cognition": gp.get("inference_provider_cognition") or [],
        }
        return kept

    # "aggregated" — population-level stats only
    gp_small = {
        "scale": gp.get("scale") or {},
        "counts": {
            "confirmed_mental_moves": len(gp.get("confirmed_mental_moves") or []),
            "rules": len(gp.get("rules_collected") or []),
            "latent_concerns": sum(
                1 for c in (gp.get("inference_p5_concern_lifecycle") or [])
                if c.get("status") == "latent_unresolved"
            ),
            "load_bearing_decisions": sum(
                1 for d in (gp.get("inference_p3_decision_load_bearing") or [])
                if d.get("load_class") == "load-bearing"
            ),
            "cross_provider_entities": len(gp.get("inference_p6_cross_provider_bleed") or []),
            "resurrected_ideas": len(gp.get("inference_idea_resurrection") or []),
            "drift_triggers_recurring": sum(
                1 for d in (gp.get("drift_recurrence_by_trigger") or [])
                if int(d.get("count") or 0) >= 2
            ),
        },
    }
    return gp_small
