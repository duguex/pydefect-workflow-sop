"""Stale-converged reconciliation (ADR 0016 parity, issue #121).

``BatchOrchestrator._reconcile_stale_converged`` resets JobStore
``converged`` records whose disk verdict is unconverged (outputs cleared
outside the pipeline) to ``pending`` so the normal wave2/wave3 paths
resubmit them.  The defect leg is deliberately skipped — its advance path
self-heals stale records via CONTCAR restarts.
"""

import yaml

from vasp_sop.core.orchestrator import BatchOrchestrator
from vasp_sop.core.paths import override_cache_root


def _make_system(root, *, with_defect: bool = False):
    root.mkdir(parents=True)
    plan = {
        "project": {"formula": "NaCl", "poscar_src": "MP mp-1"},
        "parameters": {"functional": "pbesol"},
    }
    (root / "plan.yaml").write_text(yaml.dump(plan))
    cpd = root / "cpd"
    cpd.mkdir()
    for name in ("NaCl_mp-1", "Na_mp-2", "Cl_mp-3"):
        (cpd / name).mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (cpd / name / f).write_text("x")
    if with_defect:
        d = root / "defect"
        (d / "Va_Na_0").mkdir(parents=True)
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (d / "Va_Na_0" / f).write_text("x")


class TestReconcileStaleConverged:
    def _orch(self, tmp_path, *, with_defect=False):
        override_cache_root(tmp_path / ".vasp_sop")
        _make_system(tmp_path / "p", with_defect=with_defect)
        # BatchOrchestrator's root is the parent: systems are discovered
        # as subdirectories carrying plan.yaml (production layout).
        return BatchOrchestrator(tmp_path, dry_run=False)

    def _patch(self, monkeypatch, converged: bool,
               reason: str = "missing_outcar"):
        import vasp_sop.vasp.convergence as conv_mod
        import vasp_sop.vasp.io as io_mod
        from vasp_sop.vasp.convergence import ConvergenceVerdict

        monkeypatch.setattr(
            conv_mod, "convergence_verdict",
            lambda d, task_type="": ConvergenceVerdict(converged, reason),
            raising=False,
        )
        monkeypatch.setattr(
            io_mod, "input_ready", lambda d: True, raising=False)

    def test_cpd_stale_converged_reset_to_pending(self, tmp_path, monkeypatch):
        orch = self._orch(tmp_path)
        try:
            cp = str((tmp_path / "p" / "cpd" / "Na_mp-2").resolve())
            orch.js.record(cp, "converged")
            self._patch(monkeypatch, converged=False)
            assert orch._reconcile_stale_converged() == 1
            rec = orch.js.history(cp)[-1]
            assert rec["status"] == "pending"
            assert rec["source"] == "stale_converged_reconcile"
            assert rec["reason"] == "stale-converged:missing_outcar"
        finally:
            orch.js.close()

    def test_disk_converged_left_alone(self, tmp_path, monkeypatch):
        orch = self._orch(tmp_path)
        try:
            cp = str((tmp_path / "p" / "cpd" / "Na_mp-2").resolve())
            orch.js.record(cp, "converged")
            self._patch(monkeypatch, converged=True)
            assert orch._reconcile_stale_converged() == 0
            assert orch.js.latest(cp) == "converged"
        finally:
            orch.js.close()

    def test_no_converged_record_left_alone(self, tmp_path, monkeypatch):
        orch = self._orch(tmp_path)
        try:
            cp = str((tmp_path / "p" / "cpd" / "Na_mp-2").resolve())
            orch.js.record(cp, "submitted")
            self._patch(monkeypatch, converged=False)
            assert orch._reconcile_stale_converged() == 0
            assert orch.js.latest(cp) == "submitted"
        finally:
            orch.js.close()

    def test_defect_leg_skipped(self, tmp_path, monkeypatch):
        orch = self._orch(tmp_path, with_defect=True)
        try:
            cp = str((tmp_path / "p" / "defect" / "Va_Na_0").resolve())
            orch.js.record(cp, "converged")
            self._patch(monkeypatch, converged=False)
            assert orch._reconcile_stale_converged() == 0
            assert orch.js.latest(cp) == "converged"
        finally:
            orch.js.close()
