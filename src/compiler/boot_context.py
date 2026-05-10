"""Boot-context JSON projection — structured payload for Atelier's
Product Placement Flow to render as interactive cards at project boot.

Consumer: Atelier (reads at new-project boot, renders 6-phase PPF).
"""
from __future__ import annotations
import json

from . import register
from .base import (
    ProjectionCompiler,
    VaultState,
    concerns,
    drifts,
    load_bearing_decisions,
    top_confirmed_moves,
    top_entities,
    top_rules,
)


@register("boot_context")
class BootContextCompiler(ProjectionCompiler):
    name = "boot_context"
    default_max_tokens = 8000  # JSON, less prose-dense

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        scale = gp.get("scale") or {}

        cards = {
            "mental_moves": [
                {
                    "move": (m.get("move") or "").strip(),
                    "level": m.get("level", "gen"),
                    "owner": m.get("owner", "user"),
                    "occurrences": m.get("occurrences", 1),
                }
                for m in top_confirmed_moves(gp, n=10)
            ],
            "rules": [
                {
                    "rule": (r.get("rule_text") or "").strip(),
                    "applies_to": r.get("applies_to", "?"),
                    "level": r.get("level", "?"),
                }
                for r in top_rules(gp, n=10)
            ],
            "latent_concerns": [
                {
                    "target_id": c.get("target_id"),
                    "raised_count": c.get("raised_count"),
                    "type": c.get("type", "?"),
                }
                for c in concerns(gp, status="latent_unresolved", n=8)
            ],
            "drift_warnings": [
                {"trigger": d.get("trigger"), "count": d.get("count")}
                for d in drifts(gp, n=6)
                if int(d.get("count") or 0) >= 2
            ],
            "load_bearing_decisions": [
                {
                    "target_id": d.get("target_id"),
                    "sessions_referenced": d.get("sessions_referenced"),
                }
                for d in load_bearing_decisions(gp, n=8)
            ],
            "top_entities": [
                {
                    "target_id": e.get("target_id"),
                    "type": e.get("type", "?"),
                    "events": e.get("events", 0),
                    "providers": e.get("providers", []),
                }
                for e in top_entities(gp, n=12)
            ],
        }

        payload = {
            "schema": "0.2.1",
            "source": "omnigraph",
            "scale": scale,
            "cards": cards,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
