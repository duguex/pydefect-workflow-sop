"""Pydefect / vise CLI adapter — the single seam to the post-processing toolkits.

Introduced in issue #103.  Every ``pydefect`` / ``pydefect_vasp`` / ``vise``
subprocess invocation used by the defect pipeline lives behind this module's
functions (the former inlined ``run_local(...)`` call sites in
``defect/{analysis,cpd,builder,unitcell}.py`` now route here).  The module
owns the command strings, batch slicing, and shell quoting.  The current
implementation still shells out via :func:`~vasp_sop.core.jobs.run_local`;
a future ``libs/`` integration may replace the subprocess calls with direct
library calls without changing the public interface here.

Tables
------
``VISE_TASKS``
    Command templates for the three unitcell ``vise vs`` tasks (moved from
    ``defect/unitcell.py``).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vasp_sop.core.jobs import run_local
from vasp_sop.vasp.convergence import convergence_verdict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormationEnergy:
    """One charge state's formation energy with its aggregated correction."""

    charge: int
    formation_energy: float
    is_shallow: bool
    correction: float


@dataclass(frozen=True)
class DefectEnergy:
    """One defect entry: its charge states and site occupation change."""

    name: str
    charges: list[int]
    atom_io: dict[str, int]
    formation_energies: list[FormationEnergy]


@dataclass(frozen=True)
class DefectSummary:
    """Typed view of ``defect_energy_summary.json``.

    Energy corrections are aggregated into ``FormationEnergy.correction``
    (the renderer never re-sums them — see the E deepening).
    """

    title: str
    cbm: float | None
    defects: list[DefectEnergy] = field(default_factory=list)


@dataclass(frozen=True)
class CpdDiagram:
    """Typed view of the CPD inputs the interactive report renders.

    ``rel_chem_pots`` mirrors the renderer's fallback chain
    (summary ``rel_chem_pots``, else the target-vertices dict).
    """

    target: str
    vertex_elements: list[str]
    polygons: dict[str, Any]
    rel_chem_pots: dict[str, Any]
    title: str


def defect_summary(path: Path) -> DefectSummary | None:
    """Parse ``defect_energy_summary.json`` into a :class:`DefectSummary`."""
    data = _read_json(path)
    if data is None:
        return None
    defects: list[DefectEnergy] = []
    for name, entry in data.get("defect_energies", {}).items():
        if not isinstance(entry, dict) or "charges" not in entry:
            continue
        energies = entry.get("defect_energies", [])
        fes: list[FormationEnergy] = []
        for i, q in enumerate(entry["charges"]):
            item: dict[str, Any] = {}
            if isinstance(energies, list) and i < len(energies) and isinstance(energies[i], dict):
                item = energies[i]
            corr = item.get("energy_corrections", {})
            if not isinstance(corr, dict):
                corr = {}
            fes.append(FormationEnergy(
                charge=int(q),
                formation_energy=float(item.get("formation_energy", 0.0)),
                is_shallow=bool(item.get("is_shallow")),
                correction=float(sum(corr.values())),
            ))
        defects.append(DefectEnergy(
            name=str(name),
            charges=[int(q) for q in entry["charges"]],
            atom_io={str(k): int(v) for k, v in (entry.get("atom_io") or {}).items()},
            formation_energies=fes,
        ))
    cbm = data.get("cbm", data.get("supercell_cbm"))
    return DefectSummary(
        title=str(data.get("title", "")),
        cbm=float(cbm) if isinstance(cbm, (int, float)) else None,
        defects=defects,
    )


def cpd_diagram(cpd_dir: Path, defect_summary_path: Path | None = None) -> CpdDiagram | None:
    """Parse the CPD inputs (chem_pot_diag.json + target_vertices) typed-up.

    The target name resolves exactly like the renderer's fallback chain:
    ``target_vertices.yaml`` ``target``, else the summary ``title``, else the
    first polygon key.
    """
    import yaml as _yaml

    cpd_file = cpd_dir / "chem_pot_diag.json"
    if not cpd_file.is_file():
        return None
    cpd = _read_json(cpd_file)
    if cpd is None:
        return None

    tv: dict[str, Any] | None = None
    tv_yaml = cpd_dir / "target_vertices.yaml"
    if tv_yaml.is_file():
        try:
            loaded = _yaml.safe_load(tv_yaml.read_text())
            if isinstance(loaded, dict):
                tv = loaded
        except Exception:
            tv = None
    if tv is None:
        tv_json = cpd_dir / "target_vertices.json"
        if tv_json.is_file():
            tv = _read_json(tv_json)

    de = None
    if defect_summary_path is not None and defect_summary_path.is_file():
        de = _read_json(defect_summary_path)

    title = str((de or {}).get("title", ""))
    host_name = str((tv or {}).get("target", "")) if tv else ""
    if not host_name:
        target_keys = [k for k in cpd.get("polygons", {}) if k != "combos"]
        host_name = title or (target_keys[0] if target_keys else "host")

    rcp = (de or {}).get("rel_chem_pots", tv or {})
    if not isinstance(rcp, dict):
        rcp = {}
    return CpdDiagram(
        target=host_name,
        vertex_elements=[str(e) for e in cpd.get("vertex_elements", [])],
        polygons=dict(cpd.get("polygons", {}) or {}),
        rel_chem_pots=rcp,
        title=title,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _override_ionic_conv(d: Path, data: dict[str, Any]) -> bool:
    """Patch ionic_conv in *data* and on disk if vasp-sop disagrees.

    Returns ``True`` when the file was written, ``False`` otherwise.
    """
    if not data.get("ionic_conv") and convergence_verdict(d).converged:
        logger.info(
            "Overriding ionic_conv=True for %s (pydefect said False, "
            "vasp-sop check_converged says converged)", d.name,
        )
        data["ionic_conv"] = True
        cr_file = d / "calc_results.json"
        cr_file.write_text(json.dumps(data, indent=2) + "\n")
        return True
    return False


def _cr_one(d: Path, cwd: Path) -> dict[str, Any] | None:
    """``pydefect_vasp cr`` for one dir; returns the result dict or None."""
    cr_file = d / "calc_results.json"
    if cr_file.is_file():
        data = _read_json(cr_file)
        if data is not None:
            _override_ionic_conv(d, data)
            return data
        logger.warning("calc_results: unreadable %s, re-running cr", cr_file)
    run_local(f"pydefect_vasp cr -d {shlex.quote(d.name)}", cwd=cwd)
    data = _read_json(cr_file)
    if data is not None:
        _override_ionic_conv(d, data)
        return data
    logger.warning("calc_results: no output for %s after cr", d.name)
    return None


def calc_results(dirs: list[Path], cwd: Path) -> list[dict[str, Any]]:
    """Run ``pydefect_vasp cr`` per directory in parallel and collect
    result dicts (order matches *dirs*; failures skipped with a warning)."""
    out = _map_parallel(dirs, lambda d: _cr_one(d, cwd), desc="pydefect_vasp cr")
    return [r for r in out if r is not None]


def _efnv_one(d: Path, cwd: Path, pcr_q: str, u_q: str, force: bool) -> dict[str, Any] | None:
    """``pydefect efnv`` for one dir; returns the correction dict or None."""
    corr_file = d / "correction.json"
    if corr_file.is_file() and not force:
        data = _read_json(corr_file)
        if data is not None:
            return data
        logger.warning("efnv: unreadable %s, re-running", corr_file)
    run_local(
        f"pydefect efnv -d {shlex.quote(d.name)} -pcr {pcr_q} -u {u_q}",
        cwd=cwd,
    )
    data = _read_json(corr_file)
    if data is not None:
        return data
    logger.warning("efnv: no correction.json for %s after run", d.name)
    return None


def efnv(
    dirs: list[Path],
    cwd: Path,
    perfect_calc_results: Path,
    unitcell_yaml: Path,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Run ``pydefect efnv`` per directory in parallel; collect correction
    dicts (order matches *dirs*; failures skipped with a warning)."""
    pcr_q = shlex.quote(str(perfect_calc_results))
    u_q = shlex.quote(str(unitcell_yaml))
    out = _map_parallel(
        dirs,
        lambda d: _efnv_one(d, cwd, pcr_q, u_q, force),
        desc="pydefect efnv",
    )
    return [r for r in out if r is not None]


def defect_energy_summary(
    cwd: Path,
    dirs: list[Path],
    unitcell_yaml: Path,
    perfect_band_edge_state: Path,
    target_vertices: Path,
    *,
    raise_on_error: bool = False,
) -> dict[str, Any] | None:
    """Run ``pydefect des`` and return the defect energy summary dict.

    If ``defect_energy_summary.json`` already exists in *cwd* it is read
    and returned without re-running.  Returns ``None`` when the summary
    file is absent after the command (e.g. the run failed).

    Parameters
    ----------
    cwd:
        Working directory (typically the ``defect/`` root).
    dirs:
        Defect directories to include in the summary.
    unitcell_yaml:
        Path to ``unitcell.yaml``.
    perfect_band_edge_state:
        Path to ``perfect_band_edge_state.json``.
    target_vertices:
        Path to ``target_vertices.yaml``.
    """
    summary_file = cwd / "defect_energy_summary.json"
    if summary_file.is_file():
        data = _read_json(summary_file)
        if data is not None:
            return data
        logger.warning("defect_energy_summary: unreadable existing summary, re-running des")

    if not dirs:
        logger.warning("defect_energy_summary: no dirs provided, skipping des")
        return None

    dir_names = " ".join(shlex.quote(d.name) for d in dirs)
    cmd = (
        f"pydefect des -d {dir_names}"
        f" -u {shlex.quote(str(unitcell_yaml))}"
        f" -pbes {shlex.quote(str(perfect_band_edge_state))}"
        f" -t {shlex.quote(str(target_vertices))}"
    )
    try:
        run_local(cmd, cwd=cwd)
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning("pydefect des failed: %s", exc)
        return None

    data = _read_json(summary_file)
    if data is None:
        logger.warning("defect_energy_summary: no summary file after des")
    return data


# ══════════════════════════════════════════════════════════════════════════
# Batch runner (moved from ``defect/analysis.py::_run_dir_batches``)
# ══════════════════════════════════════════════════════════════════════════


def normalize_ionic_convergence(dirs: list[Path], cwd: Path) -> int:
    """Reconcile ``ionic_conv`` in existing ``calc_results.json`` files.

    The single reconciliation point: whenever vasp-sop's convergence verdict
    says a calc is converged but pydefect's ``calc_results.json`` disagrees,
    the verdict wins and the file is rewritten.  Returns the number of files
    rewritten.  (``calc_results()`` applies the same rule on its own paths.)
    """
    n = 0
    for d in dirs:
        cr_file = d / "calc_results.json"
        if not cr_file.is_file():
            continue
        try:
            data = _read_json(cr_file)
            if data and not data.get("ionic_conv"):
                if _override_ionic_conv(d, data):
                    n += 1
        except Exception as exc:
            logger.warning("normalize calc_results failed for %s: %s", d.name, exc)
    return n


def _quote_names(dirs: list[Path]) -> str:
    """Join directory names shell-quoted for a ``-d ...`` argument."""
    return " ".join(shlex.quote(d.name) for d in dirs)


def _parallel_workers() -> int:
    """Per-dir pydefect subprocesses each use ~2-3 cores; keep headroom."""
    n = os.cpu_count() or 4
    return max(2, min(8, n // 3))


def _map_parallel(dirs: list[Path], fn, *, desc: str) -> list:
    """Run ``fn(d)`` per dir in parallel, preserving dir order.

    Failures are logged and yield ``None`` entries (callers skip them),
    matching the serial loop's per-dir isolation.
    """
    workers = _parallel_workers()
    if len(dirs) <= 1 or workers <= 1:
        out: list = []
        for d in dirs:
            try:
                out.append(fn(d))
            except Exception as exc:
                logger.warning("%s failed for %s: %s", desc, d.name, exc)
                out.append(None)
        return out
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, d): i for i, d in enumerate(dirs)}
        out = [None] * len(dirs)
        for fut, i in futures.items():
            try:
                out[i] = fut.result()
            except Exception as exc:
                logger.warning("%s failed for %s: %s", desc, dirs[i].name, exc)
        return out


def _run_batches(
    command_prefix: str,
    dirs: list[Path],
    *,
    cwd: Path,
    command_suffix: str = "",
    batch_size: int = 20,
    timeout: int = 600,
) -> None:
    """Run ``command_prefix {names}{command_suffix}`` per batch of *dirs*.

    Slices *dirs* into batches of *batch_size* and runs one shell command per
    batch, batches in parallel (worker-capped).  Per-dir artifacts remain on
    disk, so a failed batch resumes on the next run.
    """
    batches = [
        dirs[i : i + batch_size] for i in range(0, len(dirs), batch_size)
    ]
    workers = _parallel_workers()
    if len(batches) <= 1 or workers <= 1:
        for batch in batches:
            cmd = f"{command_prefix} {_quote_names(batch)}{command_suffix}"
            run_local(cmd, cwd=cwd, timeout=timeout)
        return
    from concurrent.futures import ThreadPoolExecutor

    def _one(batch: list[Path]) -> None:
        cmd = f"{command_prefix} {_quote_names(batch)}{command_suffix}"
        run_local(cmd, cwd=cwd, timeout=timeout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, batches))


# ══════════════════════════════════════════════════════════════════════════
# Defect post-processing surface (analysis pipeline)
# ══════════════════════════════════════════════════════════════════════════


def perfect_calc_results(cwd: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect_vasp cr -d perfect``."""
    run_local("pydefect_vasp cr -d perfect", cwd=cwd, timeout=timeout)


def pbes(cwd: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect_vasp pbes -d perfect``."""
    run_local("pydefect_vasp pbes -d perfect", cwd=cwd, timeout=timeout)


def defect_structure_info(dirs: list[Path], cwd: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect dsi -d ...`` in batches."""
    _run_batches("pydefect dsi -d", dirs, cwd=cwd, timeout=timeout)


def defect_volume_fraction(dirs: list[Path], cwd: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect_util dvf -d ...`` in batches."""
    _run_batches("pydefect_util dvf -d", dirs, cwd=cwd, timeout=timeout)


def band_edge_occupation(
    dirs: list[Path],
    cwd: Path,
    perfect_band_edge_state: Path,
    *,
    timeout: int = 600,
) -> None:
    """Run ``pydefect_vasp beoi -d ... -pbes <pbes>`` in batches."""
    suffix = f" -pbes {shlex.quote(str(perfect_band_edge_state))}"
    _run_batches(
        "pydefect_vasp beoi -d",
        dirs,
        cwd=cwd,
        command_suffix=suffix,
        timeout=timeout,
    )


def band_edge_states(
    dirs: list[Path],
    cwd: Path,
    perfect_band_edge_state: Path,
    *,
    timeout: int = 600,
) -> None:
    """Run ``pydefect bes -d ... -pbes <pbes>`` in batches."""
    suffix = f" -pbes {shlex.quote(str(perfect_band_edge_state))}"
    _run_batches(
        "pydefect bes -d",
        dirs,
        cwd=cwd,
        command_suffix=suffix,
        timeout=timeout,
    )


def defect_energy_info(
    dirs: list[Path],
    cwd: Path,
    *,
    perfect_calc_results: Path,
    unitcell_yaml: Path,
    standard_energies: Path,
    timeout: int = 600,
) -> None:
    """Run ``pydefect dei -d ... -pcr <pcr> -u <uc> -s <se>`` in batches."""
    suffix = (
        f" -pcr {shlex.quote(str(perfect_calc_results))}"
        f" -u {shlex.quote(str(unitcell_yaml))}"
        f" -s {shlex.quote(str(standard_energies))}"
    )
    _run_batches(
        "pydefect dei -d",
        dirs,
        cwd=cwd,
        command_suffix=suffix,
        timeout=timeout,
    )


def correction_summary(
    dirs: list[Path],
    cwd: Path,
    *,
    perfect_calc_results: Path,
    timeout: int = 600,
) -> None:
    """Run ``pydefect cs -d ... -pcr <pcr>``."""
    names = _quote_names(dirs)
    cmd = (
        f"pydefect cs -d {names}"
        f" -pcr {shlex.quote(str(perfect_calc_results))}"
    )
    run_local(cmd, cwd=cwd, timeout=timeout)


def plot_energy_vertex(cwd: Path, vertex: str, *, timeout: int = 600) -> None:
    """Run ``pydefect pe -d defect_energy_summary.json -l <vertex>``."""
    run_local(
        f"pydefect pe -d defect_energy_summary.json -l {vertex}",
        cwd=cwd,
        timeout=timeout,
    )


# ══════════════════════════════════════════════════════════════════════════
# CPD surface
# ══════════════════════════════════════════════════════════════════════════


def mce(cpd_root: Path, phase_dirs: list[str], *, timeout: int = 600) -> None:
    """Run ``pydefect_vasp mce -d <escaped dirs>`` (composition energies)."""
    dirs = " ".join(phase_dirs)
    escaped = dirs.replace("(", r"\(").replace(")", r"\)")
    run_local(f"pydefect_vasp mce -d {escaped}", cwd=cpd_root, timeout=timeout)


def sre(cpd_root: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect sre`` (relative energies)."""
    run_local("pydefect sre", cwd=cpd_root, timeout=timeout)


def chem_pot_diagram(cpd_root: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect pc`` (chem-pot diagram plot; diagnostic only)."""
    run_local("pydefect pc", cwd=cpd_root, timeout=timeout)


def chemical_vertices(cpd_root: Path, target: str, *, timeout: int = 600) -> None:
    """Run ``pydefect cv -t "<target>"`` (adjust unstable-phase energies)."""
    run_local(f'pydefect cv -t "{target}"', cwd=cpd_root, timeout=timeout)


# ══════════════════════════════════════════════════════════════════════════
# Defect-generation surface (builder pipeline)
# ══════════════════════════════════════════════════════════════════════════


def make_supercell(
    defect_root: Path,
    uc_contcar: Path,
    config: Any,
    *,
    timeout: int = 600,
) -> None:
    """Run ``pydefect s -p <CONTCAR> --max_atoms N --min_atoms N``."""
    cmd = (
        f"pydefect s -p {uc_contcar} "
        f"--max_atoms {config.supercell_max_atoms} "
        f"--min_atoms {config.supercell_min_atoms}"
    )
    run_local(cmd, cwd=defect_root, timeout=timeout)


def print_dos_extrema(defect_root: Path, dos_extrema: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect_print <dos_extrema>`` (interstitial candidates)."""
    run_local(f"pydefect_print {dos_extrema}", cwd=defect_root, timeout=timeout)


def atom_indices(
    defect_root: Path,
    dos_extrema: Path,
    interstitial_sites: str,
    *,
    timeout: int = 600,
) -> None:
    """Run ``pydefect_util ai --local_extrema <dos> -i <indices>``."""
    run_local(
        f"pydefect_util ai --local_extrema {dos_extrema} -i {interstitial_sites}",
        cwd=defect_root,
        timeout=timeout,
    )


def defect_list(
    defect_root: Path,
    dopant_elements: list[str] | None = None,
    *,
    timeout: int = 600,
) -> None:
    """Run ``pydefect ds [-d <dopants>]`` (fallback defect list)."""
    if dopant_elements:
        cmd = f"pydefect ds -d {' '.join(dopant_elements)}"
    else:
        cmd = "pydefect ds"
    run_local(cmd, cwd=defect_root, timeout=timeout)


def defect_structures(defect_root: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect_vasp de`` (per-defect structures)."""
    run_local("pydefect_vasp de", cwd=defect_root, timeout=timeout)


# ══════════════════════════════════════════════════════════════════════════
# Unitcell surface (vise + pydefect_vasp u/le)
# ══════════════════════════════════════════════════════════════════════════

VISE_TASKS: dict[str, str] = {
    "band": "vise vs -x pbesol -t band",
    "dos": "vise vs -x pbesol -t dos -k 2 -uis LVTOT True LAECHG True KPAR 1",
    "dielectric": "vise vs -x pbesol -t dielectric_dfpt -k 2",
}


def vise_task_types() -> list[str]:
    """Return the unitcell task names in submission order."""
    return list(VISE_TASKS)


def vise_vs(task_dir: Path, task_type: str, *, timeout: int = 300) -> None:
    """Run the ``vise vs`` template for *task_type* in *task_dir*."""
    cmd = VISE_TASKS[task_type]
    run_local(cmd, cwd=task_dir, timeout=timeout)


def run_in_subdir(cwd: Path, subdir: str, command: str, *, timeout: int = 600) -> None:
    """Run ``cd <subdir> && <command>`` in *cwd* (band/dos plots)."""
    run_local(f"cd {subdir} && {command}", cwd=cwd, timeout=timeout)


def local_extrema(uc_root: Path, *, timeout: int = 600) -> None:
    """Run ``pydefect_vasp le`` in the dos subdir (AECCAR local extrema)."""
    run_local(
        "cd dos && pydefect_vasp le -v AECCAR0 AECCAR2 -i all_electron_charge",
        cwd=uc_root,
        timeout=timeout,
    )


def unitcell_yaml(
    uc_root: Path,
    *,
    band_vasprun: Path,
    band_outcar: Path,
    dielectric_outcar: Path,
    formula: str,
) -> str:
    """Run ``pydefect_vasp u ...`` and return the exact command.

    The returned command is surfaced in ``unitcell_build_status.json`` when
    the build fails (zero band gap / missing vasprun).
    """
    cmd = (
        f"pydefect_vasp u -vb {shlex.quote(str(band_vasprun))} "
        f"-ob {shlex.quote(str(band_outcar))} "
        f"-odc {shlex.quote(str(dielectric_outcar))} "
        f"-odi {shlex.quote(str(dielectric_outcar))} "
        f"-n {shlex.quote(formula)}"
    )
    run_local(cmd, cwd=uc_root)
    return cmd
