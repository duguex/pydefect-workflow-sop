"""VASP input/output utilities.

Provides a single implementation of common VASP tasks — input generation,
completion checking, convergence validation, and CONTCAR restarts — that
was previously duplicated across multiple pipeline modules.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.jobs import _vasp_input_ready, run_local
from vasp_sop.vasp.convergence import convergence_verdict

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════


def input_ready(path: Path) -> bool:
    """Return True if INCAR, POSCAR, POTCAR, and KPOINTS all exist."""
    return _vasp_input_ready(path)


def prepare_inputs(
    work_dir: Path,
    config: PipelineConfig,
    *,
    kspacing: float = 2.0,
    task_type: str = "",
    extra_uis: str = "",
) -> None:
    """Generate INCAR/POTCAR/KPOINTS via ``vise vs`` if missing.

    Args:
        work_dir: Target calculation directory.
        config: Pipeline configuration (functional, potcar, encut, hubbard_u).
        kspacing: K-point spacing for ``-k`` (default 2.0).
        task_type: Optional ``-t`` value (e.g. ``"defect"``).
        extra_uis: Extra ``-uis`` flags (e.g. ``"SIGMA 0.02 LORBIT 11"``).
    """
    # Single-path SOC handling:
    #   - if inputs already complete: patch (idempotent retrofit) and return
    #   - else: run vise to generate, then patch (vise never sets SOC tags)
    # patch_incar is read-modify-write, so existing non-SOC tags are preserved.
    if input_ready(work_dir):
        logger.debug("VASP input already ready in %s", work_dir)
        if config.soc:
            patch_incar(work_dir, LSORBIT=".TRUE.", ISYM=-1)
        return

    pp_opt = (
        f"--potcar {' '.join(config.potcar_overrides)}"
        if config.potcar_overrides else ""
    )
    encut_opt = f"ENCUT {config.encut}" if config.encut else ""
    # Map generic task names to vise's expected task type values
    _VISE_TASK_MAP = {
        "dielectric": "dielectric_dfpt",
        "band": "band",
        "dos": "dos",
        "structure_opt": "structure_opt",
        "defect": "defect",
    }
    vise_task = _VISE_TASK_MAP.get(task_type, task_type)

    cmd = f"vise vs -x {config.functional} -k {kspacing}"
    if task_type:
        cmd += f" -t {vise_task}"
    if pp_opt:
        cmd += f" {pp_opt}"
    if config.hubbard_u:
        cmd += " --options set_hubbard_u True"
    uis_flags = f"NSW 50 {extra_uis} {encut_opt}".strip()
    if config.hubbard_u and "ISPIN" not in uis_flags:
        uis_flags += " ISPIN 2"
    cmd += f" -uis {uis_flags}"

    run_local(cmd, cwd=work_dir, timeout=300)
    # vise never sets SOC tags — patch AFTER run_local so freshly
    # generated INCAR inherits LSORBIT/ISYM without clobbering other tags.
    if config.soc:
        patch_incar(work_dir, LSORBIT=".TRUE.", ISYM=-1)


def check_complete(path: Path) -> bool:
    """Return True if OUTCAR exists (in *path* or *path*/output/)."""
    return (path / "OUTCAR").is_file() or (path / "output" / "OUTCAR").is_file()




_REQUIRED_UC_OUTPUTS: dict[str, list[str]] = {
    "band":       ["OUTCAR", "vasprun.xml"],
    "dos":        ["OUTCAR", "vasprun.xml"],
    "dielectric": ["OUTCAR"],
}


def check_task_complete(path: Path, task_type: str = "") -> bool:
    """Check whether a VASP task's output artifacts are fully present.

    Band/dos tasks require their computed artifacts (OUTCAR + vasprun.xml);
    dielectric requires OUTCAR. The convergence half is delegated to
    :func:`vasp_sop.vasp.convergence.convergence_verdict`, which encodes the
    skip-force rule for band/dos/dielectric (no ionic relaxation → timing
    only) and the NSW/IBRION rule for every other task type.
    """
    # First check required files exist (OUTCAR, vasprun.xml etc.)
    if task_type in _REQUIRED_UC_OUTPUTS:
        for f in _REQUIRED_UC_OUTPUTS[task_type]:
            if (path / f).is_file():
                continue
            if (path / "output" / f).is_file():
                continue
            return False

    return convergence_verdict(path, task_type).converged

def restart_from_contcar(path: Path) -> None:
    """Copy CONTCAR → POSCAR and set ISTART=1 for restart."""
    contcar = path / "CONTCAR"
    if not contcar.is_file():
        return
    shutil.copy2(str(contcar), str(path / "POSCAR"))

    incar = path / "INCAR"
    if not incar.is_file():
        return
    text = incar.read_text()
    lines = text.splitlines()
    new_lines = []
    has_istart = False
    for line in lines:
        if line.strip().startswith("ISTART"):
            new_lines.append("ISTART = 1")
            has_istart = True
        elif line.strip().startswith("NSW"):
            new_lines.append(line)
        else:
            new_lines.append(line)
    if not has_istart:
        new_lines.append("ISTART = 1")
    incar.write_text("\n".join(new_lines) + "\n")

def has_vasprun(path: Path) -> bool:
    """True if vasprun.xml exists at *path* or path/output/."""
    return (path / "vasprun.xml").is_file() or (
        path / "output" / "vasprun.xml"
    ).is_file()


def recover_vasprun_artifacts(path: Path) -> bool:
    """Surface vasprun.xml: legacy ``output/`` promote. Return if present.

    Current crisp writes into *path* directly; ``move_crisp_outputs`` is a no-op
    unless a legacy ``output/`` tree still exists.
    """
    from vasp_sop.core.jobs import move_crisp_outputs

    move_crisp_outputs(path)
    return has_vasprun(path)


def prepare_vasprun_recovery_run(path: Path) -> bool:
    """Prep resubmit for missing vasprun.xml (#0016).

    Policy (user): **do not change calculation parameters** on re-run.
    Only CONTCAR → POSCAR and ISTART=1 when CONTCAR exists.

    Returns True if inputs look submittable afterward.
    """
    contcar = path / "CONTCAR"
    if contcar.is_file():
        restart_from_contcar(path)
    return input_ready(path)


# ══════════════════════════════════════════════════════════════════════════
# INCAR patching helpers
# ══════════════════════════════════════════════════════════════════════════


def read_incar(path: Path) -> dict[str, str]:
    """Read an INCAR file into a dict of {TAG: value_string}.

    Handles ``TAG = value`` and ``TAG value`` formats.  Comments (``#``,
    ``!``) and blank lines are skipped.  Returns an empty dict if the file
    does not exist.
    """
    incar_path = Path(path) / "INCAR" if path.is_dir() else Path(path)
    if not incar_path.is_file():
        return {}
    params: dict[str, str] = {}
    for line in incar_path.read_text().splitlines():
        line = line.split("#")[0].split("!")[0].strip()
        if not line:
            continue
        if "=" in line:
            key, _, val = line.partition("=")
        else:
            parts = line.split(None, 1)
            if len(parts) == 2:
                key, val = parts
            else:
                continue
        params[key.strip().upper()] = val.strip()
    return params


def write_incar(path: Path, params: dict[str, str]) -> None:
    """Write a dict of INCAR parameters to file.

    Args:
        path: Directory containing INCAR, or direct path to INCAR file.
        params: Mapping of TAG -> value (will be formatted as ``TAG = value``).
    """
    incar_path = Path(path) / "INCAR" if path.is_dir() else Path(path)
    lines = [f"{k} = {v}" for k, v in params.items()]
    incar_path.write_text("\n".join(lines) + "\n")


def patch_incar(path: Path, **kwargs: str | int | float) -> None:
    """Read-modify-write INCAR: update only the specified tags.

    Args:
        path: Directory containing INCAR, or direct path to INCAR file.
        **kwargs: Tag-value pairs to set (values converted to str).
    """
    params = read_incar(path)
    for k, v in kwargs.items():
        params[k.upper()] = str(v)
    write_incar(path, params)

