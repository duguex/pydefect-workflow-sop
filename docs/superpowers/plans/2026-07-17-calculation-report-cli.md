# Automatic Calculation Report CLI Implementation Plan

> **For agentic workers:** Execute inline in this session with TDD and verification checkpoints.

**Goal:** Add `vasp-sop report <system_dir>` to generate an evidence-based Markdown calculation report from project files rather than copying hand-written prose.

**Architecture:** Add a pure filesystem parser/renderer in `vasp_sop/core/report.py`. It reads `plan.yaml`, unitcell structure and input outputs, CPD directories and YAML artifacts, and emits deterministic Markdown to the system directory or an explicit output path. Each field in the evidence-status table carries value/source/status evidence. The derivation table separately presents stage values and provenance columns. Add a top-level CLI dispatcher in `vasp_sop/cli/main.py`. Missing artifacts are reported as incomplete status, never invented.

**Tech Stack:** Python standard library, existing PyYAML, pymatgen already used by the project, argparse, pytest.

## Global Constraints

- Do not submit jobs or mutate VASP input files.
- Use filesystem/YAML/OUTCAR evidence only; do not copy the existing manual report.
- Preserve existing CLI behavior and sync `FEATURES.md`.
- Test both isolated fixtures and the real `CsEuCl3` production directory.
- Keep defect work out of the report workflow.

### Task 1: Report data extraction and rendering

**Files:**
- Create: `vasp_sop/core/report.py`
- Test: `tests/test_report.py`

**Steps:**
- Write tests for plan parsing, converged OUTCAR detection, POTCAR/INCAR/KPOINTS/ENCUT extraction, CPD phase inventory, target composition source, derivation rows, correction-key audit, and missing-artifact status.
- Implement deterministic helpers that return a report data structure and render Markdown.
- Parse formulas and output artifacts from actual files; recognize target composition by reduced composition/source, not literal key only.
- Show the VASP-to-CPD chain: OUTCAR final energy and CONTCAR composition, mce YAML value, normalization, correction audit, relative/standard energies, and chemical-potential vertices with units and sources.
- Include explicit statuses `已读取`, `未找到`, `不适用`, `未执行`, and `不采集`.

### Task 2: CLI command

**Files:**
- Modify: `vasp_sop/cli/main.py`
- Test: `tests/test_cli.py` or `tests/test_report.py`

**Steps:**
- Add top-level `report` parser with positional `system_dir` and optional `--output`.
- Dispatch to the report generator and print the generated path.
- Ensure command is read-only apart from writing the requested report file.

### Task 3: Documentation and issue

**Files:**
- Modify: `FEATURES.md`
- Create: `issues/0027-calculation-report-cli.md`

**Steps:**
- Add the report command to the CLI table and describe its evidence sources, derivation chain, and non-submission behavior.
- Record the original manual-report gap, evidence contract, correction-key risk, and known limitations in the issue.

### Task 4: Verification

**Commands:**
- `python3 -m pytest tests/test_report.py -q`
- `vasp-sop report /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/CsEuCl3 --output /tmp/CsEuCl3.generated-report.md`
- Compare generated report facts against `CsEuCl3/cpd/*.yaml`, `unitcell/structure_opt/OUTCAR`, `INCAR`, `KPOINTS`, and `POTCAR`.

**Acceptance:**
- Fixture tests pass without production paths.
- Real-system command succeeds and writes a fresh report.
- Generated report includes actual target energy source, 8 CPD directories, 4 target vertices, convergence status, and magnetic warning.
- No VASP/crisp submission occurs.
