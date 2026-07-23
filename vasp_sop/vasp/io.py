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

# mtime-based memoisation: avoid re-reading unchanged OUTCARs across
# successive batch-run cycles.
_check_converged_cache: dict[Path, tuple[float, bool]] = {}

_NSW_RE = _re.compile(r"NSW\s*=\s*(\d+)", _re.I)
_IBRION_RE = _re.compile(r"IBRION\s*=\s*(-?\d+)", _re.I)
_EDIFFG_RE = _re.compile(
    r"EDIFFG\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", _re.I
)
_FORCE_HDR = "TOTAL-FORCE (eV/Angst)"
_TIMING_MARK = "General timing and accounting"


def _tail_text(path: Path, n: int = 4096) -> str | None:
    """Read the last *n* bytes of *path* (no full-file read for large files)."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= n:
        try:
            return path.read_text()
        except OSError:
            return None
    try:
        with path.open("rb") as f:
            f.seek(size - n)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _head_text(path: Path, n: int = 65536) -> str:
    """Read the first *n* bytes of *path*."""
    try:
        with path.open("rb") as f:
            return f.read(n).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _parse_run_tags(text: str) -> tuple[int | None, int | None, float | None]:
    """Return (nsw, ibrion, ediffg_signed) from VASP tag text (OUTCAR/INCAR)."""
    nsw = ibrion = None
    ediffg: float | None = None
    m = list(_NSW_RE.finditer(text))
    if m:
        nsw = int(m[-1].group(1))
    m = list(_IBRION_RE.finditer(text))
    if m:
        ibrion = int(m[-1].group(1))
    m = list(_EDIFFG_RE.finditer(text))
    if m:
        ediffg = float(m[-1].group(1))
    return nsw, ibrion, ediffg


def _count_total_force_blocks(outcar: Path) -> int:
    """Count TOTAL-FORCE headers (≈ ionic steps) without loading whole file."""
    needle = _FORCE_HDR.encode("ascii")
    n = 0
    try:
        with outcar.open("rb") as f:
            buf = b""
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                data = buf + chunk
                n += data.count(needle)
                buf = data[-(len(needle) - 1) :] if len(needle) > 1 else b""
    except OSError:
        return 0
    return n


def _last_max_force(outcar: Path) -> float | None:
    """Max |F| on atoms from the last TOTAL-FORCE block (eV/Å)."""
    # Prefer a large tail window so the final ionic block is included.
    try:
        size = outcar.stat().st_size
    except OSError:
        return None
    window = min(size, 2_500_000)
    text = _tail_text(outcar, n=window) if window else None
    if not text:
        return None
    idx = text.rfind(_FORCE_HDR)
    if idx < 0:
        return None
    block = text[idx + len(_FORCE_HDR) :]
    # Drop dashed separator line(s)
    lines = block.splitlines()
    forces: list[float] = []
    started = False
    for line in lines:
        s = line.strip()
        if not s:
            if started:
                break
            continue
        if set(s) <= {"-"}:
            started = True
            continue
        if s.lower().startswith("total drift"):
            break
        if not started:
            # synthetic OUTCARs may omit the dashed line
            started = True
        parts = s.split()
        if len(parts) < 6:
            if forces:
                break
            continue
        try:
            fx, fy, fz = float(parts[3]), float(parts[4]), float(parts[5])
        except ValueError:
            if forces:
                break
            continue
        forces.append((fx * fx + fy * fy + fz * fz) ** 0.5)
    if not forces:
        return None
    return max(forces)


def parse_max_force(path: Path) -> float:
    """Extract the maximum ionic force component from OUTCAR.

    Looks for OUTCAR in *path* or *path*/output/, finds the last
    TOTAL-FORCE block, and returns the largest absolute force component.
    Returns -1.0 if the OUTCAR or force block is unavailable (callers
    should treat negative values as "no data").
    """
    outcar: Path | None = None
    for cand in (path / "OUTCAR", path / "output" / "OUTCAR"):
        if cand.is_file():
            outcar = cand
            break
    if outcar is None:
        return -1.0

    try:
        text = outcar.read_text()
    except Exception:
        return -1.0

    idx = text.rfind(_FORCE_HDR)
    if idx < 0:
        return -1.0

    max_f = 0.0
    for line in text[idx:].splitlines()[2:]:
        parts = line.strip().split()
        if len(parts) < 6:
            break
        try:
            max_f = max(max_f, abs(float(parts[3])), abs(float(parts[4])), abs(float(parts[5])))
        except ValueError:
            break
    return max_f


def check_converged(path: Path) -> bool:
    """Ionic / job-completion check for a VASP calculation directory.

    Rules
    -----
    1. OUTCAR must exist and contain ``General timing and accounting``
       (VASP finished writing results).
    2. Run parameters prefer **OUTCAR** (this job) over **INCAR** (may already
       be edited for a CONTCAR restart / NSW bump).
    3. Single-point / DFPT / MD (``NSW≤1`` or ``IBRION∉{1,2,3}``): timing only.
    4. Ionic relaxation (``IBRION∈{1,2,3}`` and ``NSW>1``):
       - If ``EDIFFG < 0`` (force criterion): **hard gate**
         ``max|F| ≤ |EDIFFG|`` on the last TOTAL-FORCE block.
       - Else (energy criterion / missing forces): fall back to
         ``n_ionic < NSW_run`` (pymatgen-style early-exit heuristic).

    Never raises.
    """
    outcar: Path | None = None
    for cand in (path / "OUTCAR", path / "output" / "OUTCAR"):
        if cand.is_file():
            outcar = cand
            break
    if outcar is None:
        return False

    try:
        mtime = outcar.stat().st_mtime
    except OSError:
        return False
    cached = _check_converged_cache.get(outcar)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    tail = _tail_text(outcar, n=8192)
    if tail is None or _TIMING_MARK not in tail:
        _check_converged_cache[outcar] = (mtime, False)
        return False

    # Prefer OUTCAR tags (run that produced this OUTCAR); INCAR is fallback only.
    head = _head_text(outcar, 65536)
    nsw, ibrion, ediffg = _parse_run_tags(head)
    # OUTCAR sometimes prints tags late in the file (rare); also search tail.
    if nsw is None or ibrion is None or ediffg is None:
        t_nsw, t_ibr, t_ed = _parse_run_tags(tail)
        if nsw is None:
            nsw = t_nsw
        if ibrion is None:
            ibrion = t_ibr
        if ediffg is None:
            ediffg = t_ed

    incar_path = path / "INCAR"
    if incar_path.is_file() and (nsw is None or ibrion is None or ediffg is None):
        try:
            incar_text = incar_path.read_text()[:8192]
        except OSError:
            incar_text = ""
        i_nsw, i_ibr, i_ed = _parse_run_tags(incar_text)
        if nsw is None:
            nsw = i_nsw
        if ibrion is None:
            ibrion = i_ibr
        if ediffg is None:
            ediffg = i_ed

    if nsw is None:
        nsw = 0
    if ibrion is None:
        ibrion = -1

    # Single-point / DFPT / MD / unknown-static: finished job is enough
    if nsw <= 1 or ibrion not in (1, 2, 3):
        _check_converged_cache[outcar] = (mtime, True)
        return True

    # --- Ionic relaxation ---
    max_f = _last_max_force(outcar)

    # Force criterion (EDIFFG < 0): hard gate — eliminates NSW-bump FP
    if ediffg is not None and ediffg < 0:
        if max_f is None:
            result = False
        else:
            result = max_f <= abs(ediffg) + 1e-8
        _check_converged_cache[outcar] = (mtime, result)
        return result

    # Energy criterion or missing EDIFFG: NSW early-exit with *run* NSW
    n_ionic = _count_total_force_blocks(outcar)
    if n_ionic < 1:
        result = False
    else:
        result = n_ionic < nsw
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

def has_vasprun(path: Path) -> bool:
    """True if vasprun.xml exists at *path* or path/output/."""
    return (path / "vasprun.xml").is_file() or (
        path / "output" / "vasprun.xml"
    ).is_file()


def recover_vasprun_artifacts(path: Path) -> bool:
    """Surface vasprun.xml: legacy ``output/`` promote + cache. Return if present.

    Current crisp writes into *path* directly; ``move_crisp_outputs`` is a no-op
    unless a legacy ``output/`` tree still exists.
    """
    from vasp_sop.core.jobs import move_crisp_outputs
    from vasp_sop.core.cache import restore_from_cache

    move_crisp_outputs(path)
    if has_vasprun(path):
        return True
    try:
        restore_from_cache(path)
    except Exception:
        pass
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

