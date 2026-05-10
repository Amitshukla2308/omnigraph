"""Shared types for the hr/ package."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Iterable


@dataclass
class Session:
    """Minimal shape hr/ consumes: a session with a date and the set of
    canonical target_ids mentioned in it.

    Optional fields carry enrichment signals used by criticality:
      valence_by_target   target_id -> dominant valence string
      concern_targets     set of targets this session raised concerns on
      event_counts        target_id -> how many mention events for that target
                          in this session (proxy for "lines changed")
    """
    id: str
    date: str                                     # YYYY-MM-DD
    targets: list[str] = field(default_factory=list)
    provider: str | None = None
    valence_by_target: dict[str, str] = field(default_factory=dict)
    concern_targets: set[str] = field(default_factory=set)
    event_counts: dict[str, int] = field(default_factory=dict)

    @property
    def target_set(self) -> set[str]:
        return set(self.targets)


@dataclass
class HRBundle:
    cochange: dict = field(default_factory=dict)
    communities: dict = field(default_factory=dict)
    criticality: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        """JSON-serializable payload consumable by any HTTP / UI layer."""
        return {
            "meta": self.meta,
            "cochange": self.cochange,
            "communities": self.communities,
            "criticality": self.criticality,
        }


def iter_session_dicts(sessions: Iterable) -> Iterable[dict]:
    """Coerce Session or raw dict to dict for scripts that don't want the class."""
    for s in sessions:
        if isinstance(s, Session):
            yield asdict(s)
        elif isinstance(s, dict):
            yield s
        else:
            continue
