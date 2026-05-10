"""Light-IR projection — XML-tagged compact prompt-injection format.

Spec: omnigraph_product_placement/05_LIGHT_IR_OUTPUT_FORMAT.md
Default target for LLM-consumer projections (CLAUDE.md, system prompt block).
"""
from __future__ import annotations
import html
import json
from pathlib import Path

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
)

SCHEMA_V = "0.2.1"
META_BUCKETS = {"meta_user", "meta_assistant"}


def _esc(s: str) -> str:
    return html.escape((s or "").strip(), quote=True)


def _meta_rule_texts(state) -> set[str] | None:
    """Return the set of rule_texts classified as meta_user or meta_assistant.

    Reads pilot/full/rules_classified.json (produced by src/rule_classifier.py).
    Returns None if the file is missing — caller falls back to including all rules.
    """
    candidates = []
    if state and getattr(state, "state_dir", None):
        candidates.append(Path(state.state_dir) / "rules_classified.json")
    candidates.append(Path("pilot/full/rules_classified.json"))
    for p in candidates:
        if p.exists():
            try:
                d = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            return {
                r["rule_text"] for r in d.get("rules", [])
                if r.get("classification", {}).get("bucket") in META_BUCKETS
                and r.get("rule_text")
            }
    return None


@register("light_ir")
class LightIRCompiler(ProjectionCompiler):
    name = "light_ir"
    default_max_tokens = 2000

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        gp = state.global_profile or {}
        lines: list[str] = [f'<user-profile v="{SCHEMA_V}">']

        # Mental moves — confirmed only (≥2 occurrences)
        for m in top_confirmed_moves(gp, n=12):
            level = _esc(m.get("level") or "gen")
            owner = _esc(m.get("owner") or "user")
            move = _esc(m.get("move") or "")
            if move:
                lines.append(f'<mm l="{level}" o="{owner}">{move}</mm>')

        # Rules — filtered to meta-buckets if classification artifact exists.
        meta_set = _meta_rule_texts(state)
        rule_pool = top_rules(gp, n=240) if meta_set is not None else top_rules(gp, n=12)
        emitted = 0
        for r in rule_pool:
            text_raw = (r.get("rule_text") or "").strip()
            if not text_raw:
                continue
            if meta_set is not None and text_raw not in meta_set:
                continue
            lines.append(f"<rule>{_esc(text_raw)}</rule>")
            emitted += 1
            if emitted >= 12:
                break

        # Concerns — recurring first, then latent
        for c in concerns(gp, status="latent_unresolved", n=6):
            tid = _esc(c.get("target_id") or "")
            nr = int(c.get("raised_count") or 1)
            last = _esc((c.get("raised_in") or [""])[-1])
            r_kind = "recurring" if nr >= 2 else "latent"
            suffix = f" [t_last: {last}, n_raised: {nr}]" if last else f" [n_raised: {nr}]"
            lines.append(f'<concern r="{r_kind}">{tid}{suffix}</concern>')

        # Drifts — only highest-signal (count >= 2)
        for d in drifts(gp, n=6):
            cnt = int(d.get("count") or 0)
            if cnt < 2:
                continue
            trig = _esc(d.get("trigger") or "")
            lines.append(f'<drift t="{trig}">count={cnt}</drift>')

        # Load-bearing decisions
        lb = load_bearing_decisions(gp, n=6)
        if lb:
            for d in lb:
                tid = _esc(d.get("target_id") or "")
                if tid:
                    lines.append(
                        f'<decision-active tid="{tid}">refs='
                        f'{int(d.get("sessions_referenced") or 0)}</decision-active>'
                    )

        # Top entities
        top = top_entities(gp, n=10)
        if top:
            kinds = []
            for e in top:
                tid = _esc(e.get("target_id") or "")
                t = _esc(e.get("type") or "?")
                if tid:
                    kinds.append(f"{tid}:{t}")
            lines.append(f'<ent-top n="{len(kinds)}">')
            lines.append("  " + " ".join(kinds))
            lines.append("</ent-top>")

        lines.append("</user-profile>")
        out = "\n".join(lines)

        cap = max_tokens or self.default_max_tokens
        if approx_tokens(out) <= cap:
            return out

        # Token-bounded truncation: drop sections from the bottom up until we fit.
        sections_order = ["<ent-top", "<decision-active", "<drift", "<concern", "<rule", "<mm "]
        current = out
        for prefix in sections_order:
            kept = [ln for ln in current.splitlines()
                    if not any(ln.strip().startswith(p) for p in [prefix])]
            # Re-join without this tag group.
            filtered = "\n".join(kept)
            current = filtered
            if approx_tokens(current) <= cap:
                break
        return current
