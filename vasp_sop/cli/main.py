"""Command-line interface for vasp-sop.

Usage::

    vasp-sop defect run -c config.yaml
    vasp-sop defect resume -r /path/to/project
    vasp-sop defect status -r /path/to/project
    vasp-sop defect init -f GaN -d Mg
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from vasp_sop import __version__
from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.state import StateStore, StepStatus
from vasp_sop.defect.pipeline import run_point_defect_pipeline

logger = logging.getLogger(__name__)


def _add_pipeline_parser(subparsers) -> None:
    """Add the ``pipeline`` subcommand."""
    p = subparsers.add_parser("pipeline", help="Run the full pipeline end-to-end")
    p.add_argument("-c", "--config", type=Path, required=True, help="Path to plan.yaml")
    p.add_argument("-r", "--root", type=Path, default=Path("."), help="Project root")


def _add_materials_parser(subparsers) -> None:
    """Add ``materials`` subcommand with sub-actions."""
    p = subparsers.add_parser("materials", help="Materials Project queries and analysis")
    sub = p.add_subparsers(dest="action", required=True)

    # fetch
    fetch_p = sub.add_parser("fetch", help="Download competing phases from MP")
    fetch_p.add_argument("-e", "--elements", type=str, nargs="+", required=True, help="Element symbols")
    fetch_p.add_argument("-d", "--dopants", type=str, nargs="*", default=[], help="Dopant elements")
    fetch_p.add_argument("-o", "--output", type=Path, default=Path("cpd"), help="Output directory")

    # phases
    phases_p = sub.add_parser("phases", help="List cached phases")
    phases_p.add_argument("-e", "--elements", type=str, nargs="+", required=True, help="Intrinsic element symbols")
    phases_p.add_argument("-d", "--cpd-dir", type=Path, default=Path("cpd"), help="CPD root directory")

    # poscar
    poscar_p = sub.add_parser("poscar", help="Download a single POSCAR by MP-ID")
    poscar_p.add_argument("mpid", type=str, help="Materials Project ID (e.g. mp-804)")

    # cache
    cache_p = sub.add_parser("cache", help="Manage MP cache")
    cache_sub = cache_p.add_subparsers(dest="cache_action", required=True)
    cache_sub.add_parser("list", help="List cached combinations")
    cache_sub.add_parser("clear", help="Clear MP cache")


def _add_vasp_parser(subparsers) -> None:
    """Add ``vasp`` subcommand with sub-actions."""
    p = subparsers.add_parser("vasp", help="VASP input/output utilities")
    sub = p.add_subparsers(dest="action", required=True)

    inputs_p = sub.add_parser("inputs", help="Generate VASP inputs via vise")
    inputs_p.add_argument("work_dir", type=Path, help="Target calculation directory")
    inputs_p.add_argument("-x", "--functional", type=str, default="pbesol", help="Exchange-correlation functional")

    check_p = sub.add_parser("check", help="Check VASP completion")
    check_p.add_argument("work_dir", type=Path, help="Calculation directory")


def _add_cpd_parser(subparsers) -> None:
    """Add ``cpd`` subcommand with sub-actions."""
    p = subparsers.add_parser("cpd", help="Chemical-potential diagram tools")
    sub = p.add_subparsers(dest="action", required=True)

    energies_p = sub.add_parser("energies", help="Compute composition energies")
    energies_p.add_argument("cpd_dir", type=Path, help="CPD root directory")
    energies_p.add_argument("-f", "--formula", type=str, required=True, help="Target formula")

    diagram_p = sub.add_parser("diagram", help="Solve and plot phase diagram")
    diagram_p.add_argument("cpd_dir", type=Path, help="CPD root directory")


def _add_unitcell_parser(subparsers) -> None:
    """Add ``unitcell`` subcommand."""
    p = subparsers.add_parser("unitcell", help="Unitcell analysis tools")
    sub = p.add_subparsers(dest="action", required=True)

    yaml_p = sub.add_parser("yaml", help="Generate unitcell.yaml from VASP outputs")
    yaml_p.add_argument("uc_dir", type=Path, help="Unitcell directory")


def _handle_materials(args: argparse.Namespace) -> None:
    if args.action == "fetch":
        from vasp_sop.materials import fetch_candidate_phases, get_intrinsic_elements
        elements = args.elements
        if args.dopants:
            elements = elements + args.dopants
        fetch_candidate_phases(elements, args.output.resolve(), use_cache=True)
        print(f"Phases downloaded to {args.output.resolve()}")

    elif args.action == "phases":
        from vasp_sop.materials import list_phases
        info = list_phases(args.cpd_dir.resolve(), args.elements)
        for name, meta in info.items():
            mpid = meta.get("mpid") or "—"
            print(f"  {name:40s}  {mpid}")

    elif args.action == "poscar":
        from vasp_sop.materials import mp_poscar_get
        poscar = mp_poscar_get(args.mpid)
        if poscar:
            print(f"Cached POSCAR for {args.mpid}: {poscar}")
        else:
            print(f"No cached POSCAR for {args.mpid}. Run 'fetch' first.")

    elif args.action == "cache":
        if args.cache_action == "list":
            from vasp_sop.core.cache import MP_CACHE
            if MP_CACHE.is_dir():
                for child in sorted(MP_CACHE.iterdir()):
                    if child.is_dir():
                        print(f"  {child.name}")
            else:
                print("MP cache is empty.")
        elif args.cache_action == "clear":
            import shutil
            from vasp_sop.core.cache import MP_CACHE
            if MP_CACHE.is_dir():
                shutil.rmtree(str(MP_CACHE))
                print("MP cache cleared.")
            else:
                print("MP cache already empty.")


def _handle_vasp(args: argparse.Namespace) -> None:
    if args.action == "inputs":
        from vasp_sop.vasp.io import input_ready, prepare_inputs
        from vasp_sop.core.config import PipelineConfig
        wd = args.work_dir.resolve()
        config = PipelineConfig(
            formula="", root=Path.cwd(), functional=args.functional,
        )
        prepare_inputs(wd, config)
        print(f"VASP inputs generated in {wd}")

    elif args.action == "check":
        from vasp_sop.vasp.io import check_converged
        wd = args.work_dir.resolve()
        if check_converged(wd):
            print(f"{wd}: converged")
        else:
            print(f"{wd}: NOT converged or not complete")


def _handle_cpd(args: argparse.Namespace) -> None:
    from vasp_sop.materials import get_intrinsic_elements
    from vasp_sop.defect.cpd import compute_chemical_potentials, adjust_unstable_phase
    from vasp_sop.core.config import PipelineConfig
    from pymatgen.core import Composition

    cpd_dir = args.cpd_dir.resolve()
    formula = getattr(args, "formula", "GaN")

    config = PipelineConfig(
        formula=formula, root=Path.cwd(),
    )
    target_comp = Composition(formula)

    if args.action == "energies":
        compute_chemical_potentials(cpd_dir, config, target_comp)
        print(f"Composition energies processed in {cpd_dir}")

    elif args.action == "diagram":
        from vasp_sop.defect.cpd import adjust_unstable_phase
        from pymatgen.core import Composition
        from pathlib import Path
        rel = cpd_dir / "relative_energies.yaml"
        adjust_unstable_phase(cpd_dir, rel, target_comp, config)
        print(f"Phase diagram processed in {cpd_dir}")


def _handle_unitcell(args: argparse.Namespace) -> None:
    from vasp_sop.defect.unitcell import build_unitcell_yaml
    from vasp_sop.core.config import PipelineConfig

    uc_dir = args.uc_dir.resolve()
    config = PipelineConfig(formula="", root=Path.cwd())
    build_unitcell_yaml(uc_dir, config)
    print(f"Unitcell YAML generated in {uc_dir}")




def main() -> None:
    """CLI entry point (``vasp-sop``)."""
    parser = argparse.ArgumentParser(
        prog="vasp-sop",
        description="Structural-optimisation pipelines for VASP defect calculations.",
    )
    parser.add_argument(
        "--version", action="version", version=f"vasp-sop {__version__}"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_pipeline_parser(subparsers)
    _add_materials_parser(subparsers)
    _add_vasp_parser(subparsers)
    _add_cpd_parser(subparsers)
    _add_unitcell_parser(subparsers)
    _add_defect_parser(subparsers)
    _add_batch_parser(subparsers)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s[%(lineno)d] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )

    # ── Dispatch ────────────────────────────────────────────────────
    if args.command == "materials":
        _handle_materials(args)
    elif args.command == "vasp":
        _handle_vasp(args)
    elif args.command == "cpd":
        _handle_cpd(args)
    elif args.command == "unitcell":
        _handle_unitcell(args)
    elif args.command == "defect":
        _handle_defect(args)
    elif args.command == "pipeline":
        config = PipelineConfig.from_yaml(args.config, root=args.root.resolve())
        _run_pipeline(config)
    elif args.command == "batch":
        _handle_batch(args)


def _add_defect_parser(subparsers) -> None:
    """Add the ``defect`` subcommand with its actions."""
    defect_parser = subparsers.add_parser("defect", help="Point-defect pipeline")
    defect_sub = defect_parser.add_subparsers(dest="action", required=True)

    # run
    run_parser = defect_sub.add_parser("run", help="Run the full pipeline")
    run_parser.add_argument(
        "-c", "--config", type=Path, required=True,
        help="Path to YAML configuration file",
    )
    run_parser.add_argument(
        "-r", "--root", type=Path, default=Path("."),
        help="Project root directory (default: current directory)",
    )

    # resume
    resume_parser = defect_sub.add_parser(
        "resume", help="Resume pipeline from saved state"
    )
    resume_parser.add_argument(
        "-r", "--root", type=Path, required=True,
        help="Project root directory containing .pipeline_state.json",
    )

    # status
    status_parser = defect_sub.add_parser(
        "status", help="Show pipeline status"
    )
    status_parser.add_argument(
        "-r", "--root", type=Path, default=Path("."),
        help="Project root directory (default: current directory)",
    )

    # init
    init_parser = defect_sub.add_parser("init", help="Generate plan.yaml with inference")
    init_parser.add_argument("-f", "--formula", type=str, required=True,
                             help="Compound formula (e.g. GaN, SiC)")
    init_parser.add_argument("-d", "--dopant", type=str, nargs="*", default=[],
                             help="Dopant elements (e.g. Mg Si)")

    # build — standalone defect structure generation
    build_parser = defect_sub.add_parser("build", help="Build defect structures only")
    build_parser.add_argument("project_dir", type=Path, help="Project root directory")

    # analyze — standalone defect post-processing
    analyze_parser = defect_sub.add_parser("analyze", help="Run defect post-processing only")
    analyze_parser.add_argument("project_dir", type=Path, help="Project root directory")


def _handle_defect(args: argparse.Namespace) -> None:
    if args.action == "init":
        _do_init(args)
    elif args.action == "status":
        _do_status(args)
    elif args.action == "run":
        _do_run(args)
    elif args.action == "resume":
        _do_resume(args)

def _do_init(args: argparse.Namespace) -> None:
    """Generate plan.yaml with inference and dynamic comments."""
    from vasp_sop.core.config import generate_config

    path = generate_config(
        project_dir=Path.cwd(),
        formula=args.formula,
        dopant_elements=args.dopant or [],
    )
    print(f"Config written to {path}")
    print()
    print("Edit the file then run:")
    print(f"  vasp-sop defect run -c {path}")


def _do_status(args: argparse.Namespace) -> None:
    """Print pipeline status for a project."""
    root = args.root.resolve()
    state = StateStore.load(root)
    print(f"Project: {root}")
    print(f"  CPD:       {state.cpd_status.value}")
    print(f"  Unitcell:  {state.unitcell_status.value}")
    print(f"  Defect:    {state.defect_status.value}")
    if state.active_jobs:
        print(f"  Active jobs: {len(state.active_jobs)}")
        for work_dir, task_name in state.active_jobs.items():
            print(f"    {task_name}: {work_dir}")
    if state.is_terminal():
        print("  >>> All stages complete.")
    elif state.defect_status == StepStatus.FAILED:
        print("  >>> Defect stage FAILED. Check logs and re-run.")
    elif state.unitcell_status == StepStatus.FAILED:
        print("  >>> Unitcell stage FAILED. Check logs and re-run.")
    elif state.cpd_status == StepStatus.FAILED:
        print("  >>> CPD stage FAILED. Check logs and re-run.")


def _do_run(args: argparse.Namespace) -> None:
    """Run (or resume) the full defect pipeline."""
    config = PipelineConfig.from_yaml(args.config, root=args.root.resolve())
    _run_pipeline(config)


def _do_resume(args: argparse.Namespace) -> None:
    """Resume pipeline from persisted state."""
    root = args.root.resolve()
    state = StateStore.load(root)

    # Try plan.yaml (new format) → config.yaml (legacy YAML) → info.json
    # (legacy JSON) before giving up. ``info.json`` is migrated to a fresh
    # plan.yaml so the rest of the pipeline sees a single canonical format.
    from vasp_sop.core.config import PLAN_FILENAME
    config_path = root / PLAN_FILENAME
    if not config_path.is_file():
        config_path = root / "config.yaml"
    if config_path.is_file():
        config = PipelineConfig.from_yaml(config_path, root=root)
    else:
        legacy_json = root / "info.json"
        if legacy_json.is_file():
            logger.info(
                "Found legacy info.json — migrating to %s.", PLAN_FILENAME,
            )
            config = PipelineConfig.from_legacy_json(legacy_json, root=root)
            new_path = root / PLAN_FILENAME
            config.to_yaml(new_path)
            logger.info("Wrote %s.", new_path)
        else:
            logger.error(
                "No %s, config.yaml, or info.json found in %s. Cannot resume.",
                PLAN_FILENAME, root,
            )
            sys.exit(1)

    if state.is_terminal():
        logger.info("Pipeline already complete. Nothing to resume.")
        return

    # State is non-terminal — re-enter the pipeline. The state machine in
    # run_point_defect_pipeline checks each stage's DONE flag and skips
    # completed work, so resume behaves the same as run.
    _run_pipeline(config)



def _run_pipeline(config: PipelineConfig) -> None:
    """Execute the pipeline."""
    logger.info(
        "Starting point-defect pipeline for %s (root: %s)",
        config.formula, config.root,
    )

    try:
        state = run_point_defect_pipeline(config)
    except Exception:
        logger.exception("Pipeline failed")
        sys.exit(1)

    if state.is_terminal():
        logger.info("Pipeline completed successfully.")
    else:
        logger.warning("Pipeline finished but not fully complete.")
        sys.exit(1)



# ══════════════════════════════════════════════════════════════════════════
# Batch — multi-system operations
# ══════════════════════════════════════════════════════════════════════════

_PRIORITY_MAP: dict[str, str] = {
    # P0 — A级 NV-like 候选
    "SrS": "P0", "MgS": "P0", "SrO": "P0",
    # P1 — B级 T2 > 2ms
    "CaS": "P1", "CaCO3": "P1", "CaMg2(SO4)3": "P1",
    "BaGe2S5": "P1", "Sr2MgGe2O7": "P1", "Sr2MgSi2O7": "P1",
    "Ca2Ge7O16": "P1", "SrGe4O9": "P1", "BaGe4O9": "P1",
    # P2 — C级 扩展候选
    "CaSe": "P2", "SeO2": "P2", "SrSe": "P2",
    "BaS3": "P2", "Ba2MgGe2O7": "P2", "GeSe2": "P2",
    "MgCO3": "P2", "Ba2MgSi2O7": "P2", "SrTe": "P2",
    "BaSe": "P2", "BaS": "P2", "Sn(SeO3)2": "P2",
    "BaTe": "P2", "Mg3TeO6": "P2", "Ba2TeO": "P2",
    "BaO2": "P2", "BaO": "P2",
    # P3 — 特殊体系
    "CeO2": "P3",
    # P4 — 已有体系
    "AlN": "P4", "CaO": "P4", "diamond": "P4",
    "GaN": "P4", "hBN": "P4", "MgO": "P4",
    "MoS2": "P4", "SiC": "P4", "ZnO": "P4",
}


def _add_batch_parser(subparsers) -> None:
    """Add ``batch`` subcommand with actions."""
    p = subparsers.add_parser("batch", help="Multi-system batch operations")
    sub = p.add_subparsers(dest="batch_action", required=True)

    # status
    sp = sub.add_parser("status", help="Show status table for all systems")
    sp.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )

    # generate-inputs
    gp = sub.add_parser("generate-inputs", help="Generate VASP inputs for all systems")
    gp.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )
    gp.add_argument(
        "--unitcell", action="store_true",
        help="Also generate unitcell inputs (structure_opt/band/dos/dielectric)",
    )

    # submit
    sp2 = sub.add_parser("submit", help="Submit VASP calculations for all systems")
    sp2.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )
    sp2.add_argument(
        "--all-phases", action="store_true",
        help="Submit all competing phases (default: target phase only)",
    )

    # run
    rp = sub.add_parser("run", help="Run batch pipeline — advance all systems until completion")
    rp.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )
    rp.add_argument(
        "--poll", type=int, default=60,
        help="Poll interval in seconds (default: 60)",
    )


def _handle_batch(args: argparse.Namespace) -> None:
    if args.batch_action == "status":
        _batch_status(args.root.resolve())
    elif args.batch_action == "generate-inputs":
        _batch_generate_inputs(args.root.resolve(), unitcell=args.unitcell)
    elif args.batch_action == "submit":
        _batch_submit(args.root.resolve(), all_phases=args.all_phases)
    elif args.batch_action == "run":
        _batch_run(args.root.resolve(), poll_interval=args.poll)



def _batch_status(root: Path) -> None:
    """Scan *root* for vasp-sop systems and print status table."""
    from vasp_sop.vasp.io import check_converged, input_ready
    from vasp_sop.core.state import StateStore, StepStatus

    rows: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan = d / "plan.yaml"
        if not plan.is_file():
            continue
        rows.append(_scan_system(d, plan))

    if not rows:
        print(f"No vasp-sop systems found in {root}")
        return

    print(f"{'System':<18} {'P':<3} {'VASPin':<7} {'CPD':<5} {'Unitcell':<9} {'Defect':<7}")
    print("-" * 55)
    for r in rows:
        print(f"{r['name']:<18} {r['pri']:<3} {r['vasp_in']:<7} {r['cpd']:<5} {r['uc']:<9} {r['defect']:<7}")

    total = len(rows)
    done = sum(1 for r in rows if r['cpd'] == '✓' and r['uc'] == '✓' and r['defect'] == '✓')
    print("-" * 55)
    print(f"{total} systems  |  {done} complete")

def _batch_run(root: Path, *, poll_interval: int = 60) -> None:
    """Batch pipeline — advance all systems independently until completion.

    Each system cycles through: TARGET → COMPETING → CPD_POST → UC_DF → DONE.
    Disk state is the source of truth (resume-safe).
    No job limit — submits everything ready for submission.
    """
    import time as _time
    from vasp_sop.vasp.io import check_converged, input_ready, prepare_inputs
    from vasp_sop.core.jobs import submit_vasp, move_crisp_outputs
    from vasp_sop.core.config import PipelineConfig
    from vasp_sop.defect import unitcell as _uc
    from vasp_sop.defect import cpd as _cpd
    from vasp_sop.defect.builder import build_all as _build_defects
    from vasp_sop.defect.analysis import analyze as _analyze_defects

    _CPD = "cpd"
    _UC = "unitcell"
    _DF = "defect"

    # ── Collect systems ─────────────────────────────────────────────
    sys_list: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan_path = d / "plan.yaml"
        if not plan_path.is_file():
            continue
        try:
            config = PipelineConfig.from_yaml(plan_path, root=d)
        except Exception:
            continue
        src = config.poscar_src
        mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
        sys_list.append({
            "name": config.formula or d.name,
            "root": d,
            "config": config,
            "formula": config.formula,
            "mpid": mpid,
        })

    if not sys_list:
        print("No systems found.")
        return

    print(f"Batch run: {len(sys_list)} systems\n")

    # ── Helpers ─────────────────────────────────────────────────────

    def _target_dir(s: dict) -> Path | None:
        if not s["mpid"]:
            return None
        cpd_dir = s["root"] / _CPD
        for pd in cpd_dir.iterdir():
            if pd.is_dir() and s["mpid"] in pd.name:
                return pd
        return None

    def _competing_dirs(s: dict) -> list[Path]:
        td = _target_dir(s)
        cpd_dir = s["root"] / _CPD
        return sorted(
            pd for pd in cpd_dir.iterdir()
            if pd.is_dir() and pd.name != td.name and pd.name not in ("combos", "mp_flag")
            and input_ready(pd) and not check_converged(pd)
        )

    def _phase(s: dict) -> str:
        td = _target_dir(s)
        if td is None:
            return "NO_TARGET"
        if not check_converged(td):
            return "TARGET"
        if _competing_dirs(s):
            return "COMPETING"
        cpd_root = s["root"] / _CPD
        if not (cpd_root / "target_vertices.yaml").is_file():
            return "CPD_POST"
        uc_root = s["root"] / _UC
        uc_tasks = ["band", "dos", "dielectric"]
        uc_pending = any(
            not check_converged(uc_root / t) for t in uc_tasks
            if (uc_root / t / "INCAR").is_file()
        )
        df_root = s["root"] / _DF
        df_done = (df_root / "defect_energy_summary.json").is_file() if df_root.is_dir() else True
        if uc_pending or not df_done:
            return "UC_DF"
        return "DONE"

    # ── Main loop ───────────────────────────────────────────────────
    active: dict[str, str] = {}  # work_dir (str) -> phase label

    def _cache_target_vasp(wd: Path) -> None:
        """Cache completed target phase VASP results."""
        for s in sys_list:
            td = _target_dir(s)
            if td and td.resolve() == wd:
                f, m = s["formula"], s["mpid"]
                if f and m:
                    from vasp_sop.core.cache import calc_results_put
                    try:
                        calc_results_put(f, m, wd)
                    except Exception:
                        pass
                break

    while True:
        # 1. Poll active jobs
        for wd_str in list(active):
            wd = Path(wd_str)
            if check_converged(wd):
                move_crisp_outputs(wd)
                _cache_target_vasp(wd)
                del active[wd_str]
                logger.info("Completed: %s", wd.name)

        # 2. Status snapshot
        phases = [_phase(s) for s in sys_list]
        done_count = sum(1 for p in phases if p == "DONE")
        running = len(active)

        # 3. Print status line
        counts = {p: phases.count(p) for p in sorted(set(phases))}
        parts = [f"{p}={n}" for p, n in sorted(counts.items())]
        print(f"  [{running} running]  {'  '.join(parts)}")

        # 4. Done?
        if done_count == len(sys_list) and running == 0:
            print("\nAll systems complete.")
            break

        # 5. Advance all systems
        for s in sys_list:
            p = _phase(s)
            if p == "DONE" or p == "NO_TARGET":
                continue

            if p == "TARGET":
                td = _target_dir(s)
                if td and str(td.resolve()) not in active and not check_converged(td):
                    f, m = s["formula"], s["mpid"]
                    cached = None
                    if f and m:
                        from vasp_sop.core.cache import calc_results_get
                        cached = calc_results_get(f, m)
                    if cached:
                        import shutil as _sh
                        for fn in ("OUTCAR", "CONTCAR", "vasprun.xml"):
                            src = cached / fn
                            if src.is_file():
                                _sh.copy2(str(src), str(td / fn))
                        logger.info("%s restored from calc cache", s["name"])
                        import json as _json
                        submit_info = {
                            "task_name": "cached",
                            "work_dir": str(td.resolve()),
                        }
                        with open((s["root"] / _CPD / ".target_submit.json"), "w") as _f:
                            _json.dump(submit_info, _f)
                    else:
                        try:
                            job = submit_vasp(td.resolve())
                            active[str(td.resolve())] = "target"
                            print(f"  → {s['name']:<18} target: {job.task_name}")
                        except Exception as exc:
                            logger.warning("%s target submit failed: %s", s["name"], exc)

            elif p == "COMPETING":
                for cd in _competing_dirs(s):
                    if str(cd.resolve()) in active:
                        continue
                    try:
                        job = submit_vasp(cd.resolve())
                        active[str(cd.resolve())] = "competing"
                        print(f"  → {s['name']:<18} phase: {cd.name} → {job.task_name}")
                    except Exception as exc:
                        logger.warning("%s/%s submit failed: %s", s["name"], cd.name, exc)

            elif p == "CPD_POST":
                for pd in cpd_root.iterdir():
                    if pd.is_dir():
                        move_crisp_outputs(pd)
                logger.info("%s: CPD post-processing ...", s["name"])
                try:
                    target_composition = _cpd._get_target_composition(s["formula"])
                    _cpd.compute_chemical_potentials(cpd_root, s["config"], target_composition)
                    f, m = s["formula"], s["mpid"]
                    if f and m:
                        from vasp_sop.core.cache import cache_target_results
                        try:
                            cache_target_results(f, m, _target_dir(s), cpd_root)
                        except Exception:
                            pass
                except Exception as exc:
                    logger.error("%s CPD failed: %s", s["name"], exc)
                    print(f"  ✗ {s['name']:<18} CPD post-processing FAILED")

            elif p == "UC_DF":
                td = _target_dir(s)
                if td and not (uc_root / "band" / "INCAR").is_file():
                    _uc._prepare_all_inputs(uc_root, td, s["config"])
                if td and not (df_root / "perfect" / "INCAR").is_file():
                    if not (df_root / "defect_in.yaml").is_file():
                        _build_defects(df_root, td, s["config"])
                    else:
                        from vasp_sop.defect.builder import _generate_vasp_inputs
                        _generate_vasp_inputs(df_root, s["config"])

                for task in ("band", "dos", "dielectric"):
                    task_dir = uc_root / task
                    if not task_dir.is_dir():
                        continue
                    if check_converged(task_dir):
                        continue
                    if str(task_dir.resolve()) in active:
                        continue
                    prepare_inputs(task_dir, s["config"], task_type=task)
                    try:
                        job = submit_vasp(task_dir.resolve())
                        active[str(task_dir.resolve())] = f"uc-{task}"
                        print(f"  → {s['name']:<18} unitcell: {task} → {job.task_name}")
                    except Exception as exc:
                        logger.warning("%s/%s submit failed: %s", s["name"], task, exc)

                if df_root.is_dir() and not (df_root / "defect_energy_summary.json").is_file():
                    for child in sorted(df_root.iterdir()):
                        if not child.is_dir():
                            continue
                        if not input_ready(child):
                            continue
                        if check_converged(child):
                            continue
                        if str(child.resolve()) in active:
                            continue
                        try:
                            job = submit_vasp(child.resolve())
                            active[str(child.resolve())] = f"df-{child.name}"
                            print(f"  → {s['name']:<18} defect: {child.name} → {job.task_name}")
                        except Exception as exc:
                            logger.warning("%s/%s submit failed: %s", s["name"], child.name, exc)

                uc_all_done = all(
                    check_converged(uc_root / t) or not (uc_root / t / "INCAR").is_file()
                    for t in ("band", "dos", "dielectric")
                )
                df_vasp_done = all(
                    check_converged(child) or not input_ready(child)
                    for child in df_root.iterdir() if child.is_dir()
                ) if df_root.is_dir() else True

                if uc_all_done and df_vasp_done and (df_root / "defect_energy_summary.json").is_file():
                    pass
                elif uc_all_done and df_vasp_done:
                    logger.info("%s: post-processing ...", s["name"])
                    try:
                        _uc.build_unitcell_yaml(uc_root, s["config"])
                        _analyze_defects(
                            df_root, s["root"], s["config"],
                            unitcell_yaml=uc_root / "unitcell.yaml",
                            standard_energies=cpd_root / "standard_energies.yaml",
                            target_vertices=cpd_root / "target_vertices.yaml",
                        )
                        print(f"  ✓ {s['name']:<18} pipeline complete")
                    except Exception as exc:
                        logger.error("%s post-processing failed: %s", s["name"], exc)

        _time.sleep(poll_interval)


def _batch_generate_inputs(root: Path, *, unitcell: bool = False) -> None:
    """Generate VASP inputs for all systems in *root* that need them."""
    import concurrent.futures
    from vasp_sop.vasp.io import input_ready, prepare_inputs
    from vasp_sop.core.config import PipelineConfig
    import logging
    log = logging.getLogger(__name__)

    _CPD_DIR = "cpd"
    _UC_DIR = "unitcell"

    # Collect all phase dirs that need inputs
    tasks: list[tuple[str, str, Path, Path]] = []  # (sys_name, phase_name, phase_dir, plan_path)
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan_path = d / "plan.yaml"
        if not plan_path.is_file():
            continue
        cpd_dir = d / _CPD_DIR
        if not cpd_dir.is_dir():
            continue
        for pd in sorted(cpd_dir.iterdir()):
            if not pd.is_dir() or pd.name == "combos":
                continue
            if not input_ready(pd):
                tasks.append((d.name, pd.name, pd, plan_path))

    if not tasks:
        print("All phase directories already have VASP inputs.")
        return

    print(f"Generating inputs for {len(tasks)} phase directories across {len(set(t[0] for t in tasks))} systems ...")

    def _gen_one(sys_name: str, phase_name: str, phase_dir: Path, plan_path: Path) -> str:
        try:
            config = PipelineConfig.from_yaml(plan_path, root=phase_dir.parent.parent)
            prepare_inputs(phase_dir, config)
            return f"OK  {sys_name}/{phase_name}"
        except Exception as exc:
            return f"FAIL {sys_name}/{phase_name}: {exc}"

    ok = 0
    fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(_gen_one, *t): t for t in tasks}
        for fut in concurrent.futures.as_completed(fut_map):
            t = fut_map[fut]
            try:
                msg = fut.result()
                if msg.startswith("OK"):
                    ok += 1
                else:
                    fail += 1
                print(f"  {msg}")
            except Exception as exc:
                fail += 1
                print(f"  FAIL {t[0]}/{t[1]}: {exc}")

    print(f"Done: {ok} generated, {fail} failed")


def _batch_submit(root: Path, *, all_phases: bool = False) -> None:
    """Submit VASP calculations for all systems."""
    import json
    from vasp_sop.vasp.io import input_ready, check_converged
    from vasp_sop.core.jobs import submit_vasp

    _CPD_DIR = "cpd"

    all_jobs: list[tuple[str, str, str]] = []  # (sys, phase, task_name)
    skipped = 0

    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan_path = d / "plan.yaml"
        if not plan_path.is_file():
            continue

        cpd_dir = d / _CPD_DIR
        if not cpd_dir.is_dir():
            continue

        # Identify target phase from poscar_src in plan.yaml
        formula = "?"
        target_mpid = None
        try:
            import yaml as _yaml
            with open(plan_path) as f:
                data = _yaml.safe_load(f)
            p = (data or {}).get("project", {})
            formula = p.get("formula", "?")
            src = p.get("poscar_src", "")
            if src.startswith("MP mp-"):
                target_mpid = "mp-" + src.split("mp-", 1)[1]
        except Exception:
            pass

        target_name = None
        other_phases: list[Path] = []
        for pd in sorted(cpd_dir.iterdir()):
            if not pd.is_dir() or pd.name == "combos" or pd.name == "mp_flag":
                continue
            if not input_ready(pd):
                continue
            if target_name is None and target_mpid and target_mpid in pd.name:
                target_name = pd.name
            elif target_name is None and target_mpid is None and formula.replace(" ", "") in pd.name.replace(" ", ""):
                target_name = pd.name
            else:
                other_phases.append(pd)

        if target_name is None:
            print(f"  {d.name:<18} no target phase found, skipped")
            continue

        target_dir = cpd_dir / target_name
        # Skip if already converged
        if check_converged(target_dir):
            print(f"  {d.name:<18} target already converged, skipped")
            skipped += 1
            continue

        # Submit target phase
        try:
            job = submit_vasp(target_dir.resolve())
            all_jobs.append((d.name, target_name, job.task_name))
            print(f"  {d.name:<18} target: {job.task_name}")
            # Write .target_submit.json for pipeline resume
            submit_info = {
                "task_name": job.task_name,
                "work_dir": str(target_dir.resolve()),
            }
            with open(cpd_dir / ".target_submit.json", "w") as f:
                json.dump(submit_info, f)
        except Exception as exc:
            print(f"  {d.name:<18} target FAIL: {exc}")

        # Submit competing phases (only if --all-phases)
        if not all_phases:
            continue

        for pd in other_phases:
            if check_converged(pd):
                continue
            try:
                job = submit_vasp(pd.resolve())
                all_jobs.append((d.name, pd.name, job.task_name))
                print(f"  {d.name:<18}   phase: {pd.name} → {job.task_name}")
            except Exception as exc:
                print(f"  {d.name:<18}   phase: {pd.name} FAIL: {exc}")


    # Summary
    print("-" * 55)
    if skipped:
        print(f"Skipped {skipped} already-converged system(s)")
    if all_jobs:
        print(f"Submitted {len(all_jobs)} job(s):")
        for sys, phase, task in all_jobs:
            print(f"  {sys}/{phase}: {task}")
    else:
        print("No jobs submitted.")




def _scan_system(d: Path, plan: Path) -> dict:
    """Inspect a single system directory and return status dict."""
    import yaml
    formula = "?"
    try:
        with open(plan) as f:
            data = yaml.safe_load(f)
        formula = (data or {}).get("project", {}).get("formula", "?")
    except Exception:
        pass

    pri = _PRIORITY_MAP.get(formula, "—")

    # VASP inputs ready?
    from vasp_sop.vasp.io import input_ready
    cpd = d / "cpd"
    vasp_ready = "·"
    if cpd.is_dir():
        phase_dirs = [x for x in cpd.iterdir() if x.is_dir() and x.name != "combos"]
        if any(input_ready(x) for x in phase_dirs):
            vasp_ready = "✓"

    # CPD stage
    cpd_status = _check_cpd(cpd)

    # Unitcell stage
    uc_status = _check_unitcell(d / "unitcell")

    # Defect stage
    defect_status = _check_defect(d / "defect")

    return {
        "name": formula,
        "pri": pri,
        "vasp_in": vasp_ready,
        "cpd": cpd_status,
        "uc": uc_status,
        "defect": defect_status,
    }


def _check_cpd(cpd_dir: Path) -> str:
    """Determine CPD stage status from disk state."""
    from vasp_sop.vasp.io import check_converged

    if not cpd_dir.is_dir():
        return "·"

    # Post-processing done?
    if (cpd_dir / "target_vertices.yaml").is_file():
        return "✓"

    # Any phase directory has converged OUTCAR?
    for child in cpd_dir.iterdir():
        if not child.is_dir():
            continue
        if check_converged(child):
            return "▶"

    # Any VASP input exists?
    from vasp_sop.vasp.io import input_ready
    for child in cpd_dir.iterdir():
        if child.is_dir() and input_ready(child):
            return "·"  # inputs ready, VASP not run

    return "·"


def _check_unitcell(uc_dir: Path) -> str:
    """Determine unitcell stage status."""
    from vasp_sop.vasp.io import check_converged, input_ready

    if not uc_dir.is_dir():
        return "·"

    stages = ["structure_opt", "band", "dos", "dielectric"]
    results = []
    for s in stages:
        sd = uc_dir / s
        if not sd.is_dir():
            results.append("·")
        elif check_converged(sd):
            results.append("✓")
        elif input_ready(sd):
            results.append("▶")  # inputs ready, VASP not run yet
        else:
            results.append("·")  # directory exists but no inputs

    done_count = sum(1 for r in results if r == "✓")
    if done_count == len(stages):
        return "✓"
    elif done_count > 0:
        return f"{done_count}/4"
    elif any(r == "▶" for r in results):
        return "▶"
    else:
        return "·"
def _check_defect(df_dir: Path) -> str:
    """Determine defect stage status."""
    from vasp_sop.vasp.io import check_converged, input_ready

    if not df_dir.is_dir():
        return "·"

    # No supercell? barely started
    if not (df_dir / "supercell_info.json").is_file():
        return "·"

    # Defect energy summary exists → fully done
    if (df_dir / "defect_energy_summary.json").is_file():
        return "✓"

    # Check VASP results
    has_perfect = check_converged(df_dir / "perfect") if (df_dir / "perfect").is_dir() else False

    # Count defect dirs with VASP done vs total
    defect_dirs = [x for x in df_dir.iterdir()
                   if x.is_dir() and x.name != "perfect" and input_ready(x)]
    done_dirs = sum(1 for x in defect_dirs if check_converged(x))

    if has_perfect and done_dirs == len(defect_dirs) and len(defect_dirs) > 0:
        return "▶"  # VASP done, post-processing pending
    elif has_perfect or done_dirs > 0:
        return f"{done_dirs}/{len(defect_dirs) or '?'}"
    elif (df_dir / "defect_in.yaml").is_file():
        return "▶"  # structures generated, VASP not run
    else:
        return "·"

if __name__ == "__main__":
    main()

