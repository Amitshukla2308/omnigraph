"""Smoke + unit tests for src/gpu_lock.py.

Tests:
  T1 — basic acquire/release: lock disappears after `with` exits.
  T2 — re-entrant acquire by same PID is OK.
  T3 — same-priority second acquirer waits, gets it after first releases.
  T4 — higher-priority preempts lower-priority holder via SIGSTOP.
  T5 — stale lock (dead PID) is reclaimed on next acquire.
  T6 — status() reflects current state correctly.
"""
from __future__ import annotations
import json
import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path

# Allow `python scripts/test_gpu_lock.py` from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import gpu_lock  # noqa: E402


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _reset_lock_dir():
    """Clear any prior state so tests run clean."""
    for p in [gpu_lock.LOCK_FILE, gpu_lock.PAUSED_FLAG, gpu_lock.PREEMPTED_FILE]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _holder_proc(holder: str, priority: int, hold_s: float, started_flag: str):
    """Worker: acquire lock, write started_flag, sleep, release."""
    sys.path.insert(0, str(ROOT / "src"))
    import gpu_lock as gl
    with gl.gpu_lock(holder=holder, priority=priority):
        Path(started_flag).write_text(str(os.getpid()))
        time.sleep(hold_s)


# ----------------------------------------------------------------------
# tests
# ----------------------------------------------------------------------

def test_t1_basic_acquire_release():
    _reset_lock_dir()
    with gpu_lock.gpu_lock(holder="t1", priority=5):
        assert gpu_lock.LOCK_FILE.exists(), "lock file should exist while held"
        st = gpu_lock.status()
        assert st["held"] and st["holder"] == "t1" and st["priority"] == 5
    assert not gpu_lock.LOCK_FILE.exists(), "lock file should be gone after release"
    print("  T1 ok — basic acquire/release")


def test_t2_reentrant():
    _reset_lock_dir()
    with gpu_lock.gpu_lock(holder="t2-outer", priority=5):
        with gpu_lock.gpu_lock(holder="t2-inner", priority=5, timeout_s=2.0):
            st = gpu_lock.status()
            assert st["pid"] == os.getpid()
    print("  T2 ok — re-entrant acquire by same PID")


def test_t3_same_priority_waits():
    _reset_lock_dir()
    started = ROOT / "scripts" / ".t3_started.flag"
    if started.exists():
        started.unlink()
    p = mp.Process(target=_holder_proc, args=("t3-holder", 5, 1.5, str(started)))
    p.start()
    # Wait for child to acquire.
    for _ in range(50):
        if started.exists():
            break
        time.sleep(0.05)
    assert started.exists(), "child failed to acquire"
    # Now we attempt with same priority — should wait, then succeed after child releases.
    # Give child a moment to enter sleep so we definitely contend.
    time.sleep(0.1)
    t0 = time.monotonic()
    with gpu_lock.gpu_lock(holder="t3-waiter", priority=5, timeout_s=5.0):
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.5, f"waiter acquired too fast ({elapsed:.2f}s)"
    p.join()
    started.unlink()
    print(f"  T3 ok — same-priority waited {elapsed:.2f}s for FIFO")


def test_t4_preemption():
    _reset_lock_dir()
    started = ROOT / "scripts" / ".t4_started.flag"
    if started.exists():
        started.unlink()
    # Hold at low priority for a long time.
    p = mp.Process(target=_holder_proc, args=("t4-etl", 9, 30.0, str(started)))
    p.start()
    for _ in range(50):
        if started.exists():
            break
        time.sleep(0.05)
    assert started.exists()
    holder_pid = int(started.read_text())
    # Higher priority should preempt.
    t0 = time.monotonic()
    with gpu_lock.gpu_lock(holder="t4-interactive", priority=1, timeout_s=10.0):
        elapsed = time.monotonic() - t0
        assert elapsed < 8.0, f"preemption too slow ({elapsed:.2f}s)"
        st = gpu_lock.status()
        assert st["holder"] == "t4-interactive"
    # On exit, preempted holder should have been SIGCONT'd.
    p.join(timeout=2.0)
    if p.is_alive():
        os.kill(holder_pid, signal.SIGTERM)
        p.join(timeout=2.0)
    started.unlink()
    print(f"  T4 ok — preempted lower-priority holder in {elapsed:.2f}s")


def test_t5_stale_lock_reclaimed():
    _reset_lock_dir()
    # Write a lock claiming a dead PID.
    fake = gpu_lock.LockState(
        pid=999999,  # very unlikely to exist
        holder="t5-ghost",
        priority=5,
        acquired_at=time.monotonic() - 1000,
        heartbeat_at=time.monotonic() - 1000,
    )
    gpu_lock._write_lock(fake)
    # We should reclaim it immediately.
    with gpu_lock.gpu_lock(holder="t5-real", priority=5, timeout_s=2.0):
        st = gpu_lock.status()
        assert st["holder"] == "t5-real"
    print("  T5 ok — stale lock reclaimed")


def test_t6_status_shape():
    _reset_lock_dir()
    st = gpu_lock.status()
    assert st["held"] is False
    with gpu_lock.gpu_lock(holder="t6", priority=4):
        st = gpu_lock.status()
        for k in ("held", "pid", "holder", "priority", "acquired_at",
                  "heartbeat_at", "pid_alive", "stale"):
            assert k in st, f"missing key {k}"
        assert st["held"] and st["holder"] == "t6" and st["priority"] == 4
        assert st["pid_alive"] is True
        assert st["stale"] is False
    print("  T6 ok — status() shape")


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

ALL_TESTS = [
    test_t1_basic_acquire_release,
    test_t2_reentrant,
    test_t3_same_priority_waits,
    test_t4_preemption,
    test_t5_stale_lock_reclaimed,
    test_t6_status_shape,
]


def main():
    failed = []
    for fn in ALL_TESTS:
        name = fn.__name__
        print(f"running {name}")
        try:
            fn()
        except AssertionError as e:
            failed.append((name, repr(e)))
            print(f"  FAIL — {e}")
        except Exception as e:
            failed.append((name, repr(e)))
            print(f"  ERROR — {e!r}")
    print()
    if failed:
        print(f"FAILED: {len(failed)}/{len(ALL_TESTS)}")
        for n, e in failed:
            print(f"  - {n}: {e}")
        return 1
    print(f"PASSED: {len(ALL_TESTS)}/{len(ALL_TESTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
