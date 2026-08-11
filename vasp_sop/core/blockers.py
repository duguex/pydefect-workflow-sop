"""Block-reason classification — the tool's self-knowledge (ADR 0007).

``batch blockers`` reports it; any future automation consumes the same
vocabulary.  One calculation directory, one block reason.

Reasons
-------
- ``done``            : converged / task complete — never reported
- ``missing_inputs``  : not input-ready, detail lists the missing files
- ``crashed``         : OUTCAR present but VASP's timing banner missing
- ``unconverged``     : ran to termination, ionic force gate not passed
- ``never_ran``       : inputs present, no OUTCAR, no live job
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_INPUT_FILES = ("INCAR", "POSCAR", "POTCAR", "KPOINTS")
_UC_TASKS = ("band", "dos", "dielectric")


@dataclass(frozen=True)
class Block:
    reason: str
    detail: str = ""
    path: str = ""

    @property
    def finished(self) -> bool:
        return self.reason == "done"


def classify_dir(path: Path, *, task_type: str = "") -> Block:
    """The block reason for one calculation directory *path*."""
    from vasp_sop.vasp.convergence import convergence_verdict
    from vasp_sop.vasp.io import input_ready, check_task_complete

    if task_type in _UC_TASKS:
        if check_task_complete(path, task_type):
            return Block("done")
    elif convergence_verdict(path).converged:
        return Block("done")

    if not input_ready(path):
        missing = [n for n in _INPUT_FILES if not (path / n).is_file()]
        return Block("missing_inputs", ",".join(missing))

    outcar = path / "OUTCAR"
    if not outcar.is_file():
        outcar = path / "output" / "OUTCAR"
    if not outcar.is_file():
        return Block("never_ran")

    if task_type in _UC_TASKS:
        # UC tasks: not complete (checked above) and OUTCAR present.
        return Block("crashed" if not _has_timing(outcar) else "unconverged")
    v = convergence_verdict(path)
    if v.reason == "truncated":
        return Block("crashed")
    detail = v.reason
    if v.max_f is not None:
        detail = f"{v.reason},max_f={v.max_f:.4f}"
    return Block("unconverged", detail)


def _has_timing(outcar: Path) -> bool:
    from vasp_sop.vasp.convergence import _tail_text
    tail = _tail_text(outcar, 4096)
    return bool(tail and "General timing and accounting" in tail)


def calc_dirs(system_root: Path) -> list[tuple[Path, str]]:
    """(dir, task_type) for every calculation directory under *system_root*."""
    out: list[tuple[Path, str]] = []
    cpd = system_root / "cpd"
    if cpd.is_dir():
        for pd in cpd.iterdir():
            if pd.is_dir() and pd.name != "combos":
                out.append((pd, ""))
    uc = system_root / "unitcell"
    if uc.is_dir():
        for t in _UC_TASKS:
            td = uc / t
            if td.is_dir():
                out.append((td, t))
    df = system_root / "defect"
    if df.is_dir():
        from vasp_sop.defect import is_valid_defect_dir

        for child in df.iterdir():
            # ADR 0013: excluded defect dirs (anion-cation antisites,
            # defect_new, junk) are never submitted, counted or audited.
            if child.is_dir() and child.name != "perfect" \
                    and is_valid_defect_dir(child):
                out.append((child, ""))
    return out


def scan_system(system_root: Path) -> dict[str, Block]:
    """{relative_dir_path: Block} for every blocked dir (done excluded)."""
    blocks: dict[str, Block] = {}
    for d, task_type in calc_dirs(system_root):
        b = classify_dir(d, task_type=task_type)
        if not b.finished:
            blocks[str(d.relative_to(system_root))] = Block(
                b.reason, b.detail, str(d)
            )
    return blocks