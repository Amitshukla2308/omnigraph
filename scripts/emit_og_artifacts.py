#!/usr/bin/env python3
"""Emit the og_artifacts/ portability layout for one Atelier installation.

Reads the current Personal Brain + shared project brains and copies/links
them into the layout the Atelier `backend/src/og_artifacts/load.ts` reader
expects:

    og_artifacts/
    ├── _config.json
    ├── _manifest.json
    ├── brain/
    │   ├── global.xml
    │   ├── personal/<uid>/personal.xml
    │   └── projects/<slug>/project.xml
    ├── ripple/      (passthrough — created empty if absent)
    ├── agents/      (passthrough — created empty if absent)
    └── ledger/      (passthrough — preserved if already present)

This is a non-destructive emit. It does NOT touch the canonical Personal
Brain at data/users/<uid>/brain/personal/ — that stays the source of
truth. The og_artifacts/ layout is a *re-export* designed for cross-tool
consumption (Cursor, Continue.dev, etc.), per RESTRUCTURE_PLAN §3.

No LLM calls. Pure filesystem copy + json write.

Usage:
    python scripts/emit_og_artifacts.py --atelier-root ~/informed-vibes/atelier
    python scripts/emit_og_artifacts.py --atelier-root ~/informed-vibes/atelier --user-id <uuid>
    python scripts/emit_og_artifacts.py --atelier-root ~/informed-vibes/atelier --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

LAYOUT_VERSION = 1
SCHEMA_VERSION = "0.2.1"  # Vault schema version this emit corresponds to.


def slugify(name: str) -> str:
    """Project-name slug — keep alnum + hyphen + underscore + dot, lowercase."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
    return s or "unnamed"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_copy(src: Path, dst: Path, *, dry: bool = False) -> bool:
    """Copy if src exists. Returns True on actual copy."""
    if not src.is_file():
        return False
    if dry:
        print(f"  [dry] would copy {src} → {dst}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def discover_users(atelier_root: Path) -> list[str]:
    users_dir = atelier_root / "data" / "users"
    if not users_dir.is_dir():
        return []
    out: list[str] = []
    for p in users_dir.iterdir():
        if not p.is_dir():
            continue
        # Skip the legacy "default" slot; emit only real user UUIDs.
        if p.name == "default":
            continue
        if (p / "brain" / "personal" / "compiled").is_dir():
            out.append(p.name)
    return sorted(out)


def discover_projects(atelier_root: Path) -> list[tuple[str, Path]]:
    """List (display_name, brain_xml_path) for shared project brains."""
    projects_dir = atelier_root / "projects"
    out: list[tuple[str, Path]] = []
    if not projects_dir.is_dir():
        return out
    for p in sorted(projects_dir.iterdir()):
        if not p.is_dir():
            continue
        brain = p / "brain.xml"
        if brain.is_file():
            out.append((p.name, brain))
    return out


def emit(atelier_root: Path, *, only_user: str | None = None, dry: bool = False) -> dict:
    out_root = atelier_root / "og_artifacts"
    if not dry:
        out_root.mkdir(parents=True, exist_ok=True)

    # 1. Pick a "canonical" global.xml — first user's light_ir.global.xml.
    users = discover_users(atelier_root)
    if only_user:
        users = [u for u in users if u == only_user]
    global_src: Path | None = None
    for uid in users:
        candidate = atelier_root / "data/users" / uid / "brain/personal/compiled/light_ir.global.xml"
        if candidate.is_file():
            global_src = candidate
            break
    global_dst = out_root / "brain" / "global.xml"
    global_written = bool(global_src and safe_copy(global_src, global_dst, dry=dry))

    # 2. Per-user personal.xml.
    personal_written: list[str] = []
    for uid in users:
        src = atelier_root / "data/users" / uid / "brain/personal/compiled/light_ir.personal.xml"
        dst = out_root / "brain" / "personal" / uid / "personal.xml"
        if safe_copy(src, dst, dry=dry):
            personal_written.append(uid)

    # 3. Project brains (shared, project-scoped).
    project_written: list[dict] = []
    for display, brain in discover_projects(atelier_root):
        slug = slugify(display)
        dst = out_root / "brain" / "projects" / slug / "project.xml"
        if safe_copy(brain, dst, dry=dry):
            project_written.append({"slug": slug, "display": display})

    # 4. Ensure passthrough dirs exist (callers expect them, even empty).
    if not dry:
        for sub in ("ripple", "agents", "ledger"):
            (out_root / sub).mkdir(parents=True, exist_ok=True)

    # 5. _config.json + _manifest.json.
    cfg = {
        "layoutVersion": LAYOUT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "lastRun": iso_now(),
        "atelierRoot": str(atelier_root),
    }
    manifest = {
        "lastRun": cfg["lastRun"],
        "brain": {
            "global": global_written,
            "personal": personal_written,
            "projects": project_written,
        },
        "ripple": {"count": 0},   # populated by a future ripple emitter
        "agents": {"compiled": []},  # populated by O4 (agent_principles compiler)
        "ledger": {"present": (out_root / "ledger").is_dir() if not dry else None},
    }
    if not dry:
        (out_root / "_config.json").write_text(json.dumps(cfg, indent=2))
        (out_root / "_manifest.json").write_text(json.dumps(manifest, indent=2))

    return {"config": cfg, "manifest": manifest}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atelier-root", required=True, type=Path,
                    help="Path to the atelier installation (the dir containing data/users/ + projects/)")
    ap.add_argument("--user-id", default=None,
                    help="Restrict to one user uuid; default = emit all discovered users")
    ap.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = ap.parse_args()

    atelier_root = args.atelier_root.expanduser().resolve()
    if not atelier_root.is_dir():
        print(f"[emit-og-artifacts] atelier root not found: {atelier_root}")
        return 2

    result = emit(atelier_root, only_user=args.user_id, dry=args.dry_run)

    print(f"[emit-og-artifacts] root: {atelier_root}/og_artifacts")
    print(f"  layoutVersion={result['config']['layoutVersion']}, schemaVersion={result['config']['schemaVersion']}")
    print(f"  global: {'✓' if result['manifest']['brain']['global'] else '—'}")
    print(f"  personal: {len(result['manifest']['brain']['personal'])} user(s)")
    print(f"  projects: {len(result['manifest']['brain']['projects'])} project(s)")
    if args.dry_run:
        print("  (dry-run — no files written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
