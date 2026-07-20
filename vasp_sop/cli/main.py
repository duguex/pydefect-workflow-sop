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
import re
import sys
from pathlib import Path
from vasp_sop import __version__
from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.batch_lifecycle import (
    cleanup,
    daemonize,
    is_stop_requested,
    stop as _lifecycle_stop,
)

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

    # run — CPD-only entrypoint (issue #93)
    run_p = sub.add_parser("run", help="Run ONLY the CPD phase (competing + CPD solve, no UC/defect)")
    run_p.add_argument("system_dir", type=Path, help="System root directory (contains cpd/, plan.yaml)")
    run_p.add_argument("-f", "--formula", type=str, required=True, help="Target formula (e.g. GaN)")
    run_p.add_argument("--dry-run", action="store_true", help="Do not submit VASP jobs")

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
            formula="fix", root=Path.cwd(), functional=args.functional,
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


def _handle_report(args: argparse.Namespace) -> None:
    from vasp_sop.core.report import generate_report

    report_path = generate_report(args.system_dir, args.output)
    print(f"Report written to {report_path}")


def _add_report_parser(subparsers) -> None:
    """Add the read-only calculation report command."""
    report_parser = subparsers.add_parser(
        "report", help="Generate a calculation report from project artifacts"
    )
    report_parser.add_argument(
        "system_dir", type=Path, help="System directory containing plan.yaml"
    )
    report_parser.add_argument(
        "--output", type=Path, help="Output Markdown path (default: system_dir/calculation_report.md)"
    )


def _handle_cpd(args: argparse.Namespace) -> None:
    from vasp_sop.materials import get_intrinsic_elements
    from vasp_sop.defect.cpd import compute_chemical_potentials, adjust_unstable_phase
    from vasp_sop.core.config import PipelineConfig
    from pymatgen.core import Composition

    if args.action == "run":
        from vasp_sop.core.orchestrator import cpd_only

        system_dir = args.system_dir.resolve()
        if not system_dir.is_dir():
            raise SystemExit(f"System directory not found: {system_dir}")
        plan_path = system_dir / "plan.yaml"
        if plan_path.is_file():
            config = PipelineConfig.from_yaml(plan_path, root=system_dir)
        else:
            config = PipelineConfig(formula=args.formula, root=system_dir)
        result = cpd_only(
            system_dir, args.formula, config, dry_run=args.dry_run
        )
        status = result.get("status", "unknown")
        print(f"CPD-only result: phase={result.get('phase')}, status={status}")
        if status == "not_ready":
            raise SystemExit(1)
        return

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
    _add_report_parser(subparsers)
    _add_batch_parser(subparsers)
    _add_cache_parser(subparsers)

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
    if args.command == "report":
        _handle_report(args)
    elif args.command == "materials":
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
    elif args.command == "cache":
        _handle_cache(args)


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

    # inventory — list defect dirs and ignored trees
    inv_parser = defect_sub.add_parser("inventory", help="List defect directories and ignored trees")
    inv_parser.add_argument("project_dir", type=Path, help="Project root directory")
    inv_parser.add_argument("--include-defect-new", action="store_true",
                            help="Include defect_new/ parallel tree")


def _handle_defect(args: argparse.Namespace) -> None:
    if args.action == "init":
        _do_init(args)
    elif args.action == "status":
        _do_status(args)
    elif args.action == "run":
        _do_run(args)
    elif args.action == "resume":
        _do_resume(args)
    elif args.action == "build":
        print("defect build: standalone defect structure generation not yet implemented. Use 'batch run' instead.")
    elif args.action == "analyze":
        _do_defect_analyze(args)
    elif args.action == "inventory":
        _do_defect_inventory(args)


def _do_defect_analyze(args: argparse.Namespace) -> None:
    """Run pydefect defect post-processing for one project root (#0014)."""
    from vasp_sop.core.config import PipelineConfig
    from vasp_sop.defect.analysis import analyze

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        raise SystemExit(f"project_dir not found: {project}")
    plan = project / "plan.yaml"
    if not plan.is_file():
        raise SystemExit(f"plan.yaml missing in {project}")
    cfg = PipelineConfig.from_yaml(plan, root=project)
    df = project / "defect"
    uc_yaml = project / "unitcell" / "unitcell.yaml"
    se = project / "cpd" / "standard_energies.yaml"
    tv = project / "cpd" / "target_vertices.yaml"
    for p, label in (
        (df, "defect/"),
        (uc_yaml, "unitcell/unitcell.yaml"),
        (se, "cpd/standard_energies.yaml"),
        (tv, "cpd/target_vertices.yaml"),
    ):
        if not p.exists():
            raise SystemExit(f"missing {label} under {project}")
    status = analyze(df, project, cfg, uc_yaml, se, tv)
    print(f"{project.name}: analyze status={status}")
    if status == "failed":
        raise SystemExit(1)



def _do_defect_inventory(args: argparse.Namespace) -> None:
    """Print defect directory inventory, including ignored trees."""
    from vasp_sop.defect.analysis import _inventory
    from vasp_sop.defect import DEFECT_NEW_DIR

    project = args.project_dir.resolve()
    df = project / "defect"
    if not df.is_dir():
        print(f"defect/ not found in {project}")
        return

    inv = _inventory(df)
    print(f"Defect inventory for {project.name}:")
    print(f"  dirs (valid defect):          {len(inv['dirs'])}")
    print(f"  converged (ionic):           {len(inv['converged'])}")
    print(f"  unconverged:                 {len(inv['unconverged'])}")
    print(f"  with correction.json:        {len(inv['corrected'])}")

    all_subdirs = {d for d in df.iterdir() if d.is_dir()}
    ignored = all_subdirs - set(inv['dirs'])
    if ignored:
        print(f"\n  Ignored under defect/ ({len(ignored)}):")
        for d in sorted(ignored):
            reason = "no _ in name" if "_" not in d.name else "junk"
            if d.name == DEFECT_NEW_DIR:
                reason = "defect_new (use --include-defect-new)"
            print(f"    {d.name} ({reason})")

    if args.include_defect_new:
        dn = project / DEFECT_NEW_DIR
        if dn.is_dir():
            dn_inv = _inventory(dn)
            print(f"\n  defect_new/ included (sibling tree):")
            print(f"    valid dirs:  {len(dn_inv['dirs'])}")
            print(f"    converged:   {len(dn_inv['converged'])}")
            print(f"    unconverged: {len(dn_inv['unconverged'])}")
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
    """Print pipeline status for a project using filesystem + _phase()."""
    root = args.root.resolve()
    print(f"Project: {root}")

    # Use _phase() to determine current pipeline stage
    from vasp_sop.core.config import PipelineConfig
    plan = root / "plan.yaml"
    if not plan.is_file():
        print("  No plan.yaml found — system not initialized.")
        return

    config = PipelineConfig.from_yaml(plan, root=root)
    src = config.poscar_src
    mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
    s = {
        "name": config.formula or root.name,
        "root": root,
        "config": config,
        "formula": config.formula,
        "mpid": mpid,
    }
    p = _phase(s)
    print(f"  Pipeline phase: {p}")

    # Per-stage detail from filesystem
    cpd_dir = root / "cpd"
    uc_dir = root / "unitcell"
    df_dir = root / "defect"

    has_target = any(
        (pd / "OUTCAR").is_file() or (pd / "output" / "OUTCAR").is_file()
        for pd in cpd_dir.iterdir() if pd.is_dir()
    ) if cpd_dir.is_dir() else False
    print(f"  Target OUTCAR: {'✓' if has_target else '·'}")

    print(f"  CPD:      {_check_cpd(cpd_dir)}")
    print(f"  Unitcell: {_check_unitcell(uc_dir)}")
    print(f"  Defect:   {_check_defect(df_dir)}")

def _do_run(args: argparse.Namespace) -> None:
    """Run (or resume) the full defect pipeline."""
    config = PipelineConfig.from_yaml(args.config, root=args.root.resolve())
    _run_pipeline(config)


def _do_resume(args: argparse.Namespace) -> None:
    """Resume pipeline from config (state now filesystem-based)."""
    root = args.root.resolve()
    from vasp_sop.core.config import PLAN_FILENAME
    config_path = root / PLAN_FILENAME
    if not config_path.is_file():
        config_path = root / "config.yaml"
    if config_path.is_file():
        config = PipelineConfig.from_yaml(config_path, root=root)
    else:
        legacy_json = root / "info.json"
        if legacy_json.is_file():
            logger.info("Found legacy info.json — migrating to %s.", PLAN_FILENAME)
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
    _run_pipeline(config)


def _run_pipeline(config: PipelineConfig) -> None:
    """Execute the pipeline using the batch-style single-system loop.

    Replaced ``run_point_defect_pipeline`` from ``pipeline.py`` after
    the legacy pipeline was removed (see issue #78).
    """
    logger.info(
        "Starting point-defect pipeline for %s (root: %s)",
        config.formula, config.root,
    )

    # Build a system dict matching what _batch_run creates
    from vasp_sop.core.config import PLAN_FILENAME
    plan_path = config.root / PLAN_FILENAME

    # ── Build CPD structure + defect structures upfront ─────────────
    # (mirrors the legacy Wave-1 behaviour where all inputs are
    # generated before any VASP is submitted)
    from vasp_sop.defect import cpd as _cpd
    from vasp_sop.defect.builder import build_all as _build_defects
    from vasp_sop.defect import unitcell as _uc
    from vasp_sop.materials import get_intrinsic_elements

    cpd_root = config.root / "cpd"
    uc_root = config.root / "unitcell"
    df_root = config.root / "defect"

    if cpd_root.is_dir():
        intrinsic = get_intrinsic_elements(config.formula)
        cpd_info = _cpd._get_cpd_info(cpd_root, intrinsic)
        target_dir, other_dirs = _cpd._split_target(cpd_root, cpd_info, config.formula)
        for d in other_dirs:
            from vasp_sop.vasp.io import prepare_inputs
            prepare_inputs(d, config)
        from vasp_sop.vasp.io import input_ready
        if target_dir and not input_ready(target_dir):
            from vasp_sop.vasp.io import prepare_inputs
            prepare_inputs(target_dir, config)
        if df_root.is_dir() and target_dir and (target_dir / "POSCAR").is_file():
            _build_defects(df_root, target_dir, config)

    # ── Polling loop (same semantics as _batch_run) ─────────────────
    import time as _time
    from vasp_sop.vasp.io import check_converged
    from vasp_sop.core.cache import cache_lookup
    from vasp_sop.core.jobs import move_crisp_outputs

    s = {
        "name": config.formula or config.root.name,
        "root": config.root,
        "config": config,
        "formula": config.formula,
    }
    mpid = config.poscar_src.split("mp-", 1)[1] if config.poscar_src.startswith("MP mp-") else None
    s["mpid"] = mpid

    for _iteration in range(200):
        # Poll completed submissions
        from vasp_sop.core.job_store import JobStore
        _js = JobStore()
        for wd_str in list(_js.tracked_dirs()):
            wd = Path(wd_str)
            if check_converged(wd):
                move_crisp_outputs(wd)
                try:
                    from vasp_sop.core.cache import vasp_results_put
                    key = vasp_results_put(wd)
                    if key is None:
                        logger.warning(
                            "%s: cache put returned None (missing output files?)",
                            wd.name,
                        )
                except Exception as exc:
                    logger.warning("Failed to cache %s: %s", wd.name, exc)
                _js.untrack(wd_str)
                _js.record(wd_str, "converged")
        _js.close()

        # Advance step
        _advance_one_system(s, dry_run=False)

        # Check completion — _phase is defined in this module
        p = _phase(s)
        if p in ("COMPLETE", "NO_TARGET"):
            logger.info("Pipeline complete (phase=%s).", p)
            return
        _time.sleep(60)
    else:
        logger.error("Pipeline did not complete after 200 iterations.")
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


def _add_cache_parser(subparsers) -> None:
    """Add ``cache`` subcommand with actions."""
    p = subparsers.add_parser("cache", help="Manage VASP calculation results cache")
    p.add_argument("--cache-root", type=Path, default=None,
                   help="vasp-cache root directory (default: $VASP_CACHE_ROOT or ~/.cache/vasp_cache)")
    sub = p.add_subparsers(dest="cache_action", required=True)

    # status
    sp = sub.add_parser("status", help="Show cache statistics")
    sp.add_argument("--verbose", action="store_true", help="List all entries")

    # put
    sp = sub.add_parser("put", help="Cache a VASP calculation directory")
    sp.add_argument("path", type=Path, help="Path to calculation directory")
    sp.add_argument("--formula", help="Chemical formula (auto-detected if omitted)")
    sp.add_argument("--task-name", help="Task name (auto-detected if omitted)")
    sp.add_argument("--recursive", "-r", action="store_true",
                    help="Recursively scan directory tree for OUTCARs")

    # query
    sp = sub.add_parser("query", help="Cross-project cache query")
    sp.add_argument("--formula", "-f", help="Filter by chemical formula")
    sp.add_argument("--limit", type=int, default=50, help="Max results")

    # migrate
    sp = sub.add_parser("migrate", help="Migrate from old SQLite cache to vasp-cache")
    sp.add_argument("--force", action="store_true",
                    help="Force migration even if vasp-cache already has data")

    # verify
    sub.add_parser("verify", help="Check store consistency")

def _handle_cache(args: argparse.Namespace) -> None:
    from vasp_sop.core.cache import (
        cache_lookup, vasp_results_put, query, list_cache,
        cache_stats, migrate_from_sqlite,
    )
    from pathlib import Path
    cr = args.cache_root

    if args.cache_action == "put":
        if args.recursive:
            root = args.path.resolve()
            if not root.is_dir():
                print(f"Not a directory: {root}")
                return

            from tqdm import tqdm

            all_dirs: list[Path] = []
            for outcar in sorted(root.rglob("OUTCAR")):
                d = outcar.parent
                if d.name == "output" and (d.parent / "OUTCAR").is_file():
                    continue
                all_dirs.append(d)

            if not all_dirs:
                print("No OUTCARs found.")
                return

            to_cache: list[Path] = []
            unconverged: list[Path] = []

            for d in tqdm(all_dirs, desc="Scanning", unit=" dirs"):
                if cache_lookup(d, cache_root=cr) is not None:
                    continue
                outcar = d / "OUTCAR"
                if not outcar.is_file():
                    unconverged.append(d)
                    continue
                size = outcar.stat().st_size
                n = 4096
                if size <= n:
                    tail = outcar.read_text()
                else:
                    with outcar.open("rb") as f:
                        f.seek(size - n)
                        tail = f.read().decode("utf-8", errors="replace")
                if "General timing and accounting" in tail:
                    to_cache.append(d)
                else:
                    unconverged.append(d)

            cached_count = len(all_dirs) - len(to_cache) - len(unconverged)
            if cached_count:
                print(f"  {cached_count} directories already cached, skipped.")
            for d in unconverged:
                print(f"  ! {d} (not converged)")

            if to_cache:
                total_cached = 0
                for d in tqdm(to_cache, desc="Caching", unit=" dirs"):
                    try:
                        key = vasp_results_put(d, cache_root=cr)
                        if key:
                            total_cached += 1
                        else:
                            logger.warning(
                                "%s: cache put returned None (missing output files?)",
                                d.name,
                            )
                            print(f"\n  ! {d} (identity failed)")
                    except Exception as exc:
                        print(f"\n  ! {d} (put failed: {exc})")
                print(f"Cached {total_cached} directories under {root}")

            if unconverged:
                print(f"Skipped {len(unconverged)} unconverged directories")
            return

        path = args.path.resolve()
        outcar = path / "OUTCAR"
        if not outcar.is_file():
            print(f"No OUTCAR in {path}, skipping.")
            return
        text = outcar.read_text()
        converged = "General timing and accounting" in text[-4096:]
        key = vasp_results_put(path, cache_root=cr)
        if key is None:
            logger.warning(
                "%s: cache put returned None (missing output files?)",
                path.name,
            )
        status = "converged" if converged else "not converged"
        print(f"Cached {path} ({status})" + (f"  key={key}" if key else ""))
        return

    if args.cache_action == "status":
        stats = cache_stats(cache_root=cr)
        n_entries = stats.get("entries", 0)
        n_formulas = stats.get("formulas", 0)
        blob_bytes = stats.get("total_blob_bytes", 0)
        print(f"vasp_results: {n_entries} entries  "
              f"({n_formulas} unique formulas)  "
              f"{blob_bytes:,} B blob storage")

        if args.verbose:
            print()
            for entry in list_cache(limit=200, cache_root=cr):
                c = "✓" if entry.get("converged_ionic") else " "
                e_val = entry.get("final_energy")
                e = f"{e_val:.4f}" if e_val is not None else "?"
                src = entry.get("source_path") or "?"
                ts = entry.get("created_at") or "?"
                ident = entry.get("identity_key", "")[:12]
                print(f"  {c} {entry['formula']:12s} {ident:12s}"
                      f"  E={e}  {ts}  {src}")

    elif args.cache_action == "query":
        results = query(formula=args.formula, limit=args.limit, cache_root=cr)
        print(f"{len(results)} results:")
        for r in results:
            e_val = r.get("final_energy")
            e = f"{e_val:.4f}" if e_val is not None else "?"
            print(f"  {r['formula']:12s}  E={e}"
                  f"  ionic={r.get('converged_ionic', 0)}")

    elif args.cache_action == "migrate":
        stats = cache_stats(cache_root=cr)
        if stats.get("entries", 0) > 0 and not args.force:
            print(f"Cache already has {stats['entries']} entries. "
                  f"Use --force to overwrite.")
            return
        n = migrate_from_sqlite()
        print(f"Migrated {n} records from SQLite cache.db.")

    elif args.cache_action == "verify":
        stats = cache_stats(cache_root=cr)
        entries = list_cache(limit=10000, cache_root=cr)
        from collections import defaultdict
        by_formula: dict[str, list] = defaultdict(list)
        for e in entries:
            by_formula[e.get("formula", "UNKNOWN")].append(e)
        print(f"Total entries: {stats.get('entries', 0)}")
        print(f"Unique formulas: {len(by_formula)}")
        for formula in sorted(by_formula):
            group = by_formula[formula]
            has_energy = any(e.get("final_energy") for e in group)
            ok = "OK" if has_energy else "NO_ENERGY"
            print(f"  {formula:12s}  {ok}  ({len(group)} entries)")
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

    # history
    p_history = sub.add_parser("history", help="Show phase transition timeline")
    p_history.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )
    p_history.add_argument("--system", "-s", type=str, default=None,
                           help="System name (omit for all)")

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
    rp.add_argument(
        "--dry-run", action="store_true",
        help="Build defect structures and generate inputs only; do NOT submit any VASP jobs.",
    )
    rp.add_argument(
        "--exclude", action="append", default=[],
        help="Exclude a system by directory name (repeatable: --exclude hBN --exclude orth-SiC)",
    )
    rp.add_argument("--loop", action="store_true",
                    help="Keep polling and advancing until all systems complete")
    rp.add_argument("--daemon", action="store_true",
                    help="Detach and run in background (requires --loop)")

    # start
    sp_start = sub.add_parser("start", help="Start background batch loop")
    sp_start.add_argument("root", type=Path, help="Project root directory")

    # stop
    sp_stop = sub.add_parser("stop", help="Stop background batch loop")
    sp_stop.add_argument("root", type=Path, help="Project root directory")

    pp = sub.add_parser("progress", help="Show per-system completion percentage")
    pp.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )



def _handle_batch(args: argparse.Namespace) -> None:
    if args.batch_action == "status":
        _batch_status(args.root.resolve())
    elif args.batch_action == "submit":
        _batch_submit(args.root.resolve(), all_phases=args.all_phases)
    elif args.batch_action == "start":
        _batch_start(args.root.resolve())
    elif args.batch_action == "stop":
        _batch_stop(args.root.resolve())
    elif args.batch_action == "history":
        _batch_history(args.root.resolve(), system=args.system)
    elif args.batch_action == "generate-inputs":
        _batch_generate_inputs(args.root.resolve(), unitcell=args.unitcell)
    elif args.batch_action == "run":
        _batch_run(args.root.resolve(), poll_interval=args.poll, dry_run=args.dry_run,
                   exclude=args.exclude, loop=args.loop)
    elif args.batch_action == "progress":
        _batch_progress(args.root.resolve())




def _batch_start(root: Path) -> None:
    if daemonize(root):
        try:
            _batch_run(root, loop=True)
        finally:
            cleanup(root)


def _batch_stop(root: Path) -> None:
    _lifecycle_stop(root.resolve())


def _batch_loop_status(root: Path) -> None:
    """Print loop PID, uptime, and the latest phase snapshot."""
    import json
    import time

    from vasp_sop.core.batch_lifecycle import _is_alive, _pid_file

    pid_path = _pid_file(root)
    if not pid_path.is_file():
        print("Loop stopped (no PID file)")
        return
    try:
        lines = pid_path.read_text().splitlines()
        pid = int(lines[0])
    except (OSError, ValueError, IndexError):
        pid_path.unlink(missing_ok=True)
        print("Loop stopped (corrupt PID file cleaned)")
        return
    if not _is_alive(pid):
        pid_path.unlink(missing_ok=True)
        print("Loop stopped (stale PID file cleaned)")
        return

    try:
        started_at = float(lines[2])
    except (IndexError, ValueError):
        started_at = pid_path.stat().st_mtime
    elapsed = max(0, int(time.time() - started_at))
    minutes, seconds = divmod(elapsed, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        uptime = f"{days}d{hours}h{minutes:02d}m"
    elif hours:
        uptime = f"{hours}h{minutes:02d}m"
    else:
        uptime = f"{minutes}m{seconds:02d}s"

    snapshot = root / "batch_snapshot.json"
    summary: list[str] = []
    snapshot_time = ""
    if snapshot.is_file():
        try:
            state = json.loads(snapshot.read_text())
            summary = [
                f"{phase}={count}"
                for phase, count in sorted(state.get("phases", {}).items())
            ]
            snapshot_time = str(state.get("timestamp", ""))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    details = "  ".join(summary)
    if snapshot_time:
        details = (
            f"{details}  snapshot={snapshot_time}"
            if details else f"snapshot={snapshot_time}"
        )
    suffix = f"  {details}" if details else ""
    print(f"Loop running (PID {pid})  uptime={uptime}{suffix}")


def _batch_progress(root: Path) -> None:
    """Print per-system completion percentage (completed / total pipeline dirs)."""
    import subprocess, json

    from vasp_sop.core.cache import cache_lookup
    def is_done(p: Path) -> bool:
        return cache_lookup(p) is not None

    rows: list[tuple[int, str, int, int, int, int, int, int]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        cpd_dirs = [p for p in (d / "cpd").iterdir() if p.is_dir()] if (d / "cpd").is_dir() else []
        uc_dirs = [p for p in (d / "unitcell").iterdir() if p.is_dir() and p.name != "structure_opt"] if (d / "unitcell").is_dir() else []
        df_dirs = [p for p in (d / "defect").iterdir() if p.is_dir()] if (d / "defect").is_dir() else []

        cpd_ok = sum(1 for p in cpd_dirs if is_done(p))
        uc_ok = sum(1 for p in uc_dirs if is_done(p))
        df_ok = sum(1 for p in df_dirs if is_done(p))

        nc, nu, nd = len(cpd_dirs), len(uc_dirs), len(df_dirs)
        total = nc + nu + nd
        done = cpd_ok + uc_ok + df_ok

        pct = 100 if (d / "defect" / "defect_energy_summary.json").exists() else (
            int(done / total * 100) if total else 0
        )
        if nc + nu + nd > 0 or pct == 100:
            rows.append((pct, d.name, nc, nu, nd, cpd_ok, uc_ok, df_ok))
    for pct, name, nc, nu, nd, cpd_ok, uc_ok, df_ok in rows:
        print(f"{name:22s}  {pct:3d}%  {cpd_ok:>2d}/{nc:<3d}  {uc_ok:>2d}/{nu:<3d}  {df_ok:>3d}/{nd:<3d}")


def _batch_status(root: Path) -> None:
    """Scan *root* for vasp-sop systems and print status table."""
    _batch_loop_status(root)
    from vasp_sop.core.job_store import JobStore
    from vasp_sop.core.config import PipelineConfig

    store = JobStore()
    all_jobs = store.latest_all()
    store.close()

    rows: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan = d / "plan.yaml"
        if not plan.is_file():
            continue
        try:
            config = PipelineConfig.from_yaml(plan, root=d)
            src = config.poscar_src
            mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
            s = {"name": d.name, "root": d, "config": config,
                 "formula": config.formula, "mpid": mpid}
        except Exception:
            continue

        phase = _phase(s)
        prefix = str(d.resolve())
        cpd_prefix = prefix + "/cpd/"
        uc_prefix = prefix + "/unitcell/"
        df_prefix = prefix + "/defect/"
        cpd_r = sum(1 for p, st in all_jobs.items()
                    if p.startswith(cpd_prefix) and st == "submitted")
        cpd_d = sum(1 for p, st in all_jobs.items()
                    if p.startswith(cpd_prefix) and st == "converged")
        uc_r = sum(1 for p, st in all_jobs.items()
                   if p.startswith(uc_prefix) and st == "submitted")
        uc_d = sum(1 for p, st in all_jobs.items()
                   if p.startswith(uc_prefix) and st == "converged")
        df_r = sum(1 for p, st in all_jobs.items()
                   if p.startswith(df_prefix) and st == "submitted")
        df_d = sum(1 for p, st in all_jobs.items()
                   if p.startswith(df_prefix) and st == "converged")

        pri = _PRIORITY_MAP.get(d.name, "\u2014")
        rows.append({"name": d.name, "pri": pri, "phase": phase,
                     "cpd_r": cpd_r, "cpd_d": cpd_d,
                     "uc_r": uc_r, "uc_d": uc_d,
                     "df_r": df_r, "df_d": df_d})

    if not rows:
        print(f"No vasp-sop systems found in {root}")
        return

    print(f"{'System':<22} {'P':<3} {'Phase':<10} {'CPD':>8} {'UC':>8} {'Defect':>9}")
    print(f"{'':22s} {'':3s} {'':10s} {'D/T':>8} {'D/T':>8} {'D/T':>9}")
    print("-" * 62)
    for r in rows:
        cpd_total = r["cpd_d"] + r["cpd_r"]
        cpd_s = f"{r['cpd_d']}/{cpd_total}" if cpd_total else "·"
        uc_total = r["uc_d"] + r["uc_r"]
        uc_s = f"{r['uc_d']}/{uc_total}" if uc_total else "·"
        df_total = r["df_d"] + r["df_r"]
        df_s = f"{r['df_d']}/{df_total}" if df_total else "·"
        print(f"{r['name']:<22} {r['pri']:<3} {r['phase']:<10} "
              f"{cpd_s:>8} {uc_s:>8} {df_s:>9}")
    print("-" * 62)
    done_count = sum(1 for r in rows if r["phase"] == "COMPLETE")
    print(f"Total: {len(rows)}  Done: {done_count}  "
          f"Remaining: {len(rows) - done_count}")


def _batch_history(root: Path, *, system: str | None = None) -> None:
    """Print job state history for one or all systems from JobStore."""
    from vasp_sop.core.job_store import JobStore
    from datetime import datetime

    store = JobStore()
    all_jobs = store.latest_all()
    store.close()

    root_prefix = str(root.resolve())

    if system:
        prefix = f"{root_prefix}/{system}"
        sys_jobs = {p: s for p, s in all_jobs.items()
                    if p.startswith(prefix)}
        if not sys_jobs:
            print(f"No job records for system '{system}'.")
            return
        print(f"Job states for {system}:")
        for path, state in sorted(sys_jobs.items()):
            rel = path.removeprefix(f"{prefix}/")
            print(f"  {rel:<30s}  {state}")
    else:
        # Group by system directory name
        systems: dict[str, list[str]] = {}
        for path, state in all_jobs.items():
            if path.startswith(root_prefix):
                parts = path[len(root_prefix):].lstrip("/").split("/", 1)
                sys_name = parts[0]
                systems.setdefault(sys_name, []).append(state)
        if not systems:
            print("No job records found.")
            return
        print(f"{'System':<22}  {'Run':>3}  {'Done':>4}  {'Total':>5}")
        print("-" * 40)
        for name in sorted(systems):
            states = systems[name]
            running = sum(1 for s in states if s == "submitted")
            done = sum(1 for s in states if s == "converged")
            print(f"  {name:<22}  {running:>3}  {done:>4}  {len(states):>5}")
    store.close()

# ── Pipeline phase constants (module-level for shared access) ──────
_CPD = "cpd"
_UC = "unitcell"
_DF = "defect"


def _target_dir(s: dict) -> Path | None:
    """Return the target phase directory, or None if no mpid."""
    if not s.get("mpid"):
        return None
    cpd_dir = s["root"] / _CPD
    import re as _re
    pattern = _re.compile(_re.escape(s["mpid"]) + r"\Z")
    if not cpd_dir.is_dir():
        return None
    for pd in cpd_dir.iterdir():
        if pd.is_dir() and pattern.search(pd.name):
            return pd
    return None


def _competing_dirs(s: dict) -> list[Path]:
    """Return competing phases that need VASP submission or retry."""
    from vasp_sop.vasp.io import check_converged, input_ready
    from vasp_sop.core.jobs import crisp_terminal_status
    from vasp_sop.core.job_store import JobStore
    import logging as _log

    _logr = _log.getLogger(__name__)
    td = _target_dir(s)
    cpd_dir = s["root"] / _CPD
    store = JobStore()
    result: list[Path] = []
    for pd in cpd_dir.iterdir():
        if not pd.is_dir() or pd.name == (td.name if td else ""):
            continue
        if pd.name == "combos":
            continue
        current = store.latest(str(pd.resolve()))
        if current == "submitted":
            continue
        marker = crisp_terminal_status(pd)
        if marker == "failed":
            if input_ready(pd) and current != "submitted":
                result.append(pd)
            continue
        if marker == "completed":
            continue
        if not input_ready(pd):
            if (pd / "POSCAR").is_file():
                _logr.warning("Competing phase %s has POSCAR but no VASP inputs "
                              "(INCAR/POTCAR missing)", pd.name)
            continue
        if check_converged(pd):
            continue
        state = store.latest(str(pd.resolve()))
        if state not in ("converged", "submitted"):
            result.append(pd)
    return sorted(result)


def _competing_blockers(s: dict) -> list[Path]:
    """Return lifecycle states that block entering CPD post-processing."""
    from vasp_sop.vasp.io import input_ready
    from vasp_sop.core.jobs import crisp_terminal_status
    from vasp_sop.core.job_store import JobStore

    td = _target_dir(s)
    cpd_dir = s["root"] / _CPD
    target_name = td.name if td else ""
    store = JobStore()
    blockers: list[Path] = []
    for pd in cpd_dir.iterdir():
        if not pd.is_dir() or pd.name in (target_name, "combos"):
            continue
        marker = crisp_terminal_status(pd)
        state = store.latest(str(pd.resolve()))
        if marker == "failed" or state in ("failed", "unconverged"):
            blockers.append(pd)
            continue
        if state not in ("converged", "submitted") and (pd / "POSCAR").is_file():
            if not input_ready(pd):
                blockers.append(pd)
    return sorted(blockers)






def _phase(s: dict) -> str:
    """Determine the current pipeline phase for a system dict.

    Uses phase-persistence gates: once CHEM_POT_DIAGRAM has written
    ``target_vertices.yaml``, the system never regresses to
    COMPETING — even when competing-phase submissions reappear.
    """
    from vasp_sop.vasp.io import input_ready
    from vasp_sop.core.job_store import JobStore
    _js = JobStore()
    td = _target_dir(s)
    if td is None:
        return "NO_TARGET"

    cpd_root = s["root"] / _CPD
    target_vertices = cpd_root / "target_vertices.yaml"

    # ── Phase-persistence gate ────────────────────────────────────
    # Once CPD_POST has written target_vertices.yaml the system is
    # irrevocably past COMPETING.  Downstream UC/DF can still cycle
    # but we will never return COMPETING again for this system.
    if target_vertices.is_file():
        uc_root = s["root"] / _UC
        uc_tasks = ["band", "dos", "dielectric"]
        uc_has_inputs = any((uc_root / t / "INCAR").is_file() for t in uc_tasks)
        if not uc_has_inputs:
            return "UNITCELL_DEFECT"


        # Unitcell yaml must exist (generated by post-processing)
        if not (uc_root / "unitcell.yaml").is_file():
            return "UNITCELL_DEFECT"

        # CPD intermediate files must exist (generated during CPD_POST)
        if not (cpd_root / "composition_energies.yaml").is_file():
            return "UNITCELL_DEFECT"
        if not (cpd_root / "standard_energies.yaml").is_file():
            return "UNITCELL_DEFECT"
        if not (cpd_root / "chem_pot_diag.json").is_file():
            return "UNITCELL_DEFECT"

        df_root = s["root"] / _DF
        if not df_root.is_dir():
            return "UNITCELL_DEFECT"
        # Every real defect calc dir must have analysis intermediates.
        # Non-calc junk dirs (no VASP inputs / OUTCAR) and JobStore-failed
        # defects do not block COMPLETE.
        defect_dirs = [c for c in df_root.iterdir() if c.is_dir()]
        for d in defect_dirs:
            if d.name == "perfect":
                continue  # perfect has its own checks below
            # Skip non-calculation subdirs (e.g. symmetry label folders).
            if not input_ready(d) and not (d / "OUTCAR").is_file() \
                    and not (d / "output" / "OUTCAR").is_file():
                continue
            # failed defect — skip, don't block
            latest_st = _js.latest(str(d.resolve()))
            if latest_st in ("failed", "unconverged"):
                continue
            if not (d / "calc_results.json").is_file():
                return "UNITCELL_DEFECT"
            if not (d / "correction.json").is_file():
                return "UNITCELL_DEFECT"
            if not (d / "defect_structure_info.json").is_file():
                return "UNITCELL_DEFECT"
        perfect = df_root / "perfect"
        if perfect.is_dir() and not (perfect / "perfect_band_edge_state.json").is_file():
            return "UNITCELL_DEFECT"

        return "COMPLETE"

    # ── Normal upstream progression (CPD not yet complete) ────────
    if _js.latest(str(td.resolve())) != "converged":
        return "STRUCTURE_OPT"
    if _competing_dirs(s) or _competing_blockers(s):
        return "COMPETING"
    return "CHEM_POT_DIAGRAM"




def _unitcell_build_failure(root: Path) -> dict[str, str] | None:
    """Read a terminal unitcell build failure without introducing a phase."""
    import json

    status_path = Path(root) / _UC / "unitcell_build_status.json"
    if not status_path.is_file():
        return None
    try:
        status = json.loads(status_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict) or status.get("status") != "failed":
        return None
    return {
        "reason": str(status.get("reason", "unitcell_build_failed")),
        "diagnostic": str(status.get("diagnostic", "no diagnostic recorded")),
    }


def _crisp_active_dirs(*, skip: bool = False) -> set[str]:
    """Query ``crisp`` for currently-running jobs and return their work dirs.

    When *skip* is True (e.g. dry-run), short-circuit and return an empty set
    without spawning the subprocess. This avoids a 30 s wait on `crisp jobs`
    when no submission is actually happening.
    """
    if skip:
        return set()
    import subprocess as _sp, json as _json
    try:
        r = _sp.run(["crisp", "jobs"], capture_output=True, text=True, timeout=30)
        raw = _json.loads(r.stdout)
        jobs = raw.get("jobs", raw.get("data", {}).get("jobs", []))
    except Exception:
        return set()
    alive = {"submit", "submitted", "running", "ready_fetch"}
    return {j.get("local_dir", "") for j in jobs
            if j.get("status") in alive and j.get("local_dir")}


def _advance_one_system(s: dict, *, dry_run: bool = False, log_to_logger: bool = False) -> None:
    """Advance one system by one cycle (runs serially in batch mode).

    Thin dispatcher — creates a :class:`~vasp_sop.core.system.System`,
    determines the current phase, and delegates to the appropriate wave
    function(s) in :mod:`vasp_sop.core.orchestrator` (issue #95).
    """
    from vasp_sop.core.system import System
    from vasp_sop.core.job_store import JobStore
    from vasp_sop.core.orchestrator import (
        wave1_optimize,
        wave2_submit,
        wave3_postprocess,
        _unitcell_build_failure as _uc_build_failure,
    )

    _logger = logging.getLogger(__name__)

    # Build System model from the legacy dict
    sys_obj = System(s["root"], s["config"])
    js = JobStore()

    p = _phase(s)

    # ── Failure gate ─────────────────────────────────────────────────
    if p == "UNITCELL_DEFECT":
        failure = _uc_build_failure(s["root"])
        if failure:
            raise RuntimeError(
                f"unitcell blocked for {s['name']}: {failure['reason']}; "
                f"{failure['diagnostic']}"
            )
    if p == "COMPLETE" or p == "NO_TARGET":
        return

    # ── Wave 1: STRUCTURE_OPT ────────────────────────────────────────
    if p == "STRUCTURE_OPT":
        wave1_optimize(sys_obj, js, dry_run, log_to_logger=log_to_logger)
        # Re-evaluate phase — target may now be recorded as done
        p = _phase(s)

    # ── Wave 2: COMPETING (early return) ─────────────────────────────
    if p == "COMPETING":
        wave2_submit(sys_obj, js, dry_run, log_to_logger=log_to_logger)
        return

    # ── Wave 3: CHEM_POT_DIAGRAM ─────────────────────────────────────
    if p == "CHEM_POT_DIAGRAM":
        wave3_postprocess(sys_obj, js, dry_run, log_to_logger=log_to_logger)

    # ── Wave 2 + 3: UNITCELL_DEFECT ─────────────────────────────────
    if p == "UNITCELL_DEFECT":
        try:
            # In dry-run, print the artifact preview first (matches
            # original behaviour where the preview preceded submission).
            if dry_run:
                wave3_postprocess(sys_obj, js, dry_run, log_to_logger=log_to_logger)
            wave2_submit(sys_obj, js, dry_run, log_to_logger=log_to_logger)
            if not dry_run:
                wave3_postprocess(sys_obj, js, dry_run, log_to_logger=log_to_logger)
        except Exception as exc:
            _logger.error("%s UNITCELL_DEFECT failed: %s", s["name"], exc)
            if _uc_build_failure(sys_obj.root):
                raise
            if not log_to_logger:
                print(f"  ✗ {s['name']:<18} UNITCELL_DEFECT FAILED")

_MAX_RESTART = 5




def _handle_unconverged_poll(wd: Path, *, js: Any = None) -> None:
    """VASP normal exit but unconverged — CONTCAR restart or give up."""
    import logging

    _log = logging.getLogger(__name__)
    from vasp_sop.vasp.io import restart_from_contcar, parse_max_force
    from vasp_sop.core.jobs import submit_vasp
    from vasp_sop.core.job_store import JobStore

    wd_str = str(wd.resolve())
    owned = js is None
    store = js if not owned else JobStore()
    try:
        history = store.history(wd_str)
        attempt = history[-1].get("attempt", 0) if history else 0

        cur_f = parse_max_force(wd)

        if cur_f > 0 and attempt > 0:
            for h in reversed(history):
                reason = h.get("reason", "")
                if reason.startswith("restart,"):
                    for part in reason.split(","):
                        if part.startswith("prev_f="):
                            prev_f = float(part.split("=")[1])
                            if cur_f >= prev_f * 0.99:
                                store.record(wd_str, "failed",
                                              reason=f"stalled,max_f={cur_f:.4f}",
                                              attempt=attempt)
                                store.untrack(wd_str)
                                _log.warning("! %s stalled (max_f %.4f→%.4f), giving up", wd.name, prev_f, cur_f)
                                return
                            break
                    break

        if attempt >= _MAX_RESTART:
            store.record(wd_str, "failed",
                          reason=f"unconverged,max_f={cur_f:.4f}",
                          attempt=attempt)
            store.untrack(wd_str)
            _log.error("! %s unconverged after %d restart(s), giving up", wd.name, attempt)
            return

        restart_from_contcar(wd)

        job = submit_vasp(wd.resolve())
        store.record(wd_str, "submitted",
                      source=job.task_name, attempt=attempt + 1,
                      reason=f"restart,prev_f={cur_f:.4f}")
        _log.info("→ %s restart #%d (max_f %.4f, %s)", wd.name, attempt + 1, cur_f, job.task_name)
    finally:
        if owned:
            store.close()

def _batch_run(root: Path, *, poll_interval: int = 60, dry_run: bool = False,
               exclude: list[str] | None = None, loop: bool = False) -> None:
    """Batch pipeline — advance all systems by one cycle (single pass or loop).

    One-shot: advances each system once, submits ready VASP jobs, then exits.
    Use ``--loop`` on the CLI for continuous poll-and-advance mode.
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
            "name": d.name,
            "root": d,
            "config": config,
            "formula": config.formula,
            "mpid": mpid,
        })

    # ── Apply --exclude filter ───────────────────────────────────
    if exclude:
        excluded_names = [s["name"] for s in sys_list if s["name"] in exclude]
        if excluded_names:
            logger.info("Excluded %d system(s): %s", len(excluded_names), excluded_names)
        sys_list = [s for s in sys_list if s["name"] not in exclude]

    if not sys_list:
        logger.warning("No systems found.")
        return

    if loop:
        from vasp_sop.core.logging import setup_file_logging
        from vasp_sop.core.snapshot import SnapshotWriter

        setup_file_logging(root)
        sw = SnapshotWriter(root)

    def _print_info(message: str) -> None:
        if loop:
            logger.info("%s", message)
        else:
            print(message)

    _print_info(f"Batch run: {len(sys_list)} systems\n")


    # ── Single JobStore for the entire batch run ─────────────────────
    from vasp_sop.core.job_store import JobStore
    js = JobStore()

    # ── Populate submission DB from crisp + filesystem ────────────────
    if not dry_run:
        _crisp_active = _crisp_active_dirs(skip=False)
        if _crisp_active:
            logger.info("Found %d active crisp tasks, recording in JobStore.",
                        len(_crisp_active))
            for p in _crisp_active:
                js.track(p)
                js.record(p, "submitted", source="restored")

    from vasp_sop.core.cache import cache_lookup, vasp_results_put as _cache_put

    import threading, queue
    _cache_queue: queue.Queue[Path | None] = queue.Queue()
    _cache_worker: threading.Thread | None = None
    _cache_seen: set[Path] = set()

    def _start_cache_worker() -> None:
        nonlocal _cache_worker
        if _cache_worker is not None:
            return
        def _run():
            while True:
                wd = _cache_queue.get()
                if wd is None:
                    break
                try:
                    _cache_put(wd)
                except Exception as exc:
                    logger.warning("Failed to cache %s: %s", wd.name, exc)
                finally:
                    _cache_queue.task_done()
        _cache_worker = threading.Thread(target=_run, daemon=True)
        _cache_worker.start()

    def _defer_cache_put(wd: Path) -> None:
        if wd in _cache_seen:
            return
        _cache_seen.add(wd)
        _start_cache_worker()
        _cache_queue.put(wd)

    def _flush_deferred_cache() -> None:
        pass

    def _join_cache_workers() -> None:
        nonlocal _cache_worker
        if _cache_worker is None:
            return
        _cache_queue.join()
        _cache_queue.put(None)
        _cache_worker.join()
        _cache_worker = None
    # ── Submit helper ──────────────────────────────────────────────
    def _submit_or_skip(path: Path, label: str, sys_name: str) -> object:
        if dry_run:
            _print_info(f"  [dry-run] {sys_name:<18} would submit: {label}")
            return None
        try:
            from vasp_sop.core.cache import lattice_too_large
            if lattice_too_large(path):
                msg = f"  ✗ {sys_name:<18} {label}: lattice too large, skipped"
                if loop:
                    logger.error(msg)
                else:
                    print(msg)
                return None
            job = submit_vasp(path.resolve())
            js.track(str(path.resolve()))
            js.record(str(path.resolve()), "submitted", source=job.task_name)
            msg = f"  → {sys_name:<18} {label}: {job.task_name}"
            if loop:
                logger.info(msg)
            else:
                print(msg)
            return job
        except RuntimeError as exc:
            msg = f"  ✗ {sys_name:<18} {label}: {exc}"
            if loop:
                logger.error(msg)
            else:
                print(msg)
            return None
        except Exception as exc:
            msg = f"  ✗ {sys_name:<18} {label}: {exc}"
            if loop:
                logger.warning(msg)
            else:
                print(msg)
            return None


    if dry_run:
        _print_info("Dry-run mode: will build defect structures and generate inputs, NO VASP submission.\n")


    # ── Loop context ────────────────────────────────────────────
    first_pass = True
    blocked_systems: set[str] = set()

    try:
        while not is_stop_requested():
            if not dry_run:
                # ── Backfill cache ──────────────────────────────────
                from vasp_sop.vasp.io import check_converged as _cc, _tail_text
                from vasp_sop.core.jobs import move_crisp_outputs
                import time as _time

                backfilled = 0
                for s in sys_list:
                    cpd_root = s["root"] / _CPD
                    if not cpd_root.is_dir():
                        continue
                    for pd in cpd_root.iterdir():
                        if not pd.is_dir() or "_mp-" not in pd.name:
                            continue
                        if js.latest(str(pd.resolve())) == "converged":
                            continue
                        if not _cc(pd):
                            continue
                        move_crisp_outputs(pd)
                        formula, mpid = pd.name.split("_mp-", 1)
                        key = _cache_put(pd, formula=formula, task_name=f"{formula}_mp-{mpid}")
                        if key is None:
                            logger.warning(
                                "%s: cache put returned None (missing output files?)",
                                pd.name,
                            )
                        backfilled += 1
                        js.record(str(pd.resolve()), "converged", source="backfill")
                if backfilled:
                    logger.info("Backfilled %d already-converged phase results.", backfilled)

                # ── Sweep orphan ───────────────────────────────────
                orphaned = 0
                for s in sys_list:
                    for root_dir in (s["root"] / _UC, s["root"] / _DF):
                        if not root_dir.is_dir():
                            continue
                        for child in root_dir.iterdir():
                            if not child.is_dir():
                                continue
                            out_dir = child / "output"
                            if not out_dir.is_dir():
                                continue
                            if not (out_dir / "OUTCAR").is_file():
                                continue
                            move_crisp_outputs(child)
                            if _cc(child) and cache_lookup(child) is None:
                                _defer_cache_put(child)
                            orphaned += 1
                if orphaned:
                    logger.info("Processed %d orphaned crisp outputs.", orphaned)

                # ── Poll tracked dirs ───────────────────────────────
                completed = 0
                crispy = _crisp_active_dirs(skip=False)
                for row in js.tracked_dirs():
                    wd = Path(row["dir_path"])
                    wd_str = str(wd.resolve())
                    if wd_str in crispy:
                        continue
                    if _cc(wd):
                        move_crisp_outputs(wd)
                        _defer_cache_put(wd)
                        js.record(wd_str, "converged")
                        js.untrack(wd_str)
                        completed += 1
                        continue
                    outcar = wd / "OUTCAR"
                    if not outcar.is_file():
                        outcar = wd / "output" / "OUTCAR"
                    if not outcar.is_file():
                        if _time.time() - row["submitted_at"] > 7 * 86400:
                            js.record(wd_str, "failed", reason="orphaned")
                            js.untrack(wd_str)
                        continue
                    tail = _tail_text(outcar, 4096)
                    if not tail or "General timing and accounting" not in tail:
                        js.record(wd_str, "failed", reason="vasp_crash")
                        js.untrack(wd_str)
                        continue
                    _handle_unconverged_poll(wd, js=js)
                if completed:
                    _print_info(f"  Cached {completed} completed calculation(s).")

            n_skipped = 0
            errors: list[tuple[str, str]] = []  # (name, reason)
            for idx, s in enumerate(sys_list, 1):
                name = s["name"]
                if name in blocked_systems:
                    n_skipped += 1
                    continue
                p = _phase(s)
                failure = _unitcell_build_failure(s["root"])
                if p == "UNITCELL_DEFECT" and failure:
                    blocked_systems.add(name)
                    reason = failure["reason"]
                    diagnostic = failure["diagnostic"]
                    message = (
                        f"{name} blocked: unitcell {reason}; {diagnostic}"
                    )
                    if loop:
                        logger.error(message)
                    else:
                        print(f"  ✗ {message}")
                    errors.append((name, reason))
                    continue
                if p in ("COMPLETE", "NO_TARGET"):
                    n_skipped += 1
                    continue

                if loop:
                    try:
                        _advance_one_system(s, dry_run=dry_run, log_to_logger=True)
                        logger.info("  [%d/%d] %-18s %s ... done", idx, len(sys_list), name, p)
                    except Exception as exc:
                        failure = _unitcell_build_failure(s["root"])
                        if failure:
                            blocked_systems.add(name)
                            reason = failure["reason"]
                        else:
                            reason = str(exc).split("(")[0].strip() or type(exc).__name__
                        logger.error("%s advance failed: %s", name, exc)
                        errors.append((name, reason))
                else:
                    print(f"  [{idx}/{len(sys_list)}] {name:<18} {p} ...", end="", flush=True)
                    try:
                        _advance_one_system(s, dry_run=dry_run)
                        print(" done")
                    except Exception as exc:
                        reason = str(exc).split("(")[0].strip() or type(exc).__name__
                        logger.error("%s advance failed: %s", name, exc)
                        print(f" FAILED ({reason})")
                        errors.append((name, reason))

            if n_skipped:
                _print_info(f"  [{n_skipped}/{len(sys_list)} systems already done, skipped]\n")

            # ── Status ──────────────────────────────────────────
            phases = [_phase(s) for s in sys_list]
            done_count = sum(1 for p in phases if p in ("COMPLETE", "NO_TARGET"))
            counts = {p: phases.count(p) for p in sorted(set(phases))}
            parts = [f"{p}={n}" for p, n in sorted(counts.items())]
            _print_info(f"{'  '.join(parts)}")

            if errors:
                if loop:
                    logger.warning("%d system(s) with errors:", len(errors))
                    for name, reason in errors:
                        logger.warning("  %-18s  %s", name, reason)
                else:
                    print(f"\n  ⚠ {len(errors)} system(s) with errors:")
                    for name, reason in errors:
                        print(f"    {name:<18}  {reason}")
            if loop:
                from vasp_sop.defect.analysis import classify_analyze_status
                import json
                import subprocess

                analyze_counts = {"full": 0, "partial": 0, "failed": 0}
                for s in sys_list:
                    defect_root = s["root"] / _DF
                    if defect_root.is_dir():
                        try:
                            analyze_counts[classify_analyze_status(defect_root)] += 1
                        except Exception:
                            pass

                crisp_active = crisp_running = crisp_failed = -1
                try:
                    result = subprocess.run(
                        ["crisp", "jobs", "-a"], capture_output=True, text=True, timeout=30,
                    )
                    jobs = json.loads(result.stdout).get("jobs") or []
                    project_jobs = [
                        job for job in jobs
                        if (job.get("local_dir") or "").startswith(str(root))
                    ]
                    crisp_active = sum(
                        1 for job in project_jobs
                        if job.get("status") in (
                            "submit", "submitted", "running", "ready_fetch", "pending",
                        )
                    )
                    crisp_running = sum(
                        1 for job in project_jobs if job.get("status") == "running"
                    )
                    crisp_failed = sum(
                        1 for job in project_jobs if job.get("status") == "failed"
                    )
                except Exception:
                    pass

                sw.write({
                    "phases": dict(counts),
                    "analyze": analyze_counts,
                    "crisp_active": crisp_active,
                    "crisp_running": crisp_running,
                    "crisp_failed": crisp_failed,
                    "errors": [
                        {"system": name, "reason": reason} for name, reason in errors
                    ],
                })

            terminal_count = done_count + len(blocked_systems)
            if done_count == len(sys_list):
                _print_info("\nAll systems complete.")
                break
            if loop and terminal_count == len(sys_list):
                _print_info("\nAll systems complete or blocked.")
                break

            if not loop:
                still = len(sys_list) - done_count
                blocked = len(errors)
                running = still - blocked
                print(f"\n{running} running, {blocked} blocked, {still} remaining — re-run `vasp-sop batch run .` after VASP jobs complete.")
                break

            _print_info(f"\n  Sleeping {poll_interval}s … (Ctrl+C to interrupt)")
            _flush_deferred_cache()
            _time.sleep(poll_interval)
            first_pass = False
    except KeyboardInterrupt:
        _print_info("\nInterrupted.")
    finally:
        _flush_deferred_cache()
        _join_cache_workers()
        js.close()


def _batch_generate_inputs(root: Path, *, unitcell: bool = False) -> None:
    """Generate VASP inputs for all systems in *root* that need them.

    With ``unitcell=True``, also generates band/dos/dielectric inputs
    for systems whose structure_opt has a CONTCAR from handoff.
    """
    from vasp_sop.vasp.io import input_ready, prepare_inputs
    from vasp_sop.core.config import PipelineConfig
    import logging
    log = logging.getLogger(__name__)

    _CPD_DIR = "cpd"
    _UC_DIR = "unitcell"

    # ── CPD phase dirs ─────────────────────────────────────────────
    tasks: list[tuple[str, str, Path, Path]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir(): continue
        plan_path = d / "plan.yaml"
        if not plan_path.is_file(): continue
        cpd_dir = d / _CPD_DIR
        if not cpd_dir.is_dir(): continue
        for pd in sorted(cpd_dir.iterdir()):
            if not pd.is_dir() or pd.name == "combos": continue
            if not input_ready(pd):
                tasks.append((d.name, pd.name, pd, plan_path))

    if tasks:
        print(f"Generating inputs for {len(tasks)} CPD directories ...")
        ok = 0
        for t in tasks:
            try:
                sys_name, phase_name, phase_dir, plan_path = t
                config = PipelineConfig.from_yaml(plan_path, root=phase_dir.parent.parent)
                prepare_inputs(phase_dir, config)
                ok += 1
                print(f"  OK  {sys_name}/{phase_name}")
            except Exception as exc:
                print(f"  FAIL {t[0]}/{t[1]}: {exc}")
        print(f"Done CPD: {ok} generated, {len(tasks)-ok} failed")

    # ── Unitcell tasks (post-handoff only) ─────────────────────────
    if unitcell:
        uc_ok = 0
        uc_skip = 0
        for d in sorted(root.iterdir()):
            if not d.is_dir(): continue
            plan_path = d / "plan.yaml"
            if not plan_path.is_file(): continue
            so = d / _UC_DIR / "structure_opt"
            if not so.is_dir() or not (so / "CONTCAR").is_file():
                uc_skip += 1
                continue
            # Identify target via _target_dir (uses plan.yaml poscar_src)
            config = PipelineConfig.from_yaml(plan_path, root=d)
            from vasp_sop.defect.unitcell import _prepare_all_inputs
            mpid = config.poscar_src.split("mp-", 1)[1] if config.poscar_src.startswith("MP mp-") else None
            td = _target_dir({"root": d, "mpid": mpid, "formula": config.formula})
            if td is None or not td.is_dir():
                uc_skip += 1
                continue
            try:
                _prepare_all_inputs(so.parent, td, config)
                uc_ok += 1
                print(f"  OK  {d.name}/band,dos,dielectric")
            except Exception as exc:
                print(f"  FAIL {d.name}/unitcell: {exc}")
        if uc_skip:
            print(f"Skipped {uc_skip} systems (structure_opt not ready — run CPD first)")
        if uc_ok:
            print(f"Done unitcell: {uc_ok} generated")


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
            if not pd.is_dir() or pd.name == "combos":
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

