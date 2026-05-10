"""OmniGraph source adapters.

Each adapter converts a provider's raw conversation dump into the canonical
NormalizedSession shape the Qwen extractor consumes. Adapters are thin:
format-translation only, no content extraction.

Registry: `get_adapter(name)` → SourceAdapter instance
         `list_adapters()` → list[str]
"""
from __future__ import annotations
from typing import Callable, Dict
from .base import SourceAdapter


_REGISTRY: Dict[str, Callable[[], SourceAdapter]] = {}


def register(name: str):
    """Decorator: register a SourceAdapter constructor under `name`."""
    def wrap(cls):
        _REGISTRY[name] = cls
        return cls
    return wrap


def get_adapter(name: str) -> SourceAdapter:
    if name not in _REGISTRY:
        # Lazy-import concrete adapters so module import has no side effects.
        _load_builtin_adapters()
    if name not in _REGISTRY:
        raise KeyError(f"unknown source adapter: {name}")
    return _REGISTRY[name]()


def list_adapters() -> list[str]:
    _load_builtin_adapters()
    return sorted(_REGISTRY.keys())


def _load_builtin_adapters() -> None:
    # Importing each module runs @register(...) at module scope.
    from . import atelier_pty, claude_desktop, claude_code, gemini, cline, antigravity  # noqa: F401


__all__ = ["SourceAdapter", "get_adapter", "list_adapters", "register"]
