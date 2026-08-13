"""CPD-phase ionic auto-restart (wave2, any phase).

Phase inference gates COMPLETE on every cpd phase, but once a system
left COMPETING nothing else ever resubmitted them — a phase that failed
ionically (force gate / NSW exhausted) dead-locked the system in
UNITCELL_DEFECT forever.  wave2 now continues such phases from their own
CONTCAR every cycle.  Electronic NELM exhaustion is deliberately NOT
auto-retried: identical inputs reproduce the same failure.
"""

from pathlib import Path

import pytest

from vasp_sop.vasp.convergence import (
    REASON_ELECTRONIC_NOT_CONV,
    REASON_FORCE_GATE_FAIL,
    REASON_MISSING_OUTCAR,
    REASON_NSW_EXHAUSTED,
    REASON_TRUNCATED,
    ConvergenceVerdict,
)


class FakeJobStore:
    def __init__(self) -> None:
        self.hist: dict[str, list[dict]] = {}
        self.tracked: list[str] = []

    def latest(self, cp: str) -> str | None:
        recs = self.hist.get(cp, [])
        return recs[-1]["status"] if recs else None

    def history(self, cp: str) -> list[dict]:
        return self.hist.get(cp, [])

    def record(self, cp: str, status: str, **kw) -> None:
        self.hist.setdefault(cp, []).append({"status": status, **kw})

    def track(self, cp: str) -> None:
        self.tracked.append(cp)


class FakeSystem:
    def __init__(self, cpd_root: Path) -> None:
        self.name = "TestSys"
        self.target_dir = None
        self.cpd_dir = cpd_root
        self.uc_dir = Path("/nonexistent/uc")
        self.defect_dir = Path("/nonexistent/defect")
        self.config = type("C", (), {"stage2_soc": False})()

    def derive_phase(self, js) -> str:
        return "UNITCELL_DEFECT"

    def competing_dirs(self, js) -> list[Path]:
        return []

    def phase(self) -> str:
        return "UNITCELL_DEFECT"

    @property
    def is_chemical_environment(self) -> bool:
        return False


def _call_wave2(system, js, monkeypatch, *, retry_failed: bool = False):
    from vasp_sop.core import orchestrator

    submitted = []
    monkeypatch.setattr(
        "vasp_sop.core.jobs.submit_vasp",
        lambda path, priority=0, tags=None: type("Job", (), {"task_name": "t-1234"})())
    monkeypatch.setattr(
        "vasp_sop.vasp.io.input_ready", lambda d: True, raising=False)
    monkeypatch.setattr(
        "vasp_sop.vasp.io.restart_from_contcar", lambda d: None, raising=False)
    original = orchestrator._submit_or_skip

    def _patched(path, label, sys_name, dry_run, info, *, js=None,
                 source=None, priority=0, tags=None):
        submitted.append((str(path), source, tags))
        return original(path, label, sys_name, dry_run, info, js=js,
                        source=source, priority=priority, tags=tags)

    monkeypatch.setattr(orchestrator, "_submit_or_skip", _patched)
    orchestrator.wave2_submit(system, js, False, retry_failed=retry_failed)
    return submitted


def test_force_gate_fail_restarts_from_contcar(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_FORCE_GATE_FAIL))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert len(submitted) == 1, submitted
    assert submitted[0][1] == "ionic_restart", submitted
    assert js.latest(str(d)) == "submitted"


def test_nsw_exhausted_restarts(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_NSW_EXHAUSTED))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert len(submitted) == 1, submitted
    assert submitted[0][1] == "ionic_restart", submitted


def test_electronic_not_conv_not_retried(tmp_path: Path, monkeypatch):
    """NELM exhaustion needs parameter work, not blind restarts."""
    d = tmp_path / "cpd" / "Al13Fe4_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_ELECTRONIC_NOT_CONV))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert submitted == [], submitted


def test_converged_phase_not_retried(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(True, "force_gate"))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert submitted == [], submitted


def test_missing_outcar_not_retried(tmp_path: Path, monkeypatch):
    """No OUTCAR at all: nothing to continue from, not a restart case."""
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_MISSING_OUTCAR))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert submitted == [], submitted


def test_no_contcar_not_retried(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    js = FakeJobStore()
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_FORCE_GATE_FAIL))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert submitted == [], submitted


def test_restart_cap_stops_blind_resubmits(tmp_path: Path, monkeypatch):
    """A phase whose ionic restarts never converge must stop being
    resubmitted (stalled force at a too-strict EDIFFG would otherwise
    burn core-hours every cycle forever)."""
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    from vasp_sop.core.retry_policy import CPD_MAX_IONIC_RESTARTS
    for _ in range(CPD_MAX_IONIC_RESTARTS):
        js.record(str(d), "unconverged", source="ionic_restart")
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_FORCE_GATE_FAIL))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert submitted == [], submitted


def test_restart_under_cap_still_submits(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    js.record(str(d), "unconverged", source="ionic_restart")
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_FORCE_GATE_FAIL))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert len(submitted) == 1, submitted
    assert submitted[0][1] == "ionic_restart", submitted


def test_already_submitted_not_retried(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    js.record(str(d), "submitted")
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_FORCE_GATE_FAIL))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert submitted == [], submitted


def test_truncated_restarts_with_long_tag(tmp_path: Path, monkeypatch):
    """A TIME-LIMIT truncation is transient — the CONTCAR advanced, so it
    continues from CONTCAR on a long-QOS cluster (not capped)."""
    d = tmp_path / "cpd" / "SrFeO2_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_TRUNCATED))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert len(submitted) == 1, submitted
    assert submitted[0][1] == "ionic_restart", submitted
    assert submitted[0][2] == ["long"], submitted


def test_truncated_exempt_from_restart_cap(tmp_path: Path, monkeypatch):
    """Truncated restarts keep advancing the CONTCAR — the force-stall cap
    must not stop them, even past CPD_MAX_IONIC_RESTARTS."""
    d = tmp_path / "cpd" / "SrFeO2_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "CONTCAR").write_text("contcar\n")
    js = FakeJobStore()
    from vasp_sop.core.retry_policy import CPD_MAX_IONIC_RESTARTS
    for _ in range(CPD_MAX_IONIC_RESTARTS + 2):
        js.record(str(d), "unconverged", source="ionic_restart")
    sys = FakeSystem(tmp_path / "cpd")

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_TRUNCATED))
    submitted = _call_wave2(sys, js, monkeypatch)
    assert len(submitted) == 1, submitted
    assert submitted[0][2] == ["long"], submitted


def test_drift_warning_once(tmp_path: Path, caplog):
    """INCAR newer than OUTCAR warns once per dir (advisory, no rerun)."""
    import logging
    from vasp_sop.core import orchestrator
    orchestrator._drift_warned.clear()

    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "OUTCAR").write_text("x\n")
    import os, time
    old = time.time() - 1000
    os.utime((d / "OUTCAR"), (old, old))  # OUTCAR older than INCAR

    with caplog.at_level(logging.WARNING, logger="vasp_sop.core.orchestrator"):
        orchestrator._warn_incar_drift(d, "TestSys/cpd/FeO_mp-1")
        orchestrator._warn_incar_drift(d, "TestSys/cpd/FeO_mp-1")
    warns = [r.message for r in caplog.records if "newer than OUTCAR" in r.message]
    assert len(warns) == 1, warns


def test_no_warning_when_incar_older(tmp_path: Path, caplog):
    import logging
    from vasp_sop.core import orchestrator
    orchestrator._drift_warned.clear()

    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    (d / "OUTCAR").write_text("x\n")
    with caplog.at_level(logging.WARNING, logger="vasp_sop.core.orchestrator"):
        orchestrator._warn_incar_drift(d, "TestSys/cpd/FeO_mp-1")
    warns = [r.message for r in caplog.records if "newer than OUTCAR" in r.message]
    assert warns == [], warns


def test_zbrent_dir_gets_ediff_1e6(tmp_path: Path):
    """A cpd whose last run died in ZBRENT is resubmitted with EDIFF=1e-6
    (operator decision 2026-08-11, issue #119) instead of looping on
    EDIFF=1e-4 forever."""
    from vasp_sop.core import orchestrator

    d = tmp_path / "cpd" / "Sr_mp-139"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("EDIFF = 1e-4\n")
    (d / "OUTCAR").write_text(
        "something\n---  I REFUSE TO CONTINUE WITH THIS SICK JOB ---\n"
        "ZBRENT: fatal error in bracketing\n")
    (d / "CONTCAR").write_text("x\n")
    from vasp_sop.core import retry_policy
    assert retry_policy.has_zbrent_failure(d) is True
    from vasp_sop.vasp.io import patch_incar
    patch_incar(d, EDIFF="1e-6")
    assert "EDIFF = 1e-6" in (d / "INCAR").read_text()


def test_no_zbrent_no_downgrade(tmp_path: Path):
    from vasp_sop.core import orchestrator

    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "OUTCAR").write_text("reached required accuracy\n")
    from vasp_sop.core import retry_policy
    assert retry_policy.has_zbrent_failure(d) is False
