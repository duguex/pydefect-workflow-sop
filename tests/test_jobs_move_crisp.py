"""Tests for move_crisp_outputs mtime-preferring promote."""
from __future__ import annotations

import time
from pathlib import Path

from vasp_sop.core.jobs import move_crisp_outputs


def test_output_newer_replaces_root(tmp_path: Path):
    (tmp_path / "OUTCAR").write_text("old\n")
    time.sleep(0.02)
    out = tmp_path / "output"
    out.mkdir()
    (out / "OUTCAR").write_text("new\n")
    (out / "vasprun.xml").write_text("<v/>\n")

    move_crisp_outputs(tmp_path)

    assert (tmp_path / "OUTCAR").read_text() == "new\n"
    assert (tmp_path / "vasprun.xml").read_text() == "<v/>\n"
    assert not (tmp_path / "output").exists()


def test_root_newer_keeps_root(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "CONTCAR").write_text("from-output\n")
    time.sleep(0.02)
    (tmp_path / "CONTCAR").write_text("from-root\n")

    move_crisp_outputs(tmp_path)

    assert (tmp_path / "CONTCAR").read_text() == "from-root\n"
    assert not (tmp_path / "output").exists()


def test_missing_root_moves(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "vasprun.xml").write_text("<ok/>\n")

    move_crisp_outputs(tmp_path)

    assert (tmp_path / "vasprun.xml").read_text() == "<ok/>\n"
    assert not out.exists()
