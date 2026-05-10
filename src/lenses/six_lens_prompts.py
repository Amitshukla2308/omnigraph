"""Prompt templates for each of the 6 synthesis lenses.

Each lens follows the same contract:
  Input:   session_brief — a structured summary of the already-extracted
           per-session objects (decisions, concerns, rules, drifts, etc.).
  Output:  ~150-200 words of grounded, claim-level perspective from that
           viewpoint. No jargon. No filler. Quote the evidence when making
           a specific claim.
"""
from __future__ import annotations


LENS_ORDER: tuple[str, ...] = (
    "engineer",
    "architect",
    "strategist",
    "economist",
    "scientist",
    "product",
)


_LENS_SYSTEMS: dict[str, str] = {
    "engineer": (
        "You are the Engineer lens. You read a session's extracted objects and "
        "comment on what was built, broken, fixed, or left half-done at the code level. "
        "Concrete. Short. Name specific tools, commands, errors, and fixes from the "
        "evidence. If nothing engineering-substantive happened, say so briefly — do not "
        "pad. Never speculate about code you cannot see. 150-200 words."
    ),
    "architect": (
        "You are the Architect lens. You comment on structure — what boundaries were "
        "tested, what contracts moved, what layers got clearer or muddier. Identify any "
        "load-bearing decisions and their consequences. If the session had no structural "
        "implications, say that plainly. 150-200 words."
    ),
    "strategist": (
        "You are the Strategist lens. You read the session through the lens of "
        "timing, leverage, and sequencing. What did this session advance toward the "
        "founder's stated goals? What did it park or drift away from? Name trade-offs "
        "the founder navigated. 150-200 words."
    ),
    "economist": (
        "You are the Economist lens. You comment on cost, ROI, and opportunity cost "
        "signals. Token burn, GPU time, founder attention, decisions deferred vs paid-down. "
        "Was the session a net investment, maintenance, or spend? Cite the evidence. "
        "150-200 words."
    ),
    "scientist": (
        "You are the Scientist lens. You read the session as a series of hypotheses "
        "and tests. What did the founder propose? What was tested? What was learned? "
        "What remains untested? If a claim was made without evidence, flag it. "
        "150-200 words."
    ),
    "product": (
        "You are the Product lens. You comment on user-journey progress, scope drift, "
        "and what a real external user would have experienced if they touched this work "
        "today. Was a real user journey advanced, or was this plumbing? Be honest. "
        "150-200 words."
    ),
}


def _format_list(title: str, items: list | None, render_item) -> str:
    if not items:
        return ""
    lines = [f"\n**{title}:**"]
    for it in items[:12]:
        try:
            rendered = render_item(it)
            if rendered:
                lines.append(f"- {rendered}")
        except Exception:
            continue
    return "\n".join(lines) + "\n"


def render_session_brief(extracted: dict) -> str:
    """Convert a per-session extraction (output of qwen_pipeline.run_session)
    into a compact human/LLM-readable brief suitable for feeding into a lens prompt.
    """
    brief_parts: list[str] = [
        f"# Session brief",
        f"",
        f"- session_id: {extracted.get('session_id', '?')}",
        f"- provider: {extracted.get('provider', '?')}",
        f"- extractor: {extracted.get('extractor', '?')}",
    ]

    meta = extracted.get("session_meta") or {}
    if meta.get("timestamp_start"):
        brief_parts.append(f"- timestamp_start: {meta['timestamp_start']}")
    if meta.get("input_type"):
        brief_parts.append(f"- input_type: {meta['input_type']}")

    brief_parts.append("")

    brief_parts.append(_format_list(
        "Decisions",
        extracted.get("decisions"),
        lambda d: f"**{d.get('decision') or d.get('text', '')[:80]}** — why: {(d.get('why') or '')[:120]} [{d.get('target_id', '?')}]",
    ))
    brief_parts.append(_format_list(
        "Concerns raised",
        [c for c in (extracted.get("mention_events") or []) if isinstance(c, dict) and (c.get("mention_type") or "").startswith("concern")],
        lambda c: f"{c.get('mention_type')}: {c.get('target_id')} — {(c.get('evidence_quote') or '')[:120]}",
    ))
    brief_parts.append(_format_list(
        "Rules",
        extracted.get("rules"),
        lambda r: f"{r.get('rule_text', '')[:160]} (applies_to: {r.get('applies_to', '?')})",
    ))
    brief_parts.append(_format_list(
        "Mental moves",
        extracted.get("mental_moves"),
        lambda m: f"[{m.get('owner', '?')}/{m.get('level', '?')}] {(m.get('move') or '')[:160]}",
    ))
    brief_parts.append(_format_list(
        "Drifts",
        extracted.get("drifts"),
        lambda d: f"{d.get('trigger', '?')}: {(d.get('proposed') or '')[:80]} → {(d.get('corrected_to') or '')[:80]}",
    ))
    brief_parts.append(_format_list(
        "Affect",
        extracted.get("affect"),
        lambda a: f"{a.get('valence', '?')} [{a.get('trigger', '?')}]",
    ))
    brief_parts.append(_format_list(
        "Top entities mentioned",
        extracted.get("mention_events"),
        lambda e: f"{e.get('target_id')} ({e.get('mention_type')}/{e.get('valence')})",
    ))

    return "\n".join(brief_parts)


def lens_prompt(lens: str, session_brief: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for Qwen.

    Raises KeyError for unknown lens names.
    """
    lens = lens.lower()
    if lens not in _LENS_SYSTEMS:
        raise KeyError(f"unknown lens: {lens!r}; valid: {LENS_ORDER}")

    system = _LENS_SYSTEMS[lens]
    user = (
        "Below is the structured brief of a single AI-coding session. "
        "Respond with only your lens's perspective as a single markdown block "
        "starting with an H3 header (e.g. `### Engineer`). Cite evidence from "
        "the brief when making specific claims. 150-200 words.\n\n"
        f"{session_brief}"
    )
    return system, user


# Public mapping for callers that want to iterate lenses in canonical order.
LENSES: tuple[str, ...] = LENS_ORDER
