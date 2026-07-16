"""Tests for vasp_sop.core.batch_lifecycle — daemonize, stop, lock file."""

import os
import time
from pathlib import Path

import pytest

from vasp_sop.core.batch_lifecycle import (
    daemonize, stop, cleanup, _pid_file, _lock_file, _verify_pid,
)


def test_daemonize_creates_pid_and_lock(tmp_path: Path, monkeypatch):
    """daemonize writes PID and lock files, child path returns True."""
    def fake_fork():
        return 0  # simulate child
    monkeypatch.setattr(os, "fork", fake_fork)
    monkeypatch.setattr(os, "setsid", lambda: None)
    for name in (".batch_loop.pid", ".batch_loop.lock"):
        (tmp_path / name).unlink(missing_ok=True)

    assert daemonize(tmp_path) is True
    assert _pid_file(tmp_path).is_file(), "PID file missing"
    assert _lock_file(tmp_path).is_file(), "lock file missing"
    lines = _pid_file(tmp_path).read_text().strip().splitlines()
    assert lines[0] == str(os.getpid()), f"PID mismatch: {lines[0]}"


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

    kills = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kills.append(sig))
    monkeypatch.setattr(time, "sleep", lambda s: os.kill.raise_oserror() if len(kills) > 1 else None)

    # We need _verify_pid to return True — patch it
    monkeypatch.setattr(
        "vasp_sop.core.batch_lifecycle._verify_pid", lambda root, pid: True,
    )
    # After SIGTERM, first sleep: kill raises OSError → stopped
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
    """Second daemonize raises SystemExit when alive lock exists."""
    pf = _pid_file(tmp_path)
    pf.write_text(f"{os.getpid()}\n{tmp_path}\n")
    lf = _lock_file(tmp_path)
    lf.write_text("lock\n")

    def fake_fork():
        return 0
    monkeypatch.setattr(os, "fork", fake_fork)
    monkeypatch.setattr(os, "setsid", lambda: None)

    with pytest.raises(SystemExit, match="Already running"):
        daemonize(tmp_path)


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
