"""Pure retry-policy seam — the single authority on retry eligibility.

One calculation directory in, one structured :class:`Decision` out.  Both
the Wave 2 executor (vasp_sop/core/orchestrator.py) and the read-only
runtime dependency audit (vasp_sop/report/deps.py) consume this module, so
a policy change cannot alter execution without altering the DAG view, or
vice versa.

The evaluators are deterministic: they accept *normalized evidence only* —
the convergence-verdict reason and converged flag, the latest calculation
state, restart/source history evidence, whether a CONTCAR to continue from
exists, (from :func:`has_zbrent_failure`) whether the last run died in a
ZBRENT line-search abort, and (COMPETING one-shot) the operator's
``--retry-failed`` arm signal.  Callers collect that evidence from disk /
JobStore / crisp; this module never reads JobStore, edits INCAR, submits
work, or decides charge-state-chain prerequisites.

Three surfaces:

- :func:`evaluate_cpd` — CPD phases (ADR 0017, issue #119): stalled
  relaxations continue from their own CONTCAR up to
  ``CPD_MAX_IONIC_RESTARTS`` times (transient truncation budget-exempt and
  long-QOS tagged); ZBRENT is decision metadata (an EDIFF downgrade) and
  never overrides the budget; NELM exhaustion, unknown reasons, budget
  exhaustion, and missing CONTCAR resolve conservatively to ``manual``;
  submitted → ``wait``; converged → ``none``.

- :func:`evaluate_defect` — defect dirs (ADR 0010 revision, ADR 0016):
  state-driven and reason-blind.  Any restart-eligible latest state
  (``failed`` / ``unconverged`` / ``pending``) or a stale ``converged``
  record whose disk verdict is unconverged auto-restarts every cycle; the
  submission shape is CONTCAR-driven (own CONTCAR → continuation, none →
  fresh submission), and *no* verdict reason — not even
  ``electronic_not_conv``, not even an unknown one — demotes it to
  ``manual``.

- :func:`evaluate_competing_one_shot` — the COMPETING ``--retry-failed``
  one-shot (ADR 0007): a human arm resubmits a failed/unconverged
  competing phase exactly once (source ``auto_retry``); a second failure is
  terminal forever; deterministic NELM exhaustion is excluded.

This module is side-effect free apart from the read-only OUTCAR-tail probe
:func:`has_zbrent_failure`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, Mapping

from vasp_sop.vasp.convergence import (
    REASON_ELECTRONIC_NOT_CONV,
    REASON_FORCE_GATE_FAIL,
    REASON_MISSING_FORCES,
    REASON_NSW_EARLY_EXIT,
    REASON_NSW_EXHAUSTED,
    REASON_TRUNCATED,
)

# Convergence-verdict reasons that justify an automatic ionic continuation
# from CONTCAR: the structure simply needs more ionic steps.  ``truncated``
# is a TIME-LIMIT / killed run — transient, budget-exempt, long-QOS tag.
# Electronic NELM exhaustion is deliberately excluded (ADR 0017); unknown
# reasons are excluded conservatively.
RETRYABLE_REASONS: Final[frozenset[str]] = frozenset({
    REASON_FORCE_GATE_FAIL,
    REASON_NSW_EXHAUSTED,
    REASON_NSW_EARLY_EXIT,
    REASON_MISSING_FORCES,
    REASON_TRUNCATED,
})

# Cap on automatic CONTCAR restarts per CPD phase (ADR 0017).  A phase whose
# forces stall (e.g. an over-strict EDIFFG) would otherwise be resubmitted
# every cycle forever, burning core-hours; past the cap the phase needs a
# parameter decision, not more iterations.
CPD_MAX_IONIC_RESTARTS: Final[int] = 3

# Cap on automatic CONTCAR restarts of an unconverged relaxation before it
# is declared terminal (the ``handle_unconverged`` budget, ADR 0008 —
# relocated from ``orchestrator._MAX_RESTART``).
UNCONVERGED_MAX_RESTARTS: Final[int] = 5

# JobStore submission source recorded for automatic ionic restarts.
IONIC_RESTART_SOURCE: Final[str] = "ionic_restart"

# JobStore submission source recorded for defect restarts (wave2's defect
# branch uses source="restart" for both CONTCAR continuations and fresh
# resubmissions).
DEFECT_RESTART_SOURCE: Final[str] = "restart"

# JobStore submission source recorded for the COMPETING one-shot auto-rerun
# (ADR 0007): a failed/unconverged competing phase gets exactly one resubmit
# marked ``auto_retry``; a second failure is terminal forever — the spent
# marker blocks every later pass even with ``--retry-failed`` re-armed, so
# recovery is a manual operator decision outside the pipeline.
COMPETING_RETRY_SOURCE: Final[str] = "auto_retry"

# Cluster tag requested for a transiently-truncated continuation, so it
# lands on a long-QOS cluster instead of being killed repeatedly.
LONG_TAG: Final[str] = "long"

# EDIFF downgrade applied to dirs whose last attempt died in a ZBRENT
# line-search bracket failure (issue #119: metallic phases at the global
# EDIFF=1e-4 leave too much force noise for the line search; EDIFF=1e-6
# converged the same dirs).  Exposed here so the executor maps the decision
# metadata onto ``patch_incar(cd, **decision.incar_adjustment)``.
ZBRENT_EDIF: Final[str] = "1e-6"

# Tail window for :func:`has_zbrent_failure` — the marker can be MBs before
# EOF when later ionic steps followed, but a fresh crash is always near the
# end.
_ZBRENT_TAIL_BYTES = 65536


def has_zbrent_failure(path: Path) -> bool:
    """True when the last run in *path* died in a ZBRENT line-search
    bracket failure.

    ``path`` is the calculation directory; only the tail of its OUTCAR is
    read (the marker of a *fresh* abort sits at the end; an older marker
    buried beyond the tail window belongs to an earlier run and must not
    decide the probe).  Never raises.
    """
    outcar = path / "OUTCAR"
    if not outcar.is_file():
        return False
    try:
        with open(outcar, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _ZBRENT_TAIL_BYTES))
            return b"ZBRENT" in f.read()
    except OSError:
        return False


Disposition = Literal["wait", "automatic", "manual", "none"]


@dataclass(frozen=True)
class Decision:
    """Immutable retry decision for one calculation directory.

    ``disposition`` is the whole decision surface:

    - ``wait`` — the calculation is already in flight; retry policy waits
      for its result;
    - ``automatic`` — a known mechanical continuation applies; the
      submission metadata below tells the executor exactly what to do;
    - ``manual`` — no safe automatic action exists; a human decides;
    - ``none`` — the calculation is converged; nothing to do.

    ``explanation`` is a stable, canonical reason string for logs and the
    DAG view.  The remaining fields are automatic-only: which submission
    source to record, whether to continue from CONTCAR, any INCAR
    adjustment to apply before submission (``{"EDIFF": ZBRENT_EDIF}`` for
    ZBRENT evidence), and any cluster tags to request (``("long",)`` for
    transient truncation).
    """

    disposition: Disposition
    explanation: str
    submission_source: str | None = None
    continue_from_contcar: bool = False
    incar_adjustment: Mapping[str, str] | None = None
    tags: tuple[str, ...] = ()


def _manual(explanation: str) -> Decision:
    return Decision(disposition="manual", explanation=explanation)


def evaluate_cpd(
    *,
    verdict_reason: str | None,
    verdict_converged: bool,
    latest_state: str | None,
    ionic_restarts: int,
    has_conticar: bool,
    has_zbrent: bool = False,
) -> Decision:
    """Evaluate the retry decision for one CPD phase directory.

    Evidence is normalized at the seam (see the module docstring); the
    evaluation itself touches no disk, JobStore, or crisp state.  The
    check order mirrors the executor so a correct decision here is the
    decision the executor would make (ADR 0017): submitted first, then
    converged, then reason / CONTCAR / budget, then metadata.
    """
    if latest_state == "submitted":
        # In flight — the wave loop and crisp own the outcome; retry policy
        # waits rather than re-evaluating a stale on-disk verdict.
        return Decision(
            disposition="wait",
            explanation="calculation is already submitted; retry policy waits",
        )
    if verdict_converged:
        # Converged is authoritative even when the reason string is also a
        # retryable one (``nsw_early_exit`` marks a successful early exit).
        return Decision(
            disposition="none",
            explanation="calculation converged; no retry",
        )
    if verdict_reason is None:
        return _manual("no convergence-verdict reason; conservative manual")
    if verdict_reason == REASON_ELECTRONIC_NOT_CONV:
        # NELM exhaustion is deterministic: identical inputs reproduce the
        # failure, so a blind retry has no rescue rate (ADR 0017).  The
        # fix is a parameter decision, not more iterations.
        return _manual(
            "electronic convergence failure (NELM): identical rerun cannot "
            "cure it; parameter decision required"
        )
    if verdict_reason not in RETRYABLE_REASONS:
        return _manual(
            f"verdict reason {verdict_reason!r} is not auto-retryable; "
            "manual decision required"
        )
    if not has_conticar:
        # Nothing to continue from — a restart would resubmit stale inputs.
        return _manual("no CONTCAR to continue from; manual decision required")
    truncated = verdict_reason == REASON_TRUNCATED
    if not truncated and ionic_restarts >= CPD_MAX_IONIC_RESTARTS:
        # Force-stall cap (ADR 0017): every round burns NSW steps with no
        # progress.  Past the budget the phase needs a parameter decision —
        # ZBRENT evidence does not override the cap (metadata only).
        return _manual(
            "auto-restart budget exhausted (3 ionic restarts without "
            "convergence); parameter decision required"
        )
    if truncated:
        explanation = (
            "truncated run: continue from CONTCAR on long-QOS cluster "
            "(budget-exempt)"
        )
        tags: tuple[str, ...] = (LONG_TAG,)
    else:
        explanation = (
            f"unconverged ({verdict_reason}): continue from CONTCAR"
        )
        tags = ()
    if has_zbrent:
        explanation += f" (ZBRENT: EDIFF={ZBRENT_EDIF})"
    return Decision(
        disposition="automatic",
        explanation=explanation,
        submission_source=IONIC_RESTART_SOURCE,
        continue_from_contcar=True,
        incar_adjustment=(
            MappingProxyType({"EDIFF": ZBRENT_EDIF}) if has_zbrent else None
        ),
        tags=tags,
    )


# JobStore latest states that make a defect restart-eligible: the dir has
# run once and its outcome is not settled (wave2's defect branch drives on
# exactly this set — independent of the verdict reason).
_RESTART_ELIGIBLE_STATES: Final[frozenset[str]] = frozenset({
    "failed",
    "unconverged",
    "pending",
})


def evaluate_defect(
    *,
    latest_state: str | None,
    verdict_converged: bool,
    verdict_reason: str | None,
    has_conticar: bool,
    has_zbrent: bool = False,
) -> Decision:
    """Evaluate the retry decision for one defect directory.

    Defect retries are *state-driven and reason-blind* (mirrors wave2's
    defect branch): any restart-eligible latest state — ``failed``,
    ``unconverged``, ``pending`` — or a stale ``converged`` record whose
    disk verdict is unconverged (ADR 0016) auto-restarts every cycle, and
    *no* verdict reason (not even ``electronic_not_conv``, not even an
    unknown one) demotes it to ``manual``.  The submission shape is
    CONTCAR-driven: an own CONTCAR → continuation, none → fresh submission.
    ZBRENT evidence is decision metadata only (the same EDIFF downgrade as
    CPD).

    ``verdict_reason`` is accepted for the uniform evidence seam but is
    deliberately not consulted here; a never-run dir (no latest state) is
    also out of this module's scope — its first submission and
    charge-state-chain seeding are executor decisions, so it resolves
    conservatively to ``manual``.
    """
    if latest_state == "submitted":
        # In flight — crisp owns the outcome; retry policy waits rather
        # than re-evaluating a stale on-disk verdict.
        return Decision(
            disposition="wait",
            explanation="calculation is already submitted; retry policy waits",
        )
    if verdict_converged:
        # A converged disk verdict is authoritative even over a stale
        # JobStore record or a retry-eligible latest state.
        return Decision(
            disposition="none",
            explanation="calculation converged; no retry",
        )
    stale_converged = latest_state == "converged"
    if latest_state not in _RESTART_ELIGIBLE_STATES and not stale_converged:
        return _manual(
            f"defect latest state {latest_state!r} is not restart-eligible; "
            "conservative manual"
        )
    if has_conticar:
        explanation = (
            f"defect restart-eligible ({latest_state}): continue from CONTCAR"
            if not stale_converged
            else "defect stale 'converged' record (ADR 0016): continue from "
            "CONTCAR"
        )
        continue_from_contcar = True
    else:
        explanation = (
            f"defect restart-eligible ({latest_state}): fresh submission "
            "(no CONTCAR)"
            if not stale_converged
            else "defect stale 'converged' record (ADR 0016): fresh "
            "submission (no CONTCAR)"
        )
        continue_from_contcar = False
    if has_zbrent:
        explanation += f" (ZBRENT: EDIFF={ZBRENT_EDIF})"
    return Decision(
        disposition="automatic",
        explanation=explanation,
        submission_source=DEFECT_RESTART_SOURCE,
        continue_from_contcar=continue_from_contcar,
        incar_adjustment=(
            MappingProxyType({"EDIFF": ZBRENT_EDIF}) if has_zbrent else None
        ),
    )


def evaluate_competing_one_shot(
    *,
    latest_state: str | None,
    verdict_converged: bool,
    verdict_reason: str | None,
    already_auto_retried: bool,
    retry_failed_armed: bool,
) -> Decision:
    """Evaluate the COMPETING one-shot auto-rerun decision (ADR 0007).

    ``batch run --retry-failed`` is a *human-armed* signal that resubmits a
    failed/unconverged competing phase exactly once, recorded with source
    ``auto_retry``; a second failure is terminal forever — the spent
    ``auto_retry`` history entry blocks every later cycle even with
    ``--retry-failed`` re-passed (the executor's already-spent check is
    unconditional), so recovery is a manual operator decision outside the
    pipeline.  Deterministic electronic NELM exhaustion is excluded (ADR
    0017): an identical rerun reproduces it, so the one-shot must not be
    burned on a parameter problem.  The check order mirrors the COMPETING
    branch of the executor: submitted, converged, state, arm,
    already-spent, reason.
    """
    if latest_state == "submitted":
        return Decision(
            disposition="wait",
            explanation="calculation is already submitted; retry policy waits",
        )
    if verdict_converged:
        return Decision(
            disposition="none",
            explanation="calculation converged; no retry",
        )
    if latest_state not in ("failed", "unconverged"):
        return _manual(
            f"COMPETING one-shot applies only to failed/unconverged states, "
            f"not {latest_state!r}; conservative manual"
        )
    if not retry_failed_armed:
        return _manual(
            "COMPETING one-shot auto_retry not armed (--retry-failed); "
            "operator decision required"
        )
    if already_auto_retried:
        return _manual(
            "COMPETING one-shot auto_retry already spent: second failure is "
            "terminal; manual parameter/operator decision required"
        )
    if verdict_reason == REASON_ELECTRONIC_NOT_CONV:
        return _manual(
            "electronic convergence failure (NELM): excluded from one-shot "
            "auto_retry; parameter decision required"
        )
    return Decision(
        disposition="automatic",
        explanation=(
            "COMPETING one-shot: resubmit once (auto_retry armed by "
            "--retry-failed)"
        ),
        submission_source=COMPETING_RETRY_SOURCE,
    )


__all__ = [
    "RETRYABLE_REASONS",
    "CPD_MAX_IONIC_RESTARTS",
    "UNCONVERGED_MAX_RESTARTS",
    "IONIC_RESTART_SOURCE",
    "DEFECT_RESTART_SOURCE",
    "COMPETING_RETRY_SOURCE",
    "LONG_TAG",
    "ZBRENT_EDIF",
    "has_zbrent_failure",
    "Disposition",
    "Decision",
    "evaluate_cpd",
    "evaluate_defect",
    "evaluate_competing_one_shot",
]
