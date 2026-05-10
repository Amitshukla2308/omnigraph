"""Shared helpers for the 3-layer brain compilers.

Layer split:
  global   — what's true about this user across ALL projects
  personal — how AI agents should behave WITH this user
  project  — what's true about ONE specific project (only injected for that project)

This module centralizes the rule-classification lookup and the per-project
slicing logic so the three compilers don't duplicate it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


META_USER = "meta_user"
META_ASSISTANT = "meta_assistant"
PROJECT_DOMAIN = "project_domain"
DEBUGGING_RECIPE = "debugging_recipe"


def _classification_path(state) -> Path | None:
    """Locate rules_classified.json from compiler state, falling back to default."""
    candidates = []
    if state and getattr(state, "state_dir", None):
        candidates.append(Path(state.state_dir) / "rules_classified.json")
    candidates.append(Path("pilot/full/rules_classified.json"))
    for p in candidates:
        if p.exists():
            return p
    return None


def load_classified_rules(state) -> list[dict]:
    """Return [{rule_text, applies_to, level, session, provider, classification}]."""
    p = _classification_path(state)
    if p is None:
        return []
    try:
        d = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return d.get("rules", [])


def rules_in_bucket(state, bucket: str) -> list[dict]:
    return [r for r in load_classified_rules(state)
            if r.get("classification", {}).get("bucket") == bucket]


def rules_for_project(state, project: str) -> list[dict]:
    """Project-domain rules whose scope matches this project (case-insensitive,
    substring either direction)."""
    proj = project.lower().strip()
    out = []
    for r in load_classified_rules(state):
        c = r.get("classification") or {}
        if c.get("bucket") != PROJECT_DOMAIN:
            continue
        scope = (c.get("scope") or "").lower().strip()
        text = (r.get("rule_text") or "").lower()
        if scope and (proj in scope or scope in proj):
            out.append(r)
        elif proj in text:
            out.append(r)
    return out


def discovered_project_scopes(state) -> list[str]:
    """Distinct non-empty project scopes seen in classifications. Noisy by
    design — not a canonical project list. Used by post-ETL sweep to emit
    one project artifact per scope."""
    seen = {}
    for r in load_classified_rules(state):
        c = r.get("classification") or {}
        if c.get("bucket") != PROJECT_DOMAIN:
            continue
        scope = (c.get("scope") or "").strip()
        if not scope:
            continue
        seen[scope] = seen.get(scope, 0) + 1
    # Sort descending by rule count.
    return [s for s, _ in sorted(seen.items(), key=lambda x: -x[1])]


def slug_for_project(project: str) -> str:
    """Filesystem-safe slug for a project name."""
    return project.lower().strip().replace(" ", "-").replace("/", "-")


def vault_summary_for_entity(target_id: str) -> str | None:
    """Return the 1-line 'What:' from an enriched Vault page, if present."""
    page = Path("pilot/vault") / f"{target_id}.md"
    if not page.exists():
        return None
    try:
        body = page.read_text()
    except OSError:
        return None
    if "## Semantic summary" not in body:
        return None
    block = body.split("## Semantic summary", 1)[1]
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("**What:**"):
            return line.replace("**What:**", "").strip()
    return None


def project_related_entities(gp: dict, project: str) -> list[dict]:
    """Entities from entity_frequency_top30 whose target_id contains the project name."""
    proj = project.lower().strip()
    out = []
    for e in gp.get("entity_frequency_top30") or []:
        tid = (e.get("target_id") or "").lower()
        if proj in tid or tid in proj:
            out.append(e)
    return out


def project_decisions(gp: dict, project: str) -> list[dict]:
    """Load-bearing decisions whose related_entities mention the project."""
    proj = project.lower().strip()
    out = []
    for d in gp.get("inference_p3_decision_load_bearing") or []:
        rels = [str(x).lower() for x in (d.get("related_entities") or [])]
        if any(proj in r or r in proj for r in rels):
            out.append(d)
    return out
