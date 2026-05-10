"""Project brain layer — true about ONE specific project.

Injected ONLY when the session is about that project (Atelier handles routing).
Contains:
  - project_domain rules tagged with this project
  - per-entity descriptions from enriched Vault pages (zeroclaw.md etc.)
  - load-bearing decisions whose related_entities mention this project
  - project entity facts from entity_frequency_top30

The project name is taken from compiler state (state.project_name).
"""
from __future__ import annotations
import html

from . import register
from .base import (
    ProjectionCompiler,
    VaultState,
    approx_tokens,
)
from ._layers import (
    project_decisions,
    project_related_entities,
    rules_for_project,
    vault_summary_for_entity,
)

SCHEMA_V = "0.2.1"


def _esc(s: str) -> str:
    return html.escape((s or "").strip(), quote=True)


@register("light_ir_project")
class LightIRProjectCompiler(ProjectionCompiler):
    name = "light_ir_project"
    default_max_tokens = 2500

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        project = getattr(state, "project_name", None) or "?"
        lines: list[str] = [f'<project-brain v="{SCHEMA_V}" p="{_esc(project)}">']

        # Per-entity descriptions — pulled from enriched Vault pages.
        ent_lines = []
        related = project_related_entities(gp, project)
        if related:
            ent_lines.append('<entities>')
            for e in related[:10]:
                tid = e.get("target_id", "")
                summary = vault_summary_for_entity(tid) or ""
                providers = ",".join(e.get("providers", []))
                ev = e.get("events", 0)
                line = f'  <ent tid="{_esc(tid)}" ev="{ev}" providers="{_esc(providers)}">'
                if summary:
                    line += _esc(summary[:280])
                line += '</ent>'
                ent_lines.append(line)
            ent_lines.append('</entities>')
            lines.extend(ent_lines)

        # Project-domain rules tagged to this project.
        rules = rules_for_project(state, project)
        if rules:
            for r in rules[:15]:
                text = _esc(r.get("rule_text") or "")
                if text:
                    lines.append(f'<rule>{text}</rule>')

        # Load-bearing decisions touching this project.
        decisions = project_decisions(gp, project)
        if decisions:
            for d in decisions[:10]:
                prop = _esc((d.get("proposition") or "")[:200])
                refs = int(d.get("sessions_referenced") or 0)
                lines.append(f'<decision refs="{refs}">{prop}</decision>')

        lines.append('</project-brain>')
        out = "\n".join(lines)
        cap = max_tokens or self.default_max_tokens
        if approx_tokens(out) <= cap:
            return out
        for prefix in ['<decision', '<rule', '  <ent', '<entities', '</entities']:
            kept = [ln for ln in out.splitlines() if not ln.strip().startswith(prefix)]
            out = "\n".join(kept)
            if approx_tokens(out) <= cap:
                break
        return out
