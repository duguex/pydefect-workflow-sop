# Goal: Publishable / comparable defect property pipeline (pydefect path)

## Definition of done (pydefect pipeline slice)
A system can be treated as **publishable-grade formation-energy ready** when:
1. `check_converged` is force-honest (OUTCAR NSW + force gate) — used for efnv eligibility
2. `analyze()` returns honest `full|partial|failed` with `analyze_status.json`
3. Final `defect_energy_summary.json` only when full; partial demoted to `.partial.json`
4. efnv/dei only on ionically converged (or corrected) dirs; shell-safe paths
5. beoi/bes/dsi do not blindly process unconverged junk the same way as efnv (or document why)
6. Missing OUTCAR does not hard-fail the whole system if a partial set is usable
7. `analyze_status.json` exposes actionable counters: n_converged, n_corrected, n_dei, n_unconverged, missing lists, skip reasons
8. `vasp-sop defect analyze` CLI works for one project (not stub)
9. unitcell.yaml build failures are explicit in status (not silent skip only)
10. Unit tests cover: NSW bump FP, force gate, partial demote, efnv skip unconverged, CLI analyze
11. Issues filed for each remaining gap; fixes committed with tests

## Production baseline (2025_undergo_spin_defect, approx)
- 9 full / 23 partial / 8 failed analyze status
- Many partials: converged >> corrected (efnv/dei gap) — e.g. SrS 0/18 corr, orth-SiC 25/155
- MgS: 0 unconverged but only ~7/22 correction → not just waiting on VASP

## Repo
- Package: `/home/duguex/vasp_sop`
- Key files: `vasp_sop/defect/analysis.py`, `vasp_sop/defect/unitcell.py`, `vasp_sop/vasp/io.py`, `vasp_sop/cli/main.py`, `vasp_sop/core/job_store.py`
- Issues dir: `issues/0003`–`0009` exist; next free: `0010+`
- Branch: main, ahead of origin; many unstaged changes from prior session (convergence + analyze + jobstore)

## Process constraint
For each problem: **issue → fix → commit**. Prefer small commits per issue.
Do not invent new phase names. Match FEATURES.md / existing pipeline stages.
