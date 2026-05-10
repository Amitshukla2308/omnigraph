"""Relationship-graph adapter for OmniGraph.

Maps OmniGraph's event stream + Vault onto a git-history-shaped input,
so the existing build pipeline (cochange, granger, communities /
criticality) scripts run against session data without modification.

HR's build/01 (tree-sitter symbol extraction) is code-specific and is
NOT consumed; the prose Vault is rendered elsewhere (Phase 4 compilers).
"""
from .export_for_hr import export_git_history_like  # noqa: F401

__all__ = ["export_git_history_like"]
