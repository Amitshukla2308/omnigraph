"""Personal brain layer — how AI agents should BEHAVE WITH this user.

Always injected. Contains:
  - meta_assistant rules (collaboration guidance — "always read before write" etc.)
  - cross-project recurring concerns framed as anticipation hints
    ("user has historically hit OOM 6×; anticipate VRAM constraints")

DOES NOT contain: mental moves (those are global), project rules, entity slugs.
"""
from __future__ import annotations
import html

from . import register
from .base import (
    ProjectionCompiler,
    VaultState,
    approx_tokens,
    concerns,
)
from ._layers import META_ASSISTANT, rules_in_bucket

SCHEMA_V = "0.2.1"


def _esc(s: str) -> str:
    return html.escape((s or "").strip(), quote=True)


@register("light_ir_personal")
class LightIRPersonalCompiler(ProjectionCompiler):
    name = "light_ir_personal"
    default_max_tokens = 1500

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        lines: list[str] = [f'<personal-brain v="{SCHEMA_V}">']

        for r in rules_in_bucket(state, META_ASSISTANT)[:20]:
            text = _esc(r.get("rule_text") or "")
            if text:
                lines.append(f'<collab>{text}</collab>')

        # Cross-project recurring failure modes — framed as anticipation hints,
        # NOT as project facts. Ditch entity slugs that look project-named.
        for c in concerns(gp, status="latent_unresolved", n=8):
            tid = (c.get("target_id") or "").lower()
            nr = int(c.get("raised_count") or 1)
            if nr < 3:
                continue
            # Heuristic: if it looks like a generic technical concern, include.
            # If it's a named project/product entity, skip (will appear in that project's brain).
            generic_signals = {"oom", "api-error", "quota-exhaustion", "permissions",
                               "web-fetch", "webfetch", "lite-llm", "curl", "bash"}
            if tid in generic_signals or any(s in tid for s in generic_signals):
                lines.append(
                    f'<anticipate r="{nr}">{_esc(tid)}</anticipate>'
                )

        lines.append('</personal-brain>')
        out = "\n".join(lines)
        cap = max_tokens or self.default_max_tokens
        if approx_tokens(out) <= cap:
            return out
        # Trim from bottom: anticipate first, then collab
        for prefix in ['<anticipate', '<collab']:
            kept = [ln for ln in out.splitlines() if not ln.strip().startswith(prefix)]
            out = "\n".join(kept)
            if approx_tokens(out) <= cap:
                break
        return out
