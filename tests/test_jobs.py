"""Tests for job execution utilities (run_local, _vasp_input_ready, run_vasp).
"""

import subprocess
from pathlib import Path

import pytest

from vasp_sop.core.jobs import (
    _vasp_input_ready,
    run_local,
)


class TestVaspInputReady:
    def test_all_files_present(self, tmp_path: Path):
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (tmp_path / f).touch()
        assert _vasp_input_ready(tmp_path)

    def test_missing_file(self, tmp_path: Path):
        (tmp_path / "INCAR").touch()
        (tmp_path / "POSCAR").touch()
        (tmp_path / "POTCAR").touch()
        assert not _vasp_input_ready(tmp_path)

    def test_empty_dir(self, tmp_path: Path):
        assert not _vasp_input_ready(tmp_path)


class TestRunLocal:
    def test_success(self, tmp_path: Path):
        result = run_local("echo hello", cwd=tmp_path)
        assert result.returncode == 0

    def test_failure_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match=r"exit 1"):
            run_local("exit 1", cwd=tmp_path)

    def test_timeout_raises(self, tmp_path: Path):
        with pytest.raises(TimeoutError):
            run_local("sleep 10", cwd=tmp_path, timeout=1)


class TestCrispSubmitCached:
    """Result-reuse hit: crisp returns {cached: True} with no task_name."""

    def _inputs(self, tmp_path: Path) -> Path:
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (tmp_path / f).write_text("x\n")
        return tmp_path

    def test_cached_response_returns_sentinel(self, tmp_path: Path, monkeypatch):
        from types import SimpleNamespace
        from vasp_sop.core.jobs import _crisp_submit

        work = self._inputs(tmp_path)

        def fake_run(*a, **k):
            return SimpleNamespace(
                returncode=0,
                stdout='{"cached": true, "local_dir": "x"}',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        job = _crisp_submit(work)
        assert job.task_name == "cached", \
            "cached response must yield a sentinel handle, not raise"

    def test_normal_response_raises_without_task(self, tmp_path: Path, monkeypatch):
        from types import SimpleNamespace
        import pytest as _pt
        from vasp_sop.core.jobs import _crisp_submit

        work = self._inputs(tmp_path)

        def fake_run(*a, **k):
            return SimpleNamespace(
                returncode=0,
                stdout='{"data": {}}',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        with _pt.raises(RuntimeError, match="missing task_name"):
            _crisp_submit(work)
