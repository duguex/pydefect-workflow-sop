# `build_unitcell_yaml` failures were silent

**Date:** 2026-07-14  
**Severity:** P2  
**File:** `vasp_sop/defect/unitcell.py`

## Problem

`pydefect_vasp u` failures (zero gap, missing band vasprun) only logged a warning; operators saw missing `unitcell.yaml` without a machine-readable reason.

## Fix

Write `unitcell/unitcell_build_status.json` with `status: failed|ok` after the build attempt.

## Acceptance

- Failed `u` leaves `unitcell_build_status.json` with `failed`
- Successful build leaves `ok` when yaml exists
