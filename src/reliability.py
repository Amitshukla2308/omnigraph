"""Per-provider reliability + grounding-survival confidence metrics.

This is a non-mutating analytics layer over the existing v0.2.1 extractions.
It does NOT bump the schema (would require re-extracting 485 sessions).
Instead it computes reliability signals from artifacts we already have:

  - Per-provider extraction-rate metrics (mentions/session, decisions/session,
    rules/session, drifts/session).
  - Grounding-survival rate proxies: object counts before/after phase 2
    are not retained in current per-session JSONs (only post-verify
    counts), so we use *cross-provider variance* as a noise proxy: if
    one provider extracts 3x as many mentions per turn as another, the
    high-volume one is likely over-extracting.
  - Phase-3 critique drop rate: we record per-session warnings/notes
    when fast-mode skipped critique; otherwise survival is implicit
    (objects remaining = post-critique).

Embedding-based entity dedup is intentionally NOT here — the LM Studio
single-model guardrail blocks loading a separate embedder. canonical_slugs.py
handles most aliasing via the curated slug_aliases.yaml table.

Used by `omnigraph reliability`.
"""
from __future__ import annotations

import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "pilot"
FULL_DIR = PILOT / "full"


_KNOWN_PROVIDERS = {
    "claude_code", "claude_desktop", "gemini_cli", "cline", "antigravity",
}


def _session_files() -> list[Path]:
    out = []
    for p in FULL_DIR.glob("*/*.json"):
        if p.name == "global_profile.json" or "_logs" in p.parts:
            continue
        # Skip non-provider sibling dirs like compiled/ added by post-ETL hook.
        if p.parent.name not in _KNOWN_PROVIDERS:
            continue
        out.append(p)
    return out


def per_provider_metrics() -> dict:
    """Return per-provider rates of each extraction kind."""
    by_provider: dict[str, dict] = defaultdict(lambda: {
        "sessions": 0,
        "total_turns": 0,
        "mention_events": [],
        "decisions": [],
        "drifts": [],
        "rules": [],
        "mental_moves": [],
        "stances": [],
        "unresolved": [],
    })
    for fp in _session_files():
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        prov = d.get("provider") or fp.parent.name
        b = by_provider[prov]
        b["sessions"] += 1
        # Heuristic turn count from session_meta or count of mention_events
        meta = d.get("session_meta") or {}
        turns = meta.get("turn_count") or len(d.get("mention_events") or [])
        b["total_turns"] += int(turns)
        for k in ("mention_events", "decisions", "drifts", "rules",
                  "mental_moves", "stances", "unresolved"):
            b[k].append(len(d.get(k) or []))

    out = {}
    for prov, b in by_provider.items():
        n = b["sessions"]
        rec = {
            "sessions": n,
            "avg_turns_per_session": round(b["total_turns"] / max(n, 1), 1),
        }
        for k in ("mention_events", "decisions", "drifts", "rules",
                  "mental_moves", "stances", "unresolved"):
            arr = b[k]
            if arr:
                rec[f"{k}_per_session_median"] = statistics.median(arr)
                rec[f"{k}_per_session_mean"] = round(statistics.mean(arr), 2)
                rec[f"{k}_total"] = sum(arr)
        out[prov] = rec
    return out


def cross_provider_volume_skew(metric: str = "mention_events_per_session_mean") -> dict:
    """Identify providers that extract dramatically more or less than the median.

    Returns: { provider: { value, skew_ratio_vs_median, verdict } }
    Higher ratios are flagged as 'likely over-extracting' (noise proxy).
    """
    metrics = per_provider_metrics()
    values = [m.get(metric, 0) for m in metrics.values() if metric in m]
    if not values:
        return {}
    med = statistics.median(values)
    out = {}
    for prov, m in metrics.items():
        v = m.get(metric, 0)
        ratio = (v / med) if med else 0
        verdict = "in-range"
        if ratio >= 2.0:
            verdict = "over-extracting (noise risk)"
        elif ratio <= 0.5 and v > 0:
            verdict = "under-extracting (sparse)"
        out[prov] = {
            "value": round(v, 2),
            "skew_ratio_vs_median": round(ratio, 2),
            "verdict": verdict,
        }
    return out


def reliability_report() -> dict:
    return {
        "per_provider": per_provider_metrics(),
        "skew_mention_events": cross_provider_volume_skew("mention_events_per_session_mean"),
        "skew_decisions": cross_provider_volume_skew("decisions_per_session_mean"),
        "skew_rules": cross_provider_volume_skew("rules_per_session_mean"),
        "notes": [
            "Embedding-based dedup not available — LM Studio single-model guardrail.",
            "Confidence-per-object deferred — would require re-extraction with prompt change.",
            "Skew ratios use median across providers as baseline; high ratios flag noise risk.",
        ],
    }


def cmd_reliability(json_out: bool = False) -> int:
    rep = reliability_report()
    if json_out:
        import sys
        json.dump(rep, sys.stdout, indent=2, default=str)
        print()
        return 0
    print("=== Per-provider extraction metrics ===")
    print(f"{'provider':<18} {'sess':>5} {'turns/s':>8} {'mentions/s':>11} {'dec/s':>6} {'rules/s':>8} {'drifts/s':>9}")
    for prov, m in rep["per_provider"].items():
        print(f"{prov:<18} {m['sessions']:>5} "
              f"{m['avg_turns_per_session']:>8} "
              f"{m.get('mention_events_per_session_mean','-'):>11} "
              f"{m.get('decisions_per_session_mean','-'):>6} "
              f"{m.get('rules_per_session_mean','-'):>8} "
              f"{m.get('drifts_per_session_mean','-'):>9}")
    print()
    print("=== Volume skew (mention_events; ratio vs cross-provider median) ===")
    for prov, s in rep["skew_mention_events"].items():
        print(f"  {prov:<18} value={s['value']:>7}  ratio={s['skew_ratio_vs_median']:>5}  {s['verdict']}")
    print()
    print("=== Notes ===")
    for n in rep["notes"]:
        print(f"  - {n}")
    return 0
