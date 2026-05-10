"""Path resolution for OmniGraph outputs under Atelier's filesystem.

Single source of truth for where compiled artifacts, raw events, and Vault
pages land. Callers pass:
    atelier_root — absolute path to ~/atelier/ (parent of projects/, users/, data/)
    user_id      — atelier_user_id (SQLite users.id UUID; "default" in Phase A)
    project      — project slug when needed (e.g., "MyProject")

When `atelier_root` is None, callers fall back to the local pilot/ tree
(legacy single-user pilot mode). This keeps the pilot corpus runnable
without Atelier installed.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional


DEFAULT_USER_ID = "default"


# ----------------------------------------------------------------------
# Canonical Atelier-scoped paths (what OmniGraph writes to)
# ----------------------------------------------------------------------

def atelier_user_root(atelier_root: Path | str, user_id: str) -> Path:
    """~/atelier/data/users/<uid>/

    Canonical user-scoped root per Atelier's live HOME-isolation layout
    (PTY spawns with HOME=data/users/<uuid>/). All user-scoped subtrees
    — .claude/, data/events/, brain/personal/ — live underneath.
    """
    return Path(atelier_root) / "data" / "users" / (user_id or DEFAULT_USER_ID)


def atelier_events_dir(atelier_root: Path | str, user_id: str) -> Path:
    """~/atelier/users/<uid>/data/events/

    Canonical home for raw per-month MentionEvent JSONL. User-scoped;
    each record carries `project: <slug>` for downstream project-view
    filtering.
    """
    return atelier_user_root(atelier_root, user_id) / "data" / "events"


def atelier_personal_brain_dir(atelier_root: Path | str, user_id: str) -> Path:
    """~/atelier/users/<uid>/brain/personal/

    OmniGraph-owned subtree. Contains: _meta.json, global_profile.json,
    compiled/*, entities/*, events/index.json (compiled index only — raw
    JSONL lives under data/events/), graph/* (optional).
    """
    return atelier_user_root(atelier_root, user_id) / "brain" / "personal"


def atelier_personal_brain_compiled_dir(atelier_root: Path | str, user_id: str) -> Path:
    return atelier_personal_brain_dir(atelier_root, user_id) / "compiled"


def atelier_personal_brain_entities_dir(atelier_root: Path | str, user_id: str) -> Path:
    return atelier_personal_brain_dir(atelier_root, user_id) / "entities"


def atelier_personal_brain_events_index(atelier_root: Path | str, user_id: str) -> Path:
    """The COMPILED event index (not raw JSONL). target_id → [(ts, file, line), ...]"""
    return atelier_personal_brain_dir(atelier_root, user_id) / "events" / "index.json"


def atelier_personal_brain_graph_dir(atelier_root: Path | str, user_id: str) -> Path:
    """Optional HR structural signals (cochange / communities / criticality)."""
    return atelier_personal_brain_dir(atelier_root, user_id) / "graph"


def atelier_project_root(atelier_root: Path | str, project: str) -> Path:
    """~/atelier/projects/<Project>/"""
    return Path(atelier_root) / "projects" / project


def atelier_domain_brain_dir(atelier_root: Path | str, project: str) -> Path:
    """~/atelier/projects/<P>/domain_brain/

    Project-scoped, shared across all users in the project. OmniGraph's
    Domain Brain researcher writes drafts and history here.
    """
    return atelier_project_root(atelier_root, project) / "domain_brain"


def atelier_domain_brain_history_dir(atelier_root: Path | str, project: str) -> Path:
    """~/atelier/projects/<P>/domain_brain/history/

    Atelier writes on accept (moving old authored file aside); OmniGraph
    never touches this dir.
    """
    return atelier_domain_brain_dir(atelier_root, project) / "history"


def atelier_sessions_dir(atelier_root: Path | str, project: str) -> Path:
    """~/atelier/projects/<P>/sessions/ — Atelier's reflection artifacts."""
    return atelier_project_root(atelier_root, project) / "sessions"


def atelier_session_raw_dir(atelier_root: Path | str, sid: str) -> Path:
    """~/atelier/data/sessions/<sid>/ — raw PTY logs."""
    return Path(atelier_root) / "data" / "sessions" / sid


# ----------------------------------------------------------------------
# Legacy fallback: pilot/ tree (single-user local dev)
# ----------------------------------------------------------------------

PILOT_ROOT = Path(__file__).resolve().parent.parent / "pilot"


def resolve_events_dir(atelier_root: Optional[Path | str], user_id: str) -> Path:
    """Return the events output dir — atelier if given, else pilot/events/."""
    if atelier_root:
        return atelier_events_dir(atelier_root, user_id)
    return PILOT_ROOT / "events"


def resolve_personal_brain_dir(atelier_root: Optional[Path | str], user_id: str) -> Path:
    if atelier_root:
        return atelier_personal_brain_dir(atelier_root, user_id)
    return PILOT_ROOT  # legacy: qwen / vault / global_profile live directly under pilot/


def resolve_vault_dir(atelier_root: Optional[Path | str], user_id: str) -> Path:
    if atelier_root:
        return atelier_personal_brain_entities_dir(atelier_root, user_id)
    return PILOT_ROOT / "vault"


def resolve_compiled_dir(atelier_root: Optional[Path | str], user_id: str) -> Path:
    if atelier_root:
        return atelier_personal_brain_compiled_dir(atelier_root, user_id)
    return PILOT_ROOT / "compiled"


def resolve_global_profile_path(atelier_root: Optional[Path | str], user_id: str) -> Path:
    if atelier_root:
        return atelier_personal_brain_dir(atelier_root, user_id) / "global_profile.json"
    return PILOT_ROOT / "qwen" / "global_profile.json"


def resolve_meta_path(atelier_root: Optional[Path | str], user_id: str) -> Path:
    if atelier_root:
        return atelier_personal_brain_dir(atelier_root, user_id) / "_meta.json"
    return PILOT_ROOT / "qwen" / "_meta.json"


def resolve_graph_dir(atelier_root: Optional[Path | str], user_id: str) -> Path:
    if atelier_root:
        return atelier_personal_brain_graph_dir(atelier_root, user_id)
    return PILOT_ROOT / "hr_out"


def resolve_hr_events_jsonl_month(atelier_root: Optional[Path | str], user_id: str, ym: str) -> Path:
    """Raw MentionEvent JSONL for a specific YYYY-MM."""
    return resolve_events_dir(atelier_root, user_id) / f"{ym}.jsonl"


def resolve_events_index_path(atelier_root: Optional[Path | str], user_id: str) -> Path:
    """Compiled event index (under brain/, not data/)."""
    if atelier_root:
        return atelier_personal_brain_events_index(atelier_root, user_id)
    return PILOT_ROOT / "events" / "index.json"


# ----------------------------------------------------------------------
# Draft / history conventions for Domain Brain
# ----------------------------------------------------------------------

def domain_brain_draft_path(atelier_root: Path | str, project: str, kind: str) -> Path:
    """<P>/domain_brain/<kind>.draft.md — OmniGraph writes; Atelier surfaces banner."""
    return atelier_domain_brain_dir(atelier_root, project) / f"{kind}.draft.md"


def domain_brain_authored_path(atelier_root: Path | str, project: str, kind: str) -> Path:
    """<P>/domain_brain/<kind>.md — authored/accepted version."""
    return atelier_domain_brain_dir(atelier_root, project) / f"{kind}.md"


def domain_brain_history_path(atelier_root: Path | str, project: str, kind: str, iso_ts: str) -> Path:
    """<P>/domain_brain/history/<kind>.<iso_ts>.md — archive path (Atelier writes)."""
    return atelier_domain_brain_history_dir(atelier_root, project) / f"{kind}.{iso_ts}.md"
