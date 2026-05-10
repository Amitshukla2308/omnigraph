"""Per-project Domain Brain auditor.

v0.1 (this release) — gap audit only. Reads existing Atelier domain_brain/
files, reports what's there, what's thin, what's missing. Flags each missing
artifact as a ResearchGap with a researcher plan.

v0.2 (next) — wire the Qwen-backed per-artifact researcher stubs in
`researcher_tasks` to actually produce drafts, pulling context from:
  - the project's meta.json (product thesis)
  - the Personal Brain's top_entities (what the founder already talks about)
  - the pilot corpus (cross-referenced mentions of this project's concepts)
  - web sources (for regulatory / competitive context, opt-in)

Keeps augment-not-replace semantics: existing founder-authored artifacts are
never overwritten. Researcher output lands as `*.draft.md` alongside the
original, for founder review + merge.
"""
from __future__ import annotations
import datetime as _dt
import os
import re
from pathlib import Path

from .schemas import ARTIFACT_KINDS, DomainBrainArtifact, GapReport, ResearchGap


MIN_SUBSTANTIVE_LINES = 8   # below this, artifact counts as "stub"
STALE_THRESHOLD_DAYS = 30   # artifact not touched this long while peers moved

# ---- researcher task specs (used by v0.2) -----------------------------------

RESEARCHER_TASKS: dict[str, dict] = {
    "industry_map": {
        "goal": "Produce a 1-page map of how this industry works: key players, "
                "business models, incumbents, new entrants, disruption vectors.",
        "needs_web": True,
        "needs_pilot_corpus": False,
        "expected_sections": ["Landscape", "Players", "Business models", "Disruption vectors"],
    },
    "current_conditions": {
        "goal": "Capture what is true NOW (YYYY-MM-DD context-stamped): policy, "
                "competition, buyer behavior. Time-sensitive by design.",
        "needs_web": True,
        "needs_pilot_corpus": True,  # scan for recent mentions
        "expected_sections": ["Regulation", "Market", "Competition", "Buyer behavior"],
    },
    "customer_personas": {
        "goal": "Name the 1-3 real personas. Age, income, channel, decision moment, "
                "fears, substitutes. Not buyer generics — specific humans.",
        "needs_web": False,
        "needs_pilot_corpus": True,  # founder has described these in sessions
        "expected_sections": ["Primary persona", "Secondary personas"],
    },
    "success_stories": {
        "goal": "What worked, under what conditions, why. Temporal — prior "
                "successes and what made them work at that time.",
        "needs_web": True,
        "needs_pilot_corpus": False,
        "expected_sections": ["Precedents", "Why they worked", "Timing"],
    },
    "failure_stories": {
        "goal": "What failed at this problem before, what killed each one. "
                "Critical for anti-patterns.",
        "needs_web": True,
        "needs_pilot_corpus": False,
        "expected_sections": ["Failed attempts", "Cause of death", "Lessons"],
    },
    "open_questions": {
        "goal": "Produce the list of questions only a human can answer. Seed the "
                "founder's homework.",
        "needs_web": False,
        "needs_pilot_corpus": True,
        "expected_sections": ["Market questions", "Regulatory questions", "Customer questions"],
    },
    "viability_verdict": {
        "goal": "Synthesize all six prior artifacts into an evidence-based "
                "'is this worth building now?' verdict. Last gate before build.",
        "needs_web": False,
        "needs_pilot_corpus": False,
        "expected_sections": ["Verdict", "Confidence", "What would change my mind"],
    },
}


def list_researcher_tasks() -> dict:
    """Public: return the researcher task specs."""
    return dict(RESEARCHER_TASKS)


# ---- gap audit (v0.1 implementation) ---------------------------------------

def _inspect_artifact(kind: str, root: Path) -> DomainBrainArtifact:
    p = root / f"{kind}.md"
    if not p.exists():
        return DomainBrainArtifact(kind=kind, path=str(p), exists=False)
    raw = p.read_text(errors="replace")
    lines = raw.splitlines()
    stat = p.stat()
    # Founder-authored heuristic: presence of first-person "I " / "my " early
    head = "\n".join(lines[:20]).lower()
    founder_voice = bool(re.search(r"\b(i | my | we |amit)\b", head))
    # Summary: first non-title / non-empty line
    summary = ""
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        summary = s[:160]
        break
    return DomainBrainArtifact(
        kind=kind,
        path=str(p),
        exists=True,
        size_bytes=stat.st_size,
        line_count=len(lines),
        last_modified=_dt.datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
        founder_authored=founder_voice,
        summary=summary,
    )


def _detect_stale(artifacts: list[DomainBrainArtifact]) -> None:
    """Mark stale = 30+ days older than the newest peer."""
    existing = [a for a in artifacts if a.exists and a.last_modified]
    if len(existing) < 2:
        return
    ts_of = lambda a: _dt.datetime.fromisoformat(a.last_modified.replace("Z", "+00:00")).timestamp()
    newest = max(ts_of(a) for a in existing)
    threshold = newest - (STALE_THRESHOLD_DAYS * 86400)
    for a in existing:
        if ts_of(a) < threshold:
            a.stale = True


def _gaps_from_artifacts(artifacts: list[DomainBrainArtifact]) -> list[ResearchGap]:
    gaps: list[ResearchGap] = []
    for a in artifacts:
        task = RESEARCHER_TASKS.get(a.kind, {})
        goal = task.get("goal", "")
        if not a.exists:
            sev = "blocker" if a.kind == "viability_verdict" else "high"
            gaps.append(ResearchGap(
                artifact=a.kind,
                question=f"{a.kind}.md is missing. {goal}",
                severity=sev,
                source="missing",
                proposed_research=f"Generate a first draft of {a.kind}.md via OmniGraph researcher (v0.2).",
            ))
            continue
        if a.line_count < MIN_SUBSTANTIVE_LINES:
            gaps.append(ResearchGap(
                artifact=a.kind,
                question=f"{a.kind}.md exists but only {a.line_count} lines — treat as stub.",
                severity="medium",
                source=f"{a.path}:1",
                proposed_research=f"Expand {a.kind}.md: {goal}",
            ))
        if a.stale:
            gaps.append(ResearchGap(
                artifact=a.kind,
                question=f"{a.kind}.md hasn't moved while peers have ({a.last_modified}).",
                severity="low",
                source=f"{a.path}:mtime",
                proposed_research=f"Refresh {a.kind}.md against current conditions.",
            ))
    return gaps


def _coverage_score(artifacts: list[DomainBrainArtifact]) -> float:
    weight = {
        "viability_verdict": 0.30,
        "current_conditions": 0.20,
        "customer_personas": 0.15,
        "industry_map": 0.10,
        "open_questions": 0.10,
        "success_stories": 0.075,
        "failure_stories": 0.075,
    }
    total = 0.0
    for a in artifacts:
        w = weight.get(a.kind, 0.0)
        if not a.exists:
            continue
        # Partial credit for stubs
        if a.line_count < MIN_SUBSTANTIVE_LINES:
            total += w * 0.25
        else:
            total += w
    return total


def audit_project_domain(atelier_project_root: str | Path) -> GapReport:
    """Scan atelier/projects/<P>/domain_brain/ and report gaps.

    Returns a JSON-serializable GapReport. Safe to run read-only; writes nothing.
    """
    root = Path(atelier_project_root)
    db_root = root / "domain_brain"
    if not db_root.exists():
        return GapReport(
            project=root.name,
            domain_brain_root=str(db_root),
            next_action=(
                f"domain_brain/ not found under {root}. Create the directory, "
                f"or let OmniGraph v0.2 scaffold the 7-file layout from project meta.json."
            ),
        )

    artifacts = [_inspect_artifact(k, db_root) for k in ARTIFACT_KINDS]
    _detect_stale(artifacts)
    gaps = _gaps_from_artifacts(artifacts)
    score = _coverage_score(artifacts)

    if score >= 0.85:
        next_action = "Domain brain is substantive. Focus on keeping current_conditions fresh."
    elif score >= 0.5:
        next_action = f"Domain brain is partial (score {score:.2f}). {len(gaps)} gaps to close."
    elif score > 0.0:
        next_action = f"Domain brain is a stub (score {score:.2f}). Priority: viability_verdict."
    else:
        next_action = "Domain brain empty. Bootstrap needed."

    return GapReport(
        project=root.name,
        domain_brain_root=str(db_root),
        artifacts=artifacts,
        gaps=gaps,
        coverage_score=score,
        next_action=next_action,
    )
