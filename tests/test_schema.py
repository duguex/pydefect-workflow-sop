"""Tests for the JSON Schema (pydefect_auto.schema).

These tests verify the schema is the single source of truth for plan.yaml
validation, and that all the leaky fields (iindex, charges, etc.) are
required rather than silently defaulted.
"""

import json

import pytest

from pydefect_auto.schema import SCHEMA, to_json, validate


# ---------- Required keys ----------

class TestRequiredFields:
    def test_top_level_required(self):
        # Schema's own "required" list must include the top-level keys
        assert "project" in SCHEMA["required"]
        assert "parameters" in SCHEMA["required"]
        assert "supercell" in SCHEMA["required"]
        assert "defects" in SCHEMA["required"]
        assert "stages" in SCHEMA["required"]

    def test_defect_subfields_required(self):
        # The previously-leaky fields must be required
        defect_required = SCHEMA["properties"]["defects"]["required"]
        assert "iindex" in defect_required
        assert "charges" in defect_required

    def test_project_obj_required(self):
        assert "obj" in SCHEMA["properties"]["project"]["required"]


# ---------- Validation: valid plan ----------

class TestValidPlan:
    def test_valid_minimal(self, valid_plan_dict):
        errs = validate(valid_plan_dict)
        assert errs == [], f"unexpected errors: {errs}"

    def test_default_plan_passes_after_fill(self):
        # Bare DEFAULT_PLAN has empty obj, so simulate generate_plan's mutation
        from pydefect_auto.plan import DEFAULT_PLAN
        plan = {**DEFAULT_PLAN, "project": {**DEFAULT_PLAN["project"], "obj": "Si"}}
        assert validate(plan) == []


# ---------- Validation: invalid plans ----------

class TestInvalidPlans:
    def test_empty_obj(self, valid_plan_dict):
        valid_plan_dict["project"]["obj"] = ""
        errs = validate(valid_plan_dict)
        assert any("project.obj" in e for e in errs)

    def test_missing_obj(self, valid_plan_dict):
        del valid_plan_dict["project"]["obj"]
        errs = validate(valid_plan_dict)
        # Schema reports missing required property at the parent path
        assert any("project" in e and "obj" in e for e in errs), errs

    def test_missing_iindex(self, valid_plan_dict):
        del valid_plan_dict["defects"]["iindex"]
        errs = validate(valid_plan_dict)
        assert any("iindex" in e for e in errs)

    def test_missing_charges(self, valid_plan_dict):
        del valid_plan_dict["defects"]["charges"]
        errs = validate(valid_plan_dict)
        assert any("charges" in e for e in errs)

    def test_negative_encut(self, valid_plan_dict):
        valid_plan_dict["parameters"]["encut"] = -10
        errs = validate(valid_plan_dict)
        assert any("encut" in e for e in errs)

    def test_encut_too_high(self, valid_plan_dict):
        valid_plan_dict["parameters"]["encut"] = 9999
        errs = validate(valid_plan_dict)
        assert any("encut" in e for e in errs)

    def test_null_encut_allowed(self, valid_plan_dict):
        # null = auto-detect, explicitly allowed
        valid_plan_dict["parameters"]["encut"] = None
        assert validate(valid_plan_dict) == []

    def test_unknown_stage(self, valid_plan_dict):
        valid_plan_dict["stages"]["bogus"] = True
        errs = validate(valid_plan_dict)
        assert any("stages" in e and "bogus" in e for e in errs)

    def test_substitutional_missing_site(self, valid_plan_dict):
        valid_plan_dict["defects"]["substitutionals"] = [{"impurity": "O"}]
        errs = validate(valid_plan_dict)
        assert any("substitutionals" in e for e in errs)

    def test_charges_wrong_type(self, valid_plan_dict):
        valid_plan_dict["defects"]["charges"] = ["zero", "one"]
        errs = validate(valid_plan_dict)
        assert any("charges" in e for e in errs)

    def test_iindex_negative(self, valid_plan_dict):
        valid_plan_dict["defects"]["iindex"] = [-1]
        errs = validate(valid_plan_dict)
        assert any("iindex" in e for e in errs)

    def test_additional_properties_rejected(self, valid_plan_dict):
        valid_plan_dict["unknown_top_level"] = 1
        errs = validate(valid_plan_dict)
        assert any("Additional" in e or "additional" in e for e in errs)

    def test_error_messages_contain_json_path(self, valid_plan_dict):
        del valid_plan_dict["defects"]["charges"]
        errs = validate(valid_plan_dict)
        # Schema validator uses JSON pointer path; our wrapper maps to dotted
        assert any("defects" in e and "charges" in e for e in errs)


# ---------- Schema as a tool ----------

class TestSchemaSerialization:
    def test_to_json_round_trip(self):
        text = to_json()
        # Must be valid JSON
        data = json.loads(text)
        assert data == SCHEMA

    def test_to_json_pretty(self):
        text = to_json()
        # Indented output contains newlines
        assert "\n" in text
