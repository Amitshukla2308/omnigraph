#!/usr/bin/env python3
"""Emit og_artifacts/ripple/ from current session extractions.

Runs `hr.build_all` over the extractions and writes:

    og_artifacts/ripple/_index.json   — full HRBundle (cochange + communities + criticality)
    og_artifacts/ripple/<safe>.json   — per-target shard with neighbors+criticality

Atelier's MCP `omnigraph_ripple_neighbors` tool reads from this dir; loadOgArtifact
already understands the layout (load.ts:170, ripplePath helper).

No LLM calls. Pure read-and-aggregate over already-extracted sessions.

Usage:
    python scripts/emit_ripple.py --atelier-root ~/informed-vibes/atelier
    python scripts/emit_ripple.py --atelier-root ~/informed-vibes/atelier --extractions pilot/full
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Local import — script lives in omnigraph/scripts/, hr lives in omnigraph/src/hr/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hr import build_all, load_sessions_from_extractions  # type: ignore  # noqa: E402


SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(target: str) -> str:
    """Map a target string to a stable filename. Path-like targets become hyphenated."""
    s = SAFE_RE.sub("-", target).strip("-")
    return (s or "unnamed")[:120]


def emit(atelier_root: Path, extraction_dirs: list[Path]) -> dict:
    sessions = load_sessions_from_extractions([str(p) for p in extraction_dirs])
    if not sessions:
        return {"ok": False, "reason": "no sessions found in given extraction dirs", "dirs": [str(p) for p in extraction_dirs]}

    bundle = build_all(sessions)
    out_root = atelier_root / "og_artifacts" / "ripple"
    out_root.mkdir(parents=True, exist_ok=True)

    # Combined index (full bundle) — small enough for the MCP tool to read once.
    index = bundle.to_json()
    (out_root / "_index.json").write_text(json.dumps(index, indent=2))

    # Per-target shards (neighbors + criticality) — for cheap lookup by file/module.
    edges = (index.get("cochange") or {}).get("edges") or {}
    crit = (index.get("criticality") or {}).get("modules") or {}
    shard_count = 0
    for target, neighbors in edges.items():
        shard = {
            "target": target,
            "neighbors": neighbors,
            "criticality": crit.get(target),
        }
        (out_root / f"{safe_filename(target)}.json").write_text(json.dumps(shard, indent=2))
        shard_count += 1

    # Bump _manifest.json's ripple count if present.
    manifest_path = atelier_root / "og_artifacts" / "_manifest.json"
    try:
        if manifest_path.is_file():
            m = json.loads(manifest_path.read_text())
            m.setdefault("ripple", {})["count"] = shard_count
            m["lastRippleEmit"] = bundle.meta
            manifest_path.write_text(json.dumps(m, indent=2))
    except Exception as e:
        print(f"  [warn] manifest update skipped: {e}", file=sys.stderr)

    return {
        "ok": True,
        "sessions": len(sessions),
        "modules": len(edges),
        "shards": shard_count,
        "bundle_meta": bundle.meta,
        "out_root": str(out_root),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atelier-root", required=True, type=Path)
    ap.add_argument("--extractions", nargs="+", default=None,
                    help="One or more extraction dirs. Default: <omnigraph_root>/pilot/full")
    args = ap.parse_args()

    atelier_root = args.atelier_root.expanduser().resolve()
    if not atelier_root.is_dir():
        print(f"[emit-ripple] atelier root not found: {atelier_root}")
        return 2

    if args.extractions:
        ex_dirs = [Path(p).expanduser().resolve() for p in args.extractions]
    else:
        ex_dirs = [ROOT / "pilot" / "full"]
    ex_dirs = [p for p in ex_dirs if p.is_dir()]
    if not ex_dirs:
        print("[emit-ripple] no valid extraction dir found")
        return 2

    result = emit(atelier_root, ex_dirs)
    print(f"[emit-ripple] {json.dumps(result, indent=2)}")
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
