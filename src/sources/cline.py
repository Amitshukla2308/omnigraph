"""Cline — two JSON shapes (ui_messages + raw api conversation) per task dir."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Iterator

from . import register
from .base import NormalizedSession, SourceAdapter, _strip_fences, flatten_content, coerce_ts

AI_CONV = Path(os.environ.get("OMNIGRAPH_AI_CONV", str(Path.home() / "ai_conversations")))
SRC_ROOT = AI_CONV / "Cline" / "conversations"


@register("cline")
class ClineAdapter(SourceAdapter):
    name = "cline"

    def iter_sessions(self) -> Iterator[NormalizedSession]:
        if not SRC_ROOT.exists():
            return
        for src in sorted(SRC_ROOT.glob("*.json")):
            sid = src.stem
            try:
                d = json.load(src.open())
            except Exception:
                continue
            turns: list[dict] = []
            idx = 0
            fname = src.name
            if "ui_messages" in fname:
                if not isinstance(d, list):
                    continue
                for m in d:
                    if not isinstance(m, dict):
                        continue
                    text = _strip_fences(m.get("text", "") or "")
                    typ = m.get("type") or m.get("say") or m.get("ask") or "msg"
                    # Drop tool-shaped ui_message types (cline emits say:"tool",
                    # say:"command", say:"command_output", ask:"tool", etc.).
                    if typ in {"tool", "command", "command_output", "api_req_started",
                               "api_req_finished", "api_req_retried", "browser_action",
                               "browser_action_result", "mcp_server_request_started",
                               "mcp_server_response", "use_mcp_server"}:
                        continue
                    role = "user" if typ in ("ask", "text") else "assistant"
                    if not text.strip():
                        continue
                    turns.append({
                        "index": idx,
                        "role": role,
                        "timestamp": coerce_ts(m.get("ts")),
                        "text": text[:5000],
                        "thinking": "",
                        "raw_type": typ,
                    })
                    idx += 1
            else:
                if not isinstance(d, list):
                    continue
                for m in d:
                    if not isinstance(m, dict):
                        continue
                    role = m.get("role", "?")
                    # Drop tool-role messages outright (raw API shape uses
                    # role:"tool" for results); flatten_content already drops
                    # tool_use/tool_result blocks inside assistant content.
                    if role in {"tool", "tool_result", "function"}:
                        continue
                    fc = flatten_content(m.get("content", ""))
                    if not fc["text"] and not fc["thinking"]:
                        continue
                    turns.append({
                        "index": idx,
                        "role": role,
                        "timestamp": None,
                        "text": fc["text"][:5000],
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
            return raw_handle.stem
        return str(raw_handle)
