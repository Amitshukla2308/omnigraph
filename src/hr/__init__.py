"""Relationship-graph math for OmniGraph's mention-event data.

Implements the core algorithms — cochange, Louvain
community detection, composite criticality — as pure-Python functions
that operate directly on OmniGraph's in-memory session/event shape.

No subprocess, no git_history.json round-trip, no tree-sitter. Code-
specific signals (authorship, revert messages, granger) are dropped;
prose-appropriate signals (event volume, valence intensity, concern
weight) are added.

Typical use:

    from hr import build_all, load_sessions_from_extractions
    sessions = load_sessions_from_extractions(["pilot/qwen", "pilot/full"])
    bundle = build_all(sessions)
    bundle.cochange["edges"]             # {target: [{module, weight}, ...]}
    bundle.communities["communities"]    # Louvain partition
    bundle.criticality["modules"]        # {target: {score, signals, reasons}}
    bundle.to_json()                     # JSON-serializable payload
"""
from .types import Session, HRBundle
from .cochange import build_cochange
from .communities import build_communities
from .criticality import build_criticality
from .adapters import load_sessions_from_extractions, load_sessions_from_event_stream
from .api import build_all

__all__ = [
    "Session", "HRBundle",
    "build_cochange", "build_communities", "build_criticality", "build_all",
    "load_sessions_from_extractions", "load_sessions_from_event_stream",
]
