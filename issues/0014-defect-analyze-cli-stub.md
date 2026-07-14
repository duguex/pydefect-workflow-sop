# `vasp-sop defect analyze` is not implemented

**Date:** 2026-07-14  
**Severity:** P1  
**File:** `vasp_sop/cli/main.py` (`defect analyze` prints stub)

## Problem

Standalone re-run of post-processing requires `batch run` full advance. Publishable workflow needs one-shot analyze after VASP finishes or for demoted partials.

## Acceptance

1. `vasp-sop defect analyze <project_dir>` runs `analyze()` for that system root (plan.yaml parent)
2. Resolves `unitcell/unitcell.yaml`, `cpd/standard_energies.yaml`, `cpd/target_vertices.yaml`
3. Prints status (`full|partial|failed`) and exits non-zero on failed
4. CLI test covers happy path with mocks

## Related

- FEATURES.md defect subcommands
