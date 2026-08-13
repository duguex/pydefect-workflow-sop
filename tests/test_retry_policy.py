"""Retry-policy decision matrix (CPD core) — pure-function tests.

The policy evaluator consumes normalized evidence only (no System, no crisp,
no JobStore writes): convergence-verdict reason/converged, latest
calculation state, ionic-restart history count, CONTCAR availability, and
ZBRENT tail evidence.  The table locks the ADR 0017 CPD rules:

- a stalled relaxation (force gate / NSW exhausted / missing forces)
  auto-restarts from CONTCAR up to ``CPD_MAX_IONIC_RESTARTS`` times;
- transient truncation is exempt from that budget and carries the long
  cluster tag;
- ZBRENT evidence is decision metadata (an EDIFF adjustment) and never
  overrides the restart budget;
- electronic NELM exhaustion stays manual — an identical rerun cannot cure
  it;
- a submitted calculation waits; a converged verdict is inactive; missing
  CONTCAR and unknown reasons fall back conservatively to manual.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vasp_sop.core import retry_policy as rp
from vasp_sop.vasp.convergence import (
    REASON_ELECTRONIC_NOT_CONV,
    REASON_FORCE_GATE_FAIL,
    REASON_MISSING_FORCES,
    REASON_NSW_EARLY_EXIT,
    REASON_NSW_EXHAUSTED,
    REASON_TRUNCATED,
)

# Row: (id, verdict_reason, verdict_converged, latest_state, ionic_restarts,
#       has_conticar, has_zbrent, disposition, explanation, incar_adjustment,
#       tags)
CPD_CASES: list[tuple] = [
    # ── stalled relaxation within the budget → automatic continuation ──
    (
        "force_gate_fail-in-budget",
        REASON_FORCE_GATE_FAIL, False, None, 0, True, False,
        "automatic",
        "unconverged (force_gate_fail): continue from CONTCAR",
        None, (),
    ),
    (
        "nsw_exhausted-in-budget",
        REASON_NSW_EXHAUSTED, False, None, 0, True, False,
        "automatic",
        "unconverged (nsw_exhausted): continue from CONTCAR",
        None, (),
    ),
    (
        "nsw_early_exit-in-budget",
        REASON_NSW_EARLY_EXIT, False, None, 0, True, False,
        "automatic",
        "unconverged (nsw_early_exit): continue from CONTCAR",
        None, (),
    ),
    (
        "missing_forces-in-budget",
        REASON_MISSING_FORCES, False, None, 0, True, False,
        "automatic",
        "unconverged (missing_forces): continue from CONTCAR",
        None, (),
    ),
    # Second restart still within the 3-restart budget.
    (
        "force_gate_fail-last-in-budget",
        REASON_FORCE_GATE_FAIL, False, None, 2, True, False,
        "automatic",
        "unconverged (force_gate_fail): continue from CONTCAR",
        None, (),
    ),
    # ── stalled relaxation at the budget → manual (parameter decision) ──
    (
        "force_gate_fail-at-budget",
        REASON_FORCE_GATE_FAIL, False, None, 3, True, False,
        "manual",
        "auto-restart budget exhausted (3 ionic restarts without "
        "convergence); parameter decision required",
        None, (),
    ),
    # ── transient truncation is budget-exempt and carries the long tag ──
    (
        "truncated-beyond-budget",
        REASON_TRUNCATED, False, None, 9, True, False,
        "automatic",
        "truncated run: continue from CONTCAR on long-QOS cluster "
        "(budget-exempt)",
        None, ("long",),
    ),
    (
        "truncated-with-zbrent",
        REASON_TRUNCATED, False, None, 9, True, True,
        "automatic",
        "truncated run: continue from CONTCAR on long-QOS cluster "
        "(budget-exempt) (ZBRENT: EDIFF=1e-6)",
        {"EDIFF": "1e-6"}, ("long",),
    ),
    # ── missing CONTCAR → manual, whatever the reason ──
    (
        "missing-conticar",
        REASON_FORCE_GATE_FAIL, False, None, 0, False, False,
        "manual",
        "no CONTCAR to continue from; manual decision required",
        None, (),
    ),
    # ── electronic NELM exhaustion → manual, never blind-retried ──
    (
        "electronic_not_conv",
        REASON_ELECTRONIC_NOT_CONV, False, None, 0, True, False,
        "manual",
        "electronic convergence failure (NELM): identical rerun cannot "
        "cure it; parameter decision required",
        None, (),
    ),
    # ── ZBRENT is metadata only: EDIFF adjustment inside the budget, ──
    # ── manual once the budget is exhausted                        ──
    (
        "zbrent-in-budget",
        REASON_FORCE_GATE_FAIL, False, None, 2, True, True,
        "automatic",
        "unconverged (force_gate_fail): continue from CONTCAR "
        "(ZBRENT: EDIFF=1e-6)",
        {"EDIFF": "1e-6"}, (),
    ),
    (
        "zbrent-at-budget",
        REASON_FORCE_GATE_FAIL, False, None, 3, True, True,
        "manual",
        "auto-restart budget exhausted (3 ionic restarts without "
        "convergence); parameter decision required",
        None, (),
    ),
    # ── already submitted → wait (checked before the verdict) ──
    (
        "submitted-waits",
        REASON_FORCE_GATE_FAIL, False, "submitted", 0, True, False,
        "wait",
        "calculation is already submitted; retry policy waits",
        None, (),
    ),
    (
        "submitted-wins-over-converged",
        REASON_NSW_EARLY_EXIT, True, "submitted", 0, True, False,
        "wait",
        "calculation is already submitted; retry policy waits",
        None, (),
    ),
    # ── converged verdict → none (even when the reason name looks
    # ── retryable: "nsw_early_exit" also marks a successful early exit) ──
    (
        "converged-none",
        REASON_NSW_EARLY_EXIT, True, None, 0, True, False,
        "none",
        "calculation converged; no retry",
        None, (),
    ),
    # ── unknown / absent reason → conservative manual ──
    (
        "unknown-reason-manual",
        "weird_crash", False, None, 0, True, False,
        "manual",
        "verdict reason 'weird_crash' is not auto-retryable; manual "
        "decision required",
        None, (),
    ),
    (
        "no-reason-manual",
        None, False, None, 0, True, False,
        "manual",
        "no convergence-verdict reason; conservative manual",
        None, (),
    ),
]


@pytest.mark.parametrize("case", CPD_CASES, ids=[c[0] for c in CPD_CASES])
def test_cpd_decision_table(case: tuple) -> None:
    (_id, reason, converged, state, restarts, contcar, zbrent,
     disposition, explanation, incar, tags) = case
    d = rp.evaluate_cpd(
        verdict_reason=reason,
        verdict_converged=converged,
        latest_state=state,
        ionic_restarts=restarts,
        has_conticar=contcar,
        has_zbrent=zbrent,
    )
    assert d.disposition == disposition
    assert d.explanation == explanation
    if disposition == "automatic":
        # Automatic decisions carry the complete submission contract.
        assert d.submission_source == rp.IONIC_RESTART_SOURCE
        assert d.submission_source == "ionic_restart"
        assert d.continue_from_contcar is True
        assert d.incar_adjustment == incar
        assert d.tags == tags
    else:
        # Non-automatic decisions never claim submission mechanics.
        assert d.submission_source is None
        assert d.continue_from_contcar is False
        assert d.incar_adjustment is None
        assert d.tags == ()


def test_retryable_reasons_exact_set() -> None:
    assert rp.RETRYABLE_REASONS == frozenset({
        "force_gate_fail", "nsw_exhausted", "nsw_early_exit",
        "missing_forces", "truncated",
    })


def test_budget_constants() -> None:
    assert rp.CPD_MAX_IONIC_RESTARTS == 3
    # Relocated from orchestrator._MAX_RESTART — same budget, new home.
    assert rp.UNCONVERGED_MAX_RESTARTS == 5


def test_source_and_tag_constants() -> None:
    assert rp.IONIC_RESTART_SOURCE == "ionic_restart"
    assert rp.LONG_TAG == "long"


# ── ZBRENT OUTCAR-tail probe ──────────────────────────────────────────

def test_zbrent_marker_in_tail_is_true(tmp_path: Path) -> None:
    (tmp_path / "OUTCAR").write_text(
        "something\n---  I REFUSE TO CONTINUE WITH THIS SICK JOB ---\n"
        "ZBRENT: fatal error in bracketing\n"
    )
    assert rp.has_zbrent_failure(tmp_path) is True


def test_zbrent_marker_outside_tail_window_is_false(tmp_path: Path) -> None:
    """Only the last 64 KiB of OUTCAR is scanned — a stale ZBRENT marker
    from a much earlier run must not decide the probe."""
    (tmp_path / "OUTCAR").write_bytes(b"ZBRENT" + b"a" * 70000)
    assert rp.has_zbrent_failure(tmp_path) is False


def test_zbrent_missing_outcar_is_false(tmp_path: Path) -> None:
    assert rp.has_zbrent_failure(tmp_path) is False


def test_decision_is_immutable() -> None:
    d = rp.evaluate_cpd(
        verdict_reason=REASON_TRUNCATED,
        verdict_converged=False,
        latest_state=None,
        ionic_restarts=0,
        has_conticar=True,
    )
    with pytest.raises(AttributeError):
        d.disposition = "manual"  # type: ignore[misc]

    zb = rp.evaluate_cpd(
        verdict_reason=REASON_TRUNCATED,
        verdict_converged=False,
        latest_state=None,
        ionic_restarts=0,
        has_conticar=True,
        has_zbrent=True,
    )
    assert zb.incar_adjustment == {"EDIFF": "1e-6"}
    with pytest.raises(TypeError):
        zb.incar_adjustment["EDIFF"] = "1e-7"  # type: ignore[index]
