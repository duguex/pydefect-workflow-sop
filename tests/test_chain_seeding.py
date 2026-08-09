"""Charge-state chain seeding tests (ADR 0010).

Covers the chain helpers (group key, charge parse, median roots), the
geometry seed primitive, and the wave-2 chain unlock + seed integration.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from vasp_sop.core.orchestrator import (
    _chain_roots,
    _defect_charge,
    _defect_group_key,
)
from vasp_sop.vasp.io import seed_geometry_from_contcar


# ── Helpers ───────────────────────────────────────────────────────────────


def _write_inputs(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "POSCAR").write_text(
        "seed\n1.0\n1 0 0\n0 1 0\n0 0 1\n1\nX\n0 0 0\n"
    )
    (d / "INCAR").write_text("SYSTEM = test\nEDIFFG = -0.03\nNSW = 100\n")
    (d / "POTCAR").write_text("dummy POTCAR\n")
    (d / "KPOINTS").write_text("k-points\n0\nGamma\n1 1 1\n0 0 0\n")


def _make_unitcell_system(root: Path, charges=(0, -1, -2)) -> Path:
    """System tree at UNITCELL_DEFECT stage with a defect/ tree."""
    cpd = root / "cpd"
    cpd.mkdir(parents=True, exist_ok=True)
    (cpd / "target_vertices.yaml").write_text("A: {}\n")
    (cpd / "standard_energies.yaml").write_text("A: 1.0\n")
    target = cpd / "NaCl_mp-1"
    target.mkdir(exist_ok=True)
    (target / "POSCAR").write_text(
        "seed\n1.0\n1 0 0\n0 1 0\n0 0 1\n2\nX Y\n0 0 0 0.5 0.5 0.5\n"
    )
    df = root / "defect"
    df.mkdir(exist_ok=True)
    for name in ("perfect", *(f"Va_O1_{q}" for q in charges)):
        _write_inputs(df / name)
    return root


class TestChainHelpers:
    def test_chain_roots_odd(self):
        assert _chain_roots([-2, -1, 0]) == {-1}
        assert _chain_roots([0, 1, 2, 3, 4]) == {2}
        assert _chain_roots([-3, -2, -1, 0, 1]) == {-1}

    def test_chain_roots_even(self):
        assert _chain_roots([-1, 0, 1, 2]) == {0, 1}
        assert _chain_roots([0, 1]) == {0, 1}

    def test_chain_roots_single(self):
        assert _chain_roots([3]) == {3}

    def test_chain_roots_empty(self):
        assert _chain_roots([]) == set()

    def test_group_key(self):
        assert _defect_group_key("Va_Gd1_-3") == "Va_Gd1"
        assert _defect_group_key("Va_Gd1_0") == "Va_Gd1"
        assert _defect_group_key("Gd_Ga1+Va_O1_-1") == "Gd_Ga1+Va_O1"
        assert _defect_group_key("perfect") == "perfect"

    def test_charge(self):
        assert _defect_charge("Va_Gd1_-3") == -3
        assert _defect_charge("Va_Gd1_0") == 0
        assert _defect_charge("Gd_Ga1+Va_O1_2") == 2
        assert _defect_charge("perfect") is None


class TestSeedGeometry:
    def test_seed_copies_poscar_forces_istart0(self, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "CONTCAR").write_text("seed geometry\n")
        (dst / "POSCAR").write_text("old\n")
        (dst / "INCAR").write_text("NSW = 100\nISTART = 1\n")
        (dst / "WAVECAR").write_text("wave\n")
        assert seed_geometry_from_contcar(dst, src) is True
        assert (dst / "POSCAR").read_text() == "seed geometry\n"
        assert not (dst / "WAVECAR").exists()
        incar = (dst / "INCAR").read_text()
        assert "ISTART = 0" in incar
        assert "NSW = 100" in incar

    def test_seed_no_contcar_returns_false(self, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (dst / "POSCAR").write_text("old\n")
        assert seed_geometry_from_contcar(dst, src) is False
        assert (dst / "POSCAR").read_text() == "old\n"

    def test_seed_without_incar_ok(self, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "CONTCAR").write_text("seed\n")
        (dst / "POSCAR").write_text("old\n")
        assert seed_geometry_from_contcar(dst, src) is True
        assert (dst / "POSCAR").read_text() == "seed\n"


# ── wave2 integration ─────────────────────────────────────────────────────


class TestWave2ChainUnlock:
    @pytest.fixture(autouse=True)
    def _patch_heavy(self, monkeypatch, tmp_path: Path):
        from vasp_sop.core.paths import override_cache_root

        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr("vasp_sop.defect.builder.build_all", lambda *a, **kw: None)
        monkeypatch.setattr(
            "vasp_sop.defect.builder._generate_vasp_inputs", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            "vasp_sop.defect.unitcell._prepare_all_inputs", lambda *a, **kw: None
        )
        self.calls: list[Path] = []
        monkeypatch.setattr(
            "vasp_sop.core.jobs.submit_vasp",
            lambda p, priority=0: (
                self.calls.append(Path(p))
                or SimpleNamespace(task_name=f"t{len(self.calls)}")
            ),
        )

    def _run_wave2(self, root: Path, monkeypatch, verdict_fn, *, retry_failed=False):
        monkeypatch.setattr(
            "vasp_sop.vasp.convergence.convergence_verdict",
            lambda p, priority=0: SimpleNamespace(
                converged=verdict_fn(str(p)), max_f=None
            ),
        )
        from vasp_sop.core.config import PipelineConfig
        from vasp_sop.core.job_store import JobStore
        from vasp_sop.core.orchestrator import wave2_submit
        from vasp_sop.core.system import System

        config = PipelineConfig.from_plan(
            yaml.safe_load((root / "plan.yaml").read_text()), root=root
        )
        sys = System(root, config)
        js = JobStore()
        try:
            wave2_submit(sys, js, dry_run=False, retry_failed=retry_failed)
        finally:
            js.close()

    def test_only_chain_root_submitted_without_siblings(
        self, tmp_path: Path, monkeypatch
    ):
        root = _make_unitcell_system(tmp_path / "p")
        plan = {
            "project": {"formula": "NaCl", "poscar_src": "MP mp-1"},
            "parameters": {"functional": "pbesol"},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        self._run_wave2(root, monkeypatch, lambda p: False)
        defect_calls = [c for c in self.calls if "defect" in str(c)]
        names = {c.name for c in defect_calls if c.name != "perfect"}
        assert names == {"Va_O1_-1"}  # median only

    def test_siblings_seeded_after_chain_root_converges(
        self, tmp_path: Path, monkeypatch
    ):
        root = _make_unitcell_system(tmp_path / "p")
        plan = {
            "project": {"formula": "NaCl", "poscar_src": "MP mp-1"},
            "parameters": {"functional": "pbesol"},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        df = root / "defect"
        (df / "Va_O1_-1" / "CONTCAR").write_text("converged geometry\n")
        (df / "Va_O1_-1" / "vasprun.xml").write_text("<vasprun/>\n")

        def verdict(path: str) -> bool:
            return "Va_O1_-1" in path

        self._run_wave2(root, monkeypatch, verdict)
        defect_calls = [c for c in self.calls if "defect" in str(c)]
        names = {c.name for c in defect_calls if c.name != "perfect"}
        # root is converged (backfilled, not resubmitted); both neighbors seeded
        assert names == {"Va_O1_0", "Va_O1_-2"}
        assert (df / "Va_O1_0" / "POSCAR").read_text() == "converged geometry\n"
        assert (df / "Va_O1_-2" / "POSCAR").read_text() == "converged geometry\n"
        assert "ISTART = 0" in (df / "Va_O1_0" / "INCAR").read_text()
        assert "ISTART = 0" in (df / "Va_O1_-2" / "INCAR").read_text()

    def test_terminal_failed_sibling_unlocks_without_seed(
        self, tmp_path: Path, monkeypatch
    ):
        root = _make_unitcell_system(tmp_path / "p")
        plan = {
            "project": {"formula": "NaCl", "poscar_src": "MP mp-1"},
            "parameters": {"functional": "pbesol"},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        from vasp_sop.core.job_store import JobStore

        js = JobStore()
        try:
            js.record(str((root / "defect" / "Va_O1_-1").resolve()), "failed")
            js.record(
                str((root / "defect" / "Va_O1_-1").resolve()),
                "failed",
                source="auto_retry",
            )
        finally:
            js.close()
        self._run_wave2(root, monkeypatch, lambda p: False)
        defect_calls = [c for c in self.calls if "defect" in str(c)]
        names = {c.name for c in defect_calls if c.name != "perfect"}
        assert names == {"Va_O1_0", "Va_O1_-2"}
        # no converged sibling -> pristine POSCAR kept
        assert (root / "defect" / "Va_O1_0" / "POSCAR").read_text().startswith(
            "seed\n"
        )

    def test_ran_before_sibling_seeded_when_sibling_converged(
        self, tmp_path: Path, monkeypatch
    ):
        """A dir that ran an old round is still seeded when a sibling
        converges — stale geometry is worse than the sibling's."""
        root = _make_unitcell_system(tmp_path / "p")
        plan = {
            "project": {"formula": "NaCl", "poscar_src": "MP mp-1"},
            "parameters": {"functional": "pbesol"},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        df = root / "defect"
        # root converged with a real CONTCAR
        (df / "Va_O1_-1" / "CONTCAR").write_text("converged geometry\n")
        (df / "Va_O1_-1" / "vasprun.xml").write_text("<vasprun/>\n")
        # sibling 0 already ran an old round (OUTCAR + own CONTCAR present)
        (df / "Va_O1_0" / "OUTCAR").write_text("old run output\n")
        (df / "Va_O1_0" / "CONTCAR").write_text("old own geometry\n")

        def verdict(path: str) -> bool:
            return "Va_O1_-1" in path

        self._run_wave2(root, monkeypatch, verdict)
        # seeded from the converged sibling, not its own stale CONTCAR
        assert (df / "Va_O1_0" / "POSCAR").read_text() == "converged geometry\n"

    def test_ran_before_waits_without_converged_sibling(
        self, tmp_path: Path, monkeypatch
    ):
        """No converged sibling: a non-root charge waits for the chain even
        if it ran an old round before (its stale geometry is not a valid
        substitute for a converged sibling's)."""
        root = _make_unitcell_system(tmp_path / "p")
        plan = {
            "project": {"formula": "NaCl", "poscar_src": "MP mp-1"},
            "parameters": {"functional": "pbesol"},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        df = root / "defect"
        (df / "Va_O1_0" / "OUTCAR").write_text("old run output\n")
        (df / "Va_O1_0" / "CONTCAR").write_text("own geometry\n")
        (df / "Va_O1_0" / "INCAR").write_text("SYSTEM = test\nISTART = 0\n")
        # median -1 has not converged -> 0 has no converged sibling

        self._run_wave2(root, monkeypatch, lambda p: False)
        # only the median root submits; both neighbors wait for the chain
        defect_calls = [c for c in self.calls if "defect" in str(c)]
        names = {c.name for c in defect_calls if c.name != "perfect"}
        assert names == {"Va_O1_-1"}
        assert (df / "Va_O1_0" / "POSCAR").read_text().startswith("seed\n")
        assert "ISTART = 0" in (df / "Va_O1_0" / "INCAR").read_text()

    def test_unconverged_sibling_does_not_unlock(
        self, tmp_path: Path, monkeypatch
    ):
        """A failed-but-not-terminal sibling must NOT unlock the chain."""
        root = _make_unitcell_system(tmp_path / "p")
        plan = {
            "project": {"formula": "NaCl", "poscar_src": "MP mp-1"},
            "parameters": {"functional": "pbesol"},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        from vasp_sop.core.job_store import JobStore

        js = JobStore()
        try:
            js.record(str((root / "defect" / "Va_O1_-1").resolve()), "failed")
        finally:
            js.close()
        self._run_wave2(root, monkeypatch, lambda p: False, retry_failed=True)
        defect_calls = [c for c in self.calls if "defect" in str(c)]
        names = {c.name for c in defect_calls if c.name != "perfect"}
        # median failed once (auto_retry armed by --retry-failed) -> root is
        # retried, but the chain stays locked for its neighbors
        assert names == {"Va_O1_-1"}
