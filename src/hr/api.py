"""One-shot `build_all` — the canonical entry point for applications.

Anyone wiring OmniGraph's graph signals into an app, an HTTP service,
or a UI should call this. It returns a JSON-serializable bundle with
cochange / communities / criticality, all computed in-process.
"""
from __future__ import annotations
from typing import Iterable

from .types import Session, HRBundle, iter_session_dicts
from .cochange import build_cochange
from .communities import build_communities
from .criticality import build_criticality


def build_all(
    sessions: Iterable[Session | dict],
    *,
    min_weight: int | None = None,
    max_targets_per_session: int = 60,
    louvain_resolution: float = 1.0,
    louvain_seed: int = 42,
) -> HRBundle:
    """Compute cochange → communities → criticality over the given sessions.

    Sessions may be Session instances or raw dicts matching the Session shape.
    """
    sessions_list = list(iter_session_dicts(sessions))
    cochange = build_cochange(
        sessions_list,
        min_weight=min_weight,
        max_targets_per_session=max_targets_per_session,
    )
    communities = build_communities(
        cochange, resolution=louvain_resolution, seed=louvain_seed
    )
    criticality = build_criticality(cochange, sessions_list)
    return HRBundle(
        cochange=cochange,
        communities=communities,
        criticality=criticality,
        meta={
            "sessions": len(sessions_list),
            "cochange_modules": cochange["meta"]["total_modules"],
            "cochange_pairs": cochange["meta"]["total_pairs"],
            "communities": communities["meta"]["n_communities"],
            "critical_modules": criticality["meta"]["total_modules"],
        },
    )
