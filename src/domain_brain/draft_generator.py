"""Domain Brain draft generator (TODO-6, minimal build).

For a given Atelier project + lens (industry_map / customer_personas / etc.),
gather:
  - canvas node intent text (project description from nodes/*/meta.json)
  - relevant global_profile slices (rules, recurring concerns, mental moves
    that touch the project's named entities)
  - existing reference artifact (if a hand-authored .md exists, used as
    style/structure prior — NOT overwritten)

Pass to Qwen with a lens template. Output `<kind>.draft.md` under the
project's domain_brain/ dir, never overwriting `<kind>.md`.

Usage:
    python -m domain_brain.draft_generator <project_root> <lens_kind>
    e.g. ... ~/atelier/projects/MyProject industry_map

GPU lock priority 3 (founder waiting in approval queue).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

PILOT = ROOT / "pilot"
GLOBAL_PROFILE = PILOT / "full" / "global_profile.json"

# Lens specifications: each kind has a focus + framing instructions.
LENS_SPECS = {
    "industry_map": {
        "title": "Industry Map",
        "focus": "How the industry/domain works: market scale, key players, deal structure, friction points.",
        "structure": "## How the industry works\n## Key players (table)\n## Friction points\n## Where this project fits",
    },
    "customer_personas": {
        "title": "Customer Personas",
        "focus": "Who the user is: demographics, jobs-to-be-done, current workarounds, why they'd switch.",
        "structure": "## Primary persona\n## Secondary persona (if any)\n## Jobs-to-be-done\n## Current workarounds & their pain",
    },
    "current_conditions": {
        "title": "Current Conditions",
        "focus": "What's true RIGHT NOW that creates the opportunity: regulation, tech maturity, market shifts.",
        "structure": "## Macro shifts\n## Regulatory state\n## Tech enablers\n## Why this window matters",
    },
    "viability_verdict": {
        "title": "Viability Verdict",
        "focus": "Honest assessment: is this real? what would have to be true for it to work? red flags.",
        "structure": "## Verdict (one line)\n## Why it could work\n## Why it might not\n## Open questions blocking conviction",
    },
    "open_questions": {
        "title": "Open Questions",
        "focus": "What we don't know yet — research, validation, or experiments needed before next decision.",
        "structure": "## Validation gaps\n## Research needed\n## Experiments to run\n## What I'd want to know in 30 days",
    },
}


# ----------------------------------------------------------------------
# context loaders
# ----------------------------------------------------------------------

def load_canvas_intent(project_root: Path) -> str:
    """Concatenate all canvas node intents into a project description."""
    nodes_dir = project_root / "canvas" / "nodes"
    if not nodes_dir.exists():
        return ""
    parts = []
    for d in sorted(nodes_dir.iterdir()):
        if not d.is_dir():
            continue
        meta = d / "meta.json"
        if not meta.exists():
            continue
        try:
            m = json.loads(meta.read_text())
        except json.JSONDecodeError:
            continue
        kind = m.get("kind", "?")
        title = m.get("title", "?")
        intent = m.get("intent", "")
        if intent:
            parts.append(f"### {kind}: {title}\n{intent[:1500]}")
    return "\n\n".join(parts)


def load_relevant_profile_slices(project_name: str) -> dict:
    """Pull global_profile slices that name this project's entities."""
    if not GLOBAL_PROFILE.exists():
        return {}
    d = json.loads(GLOBAL_PROFILE.read_text())
    name_lower = project_name.lower()
    slices = {
        "top_entities_matching": [],
        "rules_matching": [],
        "concerns_matching": [],
        "mental_moves": d.get("confirmed_mental_moves", [])[:5],
    }
    for e in d.get("entity_frequency_top30", []):
        tid = (e.get("target_id") or "").lower()
        if name_lower in tid or tid in name_lower:
            slices["top_entities_matching"].append(e)
    rules = d.get("rules_collected", []) or d.get("rules", [])
    for r in rules:
        if not isinstance(r, dict):
            continue
        text = (r.get("rule") or r.get("text") or "").lower()
        if name_lower in text:
            slices["rules_matching"].append(r)
    concerns = d.get("inference_p5_concern_lifecycle", []) or []
    for c in concerns[:50]:
        if name_lower in json.dumps(c).lower():
            slices["concerns_matching"].append(c)
    return slices


def load_reference_md(project_root: Path, kind: str) -> str:
    """If an existing hand-authored <kind>.md exists, return its first 2KB
    as a structure/style hint (NOT to be copied)."""
    p = project_root / "domain_brain" / f"{kind}.md"
    if not p.exists():
        return ""
    return p.read_text()[:2000]


# ----------------------------------------------------------------------
# prompt + draft
# ----------------------------------------------------------------------

def build_prompt(project_name: str, kind: str, canvas_text: str,
                 profile_slices: dict, reference_text: str) -> tuple[str, str]:
    spec = LENS_SPECS[kind]
    system = (
        f"You are drafting a {spec['title']} for a founder's project. "
        f"Focus: {spec['focus']} "
        f"Use this exact structure:\n{spec['structure']}\n"
        "Output is markdown. Do NOT prefix with code fences. "
        "Be concrete and specific (numbers, names, real examples). "
        "If the input does not give you enough signal for a section, "
        "write 'TODO: needs founder input' under that header — never invent facts."
    )
    user_parts = [
        f"# Project: {project_name}",
        "",
        "## Canvas (project description)",
        canvas_text or "(no canvas content)",
    ]
    if profile_slices.get("top_entities_matching"):
        user_parts += ["", "## Related entities from cross-session profile"]
        for e in profile_slices["top_entities_matching"]:
            user_parts.append(
                f"- {e.get('target_id')} ({e.get('events')} events across {e.get('providers')})"
            )
    if profile_slices.get("rules_matching"):
        user_parts += ["", "## Founder rules touching this project"]
        for r in profile_slices["rules_matching"][:8]:
            user_parts.append(f"- {r.get('rule') or r.get('text','?')}")
    if reference_text:
        user_parts += [
            "", "## Existing hand-authored version (style reference ONLY — do not copy)",
            reference_text,
        ]
    user_parts += ["", f"Now produce the {spec['title']} markdown."]
    user = "\n".join(user_parts)
    return system, user


def draft_one(project_root: Path, kind: str, dry_run: bool = False) -> dict:
    if kind not in LENS_SPECS:
        return {"status": "unknown_kind", "kind": kind, "valid": list(LENS_SPECS)}
    project_name = project_root.name
    canvas_text = load_canvas_intent(project_root)
    profile_slices = load_relevant_profile_slices(project_name)
    reference_text = load_reference_md(project_root, kind)
    system, user = build_prompt(project_name, kind, canvas_text, profile_slices, reference_text)

    if dry_run:
        return {
            "status": "dry_run",
            "prompt_chars": len(system) + len(user),
            "canvas_chars": len(canvas_text),
            "profile_matches": {k: len(v) if isinstance(v, list) else 0
                                 for k, v in profile_slices.items()},
            "reference_present": bool(reference_text),
        }

    from gpu_lock import gpu_lock
    from qwen_pipeline import qwen_call
    t0 = time.time()
    with gpu_lock(holder="domain_brain_draft", priority=3):
        resp = qwen_call(system, user, max_tokens=8192)
    elapsed = time.time() - t0

    content = (resp.get("content") or "").strip()
    if not content:
        return {"status": "empty_content", "elapsed_s": round(elapsed, 1),
                "finish_reason": resp.get("finish_reason"),
                "reasoning_chars": len(resp.get("reasoning", ""))}

    out_path = project_root / "domain_brain" / f"{kind}.draft.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"---\n"
        f"produced_by: omnigraph/domain-brain (qwen-3.6-35b-a3b)\n"
        f"produced_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"lens: {kind}\n"
        f"project: {project_name}\n"
        f"reference_present: {bool(reference_text)}\n"
        f"---\n\n"
    )
    out_path.write_text(header + content)
    return {
        "status": "drafted",
        "elapsed_s": round(elapsed, 1),
        "out_path": str(out_path),
        "chars": len(content),
    }


def main():
    if len(sys.argv) < 3:
        print("usage: python -m domain_brain.draft_generator <project_root> <lens_kind> [--dry-run]",
              file=sys.stderr)
        return 2
    proj = Path(sys.argv[1]).expanduser().resolve()
    kind = sys.argv[2]
    dry = "--dry-run" in sys.argv
    r = draft_one(proj, kind, dry_run=dry)
    print(json.dumps(r, indent=2, default=str))
    return 0 if r.get("status") in ("drafted", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
