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
from vasp_sop.vasp.protocol import (
    U_TABLE,
    effective_encut,
    magmom_values,
    protocol_tags,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════


def input_ready(path: Path) -> bool:
    """Return True if INCAR, POSCAR, POTCAR, and KPOINTS all exist."""
    return _vasp_input_ready(path)


def _strip_incar_tags(path: Path, *tags: str) -> None:
    """Remove INCAR lines whose tag matches any of *tags* (case-insensitive)."""
    incar = path / "INCAR"
    if not incar.is_file():
        return
    wanted = {t.upper() for t in tags}
    kept = []
    for line in incar.read_text().splitlines():
        tag = line.strip().split()[0].rstrip("=").strip().upper() if line.strip() else ""
        if tag in wanted:
            continue
        kept.append(line)
    incar.write_text("\n".join(kept) + "\n")


def _apply_soc_tags(work_dir: Path, config: PipelineConfig, task_type: str) -> None:
    """Apply INCAR protocol for SOC systems and DFPT tasks.

    Dielectric (DFPT, IBRION=8/LEPSILON) has a fixed protocol regardless
    of the plan ``soc`` flag:
    - **NSW=1**: DFPT is a single-step perturbative evaluation — NSW>1
      re-runs the linear response every ionic step (50x compute waste).
      vise's template default is 1; the batch CLI path overrides it to 50
      (regression 2026-08-10, La2Zr2O7) and this heals it back.
    - **LREAL=.FALSE.**: VASP strongly recommends it for DFPT (real-space
      projection is insufficiently accurate).
    - **no LSORBIT/ISYM**: VASP's DFPT does not support spin-orbit
      coupling — the spinor wavefunction doubles memory (rank-0
      allocation failures) and the linear-response kernel errors out
      (LRF_COMMUTATOR internal error).
    All other SOC-system tasks get LSORBIT/ISYM.
    """
    if task_type == "dielectric":
        patch_incar(work_dir, NSW=1, LREAL=".FALSE.")
        _strip_incar_tags(work_dir, "LSORBIT", "ISYM")
        return
    if not getattr(config, "soc", False) or getattr(config, "stage2_soc", False):
        return
    patch_incar(work_dir, LSORBIT=".TRUE.", ISYM=-1)


def prepare_inputs(
    work_dir: Path,
    config: PipelineConfig,
    *,
    kspacing: float = 2.0,
    task_type: str = "",
    extra_uis: str = "",
    charge: float | None = None,
) -> None:
    """Generate INCAR/POTCAR/KPOINTS via vise if missing.

    Args:
        work_dir: Target calculation directory.
        config: Pipeline configuration (functional, potcar, encut, hubbard_u).
        kspacing: K-point spacing for ``-k`` (default 2.0).
        task_type: Optional ``-t`` value (e.g. ``"defect"``).
        extra_uis: Extra ``-uis`` flags (e.g. ``"SIGMA 0.02 LORBIT 11"``).
        charge: Defect charge state; when given, INCAR is generated through
            vise's Python API with ``charge=q`` so NELECT is computed by
            vise itself (Σ N_i·ZVAL_i − q, ZVAL read from POTCAR) instead of
            the removed hardcoded ``_fix_defect_nelect`` patch (vise owns
            NELECT — ADR 0007 input restore).
    """
    # Single-path SOC handling:
    #   - if inputs already complete: patch (idempotent retrofit) and return
    #   - else: run vise to generate, then patch (vise never sets SOC tags)
    # patch_incar is read-modify-write, so existing non-SOC tags are preserved.
    if input_ready(work_dir):
        logger.debug("VASP input already ready in %s", work_dir)
        _apply_soc_tags(work_dir, config, task_type)
        return

    if charge is not None:
        _prepare_inputs_vise_api(work_dir, config, kspacing, task_type,
                                 charge, extra_uis=extra_uis)
        return

    pp_opt = (
        f"--potcar {' '.join(config.potcar_overrides)}"
        if config.potcar_overrides else ""
    )
    # ENCUT 永不落入 vise 模板默认(漂移来源):plan/config 显式值优先,
    # 否则按本目录 POTCAR 的 1.3×max ENMAX 检测(协议模块单一规则)。
    encut_val = effective_encut(config, work_dir)
    encut_opt = f"ENCUT {encut_val}" if encut_val else ""
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
    # DFT+U always on (ADR 0012): vise auto-adapts per element — U-table
    # elements (3d Mn-Ni, Cu, Zn, lanthanides) get U, others none.
    cmd += " --options set_hubbard_u True"
    # 协议表单一来源(ADR 0024):NSW/NELM/EDIFF/EDIFFG/SIGMA/LORBIT 按腿
    # 取数;extra_uis 先拼、协议表在后(同 key 以协议表为准)。
    tags = protocol_tags(task_type)
    flags = " ".join(f"{k} {v}" for k, v in tags.items())
    uis_flags = f"{extra_uis} {flags} {encut_opt}".strip()
    cmd += f" -uis {uis_flags}"

    # vise vs 无条件重写 POTCAR(用 vise 内置 potcar_set,如 Ga→裸 Ga
    # 08Apr2002)——破坏 PSP 库变体协议(Ga_d)。备份并在生成后恢复,
    # 保持变体 = 预置版本;ENCUT 由 vise 基于原 POTCAR 计算,恢复后自洽。
    potcar_path = work_dir / "POTCAR"
    potcar_backup = potcar_path.read_bytes() if potcar_path.is_file() else None

    run_local(cmd, cwd=work_dir, timeout=300)
    if potcar_backup is not None:
        potcar_path.write_bytes(potcar_backup)
    # vise never sets SOC tags — patch AFTER run_local so freshly
    # generated INCAR inherits LSORBIT/ISYM without clobbering other tags.
    _apply_soc_tags(work_dir, config, task_type)
    # vise 0.9.5 会丢 NELM/EDIFF(belt-and-braces):按协议表补回,保证落盘
    # INCAR 与声明一致(cpd/band/dos/dielectric/structure_opt 同源)。
    fallback = {k: v for k, v in tags.items() if k in ("NELM", "EDIFF")}
    if tags.get("EDIFFG") is not None:
        fallback["EDIFFG"] = tags["EDIFFG"]
    patch_incar(work_dir, **fallback)
    # DFT+U: vise CLI applies set_hubbard_u to defect tasks only; patch
    # U-table species (incl. Ti, missing from the libs/vise fork's table)
    # for cpd/band/dos/dielectric too — idempotent.
    patch_incar_u(work_dir)
    patch_incar_magmom(work_dir)


def _prepare_inputs_vise_api(
    work_dir: Path,
    config: PipelineConfig,
    kspacing: float,
    task_type: str,
    charge: float,
    extra_uis: str = "",
) -> None:
    """Generate VASP inputs through vise's Python API with *charge*.

    The CLI (``vise vs``) has no charge flag; the API's
    ``CategorizedInputOptions`` accepts it and ``IncarSettingsGenerator``
    computes NELECT = Σ N_i·ZVAL_i − q from the POTCAR ZVALs.  This is
    the sanctioned path for defect directories (vise owns NELECT).

    Neutral (q=0) dirs get no NELECT line — VASP's default (Σ ZVAL from
    POSCAR+POTCAR) is exactly the correct electron count; writing 0 would
    be wrong.  Only charged dirs carry NELECT.

    The API path must mirror the CLI path's options, or the generated
    INCAR silently regresses to vise's template defaults.  Historically
    this dropped NSW (50 → 20, forcing 3-5 restart rounds), the
    ``extra_uis`` flags (SIGMA 0.02 / LORBIT 11, smearings that matter
    for defect occupancies) and ``hubbard_u`` (Gd/Fe systems).  All are
    restored here: ``overridden_incar_settings`` covers the free-form
    tags, ``set_hubbard_u`` and ``cutoff_energy`` are first-class
    ``IncarSettingsGenerator`` options.
    """
    from vise.input_set.input_options import CategorizedInputOptions
    from vise.input_set.vasp_input_files import VaspInputFiles
    from vise.input_set.task import Task
    from vise.input_set.xc import Xc
    from pymatgen.core import Structure

    _VISE_TASK_MAP = {
        "dielectric": "dielectric_dfpt",
        "band": "band",
        "dos": "dos",
        "structure_opt": "structure_opt",
        "defect": "defect",
    }
    vise_task = _VISE_TASK_MAP.get(task_type, task_type)

    # 协议表单一来源(ADR 0024):NSW/NELM/EDIFF/EDIFFG/SIGMA/LORBIT 按腿
    # 取(defect cap:NELM=30 典型胞 16-30 步收敛,ADR 0016 门检 NELM 耗
    # 尽);extra_uis 显式覆盖在后(调用点可抬高占据展宽)。
    tags = protocol_tags(task_type)
    overrides: dict[str, str] = {k: str(v) for k, v in tags.items()}
    tokens = extra_uis.split()
    for i in range(0, len(tokens) - 1, 2):
        overrides[tokens[i]] = tokens[i + 1]

    structure = Structure.from_file(str(work_dir / "POSCAR"))
    options = CategorizedInputOptions(
        structure=structure,
        task=Task(vise_task),
        xc=Xc.from_string(config.functional),
        kpt_density=kspacing,
        charge=charge,
        # DFT+U always on (ADR 0012): vise auto-adapts by element.
        set_hubbard_u=True,
        # ENCUT 永不落入 vise 模板默认(漂移来源):plan/config 优先,
        # 否则按本目录 POTCAR 检测(协议模块单一规则)。
        cutoff_energy=effective_encut(config, work_dir),
    )
    # 同 CLI 路径:vise 无条件重写 POTCAR——备份并恢复预置版本
    # (API 的 potcar_set 通常一致,但不得依赖;变体由 PSP 库决定)。
    potcar_path = work_dir / "POTCAR"
    potcar_backup = potcar_path.read_bytes() if potcar_path.is_file() else None

    vif = VaspInputFiles(options, overridden_incar_settings=overrides)
    vif.create_input_files(work_dir)
    if potcar_backup is not None:
        potcar_path.write_bytes(potcar_backup)
    # Belt and braces: overrides can drift with vise releases.
    patch_incar(work_dir, **overrides)
    if config.soc and not config.stage2_soc:
        patch_incar(work_dir, LSORBIT=".TRUE.", ISYM=-1)
    # libs/vise fork's U table lacks Ti (official vise has U=4): rely on
    # set_hubbard_u for the rest, then patch any U-table species that the
    # fork dropped (idempotent — no-op when LDAU already present).
    patch_incar_u(work_dir)
    patch_incar_magmom(work_dir)


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
    """Copy CONTCAR → POSCAR and set ISTART=1 for restart.

    A zero-byte CONTCAR (truncated/corrupted run, e.g. same-dir
    concurrent VASP) must not clobber POSCAR — crisp then refuses the
    submission ("missing or empty: POSCAR") and the dir loops forever.
    """
    contcar = path / "CONTCAR"
    if not contcar.is_file() or contcar.stat().st_size == 0:
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

def seed_geometry_from_contcar(path: Path, source_dir: Path) -> bool:
    """Seed *path*'s starting geometry from a sibling charge state's CONTCAR.

    Charge-state chain seeding (ADR 0010): the same defect at different
    charges shares a near-identical equilibrium geometry, so a converged
    sibling's CONTCAR is a far better starting point than the pristine
    defect structure (typically ~10-20 ionic steps instead of ~100).

    Only the geometry carries over.  The WAVECAR is charge-state specific
    (different NELECT) and MUST NOT be reused, so it is removed and ISTART
    is forced to 0: the electronic structure self-consists from scratch on
    the seeded geometry.

    Returns True when a seed was applied.
    """
    contcar = source_dir / "CONTCAR"
    if not contcar.is_file():
        return False
    shutil.copy2(str(contcar), str(path / "POSCAR"))
    wavecar = path / "WAVECAR"
    if wavecar.is_file():
        wavecar.unlink()
    incar = path / "INCAR"
    if incar.is_file():
        patch_incar(path, ISTART=0)
    return True


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



# ── DFT+U patch (ADR 0012) ──────────────────────────────────────────────
# vise's CLI path applies set_hubbard_u only to defect tasks; cpd and
# structure_opt templates come out without LDAU tags.  We patch those
def patch_incar_u(work_dir: Path) -> None:
    """Add DFT+U tags to an INCAR that lacks them (vise CLI gap for
    non-defect tasks, e.g. cpd structure_opt templates).

    No-op when LDAU is already present, the INCAR is missing, or no
    species maps to a U.  A patched INCAR also forces ISPIN=2, matching
    the vise defect template, and initial magnetic moments via
    patch_incar_magmom.
    """
    incar = work_dir / "INCAR"
    if not incar.is_file():
        return
    species = _poscar_species(work_dir / "POSCAR") or []
    if not species:
        return
    # _poscar_species returns per-atom symbols (pymatgen iterates the
    # structure); VASP's LDAUU/LDAUL rows are per POTCAR species, so
    # dedupe keeping order.
    species = list(dict.fromkeys(species))
    # ISPIN=2 for any U-table species: vise's cpd template leaves spin
    # polarization out even for magnetic FeO.
    if any(s in U_TABLE for s in species):
        patch_incar(work_dir, ISPIN=2)
    if "LDAU" in incar.read_text(errors="ignore"):
        return
    uu = [str(U_TABLE[s][0]) if s in U_TABLE else "0" for s in species]
    ul = [str(U_TABLE[s][1]) if s in U_TABLE else "-1" for s in species]
    if all(u == "0" for u in uu):
        return
    lmaxmix = max(U_TABLE[s][1] for s in species if s in U_TABLE)
    lmaxmix = 6 if lmaxmix == 3 else 4
    patch_incar(work_dir, LDAU=True, LDAUTYPE=2, LDAUPRINT=1,
                LMAXMIX=lmaxmix, LDAUU=" ".join(uu), LDAUL=" ".join(ul),
                ISPIN=2)


def patch_incar_magmom(work_dir: Path) -> None:
    """Set MAGMOM in POSCAR atom order (0 for non-magnetic species).

    Initial moments come from :mod:`vasp_sop.vasp.protocol.INITIAL_MAGMOM`
    (high-spin; Gd=7 fixes the 4f SOC collapse, issue #151).  Without
    explicit moments every restart re-runs SCF from VASP's default
    1.0/site, which can land in a metastable magnetic basin —
    Sr[FeO2]2 drifted 80→72→56 μB across TIME-LIMIT restarts (~5 eV
    above the ferromagnetic ground state) while NSW was burned on the
    wrong spin state.  Idempotent (MAGMOM overwritten only when the
    species set contains a magnetic element).
    """
    incar = work_dir / "INCAR"
    if not incar.is_file():
        return
    species = _poscar_species(work_dir / "POSCAR") or []
    moments = magmom_values(species)
    if moments is None:
        return
    patch_incar(work_dir, MAGMOM=" ".join(str(m) for m in moments))


# ── POTCAR restore (ADR 0007: input restore) ─────────────────────────────

_DEFAULT_PSP_DIR = "/mnt/shared/VASP_POT/POT_GGA_PAW_PBE"


def _poscar_species(poscar: Path) -> list[str] | None:
    """Element symbols in POSCAR order (pymatgen, then line-6 fallback)."""
    try:
        from pymatgen.core import Structure
        structure = Structure.from_file(str(poscar))
        return [str(sp.specie) for sp in structure]
    except Exception:
        pass
    try:
        lines = poscar.read_text().splitlines()
        for line in lines[5:9]:
            parts = line.split()
            if parts and all(p[:1].isalpha() and p[0].isupper() for p in parts):
                return parts
    except Exception:
        return None
    return None


def _psp_encmax(potcar: Path) -> float | None:
    """ENMAX from a POTCAR's header block (first element line)."""
    import re
    try:
        head = potcar.read_text()[:4096]
    except OSError:
        return None
    m = re.search(r"ENMAX\s*=\s*([\d.]+)", head)
    return float(m.group(1)) if m else None


def _pick_psp_variant(
    el: str, *, psp: Path, encut: float | None
) -> Path | None:
    """PSP dir for *el*: exact name first, else the ENCUT-matching variant.

    The store carries per-element variants (``Ba_sv`` not ``Ba``, plain
    ``Se`` alongside ``Se_sv_GW``).  The documented rule: INCAR ENCUT =
    1.3 * ENMAX of the chosen POTCAR.  GW variants are never picked for
    standard PBE work.
    """
    exact = psp / el
    if (exact / "POTCAR").is_file():
        return exact
    best: Path | None = None
    best_err: float | None = None
    for cand in psp.iterdir():
        if not cand.is_dir() or not cand.name.startswith(el):
            continue
        if "_GW" in cand.name:
            continue
        potcar = cand / "POTCAR"
        if not potcar.is_file():
            continue
        if encut is None:
            if best is None:
                best = cand
            continue
        enmax = _psp_encmax(potcar)
        if enmax is None:
            continue
        err = abs(encut - 1.3 * enmax)
        if best_err is None or err < best_err:
            best_err, best = err, cand
    return best


def _dir_encut(path: Path) -> float | None:
    """INCAR ENCUT (float), or None."""
    incar = path / "INCAR"
    if not incar.is_file():
        return None
    try:
        text = incar.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("ENCUT"):
            try:
                return float(line.split("=")[1].split()[0])
            except (IndexError, ValueError):
                return None
    return None


def restore_potcar(
    path: Path, *, psp_dir: str | None = None, dry_run: bool = False
) -> tuple[bool, str]:
    """Restore a missing POTCAR for *path* from the local PSP store.

    Concats each element's PSP POTCAR in POSCAR species order, choosing
    the ENCUT-matching variant per element.  Returns (ok, message).
    """
    potcar = path / "POTCAR"
    if potcar.is_file():
        return True, "POTCAR already present"
    poscar = path / "POSCAR"
    if not poscar.is_file():
        return False, "no POSCAR — cannot infer species"
    species = _poscar_species(poscar)
    if not species:
        return False, "cannot parse POSCAR species"
    psp = Path(psp_dir or _DEFAULT_PSP_DIR)
    encut = _dir_encut(path)
    chunks: list[str] = []
    missing: list[str] = []
    for el in species:
        cand = _pick_psp_variant(el, psp=psp, encut=encut)
        if cand is None:
            missing.append(el)
            continue
        chunks.append((cand / "POTCAR").read_text())
    if missing:
        return False, f"PSP store missing: {', '.join(missing)}"
    if dry_run:
        return True, f"would restore POTCAR for {' '.join(dict.fromkeys(species))}"
    potcar.write_text("".join(chunks))
    return True, f"restored POTCAR for {' '.join(dict.fromkeys(species))}"


def restore_missing_inputs(
    root: Path, *, psp_dir: str | None = None, dry_run: bool = False
) -> dict[str, list[str]]:
    """Restore missing POTCAR for runnable-but-unprovisioned dirs.

    *root* is a project root of systems, or a single system.  Scans each
    system's cpd/unitcell/defect trees; dirs that have POSCAR but no
    POTCAR (missing exactly the PSP-derived input) get it restored.
    Returns {restored: [...], skipped: [...], failed: [...]}.
    """
    from vasp_sop.core.blockers import calc_dirs

    if (root / "plan.yaml").is_file():
        systems = [root]
    else:
        systems = [
            d for d in sorted(root.iterdir())
            if d.is_dir() and (d / "plan.yaml").is_file()
        ]

    from vasp_sop.core.blockers import classify_dir

    restored: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for sysd in systems:
        for d, task_type in calc_dirs(sysd):
            if (d / "POTCAR").is_file():
                continue
            # Only blocked dirs are restored — dirs whose calc is already
            # done on disk (POTCAR stripped after completion) stay untouched
            # (established policy: no mass restore of completed work).
            if classify_dir(d, task_type=task_type).finished:
                continue
            if not (d / "POSCAR").is_file():
                if task_type not in ("band", "dos", "dielectric"):
                    skipped.append(f"{d.relative_to(sysd)} (no POSCAR)")
                continue
            ok, msg = restore_potcar(d, psp_dir=psp_dir, dry_run=dry_run)
            target = restored if ok else failed
            target.append(f"{d.relative_to(sysd)} ({msg})")
    return {"restored": restored, "skipped": skipped, "failed": failed}
