"""Tests for vasp_sop.core.snapshot — JSON snapshot writer."""

from pathlib import Path
import json

from vasp_sop.core.snapshot import SnapshotWriter


def test_write_overwrites_snapshot(tmp_path: Path):
    sw = SnapshotWriter(tmp_path)
    sw.write({"phases": {"COMPLETE": 10}})
    first = json.loads((tmp_path / "batch_snapshot.json").read_text())
    assert first["phases"]["COMPLETE"] == 10
    assert "timestamp" in first

    sw.write({"phases": {"COMPLETE": 12}})
    second = json.loads((tmp_path / "batch_snapshot.json").read_text())
    assert second["phases"]["COMPLETE"] == 12


def test_append_to_timeline(tmp_path: Path):
    sw = SnapshotWriter(tmp_path)
    sw.write({"round": 1})
    sw.write({"round": 2})
    lines = (tmp_path / "batch_timeline.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["round"] == 1
    assert json.loads(lines[1])["round"] == 2


def test_last_returns_previous(tmp_path: Path):
    sw = SnapshotWriter(tmp_path)
    assert sw.last() is None
    sw.write({"x": 1})
    assert sw.last()["x"] == 1
