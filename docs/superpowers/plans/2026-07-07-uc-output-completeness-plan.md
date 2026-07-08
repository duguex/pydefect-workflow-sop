# UC Output Completeness Auto-Repair — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add output-completeness detection to UC_DF VASP submission loop so band/dos tasks missing vasprun.xml are re-submitted automatically.

**Architecture:** One new function `check_task_complete()` in `vasp_sop/vasp/io.py` replaces `check_converged()` call in the UC_DF submission loop in `vasp_sop/cli/main.py`. Band/dos require converged OUTCAR + vasprun.xml; dielectric requires converged OUTCAR only.

**Tech Stack:** Python 3.10+, vasp-sop, pytest

## Global Constraints

- No new dependencies
- Existing `check_converged()` signature unchanged
- Defect-task loop unaffected
- All names: `snake_case`

---

### Task 1: Add `check_task_complete()` to `vasp_sop/vasp/io.py`

**Files:**
- Modify: `vasp_sop/vasp/io.py` — add function + required-outputs dict after `check_converged()` (after line 168)

**Interfaces:**
- Produces: `check_task_complete(path: Path, task_type: str = "") -> bool`
  - Returns True only when all required output files for the given task_type exist AND OUTCAR is converged
  - For `"band"`: OUTCAR (converged) + vasprun.xml
  - For `"dos"`: OUTCAR (converged) + vasprun.xml
  - For `"dielectric"`: OUTCAR (converged) only
  - For any other `task_type` (including `""`): delegates to `check_converged(path)`

- [ ] **Step 1: Add the required-outputs dict and function**

```python
_REQUIRED_UC_OUTPUTS: dict[str, list[str]] = {
    "band":       ["OUTCAR", "vasprun.xml"],
    "dos":        ["OUTCAR", "vasprun.xml"],
    "dielectric": ["OUTCAR"],
}


def check_task_complete(path: Path, task_type: str = "") -> bool:
    """Check whether a VASP task's output artifacts are fully present.

    For band/dos tasks: requires converged OUTCAR + vasprun.xml.
    For dielectric:     requires converged OUTCAR only.
    For any other task: delegates to check_converged().
    """
    if not check_converged(path):
        return False
    if task_type in _REQUIRED_UC_OUTPUTS:
        return all((path / f).is_file() for f in _REQUIRED_UC_OUTPUTS[task_type])
    return True
```

Insert after line 168 (end of `check_converged()` function) in `vasp_sop/vasp/io.py`.

- [ ] **Step 2: Verify it imports cleanly**

```bash
python3 -c "from vasp_sop.vasp.io import check_task_complete; print('ok')"
```

Expected: prints `ok`

---

### Task 2: Replace `check_converged` with `check_task_complete` in UC_DF loop

**Files:**
- Modify: `vasp_sop/cli/main.py` line 1178

**Interfaces:**
- Consumes: `check_task_complete(path, task_type)` from Task 1

- [ ] **Step 1: Change the convergence check in the UC task loop**

Current (line 1178):
```python
                if check_converged(task_dir):
```

Replace with:
```python
                from vasp_sop.vasp.io import check_task_complete
                if check_task_complete(task_dir, task):
```

This is inside the loop `for task in ("band", "dos", "dielectric"):`. The import is local (function-scoped) to match existing patterns in `_advance_one_system`.

- [ ] **Step 2: Verify the file parses correctly**

```bash
python3 -c "import ast; ast.parse(open('vasp_sop/cli/main.py').read()); print('syntax ok')"
```

Expected: prints `syntax ok`

---

### Task 3: Test `check_task_complete()`

**Files:**
- Create: `tests/test_io.py`

**Interfaces:**
- Tests: `check_task_complete()` from Task 1

- [ ] **Step 1: Write and run tests**

```python
"""Tests for vasp_sop.vasp.io — check_converged, check_task_complete."""

from pathlib import Path
import pytest


def _write_converged_outcar(d: Path) -> None:
    """OUTCAR that satisfies check_converged."""
    text = (
        " General timing and accounting\n"
        "   100.00% CPU utilisation\n"
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
    )
    (d / "OUTCAR").write_text(text)


def _write_incar(d: Path) -> None:
    (d / "INCAR").write_text("SYSTEM = test\n")


class TestCheckTaskComplete:
    """check_task_complete: output-completeness per task type."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.dir = tmp_path / "task"
        self.dir.mkdir()
        _write_incar(self.dir)

    def test_band_with_vasprxml(self):
        """band: converged OUTCAR + vasprun.xml → True."""
        _write_converged_outcar(self.dir)
        (self.dir / "vasprun.xml").write_text("<vasprun></vasprun>")
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "band")

    def test_band_without_vasprxml(self):
        """band: converged OUTCAR only → False (missing vasprun.xml)."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_band_unconverged(self):
        """band: unconverged OUTCAR → False regardless of vasprun.xml."""
        (self.dir / "OUTCAR").write_text("some header\n")
        (self.dir / "vasprun.xml").write_text("<vasprun></vasprun>")
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_band_no_output(self):
        """band: no OUTCAR at all → False."""
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_dos_missing_vasprxml(self):
        """dos: converged OUTCAR only → False."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "dos")

    def test_dielectric_without_vasprxml(self):
        """dielectric: converged OUTCAR only → True (no vasprun.xml needed)."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "dielectric")

    def test_default_task_type(self):
        """default (task_type=""): delegates to check_converged — converged → True."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir)

    def test_unknown_task_type(self):
        """unknown task_type: delegates to check_converged."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "phonon")
```

Run:
```bash
python3 -m pytest tests/test_io.py -v
```

Expected: 9 passed

---

### Task 4: Update walkthrough test to verify auto-repair

**Files:**
- Modify: `tests/test_cli.py` — in `TestFullPipelineWalkthrough`, add assertion that UC tasks without vasprun.xml are submitted despite converged OUTCAR

- [ ] **Step 1: Add a sub-test for missing vasprun.xml → re-submit**

Inside `TestFullPipelineWalkthrough`, add a new test method:

```python
def test_uc_resubmit_when_vasprxml_missing(self, tmp_path, monkeypatch):
    """UC task with converged OUTCAR but missing vasprun.xml → re-submitted."""
    from vasp_sop.cli.main import _phase, _advance_one_system

    root = self._make_system(tmp_path, "GaN", "804")
    cpd = root / "cpd"
    (cpd / "target_vertices.yaml").write_text("tv: 1\n")
    (cpd / "standard_energies.yaml").write_text("se: 1\n")

    # Create UC dirs with CONVERGED OUTCAR but NO vasprun.xml
    uc = root / "unitcell"
    uc.mkdir()
    for t in ("band", "dos", "dielectric"):
        td = uc / t
        td.mkdir()
        _write_incar(td)
        self._write_converged_outcar(td)

    submit_calls: list[str] = []
    monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                       lambda p: (submit_calls.append(str(p.resolve())) or
                                  type("J", (), {"task_name": "t"})()))
    cache_data: dict[str, dict] = {}
    # Cache only the target (so phase advances past COMPETING)
    td = root / "cpd" / "GaN_mp-804"
    cache_data[str(td.resolve())] = {"total_energy": -12.0}
    cache_data["GaN_804"] = {"total_energy": -12.0}
    monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                       lambda p: cache_data.get(str(p.resolve())))

    s = _make_system_dict(root)
    _advance_one_system(s, dry_run=False)

    # band and dos should be re-submitted (missing vasprun.xml)
    assert str((uc / "band").resolve()) in submit_calls, \
        "band should re-submit (no vasprun.xml)"
    assert str((uc / "dos").resolve()) in submit_calls, \
        "dos should re-submit (no vasprun.xml)"
    # dielectric should NOT be re-submitted (OUTCAR only is sufficient)
    assert str((uc / "dielectric").resolve()) not in submit_calls, \
        "dielectric should not re-submit (OUTCAR sufficient)"
```

- [ ] **Step 2: Run the updated walkthrough test**

```bash
python3 -m pytest tests/test_cli.py::TestFullPipelineWalkthrough -v
```

Expected: 2 passed

---

### Task 5: Run full test suite

- [ ] **Step 1: Run all tests**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass (count increased by the new tests)

- [ ] **Step 2: Run production test (optional, requires VASP_SOP_PROD_DIR)**

```bash
VASP_SOP_PROD_DIR=/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect \
  python3 -m pytest tests/test_production.py -v -k "not orphan"
```

Expected: hBN's missing unitcell.yaml issue persists until vasprun.xml is retrieved; the code fix prevents new systems from hitting this state
