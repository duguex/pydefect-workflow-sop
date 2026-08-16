---
name: poscar-backup-policy-audit
description: Use when checking whether VASP relaxation inputs are uniformly archived or only backed up during cache prefill.
---

## Scope

Distinguish cache-prefill protection backups from an all-calculation provenance policy.

## Current crisp behavior

- `POSCAR.bak` is created only by the VASP cache prefill path.
- Preconditions: `crisp submit`, calculator `vasp`, vasp-cache installed, cache identity hit, `vasp_cache.has()` convergence gate passes, no `--skip-prefill`, and no existing `POSCAR.bak`.
- On a hit, preserve the original `POSCAR` as `POSCAR.bak`; write cached `CONTCAR` to both `POSCAR` and `CONTCAR`.
- A missing cache hit, non-VASP job, disabled prefill, or failed fetch does not create the backup.

## Audit conclusion

Never claim that `POSCAR.bak` provides universal relaxation provenance. If every relaxation must be reproducible, design and verify a separate unconditional input snapshot policy, such as `POSCAR.initial` or `POSCAR.original`, with explicit overwrite/restart semantics and tests.
