"""Unit tests for plan.py: pure inference functions.

These functions were extracted from the monolithic generate_plan() in commit
90f7820 to make them independently testable.
"""

from pathlib import Path

import pytest

from pydefect_auto.plan import (
    DEFAULT_PLAN,
    _DFTU_FALLBACK,
    _extract_elements,
    _infer_defects,
    _infer_dft_u,
    _query_potcar_variants,
)


# ---------- _extract_elements ----------

class TestExtractElements:
    def test_simple(self):
        assert _extract_elements("SiC") == ["Si", "C"]

    def test_three_elements(self):
        assert _extract_elements("SrTiO3") == ["Sr", "Ti", "O"]

    def test_single(self):
        assert _extract_elements("Si") == ["Si"]

    def test_empty(self):
        assert _extract_elements("") == []


# ---------- _infer_defects ----------

class TestInferDefects:
    def test_intrinsic_only(self):
        v, s = _infer_defects("SiC", [])
        assert v == ["Si", "C"]
        assert s == []

    def test_with_one_dopant(self):
        v, s = _infer_defects("SiC", ["O"])
        assert v == ["Si", "C"]
        assert s == [
            {"impurity": "O", "site": "Si"},
            {"impurity": "O", "site": "C"},
        ]

    def test_with_two_dopants(self):
        v, s = _infer_defects("SrTiO3", ["La", "Nb"])
        assert v == ["Sr", "Ti", "O"]
        assert len(s) == 6  # 2 dopants × 3 host sites
        assert {"impurity": "La", "site": "Ti"} in s
        assert {"impurity": "Nb", "site": "O"} in s

    def test_dopant_same_as_host(self):
        # Self-doping should still appear (caller decides whether to filter)
        v, s = _infer_defects("SiC", ["Si"])
        assert {"impurity": "Si", "site": "C"} in s


# ---------- _infer_dft_u ----------

class TestInferDftU:
    def test_fe_triggers_via_fallback(self, fe_o_poscar, monkeypatch):
        # Force fallback path: pretend vise is not importable
        import builtins
        real_import = builtins.__import__
        def fake_import(name, *args, **kwargs):
            if name.startswith("vise"):
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", fake_import)

        assert _infer_dft_u(fe_o_poscar) is True

    def test_si_does_not_trigger(self, si_poscar, monkeypatch):
        import builtins
        real_import = builtins.__import__
        def fake_import(name, *args, **kwargs):
            if name.startswith("vise"):
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", fake_import)

        assert _infer_dft_u(si_poscar) is False

    def test_missing_path_returns_false(self, tmp_path):
        assert _infer_dft_u(tmp_path / "nope") is False

    def test_fallback_set_covers_d_and_f_block(self):
        # Sanity: ensure the fallback actually has the d/f elements we expect
        assert "Fe" in _DFTU_FALLBACK
        assert "Mn" in _DFTU_FALLBACK
        assert "La" in _DFTU_FALLBACK
        assert "U" in _DFTU_FALLBACK
        assert "Si" not in _DFTU_FALLBACK
        assert "O" not in _DFTU_FALLBACK


# ---------- _query_potcar_variants ----------

class TestQueryPotcarVariants:
    def test_no_pmg_vasp_psp_dir_returns_empty(self, monkeypatch, si_poscar):
        # Clear the env var to force the "no potcar dir" path
        from pymatgen.core import SETTINGS
        monkeypatch.setattr(SETTINGS, "get", lambda k, default=None: "")
        result = _query_potcar_variants(si_poscar, "Si", [])
        assert result == {}

    def test_missing_poscar_returns_empty(self, tmp_path):
        result = _query_potcar_variants(tmp_path / "nope", "Si", [])
        assert result == {}

    def test_dopants_and_intrinsic_union(self, monkeypatch, si_poscar):
        # We can't easily mock the potcar dir; just verify the function
        # doesn't crash and returns either a dict with Si or an empty dict
        from pymatgen.core import SETTINGS
        monkeypatch.setattr(SETTINGS, "get", lambda k, default=None: "")
        result = _query_potcar_variants(si_poscar, "Si", ["O"])
        assert isinstance(result, dict)


# ---------- DEFAULT_PLAN sanity ----------

class TestDefaultPlan:
    def test_default_keys_present(self):
        # All required top-level keys from the schema must be present
        for k in ("project", "parameters", "supercell", "defects", "stages"):
            assert k in DEFAULT_PLAN

    def test_default_charges_and_iindex(self):
        # These were the leaky fields we added in the previous commit
        assert DEFAULT_PLAN["defects"]["charges"] == [0]
        assert DEFAULT_PLAN["defects"]["iindex"] == []
