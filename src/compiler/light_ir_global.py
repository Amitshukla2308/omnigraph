"""Global brain layer — true about THIS user across ALL projects.

Always injected. Contains only:
  - confirmed mental moves (what I observe about how user thinks)
  - meta_user rules (passive observations about the user)
  - drift triggers (failure-mode patterns that recur cross-project)

DOES NOT contain: project-domain rules, entity slugs, project decisions,
project-specific concerns. Those live in the per-project artifact.
"""
from __future__ import annotations
import html

from . import register
from .base import (
    ProjectionCompiler,
    VaultState,
    approx_tokens,
    drifts,
    top_confirmed_moves,
)
from ._layers import META_USER, rules_in_bucket

SCHEMA_V = "0.2.1"


def _esc(s: str) -> str:
    return html.escape((s or "").strip(), quote=True)


@register("light_ir_global")
class LightIRGlobalCompiler(ProjectionCompiler):
    name = "light_ir_global"
    default_max_tokens = 1500

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        lines: list[str] = [f'<global-brain v="{SCHEMA_V}">']

        for m in top_confirmed_moves(gp, n=15):
            level = _esc(m.get("level") or "gen")
            owner = _esc(m.get("owner") or "user")
            move = _esc(m.get("move") or "")
            if move:
                lines.append(f'<mm l="{level}" o="{owner}">{move}</mm>')

        meta_rules = rules_in_bucket(state, META_USER)
        for r in meta_rules[:12]:
            text = _esc(r.get("rule_text") or "")
            if text:
                lines.append(f'<obs>{text}</obs>')

        for d in drifts(gp, n=8):
            cnt = int(d.get("count") or 0)
            if cnt < 2:
                continue
            trig = _esc(d.get("trigger") or "")
            lines.append(f'<drift t="{trig}">count={cnt}</drift>')

        lines.append('</global-brain>')
        out = "\n".join(lines)
        cap = max_tokens or self.default_max_tokens
        if approx_tokens(out) <= cap:
            return out
        # Trim from bottom up: drift, then obs, then mm
        for prefix in ['<drift', '<obs', '<mm']:
            kept = [ln for ln in out.splitlines() if not ln.strip().startswith(prefix)]
            out = "\n".join(kept)
            if approx_tokens(out) <= cap:
                break
        return out
