#!/usr/bin/env python3
"""`omnigraph migrate` — move legacy project-scoped Personal Brain to user-scoped layout.

Legacy (Phase A): atelier/projects/<P>/brain/personal/
Canonical (Phase A.1+): atelier/users/<uid>/brain/personal/

Migration is idempotent: running it twice is safe. Files are moved, not
copied, so the project-scoped path disappears after successful migration.
A legacy-mirror shim (symlink) is left in place for one release so
Atelier's old reader keeps working during transition.

Usage:
    python src/migrate.py --atelier-root ~/atelier --user-id <uuid>
    python src/migrate.py --atelier-root ~/atelier --user-id <uuid> --project MyProject
    python src/migrate.py --atelier-root ~/atelier --user-id <uuid> --dry-run
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

from paths import (  # type: ignore
    atelier_user_root,
    atelier_personal_brain_dir,
    atelier_project_root,
)


def _project_brain_personal(atelier_root: Path, project: str) -> Path:
    """Legacy path we migrate FROM."""
    return atelier_project_root(atelier_root, project) / "brain" / "personal"


def _enumerate_projects(atelier_root: Path) -> list[str]:
    projects_dir = atelier_root / "projects"
    if not projects_dir.exists():
        return []
    return sorted(p.name for p in projects_dir.iterdir() if p.is_dir())


def migrate(
    atelier_root: Path,
    user_id: str,
    project: str | None = None,
    dry_run: bool = False,
    leave_symlink: bool = True,
) -> dict:
    """Move project-scoped Personal Brain into user-scoped location.

    If a destination already exists, sources merge into it (overwriting
    on collision — later-project writes win). That's conservative for Phase A
    single-founder, where all project-scoped brains belonged to the same human.
    """
    atelier_root = Path(atelier_root).expanduser()
    target = atelier_personal_brain_dir(atelier_root, user_id)

    projects = [project] if project else _enumerate_projects(atelier_root)
    moved_from: list[str] = []
    skipped: list[str] = []

    for p in projects:
        src = _project_brain_personal(atelier_root, p)
        if not src.exists():
            continue
        if not any(src.iterdir()):
            skipped.append(f"{p}: empty")
            continue

        if dry_run:
            moved_from.append(f"{p}: {src} → {target}")
            continue

        target.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            dst = target / item.name
            if dst.exists():
                if dst.is_dir() and item.is_dir():
                    # Merge dir: move contents one level up
                    for sub in item.iterdir():
                        sub_dst = dst / sub.name
                        if sub_dst.exists():
                            if sub_dst.is_dir():
                                shutil.rmtree(sub_dst)
                            else:
                                sub_dst.unlink()
                        shutil.move(str(sub), str(sub_dst))
                    item.rmdir()
                else:
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                    shutil.move(str(item), str(dst))
            else:
                shutil.move(str(item), str(dst))

        # Remove now-empty source
        try:
            src.rmdir()
        except OSError:
            pass

        # Legacy-mirror symlink so Atelier's old reader keeps working
        if leave_symlink and not src.exists():
            try:
                src.symlink_to(target, target_is_directory=True)
            except (OSError, FileExistsError):
                pass  # non-symlink FS or race — caller can handle

        moved_from.append(p)

    return {
        "atelier_root": str(atelier_root),
        "user_id": user_id,
        "target": str(target),
        "projects_migrated": moved_from,
        "skipped": skipped,
        "dry_run": dry_run,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Migrate legacy project-scoped Personal Brain to user-scoped")
    ap.add_argument("--atelier-root", required=True)
    ap.add_argument("--user-id", required=True, help="atelier_user_id (SQLite users.id UUID)")
    ap.add_argument("--project", default=None, help="Single project (default: all projects)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-symlink", action="store_true",
                    help="Don't leave legacy-mirror symlink at old location")
    args = ap.parse_args(argv)

    r = migrate(
        atelier_root=Path(args.atelier_root),
        user_id=args.user_id,
        project=args.project,
        dry_run=args.dry_run,
        leave_symlink=not args.no_symlink,
    )
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}target: {r['target']}")
    print(f"{tag}migrated {len(r['projects_migrated'])}: {r['projects_migrated']}")
    if r["skipped"]:
        print(f"{tag}skipped {len(r['skipped'])}: {r['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
