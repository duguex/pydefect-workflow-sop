"""Wave2 defect restart branch: ZBRENT dirs re-run with EDIFF=1e-6.

The cpd ionic-restart branch gained the ZBRENT downgrade first (issue
#119); defect restarts initially missed it, so a metallic defect dir
looped on EDIFF=1e-4 forever.  Regression coverage for the defect
branch lives here.
"""

from pathlib import Path


class _JS:
    def __init__(self) -> None:
        self.hist: dict[str, list[dict]] = {}

    def latest(self, cp: str) -> str | None:
        recs = self.hist.get(cp, [])
        return recs[-1]["status"] if recs else None

    def history(self, cp: str) -> list[dict]:
        return self.hist.get(cp, [])

    def record(self, cp: str, status: str, **kw) -> None:
        self.hist.setdefault(cp, []).append({"status": status, **kw})


class _Sys:
    name = "TestSys"
    cpd_dir = Path("/nonexistent/cpd")
    uc_dir = Path("/nonexistent/uc")
    target_dir = None
    is_chemical_environment = False

    def __init__(self, defect_dir: Path) -> None:
        self.defect_dir = defect_dir
        self.config = type("C", (), {"stage2_soc": False})()

    def derive_phase(self, js) -> str:
        return "UNITCELL_DEFECT"

    def phase(self) -> str:
        return "UNITCELL_DEFECT"


def _call_wave2(sys, js, monkeypatch) -> list[tuple[str, str | None]]:
    from vasp_sop.core import orchestrator

    submitted: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "vasp_sop.core.jobs.submit_vasp",
        lambda path, priority=0, tags=None: type(
            "Job", (), {"task_name": "t-1234"})(),
    )
    original = orchestrator._submit_or_skip

    def _patched(path, label, sys_name, dry_run, info, *, js=None,
                 source=None, priority=0):
        submitted.append((str(path), source))
        return original(path, label, sys_name, dry_run, info, js=js,
                        source=source, priority=priority)

    monkeypatch.setattr(orchestrator, "_submit_or_skip", _patched)
    orchestrator.wave2_submit(sys, js, False)
    return submitted


def test_defect_zbrent_restart_patches_ediff(tmp_path: Path, monkeypatch):
    """A defect whose last run died in ZBRENT is restarted with
    EDIFF=1e-6 (mirror of the cpd policy, issue #119)."""
    d = tmp_path / "defect" / "Va_O1_0"
    d.mkdir(parents=True)
    for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
        (d / f).write_text("x\n")
    (d / "INCAR").write_text("EDIFF = 1e-4\nNSW = 100\n")
    (d / "OUTCAR").write_text(
        "---  I REFUSE TO CONTINUE WITH THIS SICK JOB ---\n"
        "ZBRENT: fatal error in bracketing\n")
    (d / "CONTCAR").write_text("x\n")

    js = _JS()
    js.record(str(d), "failed", reason="vasp_crash")
    submitted = _call_wave2(_Sys(tmp_path / "defect"), js, monkeypatch)

    assert [s[1] for s in submitted] == ["restart"], submitted
    incar = (d / "INCAR").read_text()
    assert "EDIFF = 1e-6" in incar, incar


def test_defect_non_zbrent_restart_keeps_ediff(tmp_path: Path, monkeypatch):
    """A normal (non-ZBRENT) defect restart keeps the protocol EDIFF."""
    d = tmp_path / "defect" / "Va_O1_1"
    d.mkdir(parents=True)
    for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
        (d / f).write_text("x\n")
    (d / "INCAR").write_text("EDIFF = 1e-4\nNSW = 100\nIBRION = 2\nEDIFFG = -0.01\n")
    (d / "OUTCAR").write_text(
        "NSW = 100\nIBRION = 2\nEDIFFG = -0.01\n"
        "TOTAL-FORCE (eV/Angst)\n ---\n"
        " 0.5 0.5 0.5 0.2 0.2 0.2\n"
        " General timing and accounting informations for this job:\n")
    (d / "CONTCAR").write_text("x\n")

    js = _JS()
    js.record(str(d), "failed", reason="vasp_crash")
    submitted = _call_wave2(_Sys(tmp_path / "defect"), js, monkeypatch)

    assert [s[1] for s in submitted] == ["restart"], submitted
    assert "EDIFF = 1e-6" not in (d / "INCAR").read_text()


class TestConcurrencyGuard:
    def test_submit_or_skip_skips_crisp_live_dir(self, tmp_path, monkeypatch):
        """A dir crisp already has a live job for must not get a second
        submission (same-dir concurrent VASP corrupts OUTCAR/vasprun)."""
        from vasp_sop.core import orchestrator

        d = tmp_path / "defect" / "Va_O1_2"
        d.mkdir(parents=True)
        calls = []
        monkeypatch.setattr(
            "vasp_sop.core.jobs.submit_vasp",
            lambda *a, **k: calls.append(a) or type("Job", (), {"task_name": "t"})(),
        )
        monkeypatch.setattr(
            "vasp_sop.core.jobs.crisp_active_dirs",
            lambda skip=False: {str(d.resolve())},
        )
        js = _JS()
        js.record(str(d), "failed")

        from vasp_sop.core.orchestrator import _submit_or_skip

        _submit_or_skip(d, "df-x", "S", False, lambda *a: None, js=js)
        assert calls == []  # no submission attempted


class TestPollSinglePoint:
    def test_poll_does_not_crash_single_point(self, tmp_path, monkeypatch):
        """ADR 0014 soc2 single points (no ionic timing block) must not be
        untracked as vasp_crash by the poll path."""
        from vasp_sop.core import orchestrator
        from vasp_sop.core.orchestrator import BatchOrchestrator

        d = tmp_path / "calc"
        d.mkdir()
        (d / "OUTCAR").write_text(
            "NSW = 0\nLSORBIT = .TRUE.\n"
            "DAV: 10 -0.8E+03 0.1E-06\n"
            " reached required accuracy - stopping structural energy minimisation\n"
        )
        (d / "INCAR").write_text("NSW = 0\nLSORBIT = .TRUE.\n")

        recs: list[tuple[str, str]] = []
        class _JS:
            def tracked_dirs(self):
                return [{"dir_path": str(d.resolve()), "submitted_at": 0.0}]
            def untrack(self, p): recs.append(("untrack", p))
            def record(self, p, st, **kw): recs.append((st, p))
        js = _JS()

        monkeypatch.setattr(
            "vasp_sop.core.jobs.crisp_active_dirs", lambda skip=False: set())
        orch = BatchOrchestrator.__new__(BatchOrchestrator)
        orch.js = js
        orch.dry_run = False
        n = orch._poll_tracked()
        assert n == 0
        assert not any(r[0] == "failed" for r in recs), recs
