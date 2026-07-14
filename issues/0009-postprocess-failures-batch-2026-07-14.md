# Batch post-process failures (2026-07-14 cycle)

**Date:** 2026-07-14  
**Severity:** High  
**Log:** `/tmp/batch_run_3.log`

## Context

After perfect resubmits (32 systems with `vasprun.xml`), full `vasp-sop batch run` completed one cycle:

```
COMPLETE=8  STRUCTURE_OPT=2  UNITCELL_DEFECT=30
```

Several systems printed `✓ … pipeline complete` but remain `UNITCELL_DEFECT` (see #0007). Others hard-failed mid-analyze.

## Hard failures observed

| System | Failing step | Detail |
|--------|--------------|--------|
| CaSe | `pydefect des` | most dirs: "No such file or directory" while parsing defect energy infos (missing dei intermediates) |
| GeSe2 | `pydefect des` | same as CaSe |
| SeO2 | `pydefect efnv` | argparse / invocation failure (uncorrected empty set / bad args after glob) |
| Sn(SeO3)2 | `pydefect efnv` | **shell metacharacters**: unquoted path with `(` → `/bin/sh: Syntax error` |
| SrGe4O9 | `pydefect_vasp pbes` | `vasprun.xml` XML ParseError (truncated/corrupt) |
| BaO2 | `pydefect pe` (earlier) | empty energy sequence after skipping all uncorrected defects |

### Sn(SeO3)2 shell bug (code)

`run_local` uses shell; absolute paths with `(` must be quoted.

**Fix applied (2026-07-14):** `shlex.quote` on path arguments in `vasp_sop/defect/analysis.py`.

## Soft / partial “success”

| Systems | Note |
|---------|------|
| BaGe2S5, BaS, BaS3, BaSe, Mg3TeO6, MgS, SrS, orth-SiC, … | `pipeline complete` + summary often on disk; many defects skipped missing `correction.json` → phase stays UNITCELL_DEFECT (#0007) |

## Still waiting on VASP

| System | Action this cycle |
|--------|-------------------|
| BaGe4O9 | dielectric re-submit |
| SrTe, diamond, hBN | band (and related) re-submit |
| ZnO | target CONTCAR restart submitted after POTCAR regen (#0008) |
| CaMg2(SO4)3 | 11 CPD/target jobs submitted (#0008) |

## Related issues

- #0006 cache put blocks advance (mitigated: orphan path only caches converged)
- #0007 partial correction / false complete
- #0008 ZnO / CaMg2 STRUCTURE_OPT
