"""Draft / history write helpers for Domain Brain artifacts.

Per omnigraph↔atelier contract Turn 6:
  - OmniGraph writes proposed updates as `<P>/domain_brain/<kind>.draft.md`.
  - Atelier's UI surfaces the draft as a banner on the authored file.
  - On accept: Atelier archives the old authored file to
    `<P>/domain_brain/history/<kind>.<iso_ts>.md`, promotes draft → authored.
  - On reject: Atelier deletes the draft.
  - OmniGraph NEVER touches `history/`.

Write operations are atomic (tmp file + rename) so Atelier's banner
detector never reads a half-written draft.
"""
from __future__ import annotations
import datetime as _dt
import os
import sys
from pathlib import Path

# Absolute path import (not relative) because this file is invoked both
# as `from domain_brain.writers import ...` and as `import writers`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import (  # type: ignore  # noqa: E402
    domain_brain_authored_path,
    domain_brain_draft_path,
    atelier_domain_brain_dir,
)

from .schemas import ARTIFACT_KINDS


def _iso_now() -> str:
    try:
        return _dt.datetime.now(_dt.UTC).replace(tzinfo=None).isoformat() + "Z"
    except AttributeError:  # pragma: no cover
        return _dt.datetime.utcnow().isoformat() + "Z"


def write_draft(
    atelier_root: Path | str,
    project: str,
    kind: str,
    body: str,
    source_model: str = "qwen/qwen3.6-35b-a3b",
    rationale: str = "",
) -> Path:
    """Atomically write <kind>.draft.md next to the authored file.

    Prepends a short YAML frontmatter block with provenance + timestamp so
    Atelier's banner can render "proposed by <model> at <ts>" without
    parsing the body.
    """
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown domain-brain artifact kind: {kind!r}")

    draft_path = domain_brain_draft_path(atelier_root, project, kind)
    draft_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter_lines = [
        "---",
        "source: omnigraph",
        f"kind: {kind}",
        f"source_model: {source_model}",
        f"produced_at: {_iso_now()}",
    ]
    if rationale:
        frontmatter_lines.append(f"rationale: {rationale!r}")

    authored = domain_brain_authored_path(atelier_root, project, kind)
    if authored.exists():
        try:
            frontmatter_lines.append(f"replaces: {authored.name}")
        except Exception:
            pass

    frontmatter_lines.append("---")
    frontmatter_lines.append("")

    final_text = "\n".join(frontmatter_lines) + body.lstrip()

    tmp = draft_path.with_suffix(draft_path.suffix + ".tmp")
    tmp.write_text(final_text)
    os.replace(tmp, draft_path)
    return draft_path


def draft_exists(atelier_root: Path | str, project: str, kind: str) -> bool:
    return domain_brain_draft_path(atelier_root, project, kind).exists()


def list_pending_drafts(atelier_root: Path | str, project: str) -> list[Path]:
    """Return all <kind>.draft.md currently waiting for Atelier's accept/reject."""
    d = atelier_domain_brain_dir(atelier_root, project)
    if not d.exists():
        return []
    return sorted(d.glob("*.draft.md"))
