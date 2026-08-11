"""ADR 0016 electronic-convergence gate tests.

VASP prints "reached required accuracy" even when the last electronic
step hit NELM (warning: "spurious results ... increasing NELM").
pydefect's electronic_conv is False then and the energy is unreliable —
convergence_verdict must refuse too, scanning past the 256KB tail window
(the warning can sit MBs before EOF when later ionic steps follow).
"""

from pathlib import Path

import pytest

from vasp_sop.vasp.convergence import (
    REASON_ELECTRONIC_NOT_CONV,
    REASON_FORCE_GATE_FAIL,
    REASON_NOT_RELAXATION,
    REASON_TRUNCATED,
    convergence_verdict,
)


def _outcar(d: Path, *, nelm_warning: bool = True, warning_offset: int = 0):
    """Write an OUTCAR that satisfies the timing + force gates, with an
    optional NELM warning at *warning_offset* bytes from EOF (default:
    right after the header, far outside the tail window)."""
    head = "NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n"
    force_block = (
        "TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        " 0.001 0.001 0.001 0.001 0.001 0.001\n"
    )
    if nelm_warning:
        warn = (
            "|     number of steps (NELM). The forces and other quantities "
            "evaluated       |\n"
            "|     spurious results, we suggest increasing NELM, if you were "
            "close to      |\n"
        )
    else:
        warn = ""
    tail = (
        " General timing and accounting informations for this job:\n"
        "  100.00% CPU utilisation\n"
    )
    if warning_offset:
        content = head + "X" * (warning_offset - len(warn)) + warn + force_block + tail
    else:
        content = head + force_block + warn + tail
    (d / "OUTCAR").write_text(content)
    (d / "INCAR").write_text("NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n")


class TestElectronicGate:
    def test_nelm_warning_rejects_verdict(self, tmp_path: Path):
        d = tmp_path / "calc"
        d.mkdir()
        _outcar(d, nelm_warning=True)
        v = convergence_verdict(d)
        assert not v.converged
        assert v.reason == REASON_ELECTRONIC_NOT_CONV

    def test_warning_far_before_eof_still_detected(self, tmp_path: Path):
        """Warning >256KB before EOF (later ionic steps followed) must be
        found by the full-file fallback."""
        d = tmp_path / "calc"
        d.mkdir()
        _outcar(d, nelm_warning=True, warning_offset=400_000)
        v = convergence_verdict(d)
        assert not v.converged
        assert v.reason == REASON_ELECTRONIC_NOT_CONV

    def test_clean_relaxation_stays_converged(self, tmp_path: Path):
        d = tmp_path / "calc"
        d.mkdir()
        _outcar(d, nelm_warning=False)
        v = convergence_verdict(d)
        assert v.converged

    def test_single_point_with_warning_rejected(self, tmp_path: Path):
        """Even non-relaxation tasks must not pass with electronic junk."""
        d = tmp_path / "calc"
        d.mkdir()
        (d / "INCAR").write_text("NSW = 0\n")
        _outcar(d, nelm_warning=True)
        (d / "INCAR").write_text("NSW = 0\n")
        v = convergence_verdict(d, task_type="band")
        assert not v.converged
        assert v.reason == REASON_ELECTRONIC_NOT_CONV


class TestNelmWarningPosition:
    """The electronic gate counts a NELM warning only when it belongs to
    the FINAL ionic step (v3).  VASP prints the warning per ionic step; an
    early-step exhaustion followed by converged steps leaves the final
    forces/energies reliable, so the verdict must not fail the calc."""

    _WARN = "increasing NELM, if you were close to convergence\n"

    def _write(self, d: Path, *, first_step: str, second_step: str) -> None:
        (d / "OUTCAR").write_text(
            "NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n"
            "LOOP+:\n" + first_step +
            "LOOP+:\n" + second_step +
            "TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.001 0.001 0.001 0.001 0.001 0.001\n"
            " General timing and accounting informations for this job:\n"
        )
        (d / "INCAR").write_text("NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n")

    def test_early_step_warning_passes(self, tmp_path: Path):
        """Warning in the first ionic step, second step clean → converged."""
        d = tmp_path / "calc"
        d.mkdir()
        self._write(d, first_step=self._WARN, second_step="")
        v = convergence_verdict(d)
        assert v.converged

    def test_final_step_warning_fails(self, tmp_path: Path):
        """Warning inside the final ionic step's electronic loop is the
        ADR 0016 failure case → unconverged."""
        d = tmp_path / "calc"
        d.mkdir()
        self._write(d, first_step="", second_step=self._WARN)
        v = convergence_verdict(d)
        assert not v.converged
        assert v.reason == REASON_ELECTRONIC_NOT_CONV


class TestSocSinglePointVerdict:
    """ADR 0014 soc2 jobs are NSW=0 single points whose OUTCAR ends with
    the converged electronic tail but has no ionic timing block — they
    must not be judged truncated."""

    _SP = (
        "NSW = 0\nIBRION = -1\nLSORBIT = .TRUE.\n"
        "DAV:  10 -0.8E+03  0.1E-06  0.2E-07  720  0.3E-03\n"
        " reached required accuracy - stopping structural energy minimisation\n"
    )

    def _sp_dir(self, d):
        d.mkdir(parents=True)
        (d / "OUTCAR").write_text(self._SP)
        (d / "INCAR").write_text("NSW = 0\nIBRION = -1\nLSORBIT = .TRUE.\n")
        return d

    def test_single_point_with_accuracy_is_converged(self, tmp_path):
        d = self._sp_dir(tmp_path / "calc")
        v = convergence_verdict(d)
        assert v.converged
        assert v.reason == REASON_NOT_RELAXATION

    def test_single_point_empty_outcar_still_truncated(self, tmp_path):
        d = tmp_path / "calc"
        d.mkdir()
        (d / "OUTCAR").write_text("NSW = 0\n")
        v = convergence_verdict(d)
        assert not v.converged
        assert v.reason == REASON_TRUNCATED

    def test_relaxation_killed_after_accuracy_uses_force_gate(self, tmp_path):
        """Relaxation that died right after writing accuracy (no timing):
        judged by the force gate, not blanket-truncated."""
        d = tmp_path / "calc"
        d.mkdir()
        (d / "OUTCAR").write_text(
            "NSW = 50\nIBRION = 2\nEDIFFG = -0.01\n"
            " reached required accuracy - stopping structural energy minimisation\n"
            "TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.5 0.5 0.5 0.2 0.2 0.2\n"
        )
        v = convergence_verdict(d)
        assert not v.converged
        assert v.reason == REASON_FORCE_GATE_FAIL
