"""Truth-maintenance + temporal queries over the decision corpus.

Three capabilities:
  1. open()       — list still-open decisions and unresolved concerns.
  2. history(id)  — chronological decisions touching a target entity.
  3. supersession_chains() — heuristic detection of decision flips
     (e.g. status active→reverted on the same proposition / related_entity).

Reads pilot/full/<provider>/<sid>.json and (optionally) a sessions index
to attach timestamps. Where session-level timestamps aren't available,
falls back to extracting the earliest mention_event ts.

Used by omnigraph_cli subcommands `open` / `history`.
"""
from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "pilot"
FULL_DIR = PILOT / "full"

OPEN_DECISION_STATUSES = {
    "tentative", "proposed", "pending", "queued", "trial",
    "assess", "hold", "planned", "initiated",
}
RESOLVED_DECISION_STATUSES = {
    "locked", "implemented", "executed", "completed", "accepted",
}
REVERSED_DECISION_STATUSES = {
    "reverted", "overturned",
}


# ----------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------

_KNOWN_PROVIDERS = {
    "claude_code", "claude_desktop", "gemini_cli", "cline", "antigravity",
}


def _session_files() -> list[Path]:
    out = []
    for p in FULL_DIR.glob("*/*.json"):
        if p.name == "global_profile.json" or "_logs" in p.parts:
            continue
        if p.parent.name not in _KNOWN_PROVIDERS:
            continue
        out.append(p)
    return out


def _session_ts(d: dict) -> str:
    """Best-effort session timestamp: earliest mention_event ts, else session_meta, else empty."""
    events = d.get("mention_events") or []
    ts = []
    for e in events:
        if isinstance(e, dict) and e.get("ts"):
            ts.append(e["ts"])
    if ts:
        return min(ts)
    meta = d.get("session_meta") or {}
    return meta.get("started_at") or meta.get("ts") or ""


def load_decisions() -> list[dict]:
    """Return [{decision, session_id, provider, ts}] for every decision in pilot/full."""
    out = []
    for fp in _session_files():
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        sid = d.get("session_id") or fp.stem
        prov = d.get("provider") or fp.parent.name
        ts = _session_ts(d)
        for dec in (d.get("decisions") or []):
            if not isinstance(dec, dict):
                continue
            out.append({
                "decision": dec,
                "session_id": sid,
                "provider": prov,
                "ts": ts,
            })
    out.sort(key=lambda r: r.get("ts") or "")
    return out


def load_unresolved_concerns() -> list[dict]:
    """Concerns that surfaced but were never marked resolved.

    Concerns live in `meta_moments` / `unresolved` arrays per the schema.
    We pull from `unresolved` first; fall back to drift `tool_failure` chains.
    """
    out = []
    for fp in _session_files():
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        sid = d.get("session_id") or fp.stem
        prov = d.get("provider") or fp.parent.name
        ts = _session_ts(d)
        for u in (d.get("unresolved") or []):
            if not isinstance(u, dict):
                continue
            out.append({"concern": u, "session_id": sid, "provider": prov, "ts": ts})
    out.sort(key=lambda r: r.get("ts") or "")
    return out


# ----------------------------------------------------------------------
# queries
# ----------------------------------------------------------------------

def open_items(limit: int = 30) -> dict:
    decisions = load_decisions()
    concerns = load_unresolved_concerns()
    open_decs = [r for r in decisions
                 if (r["decision"].get("status") or "").lower() in OPEN_DECISION_STATUSES]
    return {
        "open_decisions": open_decs[-limit:],
        "open_decisions_total": len(open_decs),
        "unresolved_concerns": concerns[-limit:],
        "unresolved_concerns_total": len(concerns),
    }


def history(target_id: str, limit: int = 50) -> list[dict]:
    """Chronological decisions whose related_entities includes target_id.

    Falls back to substring match on proposition text if no related_entities
    contains the id (covers older extractions that didn't tag related_entities).
    """
    decisions = load_decisions()
    out = []
    tid_lower = target_id.lower()
    for r in decisions:
        rels = r["decision"].get("related_entities") or []
        rels_lower = [str(x).lower() for x in rels if x]
        prop = (r["decision"].get("proposition") or "").lower()
        if tid_lower in rels_lower or tid_lower in prop:
            out.append(r)
    return out[-limit:]


def supersession_chains() -> list[dict]:
    """Heuristic: group decisions by (related_entity), then flag chains
    where an early decision's status is later contradicted (locked → reverted,
    or two decisions on the same entity with conflicting propositions)."""
    decisions = load_decisions()
    by_entity: dict[str, list[dict]] = defaultdict(list)
    for r in decisions:
        for rel in (r["decision"].get("related_entities") or []):
            if not rel:
                continue
            by_entity[str(rel).lower()].append(r)

    chains = []
    for entity, rs in by_entity.items():
        if len(rs) < 2:
            continue
        rs.sort(key=lambda x: x.get("ts") or "")
        statuses = [(r["decision"].get("status") or "").lower() for r in rs]
        # Flip detection: any reversed status follows a resolved one.
        flipped = False
        for i in range(1, len(rs)):
            if (statuses[i] in REVERSED_DECISION_STATUSES
                    and statuses[i-1] in RESOLVED_DECISION_STATUSES):
                flipped = True
                break
        if flipped or any(s in REVERSED_DECISION_STATUSES for s in statuses):
            chains.append({
                "entity": entity,
                "links": [
                    {
                        "ts": r.get("ts"),
                        "session_id": r["session_id"],
                        "provider": r["provider"],
                        "status": r["decision"].get("status"),
                        "proposition": (r["decision"].get("proposition") or "")[:160],
                    } for r in rs
                ],
            })
    chains.sort(key=lambda c: -len(c["links"]))
    return chains


# ----------------------------------------------------------------------
# CLI helpers (called from omnigraph_cli)
# ----------------------------------------------------------------------

def _print_decision_row(r: dict, indent: str = "  "):
    dec = r["decision"]
    ts = (r.get("ts") or "")[:10]
    print(f"{indent}{ts}  [{r['provider']}/{r['session_id'][:24]}]  "
          f"{(dec.get('status') or '?'):<12}  {(dec.get('proposition') or '')[:120]}")


def cmd_open(limit: int = 30, json_out: bool = False) -> int:
    res = open_items(limit=limit)
    if json_out:
        print(json.dumps(res, indent=2, default=str))
        return 0
    print(f"=== Open decisions ({res['open_decisions_total']} total, showing last {limit}) ===")
    for r in res["open_decisions"]:
        _print_decision_row(r)
    print()
    print(f"=== Unresolved concerns ({res['unresolved_concerns_total']} total, showing last {limit}) ===")
    for r in res["unresolved_concerns"]:
        c = r["concern"]
        ts = (r.get("ts") or "")[:10]
        text = c.get("description") or c.get("text") or c.get("title") or json.dumps(c)[:120]
        print(f"  {ts}  [{r['provider']}/{r['session_id'][:24]}]  {text[:140]}")
    return 0


def cmd_history(target_id: str, limit: int = 50, json_out: bool = False) -> int:
    rows = history(target_id, limit=limit)
    if json_out:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    print(f"=== Decision history for '{target_id}' ({len(rows)} entries) ===")
    for r in rows:
        _print_decision_row(r)
    if not rows:
        print(f"  (no decisions found touching '{target_id}')")
    return 0


def cmd_supersession(json_out: bool = False) -> int:
    chains = supersession_chains()
    if json_out:
        print(json.dumps(chains, indent=2, default=str))
        return 0
    print(f"=== Supersession chains ({len(chains)} entities show flipped status) ===")
    for c in chains[:20]:
        print(f"\n[{c['entity']}]  {len(c['links'])} decisions")
        for link in c["links"]:
            ts = (link.get("ts") or "")[:10]
            print(f"  {ts}  {(link.get('status') or '?'):<12}  {link.get('proposition','')}")
    return 0
