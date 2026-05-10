#!/usr/bin/env python3
"""Build the append-only MentionEvent stream + target index.

Inputs:  <indir>/<provider>/<session_id>.json   (Qwen per-session extractions)
Outputs:
  <outdir>/<YYYY-MM>.jsonl    — one JSONL per month, timestamp-sorted
  <outdir>/index.json         — { target_id: [ {ts, file, offset_line}, ... ] }
  <outdir>/_meta.json         — build metadata (counts, last session ids)

Usage:
  python build_events_stream.py <indir> <outdir>
  python build_events_stream.py pilot/qwen pilot/events
"""
from __future__ import annotations
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

try:
    from canonical_slugs import canonicalize_session  # type: ignore
except Exception:
    def canonicalize_session(s):
        return s


def _derive_ts(event: dict, session: dict, session_file: Path | None = None) -> str:
    """Event timestamp. Falls through: event.ts → session_meta ts → ms-epoch sid → file mtime."""
    ts = event.get("timestamp")
    if ts:
        return str(ts)
    meta = session.get("session_meta") or {}
    ts = meta.get("timestamp_start") or meta.get("timestamp_end")
    if ts:
        return str(ts)
    sid = str(session.get("session_id") or "")
    if sid.isdigit() and len(sid) >= 13:
        import datetime as _dt
        try:
            return _dt.datetime.utcfromtimestamp(int(sid) / 1000).isoformat() + "Z"
        except Exception:
            pass
    if session_file is not None:
        try:
            import datetime as _dt
            return _dt.datetime.utcfromtimestamp(session_file.stat().st_mtime).isoformat() + "Z"
        except Exception:
            pass
    return ""


def _ym(ts: str) -> str:
    if not ts or len(ts) < 7:
        return "unknown"
    # '2026-04-24T..' → '2026-04'
    try:
        return ts[:7]
    except Exception:
        return "unknown"


def _iter_session_files(indirs: list[Path]) -> Iterable[Path]:
    seen: set[str] = set()
    for indir in indirs:
        for f in sorted(indir.glob("*/*.json")):
            stem = f.stem
            if stem == "global_profile" or "_logs" in str(f) or "_run_summary" in stem:
                continue
            if stem in seen:
                continue
            seen.add(stem)
            yield f


def build(indirs, outdir: Path) -> dict:
    if isinstance(indirs, (str, Path)):
        indirs = [Path(indirs)]
    else:
        indirs = [Path(p) for p in indirs]
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # Clean previous monthly files (idempotent full rebuild).
    for old in outdir.glob("*.jsonl"):
        old.unlink()

    # Collect events into month buckets, sortable.
    buckets: dict[str, list[dict]] = defaultdict(list)
    target_index: dict[str, list[dict]] = defaultdict(list)
    session_count = 0
    event_count = 0
    last_sid = None

    for f in _iter_session_files(indirs):
        try:
            session = json.loads(f.read_text())
        except Exception as e:
            print(f"skip {f}: {e}", file=sys.stderr)
            continue
        # Canonicalize on read (defensive — works even if extractor was skipped).
        canonicalize_session(session)

        sid = str(session.get("session_id") or f.stem)
        prov = session.get("provider") or f.parent.name
        last_sid = sid
        session_count += 1

        for ev in session.get("mention_events") or []:
            if not isinstance(ev, dict):
                continue
            target_id = ev.get("target_id")
            if not target_id:
                continue
            ts = _derive_ts(ev, session, f)
            record = {
                "ts": ts,
                "session_id": sid,
                "provider": prov,
                "target_id": target_id,
                "target_type": ev.get("target_type"),
                "mention_type": ev.get("mention_type"),
                "authorship": ev.get("authorship"),
                "valence": ev.get("valence"),
                "evidence_quote": (ev.get("evidence_quote") or "")[:400],
                "mentioned_as": ev.get("mentioned_as"),
            }
            buckets[_ym(ts)].append(record)
            event_count += 1

    # Sort each bucket and write; build index from written positions.
    total_lines = 0
    for ym, recs in buckets.items():
        recs.sort(key=lambda r: (r["ts"] or "", r["session_id"]))
        out_path = outdir / f"{ym}.jsonl"
        with out_path.open("w") as fh:
            for i, r in enumerate(recs):
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                target_index[r["target_id"]].append({
                    "ts": r["ts"],
                    "file": f"{ym}.jsonl",
                    "line": i,
                })
                total_lines += 1

    # Sort index entries by ts for easy scan.
    for tid in target_index:
        target_index[tid].sort(key=lambda e: e["ts"] or "")

    (outdir / "index.json").write_text(
        json.dumps(target_index, indent=2, ensure_ascii=False)
    )
    meta = {
        "sessions_read": session_count,
        "events_total": event_count,
        "distinct_targets": len(target_index),
        "months": sorted(buckets.keys()),
        "last_session_seen": last_sid,
    }
    (outdir / "_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def main(argv: list[str]) -> int:
    # Last arg = outdir; all preceding args = indirs.
    if len(argv) < 2:
        print("usage: build_events_stream.py <indir> [<indir2> ...] <outdir>", file=sys.stderr)
        return 2
    outdir = Path(argv[-1])
    indirs = [Path(a) for a in argv[:-1]]
    meta = build(indirs, outdir)
    print(f"✅ events stream built: {meta['events_total']} events across "
          f"{meta['distinct_targets']} targets in {len(meta['months'])} months")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
