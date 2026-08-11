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
    (d / "INCAR").write_text("EDIFF = 1e-4\nNSW = 100\n")
    (d / "OUTCAR").write_text("reached required accuracy\n")
    (d / "CONTCAR").write_text("x\n")

    js = _JS()
    js.record(str(d), "failed", reason="vasp_crash")
    submitted = _call_wave2(_Sys(tmp_path / "defect"), js, monkeypatch)

    assert [s[1] for s in submitted] == ["restart"], submitted
    assert "EDIFF = 1e-6" not in (d / "INCAR").read_text()
