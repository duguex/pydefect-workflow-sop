"""VASP input/output utilities.

Provides a single implementation of common VASP tasks — input generation,
completion checking, convergence validation, and CONTCAR restarts — that
was previously duplicated across multiple pipeline modules.
"""

from __future__ import annotations

import logging
import re as _re
import shutil
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.jobs import _vasp_input_ready, run_local

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
    if input_ready(work_dir):
        logger.debug("VASP input already ready in %s", work_dir)
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


def check_complete(path: Path) -> bool:
    """Return True if OUTCAR exists (in *path* or *path*/output/)."""
    return (path / "OUTCAR").is_file() or (path / "output" / "OUTCAR").is_file()

# mtime-based memoisation: avoid re-reading unchanged OUTCARs across
# successive batch-run cycles.
_check_converged_cache: dict[Path, tuple[float, bool]] = {}
# EDIFFG cached per directory (INCAR rarely changes).
_ediffg_cache: dict[Path, float] = {}


def _tail_text(path: Path, n: int = 4096) -> str | None:
    """Read the last *n* bytes of *path* (no full-file read for large files)."""
    size = path.stat().st_size
    if size <= n:
        return path.read_text()
    with path.open("rb") as f:
        f.seek(size - n)
        return f.read().decode("utf-8", errors="replace")


def _get_ediffg(path: Path) -> float:
    """Return the EDIFFG value from INCAR (cached)."""
    cached = _ediffg_cache.get(path)
    if cached is not None:
        return cached
    incar_path = path / "INCAR"
    efg = 0.03
    if incar_path.is_file():
        head = incar_path.read_text()[:4096]
        m = _re.search(r"EDIFFG\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", head)
        if m:
            efg = abs(float(m.group(1)))
    _ediffg_cache[path] = efg
    return efg


def check_converged(path: Path) -> bool:
    """OUTCAR-based ionic convergence check (tail-read + mtime cache).

    Returns True when an OUTCAR exists, VASP finished with timing info,
    and max ionic force component is below EDIFFG tolerance.
    """
    outcar: Path | None = None
    for cand in (path / "OUTCAR", path / "output" / "OUTCAR"):
        if cand.is_file():
            outcar = cand
            break
    if outcar is None:
        return False

    # mtime cache: unchanged files return cached result
    try:
        mtime = outcar.stat().st_mtime
    except OSError:
        return False
    cached = _check_converged_cache.get(outcar)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    # Tail-read first — most OUTCARs are not converged.
    tail = _tail_text(outcar, n=4096)
    if tail is None:
        _check_converged_cache[outcar] = (mtime, False)
        return False

    if "General timing and accounting" not in tail:
        _check_converged_cache[outcar] = (mtime, False)
        return False

    # Converged — full read for force parsing.
    try:
        text = outcar.read_text()
    except Exception:
        _check_converged_cache[outcar] = (mtime, False)
        return False

    idx = text.rfind("TOTAL-FORCE (eV/Angst)")
    if idx < 0:
        _check_converged_cache[outcar] = (mtime, False)
        return False

    efg = _get_ediffg(path)
    max_f = 0.0
    for line in text[idx:].splitlines()[2:]:
        parts = line.strip().split()
        if len(parts) < 6:
            break
        try:
            max_f = max(max_f, abs(float(parts[3])), abs(float(parts[4])), abs(float(parts[5])))
        except ValueError:
            break

    result = max_f < efg
    _check_converged_cache[outcar] = (mtime, result)
    return result


_REQUIRED_UC_OUTPUTS: dict[str, list[str]] = {
    "band":       ["OUTCAR", "vasprun.xml"],
    "dos":        ["OUTCAR", "vasprun.xml"],
    "dielectric": ["OUTCAR"],
}


def check_task_complete(path: Path, task_type: str = "") -> bool:
    """Check whether a VASP task's output artifacts are fully present.

    For band/dos tasks: requires converged OUTCAR + vasprun.xml.
    For dielectric:     requires OUTCAR with VASP completion (no force check,
                        because dielectric is a DFPT single-point calc).
    For any other task: delegates to check_converged().
    """
    # First check required files exist (OUTCAR, vasprun.xml etc.)
    if task_type in _REQUIRED_UC_OUTPUTS:
        for f in _REQUIRED_UC_OUTPUTS[task_type]:
            if (path / f).is_file():
                continue
            if (path / "output" / f).is_file():
                continue
            return False

    # dielectric: no ionic relaxation, force convergence is N/A
    if task_type == "dielectric":
        outcar = path / "OUTCAR"
        if not outcar.is_file():
            outcar = path / "output" / "OUTCAR"
        if not outcar.is_file():
            return False
        tail = _tail_text(outcar, 4096)
        return tail is not None and "General timing and accounting" in tail

    # band/dos/other: require force convergence
    if not check_converged(path):
        return False
    return True

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
