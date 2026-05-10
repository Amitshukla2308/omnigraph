#!/usr/bin/env python3
"""Stage-2 cross-session aggregation per SCHEMA.md §8.4.

Reads <indir>/<prov>/<sid>.json extractions (excluding global_profile.json,
_logs, _run_summary), produces <indir>/global_profile.json with the 6
inference patterns.

Modes:
  aggregate_full(indir)                 — full rebuild; default.
  aggregate_incremental(indir, statep)  — process only new sessions,
                                          maintain persistent state at
                                          <statep> (default:
                                          pilot/_aggregate_state.json).
                                          Derived inferences are always
                                          re-computed from accumulated
                                          state; only the dict-growing
                                          portion is incremental.

Usage:
  python stage2_aggregate.py <indir>                         # full
  python stage2_aggregate.py --incremental <indir>           # incremental
  python stage2_aggregate.py --state <path> <indir>          # custom state path
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from canonical_slugs import canonicalize_session  # type: ignore
except Exception:  # pragma: no cover
    def canonicalize_session(s):
        return s


DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "pilot" / "_aggregate_state.json"


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------

def _iter_session_files(indir: Path):
    for f in sorted(glob.glob(str(indir / "*" / "*.json"))):
        if f.endswith("global_profile.json"): continue
        if "_logs" in f: continue
        if "_run_summary" in f: continue
        yield Path(f)


def _load_sessions(indir: Path, only_ids: set | None = None) -> list[dict]:
    out: list[dict] = []
    for f in _iter_session_files(indir):
        try:
            s = json.load(open(f))
        except Exception as e:
            print(f"skip {f}: {e}", file=sys.stderr)
            continue
        canonicalize_session(s)
        sid = s.get("session_id")
        if only_ids is not None and sid in only_ids:
            continue  # already processed
        out.append(s)
    return out


# ----------------------------------------------------------------------
# State shape
# ----------------------------------------------------------------------

def _empty_state() -> dict:
    """In-memory state of accumulated observations. JSON-serializable form
    uses plain dict/list; conversion happens in _state_to_json / _state_from_json.
    """
    return {
        "processed_session_ids": set(),   # set[str]
        "target_events": defaultdict(list),   # tid -> [event, ...]
        "target_types": {},                   # tid -> type
        "target_providers": defaultdict(set), # tid -> set[provider]
        "mental_moves": defaultdict(list),    # (move, owner, level) -> [sid, ...]
        "drift_by_trigger": defaultdict(list),
        "rules_all": [],
        "affect_all": [],
        "stances_all": [],
        # entity -> [{sid, provider, proposition, status}], built from
        # per-session Decision objects' related_entities. Lets us tag
        # entities anchoring multiple decisions as load-bearing.
        "decision_anchors": defaultdict(list),
    }


def _state_to_json(state: dict) -> dict:
    """Serializable form."""
    return {
        "processed_session_ids": sorted(state["processed_session_ids"]),
        "target_events": dict(state["target_events"]),
        "target_types": state["target_types"],
        "target_providers": {k: sorted(v) for k, v in state["target_providers"].items()},
        "mental_moves": [
            {"key": list(k), "sids": v}
            for k, v in state["mental_moves"].items()
        ],
        "drift_by_trigger": dict(state["drift_by_trigger"]),
        "rules_all": state["rules_all"],
        "affect_all": state["affect_all"],
        "stances_all": state["stances_all"],
        "decision_anchors": dict(state["decision_anchors"]),
    }


def _state_from_json(d: dict) -> dict:
    s = _empty_state()
    s["processed_session_ids"] = set(d.get("processed_session_ids") or [])
    for tid, evs in (d.get("target_events") or {}).items():
        s["target_events"][tid] = list(evs)
    s["target_types"] = dict(d.get("target_types") or {})
    for tid, provs in (d.get("target_providers") or {}).items():
        s["target_providers"][tid] = set(provs)
    for entry in d.get("mental_moves") or []:
        k = tuple(entry.get("key") or ())
        s["mental_moves"][k] = list(entry.get("sids") or [])
    for trg, items in (d.get("drift_by_trigger") or {}).items():
        s["drift_by_trigger"][trg] = list(items)
    s["rules_all"] = list(d.get("rules_all") or [])
    s["affect_all"] = list(d.get("affect_all") or [])
    s["stances_all"] = list(d.get("stances_all") or [])
    for ent, anchors in (d.get("decision_anchors") or {}).items():
        s["decision_anchors"][ent] = list(anchors)
    return s


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return _empty_state()
    try:
        return _state_from_json(json.loads(state_path.read_text()))
    except Exception as e:
        print(f"[stage2] state file unreadable ({e}); starting fresh", file=sys.stderr)
        return _empty_state()


def _save_state(state: dict, state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(_state_to_json(state), indent=2, default=str))


# ----------------------------------------------------------------------
# Collection — adds each session's contributions to state
# ----------------------------------------------------------------------

def _session_fallback_ts(session: dict) -> str:
    """Best-effort ISO timestamp for a session when meta is missing.

    Tries session_meta → ms-epoch session_id → source_normalized file mtime.
    Returns empty string if nothing works.
    """
    meta = session.get("session_meta") or {}
    ts = meta.get("timestamp_start") or meta.get("timestamp_end")
    if ts:
        return str(ts)
    sid = str(session.get("session_id") or "")
    if sid.isdigit() and len(sid) >= 13:
        try:
            import datetime as _dt
            return _dt.datetime.utcfromtimestamp(int(sid) / 1000).isoformat() + "Z"
        except Exception:
            pass
    src = meta.get("source_normalized")
    if src:
        try:
            import datetime as _dt, os
            return _dt.datetime.utcfromtimestamp(os.path.getmtime(src)).isoformat() + "Z"
        except Exception:
            pass
    return ""


def _collect_into(state: dict, sessions: list[dict]) -> None:
    for s in sessions:
        prov = s.get("provider", "?")
        sid = s.get("session_id", "?")
        ts_session = _session_fallback_ts(s)

        state["processed_session_ids"].add(sid)

        for ev in s.get("mention_events", []) or []:
            if not isinstance(ev, dict):
                continue
            tid = ev.get("target_id")
            if not tid:
                continue
            state["target_events"][tid].append({
                "session_id": sid, "provider": prov,
                "timestamp": ev.get("timestamp") or ts_session,
                "mention_type": ev.get("mention_type", "?"),
                "authorship": ev.get("authorship", "?"),
                "valence": ev.get("valence", "?"),
                "evidence_quote": (ev.get("evidence_quote") or "")[:200],
            })
            state["target_providers"][tid].add(prov)
            state["target_types"].setdefault(tid, ev.get("target_type", "?"))

        for m in s.get("mental_moves", []) or []:
            if not isinstance(m, dict):
                continue
            key = (m.get("move", "")[:100], m.get("owner", "?"), m.get("level", "?"))
            state["mental_moves"][key].append(sid)

        # Decision anchoring — feeds load-bearing-decisions inference.
        for dec in s.get("decisions", []) or []:
            if not isinstance(dec, dict):
                continue
            prop = (dec.get("proposition") or "")[:200]
            status = dec.get("status") or "?"
            for ent in dec.get("related_entities") or []:
                if not ent:
                    continue
                state["decision_anchors"][str(ent).lower()].append({
                    "session_id": sid, "provider": prov,
                    "proposition": prop, "status": status,
                })

        for d in s.get("drifts", []) or []:
            if not isinstance(d, dict):
                continue
            state["drift_by_trigger"][d.get("trigger", "?")].append({
                "session": sid, "provider": prov,
                "rule_generated": d.get("rule_generated", ""),
                "proposed": (d.get("proposed") or "")[:120],
                "corrected_to": (d.get("corrected_to") or "")[:120],
            })

        for r in s.get("rules", []) or []:
            if not isinstance(r, dict):
                continue
            state["rules_all"].append({
                "rule_text": r.get("rule_text", ""),
                "applies_to": r.get("applies_to", "?"),
                "level": r.get("level", "?"),
                "session": sid, "provider": prov,
            })

        for a in s.get("affect", []) or []:
            if not isinstance(a, dict):
                continue
            state["affect_all"].append({**a, "session": sid, "provider": prov})

        for st in s.get("stances", []) or []:
            if not isinstance(st, dict):
                continue
            state["stances_all"].append({**st, "session": sid, "provider": prov})


# ----------------------------------------------------------------------
# Derivation — pure function from state → global_profile
# ----------------------------------------------------------------------

def _parse_ts(s: str) -> float | None:
    if not s:
        return None
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _days_between(a: str, b: str) -> float | None:
    ta, tb = _parse_ts(a), _parse_ts(b)
    if ta is None or tb is None:
        return None
    return abs(tb - ta) / 86400.0


def _derive_gp(state: dict) -> dict:
    target_events = state["target_events"]
    target_types = state["target_types"]
    target_providers = state["target_providers"]
    mental_moves = state["mental_moves"]
    drift_by_trigger = state["drift_by_trigger"]

    convergence_patterns = []
    for tid, events in target_events.items():
        if len(events) < 3: continue
        events_sorted = sorted(events, key=lambda e: e.get("timestamp") or "")
        last3 = events_sorted[-3:]
        last_vals = [e["valence"] for e in last3]
        last_types = [e["mention_type"] for e in last3]
        if any(v in ("positive", "confident") for v in last_vals) or "concern_resolved" in last_types:
            status = "settled"
        elif any(v in ("frustrated", "negative") for v in last_vals) and "concern_resolved" not in last_types:
            status = "possibly_abandoned_or_stuck"
        else:
            status = "ambiguous"
        convergence_patterns.append({
            "target_id": tid, "type": target_types.get(tid, "?"),
            "event_count": len(events),
            "providers": sorted(target_providers[tid]),
            "first_seen": events_sorted[0]["timestamp"],
            "last_seen": events_sorted[-1]["timestamp"],
            "status": status, "last_valences": last_vals,
        })

    # Load-bearing decisions: entities anchoring multiple decisions across
    # sessions (per-session decisions[*].related_entities). Threshold ≥ 3
    # filters one-off mentions while keeping real recurring anchor-points.
    decision_load = []
    for ent, anchors in state.get("decision_anchors", {}).items():
        sessions_ref = {a["session_id"] for a in anchors}
        decision_load.append({
            "target_id": ent,
            "sessions_referenced": len(sessions_ref),
            "decision_count": len(anchors),
            "providers": sorted({a["provider"] for a in anchors}),
            "sample_propositions": [a["proposition"] for a in anchors[:3] if a.get("proposition")],
            "load_class": "load-bearing" if len(sessions_ref) >= 3 else "single-mention",
        })

    concerns_raised: dict[str, list] = {}
    concerns_resolved: dict[str, list] = {}
    for tid, events in target_events.items():
        for e in events:
            if e["mention_type"] == "concern_raised":
                concerns_raised.setdefault(tid, []).append(e)
            elif e["mention_type"] == "concern_resolved":
                concerns_resolved.setdefault(tid, []).append(e)
    concern_lifecycle = []
    for tid in concerns_raised:
        raised = concerns_raised[tid]
        resolved = concerns_resolved.get(tid, [])
        concern_lifecycle.append({
            "target_id": tid, "type": target_types.get(tid, "?"),
            "raised_in": [c["session_id"] for c in raised],
            "resolved_in": [c["session_id"] for c in resolved],
            "status": "resolved" if resolved else "latent_unresolved",
            "raised_count": len(raised), "resolved_count": len(resolved),
        })

    cross_provider = []
    for tid, provs in target_providers.items():
        if len(provs) >= 2:
            cross_provider.append({
                "target_id": tid, "type": target_types.get(tid, "?"),
                "providers": sorted(provs), "provider_count": len(provs),
                "event_count": len(target_events[tid]),
            })
    cross_provider.sort(key=lambda x: -x["provider_count"])

    confirmed_moves, candidate_moves = [], []
    for (move, owner, level), sids in mental_moves.items():
        entry = {"move": move, "owner": owner, "level": level,
                 "occurrences": len(sids), "example_sessions": sids[:5]}
        (confirmed_moves if len(sids) >= 2 else candidate_moves).append(entry)

    entity_freq = Counter({tid: len(events) for tid, events in target_events.items()})
    top_entities = [
        {"target_id": tid, "type": target_types.get(tid, "?"),
         "events": c, "providers": sorted(target_providers[tid])}
        for tid, c in entity_freq.most_common(30)
    ]

    drift_recurrence = sorted(
        [{"trigger": t, "count": len(items), "examples": items[:3]}
         for t, items in drift_by_trigger.items()],
        key=lambda x: -x["count"])

    # ---- Temporal intelligence (plan 03 Application 2) ----
    # Reference "now" = latest event seen across the corpus (not wall-clock)
    # so incremental runs stay deterministic and reproducible.
    all_ts = [e["timestamp"] for ev in target_events.values() for e in ev if e.get("timestamp")]
    now_iso = max(all_ts) if all_ts else ""

    # Idea resurrection: target was dormant for ≥30d then came back in last ≤14d.
    idea_resurrection = []
    for tid, events in target_events.items():
        if len(events) < 3: continue
        ev_sorted = sorted(events, key=lambda e: e.get("timestamp") or "")
        # largest gap between consecutive events
        max_gap_days = 0.0
        gap_pair = None
        for i in range(1, len(ev_sorted)):
            d = _days_between(ev_sorted[i - 1]["timestamp"], ev_sorted[i]["timestamp"])
            if d is not None and d > max_gap_days:
                max_gap_days = d
                gap_pair = (ev_sorted[i - 1]["timestamp"], ev_sorted[i]["timestamp"])
        if max_gap_days < 30: continue
        age_last = _days_between(ev_sorted[-1]["timestamp"], now_iso)
        if age_last is None or age_last > 14: continue
        idea_resurrection.append({
            "target_id": tid,
            "type": target_types.get(tid, "?"),
            "gap_days": round(max_gap_days, 1),
            "gap_from": gap_pair[0] if gap_pair else None,
            "gap_to": gap_pair[1] if gap_pair else None,
            "last_seen": ev_sorted[-1]["timestamp"],
            "event_count": len(ev_sorted),
        })
    idea_resurrection.sort(key=lambda x: -x["gap_days"])

    # Decision half-life: for each Decision target_id with ≥2 events, compute
    # time between first_introduction/made and first subsequent reference.
    decision_half_life = []
    for tid, events in target_events.items():
        if target_types.get(tid) != "Decision":
            continue
        ev_sorted = sorted(events, key=lambda e: e.get("timestamp") or "")
        first = next((e for e in ev_sorted if e["mention_type"] in ("first_introduction", "decision_made")), ev_sorted[0])
        revisits = [e for e in ev_sorted if e is not first]
        if not revisits:
            continue
        first_ts = first["timestamp"]
        revisit_ts = revisits[0]["timestamp"]
        d = _days_between(first_ts, revisit_ts)
        if d is None:
            continue
        decision_half_life.append({
            "target_id": tid,
            "first_ts": first_ts,
            "first_revisit_ts": revisit_ts,
            "half_life_days": round(d, 2),
            "total_events": len(ev_sorted),
            "thrashing": d < 2 and len(ev_sorted) >= 3,  # quick revisit + repeat = thrashing
        })
    decision_half_life.sort(key=lambda x: x["half_life_days"])

    # Concern half-life: for resolved concerns, time between raised and resolved.
    # For latent, age-so-far (how long it's been open).
    concern_lifetime = []
    for c in concern_lifecycle:
        tid = c["target_id"]
        raised_evs = sorted([e for e in target_events.get(tid, []) if e["mention_type"] == "concern_raised"],
                            key=lambda e: e.get("timestamp") or "")
        resolved_evs = sorted([e for e in target_events.get(tid, []) if e["mention_type"] == "concern_resolved"],
                              key=lambda e: e.get("timestamp") or "")
        if not raised_evs:
            continue
        first_raised = raised_evs[0]["timestamp"]
        if resolved_evs:
            end = resolved_evs[0]["timestamp"]
            kind = "resolved"
        else:
            end = now_iso
            kind = "still_open"
        days = _days_between(first_raised, end)
        if days is None:
            continue
        concern_lifetime.append({
            "target_id": tid,
            "kind": kind,
            "days": round(days, 1),
            "first_raised": first_raised,
            "closed_or_now": end,
            "raised_count": len(raised_evs),
        })
    concern_lifetime.sort(key=lambda x: (x["kind"] != "still_open", -x["days"]))

    # Provider-specific cognition: per-provider valence distribution over concerns.
    provider_concerns = defaultdict(Counter)
    provider_sessions = defaultdict(set)
    for tid, events in target_events.items():
        for e in events:
            provider_sessions[e["provider"]].add(e["session_id"])
            if (e["mention_type"] or "").startswith("concern"):
                provider_concerns[e["provider"]][e["mention_type"]] += 1
    provider_cognition = []
    for prov, counter in provider_concerns.items():
        n_sessions = len(provider_sessions.get(prov, set())) or 1
        provider_cognition.append({
            "provider": prov,
            "sessions": n_sessions,
            "concern_raised_rate": round(counter.get("concern_raised", 0) / n_sessions, 3),
            "concern_resolved_rate": round(counter.get("concern_resolved", 0) / n_sessions, 3),
            "breakdown": dict(counter),
        })
    provider_cognition.sort(key=lambda x: -x["concern_raised_rate"])

    total_mention_events = sum(len(v) for v in target_events.values())
    total_deltas = (
        sum(len(s) for s in (state["rules_all"], state["affect_all"], state["stances_all"]))
        + sum(len(v) for v in mental_moves.values())
        + sum(len(v) for v in drift_by_trigger.values())
    )

    return {
        "scale": {
            "sessions": len(state["processed_session_ids"]),
            "providers": sorted({
                p for provs in target_providers.values() for p in provs
            }),
            "total_mention_events": total_mention_events,
            "total_deltas": total_deltas,
        },
        "inference_p1_convergence_vs_abandonment": sorted(
            convergence_patterns, key=lambda x: -x["event_count"]),
        "inference_p3_decision_load_bearing": sorted(
            decision_load, key=lambda x: -x["sessions_referenced"]),
        "inference_p5_concern_lifecycle": sorted(
            concern_lifecycle, key=lambda x: (x["status"], -x["raised_count"])),
        "inference_p6_cross_provider_bleed": cross_provider,
        "confirmed_mental_moves": sorted(confirmed_moves, key=lambda x: -x["occurrences"]),
        "candidate_mental_moves_single_session": candidate_moves,
        "entity_frequency_top30": top_entities,
        "drift_recurrence_by_trigger": drift_recurrence,
        "inference_idea_resurrection": idea_resurrection,
        "inference_decision_half_life": decision_half_life,
        "inference_concern_lifetime": concern_lifetime,
        "inference_provider_cognition": provider_cognition,
        "rules_collected_count": len(state["rules_all"]),
        "rules_collected": state["rules_all"],
        "affect_events": state["affect_all"],
        "stances_collected_count": len(state["stances_all"]),
        "pilot_gaps_noted": [
            "P2 (internalization / teaching ceiling): requires mention-token-length trend analysis — skipped at pilot N.",
            "P4 (rename / pivot detection): requires co-occurrence crossover windows — skipped at pilot N.",
            "CognitiveDual detection: requires user-turn-level analysis; corpus too small to confirm.",
        ],
    }


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def aggregate_full(indir: str | Path) -> dict:
    """Full rebuild. Equivalent to pre-Phase-3 behavior."""
    indir = Path(indir)
    sessions = _load_sessions(indir)
    state = _empty_state()
    _collect_into(state, sessions)
    return _derive_gp(state)


def aggregate_incremental(indir: str | Path, state_path: str | Path | None = None) -> tuple[dict, int]:
    """Process only sessions not in state; return (gp, n_new_sessions)."""
    indir = Path(indir)
    sp = Path(state_path) if state_path else DEFAULT_STATE_PATH
    state = _load_state(sp)
    new_sessions = _load_sessions(indir, only_ids=state["processed_session_ids"])
    _collect_into(state, new_sessions)
    gp = _derive_gp(state)
    _save_state(state, sp)
    return gp, len(new_sessions)


# Backwards-compat alias (callers may still import `aggregate`).
aggregate = aggregate_full


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("indir")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--full", action="store_true", help="Force full rebuild (default if no state exists).")
    ap.add_argument("--state", default=None, help="Path to _aggregate_state.json (default: pilot/_aggregate_state.json)")
    args = ap.parse_args(argv)

    indir = Path(args.indir)
    state_path = Path(args.state) if args.state else DEFAULT_STATE_PATH

    if args.full or not (args.incremental or state_path.exists()):
        gp = aggregate_full(indir)
        n_new = None
    else:
        gp, n_new = aggregate_incremental(indir, state_path)

    out = indir / "global_profile.json"
    with open(out, "w") as f:
        json.dump(gp, f, indent=2, ensure_ascii=False, default=str)

    print(f"✅ {out}")
    print(f"scale: {gp['scale']['sessions']} sessions, "
          f"{gp['scale']['total_mention_events']} mentions, "
          f"{gp['scale']['total_deltas']} deltas")
    if n_new is not None:
        print(f"incremental: +{n_new} new sessions, state at {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
