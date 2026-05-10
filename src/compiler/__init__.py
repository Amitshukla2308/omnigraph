"""OmniGraph projection compilers.

Each compiler reads a VaultState (global_profile.json + optional Vault/events
dirs) and emits target-specific text for a specific consumer.
"""
from __future__ import annotations
from typing import Callable, Dict
from .base import ProjectionCompiler, VaultState


_REGISTRY: Dict[str, Callable[[], ProjectionCompiler]] = {}


def register(name: str):
    def wrap(cls):
        _REGISTRY[name] = cls
        return cls
    return wrap


def get_compiler(name: str) -> ProjectionCompiler:
    if name not in _REGISTRY:
        _load_builtin_compilers()
    if name not in _REGISTRY:
        raise KeyError(f"unknown compile target: {name} (have: {sorted(_REGISTRY)})")
    return _REGISTRY[name]()


def list_targets() -> list[str]:
    _load_builtin_compilers()
    return sorted(_REGISTRY.keys())


def _load_builtin_compilers() -> None:
    from . import (  # noqa: F401
        light_ir, claude_md, boot_context, cursor_rules, gemini_md, brain_view,
        light_ir_global, light_ir_personal, light_ir_project,
    )


__all__ = [
    "ProjectionCompiler", "VaultState",
    "get_compiler", "list_targets", "register",
]
