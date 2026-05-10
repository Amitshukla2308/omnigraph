#!/usr/bin/env python3
"""`omnigraph canonicalize --rewrite-canvas <project>` — reconcile Canvas slugs.

Per Turn 6 of omnigraph↔atelier: when the MCP `canonicalize` tool is
unavailable at node-creation time, Atelier's `canvas_propose_node` stores
raw title + `canonicalized: false` and defers the slug. This command
runs idempotently post-hoc to fill in `slug_canonical` + `canonicalized_at`
for every not-yet-canonicalized node.

Pure deterministic — no Qwen, no LLM, just the alias table.

Atelier's Canvas node file shape (per NodeMeta extension discussed in thread):
    {
      "id": "node-uuid",
      "raw_title": "MyProject Parse",
      "slug_canonical": "fastbrick",       // nullable until canonicalized
      "canonicalized_at": "2026-04-24T...", // nullable until canonicalized
      ...other NodeMeta fields
    }

Files live under atelier/projects/<P>/canvas/nodes/<node_id>.json (one file
per node, per existing Atelier convention — backend/src/project/canvas.ts).

Safe to re-run: nodes already carrying a non-null `slug_canonical` are
skipped unless --force is passed.

Usage:
    python src/canonicalize_canvas.py --atelier-root ~/atelier --project MyProject
    python src/canonicalize_canvas.py --atelier-root ~/atelier --project MyProject --dry-run
    python src/canonicalize_canvas.py --atelier-root ~/atelier --project MyProject --force
"""
from __future__ import annotations
import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from canonical_slugs import canonicalize_slug, load_alias_table  # type: ignore


def _iso_now() -> str:
    try:
        return _dt.datetime.now(_dt.UTC).replace(tzinfo=None).isoformat() + "Z"
    except AttributeError:  # pragma: no cover
        return _dt.datetime.utcnow().isoformat() + "Z"


def _canvas_nodes_dir(atelier_root: Path, project: str) -> Path:
    return atelier_root / "projects" / project / "canvas" / "nodes"


def rewrite_canvas(
    atelier_root: Path | str,
    project: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    atelier_root = Path(atelier_root).expanduser()
    nodes_dir = _canvas_nodes_dir(atelier_root, project)
    if not nodes_dir.exists():
        return {
            "atelier_root": str(atelier_root),
            "project": project,
            "error": f"canvas/nodes/ not found at {nodes_dir}",
            "scanned": 0,
            "rewritten": 0,
            "skipped": 0,
        }

    load_alias_table(force=True)  # warm the registry

    now = _iso_now()
    scanned = 0
    rewritten: list[dict] = []
    skipped = 0

    for p in sorted(nodes_dir.glob("*.json")):
        scanned += 1
        try:
            node = json.loads(p.read_text())
        except Exception as e:
            skipped += 1
            print(f"skip {p}: {e}", file=sys.stderr)
            continue

        raw = node.get("raw_title") or node.get("title") or node.get("label")
        if not isinstance(raw, str) or not raw:
            skipped += 1
            continue

        existing = node.get("slug_canonical")
        if existing and not force:
            skipped += 1
            continue

        new_slug = canonicalize_slug(raw)
        if not new_slug:
            skipped += 1
            continue

        changed = (existing or None) != new_slug
        if dry_run:
            if changed:
                rewritten.append({
                    "node_id": node.get("id") or p.stem,
                    "raw_title": raw,
                    "old_slug": existing,
                    "new_slug": new_slug,
                })
            continue

        if not changed:
            skipped += 1
            continue

        node["slug_canonical"] = new_slug
        node["canonicalized_at"] = now
        p.write_text(json.dumps(node, indent=2, ensure_ascii=False))
        rewritten.append({
            "node_id": node.get("id") or p.stem,
            "raw_title": raw,
            "old_slug": existing,
            "new_slug": new_slug,
        })

    return {
        "atelier_root": str(atelier_root),
        "project": project,
        "scanned": scanned,
        "rewritten": len(rewritten),
        "skipped": skipped,
        "dry_run": dry_run,
        "changes": rewritten[:50],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Canonicalize Canvas node slugs post-hoc")
    ap.add_argument("--atelier-root", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="Re-canonicalize nodes that already have a slug")
    args = ap.parse_args(argv)

    r = rewrite_canvas(args.atelier_root, args.project, args.dry_run, args.force)
    if "error" in r:
        print(f"❌ {r['error']}", file=sys.stderr)
        return 2
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}project={r['project']}  scanned={r['scanned']}  rewritten={r['rewritten']}  skipped={r['skipped']}")
    for c in r["changes"][:10]:
        print(f"{tag}  {c['node_id']}: {c['raw_title']!r} → {c['new_slug']!r}  (was {c['old_slug']!r})")
    if r["rewritten"] > 10:
        print(f"{tag}  … +{r['rewritten'] - 10} more")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
