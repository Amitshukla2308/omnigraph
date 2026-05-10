"""CLAUDE.md / AGENTS.md projection — Markdown with YAML frontmatter.

Consumer: Claude Code, Claude Desktop, or any tool that reads a project-level
system-prompt Markdown file.
"""
from __future__ import annotations

from . import register
from .base import (
    ProjectionCompiler,
    VaultState,
    approx_tokens,
    concerns,
    drifts,
    load_bearing_decisions,
    top_confirmed_moves,
    top_entities,
    top_rules,
    truncate_to_tokens,
)


@register("claude_md")
class ClaudeMDCompiler(ProjectionCompiler):
    name = "claude_md"
    default_max_tokens = 4000

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        scale = gp.get("scale") or {}

        parts: list[str] = []
        parts.append("---")
        parts.append("source: omnigraph")
        parts.append("schema: 0.2.1")
        parts.append(f"sessions: {scale.get('sessions', 0)}")
        parts.append(f"mentions: {scale.get('total_mention_events', 0)}")
        parts.append(f"providers: {scale.get('providers') or []}")
        parts.append("---")
        parts.append("")
        parts.append("# User meta-profile")
        parts.append("")
        parts.append(
            "_Auto-compiled by OmniGraph from cross-session extraction. "
            "Honor mental moves, rules, concerns, and load-bearing decisions "
            "below when planning or coding._"
        )
        parts.append("")

        moves = top_confirmed_moves(gp, n=10)
        if moves:
            parts.append("## Mental moves (confirmed, ≥2 occurrences)")
            for m in moves:
                parts.append(
                    f"- **[{m.get('level', 'gen')}/{m.get('owner', 'user')}]** "
                    f"{(m.get('move') or '').strip()}  "
                    f"_(×{m.get('occurrences', 1)})_"
                )
            parts.append("")

        rules = top_rules(gp, n=10)
        if rules:
            parts.append("## Standing rules")
            for r in rules:
                parts.append(f"- {(r.get('rule_text') or '').strip()}")
            parts.append("")

        unresolved = concerns(gp, status="latent_unresolved", n=8)
        if unresolved:
            parts.append("## Latent/unresolved concerns")
            for c in unresolved:
                tid = c.get("target_id") or ""
                nr = c.get("raised_count") or 1
                parts.append(f"- `{tid}` (raised ×{nr})")
            parts.append("")

        lb = load_bearing_decisions(gp, n=8)
        if lb:
            parts.append("## Load-bearing decisions (active)")
            for d in lb:
                parts.append(
                    f"- `{d.get('target_id')}` — referenced in "
                    f"{d.get('sessions_referenced')} sessions"
                )
            parts.append("")

        drs = drifts(gp, n=6)
        high_drifts = [d for d in drs if int(d.get("count") or 0) >= 2]
        if high_drifts:
            parts.append("## Recurring drift triggers")
            for d in high_drifts:
                parts.append(f"- `{d.get('trigger')}` — seen ×{d.get('count')}")
            parts.append("")

        ents = top_entities(gp, n=12)
        if ents:
            parts.append("## Top entities")
            parts.append("")
            parts.append("| entity | type | events |")
            parts.append("|---|---|---:|")
            for e in ents:
                parts.append(
                    f"| `{e.get('target_id')}` | {e.get('type', '?')} | {e.get('events', 0)} |"
                )
            parts.append("")

        # Temporal signals (only if present)
        still_open = [c for c in (gp.get("inference_concern_lifetime") or [])
                      if c.get("kind") == "still_open"][:5]
        if still_open:
            parts.append("## Concerns open longest")
            for c in still_open:
                parts.append(f"- `{c.get('target_id')}` — open {c.get('days')} days "
                             f"(raised ×{c.get('raised_count')})")
            parts.append("")

        resurrection = (gp.get("inference_idea_resurrection") or [])[:5]
        if resurrection:
            parts.append("## Ideas recently resurrected")
            for r in resurrection:
                parts.append(f"- `{r.get('target_id')}` — gap {r.get('gap_days')} days, "
                             f"last seen {(r.get('last_seen') or '')[:10]}")
            parts.append("")

        thrashing = [d for d in (gp.get("inference_decision_half_life") or [])
                     if d.get("thrashing")][:5]
        if thrashing:
            parts.append("## Decisions showing thrashing")
            for d in thrashing:
                parts.append(f"- `{d.get('target_id')}` — half-life "
                             f"{d.get('half_life_days')} days, events {d.get('total_events')}")
            parts.append("")

        out = "\n".join(parts)
        cap = max_tokens or self.default_max_tokens
        if approx_tokens(out) > cap:
            out = truncate_to_tokens(out, cap)
        return out
