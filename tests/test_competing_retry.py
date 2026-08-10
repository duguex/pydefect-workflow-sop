"""COMPETING-phase terminal retry policy (ADR 0007, same as defect dirs).

A failed/unconverged cpd dir gets exactly one machine resubmit marked
``auto_retry``; a second failure is terminal forever and only an explicit
``batch run --retry-failed`` arms it again.  This stops the per-cycle
resubmit loop that previously re-submitted ZBRENT-crashed cpd dirs every
poll (observed: 56 retries of one FeO phase).
"""

from pathlib import Path


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
    """Minimal System surface used by wave2_submit's COMPETING branch."""

    def __init__(self, dirs: list[Path]) -> None:
        self.name = "TestSys"
        self.target_dir = None
        self.cpd_dir = Path("/nonexistent/cpd")
        self.uc_dir = Path("/nonexistent/uc")
        self.defect_dir = Path("/nonexistent/defect")
        self.config = type("C", (), {"stage2_soc": False})()
        self._dirs = dirs

    def derive_phase(self, js) -> str:
        return "COMPETING"

    def competing_dirs(self, js) -> list[Path]:
        return self._dirs

    def phase(self) -> str:
        return "COMPETING"

    @property
    def is_chemical_environment(self) -> bool:
        return False


def _call_wave2(system, js, monkeypatch, *, retry_failed: bool = False):
    from vasp_sop.core import orchestrator
    submitted = []
    monkeypatch.setattr(
        "vasp_sop.core.jobs.submit_vasp",
        lambda path, priority=0: type(
            "Job", (), {"task_name": "t-1234"})())
    original = orchestrator._submit_or_skip

    def _patched(path, label, sys_name, dry_run, info, *, js=None,
                 source=None, priority=0):
        submitted.append((str(path), source))
        return original(path, label, sys_name, dry_run, info, js=js,
                        source=source, priority=priority)

    monkeypatch.setattr(orchestrator, "_submit_or_skip", _patched)
    orchestrator.wave2_submit(system, js, False, retry_failed=retry_failed)
    return submitted


def test_failed_cpd_resubmitted_once(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    js = FakeJobStore()
    js.record(str(d), "failed", reason="vasp_crash")
    sys = FakeSystem([d])
    submitted = _call_wave2(sys, js, monkeypatch, retry_failed=True)
    assert [s[1] for s in submitted] == ["auto_retry"], submitted
    assert js.latest(str(d)) == "submitted"


def test_second_failure_is_terminal(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    js = FakeJobStore()
    js.record(str(d), "failed", reason="vasp_crash")
    js.record(str(d), "submitted", source="auto_retry")
    js.record(str(d), "failed", reason="vasp_crash")
    sys = FakeSystem([d])
    submitted = _call_wave2(sys, js, monkeypatch, retry_failed=True)
    assert submitted == [], "second failure must not auto-resubmit"


def test_failed_waits_without_retry_failed(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    js = FakeJobStore()
    js.record(str(d), "failed", reason="vasp_crash")
    sys = FakeSystem([d])
    submitted = _call_wave2(sys, js, monkeypatch)
    assert submitted == [], "loop without --retry-failed must not resubmit"


def test_fresh_cpd_submits_normally(tmp_path: Path, monkeypatch):
    d = tmp_path / "cpd" / "Ga2O3_mp-2"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    js = FakeJobStore()
    sys = FakeSystem([d])
    submitted = _call_wave2(sys, js, monkeypatch)
    assert [s[1] for s in submitted] == [None], submitted
    assert js.latest(str(d)) == "submitted"


def test_electronic_not_conv_excluded_from_auto_retry(
    tmp_path: Path, monkeypatch
):
    """A deterministic NELM exhaustion reproduces with identical inputs —
    auto_retry must not burn a rerun on it (needs a parameter decision)."""
    from vasp_sop.vasp.convergence import (
        REASON_ELECTRONIC_NOT_CONV,
        ConvergenceVerdict,
    )
    d = tmp_path / "cpd" / "Al13Fe4_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    js = FakeJobStore()
    js.record(str(d), "failed", reason="vasp_crash")
    sys = FakeSystem([d])

    import vasp_sop.vasp.convergence as conv_mod
    monkeypatch.setattr(
        conv_mod, "convergence_verdict",
        lambda d, task_type="": ConvergenceVerdict(
            False, REASON_ELECTRONIC_NOT_CONV))
    submitted = _call_wave2(sys, js, monkeypatch, retry_failed=True)
    assert submitted == [], submitted


def test_transient_failure_still_auto_retried(tmp_path: Path, monkeypatch):
    """A vasp_crash (no electronic evidence on disk) keeps the one-shot
    auto_retry — the crash may be transient (node/limits)."""
    d = tmp_path / "cpd" / "FeO_mp-1"
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 50\n")
    js = FakeJobStore()
    js.record(str(d), "failed", reason="vasp_crash")
    sys = FakeSystem([d])
    submitted = _call_wave2(sys, js, monkeypatch, retry_failed=True)
    assert [s[1] for s in submitted] == ["auto_retry"], submitted
