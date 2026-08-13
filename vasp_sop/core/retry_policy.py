"""Pure retry-policy seam (CPD core) — the single authority on retry
eligibility.

One calculation directory in, one structured
:class:`Decision` out.  Both the Wave 2 executor
(vasp_sop/core/orchestrator.py) and the read-only runtime dependency audit
(vasp_sop/report/deps.py) consume this module, so a policy change cannot
alter execution without altering the DAG view, or vice versa.

The evaluator is deterministic: it accepts *normalized evidence only* — the
convergence-verdict reason and converged flag, the latest calculation state,
the count of prior ``ionic_restart`` history entries, whether a CONTCAR to
continue from exists, and (from :func:`has_zbrent_failure`) whether the last
run died in a ZBRENT line-search abort.  Callers collect that evidence from
disk / JobStore / crisp; this module never reads JobStore, edits INCAR,
submits work, or decides charge-state-chain prerequisites.

CPD rules (ADR 0017, issue #119):

- a stalled relaxation (force gate / NSW exhausted / missing forces)
  continues from its own CONTCAR up to ``CPD_MAX_IONIC_RESTARTS`` times;
  past the cap it needs a parameter decision → ``manual``;
- transient truncation (TIME-LIMIT / killed run) is budget-exempt — the
  CONTCAR keeps advancing every round — and requests the ``long`` cluster
  tag;
- ZBRENT evidence is decision *metadata* (an EDIFF adjustment the executor
  applies before submission) and never overrides the restart budget;
- electronic NELM exhaustion stays ``manual``: an identical rerun reproduces
  the same failure, so blind retries only burn core-hours;
- a submitted calculation → ``wait``, a converged verdict → ``none``, and
  any other reason / missing CONTCAR resolves conservatively to ``manual``.

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


__all__ = [
    "RETRYABLE_REASONS",
    "CPD_MAX_IONIC_RESTARTS",
    "UNCONVERGED_MAX_RESTARTS",
    "IONIC_RESTART_SOURCE",
    "LONG_TAG",
    "ZBRENT_EDIF",
    "has_zbrent_failure",
    "Disposition",
    "Decision",
    "evaluate_cpd",
]
