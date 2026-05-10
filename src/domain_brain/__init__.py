"""Domain Brain researcher — per-project industry/customer/regulation helpers.

OmniGraph's Personal Brain captures *how the founder works*. Domain Brain captures
*what the project's industry requires*. For example, a real-estate
product might need regulatory coverage, buyer-persona research, an analysis of
why prior plays failed, and what changed recently that makes the new attempt viable.

Produces 7 artifacts matching Atelier's domain_brain schema (VISION.md §Domain Brain):

  industry_map.md         how the industry works, players, business models
  success_stories.md      what worked, under what conditions (temporal)
  failure_stories.md      what failed, under what conditions (temporal)
  current_conditions.md   what is true NOW — policy, competition, buyer behavior
  customer_personas.md    who the actual user is
  open_questions.md       what we don't know yet, needs human input
  viability_verdict.md    "is this worth building now?" evidence-based, honest

Operates in augment-not-replace mode: reads the existing domain_brain/,
detects gaps by scanning open_questions.md + cross-referencing with pilot
corpus mentions, and produces researcher tasks.

v0.1 (this release): structure + gap audit.
v0.2 (next): wire per-researcher Qwen prompts + web fetch.
"""
from .schemas import DomainBrainArtifact, ResearchGap, GapReport
from .researcher import audit_project_domain, list_researcher_tasks
from .writers import write_draft, draft_exists, list_pending_drafts

__all__ = [
    "DomainBrainArtifact",
    "ResearchGap",
    "GapReport",
    "audit_project_domain",
    "list_researcher_tasks",
    "write_draft",
    "draft_exists",
    "list_pending_drafts",
]
