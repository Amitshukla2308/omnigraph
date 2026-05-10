"""ProjectionCompiler abstract base + VaultState dataclass."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VaultState:
    """What a compiler needs to render a projection.

    Minimum: global_profile.json content. Optional: vault_dir for per-entity
    markdown, events_dir for MentionEvent stream. Compilers use what they need.
    state_dir lets layered compilers find sibling artifacts (e.g.
    rules_classified.json). project_name is set by the CLI when emitting the
    project-scoped brain.
    """
    global_profile: dict = field(default_factory=dict)
    vault_dir: Path | None = None
    events_dir: Path | None = None
    state_dir: Path | None = None
    project_name: str | None = None

    @classmethod
    def from_dir(cls, base: Path) -> "VaultState":
        """Load from an aggregate output directory (e.g. pilot/qwen/).

        Expected layout:
            <base>/global_profile.json
            <base_parent>/vault/       (optional)
            <base_parent>/events/      (optional)
        """
        base = Path(base)
        gp_path = base / "global_profile.json"
        gp: dict = {}
        if gp_path.exists():
            try:
                gp = json.loads(gp_path.read_text())
            except Exception:
                gp = {}
        parent = base.parent
        vault_dir = parent / "vault"
        events_dir = parent / "events"
        return cls(
            global_profile=gp,
            vault_dir=vault_dir if vault_dir.exists() else None,
            events_dir=events_dir if events_dir.exists() else None,
            state_dir=base,
        )


class ProjectionCompiler:
    name: str = "base"
    default_max_tokens: int = 4000

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        raise NotImplementedError


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------

def approx_tokens(text: str) -> int:
    """Cheap GPT-ish token approximation (chars / 4)."""
    return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate from the end to fit in max_tokens (approx)."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit("\n", 1)[0] + "\n"


def top_confirmed_moves(gp: dict, n: int) -> list[dict]:
    return (gp.get("confirmed_mental_moves") or [])[:n]


def top_rules(gp: dict, n: int) -> list[dict]:
    # Deduplicate by rule_text (keep first occurrence).
    seen: set[str] = set()
    out: list[dict] = []
    for r in gp.get("rules_collected") or []:
        rt = r.get("rule_text") or ""
        if not rt or rt in seen:
            continue
        seen.add(rt)
        out.append(r)
        if len(out) >= n:
            break
    return out


def concerns(gp: dict, status: str | None = None, n: int = 20) -> list[dict]:
    items = gp.get("inference_p5_concern_lifecycle") or []
    if status:
        items = [c for c in items if c.get("status") == status]
    return items[:n]


def top_entities(gp: dict, n: int) -> list[dict]:
    return (gp.get("entity_frequency_top30") or [])[:n]


def load_bearing_decisions(gp: dict, n: int) -> list[dict]:
    return [
        d for d in (gp.get("inference_p3_decision_load_bearing") or [])
        if d.get("load_class") == "load-bearing"
    ][:n]


def drifts(gp: dict, n: int) -> list[dict]:
    return (gp.get("drift_recurrence_by_trigger") or [])[:n]
