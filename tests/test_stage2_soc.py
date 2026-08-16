"""ADR 0014 two-phase SOC stage-2 supplement tests.

Stage 1 converges without LSORBIT; stage 2 adds SOC and continues from
CONTCAR as a full structure relaxation for every dir (ADR 0022 — the
NSW=0 single-point regime was retired 2026-08-12).  Arming is one-shot
(a ``soc_stage2`` record anywhere in history prevents re-arming).
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
    def test_converged_without_final_protocol_is_pending(self, tmp_path):
        d = _mkdir(tmp_path, "Va_O1_0")
        js = FakeJobStore()
        js.record(str(d), "converged")
        cfg = type("C", (), {"soc": True})()
        assert orchestrator._stage2_pending(d, js, cfg)

    def test_converged_with_final_protocol_is_not_pending(self, tmp_path):
        """物理判定:INCAR 已含 LSORBIT + LDAU → 不再补(source 无关)。"""
        d = _mkdir(tmp_path, "Va_O1_0")
        (d / "INCAR").write_text("NSW = 100\nLSORBIT = .TRUE.\nLDAU = True\n")
        js = FakeJobStore()
        js.record(str(d), "converged")
        cfg = type("C", (), {"soc": True})()
        assert not orchestrator._stage2_pending(d, js, cfg)

    def test_failed_stage2_not_repending(self, tmp_path):
        """One-shot:stage2 提交已 patch INCAR——失败也不循环补。"""
        d = _mkdir(tmp_path, "Va_O1_0")
        (d / "INCAR").write_text("NSW = 100\nLSORBIT = .TRUE.\nLDAU = True\n")
        js = FakeJobStore()
        js.record(str(d), "converged")
        js.record(str(d), "failed", source="stage2")
        cfg = type("C", (), {"soc": True})()
        assert not orchestrator._stage2_pending(d, js, cfg)

    def test_not_converged_is_not_pending(self, tmp_path):
        d = _mkdir(tmp_path, "Va_O1_0")
        js = FakeJobStore()
        js.record(str(d), "submitted")
        cfg = type("C", (), {"soc": True})()
        assert not orchestrator._stage2_pending(d, js, cfg)


class TestSubmitStage2:
    def _call(self, d, monkeypatch, soc=True):
        calls = []
        js = FakeJobStore()
        monkeypatch.setattr(
            orchestrator, "_submit_or_skip",
            lambda path, label, sys_name, dry_run, info, *, js=None,
            source=None, priority=0: calls.append((label, source)))
        cfg = type("C", (), {"soc": soc})()
        sys = type("S", (), {"name": "X", "config": cfg})()
        orchestrator._submit_stage2(d, sys, js, False, lambda *a: None,
                                    task_type="defect")
        return calls, js

    def test_relaxes_under_final_protocol(self, tmp_path, monkeypatch):
        """ADR 0025: stage2 = SOC(体系需)+ U(含 Fe)一次 patch, CONTCAR
        续算弛豫(合并策略, grill Q6)。"""
        d = _mkdir(tmp_path, "Va_O1_0")
        calls, js = self._call(d, monkeypatch)
        incar = (d / "INCAR").read_text()
        poscar = (d / "POSCAR").read_text()
        assert "LSORBIT" in incar and "ISYM = -1" in incar
        assert "LDAU = True" in incar, "Fe 含 U 元素 → +U"
        assert "NSW = 100" in incar, "续算弛豫,非单点"
        assert "0.1 0.1 0.1" in poscar, "POSCAR must come from CONTCAR"
        assert calls == [("st2:Va_O1_0", "stage2")]

    def test_u_only_when_soc_disabled(self, tmp_path, monkeypatch):
        """soc=False:仅补 U(Fe),无 LSORBIT。"""
        d = _mkdir(tmp_path, "Va_O1_0")
        calls, js = self._call(d, monkeypatch, soc=False)
        incar = (d / "INCAR").read_text()
        assert "LDAU = True" in incar
        assert "LSORBIT" not in incar


class TestWave2Stage2Trigger:
    def _sys(self, df, cfg):
        return type("S", (), {
            "name": "T", "config": cfg,
            "target_dir": None, "is_chemical_environment": False,
            "cpd_dir": Path("/nonexistent/cpd"), "uc_dir": Path("/nonexistent/uc"),
            "defect_dir": df, "derive_phase": lambda self, js: "UNITCELL_DEFECT",
            "phase": lambda self: "UNITCELL_DEFECT",
        })()

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
        sys = self._sys(df, type("C", (), {"soc": True})())
        orchestrator.wave2_submit(sys, js, False)
        assert submitted == [("Va_O1_0", "stage2")], submitted

    def test_no_trigger_when_final_protocol_satisfied(self, tmp_path, monkeypatch):
        """INCAR 已含最终协议(单阶段/已补)→ 不重补。"""
        conv = _mkdir(tmp_path, "Va_O1_0")
        (conv / "INCAR").write_text("NSW = 100\nLSORBIT = .TRUE.\nLDAU = True\n")
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
        sys = self._sys(tmp_path / "defect", type("C", (), {"soc": True})())
        orchestrator.wave2_submit(sys, js, False)
        assert submitted == [], submitted
