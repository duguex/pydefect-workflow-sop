"""Tests for vasp_sop.core.batch_lifecycle — daemonize, stop, lock file."""

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vasp_sop.core.batch_lifecycle import (
    daemonize,
    stop,
    cleanup,
    _pid_file,
    _lock_file,
    _verify_pid,
    _acquire_lock,
)


def test_daemonize_creates_pid_and_lock(tmp_path: Path, monkeypatch):
    """daemonize writes PID and lock files, child path returns True."""
    import vasp_sop.core.batch_lifecycle as lifecycle

    lock_fds = []
    real_acquire = lifecycle._acquire_lock

    def tracked_acquire(root):
        fd = real_acquire(root)
        lock_fds.append(fd)
        return fd

    monkeypatch.setattr(lifecycle, "_acquire_lock", tracked_acquire)
    monkeypatch.setattr(os, "fork", lambda: 0)
    monkeypatch.setattr(os, "setsid", lambda: None)
    try:
        assert daemonize(tmp_path) is True
        assert _pid_file(tmp_path).is_file(), "PID file missing"
        assert _lock_file(tmp_path).is_file(), "lock file missing"
        lines = _pid_file(tmp_path).read_text().strip().splitlines()
        assert lines[0] == str(os.getpid()), f"PID mismatch: {lines[0]}"
    finally:
        cleanup(tmp_path)
        for fd in lock_fds:
            lifecycle._release_lock(fd)


def test_daemonize_parent_returns_false(tmp_path: Path, monkeypatch):
    """Parent path returns False (non-daemon exit)."""
    monkeypatch.setattr(os, "fork", lambda: 99999)
    result = daemonize(tmp_path)
    assert result is False


def test_stop_sends_sigterm(tmp_path: Path, monkeypatch):
    """stop kills a mocked PID with SIGTERM."""
    pf = _pid_file(tmp_path)
    pf.write_text(f"{os.getpid()}\n{tmp_path}\n")
    lf = _lock_file(tmp_path)
    lf.write_text("lock\n")

    monkeypatch.setattr(
        "vasp_sop.core.batch_lifecycle._verify_pid", lambda root, pid: True,
    )

    class KillTracker:
        def __init__(self):
            self.calls = 0

        def __call__(self, pid, sig):
            self.calls += 1
            if self.calls == 1:
                return None  # SIGTERM sent
            raise OSError("stopped")

    tracker = KillTracker()
    monkeypatch.setattr(os, "kill", tracker)

    stop(tmp_path)
    assert tracker.calls >= 1


def test_duplicate_start_blocked(tmp_path: Path, monkeypatch):
    """Second daemonize raises SystemExit when the loop lock is held."""
    pf = _pid_file(tmp_path)
    pf.write_text(f"{os.getpid()}\n{tmp_path}\n")
    lock_fd = os.open(_lock_file(tmp_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(
        "vasp_sop.core.batch_lifecycle._verify_pid", lambda root, pid: True,
    )
    try:
        with pytest.raises(SystemExit, match="Already running"):
            daemonize(tmp_path)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_cleanup_removes_files(tmp_path: Path):
    lf = _lock_file(tmp_path)
    lf.write_text("x\n")
    pf = _pid_file(tmp_path)
    pf.write_text("1\n")

    cleanup(tmp_path)
    assert not lf.is_file()
    assert not pf.is_file()


def test_verify_pid_mocked_cmdline(tmp_path: Path, monkeypatch):
    """Returns True when /proc cmdline contains root and 'batch'."""
    pid = os.getpid()
    fake = f"python\0vasp-sop\0batch\0{tmp_path}\0".encode()
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == f"/proc/{pid}/cmdline":
            from unittest.mock import mock_open
            return mock_open(read_data=fake)()
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    assert _verify_pid(tmp_path, pid) is True


def test_write_pid_publishes_with_atomic_replace(tmp_path: Path, monkeypatch):
    """PID publication replaces a temp file rather than writing in place."""
    import vasp_sop.core.batch_lifecycle as lifecycle

    replacements = []
    real_replace = os.replace

    def tracked_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", tracked_replace)
    lifecycle._write_pid(tmp_path)

    assert replacements
    source, destination = replacements[0]
    assert source.parent == tmp_path
    assert source.name.startswith(".batch_loop.pid.")
    assert destination == _pid_file(tmp_path)
    assert _pid_file(tmp_path).read_text().splitlines()[0] == str(os.getpid())


def test_lock_held_before_pid_does_not_get_unlinked(tmp_path: Path):
    """A live lock without a published PID remains authoritative."""
    lock_fd = os.open(_lock_file(tmp_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(SystemExit, match="Already running"):
            _acquire_lock(tmp_path)
        assert _lock_file(tmp_path).is_file()
        assert not _pid_file(tmp_path).is_file()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_stop_process_cleans_pid_and_lock(tmp_path: Path):
    """stop sends SIGTERM to a real loop process and removes lifecycle
    files."""
    script = (
        "import fcntl, os, signal, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "lock = os.open(root / '.batch_loop.lock', "
        "os.O_CREAT | os.O_RDWR, 0o600)\n"
        "fcntl.flock(lock, fcntl.LOCK_EX)\n"
        "(root / '.batch_loop.pid').write_text("
        "f'{os.getpid()}\\n{root}\\n{time.time() - 3600}\\n')\n"
        "signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))\n"
        "signal.pause()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), "batch"],
    )
    try:
        for _ in range(100):
            if _pid_file(tmp_path).is_file():
                break
            time.sleep(0.01)
        assert _pid_file(tmp_path).is_file()
        stop(tmp_path)
        proc.wait(timeout=2)
        assert not _pid_file(tmp_path).exists()
        assert not _lock_file(tmp_path).exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        cleanup(tmp_path)
