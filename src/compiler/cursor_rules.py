"""Cursor-flavored rules (.cursorrules).

Cursor expects a plain text file with imperative rules. We emit a compact
bullet list sourced from confirmed mental moves + standing rules + concerns.
"""
from __future__ import annotations

from . import register
from .base import (
    ProjectionCompiler,
    VaultState,
    approx_tokens,
    concerns,
    top_confirmed_moves,
    top_rules,
    truncate_to_tokens,
)


@register("cursor_rules")
class CursorRulesCompiler(ProjectionCompiler):
    name = "cursor_rules"
    default_max_tokens = 2000

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        parts: list[str] = []
        parts.append("# Auto-compiled by OmniGraph. Mental moves, rules, and")
        parts.append("# open concerns distilled from cross-session history.")
        parts.append("")

        parts.append("## Mental moves to honor")
        for m in top_confirmed_moves(gp, n=8):
            parts.append(f"- {(m.get('move') or '').strip()}")
        parts.append("")

        parts.append("## Standing rules")
        for r in top_rules(gp, n=8):
            parts.append(f"- {(r.get('rule_text') or '').strip()}")
        parts.append("")

        unresolved = concerns(gp, status="latent_unresolved", n=6)
        if unresolved:
            parts.append("## Open concerns (do not silently introduce regressions here)")
            for c in unresolved:
                parts.append(f"- `{c.get('target_id')}`")
            parts.append("")

        out = "\n".join(parts)
        cap = max_tokens or self.default_max_tokens
        if approx_tokens(out) > cap:
            out = truncate_to_tokens(out, cap)
        return out
