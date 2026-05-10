"""Session loaders — convert OmniGraph extractions / event stream into
the hr/ `Session` shape.

Two entry points:
  load_sessions_from_extractions(dirs)   — reads pilot/qwen/<prov>/<sid>.json
                                            (or any dir of the same shape)
  load_sessions_from_event_stream(dir)   — reads pilot/events/<YYYY-MM>.jsonl

Both return list[Session] and apply canonicalize_session on the way in.
"""
from __future__ import annotations
import datetime as _dt
import json
from collections import Counter, defaultdict
from pathlib import Path

from .types import Session

try:
    from canonical_slugs import canonicalize_session  # type: ignore
except Exception:
    def canonicalize_session(s):
        return s


def _session_date(session: dict, session_file: Path | None) -> str:
    meta = session.get("session_meta") or {}
    for ts in (meta.get("timestamp_start"), meta.get("timestamp_end")):
        if isinstance(ts, str) and len(ts) >= 10:
            return ts[:10]
    sid = str(session.get("session_id") or "")
    if sid.isdigit() and len(sid) >= 13:
        try:
            return _dt.datetime.utcfromtimestamp(int(sid) / 1000).date().isoformat()
        except Exception:
            pass
    if session_file is not None:
        try:
            return _dt.datetime.utcfromtimestamp(session_file.stat().st_mtime).date().isoformat()
        except Exception:
            pass
    return "1970-01-01"


def _iter_extraction_files(dirs: list[Path]):
    seen: set[str] = set()
    for d in dirs:
        for f in sorted(d.glob("*/*.json")):
            stem = f.stem
            if stem == "global_profile" or "_logs" in str(f) or "_run_summary" in stem:
                continue
            if stem in seen:
                continue
            seen.add(stem)
            yield f


def load_sessions_from_extractions(indirs) -> list[Session]:
    if isinstance(indirs, (str, Path)):
        indirs = [Path(indirs)]
    else:
        indirs = [Path(p) for p in indirs]

    out: list[Session] = []
    for f in _iter_extraction_files(indirs):
        try:
            s = json.loads(f.read_text())
        except Exception:
            continue
        canonicalize_session(s)
        sid = str(s.get("session_id") or f.stem)
        provider = s.get("provider")
        date = _session_date(s, f)

        event_counts: Counter[str] = Counter()
        valence_by_target: dict[str, str] = {}
        concern_targets: set[str] = set()
        valence_votes: dict[str, Counter] = defaultdict(Counter)

        for ev in s.get("mention_events") or []:
            if not isinstance(ev, dict):
                continue
            tid = ev.get("target_id")
            if not isinstance(tid, str) or not tid:
                continue
            event_counts[tid] += 1
            v = ev.get("valence")
            if isinstance(v, str):
                valence_votes[tid][v] += 1
            mt = ev.get("mention_type") or ""
            if isinstance(mt, str) and mt.startswith("concern"):
                concern_targets.add(tid)

        for tid, votes in valence_votes.items():
            valence_by_target[tid] = votes.most_common(1)[0][0]

        out.append(Session(
            id=sid,
            date=date,
            targets=sorted(event_counts.keys()),
            provider=provider,
            event_counts=dict(event_counts),
            valence_by_target=valence_by_target,
            concern_targets=concern_targets,
        ))
    return out


def load_sessions_from_event_stream(events_dir) -> list[Session]:
    """Build Session list from materialized pilot/events/*.jsonl."""
    events_dir = Path(events_dir)
    per_sid: dict[str, dict] = {}
    valence_votes: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    event_counts: dict[str, Counter] = defaultdict(Counter)
    concern_flags: dict[str, set] = defaultdict(set)

    for f in sorted(events_dir.glob("*.jsonl")):
        for line in f.open():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            sid = r.get("session_id")
            tid = r.get("target_id")
            if not sid or not tid:
                continue
            s = per_sid.setdefault(sid, {
                "id": sid,
                "date": (r.get("ts") or "")[:10] or "1970-01-01",
                "provider": r.get("provider"),
            })
            # keep earliest ts as canonical date
            d = (r.get("ts") or "")[:10]
            if d and d < s["date"]:
                s["date"] = d
            event_counts[sid][tid] += 1
            v = r.get("valence")
            if isinstance(v, str):
                valence_votes[sid][tid][v] += 1
            mt = r.get("mention_type") or ""
            if isinstance(mt, str) and mt.startswith("concern"):
                concern_flags[sid].add(tid)

    out: list[Session] = []
    for sid, stub in per_sid.items():
        ec = event_counts[sid]
        vbt = {t: votes.most_common(1)[0][0] for t, votes in valence_votes[sid].items()}
        out.append(Session(
            id=sid,
            date=stub["date"],
            provider=stub.get("provider"),
            targets=sorted(ec.keys()),
            event_counts=dict(ec),
            valence_by_target=vbt,
            concern_targets=concern_flags.get(sid, set()),
        ))
    return out
