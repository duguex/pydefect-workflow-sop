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
