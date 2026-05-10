#!/usr/bin/env python3
"""`omnigraph index` — compute cochange/communities/criticality in-process
using the vendored hr/ package, then persist results.

No subprocess, no git_history round-trip (though export_for_hr remains
available for external tools that expect HR's JSON shape).

Writes:
  <out>/cochange.json        pair-level co-mention weights
  <out>/communities.json     Louvain (or connected-component fallback)
  <out>/criticality.json     composite score per target
  <out>/index_bundle.json    combined view for HTTP/UI consumers

Usage:
  python bridge_cli.py --sessions pilot/qwen --sessions pilot/full --out pilot/hr_out
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # src/
sys.path.insert(0, str(ROOT))

from hr import build_all, load_sessions_from_extractions  # type: ignore
from hr_adapter.export_for_hr import export_git_history_like  # type: ignore


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", action="append", required=True,
                    help="Qwen extraction dir(s). Repeat for multiple.")
    ap.add_argument("--out", required=True, help="Output dir")
    ap.add_argument("--min-weight", type=int, default=None)
    ap.add_argument("--max-targets", type=int, default=60)
    ap.add_argument("--louvain-resolution", type=float, default=1.0)
    ap.add_argument("--louvain-seed", type=int, default=42)
    ap.add_argument("--also-export-git-history", action="store_true",
                    help="Additionally write git_history.json for external tools.")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sessions = load_sessions_from_extractions([Path(p) for p in args.sessions])
    print(f"📦 loaded {len(sessions)} sessions from {len(args.sessions)} extraction dir(s)")

    bundle = build_all(
        sessions,
        min_weight=args.min_weight,
        max_targets_per_session=args.max_targets,
        louvain_resolution=args.louvain_resolution,
        louvain_seed=args.louvain_seed,
    )

    # Persist individual + combined products.
    (out / "cochange.json").write_text(json.dumps(bundle.cochange, indent=2))
    (out / "communities.json").write_text(json.dumps(bundle.communities, indent=2))
    (out / "criticality.json").write_text(json.dumps(bundle.criticality, indent=2))
    (out / "index_bundle.json").write_text(json.dumps(bundle.to_json(), indent=2))

    if args.also_export_git_history:
        gh_path = out / "git_history.json"
        export_git_history_like([Path(p) for p in args.sessions], gh_path)
        print(f"   also wrote {gh_path} for external tooling")

    m = bundle.meta
    print(f"✅ hr bundle → {out}")
    print(f"   sessions={m['sessions']}  "
          f"cochange={m['cochange_modules']} modules / {m['cochange_pairs']} pairs  "
          f"communities={m['communities']}  "
          f"critical_modules={m['critical_modules']}")
    # Show top-10 critical
    top = bundle.criticality["meta"]["top_10"]
    print("\n   Top-10 critical targets:")
    for entry in top:
        print(f"     [{entry['score']:.3f}] {entry['module']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
