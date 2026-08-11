"""Dependency-graph audit (batch deps): structure, edges, bottlenecks."""

import pathlib

import pytest

from vasp_sop.report import deps


def _make_system(tmp_path: pathlib.Path, name: str = "TestSys") -> pathlib.Path:
    """Minimal system: plan.yaml + unitcell + cpd + defect dirs."""
    sys_dir = tmp_path / name
    (sys_dir / "unitcell" / "structure_opt").mkdir(parents=True)
    (sys_dir / "cpd").mkdir()
    (sys_dir / "defect" / "perfect").mkdir(parents=True)
    for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
        (sys_dir / "unitcell" / "structure_opt" / f).write_text("x\n")
    (sys_dir / "plan.yaml").write_text(
        "project:\n  formula: TestO3\n  poscar_src: MP mp-1\n"
    )
    return sys_dir


def _dirs(status_map: dict[str, str]):
    """Fake _dir_status: path string -> status."""

    def fake(path: pathlib.Path, *, jobstore_fallback: bool = False):
        key = str(path)
        for prefix, st in status_map.items():
            if key.endswith(prefix):
                return st, "test"
        return "not-run", "test"

    return fake


@pytest.fixture
def graph(tmp_path, monkeypatch):
    sys_dir = _make_system(tmp_path)
    (sys_dir / "cpd" / "FeO_mp-1").mkdir()
    (sys_dir / "cpd" / "Fe2O3_mp-2").mkdir()
    for d in ("Va_O1_0", "Va_O1_1", "Va_O1_2"):
        (sys_dir / "defect" / d).mkdir()
    status_map = {
        "structure_opt": "converged",
        "FeO_mp-1": "converged",
        "Fe2O3_mp-2": "not-run",
        "Va_O1_0": "converged",   # root (median of 0/1/2)
        "Va_O1_1": "not-run",
        "Va_O1_2": "not-run",
    }
    monkeypatch.setattr(deps, "_dir_status", _dirs(status_map))
    g = deps.build_graph(tmp_path)
    return g, status_map


def test_system_node_and_groups(graph):
    g, _ = graph
    nodes = {n["id"]: n for n in g["nodes"]}
    sys_id = "sys:TestSys"
    assert sys_id in nodes
    children = nodes[sys_id]["children"]
    kinds = {nodes[c]["kind"] for c in children if c in nodes}
    assert "task-group" in kinds
    group_ids = [c for c in children
                 if nodes.get(c, {}).get("kind") == "task-group"]
    labels = sorted(nodes[c]["label"] for c in group_ids)
    assert labels == ["cpd (2)", "defects (1 chains)", "unitcell (1)"]


def test_seeding_edges(graph):
    g, _ = graph
    nodes = {n["id"]: n for n in g["nodes"]}
    # Va_O1 chain: root is 1 (median of [0,1,2]); 0 and 2 depend on it
    chain = next(n for n in g["nodes"] if n["kind"] == "defect-chain")
    by_label = {c["label"]: c for c in g["nodes"]
                if c["id"].startswith(chain["id"] + ":")}
    root_id = by_label["Va_O1_1"]["id"]
    assert root_id in by_label["Va_O1_0"]["deps"]
    assert root_id in by_label["Va_O1_2"]["deps"]
    # chain status: root converged, others waiting-seed -> not converged
    assert chain["n_ok"] == 1
    assert chain["n_total"] == 3


def test_bottleneck_scoring(graph):
    g, _ = graph
    # wave3 depends on all cpd + chains + uc: Fe2O3 not-run -> 1 block
    by_label = {n["label"]: n for n in g["nodes"]
                if n["label"] == "Fe2O3_mp-2"}
    assert by_label["Fe2O3_mp-2"]["bottleneck"] >= 1
    # bottlenecks list is sorted descending
    scores = [b["score"] for b in g["bottlenecks"]]
    assert scores == sorted(scores, reverse=True)


def test_wave3_gate_deps(graph):
    g, _ = graph
    w3 = next(n for n in g["nodes"] if n["kind"] == "wave3")
    # deps include the cpd dirs and the defect chain
    labels = {n["label"] for n in g["nodes"]
              if n["id"] in w3["deps"]}
    assert "FeO_mp-1" in labels and "Fe2O3_mp-2" in labels
    assert "Va_O1" in labels


def test_renderers(graph):
    g, _ = graph
    tree = deps.render_tree(g)
    assert "TestSys" in tree
    assert "bottlenecks" in tree
    mm = deps.render_mermaid(g)
    assert mm.startswith("graph TD")
    raw = deps.to_json(g)
    assert '"systems"' in raw
