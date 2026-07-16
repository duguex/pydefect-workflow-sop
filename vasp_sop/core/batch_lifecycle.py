"""Batch loop lifecycle — start/stop/status via PID file + signals."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

# Guard: signal handlers fail in non-main threads/test runners.
try:
    _STOP_REQUESTED = False

    def _handle_sigterm(signum, frame):
        global _STOP_REQUESTED
        _STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, _handle_sigterm)
except ValueError:
    _STOP_REQUESTED = False


def is_stop_requested() -> bool:
    return _STOP_REQUESTED


def _pid_file(root: Path) -> Path:
    return root / ".batch_loop.pid"


def _lock_file(root: Path) -> Path:
    return root / ".batch_loop.lock"


def _verify_pid(root: Path, pid: int) -> bool:
    """Check PID alive and likely our batch loop."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read()
        root_bytes = str(root).encode()
        return root_bytes in cmd and b"batch" in cmd
    except Exception:
        return True  # can't verify on this OS — trust alive


def _acquire_lock(root: Path) -> int:
    """Create exclusive lock file; return open fd (held by caller)."""
    lf = _lock_file(root)
    try:
        return os.open(lf, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pf = _pid_file(root)
        try:
            pid = int(pf.read_text().strip().splitlines()[0])
            os.kill(pid, 0)
            raise SystemExit(f"Already running (PID {pid})")
        except (ValueError, IndexError):
            pf.unlink(missing_ok=True)
            lf.unlink(missing_ok=True)
            return _acquire_lock(root)
        except OSError:
            pf.unlink(missing_ok=True)
            lf.unlink(missing_ok=True)
            return _acquire_lock(root)


def daemonize(root: Path) -> bool:
    root = root.resolve()
    lock_fd = _acquire_lock(root)
    pid = os.fork()
    if pid != 0:
        os.close(lock_fd)
        print(f"Started background loop (PID {pid})")
        return False
    os.setsid()
    pf = _pid_file(root)
    with open(pf, "w") as f:
        f.write(str(os.getpid()) + "\n")
        f.write(str(root) + "\n")
    return True


def stop(root: Path) -> None:
    """Send SIGTERM to running loop, wait up to 10 s."""
    root = root.resolve()
    pf = _pid_file(root)
    if not pf.is_file():
        print("No loop running.")
        return
    try:
        lines = pf.read_text().strip().splitlines()
        pid = int(lines[0])
    except (ValueError, IndexError, OSError):
        pf.unlink(missing_ok=True)
        _lock_file(root).unlink(missing_ok=True)
        print("PID file corrupt — cleaned up.")
        return
    if not _verify_pid(root, pid):
        pf.unlink(missing_ok=True)
        _lock_file(root).unlink(missing_ok=True)
        print("PID stale — cleaned up.")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except OSError:
            print(f"Stopped (PID {pid}).")
            pf.unlink(missing_ok=True)
            _lock_file(root).unlink(missing_ok=True)
            return
    print(f"Sent SIGTERM but PID {pid} still alive after 10 s.")


def cleanup(root: Path) -> None:
    _pid_file(root).unlink(missing_ok=True)
    _lock_file(root).unlink(missing_ok=True)