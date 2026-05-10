"""Composite criticality scoring per target_id.

Prose-tuned signal set (replaces HR's code-centric signals):
  blast_radius       count of co-mention neighbors           (weight 0.30)
  event_volume       total mention events across sessions    (weight 0.20)
  recency            time since most recent mention          (weight 0.15)
  valence_intensity  fraction of mentions with strong valence(weight 0.15)
  concern_weight     fraction of mentions that are concern_* (weight 0.20)

Dropped (not applicable without multi-author commit history or git messages):
  author_concentration, granger_influence, revert_risk.

Input:
  cochange: dict   — from build_cochange
  sessions: list   — Session (or dict) list, used for volume/recency/valence

Output:
  { meta, modules: { target: { score, rank, signals, reasons } } }
"""
from __future__ import annotations
import datetime as _dt
from collections import defaultdict
from typing import Iterable

from .types import Session, iter_session_dicts

WEIGHTS = {
    "blast_radius": 0.30,
    "event_volume": 0.20,
    "recency": 0.15,
    "valence_intensity": 0.15,
    "concern_weight": 0.20,
}

STRONG_VALENCES = {"frustrated", "confident", "negative", "positive", "urgent"}


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi == lo:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _iso_to_ts(s: str) -> float | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _blast_radius(cochange: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for mod, partners in (cochange.get("edges") or {}).items():
        if isinstance(partners, list):
            out[mod] = float(len(partners))
    return _normalize(out)


def _event_volume_and_providers(sessions: list[dict]) -> tuple[dict[str, float], dict[str, set]]:
    vol: dict[str, float] = defaultdict(float)
    providers: dict[str, set] = defaultdict(set)
    for s in sessions:
        prov = s.get("provider")
        counts = s.get("event_counts") or {}
        for tgt, c in counts.items():
            vol[tgt] += float(c)
            if prov:
                providers[tgt].add(prov)
        # If event_counts is empty but targets are present, count 1 per target.
        if not counts:
            for t in s.get("targets") or []:
                vol[t] += 1.0
                if prov:
                    providers[t].add(prov)
    return _normalize(dict(vol)), providers


def _recency(sessions: list[dict]) -> dict[str, float]:
    latest: dict[str, float] = {}
    for s in sessions:
        ts = _iso_to_ts(str(s.get("date") or ""))
        if ts is None:
            continue
        for t in s.get("targets") or []:
            if t not in latest or ts > latest[t]:
                latest[t] = ts
    return _normalize(latest)


def _valence_intensity(sessions: list[dict]) -> dict[str, float]:
    strong = defaultdict(int)
    total = defaultdict(int)
    for s in sessions:
        vbt = s.get("valence_by_target") or {}
        for t in s.get("targets") or []:
            total[t] += 1
            v = vbt.get(t)
            if isinstance(v, str) and v in STRONG_VALENCES:
                strong[t] += 1
    out = {t: strong[t] / total[t] for t in total if total[t] > 0}
    return _normalize(out)


def _concern_weight(sessions: list[dict]) -> dict[str, float]:
    raised = defaultdict(int)
    total = defaultdict(int)
    for s in sessions:
        concerns = s.get("concern_targets") or []
        if isinstance(concerns, set):
            concerns = list(concerns)
        for t in s.get("targets") or []:
            total[t] += 1
        for t in concerns:
            raised[t] += 1
    out = {t: raised[t] / total[t] for t in total if total[t] > 0}
    return _normalize(out)


def build_criticality(cochange: dict, sessions: Iterable[Session | dict]) -> dict:
    sessions_list = list(iter_session_dicts(sessions))

    signals = {
        "blast_radius": _blast_radius(cochange),
        "event_volume": _event_volume_and_providers(sessions_list)[0],
        "recency": _recency(sessions_list),
        "valence_intensity": _valence_intensity(sessions_list),
        "concern_weight": _concern_weight(sessions_list),
    }
    _, providers = _event_volume_and_providers(sessions_list)

    all_modules: set[str] = set()
    for scores in signals.values():
        all_modules.update(scores.keys())

    modules: dict[str, dict] = {}
    for mod in all_modules:
        sig_vals = {s: round(signals[s].get(mod, 0.0), 4) for s in signals}
        weighted = sum(sig_vals[s] * WEIGHTS[s] for s in WEIGHTS)
        reasons = [
            f"{s.replace('_', ' ').title()}: {sig_vals[s]:.2f}"
            for s in sorted(sig_vals, key=lambda k: -sig_vals[k])
            if sig_vals[s] > 0.5
        ][:4]
        modules[mod] = {
            "score": round(weighted, 4),
            "signals": sig_vals,
            "reasons": reasons,
            "providers": sorted(providers.get(mod, [])),
        }

    ranked = sorted(modules, key=lambda m: -modules[m]["score"])
    for rank, mod in enumerate(ranked, 1):
        modules[mod]["rank"] = rank

    top10 = [{"module": m, "score": modules[m]["score"]} for m in ranked[:10]]

    return {
        "meta": {
            "total_modules": len(modules),
            "signal_weights": WEIGHTS,
            "top_10": top10,
        },
        "modules": modules,
    }
