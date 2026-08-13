"""Runtime dependency audit: gates, lineage, blocking roots."""

from pathlib import Path

import pytest

from vasp_sop.report import deps

_INPUTS = ("INCAR", "POSCAR", "POTCAR", "KPOINTS")


def _write_inputs(directory: Path) -> None:
    for name in _INPUTS:
        (directory / name).write_text("x\n")


def _make_system(tmp_path: Path) -> Path:
    """One target, two competing phases, and a three-charge defect chain."""
    system = tmp_path / "TestSys"
    so = system / "unitcell" / "structure_opt"
    so.mkdir(parents=True)
    _write_inputs(so)
    cpd = system / "cpd"
    cpd.mkdir()
    target = cpd / "TestO3_mp-1"
    target.mkdir()
    _write_inputs(target)
    for phase in ("FeO_mp-1", "Fe2O3_mp-2"):
        directory = cpd / phase
        directory.mkdir()
        _write_inputs(directory)
    (cpd / "target_vertices.yaml").write_text("vertices: []\n")
    (cpd / "standard_energies.yaml").write_text("energies: {}\n")
    defect = system / "defect"
    defect.mkdir()
    for name in ("Va_O1_0", "Va_O1_1", "Va_O1_2"):
        directory = defect / name
        directory.mkdir()
        _write_inputs(directory)
    (system / "plan.yaml").write_text(
        "project:\n  formula: TestO3\n  poscar_src: MP mp-1\n"
    )
    return system


def _statuses(status_map: dict[str, str]):
    def fake(path: Path, *, jobstore_fallback: bool = False):
        for suffix, status in status_map.items():
            if str(path).endswith(suffix):
                return status, "test"
        return "not-run", "test"

    return fake


@pytest.fixture
def graph(tmp_path, monkeypatch):
    _make_system(tmp_path)
    status_map = {
        "structure_opt": "converged",
        "TestO3_mp-1": "converged",
        "FeO_mp-1": "converged",
        "Fe2O3_mp-2": "not-run",
        # Charge 1 is the median root; it gates 0 and 2 first submissions.
        "Va_O1_0": "not-run",
        "Va_O1_1": "not-run",
        "Va_O1_2": "not-run",
    }
    monkeypatch.setattr(deps, "_dir_status", _statuses(status_map))
    return deps.build_graph(tmp_path)


def _nodes(graph: dict) -> dict[str, dict]:
    return {node["id"]: node for node in graph["nodes"]}


def test_systems_are_addressable_objects_and_groups(graph):
    nodes = _nodes(graph)
    assert graph["systems"] == [{"id": "sys:TestSys", "label": "TestSys"}]
    system = nodes["sys:TestSys"]
    labels = sorted(
        nodes[c]["label"]
        for c in system["children"]
        if nodes[c]["kind"] == "task-group"
    )
    assert labels == ["cpd (3)", "defects (1 chains)", "unitcell (1)"]


def test_seed_is_hard_gate_and_target_poscar_is_lineage(graph):
    nodes = _nodes(graph)
    root = "sys:TestSys:df:Va_O1:Va_O1_1"
    child = "sys:TestSys:df:Va_O1:Va_O1_0"
    assert root in nodes[child]["deps"]
    seed = next(
        edge
        for edge in graph["edges"]
        if edge["source"] == root
        and edge["target"] == child
        and edge["type"] == deps.RUNTIME_GATE
    )
    assert seed["hard"] is True
    lineage = next(
        edge
        for edge in graph["edges"]
        if edge["target"] == child
        and edge["type"] == deps.LINEAGE
        and edge["label"] == "defect input source"
    )
    assert lineage["source"] == "sys:TestSys:artifact:target-poscar"


def test_parallel_defects_have_no_false_structure_opt_gate(graph):
    nodes = _nodes(graph)
    structure_opt = "sys:TestSys:uc:structure_opt"
    cpd = nodes["sys:TestSys:cpd:FeO_mp-1"]
    defect = nodes["sys:TestSys:df:Va_O1:Va_O1_0"]
    assert structure_opt not in cpd["deps"]
    assert structure_opt not in defect["deps"]


def test_analysis_uses_real_gate_artifacts_not_all_cpd_phases(graph):
    nodes = _nodes(graph)
    analysis = next(node for node in graph["nodes"] if node["kind"] == "analysis-gate")
    assert "sys:TestSys:artifact:target_vertices" in analysis["deps"]
    assert "sys:TestSys:artifact:standard_energies" in analysis["deps"]
    assert "sys:TestSys:cpd:Fe2O3_mp-2" not in analysis["deps"]
    assert nodes["sys:TestSys:artifact:target_vertices"]["kind"] == "gate-artifact"


def test_blocking_roots_are_disposition_partitioned_and_transitive(graph):
    roots = graph["blocking_roots"]
    va_root = next(root for root in roots if root["label"] == "Va_O1_1")
    assert va_root["disposition"] == "automatic"
    # Both non-root charge states and their common analysis downstream count.
    assert va_root["affected"] >= 2
    assert roots == sorted(
        roots,
        key=lambda root: (
            {"manual": 0, "automatic": 1, "wait": 2, "none": 3}[root["disposition"]],
            -root["affected"],
            root["label"],
        ),
    )


def test_renderers_expose_roots_and_relation_edges(graph):
    tree = deps.render_tree(graph)
    assert "TestSys" in tree
    assert "blocking roots" in tree
    assert "Va_O1_1" in tree
    assert deps.render_mermaid(graph).startswith("graph TD")
    assert '"blocking_roots"' in deps.to_json(graph)


# ── shared retry-policy parity (t4) ───────────────────────────────────
# CPD and defect nodes must carry exactly the disposition AND explanation
# the unified retry-policy module produces for the same raw evidence —
# never a hand-rolled re-derivation.

def _make_retry_system(tmp_path: Path) -> Path:
    """System whose cpd/defect dirs exercise the shared retry-policy shapes."""
    system = tmp_path / "RetrySys"
    so = system / "unitcell" / "structure_opt"
    so.mkdir(parents=True)
    _write_inputs(so)
    cpd = system / "cpd"
    cpd.mkdir()
    for name in ("A_mp-1", "B_mp-2", "C_mp-3", "D_mp-4"):
        directory = cpd / name
        directory.mkdir()
        _write_inputs(directory)
    (cpd / "target_vertices.yaml").write_text("vertices: []\n")
    (cpd / "standard_energies.yaml").write_text("energies: {}\n")
    defect = system / "defect"
    defect.mkdir()
    for name in (
        "Va_O1_0",
        "Va_O1_1",
        "Va_O1_2",
        "Va_O2_0",
        "Va_O2_1",
        "Va_O2_2",
    ):
        directory = defect / name
        directory.mkdir()
        _write_inputs(directory)
    (system / "plan.yaml").write_text(
        "project:\n  formula: TestO3\n  poscar_src: MP mp-1\n"
    )
    return system


def _evidence(evidence_map: dict[str, dict]):
    """Fake raw-evidence collector keyed by path suffix.

    ``history`` is derived from ``latest_state`` so defect nodes with a
    record take the evaluator branch (never-run dirs wait for a seed).
    """
    defaults = {
        "latest_state": None,
        "verdict_converged": False,
        "verdict_reason": "missing_outcar",
        "ionic_restarts": 0,
        "has_conticar": False,
        "has_zbrent": False,
        "history": [],
    }

    def fake(path: Path) -> dict:
        for suffix, evidence in evidence_map.items():
            if str(path).endswith(suffix):
                ev = dict(defaults)
                ev.update(evidence)
                if ev["latest_state"] is not None and not ev["history"]:
                    ev["history"] = [
                        {"status": ev["latest_state"], "source": None,
                         "reason": None}
                    ]
                return ev
        return dict(defaults)

    return fake


@pytest.fixture
def retry_graph(tmp_path, monkeypatch):
    _make_retry_system(tmp_path)
    evidence = {
        # cpd: retryable reason but no CONTCAR to continue from → manual
        "A_mp-1": {"latest_state": "failed", "verdict_reason": "nsw_exhausted",
                   "has_conticar": False},
        # cpd: budget exhausted (3 restarts) WITH ZBRENT evidence — the
        # cap wins over ZBRENT metadata → manual
        "B_mp-2": {"latest_state": "failed", "verdict_reason": "nsw_exhausted",
                   "ionic_restarts": 3, "has_conticar": True, "has_zbrent": True},
        # cpd: transient truncation — budget-exempt automatic continuation
        "C_mp-3": {"latest_state": "unconverged", "verdict_reason": "truncated",
                   "has_conticar": True},
        # cpd: deterministic NELM exhaustion → manual
        "D_mp-4": {"latest_state": "failed",
                   "verdict_reason": "electronic_not_conv",
                   "has_conticar": True},
        # defect: restart-eligible failed, no own CONTCAR → fresh submission
        "Va_O1_0": {"latest_state": "failed", "verdict_reason": "nsw_exhausted",
                    "has_conticar": False},
        # defect: restart-eligible unconverged, own CONTCAR → continuation
        "Va_O1_1": {"latest_state": "unconverged",
                    "verdict_reason": "nsw_exhausted", "has_conticar": True},
        # defect: in flight → wait
        "Va_O1_2": {"latest_state": "submitted",
                    "verdict_reason": "nsw_exhausted", "has_conticar": True},
        # defect: stale JobStore converged record, disk verdict unconverged
        # (ADR 0016) → automatic continuation
        "Va_O2_0": {"latest_state": "converged",
                    "verdict_reason": "nsw_exhausted", "has_conticar": True},
        # defect: verdict converged → none
        "Va_O2_1": {"latest_state": "converged",
                    "verdict_converged": True, "verdict_reason": "nsw_early_exit",
                    "has_conticar": True},
        # defect: never-run non-root charge → waits for its seed, not a retry
        "Va_O2_2": {"latest_state": None},
    }
    status_map = {
        "A_mp-1": "failed", "B_mp-2": "failed", "C_mp-3": "failed",
        "D_mp-4": "failed",
        "Va_O1_0": "failed", "Va_O1_1": "failed", "Va_O1_2": "running",
        "Va_O2_0": "failed", "Va_O2_1": "converged", "Va_O2_2": "not-run",
    }
    monkeypatch.setattr(deps, "_dir_evidence", _evidence(evidence))
    monkeypatch.setattr(deps, "_dir_status", _statuses(status_map))
    return deps.build_graph(tmp_path)


def test_cpd_nodes_follow_shared_retry_decision(retry_graph):
    nodes = _nodes(retry_graph)
    expected = {
        "sys:RetrySys:cpd:A_mp-1": (
            "manual",
            "no CONTCAR to continue from; manual decision required",
        ),
        "sys:RetrySys:cpd:B_mp-2": (
            "manual",
            "auto-restart budget exhausted (3 ionic restarts without "
            "convergence); parameter decision required",
        ),
        "sys:RetrySys:cpd:C_mp-3": (
            "automatic",
            "truncated run: continue from CONTCAR on long-QOS cluster "
            "(budget-exempt)",
        ),
        "sys:RetrySys:cpd:D_mp-4": (
            "manual",
            "electronic convergence failure (NELM): identical rerun cannot "
            "cure it; parameter decision required",
        ),
    }
    for nid, (disposition, explanation) in expected.items():
        node = nodes[nid]
        assert node["disposition"] == disposition
        assert node["explanation"] == explanation


def test_defect_nodes_follow_shared_retry_decision(retry_graph):
    nodes = _nodes(retry_graph)
    expected = {
        "sys:RetrySys:df:Va_O1:Va_O1_0": (
            "automatic",
            "defect restart-eligible (failed): fresh submission (no CONTCAR)",
        ),
        "sys:RetrySys:df:Va_O1:Va_O1_1": (
            "automatic",
            "defect restart-eligible (unconverged): continue from CONTCAR",
        ),
        "sys:RetrySys:df:Va_O1:Va_O1_2": (
            "wait",
            "calculation is already submitted; retry policy waits",
        ),
        "sys:RetrySys:df:Va_O2:Va_O2_0": (
            "automatic",
            "defect stale 'converged' record (ADR 0016): continue from "
            "CONTCAR",
        ),
        "sys:RetrySys:df:Va_O2:Va_O2_1": (
            "none",
            "calculation converged; no retry",
        ),
    }
    for nid, (disposition, explanation) in expected.items():
        node = nodes[nid]
        assert node["disposition"] == disposition
        assert node["explanation"] == explanation
    # waiting-seed semantics survive: a never-run non-root charge is not a
    # retry decision, it waits for its root's seed (ADR 0010).
    waiting = nodes["sys:RetrySys:df:Va_O2:Va_O2_2"]
    assert waiting["status"] == "waiting-seed"
    assert waiting["disposition"] == "wait"


def test_dir_status_maps_jobstore_submitted_to_inflight(tmp_path, monkeypatch):
    """A JobStore ``submitted`` record must surface as in-flight, not fold
    to not-run (a stale disk verdict would otherwise mislabel it)."""
    directory = tmp_path / "Va_O1_2"
    directory.mkdir()
    _write_inputs(directory)
    monkeypatch.setattr(
        deps,
        "_dir_evidence",
        _evidence(
            {
                "Va_O1_2": {"latest_state": "submitted",
                            "verdict_reason": "nsw_exhausted"}
            }
        ),
    )
    monkeypatch.setattr(deps, "_crisp_status", lambda path: None)
    status, detail = deps._dir_status(directory)
    assert status == "running"
    assert detail == "jobstore:submitted (in flight)"


def test_dir_status_surfaces_stale_converged_record(tmp_path, monkeypatch):
    """A stale JobStore ``converged`` record with an unconverged disk
    verdict stays visible (status failed) instead of folding to not-run."""
    directory = tmp_path / "cpd"
    directory.mkdir()
    _write_inputs(directory)
    monkeypatch.setattr(
        deps,
        "_dir_evidence",
        _evidence(
            {
                "cpd": {"latest_state": "converged",
                        "verdict_reason": "nsw_exhausted"}
            }
        ),
    )
    monkeypatch.setattr(deps, "_crisp_status", lambda path: None)
    status, detail = deps._dir_status(directory)
    assert status == "failed"
    assert detail == "stale JobStore 'converged' record; disk verdict nsw_exhausted"


def test_graph_build_is_read_only(tmp_path):
    """Graph construction writes nothing — and takes its verdicts from fresh
    disk reads, not the persistent memo: even a conflicting pre-seeded
    cached verdict must not leak into the DAG."""
    from vasp_sop.core import paths as sop_paths
    from vasp_sop.vasp import convergence as conv

    sop_paths.override_cache_root(tmp_path / ".vasp_sop")
    try:
        conv._verdict_cache.clear()
        conv._verdict_loaded = False
        conv._verdict_dirty.clear()
        _make_retry_system(tmp_path)
        # Empty OUTCAR → truncated verdict, which is a persistent-cache site.
        outcar = tmp_path / "RetrySys" / "cpd" / "A_mp-1" / "OUTCAR"
        outcar.write_text("")
        # Seed a CONFLICTING cached verdict (converged) with the file's real
        # mtime: a cache-consulting evaluation would return converged.
        from vasp_sop.vasp.convergence import ConvergenceVerdict

        conv._verdict_cache[outcar] = {
            "": (outcar.stat().st_mtime, ConvergenceVerdict(True, "not_relaxation"))
        }
        graph = deps.build_graph(tmp_path)
        nodes = _nodes(graph)
        node = nodes["sys:RetrySys:cpd:A_mp-1"]
        # Fresh disk read wins: empty OUTCAR is truncated → status not-run,
        # no CONTCAR to continue from → manual.  Never converged.
        assert node["status"] == "not-run"
        assert node["disposition"] == "manual"
        assert conv._verdict_dirty == set()
        conv._flush_sidecar()
        assert not conv._sidecar_path().is_file()
    finally:
        sop_paths.override_cache_root(None)
