from __future__ import annotations

import json
from pathlib import Path
import pytest

from vasp_sop.materials import mp as mp_module


def _write_state(path: Path, *, elements: list[str], phase_dirs: list[str]) -> None:
    (path / "mp_state.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "status": "completed",
                "elements": sorted(set(elements)),
                "phase_dirs": phase_dirs,
                "molecule_resource_version": "diatomic-reference-v2",
            }
        )
    )


def test_fetch_writes_authoritative_manifest(tmp_path, monkeypatch):
    target = tmp_path / "cpd"
    target.mkdir()
    calls = []

    def fake_run_local(command, cwd, **kwargs):
        calls.append((command, cwd))
        phase = target / "mol_Cl2"
        phase.mkdir()
        (phase / "POSCAR").write_text("Cl2\n")

    monkeypatch.setattr(mp_module, "run_local", fake_run_local)

    mp_module.fetch_candidate_phases(["Eu", "Cl", "Cs"], target, use_cache=False)

    state = json.loads((target / "mp_state.json").read_text())
    assert len(calls) == 1
    assert state["schema"] == 2
    assert state["status"] == "completed"
    assert state["elements"] == ["Cl", "Cs", "Eu"]
    assert state["phase_dirs"] == ["mol_Cl2"]
    assert state["source"] == "pydefect_vasp mp"
    assert state["cache"] is False
    assert state["generated_at"]
    assert state["molecule_resource_version"] == "diatomic-reference-v2"


def test_fetch_does_not_trust_flag_without_valid_manifest(tmp_path, monkeypatch):
    target = tmp_path / "cpd"
    target.mkdir()
    (target / "mp_flag").touch()
    calls = []

    def fake_run_local(command, cwd, **kwargs):
        calls.append(command)
        phase = target / "mol_Cl2"
        phase.mkdir()
        (phase / "POSCAR").write_text("Cl2\n")

    monkeypatch.setattr(mp_module, "run_local", fake_run_local)

    mp_module.fetch_candidate_phases(["Cl"], target, use_cache=False)

    assert calls
    assert (target / "mp_state.json").is_file()


def test_fetch_treats_non_object_manifest_as_invalid(tmp_path, monkeypatch):
    target = tmp_path / "cpd"
    target.mkdir()
    (target / "mp_state.json").write_text("[]\n")
    calls = []

    def fake_run_local(command, cwd, **kwargs):
        calls.append(command)
        phase = target / "mol_Cl2"
        phase.mkdir()
        (phase / "POSCAR").write_text("Cl2\n")

    monkeypatch.setattr(mp_module, "run_local", fake_run_local)

    mp_module.fetch_candidate_phases(["Cl"], target, use_cache=False)

    assert calls
    assert (target / "mp_state.json").is_file()


def test_fetch_refetches_when_manifest_elements_or_phases_mismatch(
    tmp_path, monkeypatch
):
    target = tmp_path / "cpd"
    target.mkdir()
    (target / "mol_Cl2").mkdir()
    (target / "mol_Cl2" / "POSCAR").write_text("Cl2\n")
    _write_state(target, elements=["Cl"], phase_dirs=["missing_phase"])
    calls = []

    def fake_run_local(command, cwd, **kwargs):
        calls.append(command)
        (target / "Cs_mp-1").mkdir()
        (target / "Cs_mp-1" / "POSCAR").write_text("Cs\n")

    monkeypatch.setattr(mp_module, "run_local", fake_run_local)

    mp_module.fetch_candidate_phases(["Cl", "Cs"], target, use_cache=False)

    assert calls
    assert (target / "mp_state.json").is_file()


@pytest.mark.parametrize(
    ("element", "molecule_dir"),
    [
        ("H", "mol_H2"),
        ("N", "mol_N2"),
        ("O", "mol_O2"),
        ("F", "mol_F2"),
        ("Cl", "mol_Cl2"),
    ],
)
def test_fetch_refetches_when_expected_molecule_resource_is_missing(
    element, molecule_dir, tmp_path, monkeypatch
):
    target = tmp_path / "cpd"
    target.mkdir()
    phase = target / "Cs_mp-1"
    phase.mkdir()
    (phase / "POSCAR").write_text("Cs\n")
    _write_state(target, elements=["Cs", element], phase_dirs=["Cs_mp-1"])
    calls = []

    def fake_run_local(command, cwd, **kwargs):
        calls.append(command)
        mol = target / molecule_dir
        mol.mkdir()
        (mol / "POSCAR").write_text(f"{molecule_dir}\n")

    monkeypatch.setattr(mp_module, "run_local", fake_run_local)

    mp_module.fetch_candidate_phases(["Cs", element], target, use_cache=False)

    assert calls
    assert (target / molecule_dir).is_dir()
