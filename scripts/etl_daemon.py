"""Continuous ETL daemon for OmniGraph.

Polls each provider's source location at a fixed interval; when new
sessions appear, invokes phase4_scale.py for that provider with
QWEN_LOCK_PRIORITY=9 (lowest — every other Qwen consumer preempts).
On successful completion of a provider batch, fires the post-ETL hook
(stage-2 aggregate + Phase 1 compile + Phase 2 publish).

Usage:
    python scripts/etl_daemon.py                      # all providers, 600s poll
    python scripts/etl_daemon.py --providers gemini_cli --once   # one cycle, pilot
    python scripts/etl_daemon.py --interval 300       # 5-min poll
    python scripts/etl_daemon.py --no-hook            # skip post-ETL hook (debug)

Idempotency: defers to phase4_scale.py's existing skip-if-already-extracted
behavior. We only invoke phase4_scale if `unprocessed_count(provider) > 0`.

GPU coordination: the daemon does NOT hold the lock itself. Each spawned
qwen_pipeline.py worker acquires the lock per-call at priority 9. Higher-
priority callers (atelier reflect at p2, ad-hoc at p1) preempt cleanly
between qwen_call invocations.

Reconciliation: on startup, the daemon walks pilot/full/<provider>/ and
ensures `processed_sessions.log` (if it exists) reflects disk truth.
This closes the 'progress.jsonl drift' gap.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ai_conversations lives at ~/informed-vibes/data/ai_conversations (under
# data/ zone, not at root). OMNIGRAPH_AI_CONV env override matches src/sources/*.py.
AI_CONV = Path(os.environ.get("OMNIGRAPH_AI_CONV",
                              str(ROOT.parent.parent / "data" / "ai_conversations")))
PILOT = ROOT / "pilot"
OUT_FULL = PILOT / "full"
LOG_DIR = OUT_FULL / "_logs"
DAEMON_PID_FILE = LOG_DIR / "etl_daemon.pid"
DAEMON_LOG_FILE = LOG_DIR / "etl_daemon.log"

# (provider_key, source_glob)
PROVIDER_SOURCES = {
    "claude_desktop": (AI_CONV / "Anthropic_ClaudeDesktop" / "data", "*"),
    "claude_code":    (AI_CONV / "Anthropic_ClaudeCode" / "conversations", "*.jsonl"),
    "gemini_cli":     (AI_CONV / "Google_GeminiCLI" / "conversations", "*.json"),
    "cline":          (AI_CONV / "Cline" / "conversations", "*.json"),
    "antigravity":    (AI_CONV / "Google_Antigravity" / "brain", "*"),
}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with DAEMON_LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _normalize_source_name(provider: str, raw: str) -> str:
    """Match the stem that phase4_scale actually writes under pilot/full/<provider>/.

    Provider-specific renames:
      - claude_desktop strips a `local_` prefix from source dir names.
    """
    if provider == "claude_desktop" and raw.startswith("local_"):
        return raw[len("local_"):]
    return raw


def _list_source_sessions(provider: str) -> set[str]:
    src_dir, pattern = PROVIDER_SOURCES[provider]
    if not src_dir.exists():
        return set()
    if pattern == "*":
        names = (p.name for p in src_dir.iterdir() if p.is_dir())
    else:
        names = (Path(p).stem for p in glob.glob(str(src_dir / pattern)))
    return {_normalize_source_name(provider, n) for n in names}


def _list_extracted_sessions(provider: str) -> set[str]:
    out_dir = OUT_FULL / provider
    if not out_dir.exists():
        return set()
    return {p.stem for p in out_dir.glob("*.json")}


def _unprocessed_count(provider: str) -> int:
    src = _list_source_sessions(provider)
    done = _list_extracted_sessions(provider)
    return len(src - done)


def _phase4_scale_running(provider: str) -> int | None:
    """Return PID if phase4_scale is already running for provider, else None."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", "phase4_scale.py"], text=True
        )
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        if f"phase4_scale.py {provider}" in line:
            try:
                return int(line.split(None, 1)[0])
            except (ValueError, IndexError):
                continue
    return None


def _reconcile(provider: str) -> dict:
    """Walk pilot/full/<provider>/ and emit a disk-truth manifest."""
    manifest = {
        "provider": provider,
        "scanned_at": time.time(),
        "extracted_count": 0,
        "source_count": 0,
        "unprocessed_count": 0,
    }
    src = _list_source_sessions(provider)
    done = _list_extracted_sessions(provider)
    manifest["source_count"] = len(src)
    manifest["extracted_count"] = len(done)
    manifest["unprocessed_count"] = len(src - done)
    return manifest


# ----------------------------------------------------------------------
# orchestration
# ----------------------------------------------------------------------

def run_provider_batch(provider: str, dry_run: bool = False) -> dict:
    """Invoke phase4_scale.py <provider> with lock-aware env.

    Returns a result dict with: provider, started_at, ended_at, returncode, pid.
    """
    result = {
        "provider": provider,
        "started_at": time.time(),
        "ended_at": None,
        "returncode": None,
        "pid": None,
        "skipped": False,
        "skip_reason": None,
    }

    existing = _phase4_scale_running(provider)
    if existing is not None:
        result["skipped"] = True
        result["skip_reason"] = f"phase4_scale already running (pid {existing})"
        return result

    unprocessed = _unprocessed_count(provider)
    if unprocessed == 0:
        result["skipped"] = True
        result["skip_reason"] = "no unprocessed sessions"
        return result

    _log(f"[{provider}] {unprocessed} unprocessed sessions — starting phase4_scale")
    if dry_run:
        result["skipped"] = True
        result["skip_reason"] = f"dry-run; would process {unprocessed} sessions"
        return result

    env = os.environ.copy()
    env["QWEN_LOCK_HOLDER"] = f"etl_daemon:{provider}"
    env["QWEN_LOCK_PRIORITY"] = "9"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"daemon_{provider}.log"
    with log_path.open("a") as logf:
        logf.write(f"\n--- daemon-spawned phase4_scale {provider} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        logf.flush()
        proc = subprocess.Popen(
            ["python", str(ROOT / "src" / "phase4_scale.py"), provider],
            cwd=str(ROOT),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        result["pid"] = proc.pid
        rc = proc.wait()
        result["returncode"] = rc
    result["ended_at"] = time.time()
    _log(f"[{provider}] phase4_scale exited rc={result['returncode']} "
         f"in {result['ended_at']-result['started_at']:.1f}s")
    return result


ATELIER_ROOT = ROOT.parent / "atelier"  # sibling directory


def _atelier_user_ids() -> list[str]:
    users_dir = ATELIER_ROOT / "data" / "users"
    if not users_dir.exists():
        return []
    return sorted(p.name for p in users_dir.iterdir() if p.is_dir())


def post_etl_hook(state_dir: str = "pilot/full") -> dict:
    """Stage-2 aggregate → compile artifacts locally → publish to every Atelier user.

    Each step is a separate subprocess; failures don't cascade. Lock priority 5
    (batch) so any interactive / atelier-reflect / brain-viz work preempts.
    """
    out = {"steps": []}
    env = os.environ.copy()
    env["QWEN_LOCK_HOLDER"] = "post_etl_hook"
    env["QWEN_LOCK_PRIORITY"] = "5"

    steps = [
        ("aggregate", ["python", "src/omnigraph_cli.py", "aggregate", "--full",
                       "--indir", state_dir, "--state", state_dir]),
        ("dedup_global_profile", ["python", "scripts/dedup_global_profile.py",
                                  "--state", state_dir]),
    ]
    # Local compile (canonical artifacts under pilot/full/compiled/).
    compiled_dir = Path(state_dir) / "compiled"
    compiled_dir.mkdir(parents=True, exist_ok=True)
    targets = [
        ("light_ir", "light_ir.xml"),                  # legacy back-compat
        ("light_ir_global", "light_ir.global.xml"),    # 3-layer brain — global
        ("light_ir_personal", "light_ir.personal.xml"), # 3-layer brain — personal
        ("claude_md", "claude.md"),
        ("boot_context", "boot_context.json"),
        ("cursor_rules", "cursor.rules"),
        ("brain_view", "brain_view.json"),
    ]
    for target, fname in targets:
        steps.append((
            f"compile_local_{target}",
            ["python", "src/omnigraph_cli.py", "compile", target,
             "--state", state_dir, "--out", str(compiled_dir / fname)],
        ))
    # Per-project brain sweep (one artifact per discovered scope).
    steps.append((
        "compile_local_projects",
        ["python", "src/omnigraph_cli.py", "compile-projects",
         "--state", state_dir, "--out-dir", str(compiled_dir / "projects")],
    ))
    # B2 (2026-04-26): publish a SHARED project brain to
    # atelier/projects/<P>/brain.xml for every real Atelier project dir.
    # The Atelier reader prefers this shared path over the per-user copies
    # so co-founders touching the same project read the same brain.
    atelier_projects_dir = ATELIER_ROOT / "projects"
    if atelier_projects_dir.exists():
        for proj_dir in sorted(atelier_projects_dir.iterdir()):
            if not proj_dir.is_dir():
                continue
            steps.append((
                f"publish_shared_project_{proj_dir.name}",
                ["python", "src/omnigraph_cli.py", "compile", "light_ir_project",
                 "--state", state_dir,
                 "--project", proj_dir.name,
                 "--out", str(proj_dir / "brain.xml")],
            ))
    # Atelier publish — once per user, including the project sweep.
    for uid in _atelier_user_ids():
        for target, _ in targets:
            steps.append((
                f"publish_{uid[:8]}_{target}",
                ["python", "src/omnigraph_cli.py", "compile", target,
                 "--state", state_dir,
                 "--atelier-root", str(ATELIER_ROOT),
                 "--user-id", uid],
            ))
        steps.append((
            f"publish_{uid[:8]}_projects",
            ["python", "src/omnigraph_cli.py", "compile-projects",
             "--state", state_dir,
             "--atelier-root", str(ATELIER_ROOT),
             "--user-id", uid],
        ))

    for name, cmd in steps:
        t0 = time.time()
        try:
            rc = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=1800).returncode
        except subprocess.TimeoutExpired:
            rc = -1
        out["steps"].append({"name": name, "rc": rc, "elapsed_s": round(time.time()-t0, 1)})
        _log(f"  hook step {name}: rc={rc}")
    return out


# ----------------------------------------------------------------------
# daemon loop
# ----------------------------------------------------------------------

def write_pid():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_PID_FILE.write_text(str(os.getpid()))


def clear_pid():
    try:
        DAEMON_PID_FILE.unlink()
    except FileNotFoundError:
        pass


_STOP = False


def _handle_signal(signum, frame):
    global _STOP
    _STOP = True
    _log(f"received signal {signum} — will exit after current cycle")


def daemon_loop(providers: list[str], interval_s: int, run_hook: bool, once: bool, dry_run: bool):
    write_pid()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    _log(f"daemon started — providers={providers} interval={interval_s}s once={once} dry_run={dry_run}")
    try:
        while not _STOP:
            cycle_started = time.time()
            cycle_results = []
            for provider in providers:
                if _STOP:
                    break
                manifest = _reconcile(provider)
                _log(f"[{provider}] source={manifest['source_count']} "
                     f"done={manifest['extracted_count']} "
                     f"unprocessed={manifest['unprocessed_count']}")
                if manifest["unprocessed_count"] == 0:
                    continue
                res = run_provider_batch(provider, dry_run=dry_run)
                cycle_results.append(res)
                if res.get("returncode") == 0 and run_hook and not dry_run:
                    _log(f"[{provider}] firing post-ETL hook")
                    post_etl_hook()

            if once:
                _log("--once specified; exiting")
                break

            elapsed = time.time() - cycle_started
            sleep_s = max(0, interval_s - int(elapsed))
            if sleep_s and not _STOP:
                _log(f"cycle done in {elapsed:.1f}s; sleeping {sleep_s}s")
                # interruptible sleep
                slept = 0
                while slept < sleep_s and not _STOP:
                    time.sleep(min(1.0, sleep_s - slept))
                    slept += 1
    finally:
        clear_pid()
        _log("daemon stopped")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", nargs="+",
                    default=list(PROVIDER_SOURCES.keys()),
                    help="Providers to watch (default: all)")
    ap.add_argument("--interval", type=int, default=600,
                    help="Poll interval in seconds (default: 600)")
    ap.add_argument("--once", action="store_true",
                    help="Run one cycle and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would run without invoking phase4_scale")
    ap.add_argument("--no-hook", action="store_true",
                    help="Skip post-ETL hook (debug)")
    ap.add_argument("--reconcile-only", action="store_true",
                    help="Print disk-truth manifests and exit")
    args = ap.parse_args()

    for p in args.providers:
        if p not in PROVIDER_SOURCES:
            print(f"unknown provider: {p}; known: {list(PROVIDER_SOURCES)}", file=sys.stderr)
            return 2

    if args.reconcile_only:
        for p in args.providers:
            print(json.dumps(_reconcile(p), indent=2, default=str))
        return 0

    daemon_loop(
        providers=args.providers,
        interval_s=args.interval,
        run_hook=not args.no_hook,
        once=args.once,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
