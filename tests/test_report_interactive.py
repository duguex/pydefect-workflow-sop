"""Tests for vasp_sop.report.interactive — interactive formation-energy HTML."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from vasp_sop.report.interactive import (
    _bary_js,
    _build_defects,
    _defect_segments,
    _extract_vertex_data,
    _formula_html,
    _formula_subscripts,
    _html_template,
    _defect_kind,
    _infer_host_valences,
    _ion_valence_template,
    _kind_colors,
    _load_inputs,
    _load_magnetizations,
    _sort_defect_names,
    generate_interactive_html,
)


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _make_minimal_des() -> dict:
    """Return a minimal defect_energy_summary.json-like dict."""
    return {
        "title": "CsPbBr3",
        "cbm": 2.4095,
        "supercell_vbm": -0.0944,
        "supercell_cbm": 2.5271,
        "rel_chem_pots": {
            "A": {"Br": -1.20, "Cs": -3.76, "Pb": -2.42, "Bi": -2.60,
                  "competing_phases": ["CsPbBr3", "CsBr"],
                  "impurity_phases": ["BiBr3"]},
            "B": {"Br": -0.50, "Cs": -3.10, "Pb": -1.80, "Bi": -1.90,
                  "competing_phases": ["CsBr"]},
            "C": {"Br": -1.50, "Cs": -4.00, "Pb": -3.10, "Bi": -3.20,
                  "competing_phases": ["PbBr2"],
                  "impurity_phases": ["BiBr3"]},
            "D": {"Br": -0.80, "Cs": -4.20, "Pb": -2.90, "Bi": -3.50,
                  "competing_phases": ["PbBr2"]},
        },
        "defect_energies": {
            "Bi_Pb1": {
                "charges": [-1, 0, 1],
                "atom_io": {"Pb": -1, "Bi": 1},
                "defect_energies": [
                    {
                        "formation_energy": -0.6,
                        "is_shallow": False,
                        "energy_corrections": {"pc term": 0.10},
                    },
                    {
                        "formation_energy": 0.2,
                        "is_shallow": False,
                        "energy_corrections": {},
                    },
                    {
                        "formation_energy": 1.0,
                        "is_shallow": False,
                        "energy_corrections": {"pc term": 0.05},
                    },
                ],
            },
            "Va_Br1": {
                "charges": [-1, 0, 1],
                "atom_io": {"Br": -1},
                "defect_energies": [
                    {
                        "formation_energy": 1.5,
                        "is_shallow": False,
                        "energy_corrections": {},
                    },
                    {
                        "formation_energy": 0.8,
                        "is_shallow": True,  # shallow → filtered
                        "energy_corrections": {},
                    },
                    {
                        "formation_energy": 2.3,
                        "is_shallow": False,
                        "energy_corrections": {"alignment term": -0.05},
                    },
                ],
            },
        },
    }


def _make_minimal_cpd() -> dict:
    """Return a minimal chem_pot_diag.json-like dict."""
    return {
        "vertex_elements": ["Br", "Cs", "Pb"],
        "polygons": {
            "CsPbBr3": [[-1.20, -3.76, -2.42],
                         [-0.50, -3.10, -1.80],
                         [-1.50, -4.00, -3.10],
                         [-0.80, -4.20, -2.90]],
        },
    }


def _make_minimal_tv() -> dict:
    return {"target": "CsPbBr3"}


def _des_summary(de: dict) -> Any:
    """Convert a defect_energy_summary-like dict into a DefectSummary."""
    from vasp_sop.defect.pydefect_adapter import (
        DefectEnergy, DefectSummary, FormationEnergy,
    )

    defects = []
    for name, entry in de.get("defect_energies", {}).items():
        if not isinstance(entry, dict) or "charges" not in entry:
            continue
        energies = entry.get("defect_energies", [])
        fes = []
        for i, q in enumerate(entry["charges"]):
            item = {}
            if isinstance(energies, list) and i < len(energies) and isinstance(energies[i], dict):
                item = energies[i]
            corr = item.get("energy_corrections", {})
            if not isinstance(corr, dict):
                corr = {}
            fes.append(FormationEnergy(
                charge=int(q),
                formation_energy=float(item.get("formation_energy", 0.0)),
                is_shallow=bool(item.get("is_shallow")),
                correction=float(sum(corr.values())),
            ))
        defects.append(DefectEnergy(
            name=name,
            charges=[int(q) for q in entry["charges"]],
            atom_io=dict(entry.get("atom_io", {})),
            formation_energies=fes,
        ))
    cbm = de.get("cbm", de.get("supercell_cbm"))
    return DefectSummary(
        title=str(de.get("title", "")),
        cbm=float(cbm) if isinstance(cbm, (int, float)) else None,
        defects=defects,
    )


def _cpd_record(cpd: dict, tv: dict, de: dict) -> Any:
    """Convert (chempot_diag, target_vertices, summary) dicts into a CpdDiagram."""
    from vasp_sop.defect.pydefect_adapter import CpdDiagram

    host = tv.get("target", "") if isinstance(tv, dict) else ""
    target_keys = [k for k in cpd.get("polygons", {}) if k != "combos"]
    if not host:
        host = de.get("title", target_keys[0] if target_keys else "host")
    rcp = de.get("rel_chem_pots", tv)
    if not isinstance(rcp, dict):
        rcp = {}
    return CpdDiagram(
        target=str(host),
        vertex_elements=[str(e) for e in cpd.get("vertex_elements", [])],
        polygons=dict(cpd.get("polygons", {}) or {}),
        rel_chem_pots=rcp,
        title=str(de.get("title", "")),
    )


def _write_system(tmp_path: Path) -> Path:
    """Write a minimal CsPbBr3 system and return its path."""
    root = tmp_path / "CsPbBr3"
    (root / "defect").mkdir(parents=True)
    (root / "cpd").mkdir(parents=True)
    (root / "defect" / "defect_energy_summary.json").write_text(
        json.dumps(_make_minimal_des())
    )
    (root / "cpd" / "chem_pot_diag.json").write_text(
        json.dumps(_make_minimal_cpd())
    )
    (root / "cpd" / "target_vertices.yaml").write_text(
        yaml.dump(_make_minimal_tv())
    )
    return root


# ═════════════════════════════════════════════════════════════════════
# _build_defects
# ═════════════════════════════════════════════════════════════════════


class TestBuildDefects:
    def test_applies_corrections(self):
        """E0 values include energy_corrections (aggregated in the record)."""
        defects = _build_defects(_des_summary(_make_minimal_des()))
        # Bi_Pb1 q=-1: formation_energy=-0.6 + pc=0.10 = -0.5
        bi = defects["Bi_Pb1"]
        charges = {c["q"]: c["e0"] for c in bi["charges"]}
        assert charges[-1] == pytest.approx(-0.5)  # -0.6 + 0.10
        assert charges[0] == pytest.approx(0.2)   # 0.2 + 0
        assert charges[1] == pytest.approx(1.05)   # 1.0 + 0.05

    def test_filters_shallow_charge_states(self):
        """Shallow charge states are excluded."""
        defects = _build_defects(_des_summary(_make_minimal_des()))
        # Va_Br1: q=0 is shallow → excluded
        va = defects["Va_Br1"]
        qs = [c["q"] for c in va["charges"]]
        assert qs == [-1, 1]
        assert 0 not in qs

    def test_shallow_removed_entire_defect_if_all_shallow(self):
        """If all charge states are shallow, defect is omitted entirely."""
        de = _make_minimal_des()
        # Make Va_Br1 entirely shallow
        for e in de["defect_energies"]["Va_Br1"]["defect_energies"]:
            e["is_shallow"] = True
        defects = _build_defects(_des_summary(de))
        assert "Va_Br1" not in defects
        # Bi_Pb1 still present
        assert "Bi_Pb1" in defects


# ═════════════════════════════════════════════════════════════════════
# _extract_vertex_data


class TestExtractPolygon:
    def test_returns_ordered_vertices(self):
        de = _make_minimal_des()
        cpd = _make_minimal_cpd()
        tv = _make_minimal_tv()
        vertex_mu, names, host, elems, phases = _extract_vertex_data(_cpd_record(cpd, tv, de))
        assert host == "CsPbBr3"
        assert len(vertex_mu) == 4
        assert len(names) == 4
        assert len(vertex_mu) == 4
        # Constraint phases survive extraction (per vertex, cyclic order).
        assert len(phases) == 4
        by_name = dict(zip(names, phases))
        assert by_name["A"]["competing"] == ["CsPbBr3", "CsBr"]
        assert by_name["A"]["impurity"] == ["BiBr3"]

    def test_names_match_rcp_keys(self):
        de = _make_minimal_des()
        cpd = _make_minimal_cpd()
        tv = _make_minimal_tv()
        _, names, _, _, _ = _extract_vertex_data(_cpd_record(cpd, tv, de))
        assert set(names) == {"A", "B", "C", "D"}

    def test_cyclic_order_includes_all_vertices(self):
        """Angle-sorted ordering includes every vertex exactly once."""
        de = _make_minimal_des()
        cpd = _make_minimal_cpd()
        tv = _make_minimal_tv()
        vertex_mu, names, _, _, _ = _extract_vertex_data(_cpd_record(cpd, tv, de))
        assert len(vertex_mu) == 4
        assert len(names) == 4
        assert set(names) == {"A", "B", "C", "D"}

    def test_bow_tie_prevention_on_real_data(self):
        """On the known-good CsPbBr3 data, vertices form a convex polygon."""
        p = Path("/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/CsPbBr3")
        if not p.is_dir():
            pytest.skip("CsPbBr3 system not available")
        de = json.loads((p / "defect" / "defect_energy_summary.json").read_text())
        cpd = json.loads((p / "cpd" / "chem_pot_diag.json").read_text())
        tv = yaml.safe_load((p / "cpd" / "target_vertices.yaml").read_text())
        vertex_mu, _, _, vertex_elements, _ = _extract_vertex_data(_cpd_record(cpd, tv, de))
        assert len(vertex_mu) == 4

        def _cross(o, a, b):
            return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
        signs = []
        ve = vertex_elements
        for n in range(4):
            pn = vertex_mu[n%4]; pn1 = vertex_mu[(n+1)%4]; pn2 = vertex_mu[(n+2)%4]
            signs.append(_cross([pn[ve[0]],pn[ve[1]]],
                                [pn1[ve[0]],pn1[ve[1]]],
                                [pn2[ve[0]],pn2[ve[1]]]))
        assert all(s > 0 for s in signs) or all(s < 0 for s in signs)


# ═════════════════════════════════════════════════════════════════════
# _sort_defect_names
# ═════════════════════════════════════════════════════════════════════


class TestKindColors:
    def test_kind_strips_site_digits(self):
        assert _defect_kind("Va_O13") == "Va_O"
        assert _defect_kind("Bi_Ti1") == "Bi_Ti"
        assert _defect_kind("O_i1") == "O_i"

    def test_shared_color_per_kind(self):
        names = ["Va_O1", "Va_O2", "Bi_Ti1", "Bi_Ti2", "Bi_Ti3"]
        colors = _kind_colors(names)
        assert colors[0] == colors[1]
        assert colors[2] == colors[3] == colors[4]
        assert colors[0] != colors[2]


class TestSortDefectNames:
    def test_doped_first(self):
        defects = {"Va_Br1": {}, "Bi_Pb1": {}, "Cs_Pb1": {}, "Bi_Cs1": {}}
        sorted_ = _sort_defect_names(defects)
        # Bi_ defects come first
        assert sorted_[0].startswith("Bi_")
        assert sorted_[1].startswith("Bi_")
        # intrinsic follows
        assert not sorted_[2].startswith("Bi_")
        assert not sorted_[3].startswith("Bi_")

    def test_alphabetic_within_group(self):
        defects = {"Bi_Pb1": {}, "Bi_Cs1": {}, "Va_Cs1": {}, "Va_Br1": {}}
        sorted_ = _sort_defect_names(defects)
        assert sorted_[0] == "Bi_Cs1"
        assert sorted_[1] == "Bi_Pb1"
        assert sorted_[2] == "Va_Br1"
        assert sorted_[3] == "Va_Cs1"


# ═════════════════════════════════════════════════════════════════════
# Readout chemistry: compound-inferred ion valence + magnetization
# ═════════════════════════════════════════════════════════════════════


class TestInferHostValences:
    def test_solves_charge_neutrality(self):
        # Gd2GaSbO7: 2·3 + 3 + 5 = 14 = 7·2
        assert _infer_host_valences("Gd2GaSbO7") == {
            "Gd": 3, "Ga": 3, "Sb": 5,
        }

    def test_tungstate(self):
        assert _infer_host_valences("Ba3W2O9") == {"Ba": 2, "W": 6}

    def test_bracketed_formula_expands(self):
        # Sr[FeO2]2 = SrFe2O4: Sr²⁺ + 2 Fe³⁺ balances 4 O²⁻
        assert _infer_host_valences("Sr[FeO2]2") == {"Sr": 2, "Fe": 3}

    def test_unparseable_returns_empty(self):
        assert _infer_host_valences("") == {}
        assert _infer_host_valences("???") == {}


class TestIonValenceTemplate:
    def test_substitution_uses_host_valence(self):
        hv = _infer_host_valences("Gd2GaSbO7")
        assert _ion_valence_template("Bi_Ga1", hv) == {"p": "Bi", "h": 3}
        assert _ion_valence_template("Bi_Sb1", hv) == {"p": "Bi", "h": 5}

    def test_interstitial_is_charge_conserved(self):
        assert _ion_valence_template("O_i1", {}) == {"p": "O", "h": None}

    def test_vacancy_is_bare_q(self):
        assert _ion_valence_template("Va_O1", {}) == {"v": True}

    def test_unknown_site_or_form(self):
        assert _ion_valence_template("Xx_Y1", {}) == {"p": "?", "h": None}
        assert _ion_valence_template("Bi_Ga1", {}) == {"p": "?", "h": None}


class TestLoadMagnetizations:
    def _write_cr(self, root: Path, dirname: str, mag: float) -> None:
        d = root / "defect" / dirname
        d.mkdir(parents=True)
        (d / "calc_results.json").write_text(
            json.dumps({"magnetization": mag, "energy": -1.0}),
        )

    def test_reads_per_charge_state(self, tmp_path):
        self._write_cr(tmp_path, "Va_O1_0", 0.573)
        self._write_cr(tmp_path, "Va_O1_1", -0.571)
        mags = _load_magnetizations(tmp_path, ["Va_O1"])
        assert mags == {"Va_O1": {0: 0.573, 1: -0.571}}

    def test_skips_missing_and_foreign_dirs(self, tmp_path):
        self._write_cr(tmp_path, "Va_O1_0", 0.573)
        (tmp_path / "defect" / "perfect").mkdir(parents=True)
        mags = _load_magnetizations(tmp_path, ["Va_O1"])
        assert mags == {"Va_O1": {0: 0.573}}

    def test_skips_unreadable_calc_results(self, tmp_path):
        d = tmp_path / "defect" / "Va_O1_0"
        d.mkdir(parents=True)
        (d / "calc_results.json").write_text("{not json")
        assert _load_magnetizations(tmp_path, ["Va_O1"]) == {}


# ═════════════════════════════════════════════════════════════════════
# display-name typesetting (_defect_display / _formula_*)
# ═════════════════════════════════════════════════════════════════════


class TestDisplayNames:
    def test_defect_name_species_site_subscript_charge_superscript(self):
        assert _defect_segments("Al_Ca1_-1") == [
            ["n", "Al"], ["s", "Ca1"], ["p", "-1"],
        ]
        assert _defect_segments("Va_O1_0") == [
            ["n", "Va"], ["s", "O1"], ["p", "0"],
        ]

    def test_defect_name_without_charge_part(self):
        assert _defect_segments("Bi_Pb1") == [["n", "Bi"], ["s", "Pb1"]]

    def test_defect_name_with_legacy_prefix(self):
        assert _defect_segments("1_Fe_Ca1_2+") == [
            ["n", "Fe"], ["s", "Ca1"], ["p", "2+"],
        ]

    def test_defect_name_with_underscored_site(self):
        assert _defect_segments("O_i1_0") == [
            ["n", "O"], ["s", "i1"], ["p", "0"],
        ]

    def test_unparseable_name_stays_single_normal_segment(self):
        assert _defect_segments("BaAl4O7") == [["n", "BaAl4O7"]]

    def test_formula_subscripts(self):
        assert _formula_subscripts("CaAl4O7") == "CaAl₄O₇"
        assert _formula_subscripts("Sr[FeO2]2") == "Sr[FeO₂]₂"

    def test_formula_html_wraps_digit_runs(self):
        assert _formula_html("CaAl4O7") == "CaAl<sub>4</sub>O<sub>7</sub>"
        assert _formula_html("Gd2GaSbO7:Bi") == (
            "Gd<sub>2</sub>GaSbO<sub>7</sub>:Bi"
        )


# ═════════════════════════════════════════════════════════════════════
# _bary_js_func
# ═════════════════════════════════════════════════════════════════════


class TestBaryJsFunc:
    @pytest.fixture
    def verts(self):
        return [[-1.20, -3.76, -2.42, -2.60],
                [-0.50, -3.10, -1.80, -1.90],
                [-1.50, -4.00, -3.10, -3.20],
                [-0.80, -4.20, -2.90, -3.50]]

    def test_produces_valid_function(self, verts):
        js = _bary_js(verts, (0, 1, 2))
        assert js.startswith("function(px,py)")
        assert "return[a,b,c,inside]" in js
    def test_no_double_dash_bug(self, verts):
        """The '--' operator must NOT appear in JS output."""
        js12 = _bary_js(verts, (0, 1, 2))
        js23 = _bary_js(verts, (0, 2, 3))
        # Should not have JS decrement '--' (with no space)
        assert "--" not in js12.replace("- ", "").replace(" -", " - ")
        assert "--" not in js23.replace("- ", "").replace(" -", " - ")


# ═════════════════════════════════════════════════════════════════════
# _html_template
# ═════════════════════════════════════════════════════════════════════


class TestHtmlTemplate:
    def test_produces_valid_html(self):
        html = _html_template(
            host_name="CsPbBr3",
            n_vertices=4,
            poly_2d=[[-1.20, -3.76], [-0.50, -3.10],
                      [-1.50, -4.00], [-0.80, -4.20]],
            vertex_mu=[{"Br": -1.20, "Cs": -3.76, "Pb": -2.42, "Bi": -2.60},
                       {"Br": -0.50, "Cs": -3.10, "Pb": -1.80, "Bi": -1.90},
                       {"Br": -1.50, "Cs": -4.00, "Pb": -3.10, "Bi": -3.20},
                       {"Br": -0.80, "Cs": -4.20, "Pb": -2.90, "Bi": -3.50}],
            vertex_names=["A", "B", "C", "D"],
            vertex_phases=[
                {"competing": ["CsBr"], "impurity": ["BiBr3"]},
                {"competing": ["CsBr"], "impurity": []},
                {"competing": ["PbBr2"], "impurity": ["BiBr3"]},
                {"competing": ["PbBr2"], "impurity": []},
            ],
            vertex_elements=["Br", "Cs", "Pb"],
            defects={"Bi_Pb1": {"charges": [{"q": -1, "e0": -0.5}],
                                "delta": {"Pb": -1, "Bi": 1}}},
            sorted_names=["Bi_Pb1"],
            ref_mu={"Br": -1.20, "Cs": -3.76, "Pb": -2.42},
            colors=["#e94560"],
            cbm=2.4095,
            ax0="Br", ax1="Cs",
            a0_range=(-1.8, -0.2),
            a1_range=(-4.5, -2.8),
            exo_elements=["Bi"],
        )
        # Core rendering and the two-card scientific workspace exist.
        assert "<!DOCTYPE html>" in html
        assert "report-grid" in html
        assert "化学势稳定区" in html
        assert "var POLY" in html
        assert "var DEF" in html
        assert "var BG" in html
        assert "function drawFE" in html
        assert "getMu(px,py)" in html
        assert "function fillTip" in html
        assert "function dockTip" in html
        assert "function undockTip" in html
        # Formation-energy axis never exceeds +10 eV.
        assert "if(maxY>10)maxY=10" in html

    def test_constraint_phases_and_charge_neutrality_embedded(self):
        html = _html_template(
            host_name="CsPbBr3",
            n_vertices=4,
            poly_2d=[[-1.20, -3.76], [-0.50, -3.10],
                      [-1.50, -4.00], [-0.80, -4.20]],
            vertex_mu=[{"Br": -1.20, "Cs": -3.76, "Pb": -2.42, "Bi": -2.60},
                       {"Br": -0.50, "Cs": -3.10, "Pb": -1.80, "Bi": -1.90},
                       {"Br": -1.50, "Cs": -4.00, "Pb": -3.10, "Bi": -3.20},
                       {"Br": -0.80, "Cs": -4.20, "Pb": -2.90, "Bi": -3.50}],
            vertex_names=["A", "B", "C", "D"],
            vertex_phases=[
                {"competing": ["CsBr"], "impurity": ["BiBr3"]},
                {"competing": ["CsBr"], "impurity": []},
                {"competing": ["PbBr2"], "impurity": ["BiBr3"]},
                {"competing": ["PbBr2"], "impurity": []},
            ],
            vertex_elements=["Br", "Cs", "Pb"],
            defects={"Bi_Pb1": {"charges": [{"q": -1, "e0": -0.5}],
                                "delta": {"Pb": -1, "Bi": 1}}},
            sorted_names=["Bi_Pb1"],
            ref_mu={"Br": -1.20, "Cs": -3.76, "Pb": -2.42},
            colors=["#e94560"],
            cbm=2.4095,
            ax0="Br", ax1="Cs",
            a0_range=(-1.8, -0.2),
            a1_range=(-4.5, -2.8),
            exo_elements=["Bi"],
        )
        # Constraint phases remain encoded in VPHASES; the currently selected
        # vertex is rendered into the scientific readout instead of printed
        # beside every CPD point.
        assert "CsBr" in html
        assert "PbBr<sub>2</sub>" in html or "PbBr2" in html
        # Uniform label: no impurity-phase section (doped systems match
        # undoped ones).
        assert "不稳定" not in html
        assert "var VPHASES" in html
        # Charge neutrality: intrinsic-only balance + drawn Fermi line.
        assert "var EXO" in html
        assert '["Bi"]' in html
        assert "function calcFermi" in html
        assert "function drawFermi" in html
        assert "电荷中性" in html
        assert "function isIntrinsic" in html
        # Overlap removed: the per-element μ printout in the FE canvas is gone.
        assert 'fillText(k+" = "+mu[k]' not in html
        # Legend: grouped by defect KIND (site-independent base name) and
        # fixed — the drag reorder loop must not be present.
        assert "function defectBase" in html
        assert "function toggleGroup" in html
        assert "leg-group" in html
        assert "leg-cat" in html
        assert "leg.appendChild(div)" not in html
        # Charge neutrality ignores display hiding (legend click must not
        # change the physics).
        assert "hidden[n]||!isIntrinsic(n)" not in html

    def test_embed_display_names_panel_and_responsive_layout(self):
        html = _html_template(
            host_name="CsPbBr3",
            n_vertices=4,
            poly_2d=[[-1.20, -3.76], [-0.50, -3.10],
                      [-1.50, -4.00], [-0.80, -4.20]],
            vertex_mu=[{"Br": -1.20, "Cs": -3.76, "Pb": -2.42, "Bi": -2.60},
                       {"Br": -0.50, "Cs": -3.10, "Pb": -1.80, "Bi": -1.90},
                       {"Br": -1.50, "Cs": -4.00, "Pb": -3.10, "Bi": -3.20},
                       {"Br": -0.80, "Cs": -4.20, "Pb": -2.90, "Bi": -3.50}],
            vertex_names=["A", "B", "C", "D"],
            vertex_phases=[
                {"competing": ["CsBr"], "impurity": ["BiBr3"]},
                {"competing": ["CsBr"], "impurity": []},
                {"competing": ["PbBr2"], "impurity": ["BiBr3"]},
                {"competing": ["PbBr2"], "impurity": []},
            ],
            vertex_elements=["Br", "Cs", "Pb"],
            defects={"Bi_Pb1": {"charges": [{"q": -1, "e0": -0.5}],
                                "delta": {"Pb": -1, "Bi": 1}}},
            sorted_names=["Bi_Pb1"],
            ref_mu={"Br": -1.20, "Cs": -3.76, "Pb": -2.42},
            colors=["#e94560"],
            cbm=2.4095,
            ax0="Br", ax1="Cs",
            a0_range=(-1.8, -0.2),
            a1_range=(-4.5, -2.8),
            exo_elements=["Bi"],
        )
        # Typeset title (HTML <sub>) and display-name segment map.
        assert "CsPbBr<sub>3</sub>" in html
        assert '"Bi_Pb1": [["n", "Bi"], ["s", "Pb1"]]' in html
        # Chemical-potential card and responsive native chart sizing.
        assert "当前化学条件" in html
        assert "化学势范围" in html
        assert "function buildMuPanel" in html
        assert "function updateMuPanel" in html
        assert "function updateSelectionCard" in html
        assert "devicePixelRatio" in html
        assert "function layout" in html
        assert "fe-tip" in html
        assert "fe-note" in html
        assert "matchMedia" in html
        # Inspector/legend use typeset HTML names.
        assert "function segHtml" in html
        assert "class='csub'" in html
        assert "class='csup'" in html
        assert "setAttribute(\"data-name\"" in html


# ═════════════════════════════════════════════════════════════════════
# _load_inputs
# ═════════════════════════════════════════════════════════════════════


class TestLoadInputs:
    def test_reads_all_three(self, tmp_path):
        root = _write_system(tmp_path)
        de, cpd = _load_inputs(root)
        assert de.title == "CsPbBr3"
        assert cpd.polygons
        assert cpd.target == "CsPbBr3"

    def test_raises_on_missing_json(self, tmp_path):
        root = tmp_path / "Foo"
        root.mkdir()
        (root / "cpd").mkdir()
        (root / "defect").mkdir()
        (root / "cpd" / "target_vertices.yaml").write_text("target: X\n")
        (root / "cpd" / "chem_pot_diag.json").write_text("{}")
        with pytest.raises(ValueError):
            _load_inputs(root)


# ═════════════════════════════════════════════════════════════════════
# generate_interactive_html (integration)
# ═════════════════════════════════════════════════════════════════════


class TestGenerateInteractiveHtml:
    def test_writes_html_file(self, tmp_path):
        root = _write_system(tmp_path)
        out = generate_interactive_html(root)
        assert out.is_file()
        assert out.name == "formation_energy_interactive.html"
        content = out.read_text()
        assert content.startswith("<!DOCTYPE html>")

    def test_includes_defect_data(self, tmp_path):
        root = _write_system(tmp_path)
        out = generate_interactive_html(root)
        content = out.read_text()
        # Bi_Pb1 with corrected e0 values
        assert '"Bi_Pb1"' in content
        assert '"Va_Br1"' in content
        # Check BG is set to cbm
        assert "var BG = 2.4095" in content

    def test_bary_functions_defined(self, tmp_path):
        root = _write_system(tmp_path)
        out = generate_interactive_html(root)
        content = out.read_text()
        # unified N-gon path: barycentric functions live in the BARYS array
        assert "var BARYS = [" in content
        assert "var TRIS = [" in content
        assert content.count("function(px,py)") >= 1

    def test_doped_line_style(self, tmp_path):
        """Doped defects should use dashed lines."""
        root = _write_system(tmp_path)
        out = generate_interactive_html(root)
        content = out.read_text()
        # isDoped check present
        assert "isDoped" in content

    def test_hover_readout_code_present(self, tmp_path):
        root = _write_system(tmp_path)
        out = generate_interactive_html(root)
        content = out.read_text()
        assert "mousemove" in content
        assert "matchMedia(\"(hover:hover)\")" in content
        assert "fe-tip" in content
        assert "fillTip" in content
        assert "dockTip" in content
        assert "getElementById(\"cpdCard\")" in content
        # Content-adaptive sizing contract: clamped width, viewport-capped
        # height, re-measure on every content update.
        assert "READOUT_W_MIN" in content
        assert "READOUT_W_MAX" in content
        assert "function sizeTip" in content
        assert "window.innerHeight" in content
        # Readout chemistry contract: per-row ion valence (compound-inferred
        # host-site valence + q — the dynamic 价态) + that charge state's
        # magnetization.
        assert "var MAG" in content
        assert "var VOX" in content
        assert "function calcRow" in content
        assert "function ionLabel" in content
        assert "function qLabel" in content
        assert "function muLabel" in content
        assert "tspin" in content
        # Docked-over-CPD semantics: no follow/flip/freeze machinery remains,
        # no pinned state, and the retired fixed panel is fully gone.
        assert "showTip" not in content
        assert "pinnedEF" not in content
        assert "形成能检查器" not in content
        assert "renderInspector" not in content
        assert "tipHover" not in content
        assert "tipTimer" not in content

    def test_idempotent(self, tmp_path):
        """Second call overwrites cleanly."""
        root = _write_system(tmp_path)
        out1 = generate_interactive_html(root)
        out2 = generate_interactive_html(root)
        assert out1 == out2
        assert out1.is_file()


# ═════════════════════════════════════════════════════════════════════
# Data-consistency: interactive vs pydefect static PDF
# ═════════════════════════════════════════════════════════════════════

class TestInteractiveVsPydefect:
    """Verify interactive HTML data matches pydefect static PDF output.

    For the same chemical-potential vertex, the formation energies
    embedded in the HTML must equal those plotted in ``pydefect pe`` PDFs.
    """

    @pytest.fixture(scope="class")
    def _csPbBr3_data(self):
        p = Path("/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/CsPbBr3")
        if not p.is_dir():
            pytest.skip("CsPbBr3 system not available")
        from vasp_sop.report.interactive import (
            _build_defects, _extract_vertex_data, _load_inputs,
        )
        de, cpd = _load_inputs(p)
        defects = _build_defects(de)
        vertex_mu, poly_names, host_name, vertex_elements, _ = _extract_vertex_data(
            cpd
        )
        ref_mu_raw = cpd.rel_chem_pots
        ref_mu = next(iter(ref_mu_raw.values())) if ref_mu_raw else {}
        return {
            "defects": defects,
            "poly": vertex_mu,
            "poly_names": poly_names,
            "poly_mu_bi": [v.get("Bi", 0.0) for v in vertex_mu],
            "ref_mu": ref_mu,
            "vertex_elements": vertex_elements,
        }
    @staticmethod
    def _calc_ef(defects, mu, name, charge_entry, e_f=0.0):
        """Mirror of JS calcE: e0 + q*e_f + Σ(-Δn_elem * μ_elem)."""
        delta = defects[name]["delta"]
        ms = 0.0
        for elem, dn in delta.items():
            if elem in mu:
                ms -= dn * mu[elem]
        return charge_entry["e0"] + charge_entry["q"] * e_f + ms

    def test_formation_energy_vertex_a_match_pydefect(self, _csPbBr3_data):
        """At vertex A, E_f(0) values equal pydefect charge_energies output."""
        d = _csPbBr3_data
        de_dict = self._load_real_de()
        try:
            from pydefect.analyzer.defect_energy import DefectEnergySummary
            from monty.json import MontyDecoder
            summary = MontyDecoder().process_decoded(de_dict)
        except Exception as exc:
            pytest.skip(f"pydefect unavailable: {exc}")

        ce = summary.charge_energies(
            "A", allow_shallow=False, with_corrections=True,
            e_range=(0, de_dict["cbm"]), name_style=False,
        )
        cbm = de_dict["cbm"]

        # pydefect oracle: charge_energies_dict maps name → SingleChargeEnergies
        cdict = ce.charge_energies_dict

        ve = d["vertex_elements"]
        vm0 = d["poly"][0]
        vertex_a_mu = {
            ve[0]: vm0[ve[0]],
            ve[1]: vm0[ve[1]],
            ve[2]: vm0[ve[2]],
            "Bi": d["poly_mu_bi"][0],
        }

        for name, single in cdict.items():
            for q, energy in single.charge_energies:
                cs = [c for c in d["defects"].get(name, {}).get("charges", [])
                      if c["q"] == q]
                if not cs:
                    continue
                ef = self._calc_ef(d["defects"], vertex_a_mu, name, cs[0])
                assert ef == pytest.approx(energy, abs=0.002), (
                    f"vertex=A {name} q={q}: "
                    f"interactive={ef:.4f} pydefect={energy:.4f}"
                )

    def test_html_embed_matches_python_data(self, tmp_path, _csPbBr3_data):
        """The DEF object in generated HTML equals _build_defects output."""
        root = tmp_path / "system"
        root.mkdir()
        (root / "defect").mkdir()
        (root / "cpd").mkdir()
        import json as _json
        p = Path("/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/CsPbBr3")
        (root / "defect" / "defect_energy_summary.json").write_text(
            (p / "defect" / "defect_energy_summary.json").read_text()
        )
        (root / "cpd" / "chem_pot_diag.json").write_text(
            (p / "cpd" / "chem_pot_diag.json").read_text()
        )
        (root / "cpd" / "target_vertices.yaml").write_text(
            (p / "cpd" / "target_vertices.yaml").read_text()
        )

        html = generate_interactive_html(root).read_text()

        # Extract the DEF JSON from var DEF = ...;
        import re as _re
        m = _re.search(r"var DEF = (\{.*?\});", html, _re.DOTALL)
        assert m, "DEF not found in generated HTML"
        html_def = _json.loads(m.group(1))
        py_def = _csPbBr3_data["defects"]
        for name in html_def:
            assert name in py_def, f"{name}: in HTML but not in Python _build_defects"
            hc = {c["q"]: c["e0"] for c in html_def[name]["charges"]}
            pc = {c["q"]: c["e0"] for c in py_def[name]["charges"]}
            for q, e0 in hc.items():
                assert q in pc, f"{name} q={q}: in HTML but not Python"
                assert e0 == pytest.approx(pc[q], abs=0.0001), (
                    f"{name} q={q}: HTML={e0:.6f} vs py={pc[q]:.6f}"
                )
            assert html_def[name]["delta"] == py_def[name]["delta"]


    def test_all_vertices_match_pydefect_charge_energies(self, _csPbBr3_data):
        """Per-vertex, per-defect, per-charge-state comparison with pydefect.

        Uses the real pydefect DefectEnergySummary.charge_energies() as oracle.
        Skips if pydefect is not importable.
        """
        d = _csPbBr3_data
        de_dict = self._load_real_de()
        try:
            from pydefect.analyzer.defect_energy import DefectEnergySummary
            from monty.json import MontyDecoder
            summary = MontyDecoder().process_decoded(de_dict)
        except Exception as exc:
            pytest.skip(f"pydefect oracle unavailable: {exc}")

        cbm = de_dict["cbm"]
        vertex_mu_list = d["poly"]
        poly_names = d["poly_names"]
        poly_mu_bi = d["poly_mu_bi"]
        vertex_elements = d["vertex_elements"]

        for vi, vname in enumerate(poly_names):
            mu = {}
            for elem in vertex_elements:
                if elem in vertex_mu_list[vi]:
                    mu[elem] = vertex_mu_list[vi][elem]
            if vi < len(poly_mu_bi):
                mu["Bi"] = poly_mu_bi[vi]

            try:
                ce = summary.charge_energies(
                    vname, allow_shallow=False, with_corrections=True,
                    e_range=(0, cbm), name_style=False,
                )
            except Exception:
                continue

            for name, single in ce.charge_energies_dict.items():
                for q, energy in single.charge_energies:
                    cs = [c for c in d["defects"].get(name, {}).get("charges", [])
                          if c["q"] == q]
                    if not cs:
                        continue
                    ef = self._calc_ef(d["defects"], mu, name, cs[0])
                    assert ef == pytest.approx(energy, abs=0.002), (
                        f"vertex={vname} {name} q={q}: "
                        f"interactive={ef:.4f} pydefect={energy:.4f}"
                    )

    @staticmethod
    def _load_real_de():
        import json as _json
        p = Path("/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/CsPbBr3")
        return _json.loads((p / "defect" / "defect_energy_summary.json").read_text())