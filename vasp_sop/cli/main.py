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
from vasp_sop.core.system import (
    CHEM_POT_DIAGRAM,
    COMPETING,
    COMPLETE,
    NO_TARGET,
    STRUCTURE_OPT,
    UNITCELL_DEFECT,
    System,
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
            from vasp_sop.core.paths import MP_CACHE
            if MP_CACHE.is_dir():
                for child in sorted(MP_CACHE.iterdir()):
                    if child.is_dir():
                        print(f"  {child.name}")
            else:
                print("MP cache is empty.")
        elif args.cache_action == "clear":
            import shutil
            from vasp_sop.core.paths import MP_CACHE
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
        from vasp_sop.vasp.convergence import convergence_verdict
        wd = args.work_dir.resolve()
        if convergence_verdict(wd).converged:
            print(f"{wd}: converged")
        else:
            print(f"{wd}: NOT converged or not complete")


def _handle_report(args: argparse.Namespace) -> None:
    if args.interactive:
        from vasp_sop.report.interactive import generate_interactive_html
        out = generate_interactive_html(args.system_dir)
        if args.output:
            import shutil
            shutil.copy2(out, args.output)
            out = args.output
        print(f"Interactive report written to {out}")
        return

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
        "--output", type=Path,
        help="Output path (default: system_dir/calculation_report.md or formation_energy_interactive.html)",
    )
    report_parser.add_argument(
        "--interactive", action="store_true",
        help="Generate interactive formation-energy HTML instead of Markdown",
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
    """Print pipeline status for a project using the canonical phase machine."""
    root = args.root.resolve()
    print(f"Project: {root}")

    # Use System.phase() to determine current pipeline stage
    from vasp_sop.core.config import PipelineConfig
    plan = root / "plan.yaml"
    if not plan.is_file():
        print("  No plan.yaml found — system not initialized.")
        return

    config = PipelineConfig.from_yaml(plan, root=root)
    sys_obj = System(root, config)
    p = sys_obj.phase()
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
    """Drive the single-system pipeline end-to-end via the core orchestrator.

    Replaced ``run_point_defect_pipeline`` from ``pipeline.py`` after the
    legacy pipeline was removed (see issue #78), and now the inlined
    poll/cache/advance copies in turn (they were duplicates of the batch
    loop).  A single-system :class:`~vasp_sop.core.orchestrator.BatchOrchestrator`
    runs the same machine, bounded to 200 cycles.
    """
    logger.info(
        "Starting point-defect pipeline for %s (root: %s)",
        config.formula, config.root,
    )

    from vasp_sop.core.orchestrator import BatchOrchestrator

    BatchOrchestrator(config.root, loop=True).run(max_cycles=200)

    p = System(config.root, config).phase()
    if p not in (COMPLETE, NO_TARGET):
        logger.error(
            "Pipeline did not complete after 200 iterations (phase=%s).", p,
        )
        sys.exit(1)
    logger.info("Pipeline complete (phase=%s).", p)



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

    # history
    p_history = sub.add_parser("history", help="Show phase transition timeline")
    p_history.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )
    p_history.add_argument("--system", "-s", type=str, default=None,
                           help="System name (omit for all)")
    p_history.add_argument(
        "--prune", action="store_true",
        help="Delete JobStore records whose directory no longer exists",
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
        _batch_history(args.root.resolve(), system=args.system,
                       prune=args.prune)
    elif args.batch_action == "generate-inputs":
        _batch_generate_inputs(args.root.resolve(), unitcell=args.unitcell)
    elif args.batch_action == "run":
        _batch_run(args.root.resolve(), poll_interval=args.poll, dry_run=args.dry_run,
                   exclude=args.exclude, loop=args.loop)




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


def _batch_status(root: Path) -> None:
    """Scan *root* for vasp-sop systems and print status table.

    D/T columns are **disk truth**: the denominator is every directory on
    disk (cpd/ phases, unitcell tasks except structure_opt, defect dirs)
    and the numerator is how many of them pass the convergence verdict.
    Run counts JobStore ``submitted`` records whose directory still exists
    (jobs believed to be in crisp's queue); records for deleted dirs are
    filtered out on read.
    """
    _batch_loop_status(root)
    from vasp_sop.core.job_store import JobStore
    from vasp_sop.core.config import PipelineConfig
    from vasp_sop.vasp.convergence import convergence_verdict

    store = JobStore()
    all_jobs = store.latest_all()
    store.close()

    def _dirs(base: Path, *, exclude: str | None = None) -> list[Path]:
        if not base.is_dir():
            return []
        return [p for p in base.iterdir() if p.is_dir() and p.name != exclude]

    def _running(prefix: str) -> int:
        # Read-side filter: ignore records whose directory no longer exists.
        return sum(
            1 for p, st in all_jobs.items()
            if st == "submitted" and p.startswith(prefix) and Path(p).is_dir()
        )

    rows: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan = d / "plan.yaml"
        if not plan.is_file():
            continue
        try:
            config = PipelineConfig.from_yaml(plan, root=d)
        except Exception:
            continue

        phase = System(d, config).phase()
        prefix = str(d.resolve())
        cpd_dirs = _dirs(d / "cpd")
        uc_dirs = _dirs(d / "unitcell", exclude="structure_opt")
        df_dirs = _dirs(d / "defect")
        cpd_d = sum(1 for p in cpd_dirs if convergence_verdict(p).converged)
        uc_d = sum(1 for p in uc_dirs if convergence_verdict(p).converged)
        df_d = sum(1 for p in df_dirs if convergence_verdict(p).converged)
        running = (
            _running(prefix + "/cpd/")
            + _running(prefix + "/unitcell/")
            + _running(prefix + "/defect/")
        )
        total = len(cpd_dirs) + len(uc_dirs) + len(df_dirs)
        done = cpd_d + uc_d + df_d
        # Pure disk truth — no summary shortcut: % is the fraction of
        # directories that passed the verdict, matching the COMPLETE gate.
        pct = int(done / total * 100) if total else 0
        rows.append({
            "name": d.name, "pri": _PRIORITY_MAP.get(d.name, "\u2014"),
            "phase": phase, "cpd": (cpd_d, len(cpd_dirs)),
            "uc": (uc_d, len(uc_dirs)), "df": (df_d, len(df_dirs)),
            "running": running, "pct": pct,
        })

    if not rows:
        print(f"No vasp-sop systems found in {root}")
        return

    print(f"{'System':<22} {'P':<3} {'Phase':<10} {'CPD':>8} {'UC':>8} {'Defect':>9} {'Run':>4} {'%':>4}")
    print(f"{'':22s} {'':3s} {'':10s} {'D/T':>8} {'D/T':>8} {'D/T':>9}")
    print("-" * 66)
    for r in rows:
        cpd_s = f"{r['cpd'][0]}/{r['cpd'][1]}" if r["cpd"][1] else "\u00b7"
        uc_s = f"{r['uc'][0]}/{r['uc'][1]}" if r["uc"][1] else "\u00b7"
        df_s = f"{r['df'][0]}/{r['df'][1]}" if r["df"][1] else "\u00b7"
        run_s = str(r["running"]) if r["running"] else "\u00b7"
        pct_s = f"{r['pct']:3d}%"
        print(f"{r['name']:<22} {r['pri']:<3} {r['phase']:<10} "
              f"{cpd_s:>8} {uc_s:>8} {df_s:>9} {run_s:>4} {pct_s:>4}")
    print("-" * 66)
    done_count = sum(1 for r in rows if r["phase"] == "COMPLETE")
    print(f"Total: {len(rows)}  Done: {done_count}  "
          f"Remaining: {len(rows) - done_count}")


def _batch_history(root: Path, *, system: str | None = None,
                   prune: bool = False) -> None:
    """Print job state history for one or all systems from JobStore.

    With *prune*, delete JobStore records (job_history + tracked) whose
    directory no longer exists — stale entries for deleted calculation
    dirs otherwise inflate status accounting.
    """
    from vasp_sop.core.job_store import JobStore
    from datetime import datetime

    store = JobStore()
    if prune:
        n_hist, n_trk = store.prune_missing()
        store.close()
        print(f"Pruned {n_hist} history record(s) and {n_trk} tracked "
              f"row(s) for missing directories.")
        return
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


def _batch_run(root: Path, *, poll_interval: int = 60, dry_run: bool = False,
               exclude: list[str] | None = None, loop: bool = False) -> None:
    """Batch pipeline — advance all systems via the core orchestrator.

    One-shot advances each system once, then exits; ``--loop`` runs the
    continuous poll-and-advance cycle.  All loop machinery (JobStore handle,
    cache worker, restart policy, snapshots) lives in
    :class:`vasp_sop.core.orchestrator.BatchOrchestrator`.
    """
    from vasp_sop.core.orchestrator import BatchOrchestrator

    BatchOrchestrator(
        root,
        dry_run=dry_run,
        exclude=exclude,
        poll_interval=poll_interval,
        loop=loop,
    ).run()


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
            # Identify target via System.target_dir (uses plan.yaml poscar_src)
            config = PipelineConfig.from_yaml(plan_path, root=d)
            from vasp_sop.defect.unitcell import _prepare_all_inputs
            td = System(d, config).target_dir
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
    from vasp_sop.vasp.io import input_ready
    from vasp_sop.vasp.convergence import convergence_verdict
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
        if convergence_verdict(target_dir).converged:
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
            if convergence_verdict(pd).converged:
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
    from vasp_sop.vasp.convergence import convergence_verdict

    if not cpd_dir.is_dir():
        return "·"

    # Post-processing done?
    if (cpd_dir / "target_vertices.yaml").is_file():
        return "✓"

    # Any phase directory has converged OUTCAR?
    for child in cpd_dir.iterdir():
        if not child.is_dir():
            continue
        if convergence_verdict(child).converged:
            return "▶"

    # Any VASP input exists?
    from vasp_sop.vasp.io import input_ready
    for child in cpd_dir.iterdir():
        if child.is_dir() and input_ready(child):
            return "·"  # inputs ready, VASP not run

    return "·"


def _check_unitcell(uc_dir: Path) -> str:
    """Determine unitcell stage status."""
    from vasp_sop.vasp.convergence import convergence_verdict
    from vasp_sop.vasp.io import input_ready

    if not uc_dir.is_dir():
        return "·"

    stages = ["structure_opt", "band", "dos", "dielectric"]
    results = []
    for s in stages:
        sd = uc_dir / s
        if not sd.is_dir():
            results.append("·")
        elif convergence_verdict(sd).converged:
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
    from vasp_sop.vasp.convergence import convergence_verdict
    from vasp_sop.vasp.io import input_ready

    if not df_dir.is_dir():
        return "·"

    # No supercell? barely started
    if not (df_dir / "supercell_info.json").is_file():
        return "·"

    # Defect energy summary exists → fully done
    if (df_dir / "defect_energy_summary.json").is_file():
        return "✓"

    # Check VASP results
    has_perfect = convergence_verdict(df_dir / "perfect").converged if (df_dir / "perfect").is_dir() else False

    # Count defect dirs with VASP done vs total
    defect_dirs = [x for x in df_dir.iterdir()
                   if x.is_dir() and x.name != "perfect" and input_ready(x)]
    done_dirs = sum(1 for x in defect_dirs if convergence_verdict(x).converged)

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

