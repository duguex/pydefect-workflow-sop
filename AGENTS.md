# Agent instructions

> Entrypoint only. Prefer repo docs and code over pretraining.  
> **No `CLAUDE.md`** unless the user requests scheme A — do not invent adapter files.  
> **Human start:** [`README.md`](README.md) · architecture depth: [`FEATURES.md`](FEATURES.md), [`PROJECT.md`](PROJECT.md).

## Precedence

1. User’s current explicit message  
2. This file  
3. Linked docs (`docs/agent-conventions.md`, package code)

## Always-on

- **What this is**: **vasp-sop** — VASP **point-defect high-throughput orchestrator** (not a DFT code, not Slurm, not a materials DB). Depends on **vasp-cache**. Submits via **`crisp`** / mpirun.  
- **State machine** (batch): `STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → UNITCELL_DEFECT → COMPLETE` via `_advance_one_system`.
- **Three-wave VASP schedule**: Wave1 structure_opt → Wave2 competing+UC+defects parallel → Wave3 pydefect post.  
- **CLI**: `vasp-sop batch run .` (`--dry-run`), `defect build`, `cache status|query|…`, `materials fetch`.  
- **Config**: `plan.yaml` per project; JobStore (SQLite) for job state; vasp-cache for results cache.  
- **Tests**: `python3 -m pytest tests/` — isolate cache paths; heavy patching of VASP/crisp in unit tests.  
- **Do not invent** new phase names or store layouts — match code + FEATURES.md.  
- **Secrets / MP API**: use env; do not commit keys. Production trees (e.g. `2025_undergo_spin_defect`) stay outside this package tree.  

## Development commands

```bash
pip install -e .   # or project’s install path

python3 -m pytest tests/
python3 -m pytest tests/test_cache.py -v

vasp-sop batch run /path/to/project --dry-run
vasp-sop batch run /path/to/project
vasp-sop cache status --verbose
```

## Read on demand

| When | Read first |
|------|------------|
| **Starting implementation work** | [`docs/next-actions.md`](docs/next-actions.md) — execution order + fix recipes |
| Full architecture, conventions, known issues | [`docs/agent-conventions.md`](docs/agent-conventions.md) |
| Feature inventory / JobStore | [`FEATURES.md`](FEATURES.md) |
| Project narrative | [`PROJECT.md`](PROJECT.md) |
| Human one-pager | [`README.md`](README.md) |
| Open issues | `gh issue list` (GitHub Issues is the single source of truth) |
| Planned | `next.md` |

## Keep in sync

| Topic | Files |
|-------|--------|
| Agent rules | This file only (until user adds CLAUDE) |
| Phase names / CLI | `vasp_sop/` package ↔ FEATURES ↔ this file’s always-on |
| Human README | quick start ↔ real CLI |
