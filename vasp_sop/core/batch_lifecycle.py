"""Batch loop lifecycle — start/stop/status via PID file + signals."""

from __future__ import annotations

import errno
import fcntl
import os
import signal
import tempfile
import time
from pathlib import Path

_STOP_REQUESTED = False


def _handle_sigterm(signum, frame):
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


try:
    signal.signal(signal.SIGTERM, _handle_sigterm)
except ValueError:
    # Importing from a non-main thread is supported by the test suite.
    pass


def is_stop_requested() -> bool:
    return _STOP_REQUESTED


def _pid_file(root: Path) -> Path:
    return root / ".batch_loop.pid"


def _lock_file(root: Path) -> Path:
    return root / ".batch_loop.lock"


def _global_loop_lock_file() -> Path:
    from vasp_sop.core import paths

    return paths.SOP_ROOT / ".batch_loop.global.lock"


def acquire_global_loop_lock() -> int:
    """Acquire the host-wide lock owned by the unified batch loop."""
    lock_file = _global_loop_lock_file()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(lock_fd)
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        raise SystemExit("Another unified batch loop is already running")
    return lock_fd


def _read_pid(root: Path) -> int | None:
    try:
        pid = int(_pid_file(root).read_text().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None
    return pid if pid > 0 else None


def _verify_pid(root: Path, pid: int) -> bool:
    """Check that *pid* is alive and belongs to this batch loop."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as stream:
            cmdline = stream.read()
    except OSError:
        return True
    return str(root).encode() in cmdline and b"batch" in cmdline


def _release_lock(lock_fd: int) -> None:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _acquire_lock(root: Path) -> int:
    """Acquire a process lock without trusting an unpublished PID file."""
    lock_fd = os.open(_lock_file(root), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(lock_fd)
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        pid = _read_pid(root)
        if pid is None:
            raise SystemExit(
                "Already running (PID unavailable; startup in progress)"
            )
        raise SystemExit(f"Already running (PID {pid})")

    old_pid = _read_pid(root)
    if old_pid is not None and _verify_pid(root, old_pid):
        _release_lock(lock_fd)
        raise SystemExit(f"Already running (PID {old_pid})")
    _pid_file(root).unlink(missing_ok=True)
    return lock_fd


def _write_pid(root: Path) -> None:
    """Publish PID metadata atomically after the child process exists."""
    payload = f"{os.getpid()}\n{root}\n{time.time():.6f}\n"
    fd, temporary = tempfile.mkstemp(
        dir=root, prefix=".batch_loop.pid.", text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _pid_file(root))
    finally:
        Path(temporary).unlink(missing_ok=True)


def daemonize(root: Path) -> bool:
    """Fork the loop and return True only in the child process."""
    root = root.resolve()
    lock_fd = _acquire_lock(root)
    try:
        pid = os.fork()
    except BaseException:
        _release_lock(lock_fd)
        raise
    if pid != 0:
        os.close(lock_fd)
        print(f"Started background loop (PID {pid})")
        return False
    os.setsid()
    _write_pid(root)
    return True


def _is_zombie(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    closing_paren = stat.rfind(")")
    return (
        closing_paren >= 0
        and len(stat) > closing_paren + 2
        and stat[closing_paren + 2] == "Z"
    )


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return not _is_zombie(pid)


def stop(root: Path) -> None:
    """Send SIGTERM to the loop and wait up to ten seconds for exit."""
    root = root.resolve()
    pf = _pid_file(root)
    if not pf.is_file():
        print("No loop running.")
        return
    pid = _read_pid(root)
    if pid is None:
        pf.unlink(missing_ok=True)
        print("PID file corrupt — cleaned up.")
        return
    if not _verify_pid(root, pid):
        pf.unlink(missing_ok=True)
        print("PID stale — cleaned up.")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        cleanup(root)
        print(f"Stopped (PID {pid}).")
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            cleanup(root)
            print(f"Stopped (PID {pid}).")
            return
        time.sleep(0.1)
    print(f"Sent SIGTERM but PID {pid} still alive after 10 s.")


def cleanup(root: Path) -> None:
    _pid_file(root).unlink(missing_ok=True)
    _lock_file(root).unlink(missing_ok=True)
