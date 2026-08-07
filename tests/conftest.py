"""Shared test fixtures for vasp-sop test suite.

Provides common fixtures that were previously duplicated across test files:
- isolated_cache: redirect all cache paths into tmp_path
- mock_crisp: mock subprocess/crisp calls to prevent real HPC submissions
- sample_project: minimal project tree with plan.yaml for pipeline tests
"""

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def isolated_cache(tmp_path: Path):
    """Redirect all vasp-sop cache paths into a temporary directory.

    This prevents tests from reading/writing the real ~/.vasp_sop cache.
    Returns the cache root path for assertions.
    """
    from vasp_sop.core.paths import override_cache_root

    cache_root = tmp_path / ".vasp_sop"
    override_cache_root(cache_root)

    # Also patch materials.mp module which may have imported old paths
    # at module load time.
    import vasp_sop.materials.mp as _mp_mod

    _mp_mod.MP_CACHE = cache_root / "mp_cache"
    _mp_mod.POSCAR_CACHE = _mp_mod.MP_CACHE / "poscars"

    return cache_root


@pytest.fixture
def mock_crisp(monkeypatch):
    """Mock subprocess.run to prevent real crisp/HPC calls.

    Returns a list that accumulates all subprocess.run call arguments,
    so tests can assert on what would have been submitted.
    """
    import subprocess

    calls: list = []

    def fake_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        result = type("CompletedProcess", (), {})()
        result.stdout = '{"jobs": []}'
        result.stderr = ""
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a minimal project tree with plan.yaml for pipeline tests.

    Structure::

        tmp_path/
          plan.yaml
          cpd/
            NaCl_mp-12345/
              POSCAR, INCAR, POTCAR, KPOINTS, OUTCAR (converged)

    Returns the project root path.
    """
    root = tmp_path / "project"
    root.mkdir()

    plan = {
        "project": {
            "formula": "NaCl",
            "dopant_elements": [],
            "poscar_src": "MP mp-12345",
        },
        "parameters": {"functional": "pbesol"},
        "supercell": {"tool": "doped", "min_distance": 10.0},
    }
    (root / "plan.yaml").write_text(yaml.dump(plan))

    # Create a converged target directory
    target_dir = root / "cpd" / "NaCl_mp-12345"
    target_dir.mkdir(parents=True)
    _write_poscar(target_dir, 4)
    _write_incar(target_dir)
    _write_potcar(target_dir)
    _write_kpoints(target_dir)
    _write_converged_outcar(target_dir)

    return root


# ── Helper functions for building synthetic VASP files ────────────────────


def _write_poscar(d: Path, n_atoms: int) -> None:
    """Write a minimal valid POSCAR."""
    lines = [
        "Test POSCAR",
        "1.0",
        "10.0 0.0 0.0",
        "0.0 10.0 0.0",
        "0.0 0.0 10.0",
        "X",
        str(n_atoms),
        "Direct",
    ]
    for i in range(n_atoms):
        lines.append(f"{i / n_atoms:.6f} {i / n_atoms:.6f} {i / n_atoms:.6f}")
    (d / "POSCAR").write_text("\n".join(lines) + "\n")


def _write_incar(d: Path) -> None:
    (d / "INCAR").write_text("SYSTEM = test\nEDIFFG = -0.03\n")


def _write_potcar(d: Path) -> None:
    (d / "POTCAR").write_text("dummy POTCAR\n")


def _write_kpoints(d: Path) -> None:
    (d / "KPOINTS").write_text("k-points\n0\nGamma\n1 1 1\n0 0 0\n")


def _write_converged_outcar(d: Path, max_force: float = 0.01) -> None:
    """Write a synthetic OUTCAR that passes check_converged."""
    text = (
        " POSITION                                       TOTAL-FORCE (eV/Angst)\n"
        " -----------------------------------------------------------------------------------\n"
        f"     0.00000      0.00000      0.00000      {max_force:.6f}      0.00000      0.00000\n"
        "\n"
        " General timing and accounting informations for this job:\n"
    )
    (d / "OUTCAR").write_text(text)
