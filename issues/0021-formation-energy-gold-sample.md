# Gold-sample / regression checks for publishable formation energies

**Date:** 2026-07-14  
**Severity:** P2 (quality gate for “publishable”)  
**Related:** publishable pipeline goal; #0007

## Problem

Code can report `analyze=full` without proving formation energies are **scientifically sane** (correction magnitudes, charge continuity, transition levels vs band edges).

## Proposal

1. Pick **1–2 gold systems** already full in production (e.g. **GaN**, **AlN** or **MgO**).  
2. Freeze expected artifacts or soft bounds:
   - `defect_energy_summary.json` exists and parses  
   - every converged charge has `correction.json`  
   - \|correction\| within reasonable eV range (configurable)  
   - optional: plot `pe` vertices without crash  
3. CI or `tests/test_production.py` opt-in marker:
   - skip if production tree absent  
   - run bounds checks when `VASP_SOP_PROD_ROOT` set  

## Acceptance

- [ ] Document gold systems + bounds in FEATURES or tests docstring  
- [ ] Automated check fails on missing summary / zero corrections for gold system  
- [ ] Manual notebook or CLI one-liner to regenerate formation-energy figure for gold system  

## Non-goals

- Full doped database validation  
- Replacing human paper review
