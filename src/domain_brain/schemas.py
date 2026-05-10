"""Schemas for Domain Brain artifacts + gap-audit reports."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

# The 7 artifact kinds Atelier expects. Order matters — display order in UI.
ARTIFACT_KINDS: tuple[str, ...] = (
    "industry_map",
    "current_conditions",
    "customer_personas",
    "success_stories",
    "failure_stories",
    "open_questions",
    "viability_verdict",
)

ArtifactKind = Literal[
    "industry_map", "current_conditions", "customer_personas",
    "success_stories", "failure_stories", "open_questions", "viability_verdict",
]


@dataclass
class DomainBrainArtifact:
    """One domain-brain file's current state."""
    kind: ArtifactKind
    path: str                 # absolute path under projects/<P>/domain_brain/
    exists: bool
    size_bytes: int = 0
    line_count: int = 0
    last_modified: str = ""   # ISO timestamp, empty if missing
    stale: bool = False       # heuristic: hasn't been touched in >30d while other files have
    founder_authored: bool = False  # heuristic: first-person voice at top
    summary: str = ""          # first H2 or opening paragraph


@dataclass
class ResearchGap:
    """One identified gap — something the researcher should fill in."""
    artifact: ArtifactKind
    question: str             # the specific thing unknown
    severity: Literal["blocker", "high", "medium", "low"]
    source: str               # where the gap was detected (file + line, or "missing")
    proposed_research: str    # what the researcher would do to close it
    estimated_cost: Literal["cheap", "moderate", "expensive"] = "moderate"


@dataclass
class GapReport:
    """Output of audit_project_domain()."""
    project: str
    domain_brain_root: str
    artifacts: list[DomainBrainArtifact] = field(default_factory=list)
    gaps: list[ResearchGap] = field(default_factory=list)
    coverage_score: float = 0.0      # 0..1, fraction of artifacts present with substantive content
    next_action: str = ""             # one-line recommendation

    def to_json(self) -> dict:
        return {
            "project": self.project,
            "domain_brain_root": self.domain_brain_root,
            "artifacts": [a.__dict__ for a in self.artifacts],
            "gaps": [g.__dict__ for g in self.gaps],
            "coverage_score": round(self.coverage_score, 3),
            "next_action": self.next_action,
        }
