# Automation boundary: what the machine will do unattended

> **Superseded by [ADR 0008](0008-automatic-retry-policy.md)** — the
> one-shot auto-rerun rule is replaced by a classified retry state machine
> (transient failures retry until success, persistent failures bounded at a
> configurable limit). Retained here: the human-gated mass-submission
> principle and `batch blockers` as the counting surface.

Asked whether the tool can "automatically handle" the unfinished systems of
the production testbed, we checked the blocker census and found 32/32
remaining systems blocked, with the dominant class being 346 terminal
defect directories (201 `force_gate_fail`, 145 crashed/truncated), plus
~100 directories missing inputs (no POTCAR) and zero self-awareness of any
of it. The tool was complete for *healthy* flow (proven end-to-end by
BaSe) but not for *unattended completion*.

The machine will now do, unattended:
- the healthy flow (submit → run → finalize → analyze → COMPLETE);
- reconcile stale records, and resubmit failed unit-cell tasks (existing);
- **auto-rerun**: resubmit *every* failed or unconverged defect directory —
  truncated/crashed are run fresh, unconverged restart from CONTCAR — but
  exactly **once**, marked `auto_retry` in the JobStore; a second failure
  is terminal forever (consistent with ADR 0004: the first failure is a
  transient, the second is the answer);
- **input restore**: replace missing POTCAR from the local PSP store,
  keyed by POSCAR species, so never-ran or input-stripped directories
  become runnable;
- `batch blockers` — enumerate every block reason, because automate
  nothing you cannot count.

Still human-gated: CPD competing-phase run/exclude after the persistent
gate, any retry beyond the one shot, exclusion decisions, and all *mass*
submission — the one-shot policy is armed only by an explicit
`batch run --retry-failed`, which re-queues every eligible terminal dir at
once (~346 jobs); a routine `batch run` without the flag stays terminal on
failed defect dirs. Mass submission happens only on that operator command,
never by default (the CsEuCl3 incident: 37 jobs submitted without a
decision).

The 201 `force_gate_fail` reruns are a deliberate compute-and-trust cost:
they were invoked knowing convergence is uncertain; the one-shot marker
bounds it.