"""GEMINI.md projection — Markdown consumable via GEMINI_SYSTEM_MD env var.

Thin wrapper over claude_md with slightly different header framing; content
overlap is intentional — same source, slightly different consumer dialect.
"""
from __future__ import annotations

from . import register
from .base import ProjectionCompiler, VaultState
from .claude_md import ClaudeMDCompiler


@register("gemini_md")
class GeminiMDCompiler(ProjectionCompiler):
    name = "gemini_md"
    default_max_tokens = 4000

    def compile(self, state: VaultState, max_tokens: int | None = None) -> str:
        # Reuse claude_md body, replace the heading for Gemini consumers.
        body = ClaudeMDCompiler().compile(state, max_tokens=max_tokens)
        return body.replace("# User meta-profile", "# Operator meta-profile", 1)
