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
