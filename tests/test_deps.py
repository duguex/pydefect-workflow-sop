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
