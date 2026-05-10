"""Co-change (co-mention) graph over sessions.

Prose tuning vs HR's 06_build_cochange.py:
  - No source-extension filter (every canonical target_id is a valid module).
  - `max_targets_per_session` raised a bit (sessions can mention many targets
    cleanly, unlike mega-commits which are usually merges).
  - `min_weight` auto-scales on session count, same rule shape as HR.

Input:  iterable of Session (or dict with "id", "date", "targets" keys)
Output: same shape HR emits — {meta, edges} — so downstream consumers
        written against HR stay compatible.
"""
from __future__ import annotations
from collections import defaultdict
from itertools import combinations
from typing import Iterable

from .types import Session, iter_session_dicts


def _auto_min_weight(n_sessions: int) -> int:
    if n_sessions < 200:
        return 2
    if n_sessions < 1000:
        return 2
    return 3


def build_cochange(
    sessions: Iterable[Session | dict],
    min_weight: int | None = None,
    max_targets_per_session: int = 60,
    top_k: int = 30,
) -> dict:
    """Pair-level co-mention weights with auto-thresholded filtering."""
    cochange: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_sessions = 0
    skipped_mega = 0

    for s in iter_session_dicts(sessions):
        total_sessions += 1
        targets = [t for t in (s.get("targets") or []) if isinstance(t, str) and t]
        if len(targets) < 2:
            continue
        if len(targets) > max_targets_per_session:
            skipped_mega += 1
            continue
        unique = sorted(set(targets))
        for a, b in combinations(unique, 2):
            cochange[a][b] += 1
            cochange[b][a] += 1

    mw = min_weight if min_weight is not None else _auto_min_weight(total_sessions)

    edges: dict[str, list[dict]] = {}
    total_pairs = 0
    for mod, partners in cochange.items():
        filtered = sorted(
            [{"module": m, "weight": w} for m, w in partners.items() if w >= mw],
            key=lambda x: -x["weight"],
        )[:top_k]
        if filtered:
            edges[mod] = filtered
            total_pairs += len(filtered)

    return {
        "meta": {
            "total_sessions": total_sessions,
            "skipped_mega_sessions": skipped_mega,
            "total_modules": len(edges),
            "total_pairs": total_pairs,
            "min_weight": mw,
            "max_targets_per_session": max_targets_per_session,
        },
        "edges": edges,
    }
