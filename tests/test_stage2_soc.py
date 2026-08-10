"""ADR 0014 two-phase SOC stage-2 supplement tests.

Stage 1 converges without LSORBIT; stage 2 adds it — Bi_* dirs continue
from CONTCAR, everything else gets an NSW=0 single point.  Arming is
one-shot (a ``soc_stage2`` record anywhere in history prevents re-arming).
"""

from pathlib import Path

from vasp_sop.core import orchestrator


class FakeJobStore:
    def __init__(self) -> None:
        self.hist: dict[str, list[dict]] = {}

    def latest(self, cp: str) -> str | None:
        recs = self.hist.get(cp, [])
        return recs[-1]["status"] if recs else None

    def history(self, cp: str) -> list[dict]:
        return self.hist.get(cp, [])

    def record(self, cp: str, status: str, **kw) -> None:
        self.hist.setdefault(cp, []).append({"status": status, **kw})

    def track(self, cp: str) -> None:
        pass


def _mkdir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "defect" / name
    d.mkdir(parents=True)
    (d / "INCAR").write_text("NSW = 100\nIBRION = 2\n")
    (d / "POSCAR").write_text(
        "title\n1.0\n10 0 0\n0 10 0\n0 0 10\nFe O\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n")
    (d / "CONTCAR").write_text(
        "title\n1.0\n10 0 0\n0 10 0\n0 0 10\nFe O\n1 1\nDirect\n0.1 0.1 0.1\n0.6 0.6 0.6\n")
    (d / "KPOINTS").write_text("k-points\n0\nGamma\n1 1 1\n")
    (d / "POTCAR").write_text("PAW_PBE Fe\nPAW_PBE O\n")
    return d


class TestStage2Pending:
    def test_converged_without_stage2_is_pending(self, tmp_path):
        d = _mkdir(tmp_path, "Va_O1_0")
        js = FakeJobStore()
        js.record(str(d), "converged")
        assert orchestrator._stage2_soc_pending(d, js)

    def test_converged_with_stage2_is_not_pending(self, tmp_path):
        d = _mkdir(tmp_path, "Va_O1_0")
        js = FakeJobStore()
        js.record(str(d), "converged")
        js.record(str(d), "submitted", source="soc_stage2")
        assert not orchestrator._stage2_soc_pending(d, js)

    def test_failed_stage2_is_not_pending(self, tmp_path):
        """One-shot arming: a failed supplement does not loop forever."""
        d = _mkdir(tmp_path, "Va_O1_0")
        js = FakeJobStore()
        js.record(str(d), "converged")
        js.record(str(d), "failed", source="soc_stage2")
        assert not orchestrator._stage2_soc_pending(d, js)

    def test_not_converged_is_not_pending(self, tmp_path):
        d = _mkdir(tmp_path, "Va_O1_0")
        js = FakeJobStore()
        js.record(str(d), "submitted")
        assert not orchestrator._stage2_soc_pending(d, js)


class TestSubmitStage2:
    def _call(self, d, monkeypatch):
        calls = []
        js = FakeJobStore()
        monkeypatch.setattr(
            orchestrator, "_submit_or_skip",
            lambda path, label, sys_name, dry_run, info, *, js=None,
            source=None, priority=0: calls.append((label, source)))
        orchestrator._submit_stage2_soc(d, type("S", (), {"name": "X"})(),
                                        js, False, lambda *a: None)
        return calls, js

    def test_non_bi_gets_nsw0_single_point(self, tmp_path, monkeypatch):
        d = _mkdir(tmp_path, "Va_O1_0")
        calls, js = self._call(d, monkeypatch)
        incar = (d / "INCAR").read_text()
        assert "LSORBIT" in incar and "ISYM = -1" in incar
        assert "NSW = 0" in incar
        assert calls == [("soc2:Va_O1_0", "soc_stage2")]

    def test_bi_dir_continues_from_contcar(self, tmp_path, monkeypatch):
        d = _mkdir(tmp_path, "Bi_Y1_0")
        self._call(d, monkeypatch)
        incar = (d / "INCAR").read_text()
        poscar = (d / "POSCAR").read_text()
        assert "LSORBIT" in incar
        assert "NSW = 0" not in incar, "Bi dirs continue, not single-point"
        assert "0.1 0.1 0.1" in poscar, "POSCAR must come from CONTCAR"


class TestWave2Stage2Trigger:
    def test_trigger_submits_converged_dirs(self, tmp_path, monkeypatch):
        df = tmp_path / "defect"
        conv = _mkdir(tmp_path, "Va_O1_0")
        (conv / "OUTCAR").write_text(
            "NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n"
            "TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.001 0.001 0.001 0.001 0.001 0.001\n"
            " General timing and accounting informations for this job:\n")
        (conv / "vasprun.xml").write_text("<vasprun/>\n")
        js = FakeJobStore()
        js.record(str(conv), "converged")
        sub = _mkdir(tmp_path, "Va_O2_0")
        js.record(str(sub), "submitted")
        submitted = []
        monkeypatch.setattr(
            orchestrator, "_submit_or_skip",
            lambda path, label, sys_name, dry_run, info, *, js=None,
            source=None, priority=0: submitted.append((path.name, source)))
        sys = type("S", (), {
            "name": "T", "config": type("C", (), {"stage2_soc": True})(),
            "target_dir": None, "is_chemical_environment": False,
            "cpd_dir": Path("/nonexistent/cpd"), "uc_dir": Path("/nonexistent/uc"),
            "defect_dir": df, "derive_phase": lambda self, js: "UNITCELL_DEFECT",
            "phase": lambda self: "UNITCELL_DEFECT",
        })()
        orchestrator.wave2_submit(sys, js, False)
        assert submitted == [("Va_O1_0", "soc_stage2")], submitted

    def test_disabled_config_no_trigger(self, tmp_path, monkeypatch):
        conv = _mkdir(tmp_path, "Va_O1_0")
        (conv / "OUTCAR").write_text(
            "NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n"
            "TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.001 0.001 0.001 0.001 0.001 0.001\n"
            " General timing and accounting informations for this job:\n")
        (conv / "vasprun.xml").write_text("<vasprun/>\n")
        js = FakeJobStore()
        js.record(str(conv), "converged")
        submitted = []
        monkeypatch.setattr(
            orchestrator, "_submit_or_skip",
            lambda path, label, sys_name, dry_run, info, *, js=None,
            source=None, priority=0: submitted.append((path.name, source)))
        sys = type("S", (), {
            "name": "T", "config": type("C", (), {"stage2_soc": False})(),
            "target_dir": None, "is_chemical_environment": False,
            "cpd_dir": Path("/nonexistent/cpd"), "uc_dir": Path("/nonexistent/uc"),
            "defect_dir": tmp_path / "defect",
            "derive_phase": lambda self, js: "UNITCELL_DEFECT",
            "phase": lambda self: "UNITCELL_DEFECT",
        })()
        orchestrator.wave2_submit(sys, js, False)
        assert submitted == [], "stage2 must be opt-in via plan"
