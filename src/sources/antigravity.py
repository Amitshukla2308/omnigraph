"""Antigravity — artifact-based sessions (md files under brain/<uuid>/)."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Iterator

from . import register
from .base import NormalizedSession, SourceAdapter

AI_CONV = Path(os.environ.get("OMNIGRAPH_AI_CONV", str(Path.home() / "ai_conversations")))
SRC_ROOT = AI_CONV / "Google_Antigravity" / "brain"


@register("antigravity")
class AntigravityAdapter(SourceAdapter):
    name = "antigravity"

    def iter_sessions(self) -> Iterator[NormalizedSession]:
        if not SRC_ROOT.exists():
            return
        for sess_dir in sorted(SRC_ROOT.iterdir()):
            if not sess_dir.is_dir():
                continue
            artifacts: list[dict] = []
            for p in sorted(sess_dir.iterdir()):
                if p.suffix != ".md":
                    continue
                try:
                    content = p.read_text(errors="replace")[:15000]
                except Exception:
                    continue
                artifacts.append({
                    "filename": p.name,
                    "content": content,
                    "size": p.stat().st_size,
                })
            if not artifacts:
                continue
            yield NormalizedSession(
                session_id=sess_dir.name,
                provider=self.name,
                source_path=str(sess_dir),
                input_type="artifacts",
                artifacts=artifacts,
            )

    def session_id(self, raw_handle) -> str:
        if isinstance(raw_handle, Path):
            return raw_handle.name
        return str(raw_handle)
