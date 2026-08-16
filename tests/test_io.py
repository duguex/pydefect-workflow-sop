"""Tests for vasp_sop.vasp.io — check_converged, check_task_complete."""

from pathlib import Path
from types import SimpleNamespace

import pytest


def _write_converged_outcar(d: Path) -> None:
    """OUTCAR that satisfies check_converged."""
    text = (
        " General timing and accounting\n"
        "   100.00% CPU utilisation\n"
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
    )
    (d / "OUTCAR").write_text(text)


def _write_incar(d: Path) -> None:
    (d / "INCAR").write_text("SYSTEM = test\n")


class TestCheckTaskComplete:
    """check_task_complete: output-completeness per task type."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.dir = tmp_path / "task"
        self.dir.mkdir()
        _write_incar(self.dir)

    def test_band_with_vasprxml(self):
        """band: converged OUTCAR + vasprun.xml → True."""
        _write_converged_outcar(self.dir)
        (self.dir / "vasprun.xml").write_text("<vasprun></vasprun>")
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "band")

    def test_band_without_vasprxml(self):
        """band: converged OUTCAR only → False (missing vasprun.xml)."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_band_unconverged(self):
        """band: unconverged OUTCAR → False regardless of vasprun.xml."""
        (self.dir / "OUTCAR").write_text("some header\n")
        (self.dir / "vasprun.xml").write_text("<vasprun></vasprun>")
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_band_no_output(self):
        """band: no OUTCAR at all → False."""
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_dos_missing_vasprxml(self):
        """dos: converged OUTCAR only → False."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "dos")

    def test_dielectric_without_vasprxml(self):
        """dielectric: converged OUTCAR only → True (no vasprun.xml needed)."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "dielectric")

    def test_default_task_type(self):
        """default (task_type=""): delegates to check_converged — converged → True."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir)

    def test_unknown_task_type(self):
        """unknown task_type: delegates to check_converged."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "phonon")



class TestRestorePotcar:
    """POTCAR restore from the local PSP store (ADR 0007 input restore)."""

    def _psp_store(self, tmp_path: Path) -> Path:
        psp = tmp_path / "psp"
        (psp / "Se").mkdir(parents=True)
        (psp / "Se" / "POTCAR").write_text("Se psp\n   ENMAX = 211.55 ;\n")
        (psp / "Ba_sv").mkdir(parents=True)
        (psp / "Ba_sv" / "POTCAR").write_text("Ba sv psp\n   ENMAX = 97.04 ;\n")
        (psp / "Ba_sv_GW").mkdir(parents=True)
        (psp / "Ba_sv_GW" / "POTCAR").write_text("GW psp\n   ENMAX = 1.0 ;\n")
        return psp

    def _poscar(self, d: Path, species: str) -> None:
        (d / "POSCAR").write_text(
            "title\n1.0\n"
            "10.0 0.0 0.0\n0.0 10.0 0.0\n0.0 0.0 10.0\n"
            f"{species}\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n")

    def test_exact_and_variant_restore(self, tmp_path: Path):
        from vasp_sop.vasp.io import restore_potcar
        psp = self._psp_store(tmp_path)
        d = tmp_path / "dir"
        d.mkdir()
        self._poscar(d, "Ba Se")

        ok, msg = restore_potcar(d, psp_dir=str(psp))
        assert ok, msg
        text = (d / "POTCAR").read_text()
        assert "Ba sv psp" in text, "Ba must resolve to Ba_sv (not GW)"
        assert "Se psp" in text
        assert "GW psp" not in text

    def test_encut_matches_variant(self, tmp_path: Path):
        from vasp_sop.vasp.io import restore_potcar
        from vasp_sop.vasp.io import _pick_psp_variant
        psp = self._psp_store(tmp_path)
        # ENCUT 126 = 1.3 * 97.04  (Ba_sv); a plain-Ba dir doesn't exist
        candidate = _pick_psp_variant("Ba", psp=psp, encut=126.2)
        assert candidate is not None
        assert candidate.name == "Ba_sv"

    def test_restore_missing_inputs_skips_done(self, tmp_path: Path):
        """Blocked dirs restored; dirs already done on disk (POTCAR
        stripped after completion) are NOT mass-restored."""
        from vasp_sop.vasp.io import restore_missing_inputs
        psp = self._psp_store(tmp_path)
        sysd = tmp_path / "NaSe"
        sysd.mkdir()
        (sysd / "plan.yaml").write_text("x\n")
        blocked = sysd / "cpd" / "NaSe_mp-1"
        blocked.mkdir(parents=True)
        self._poscar(blocked, "Ba Se")
        (blocked / "INCAR").write_text("ENCUT = 200\n")
        (blocked / "KPOINTS").write_text("k\n")
        done = sysd / "cpd" / "BaSeO_mp-2"
        done.mkdir(parents=True)
        self._poscar(done, "Ba Se O")
        (done / "INCAR").write_text("ENCUT = 200\n")
        (done / "KPOINTS").write_text("k\n")
        # done on disk: converged OUTCAR → restore must skip it
        (done / "OUTCAR").write_text(
            "NSW = 50\nIBRION = 2\nEDIFFG = -0.005\n"
            " General timing and accounting informations for this job:\n"
            " TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.001 0.001 0.001 0.002 0.001 0.001\n")

        res = restore_missing_inputs(sysd, psp_dir=str(psp))
        assert len(res["restored"]) == 1, res
        assert (blocked / "POTCAR").is_file()
        assert not (done / "POTCAR").is_file(), \
            "done-on-disk dir must not get a POTCAR restored"


class TestPatchIncarU:
    """patch_incar_u: DFT+U for INCARs from the vise CLI gap (ADR 0012)."""

    def _dir(self, tmp_path: Path, species: str) -> Path:
        d = tmp_path / "calc"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 50\n")
        (d / "POSCAR").write_text(
            "title\n1.0\n"
            "10.0 0.0 0.0\n0.0 10.0 0.0\n0.0 0.0 10.0\n"
            f"{species}\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n")
        return d

    def test_adds_ldau_for_fe(self, tmp_path: Path):
        from vasp_sop.vasp.io import patch_incar_u
        d = self._dir(tmp_path, "Fe O")
        patch_incar_u(d, apply_u=True)
        txt = (d / "INCAR").read_text()
        assert "LDAU = True" in txt
        assert "LDAUU = 3.0 0" in txt
        assert "LDAUL = 2 -1" in txt
        assert "LMAXMIX = 4" in txt
        assert "ISPIN = 2" in txt
        assert "NSW = 50" in txt, "existing tags must survive"

    def test_duplicate_atom_species_deduped(self, tmp_path: Path):
        """VASP LDAUU/LDAUL rows are per species, not per atom."""
        from vasp_sop.vasp.io import patch_incar_u
        d = tmp_path / "calc"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 50\n")
        (d / "POSCAR").write_text(
            "title\n1.0\n"
            "10.0 0.0 0.0\n0.0 10.0 0.0\n0.0 0.0 10.0\n"
            "Fe O\n4 4\nDirect\n"
            "0 0 0\n0.5 0.5 0.5\n0.25 0.25 0.25\n0.75 0.75 0.75\n"
            "0.1 0.1 0.1\n0.2 0.2 0.2\n0.3 0.3 0.3\n0.4 0.4 0.4\n")
        patch_incar_u(d, apply_u=True)
        txt = (d / "INCAR").read_text()
        assert "LDAUU = 3.0 0" in txt
        assert "LDAUL = 2 -1" in txt

    def test_noop_without_u_element(self, tmp_path: Path):
        from vasp_sop.vasp.io import patch_incar_u
        d = self._dir(tmp_path, "Ca O")
        patch_incar_u(d, apply_u=True)
        assert "LDAU" not in (d / "INCAR").read_text()

    def test_noop_when_ldau_present(self, tmp_path: Path):
        from vasp_sop.vasp.io import patch_incar_u
        d = self._dir(tmp_path, "Fe O")
        (d / "INCAR").write_text("LDAU = True\nLDAUU = 5 0\n")
        patch_incar_u(d, apply_u=True)
        assert "LDAUU = 5 0" in (d / "INCAR").read_text(), \
            "existing U must not be overwritten"

    def test_ispin_added_when_ldau_present_but_spin_missing(self, tmp_path: Path):
        """vise's cpd template emits LDAU (with -t structure_opt) but no
        ISPIN — spin polarization must still be forced for U species."""
        from vasp_sop.vasp.io import patch_incar_u
        d = self._dir(tmp_path, "Fe O")
        (d / "INCAR").write_text("LDAU = True\nLDAUU = 3 0\nLDAUL = 2 -1\n")
        patch_incar_u(d, apply_u=True)
        txt = (d / "INCAR").read_text()
        assert "ISPIN = 2" in txt
        assert "LDAUU = 3 0" in txt, "existing U values untouched"

    def test_f_element_lmaxmix6(self, tmp_path: Path):
        from vasp_sop.vasp.io import patch_incar_u
        d = self._dir(tmp_path, "Gd O")
        patch_incar_u(d, apply_u=True)
        txt = (d / "INCAR").read_text()
        assert "LDAUU = 5.0 0" in txt
        assert "LDAUL = 3 -1" in txt
        assert "LMAXMIX = 6" in txt


class TestDielectricProtocol:
    """DFPT dielectric INCAR protocol: NSW=1, LREAL=.FALSE., no SOC tags —
    unconditional (VASP DFPT is single-step; no SOC support)."""

    def _incar_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "dielectric"
        d.mkdir()
        (d / "INCAR").write_text(
            "NSW = 50\nLREAL = Auto\nLSORBIT = .TRUE.\nISYM = -1\nIBRION = 8\n"
        )
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            (d / f).write_text("x\n")
        return d

    def test_dielectric_gets_nsw1_lreal_false_no_soc(self, tmp_path: Path):
        from vasp_sop.vasp.io import prepare_inputs

        d = self._incar_dir(tmp_path)
        cfg = SimpleNamespace(soc=True, stage2_soc=False, functional="pbesol",
                              potcar_overrides=[], encut=None)
        prepare_inputs(d, cfg, task_type="dielectric")
        txt = (d / "INCAR").read_text()
        assert "NSW = 1" in txt
        assert "LREAL = .FALSE." in txt
        assert "LSORBIT" not in txt
        assert "ISYM" not in txt

    def test_dielectric_nsw1_even_without_soc_plan(self, tmp_path: Path):
        """The NSW=50 regression hit non-SOC systems too — the DFPT caps
        must not depend on the soc flag."""
        from vasp_sop.vasp.io import prepare_inputs

        d = self._incar_dir(tmp_path)
        cfg = SimpleNamespace(soc=False, stage2_soc=False, functional="pbesol",
                              potcar_overrides=[], encut=None)
        prepare_inputs(d, cfg, task_type="dielectric")
        assert "NSW = 1" in (d / "INCAR").read_text()

    def test_band_gets_no_soc_tags(self, tmp_path: Path):
        """2026-08-16 协议(grill Q7):单点腿 band/dos 带 U 不带 SOC。"""
        from vasp_sop.vasp.io import prepare_inputs

        d = tmp_path / "band"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 1\nIBRION = -1\n")
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            (d / f).write_text("x\n")
        cfg = SimpleNamespace(soc=True, stage2_soc=False, functional="pbesol",
                              potcar_overrides=[], encut=None)
        prepare_inputs(d, cfg, task_type="band")
        txt = (d / "INCAR").read_text()
        assert "LSORBIT" not in txt, txt
        assert "NSW = 1" in txt  # untouched


class TestNelmFallback:
    """vise 0.9.5 drops NELM from -uis — the CLI path must enforce the
    NELM=50 protocol after generation (regression guard)."""

    def test_cli_generation_gets_nelm50(self, tmp_path: Path, monkeypatch):
        from vasp_sop.vasp import io as io_mod

        d = tmp_path / "cpd"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 50\nNELM = 100\n")  # vise template value
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            (d / f).write_text("x\n")
        cfg = SimpleNamespace(soc=False, stage2_soc=False, functional="pbesol",
                              potcar_overrides=[], encut=None)
        # Force the regeneration path: input_ready must be False — INCAR
        # missing triggers generation, but we keep it simple by patching
        # input_ready to False and run_local to no-op.
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        monkeypatch.setattr(io_mod, "run_local", lambda *a, **kw: None)
        io_mod.prepare_inputs(d, cfg, task_type="structure_opt")
        txt = (d / "INCAR").read_text()
        assert "NELM = 50" in txt, txt
        assert "NSW = 50" in txt


class TestTiHubbardUFallback:
    """libs/vise fork's U table lacks Ti — the API path must patch U=4
    (operator decision 2026-08-11). 两阶段(ADR 0025):stage1 只自旋段,
    U 由 apply_final_protocol(stage2)补充。"""

    def test_api_path_stage1_spin_only_then_stage2_u(self, tmp_path: Path):
        from pymatgen.core import Lattice, Structure
        from vasp_sop.vasp import io as io_mod

        struct = Structure(Lattice.cubic(5.0), ["Y", "Ti", "O"],
                           [[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25]])
        struct.to(filename=str(tmp_path / "POSCAR"))
        cfg = SimpleNamespace(soc=False, stage2_soc=False,
                              functional="pbesol", encut=None)
        io_mod.prepare_inputs(tmp_path, cfg, kspacing=0.1, task_type="defect",
                              charge=0.0)
        txt = (tmp_path / "INCAR").read_text()
        assert "LDAU" not in txt, "stage1 无 U(两阶段, ADR 0025)"
        assert "ISPIN = 2" in txt, "自旋段 stage1 保留"
        # stage2:最终协议补 U。
        io_mod.apply_final_protocol(tmp_path, cfg, task_type="defect")
        txt2 = (tmp_path / "INCAR").read_text()
        uu = next(l for l in txt2.splitlines() if "LDAUU" in l)
        assert "4" in uu, uu

    def test_cli_path_stage1_spin_only_then_stage2_u(
        self, tmp_path: Path, monkeypatch
    ):
        from pymatgen.core import Lattice, Structure
        from vasp_sop.vasp import io as io_mod

        struct = Structure(Lattice.cubic(5.0), ["Y", "Ti", "O"],
                           [[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25]])
        struct.to(filename=str(tmp_path / "POSCAR"))
        monkeypatch.setattr(io_mod, "run_local", lambda *a, **kw: None)
        cfg = SimpleNamespace(soc=False, stage2_soc=False, functional="pbesol",
                              encut=None, potcar_overrides=[])
        io_mod.prepare_inputs(tmp_path, cfg, task_type="structure_opt")
        txt = (tmp_path / "INCAR").read_text()
        assert "LDAU" not in txt, "stage1 无 U(两阶段)"
        assert "ISPIN = 2" in txt, "自旋段 stage1 保留(Ti 为 U 表元素)"
        io_mod.apply_final_protocol(tmp_path, cfg, task_type="structure_opt")
        txt2 = (tmp_path / "INCAR").read_text()
        uu = next(l for l in txt2.splitlines() if "LDAUU" in l)
        assert "4" in uu, uu


class TestMagmomPatch:
    """SCF moment lock: MAGMOM per POSCAR atom order (Fe=5.0, others 0)."""

    def test_fe_moments_in_atom_order(self, tmp_path: Path):
        from pymatgen.core import Lattice, Structure
        from vasp_sop.vasp import io as io_mod

        struct = Structure(Lattice.cubic(5.0), ["Sr", "Fe", "O"],
                           [[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25]])
        struct.to(filename=str(tmp_path / "POSCAR"))
        (tmp_path / "INCAR").write_text("ISPIN = 2\n")
        io_mod.patch_incar_magmom(tmp_path)
        txt = (tmp_path / "INCAR").read_text()
        mag = next(l for l in txt.splitlines() if "MAGMOM" in l)
        assert mag == "MAGMOM = 0.0 5.0 0.0", mag

    def test_no_magmom_without_magnetic_species(self, tmp_path: Path):
        from pymatgen.core import Lattice, Structure
        from vasp_sop.vasp import io as io_mod

        struct = Structure(Lattice.cubic(5.0), ["Sr", "Al", "O"],
                           [[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25]])
        struct.to(filename=str(tmp_path / "POSCAR"))
        (tmp_path / "INCAR").write_text("ISPIN = 2\n")
        io_mod.patch_incar_magmom(tmp_path)
        assert "MAGMOM" not in (tmp_path / "INCAR").read_text()


class TestCpdEdiffProtocol:
    """CLI-path EDIFF=1e-4 (operator decision 2026-08-11, incl. cpd)."""

    def test_cli_generation_gets_ediff_1e4(self, tmp_path: Path, monkeypatch):
        from vasp_sop.vasp import io as io_mod

        d = tmp_path / "cpd"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 50\nNELM = 100\nEDIFF = 1e-7\n")
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            (d / f).write_text("x\n")
        cfg = SimpleNamespace(soc=False, stage2_soc=False, functional="pbesol",
                              potcar_overrides=[], encut=None)
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        monkeypatch.setattr(io_mod, "run_local", lambda *a, **kw: None)
        io_mod.prepare_inputs(d, cfg, task_type="structure_opt")
        txt = (d / "INCAR").read_text()
        assert "EDIFF = 1e-4" in txt, txt
        assert "NELM = 50" in txt


class TestEdiffgProtocol:
    """Global EDIFFG=-0.01 for relaxations (operator decision 2026-08-11)."""

    def test_structure_opt_gets_ediffg_001(self, tmp_path: Path, monkeypatch):
        from vasp_sop.vasp import io as io_mod

        d = tmp_path / "cpd"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 50\nEDIFFG = -0.005\n")
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            (d / f).write_text("x\n")
        cfg = SimpleNamespace(soc=False, stage2_soc=False, functional="pbesol",
                              potcar_overrides=[], encut=None)
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        monkeypatch.setattr(io_mod, "run_local", lambda *a, **kw: None)
        io_mod.prepare_inputs(d, cfg, task_type="structure_opt")
        assert "EDIFFG = -0.01" in (d / "INCAR").read_text()

    def test_single_point_tasks_keep_template_ediffg(self, tmp_path: Path,
                                                     monkeypatch):
        from vasp_sop.vasp import io as io_mod

        d = tmp_path / "band"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 50\nEDIFFG = -0.005\n")
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            (d / f).write_text("x\n")
        cfg = SimpleNamespace(soc=False, stage2_soc=False, functional="pbesol",
                              potcar_overrides=[], encut=None)
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        monkeypatch.setattr(io_mod, "run_local", lambda *a, **kw: None)
        io_mod.prepare_inputs(d, cfg, task_type="band")
        assert "EDIFFG = -0.005" in (d / "INCAR").read_text()
