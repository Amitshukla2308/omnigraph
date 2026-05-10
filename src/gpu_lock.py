"""Single-GPU coordination for OmniGraph Qwen consumers.

Why this exists
---------------
LM Studio runs one Qwen instance on the local 5090. Multiple OmniGraph
consumers (ETL daemon, on-demand compiles, brain-viz inference jobs,
Atelier-triggered reflects, ad-hoc user calls) all want it. Without
coordination they thrash or corrupt each other's outputs.

This module provides a file-backed advisory lock with priority-based
preemption. Higher-priority acquirers SIGSTOP a lower-priority holder,
do their work, then SIGCONT it on release.

Priority ladder (lower number = higher priority):
    1 — interactive: the user at the CLI/REPL
    2 — atelier_reflect: session just ended, must complete fast
    3 — domain_brain: founder waiting in approval queue
    4 — brain_viz: founder watching UI render
    5 — batch: vault enrichment, on-demand compile
    9 — etl_daemon: lowest; everything else preempts

Usage
-----
    from gpu_lock import gpu_lock

    with gpu_lock(holder="brain_viz", priority=4):
        # call Qwen
        ...

The context manager handles acquisition, heartbeat, preemption-on-entry,
and release-with-resume. If acquirer priority >= holder priority, it
waits in a polling queue (no preemption — same-priority work is FIFO).
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

LOCK_DIR = Path.home() / ".omnigraph"
LOCK_FILE = LOCK_DIR / "gpu.lock"
PAUSED_FLAG = LOCK_DIR / "gpu.paused"  # holder writes this when SIGSTOP'd

HEARTBEAT_INTERVAL_S = 30.0
STALE_AFTER_S = 90.0  # 3x heartbeat
ACQUIRE_POLL_S = 0.5
PREEMPT_ACK_TIMEOUT_S = 5.0


# ----------------------------------------------------------------------
# data shape
# ----------------------------------------------------------------------

@dataclass
class LockState:
    pid: int
    holder: str
    priority: int
    acquired_at: float
    heartbeat_at: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "LockState":
        d = json.loads(raw)
        return cls(**d)


# ----------------------------------------------------------------------
# pid liveness
# ----------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but owned by someone else — still alive
        return True


# ----------------------------------------------------------------------
# atomic read/write
# ----------------------------------------------------------------------

def _read_lock() -> Optional[LockState]:
    if not LOCK_FILE.exists():
        return None
    try:
        return LockState.from_json(LOCK_FILE.read_text())
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _write_lock(state: LockState) -> None:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LOCK_FILE.with_suffix(".lock.tmp")
    tmp.write_text(state.to_json())
    os.replace(tmp, LOCK_FILE)


def _delete_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def _is_stale(state: LockState) -> bool:
    if not _pid_alive(state.pid):
        return True
    return (time.time() - state.heartbeat_at) > STALE_AFTER_S


# ----------------------------------------------------------------------
# preemption
# ----------------------------------------------------------------------

def _preempt(holder_state: LockState) -> bool:
    """SIGSTOP the current holder. SIGSTOP is synchronous at the kernel level —
    the holder cannot run any further user code once os.kill returns."""
    if not _pid_alive(holder_state.pid):
        return True
    try:
        os.kill(holder_state.pid, signal.SIGSTOP)
        return True
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def _resume(pid: int) -> None:
    if not _pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGCONT)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        PAUSED_FLAG.unlink()
    except FileNotFoundError:
        pass


# ----------------------------------------------------------------------
# heartbeat thread
# ----------------------------------------------------------------------

class _Heartbeat:
    def __init__(self, state: LockState):
        self.state = state
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_S):
            cur = _read_lock()
            if cur is None or cur.pid != self.state.pid:
                # Lost the lock (preempted hard or released externally).
                return
            cur.heartbeat_at = time.time()
            try:
                _write_lock(cur)
            except OSError:
                pass


# ----------------------------------------------------------------------
# SIGSTOP-aware paused flag
# ----------------------------------------------------------------------
# A process that's been SIGSTOP'd cannot run any code, so we cannot
# write PAUSED_FLAG from inside the stopped process. Instead the
# preempter waits PREEMPT_ACK_TIMEOUT_S then proceeds — SIGSTOP at the
# OS level is sufficient guarantee. PAUSED_FLAG remains a hint for
# `omnigraph gpu status`, written by holders in their main loop between
# Qwen calls.


def write_paused_hint() -> None:
    """Holder calls this when it knows it has been preempted.

    Useful for ETL daemons that wake up to do bookkeeping between Qwen
    calls and want `omnigraph gpu status` to show 'paused' state cleanly.
    """
    try:
        PAUSED_FLAG.write_text(str(os.getpid()))
    except OSError:
        pass


def clear_paused_hint() -> None:
    try:
        PAUSED_FLAG.unlink()
    except FileNotFoundError:
        pass


# ----------------------------------------------------------------------
# acquire loop
# ----------------------------------------------------------------------

def _try_acquire(holder: str, priority: int) -> Optional[LockState]:
    """One acquisition attempt. Returns LockState on success, None on contention."""
    cur = _read_lock()
    now = time.time()

    if cur is None or _is_stale(cur):
        # Free or stale — claim it.
        new = LockState(
            pid=os.getpid(),
            holder=holder,
            priority=priority,
            acquired_at=now,
            heartbeat_at=now,
        )
        _write_lock(new)
        # Re-read to confirm we won the race.
        check = _read_lock()
        if check and check.pid == os.getpid():
            return check
        return None

    if cur.pid == os.getpid():
        # Already holding it (re-entrant).
        return cur

    if priority < cur.priority:
        # We outrank the holder — preempt.
        if _preempt(cur):
            new = LockState(
                pid=os.getpid(),
                holder=holder,
                priority=priority,
                acquired_at=now,
                heartbeat_at=now,
            )
            # Preserve the preempted holder's identity for resume on release.
            _write_lock(new)
            # Stash the preempted PID so __exit__ can resume it.
            _write_preempted(cur.pid, cur.holder, cur.priority)
            check = _read_lock()
            if check and check.pid == os.getpid():
                return check
        return None

    # Same or lower priority — wait.
    return None


PREEMPTED_FILE = LOCK_DIR / "gpu.preempted"


def _write_preempted(pid: int, holder: str, priority: int) -> None:
    try:
        PREEMPTED_FILE.write_text(json.dumps({
            "pid": pid, "holder": holder, "priority": priority
        }))
    except OSError:
        pass


def _read_preempted() -> Optional[dict]:
    try:
        return json.loads(PREEMPTED_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _clear_preempted() -> None:
    try:
        PREEMPTED_FILE.unlink()
    except FileNotFoundError:
        pass


# ----------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------

@contextlib.contextmanager
def gpu_lock(holder: str, priority: int, timeout_s: Optional[float] = None):
    """Acquire the GPU lock for the duration of the with-block.

    Args:
        holder: short identifier ('etl_daemon', 'brain_viz', 'reflect', ...)
        priority: 1 (highest) ... 9 (lowest, ETL)
        timeout_s: max wait in seconds; None = wait forever

    On exit, releases the lock and SIGCONTs any holder we preempted.

    Raises:
        TimeoutError if timeout_s elapses without acquisition.
    """
    if not (1 <= priority <= 9):
        raise ValueError(f"priority must be 1..9, got {priority}")

    deadline = (time.time() + timeout_s) if timeout_s else None
    acquired_state: Optional[LockState] = None
    heartbeat: Optional[_Heartbeat] = None

    try:
        while acquired_state is None:
            acquired_state = _try_acquire(holder, priority)
            if acquired_state is not None:
                break
            if deadline and time.time() >= deadline:
                raise TimeoutError(
                    f"gpu_lock: {holder}@p{priority} timed out after {timeout_s}s"
                )
            time.sleep(ACQUIRE_POLL_S)

        heartbeat = _Heartbeat(acquired_state)
        heartbeat.start()
        yield acquired_state
    finally:
        if heartbeat:
            heartbeat.stop()
        if acquired_state and _read_lock() and _read_lock().pid == os.getpid():
            _delete_lock()
        # Resume any holder we preempted on entry.
        preempted = _read_preempted()
        if preempted:
            _resume(preempted["pid"])
            _clear_preempted()


# ----------------------------------------------------------------------
# inspection / control surface (used by CLI)
# ----------------------------------------------------------------------

def status() -> dict:
    """Return current lock state for `omnigraph gpu status`."""
    cur = _read_lock()
    out = {
        "lock_file": str(LOCK_FILE),
        "held": False,
        "stale": False,
        "paused_hint": PAUSED_FLAG.exists(),
        "preempted_holder": _read_preempted(),
    }
    if cur is None:
        return out
    out["held"] = True
    out["pid"] = cur.pid
    out["holder"] = cur.holder
    out["priority"] = cur.priority
    out["acquired_at"] = cur.acquired_at
    out["acquired_age_s"] = round(time.time() - cur.acquired_at, 1)
    out["heartbeat_at"] = cur.heartbeat_at
    out["heartbeat_age_s"] = round(time.time() - cur.heartbeat_at, 1)
    out["pid_alive"] = _pid_alive(cur.pid)
    out["stale"] = _is_stale(cur)
    return out


def force_release() -> dict:
    """Emergency release for `omnigraph gpu release`. Does NOT kill the holder."""
    cur = _read_lock()
    _delete_lock()
    _clear_preempted()
    return {"released": True, "previous": asdict(cur) if cur else None}


def pause_holder() -> dict:
    """Send SIGSTOP to current holder (used by `omnigraph etl pause`)."""
    cur = _read_lock()
    if cur is None:
        return {"paused": False, "reason": "no holder"}
    if not _pid_alive(cur.pid):
        return {"paused": False, "reason": "holder pid not alive"}
    try:
        os.kill(cur.pid, signal.SIGSTOP)
        return {"paused": True, "pid": cur.pid, "holder": cur.holder}
    except (ProcessLookupError, PermissionError) as e:
        return {"paused": False, "reason": str(e)}


def resume_holder() -> dict:
    """SIGCONT current holder (used by `omnigraph etl resume`)."""
    cur = _read_lock()
    if cur is None:
        return {"resumed": False, "reason": "no holder"}
    try:
        os.kill(cur.pid, signal.SIGCONT)
        clear_paused_hint()
        return {"resumed": True, "pid": cur.pid, "holder": cur.holder}
    except (ProcessLookupError, PermissionError) as e:
        return {"resumed": False, "reason": str(e)}
