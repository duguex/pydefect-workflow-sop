"""Tests for the VASP convergence verdict — ``vasp/convergence.py``."""

from pathlib import Path

import pytest

from vasp_sop.vasp.convergence import convergence_verdict


def _make_outcar(dir_path: Path, nsw: int = 50, ediffg: float = -0.03,
                 last_ionic_step: int = 3, max_force: float = 0.01,
                 completed: bool = True, ibrion: int = 2) -> Path:
    """Write a synthetic OUTCAR with specified convergence behavior."""
    # Write INCAR so NSW/IBRION are available for check_converged
    (dir_path / "INCAR").write_text(f"NSW = {nsw}\nIBRION = {ibrion}\nEDIFFG = {ediffg}\n")
    lines = [f"  NSW = {nsw}", f"  EDIFFG = {ediffg}"]
    # Add iteration markers + TOTAL-FORCE block for each ionic step
    for ionic in range(1, last_ionic_step + 1):
        for elec in range(1, 6):
            lines.append(f"--------------------------------------- Iteration {elec:4d}({ionic:4d})  ---")
        lines.append(" POSITION                                       TOTAL-FORCE (eV/Angst)")
        lines.append("-" * 80)
        lines.append(f"     0.00000      0.00000      0.00000      {max_force:.6f}      0.00000      0.00000")
        lines.append("")
        lines.append("")

    if completed:
        lines.append("\n General timing and accounting informations for this job:\n")

    outcar = dir_path / "OUTCAR"
    outcar.write_text("\n".join(lines))
    return outcar

class TestVaspJobDone:
    def test_converged(self, tmp_path: Path):
        """Normal convergence: max_f < |EDIFFG|."""
        _make_outcar(tmp_path, nsw=50, last_ionic_step=5, max_force=0.01)
        assert convergence_verdict(tmp_path).converged is True

    def test_unconverged(self, tmp_path: Path):
        """Completed but forces too high: max_f >= |EDIFFG|."""
        _make_outcar(tmp_path, nsw=50, last_ionic_step=50, max_force=0.5)
        assert convergence_verdict(tmp_path).converged is False

    def test_truncated(self, tmp_path: Path):
        """VASP did not finish — no 'General timing and accounting'."""
        _make_outcar(tmp_path, completed=False)
        assert convergence_verdict(tmp_path).converged is False

    def test_no_outcar(self, tmp_path: Path):
        """No OUTCAR file at all."""
        assert convergence_verdict(tmp_path).converged is False

    def test_empty_outcar(self, tmp_path: Path):
        """OUTCAR exists but is empty."""
        (tmp_path / "OUTCAR").write_text("")
        assert convergence_verdict(tmp_path).converged is False

    def test_missing_force_block(self, tmp_path: Path):
        """Relaxation OUTCAR has completion but no TOTAL-FORCE block → unconverged."""
        (tmp_path / "INCAR").write_text("NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n")
        lines = ["  NSW = 50", "  EDIFFG = -0.03",
                 " General timing and accounting informations for this job:"]
        (tmp_path / "OUTCAR").write_text("\n".join(lines))
        assert convergence_verdict(tmp_path).converged is False

    def test_many_atoms_converged(self, tmp_path: Path):
        """Multiple atoms, all forces below threshold."""
        lines = ["  NSW = 50", "  EDIFFG = -0.03"]
        for i in range(1, 6):
            lines.append(f"--------------------------------------- Iteration {i:4d}(  1)  ---")
        lines.append(" POSITION                                       TOTAL-FORCE (eV/Angst)")
        lines.append("-" * 80)
        for _ in range(5):
            lines.append("     0.00000      0.00000      0.00000      0.02000      0.01500      0.02500")
        lines.append("")
        lines.append("\n General timing and accounting informations for this job:\n")
        (tmp_path / "OUTCAR").write_text("\n".join(lines))
        # max_f = 0.025 < 0.03 → converged
        assert convergence_verdict(tmp_path).converged is True

    def test_converged_output_subdir(self, tmp_path: Path):
        """OUTCAR in legacy output/ subdirectory still detected."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        _make_outcar(output_dir, nsw=50, last_ionic_step=4, max_force=0.02)
        assert convergence_verdict(tmp_path).converged is True

    def test_single_point_nsw1(self, tmp_path: Path):
        """NSW=1 single point → always converged if VASP finished."""
        (tmp_path / "INCAR").write_text("NSW = 1\nIBRION = -1\n")
        (tmp_path / "OUTCAR").write_text(
            " General timing and accounting informations for this job:\n")
        assert convergence_verdict(tmp_path).converged is True

    def test_dfpt_dielectric_ibrion8(self, tmp_path: Path):
        """DFPT dielectric (IBRION=8) → always converged if VASP finished."""
        (tmp_path / "INCAR").write_text("NSW = 50\nIBRION = 8\nLEPSILON = .TRUE.\n")
        (tmp_path / "OUTCAR").write_text(
            " General timing and accounting informations for this job:\n")
        assert convergence_verdict(tmp_path).converged is True

    def test_nsw_early_exit_converged(self, tmp_path: Path):
        """NSW=100, 50 ionic steps → converged (exited early = EDIFFG met)."""
        _make_outcar(tmp_path, nsw=100, last_ionic_step=50, max_force=0.01)
        assert convergence_verdict(tmp_path).converged is True

    def test_nsw_exhausted_unconverged(self, tmp_path: Path):
        """NSW=50, 50 steps → unconverged (all NSW used)."""
        _make_outcar(tmp_path, nsw=50, last_ionic_step=50, max_force=0.5)
        assert convergence_verdict(tmp_path).converged is False

    def test_md_ibrion0(self, tmp_path: Path):
        """IBRION=0 molecular dynamics → no relaxation check, converged."""
        (tmp_path / "INCAR").write_text("NSW = 100\nIBRION = 0\n")
        (tmp_path / "OUTCAR").write_text(
            " General timing and accounting informations for this job:\n")
        assert convergence_verdict(tmp_path).converged is True

    def test_no_incar_fallback(self, tmp_path: Path):
        """No INCAR → treated as single point → converged if VASP finished."""
        (tmp_path / "OUTCAR").write_text(
            " General timing and accounting informations for this job:\n")
        assert convergence_verdict(tmp_path).converged is True

    def test_incar_nsw_bump_does_not_false_converge(self, tmp_path: Path):
        """INCAR NSW raised for restart must not make exhausted OUTCAR look converged."""
        _make_outcar(tmp_path, nsw=50, last_ionic_step=50, max_force=0.08)
        # Simulate bulk CONTCAR restart bump
        (tmp_path / "INCAR").write_text("NSW = 250\nIBRION = 2\nEDIFFG = -0.03\n")
        assert convergence_verdict(tmp_path).converged is False

    def test_force_ok_at_full_nsw_is_converged(self, tmp_path: Path):
        """n_ionic == NSW but max|F| <= |EDIFFG| → converged (avoid FN)."""
        _make_outcar(tmp_path, nsw=50, last_ionic_step=50, max_force=0.02)
        assert convergence_verdict(tmp_path).converged is True

    def test_force_fail_even_if_early_exit_counts(self, tmp_path: Path):
        """If forces still high, do not trust n_ionic < NSW alone."""
        _make_outcar(tmp_path, nsw=100, last_ionic_step=40, max_force=0.2)
        assert convergence_verdict(tmp_path).converged is False


class TestVasprunRecovery:
    def test_prepare_copies_contcar_keeps_nsw_ibrion(self, tmp_path: Path):
        """Re-run must not rewrite NSW/IBRION — only CONTCAR→POSCAR + ISTART."""
        from vasp_sop.vasp.io import prepare_vasprun_recovery_run

        (tmp_path / "CONTCAR").write_text("contcar-body\n")
        (tmp_path / "POSCAR").write_text("old-poscar\n")
        (tmp_path / "INCAR").write_text("NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n")
        (tmp_path / "POTCAR").write_text("p\n")
        (tmp_path / "KPOINTS").write_text("k\n")
        assert prepare_vasprun_recovery_run(tmp_path)
        assert (tmp_path / "POSCAR").read_text() == "contcar-body\n"
        text = (tmp_path / "INCAR").read_text()
        assert "NSW = 50" in text
        assert "IBRION = 2" in text
        assert "EDIFFG = -0.03" in text
        assert "ISTART = 1" in text




class TestVerdictSidecar:
    """Persistent mtime-keyed verdict memo (batch status/progress speed)."""

    def test_sidecar_persists_and_isolates_task_type(self, tmp_path):
        from vasp_sop.core.paths import override_cache_root
        import vasp_sop.vasp.convergence as conv

        override_cache_root(tmp_path / ".vasp_sop")
        conv._verdict_cache.clear()
        conv._verdict_loaded = False
        conv._verdict_dirty.clear()

        d = tmp_path / "calc"
        d.mkdir()
        _make_outcar(d, nsw=40, ediffg=-0.03, max_force=0.001, last_ionic_step=3)

        v_relax = conv.convergence_verdict(d)
        v_band = conv.convergence_verdict(d, "band")
        assert v_relax.reason != v_band.reason, \
            "task_type must not poison the shared OUTCAR key"

        conv._flush_sidecar()
        assert conv._sidecar_path().is_file()

        # Fresh-process view: clear the memo, reload from the sidecar.
        conv._verdict_cache.clear()
        conv._verdict_loaded = False
        v2 = conv.convergence_verdict(d)
        assert v2 == v_relax, "sidecar must reproduce the relaxation verdict"

        # Reload must also keep the band verdict per task type.
        assert conv.convergence_verdict(d, "band") == v_band

    def test_sidecar_schema_bump_invalidates_stale_verdicts(self, tmp_path):
        """Pre-gate sidecars (schema 1) must not replay after verdict logic
        changes — a stale 'converged' memo would skip the ADR 0016 NELM
        gate forever (same OUTCAR mtime)."""
        from vasp_sop.core.paths import override_cache_root
        import vasp_sop.vasp.convergence as conv

        override_cache_root(tmp_path / ".vasp_sop")
        conv._verdict_cache.clear()
        conv._verdict_loaded = False
        conv._verdict_dirty.clear()

        d = tmp_path / "calc"
        d.mkdir()
        # NELM-exhausted OUTCAR: real verdict is electronic_not_conv, but
        # the stale schema-1 sidecar says force_gate converged.
        (d / "OUTCAR").write_text(
            "NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n"
            "TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.001 0.001 0.001 0.001 0.001 0.001\n"
            "|     spurious results, we suggest increasing NELM, if you were "
            "close to      |\n"
            " General timing and accounting informations for this job:\n")
        (d / "INCAR").write_text("NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n")
        # Poison: a stale schema-1 sidecar with a converged relaxation verdict.
        sp = conv._sidecar_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text('{"%s": {"structure_opt": {"mtime": %d, "verdict": {"converged": true, "reason": "force_gate"}}}}' % (d / "OUTCAR", (d / "OUTCAR").stat().st_mtime))

        conv._verdict_cache.clear()
        conv._verdict_loaded = False
        v = conv.convergence_verdict(d)
        assert not v.converged, "schema-1 stale memo must be discarded"
