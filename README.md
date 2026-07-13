# vasp-sop

**VASP point-defect high-throughput pipeline orchestrator** (v0.1.0, MIT).

Given a chemical formula and optional dopants, drives competing-phase search → chemical-potential diagram → unitcell properties → supercell/defect enumeration → VASP submission → formation-energy analysis.

**Not** a DFT engine, materials database, or Slurm replacement. Submits work through **`crisp`** (or mpirun) and stores results via **[vasp-cache](https://github.com/duguex/vasp-cache)**.

---

## Quick start

```bash
# install (editable)
pip install -e .

# dry-run a project tree (plan.yaml present)
vasp-sop batch run /path/to/project --dry-run

# advance systems for real
vasp-sop batch run /path/to/project

# cache / jobs
vasp-sop cache status --verbose
```

Tests:

```bash
python3 -m pytest tests/
```

---

## Pipeline (short)

| Stage | Meaning |
|-------|---------|
| TARGET | Structure optimization of host |
| COMPETING | Competing phases |
| CPD_POST | Chemical potential diagram post |
| UC_DF | Unitcell props + defect supercells + VASP |
| DONE | Terminal |

Three-wave VASP scheduling and JobStore details: [FEATURES.md](FEATURES.md), [docs/agent-conventions.md](docs/agent-conventions.md).

---

## Documentation roles

| Audience | File | Role |
|----------|------|------|
| **Humans** | This README | Install + one-command start |
| **Coding agents** | [AGENTS.md](AGENTS.md) | **Canonical** agent rules (short) |
| Deep conventions | [docs/agent-conventions.md](docs/agent-conventions.md) | Architecture, patterns, known issues (ex-AGENTS dump) |
| Feature inventory | [FEATURES.md](FEATURES.md) | JobStore, phases, capabilities |
| Project write-up | [PROJECT.md](PROJECT.md) | Longer product narrative |

No root `CLAUDE.md` (by policy: do not invent unless you request scheme A).

---

## Layout (top level)

| Path | Role |
|------|------|
| `vasp_sop/` | Package + CLI |
| `tests/` | pytest suite |
| `docs/` | Agent conventions + other notes |
| `issues/` | Tracked issues |
| `unitcell/` | Unitcell-related assets |
| `libs/` | Vendored/helpers as present |

---

## Related

- Cache backend: `vasp-cache`  
- HPC submit path: `crisp` (see your cluster docs)
