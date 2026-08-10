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
