"""Retry-policy decision matrix — pure-function tests.

The policy evaluators consume normalized evidence only (no System, no crisp,
no JobStore writes): convergence-verdict reason/converged, latest
calculation state, restart-history evidence, CONTCAR availability, ZBRENT
tail evidence, and (one-shot) the operator arm signal.  The tables lock the
ADR 0017 CPD rules, the state-driven reason-blind defect rules (ADR 0010
rev / ADR 0016), and the COMPETING one-shot (ADR 0007):

- a stalled relaxation (force gate / NSW exhausted / missing forces)
  auto-restarts from CONTCAR up to ``CPD_MAX_IONIC_RESTARTS`` times;
- transient truncation is exempt from that budget and carries the long
  cluster tag;
- ZBRENT evidence is decision metadata (an EDIFF adjustment) and never
  overrides the restart budget;
- electronic NELM exhaustion stays manual for CPD and the one-shot — an
  identical rerun cannot cure it — but NOT for defect restarts, which are
  state-driven and reason-blind;
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


# ── defect decision table (state-driven, reason-blind) ────────────────
# Mirrors wave2's defect branch: any restart-eligible latest state
# (failed / unconverged / pending) or a stale "converged" record with an
# unconverged disk verdict (ADR 0016) auto-restarts every cycle, and *no*
# verdict reason — not even electronic_not_conv, not even an unknown one —
# demotes it to manual.  The submission shape is CONTCAR-driven: own
# CONTCAR → continuation, none → fresh submission.  ZBRENT stays decision
# metadata (the same EDIFF downgrade as CPD).
DEFECT_CASES: list[tuple] = [
    # (id, latest_state, verdict_converged, verdict_reason, has_conticar,
    #  has_zbrent, disposition, explanation, submission_source,
    #  continue_from_contcar, incar_adjustment, tags)
    # ── restart-eligible latest states → automatic, artifact-driven ──
    (
        "failed-with-contcar",
        "failed", False, REASON_FORCE_GATE_FAIL, True, False,
        "automatic",
        "defect restart-eligible (failed): continue from CONTCAR",
        rp.DEFECT_RESTART_SOURCE, True, None, (),
    ),
    (
        "failed-no-contcar",
        "failed", False, REASON_FORCE_GATE_FAIL, False, False,
        "automatic",
        "defect restart-eligible (failed): fresh submission (no CONTCAR)",
        rp.DEFECT_RESTART_SOURCE, False, None, (),
    ),
    (
        "unconverged-with-contcar",
        "unconverged", False, REASON_NSW_EXHAUSTED, True, False,
        "automatic",
        "defect restart-eligible (unconverged): continue from CONTCAR",
        rp.DEFECT_RESTART_SOURCE, True, None, (),
    ),
    (
        "pending-no-contcar",
        "pending", False, None, False, False,
        "automatic",
        "defect restart-eligible (pending): fresh submission (no CONTCAR)",
        rp.DEFECT_RESTART_SOURCE, False, None, (),
    ),
    # ── reason-blind: electronic_not_conv and unknown reasons still
    # ── restart automatically (only the one-shot excludes NELM) ──
    (
        "electronic_not_conv-restarts",
        "failed", False, REASON_ELECTRONIC_NOT_CONV, True, False,
        "automatic",
        "defect restart-eligible (failed): continue from CONTCAR",
        rp.DEFECT_RESTART_SOURCE, True, None, (),
    ),
    (
        "unknown-reason-restarts",
        "unconverged", False, "weird_crash", True, False,
        "automatic",
        "defect restart-eligible (unconverged): continue from CONTCAR",
        rp.DEFECT_RESTART_SOURCE, True, None, (),
    ),
    # ── ZBRENT is metadata only: EDIFF adjustment, still automatic ──
    (
        "failed-zbrent",
        "failed", False, REASON_FORCE_GATE_FAIL, True, True,
        "automatic",
        "defect restart-eligible (failed): continue from CONTCAR "
        "(ZBRENT: EDIFF=1e-6)",
        rp.DEFECT_RESTART_SOURCE, True, {"EDIFF": "1e-6"}, (),
    ),
    # ── already submitted → wait (checked before the verdict) ──
    (
        "submitted-waits",
        "submitted", False, REASON_FORCE_GATE_FAIL, True, False,
        "wait",
        "calculation is already submitted; retry policy waits",
        None, False, None, (),
    ),
    (
        "submitted-wins-over-converged",
        "submitted", True, REASON_NSW_EARLY_EXIT, True, False,
        "wait",
        "calculation is already submitted; retry policy waits",
        None, False, None, (),
    ),
    # ── converged verdict → none, whatever the latest state says ──
    (
        "verdict-converged-none",
        "failed", True, REASON_NSW_EARLY_EXIT, False, False,
        "none",
        "calculation converged; no retry",
        None, False, None, (),
    ),
    # ── stale "converged" record + unconverged disk verdict (ADR 0016)
    # ── → automatic, artifact-driven like the rest ──
    (
        "stale-converged-with-contcar",
        "converged", False, REASON_ELECTRONIC_NOT_CONV, True, False,
        "automatic",
        "defect stale 'converged' record (ADR 0016): continue from "
        "CONTCAR",
        rp.DEFECT_RESTART_SOURCE, True, None, (),
    ),
    (
        "stale-converged-no-contcar",
        "converged", False, None, False, False,
        "automatic",
        "defect stale 'converged' record (ADR 0016): fresh submission "
        "(no CONTCAR)",
        rp.DEFECT_RESTART_SOURCE, False, None, (),
    ),
    # ── converged record + converged verdict → none ──
    (
        "converged-record-verdict-converged-none",
        "converged", True, None, True, False,
        "none",
        "calculation converged; no retry",
        None, False, None, (),
    ),
    # ── never-run / unknown latest states → conservative manual
    # ── (first submission and chain seeding are out of retry scope) ──
    (
        "never-run-manual",
        None, False, None, False, False,
        "manual",
        "defect latest state None is not restart-eligible; conservative "
        "manual",
        None, False, None, (),
    ),
]


@pytest.mark.parametrize("case", DEFECT_CASES, ids=[c[0] for c in DEFECT_CASES])
def test_defect_decision_table(case: tuple) -> None:
    (_id, state, converged, reason, contcar, zbrent,
     disposition, explanation, source, from_contcar, incar, tags) = case
    d = rp.evaluate_defect(
        latest_state=state,
        verdict_converged=converged,
        verdict_reason=reason,
        has_conticar=contcar,
        has_zbrent=zbrent,
    )
    assert d.disposition == disposition
    assert d.explanation == explanation
    if disposition == "automatic":
        # Automatic decisions carry the complete submission contract.
        assert d.submission_source == source
        assert d.continue_from_contcar is from_contcar
        assert d.incar_adjustment == incar
        assert d.tags == tags
    else:
        # Non-automatic decisions never claim submission mechanics.
        assert d.submission_source is None
        assert d.continue_from_contcar is False
        assert d.incar_adjustment is None
        assert d.tags == ()


# ── COMPETING one-shot (ADR 0007) decision table ──────────────────────
# ``batch run --retry-failed`` is a human-armed signal that resubmits a
# failed/unconverged competing phase exactly once (recorded ``auto_retry``);
# a second failure is terminal forever, and deterministic electronic NELM
# exhaustion (ADR 0017) never burns the one-shot.
ONE_SHOT_CASES: list[tuple] = [
    # (id, latest_state, verdict_converged, verdict_reason,
    #  already_auto_retried, retry_failed_armed, disposition, explanation,
    #  submission_source, continue_from_contcar)
    (
        "armed-fresh-failure",
        "failed", False, "vasp_crash", False, True,
        "automatic",
        "COMPETING one-shot: resubmit once (auto_retry armed by "
        "--retry-failed)",
        rp.COMPETING_RETRY_SOURCE, False,
    ),
    (
        "armed-unconverged",
        "unconverged", False, REASON_NSW_EXHAUSTED, False, True,
        "automatic",
        "COMPETING one-shot: resubmit once (auto_retry armed by "
        "--retry-failed)",
        rp.COMPETING_RETRY_SOURCE, False,
    ),
    # ── second failure is terminal forever ──
    (
        "armed-already-used",
        "failed", False, "vasp_crash", True, True,
        "manual",
        "COMPETING one-shot auto_retry already spent: second failure is "
        "terminal; manual parameter/operator decision required",
        None, False,
    ),
    # ── electronic NELM exhaustion never burns the one-shot ──
    (
        "armed-electronic-excluded",
        "failed", False, REASON_ELECTRONIC_NOT_CONV, False, True,
        "manual",
        "electronic convergence failure (NELM): excluded from one-shot "
        "auto_retry; parameter decision required",
        None, False,
    ),
    # ── unarmed → the human must arm --retry-failed ──
    (
        "unarmed-failed",
        "failed", False, "vasp_crash", False, False,
        "manual",
        "COMPETING one-shot auto_retry not armed (--retry-failed); "
        "operator decision required",
        None, False,
    ),
    # ── submitted → wait (checked before everything else) ──
    (
        "submitted-waits",
        "submitted", False, None, False, False,
        "wait",
        "calculation is already submitted; retry policy waits",
        None, False,
    ),
    # ── converged verdict → none ──
    (
        "converged-none",
        "failed", True, None, False, True,
        "none",
        "calculation converged; no retry",
        None, False,
    ),
]


@pytest.mark.parametrize("case", ONE_SHOT_CASES, ids=[c[0] for c in ONE_SHOT_CASES])
def test_competing_one_shot_table(case: tuple) -> None:
    (_id, state, converged, reason, already_retried, armed,
     disposition, explanation, source, from_contcar) = case
    d = rp.evaluate_competing_one_shot(
        latest_state=state,
        verdict_converged=converged,
        verdict_reason=reason,
        already_auto_retried=already_retried,
        retry_failed_armed=armed,
    )
    assert d.disposition == disposition
    assert d.explanation == explanation
    if disposition == "automatic":
        assert d.submission_source == source
        assert d.continue_from_contcar is from_contcar
        assert d.incar_adjustment is None
        assert d.tags == ()
    else:
        assert d.submission_source is None
        assert d.continue_from_contcar is False
        assert d.incar_adjustment is None
        assert d.tags == ()


def test_defect_and_one_shot_source_constants() -> None:
    # Defect restarts are recorded with the executor's "restart" source;
    # the COMPETING one-shot with "auto_retry" (ADR 0007).
    assert rp.DEFECT_RESTART_SOURCE == "restart"
    assert rp.COMPETING_RETRY_SOURCE == "auto_retry"


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
