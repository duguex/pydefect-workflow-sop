# Next directions

## 1. Run production batch
```bash
vasp-sop batch run /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
```

## 2. vasp-incar integration
Import INCAR tag knowledge from `/home/duguex/vasp_incar` into `_extract_tags` for richer semantic tags.

## 3. ZnO investigation
ZnO is stuck at CPD_POST but missing `target_vertices.yaml` — check what's wrong.

## 4. Test coverage
- [ ] `_extract_tags` with Line_mode / band-structure KPOINTS
- [ ] `_extract_tags` with combined INCAR+KPOINTS+structure
- [ ] `_extract_tags` with space group from sga
- [ ] INCAR tags: SCAN, PBEsol, phonon, dielectric, high-encut, low-encut
- [ ] `cache put -r` recursive scanning
- [ ] `cache put --formula --task-id` explicit args
- [ ] `vasp_results_put` with only formula or only task_id (partial auto-detect)
