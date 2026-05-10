"""Atelier PTY source adapter — stub pending Atelier-side session-dump format.

CONTRACT (draft, 2026-04-24):
  Atelier will emit per-session JSON files at a location TBD (candidate:
  `~/.atelier/sessions/<session_id>.json`). The file contains a structured
  event log already segmented into user/assistant turns, plus per-turn
  tool-call records. Because Atelier controls the capture layer, the data
  is expected to be near-canonical — no provider-specific flattening
  needed.

  Required session fields:
    - session_id:   stable unique id (e.g. ULID or the project-scoped ULID)
    - started_at:   ISO timestamp
    - project_slug: canonical project id (feeds canonical_slugs)
    - turns: [
        {
          index: int,
          role: "user" | "assistant",
          timestamp: ISO8601,
          text: str,
          thinking: str,        # if available
          tool_calls: [ { name, input } ],
        }, ...
      ]

  This adapter's responsibility is limited to:
    - Enumerate files in the Atelier session dir (env var ATELIER_SESSIONS_DIR
      or default path).
    - Parse each file into a NormalizedSession.
    - Copy project_slug into NormalizedSession.meta so Phase 2 Vault
      materialization can reuse it as a canonical target.

Until Atelier lands its emit path, this adapter is a no-op (yields nothing
when the directory is empty / missing). Pipeline keeps working against
the historical multi-provider adapters.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Iterator

from . import register
from .base import NormalizedSession, SourceAdapter


DEFAULT_SESSIONS_DIR = Path(os.path.expanduser("~/.atelier/sessions"))


@register("atelier_pty")
class AtelierPtyAdapter(SourceAdapter):
    name = "atelier_pty"

    def __init__(self, sessions_dir: Path | None = None) -> None:
        env = os.environ.get("ATELIER_SESSIONS_DIR")
        self.sessions_dir = (
            Path(sessions_dir) if sessions_dir
            else Path(env) if env
            else DEFAULT_SESSIONS_DIR
        )

    def iter_sessions(self) -> Iterator[NormalizedSession]:
        if not self.sessions_dir.exists():
            return
        for p in sorted(self.sessions_dir.glob("*.json")):
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            sid = d.get("session_id") or p.stem
            if not isinstance(sid, str) or not sid:
                continue
            turns = d.get("turns") or []
            if not isinstance(turns, list):
                turns = []
            yield NormalizedSession(
                session_id=sid,
                provider=self.name,
                source_path=str(p),
                input_type="dialog",
                turns=[t for t in turns if isinstance(t, dict)],
                meta={
                    "project_slug": d.get("project_slug"),
                    "started_at": d.get("started_at"),
                },
            )

    def session_id(self, raw_handle) -> str:
        if isinstance(raw_handle, Path):
            return raw_handle.stem
        if isinstance(raw_handle, dict):
            sid = raw_handle.get("session_id")
            if isinstance(sid, str):
                return sid
        return str(raw_handle)
