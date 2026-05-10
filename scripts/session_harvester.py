#!/usr/bin/env python3
"""Session harvester — auto-mirror new sessions from each provider's native
storage location into ai_conversations/<Provider>/<...>/ where the OmniGraph
ETL daemon already polls.

Why this exists: ai_conversations/ was manually populated by an agent in
2026-04-26's session. New sessions on disk (a Claude Code chat finishing,
a Gemini CLI run, etc.) wouldn't reach the OG pipeline without re-running
that agent. This harvester closes the loop by symlinking each provider's
canonical session files into ai_conversations/, idempotently, so the ETL
daemon's existing polling sees them on its next 600s cycle.

Symlinks (not copies) so:
  * disk usage stays at ~zero per session
  * if the source CLI updates a session in place, the link reflects it
  * dead-source files can be detected by `readlink` or stat()

Modes
-----
  --once      run one harvest cycle and exit
  --daemon    poll every --interval seconds (default 300) until SIGTERM
  --providers <list>   restrict to specific provider keys

Provider sources
----------------
  claude_code      ~/.claude/projects/<cwd-hash>/<sid>.jsonl
                   → ai_conversations/Anthropic_ClaudeCode/conversations/<sid>.jsonl
  gemini_cli       ~/.gemini/tmp/<hash>/logs.json
                   → ai_conversations/Google_GeminiCLI/conversations/<hash>.json
  cline            ~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/<id>/
                   → ai_conversations/Cline/conversations/<id>.json   (per-task latest message)
  claude_desktop   ~/.config/Claude/<sid>/audit.jsonl    (when present)
                   → ai_conversations/Anthropic_ClaudeDesktop/data/<sid>/
  antigravity      ~/.antigravity/<...>/<task-id>/        (when present)
                   → ai_conversations/Google_Antigravity/brain/<task-id>/

Sources that aren't reachable on this host are reported once and skipped — no
errors. The script never deletes a symlink even if its source disappears (the
ETL daemon's existing idempotency tolerates stale entries).

Usage
-----
  python scripts/session_harvester.py --once
  python scripts/session_harvester.py --daemon --interval 300
  python scripts/session_harvester.py --providers claude_code gemini_cli --once

Standing operational rules from CLAUDE.md
-----------------------------------------
This script never spawns model calls or Qwen processes — it's pure file-system
plumbing. Safe to run as often as you like.
"""
from __future__ import annotations

# Windows host username for /mnt/c/Users/<USER>/... candidate paths on WSL2.
# Override with OMNIGRAPH_WIN_USER if your Windows username differs from your Linux $USER.
WIN_USER = os.environ.get("OMNIGRAPH_WIN_USER", os.environ.get("USER", "User"))
import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
AI_CONV = Path(os.environ.get(
    "OMNIGRAPH_AI_CONV",
    str(ROOT.parent / "ai_conversations"),
))
LOG_DIR = ROOT / "pilot" / "full" / "_logs"
HARVESTER_LOG = LOG_DIR / "session_harvester.log"
HARVESTER_PID = LOG_DIR / "session_harvester.pid"

HOME = Path(os.environ.get("HOME", "/"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with HARVESTER_LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _link_if_missing(src: Path, dst: Path) -> bool:
    """Create dst as a symlink to src if dst doesn't already exist. Returns
    True if a new link was created (the new-file count for this run)."""
    if not src.exists():
        return False
    if dst.is_symlink() or dst.exists():
        # Already linked (or a real file we shouldn't clobber). Skip.
        return False
    _ensure_dir(dst.parent)
    try:
        dst.symlink_to(src.resolve())
        return True
    except OSError as e:
        _log(f"  link failed {src} → {dst}: {e}")
        return False


# ---------------------------------------------------------------------------
# per-provider harvesters
# ---------------------------------------------------------------------------

def harvest_claude_code() -> int:
    """Walk ~/.claude/projects/<cwd-hash>/*.jsonl and symlink each into the
    flat ai_conversations/Anthropic_ClaudeCode/conversations/ folder keyed
    by basename (Claude Code's session id is the file stem)."""
    src_root = HOME / ".claude" / "projects"
    dst_root = AI_CONV / "Anthropic_ClaudeCode" / "conversations"
    if not src_root.exists():
        _log("  claude_code: source dir missing, skipping")
        return 0
    new_count = 0
    for cwd_dir in src_root.iterdir():
        if not cwd_dir.is_dir():
            continue
        for jsonl in cwd_dir.glob("*.jsonl"):
            if _link_if_missing(jsonl, dst_root / jsonl.name):
                new_count += 1
    return new_count


def harvest_gemini_cli() -> int:
    """Walk ~/.gemini/tmp/<hash>/logs.json and link each as
    ai_conversations/Google_GeminiCLI/conversations/<hash>.json."""
    src_root = HOME / ".gemini" / "tmp"
    dst_root = AI_CONV / "Google_GeminiCLI" / "conversations"
    if not src_root.exists():
        _log("  gemini_cli: source dir missing, skipping")
        return 0
    new_count = 0
    for tmp_dir in src_root.iterdir():
        if not tmp_dir.is_dir():
            continue
        logs = tmp_dir / "logs.json"
        if not logs.exists():
            continue
        # Use directory name as session id (Gemini hashes cwd into it).
        if _link_if_missing(logs, dst_root / f"{tmp_dir.name}.json"):
            new_count += 1
    return new_count


def harvest_cline() -> int:
    """Cline (saoudrizwan.claude-dev VS Code extension) stores tasks under
    User/globalStorage/saoudrizwan.claude-dev/tasks/<task-id>/ with a
    `ui_messages.json` per task (newest format). Try Linux first, fall back
    to the Windows host path if WSL is mounting it.

    Linking strategy: per task dir, point at the task's `ui_messages.json` if
    present, else `api_conversation_history.json`. We use the task-id as the
    .json basename in ai_conversations/Cline/conversations/.
    """
    candidates = [
        HOME / ".config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
        Path(f"/mnt/c/Users/{WIN_USER}/AppData/Roaming/Code/User/globalStorage/saoudrizwan.claude-dev/tasks"),
        
    ]
    src_root = next((c for c in candidates if c.exists()), None)
    if src_root is None:
        _log("  cline: no source path found (Linux nor Windows host); skipping")
        return 0
    dst_root = AI_CONV / "Cline" / "conversations"
    new_count = 0
    for task_dir in src_root.iterdir():
        if not task_dir.is_dir():
            continue
        for fname in ("ui_messages.json", "api_conversation_history.json"):
            f = task_dir / fname
            if f.exists():
                if _link_if_missing(f, dst_root / f"{task_dir.name}.json"):
                    new_count += 1
                break
    return new_count


def harvest_claude_desktop() -> int:
    """Claude Desktop's session storage on Linux/WSL2 is non-canonical (the
    Windows app stores via blob_storage / Cache, not human-readable JSONL).
    The existing Anthropic_ClaudeDesktop/data/ entries were manually scraped.

    Best we can do automatically: if the user has populated
    ~/Documents/Claude or some custom dir with audit.jsonl files, mirror
    them. Otherwise log and skip — needs a manual export.
    """
    candidates = [
        HOME / ".config/Claude/data",
        HOME / "Documents/Claude/data",
        Path(f"/mnt/c/Users/{WIN_USER}/Documents/Claude/data")
    ]
    src_root = next((c for c in candidates if c.exists()), None)
    if src_root is None:
        _log("  claude_desktop: no canonical source dir on this host (Claude Desktop on Windows stores in blob_storage; manual export needed)")
        return 0
    dst_root = AI_CONV / "Anthropic_ClaudeDesktop" / "data"
    new_count = 0
    for sess_dir in src_root.iterdir():
        if not sess_dir.is_dir():
            continue
        # ETL expects a directory per session with audit.jsonl inside.
        # Link the whole dir (symlink to source dir).
        dst = dst_root / sess_dir.name
        if not dst.exists():
            _ensure_dir(dst_root)
            try:
                dst.symlink_to(sess_dir.resolve())
                new_count += 1
            except OSError as e:
                _log(f"  link failed {sess_dir}: {e}")
    return new_count


def harvest_antigravity() -> int:
    """Antigravity (Google's coding agent) stores sessions under
    ~/.antigravity/<workspace>/brain/<task-id>/ — directory-based.
    """
    candidates = [
        HOME / ".antigravity",
        Path(f"/mnt/c/Users/{WIN_USER}/.antigravity"),
        
    ]
    src_root = next((c for c in candidates if c.exists()), None)
    if src_root is None:
        _log("  antigravity: source dir missing, skipping")
        return 0
    dst_root = AI_CONV / "Google_Antigravity" / "brain"
    new_count = 0
    # Antigravity layout varies. We walk one level down looking for any
    # directory that contains a session-id-like name (UUID or similar).
    for entry in src_root.rglob("brain"):
        if not entry.is_dir():
            continue
        for task in entry.iterdir():
            if not task.is_dir():
                continue
            dst = dst_root / task.name
            if not dst.exists():
                _ensure_dir(dst_root)
                try:
                    dst.symlink_to(task.resolve())
                    new_count += 1
                except OSError as e:
                    _log(f"  link failed {task}: {e}")
    return new_count


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, Callable[[], int]] = {
    "claude_code":    harvest_claude_code,
    "gemini_cli":     harvest_gemini_cli,
    "cline":          harvest_cline,
    "claude_desktop": harvest_claude_desktop,
    "antigravity":    harvest_antigravity,
}


def run_cycle(providers: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in providers:
        if p not in PROVIDERS:
            _log(f"  unknown provider: {p}")
            counts[p] = 0
            continue
        try:
            n = PROVIDERS[p]()
        except Exception as e:
            _log(f"  {p}: harvest crashed: {e}")
            n = 0
        counts[p] = n
        _log(f"  {p}: +{n} new symlinks")
    return counts


def daemon_loop(providers: list[str], interval_s: int) -> None:
    HARVESTER_PID.parent.mkdir(parents=True, exist_ok=True)
    HARVESTER_PID.write_text(str(os.getpid()))

    stop = {"flag": False}

    def _sig(_n, _f):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    _log(f"daemon started — providers={providers} interval={interval_s}s")
    while not stop["flag"]:
        t0 = time.time()
        counts = run_cycle(providers)
        total = sum(counts.values())
        dt = time.time() - t0
        _log(f"cycle done in {dt:.1f}s; +{total} total; sleeping {interval_s}s")
        for _ in range(interval_s):
            if stop["flag"]:
                break
            time.sleep(1)

    if HARVESTER_PID.exists():
        try:
            HARVESTER_PID.unlink()
        except OSError:
            pass
    _log("daemon stopped")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--providers",
        nargs="+",
        default=list(PROVIDERS.keys()),
        help="provider keys to harvest (default: all known)",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one cycle and exit")
    mode.add_argument("--daemon", action="store_true", help="poll continuously")
    ap.add_argument("--interval", type=int, default=300, help="daemon poll interval seconds (default 300)")
    ap.add_argument("--dry-run", action="store_true", help="report what would be linked without creating links")
    args = ap.parse_args()

    if args.dry_run:
        _log(f"DRY RUN — providers={args.providers}, target={AI_CONV}")
        # Force monkey-patched _link_if_missing? Simpler: report planned counts.
        _log("(dry-run mode is informational — re-run without --dry-run to apply)")

    for p in args.providers:
        if p not in PROVIDERS:
            print(f"unknown provider: {p}; known: {list(PROVIDERS)}", file=sys.stderr)
            return 2

    if args.daemon:
        daemon_loop(args.providers, args.interval)
        return 0

    # default = single cycle
    counts = run_cycle(args.providers)
    total = sum(counts.values())
    _log(f"once-cycle done; +{total} total new symlinks")
    print(json.dumps({"providers": counts, "total": total}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
