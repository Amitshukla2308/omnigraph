"""Export OmniGraph events into HR's git_history.json shape.

HR's 06_build_cochange.py + 09_build_granger.py consume:
  {
    "repositories": [
      {
        "name": "<repo>",
        "commits": [
          { "hash": "<sid>", "date": "YYYY-MM-DD",
            "files_changed": [ {"path": "<canonical_target_id>"}, ... ] }
        ]
      }
    ]
  }

Mapping from OmniGraph:
  - repository: "omnigraph-vault"   (one; clustering by provider is future work)
  - commits:   sessions (one per extracted session JSON)
  - hash:      session_id
  - date:      derived timestamp (event-level → session_meta → mtime fallback)
  - files_changed.path: distinct canonical target_ids mentioned in the session
"""
from __future__ import annotations
import datetime as _dt
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


def _iter_session_files(indirs: list[Path]) -> Iterable[Path]:
    seen: set[str] = set()
    for d in indirs:
        for f in sorted(d.glob("*/*.json")):
            stem = f.stem
            if stem == "global_profile" or "_logs" in str(f) or "_run_summary" in stem:
                continue
            if stem in seen:
                continue
            seen.add(stem)
            yield f


def _session_date(session: dict, session_file: Path) -> str:
    """YYYY-MM-DD for this session. Falls through ts→sid→mtime."""
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
    try:
        return _dt.datetime.utcfromtimestamp(session_file.stat().st_mtime).date().isoformat()
    except Exception:
        return "1970-01-01"


def export_git_history_like(
    session_dirs: list[Path] | list[str] | str | Path,
    out_path: Path | str,
    repo_name: str = "omnigraph-vault",
) -> dict:
    """Produce git_history.json-shaped file from OmniGraph extractions.

    Returns summary dict.
    """
    if isinstance(session_dirs, (str, Path)):
        session_dirs = [Path(session_dirs)]
    else:
        session_dirs = [Path(p) for p in session_dirs]
    out_path = Path(out_path)

    commits = []
    n_files_total = 0
    for f in _iter_session_files(session_dirs):
        try:
            s = json.loads(f.read_text())
        except Exception:
            continue
        canonicalize_session(s)
        sid = str(s.get("session_id") or f.stem)
        date = _session_date(s, f)
        tids: set[str] = set()
        for ev in s.get("mention_events") or []:
            if not isinstance(ev, dict):
                continue
            tid = ev.get("target_id")
            if isinstance(tid, str) and tid:
                tids.add(tid)
        if not tids:
            continue
        commits.append({
            "hash": sid,
            "date": date,
            "files_changed": [{"path": t} for t in sorted(tids)],
        })
        n_files_total += len(tids)

    # HR's 06_build_cochange expects commits in ascending date order.
    commits.sort(key=lambda c: (c["date"], c["hash"]))

    payload = {"repositories": [{"name": repo_name, "commits": commits}]}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return {
        "output": str(out_path),
        "commits": len(commits),
        "file_mentions_total": n_files_total,
    }


def main(argv: list[str]) -> int:
    # export_for_hr.py <session_dir> [<session_dir2> ...] <output_json>
    if len(argv) < 2:
        print("usage: export_for_hr.py <session_dir> [<more_dirs>...] <output_json>", file=sys.stderr)
        return 2
    out = Path(argv[-1])
    dirs = [Path(a) for a in argv[:-1]]
    r = export_git_history_like(dirs, out)
    print(f"✅ HR git_history: {r['commits']} commits, {r['file_mentions_total']} mentions → {r['output']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
