"""Claude Code — JSONL conversations."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Iterator

from . import register
from .base import NormalizedSession, SourceAdapter, flatten_content, coerce_ts

AI_CONV = Path(os.environ.get("OMNIGRAPH_AI_CONV", str(Path.home() / "ai_conversations")))
SRC_ROOT = AI_CONV / "Anthropic_ClaudeCode" / "conversations"


@register("claude_code")
class ClaudeCodeAdapter(SourceAdapter):
    name = "claude_code"

    def iter_sessions(self) -> Iterator[NormalizedSession]:
        if not SRC_ROOT.exists():
            return
        for src in sorted(SRC_ROOT.glob("*.jsonl")):
            sid = src.stem.replace("local_", "")
            turns: list[dict] = []
            idx = 0
            for line in src.open(errors="replace"):
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                t = o.get("type")
                if t not in ("user", "assistant"):
                    continue
                msg = o.get("message", {}) or {}
                role = msg.get("role", t)
                if role in {"tool", "tool_result", "function"}:
                    continue
                fc = flatten_content(msg.get("content", ""))
                if not (fc["text"] or fc["thinking"]):
                    continue
                turns.append({
                    "index": idx,
                    "role": role,
                    "timestamp": coerce_ts(o.get("timestamp")),
                    "text": fc["text"],
                    "thinking": fc["thinking"],
                })
                idx += 1
            if not turns:
                continue
            yield NormalizedSession(
                session_id=sid,
                provider=self.name,
                source_path=str(src),
                input_type="dialog",
                turns=turns,
            )

    def session_id(self, raw_handle) -> str:
        if isinstance(raw_handle, Path):
            return raw_handle.stem.replace("local_", "")
        return str(raw_handle)
