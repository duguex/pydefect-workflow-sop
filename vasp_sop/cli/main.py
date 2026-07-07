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
    _add_cache_parser(subparsers)
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
        print("defect analyze: standalone post-processing not yet implemented. Use 'batch run' instead.")

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
        if df_root.is_dir() and target_dir and (target_dir / "POSCAR").is_file():
            _build_defects(df_root, target_dir, config)
        if uc_root.is_dir() and target_dir:
            _uc._prepare_all_inputs(uc_root, target_dir, config)

    # ── Polling loop (same semantics as _batch_run) ─────────────────
    import time as _time
    from vasp_sop.vasp.io import check_converged
    from vasp_sop.core.cache import cache_lookup, _get_submitted_dirs, clear_submission
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
        for wd_str in list(_get_submitted_dirs()):
            wd = Path(wd_str)
            if check_converged(wd):
                move_crisp_outputs(wd)
                try:
                    from vasp_sop.core.cache import vasp_results_put
                    vasp_results_put(wd)
                except Exception as exc:
                    logger.warning("Failed to cache %s: %s", wd.name, exc)
                clear_submission(wd_str)

        # Advance step
        _advance_one_system(s, dry_run=False)

        # Check completion — _phase is defined in this module
        p = _phase(s)
        if p in ("DONE", "NO_TARGET"):
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
    p = subparsers.add_parser("cache", help="Manage calculation caches")
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
    sp = sub.add_parser("query", help="Semantic cross-project cache query")
    sp.add_argument("--formula", "-f", help="Filter by chemical formula")
    sp.add_argument("--functional", help="Filter by functional (e.g. PBE, HSE, SCAN)")
    sp.add_argument("--calc-type", help="Filter by calc type (e.g. Static, Relax)")
    sp.add_argument("--tags", help="Filter by tags (e.g. DFT+U, spin)")
    sp.add_argument("--bandgap-min", type=float, help="Minimum bandgap (eV)")
    sp.add_argument("--max-lattice", type=float, help="Max lattice constant a/b/c (Å), filters out large cells")
    sp.add_argument("--limit", type=int, default=50, help="Max results")

    # migrate
    sp = sub.add_parser("migrate", help="Migrate from old SQLite cache to JSONStore")
    sp.add_argument("--force", action="store_true",
                    help="Force migration even if JSONStore already has data")

    # verify
    sub.add_parser("verify", help="Check store consistency")


def _handle_cache(args: argparse.Namespace) -> None:
    from vasp_sop.core.cache import (
        cache_lookup, vasp_results_put, query, list_cache,
        cache_stats, migrate_from_sqlite, _get_stores,
    )
    from pathlib import Path
    if args.cache_action == "put":
        if args.recursive:
            root = args.path.resolve()
            if not root.is_dir():
                print(f"Not a directory: {root}")
                return

            from tqdm import tqdm

            # Phase 1: collect all OUTCAR dirs (single-pass rglob)
            all_dirs: list[Path] = []
            for outcar in sorted(root.rglob("OUTCAR")):
                d = outcar.parent
                if d.name == "output" and (d.parent / "OUTCAR").is_file():
                    continue
                all_dirs.append(d)

            if not all_dirs:
                print("No OUTCARs found.")
                return

            # Phase 2: classify serially (NFS-friendly, cache-aware)
            to_cache: list[Path] = []
            unconverged: list[Path] = []

            for d in tqdm(all_dirs, desc="Scanning", unit=" dirs"):
                if cache_lookup(d) is not None:
                    continue  # already cached
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

            # Phase 3: report cached + unconverged
            cached_count = len(all_dirs) - len(to_cache) - len(unconverged)
            if cached_count:
                print(f"  {cached_count} directories already cached, skipped.")
            for d in unconverged:
                print(f"  ! {d} (not converged)")


            # Phase 4: cache serially (parse + write incrementally)
            if to_cache:
                from vasp_sop.core.cache import _parse_and_build, _get_stores
                meta_store, blob_store = _get_stores()
                meta_docs, blob_docs = [], []
                total_cached = 0
                for d in tqdm(to_cache, desc="Caching", unit=" dirs"):
                    try:
                        r = _parse_and_build(d)
                        if r:
                            meta_docs.append(r["meta"])
                            if r.get("blob"):
                                blob_docs.append(r["blob"])
                        if len(meta_docs) >= 100:
                            meta_store.update(meta_docs)
                            if blob_docs:
                                blob_store.update(blob_docs)
                            total_cached += len(meta_docs)
                            meta_docs, blob_docs = [], []
                    except Exception as exc:
                        print(f"\n  ! {d} (parse failed: {exc})")

                if meta_docs:
                    meta_store.update(meta_docs)
                    if blob_docs:
                        blob_store.update(blob_docs)
                    total_cached += len(meta_docs)
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
        vasp_results_put(path, formula=args.formula,
                         task_name=getattr(args, "task_name", None))
        status = "converged" if converged else "not converged"
        print(f"Cached {path} ({status})")
        return

    if args.cache_action == "status":
        stats = cache_stats()
        print(f"vasp_results: {stats['total_entries']} entries  "
              f"({stats['converged_entries']} converged)  "
              f"{len(stats['formulas'])} unique formulas")
        if stats["formulas"]:
            print(f"Formulas: {', '.join(stats['formulas'][:20])}")

        if args.verbose:
            print()
            for entry in list_cache(limit=200):
                c = "C" if entry.get("converged") else " "
                e = f"{entry.get('total_energy', 0):.4f}" if entry.get("total_energy") is not None else "?"
                sg = entry.get("space_group") or "?"
                ns = str(entry.get("nsites") or "?")
                src = entry.get("source_dir") or "?"
                import datetime
                ts = datetime.datetime.fromtimestamp(
                    entry.get("cached_at", 0)
                ).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {c} {entry['formula']:12s} {entry.get('content_hash', '')[:12]:12s}"
                      f"  E={e}  {ns:>4s} sites  {sg:8s}"
                      f"  {ts}  {src}")

    elif args.cache_action == "query":
        results = query(
            formula=args.formula,
            functional=args.functional,
            calc_type=args.calc_type,
            tags_contains=args.tags,
            bandgap_min=args.bandgap_min,
            lattice_max=args.max_lattice,
            limit=args.limit,
        )
        print(f"{len(results)} results:")
        for r in results:
            e = f"{r.get('total_energy', 0):.4f}" if r.get('total_energy') is not None else "?"
            bg = f"{r.get('bandgap', 0):.2f}" if r.get('bandgap') is not None else "?"
            abc = f"{r.get('max_abc', 0):.1f}" if r.get('max_abc') else "?"
            print(f"  {r['formula']:12s}  E={e}  gap={bg}eV  max_abc={abc}Å"
                  f"  {r.get('calc_type') or '':10s}  tags={r.get('tags', '')}")

    elif args.cache_action == "migrate":
        stats = cache_stats()
        if stats["total_entries"] > 0 and not args.force:
            print(f"JSONStore already has {stats['total_entries']} entries. "
                  f"Use --force to overwrite.")
            return
        n = migrate_from_sqlite()
        print(f"Migrated {n} records from SQLite cache.db.")

    elif args.cache_action == "verify":
        from collections import defaultdict
        meta_store, _ = _get_stores()
        all_entries = list(meta_store.query(
            criteria={},
            properties=["formula", "content_hash", "source_dir", "cached_at"],
        ))
        by_formula: dict[str, list] = defaultdict(list)
        for e in all_entries:
            by_formula[e.get("formula", "UNKNOWN")].append(e)
        stats = cache_stats()
        print(f"Total entries: {stats['total_entries']}")
        print(f"Converged: {stats['converged_entries']}")
        print(f"Unique formulas: {len(by_formula)}")
        for formula in sorted(by_formula):
            entries = by_formula[formula]
            ok = "OK" if any(e.get("total_energy") for e in entries) else "NO_BLOB"
            print(f"  {formula:12s}  {ok}  ({len(entries)} entries)")
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
    rp.add_argument(
        "--dry-run", action="store_true",
        help="Build defect structures and generate inputs only; do NOT submit any VASP jobs.",
    )
    rp.add_argument(
        "--exclude", action="append", default=[],
        help="Exclude a system by directory name (repeatable: --exclude hBN --exclude orth-SiC)",
    )

    # progress
    pp = sub.add_parser("progress", help="Show per-system completion percentage")
    pp.add_argument(
        "root", type=Path,
        help="Project root directory containing system subdirectories",
    )



def _handle_batch(args: argparse.Namespace) -> None:
    if args.batch_action == "status":
        _batch_status(args.root.resolve())
    elif args.batch_action == "generate-inputs":
        _batch_generate_inputs(args.root.resolve(), unitcell=args.unitcell)
    elif args.batch_action == "submit":
        _batch_submit(args.root.resolve(), all_phases=args.all_phases)
    elif args.batch_action == "run":
        _batch_run(args.root.resolve(), poll_interval=args.poll, dry_run=args.dry_run,
                   exclude=args.exclude)
    elif args.batch_action == "progress":
        _batch_progress(args.root.resolve())




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
    from vasp_sop.vasp.io import check_converged, input_ready

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

    # ── Summary footer ────────────────────────────────────────────────
    # Status string conventions used by _check_cpd / _check_unitcell /
    # _check_defect: "✓" = done, "▶" or "N/M" = in progress, "·" = not started.
    total = len(rows)
    completed = sum(
        1 for r in rows
        if r["cpd"] == "✓" and r["uc"] == "✓" and r["defect"] == "✓"
    )
    in_progress = total - completed - sum(
        1 for r in rows
        if r["cpd"] == "·" and r["uc"] == "·" and r["defect"] == "·"
    )
    not_started = sum(
        1 for r in rows
        if r["cpd"] == "·" and r["uc"] == "·" and r["defect"] == "·"
    )
    print("-" * 55)
    print(
        f"Total: {total}  Completed: {completed}  "
        f"In progress: {in_progress}  Not started: {not_started}"
    )


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
    for pd in cpd_dir.iterdir():
        if pd.is_dir() and pattern.search(pd.name):
            return pd
    return None


def _competing_dirs(s: dict) -> list[Path]:
    """Return dirs in cpd/ that need VASP submission."""
    from vasp_sop.vasp.io import check_converged, input_ready
    from vasp_sop.core.cache import cache_lookup, is_submitted
    import logging as _log
    _logr = _log.getLogger(__name__)
    td = _target_dir(s)
    cpd_dir = s["root"] / _CPD
    result: list[Path] = []
    for pd in cpd_dir.iterdir():
        if not pd.is_dir() or pd.name == (td.name if td else ""):
            continue
        if pd.name in ("combos", "mp_flag"):
            continue
        if not input_ready(pd):
            poscar = pd / "POSCAR"
            if poscar.is_file():
                _logr.warning("Competing phase %s has POSCAR but no VASP inputs "
                              "(INCAR/POTCAR missing)", pd.name)
            continue
        if check_converged(pd):
            continue
        if is_submitted(str(pd.resolve())):
            continue
        if cache_lookup(pd) is not None:
            continue
        result.append(pd)
    return sorted(result)


def _phase(s: dict) -> str:
    """Determine the current pipeline phase for a system dict.

    Uses phase-persistence gates: once CPD_POST has written
    ``target_vertices.yaml``, the system never regresses to
    COMPETING — even when competing-phase submissions reappear.
    """
    from vasp_sop.vasp.io import check_converged
    from vasp_sop.core.cache import cache_lookup
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
            return "UC_DF"
        uc_pending = any(
            cache_lookup(uc_root / t) is None for t in uc_tasks
            if (uc_root / t / "INCAR").is_file()
        )
        df_root = s["root"] / _DF
        if not df_root.is_dir():
            return "UC_DF"
        if not (df_root / "defect_energy_summary.json").is_file():
            return "UC_DF"
        if uc_pending:
            return "UC_DF"
        return "DONE"

    # ── Normal upstream progression (CPD not yet complete) ────────
    if cache_lookup(td) is None:
        return "TARGET"
    if _competing_dirs(s):
        return "COMPETING"
    return "CPD_POST"




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


def _advance_one_system(s: dict, *, dry_run: bool = False) -> None:
    """Advance one system by one cycle (runs serially in batch mode)."""
    # Re-imports needed for module-level dispatch
    import logging
    from pathlib import Path
    from vasp_sop.vasp.io import check_converged, input_ready, prepare_inputs
    from vasp_sop.core.jobs import submit_vasp, move_crisp_outputs
    from vasp_sop.defect import unitcell as _uc
    from vasp_sop.defect import cpd as _cpd
    from vasp_sop.defect.builder import build_all as _build_defects
    from vasp_sop.defect.analysis import analyze as _analyze_defects
    from vasp_sop.core.cache import (
        cache_lookup, vasp_results_get as _crg, vasp_results_put,
        mark_submitted, is_submitted,
    )
    _logger = logging.getLogger(__name__)

    def _submit_or_skip(path: Path, label: str, sys_name: str) -> object:
        if dry_run:
            if not label.startswith("df-"):
                print(f"  [dry-run] {sys_name:<18} would submit: {label}")
            return None
        try:
            job = submit_vasp(path.resolve())
            mark_submitted(str(path.resolve()), job.task_name)
            print(f"  → {sys_name:<18} {label}: {job.task_name}")
            return job
        except Exception as exc:
            _logger.warning("%s/%s submit failed: %s", sys_name, label, exc)
            return None




    p = _phase(s)
    if p == "DONE" or p == "NO_TARGET":
        return

    root_dir = s["root"]
    cpd_root = root_dir / _CPD
    uc_root = root_dir / _UC
    df_root = root_dir / _DF

    # Build defect structures as soon as target POSCAR exists (regardless of phase)
    td = _target_dir(s)
    if td and (td / "POSCAR").is_file():
        if not (df_root / "defect_in.yaml").is_file():
            _logger.info("%s: building defect structures (early, phase=%s) ...", s["name"], p)
            try:
                _build_defects(df_root, td, s["config"])
            except Exception as exc:
                _logger.error("%s defect build failed: %s", s["name"], exc)
        # Fill in any missing VASP inputs (parallel now, cheap)
        if (df_root / "defect_in.yaml").is_file():
            potcar_count = len(list(df_root.rglob("POTCAR")))
            dir_count = len([c for c in df_root.iterdir() if c.is_dir()])
            if potcar_count < dir_count:
                _logger.info("%s: completing missing VASP inputs (%d/%d POTCARs) ...",
                             s["name"], potcar_count, dir_count)
                try:
                    from vasp_sop.defect.builder import _generate_vasp_inputs
                    _generate_vasp_inputs(df_root, s["config"])
                except Exception as exc:
                    _logger.error("%s VASP inputs completion failed: %s", s["name"], exc)
        # Dry-run summary: count defect dirs that would be submitted
        if dry_run and (df_root / "defect_in.yaml").is_file():
            n_df = len([c for c in df_root.iterdir()
                        if c.is_dir() and c.name != "perfect" and (c / "INCAR").is_file()])
            n_perfect = 1 if (df_root / "perfect" / "INCAR").is_file() else 0
            uc_tasks = [t for t in ("band", "dos", "dielectric") if (uc_root / t / "INCAR").is_file()]
            parts = []
            if uc_tasks:
                parts.append("uc-" + "+".join(uc_tasks))
            if n_df:
                parts.append(f"df-{n_df} defects")
            if n_perfect:
                parts.append("perfect")
            if parts:
                print(f"  [dry-run] {s['name']:<18} would submit: {' '.join(parts)}")
    if p == "TARGET":
        td = _target_dir(s)
        if td and not is_submitted(str(td.resolve())) and not check_converged(td):
            f, m = s["formula"], s["mpid"]
            cached = None
            if f and m:
                cached = _crg(f, m)
            if cached:
                _logger.info("%s target restored from calc cache", s["name"])
                from vasp_sop.core.cache import restore_from_cache
                restore_from_cache(td)
                import json as _json
                submit_info = {"task_name": "cached", "work_dir": str(td.resolve())}
                with open((s["root"] / _CPD / ".target_submit.json"), "w") as _f:
                    _json.dump(submit_info, _f)

    if p == "COMPETING":
        for cd in _competing_dirs(s):
            if is_submitted(str(cd.resolve())):
                continue
            if "_mp-" in cd.name:
                _cf, _cm = cd.name.split("_mp-", 1)
                _cached = _crg(_cf, _cm)
                if _cached:
                    _logger.info("%s restored from calc cache", cd.name)
                    continue
            _submit_or_skip(cd, f"phase:{cd.name}", s["name"])
        return

    if p == "CPD_POST":
        if not dry_run:
            for pd in cpd_root.iterdir():
                if pd.is_dir() and check_converged(pd):
                    move_crisp_outputs(pd)
            _logger.info("%s: CPD post-processing ...", s["name"])
            try:
                target_composition = _cpd._get_target_composition(s["formula"])
                _cpd.compute_chemical_potentials(cpd_root, s["config"], target_composition)
                f, m = s["formula"], s["mpid"]
                if f and m:
                    try:
                        vasp_results_put(_target_dir(s))
                    except Exception:
                        pass
            except Exception as exc:
                _logger.error("%s CPD failed: %s", s["name"], exc)
                print(f"  ✗ {s['name']:<18} CPD post-processing FAILED")
        return
    if p == "UC_DF":
        # ── Dry-run artifact-based preview ────────────────────────────
        # The UC_DF branch normally submits VASP and (when converged) calls
        # _analyze_defects for post-processing. In dry-run, no VASP will ever
        # run, so the convergence gate never opens. We instead check whether
        # the artifacts post-processing would consume are all present and
        # log what *would* happen — without mutating anything. See issue #20.
        if dry_run:
            artifacts = {
                "unitcell.yaml": uc_root / "unitcell.yaml",
                "target_vertices.yaml": cpd_root / "target_vertices.yaml",
                "standard_energies.yaml": cpd_root / "standard_energies.yaml",
            }
            missing = [name for name, p in artifacts.items() if not p.is_file()]
            has_defect_contcar = False
            if df_root.is_dir():
                has_defect_contcar = any(
                    child.is_dir() and (child / "CONTCAR").is_file()
                    for child in df_root.iterdir()
                )
            if not has_defect_contcar:
                missing.append("defect/CONTCAR")
            done_summary = df_root / "defect_energy_summary.json"
            if not missing and not done_summary.is_file():
                print(
                    f"  [dry-run] {s['name']:<18} would post-process "
                    f"(artifacts present, no analysis run)"
                )
            elif not missing and done_summary.is_file():
                print(
                    f"  [dry-run] {s['name']:<18} already complete "
                    f"(summary exists)"
                )
            else:
                print(
                    f"  [dry-run] {s['name']:<18} post-process blocked "
                    f"(missing: {', '.join(missing)})"
                )

        try:
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
                from vasp_sop.vasp.io import check_task_complete
                if check_task_complete(task_dir, task):
                    continue
                if is_submitted(str(task_dir.resolve())):
                    continue
                if cache_lookup(task_dir) is not None:
                    continue
                prepare_inputs(task_dir, s["config"], task_type=task)
                _submit_or_skip(task_dir, f"uc-{task}", s["name"])

            if df_root.is_dir() and not (df_root / "defect_energy_summary.json").is_file():
                for child in sorted(df_root.iterdir()):
                    if not child.is_dir():
                        continue
                    if not input_ready(child):
                        continue
                    if check_converged(child):
                        continue
                    if is_submitted(str(child.resolve())):
                        continue
                    if cache_lookup(child) is not None:
                        continue
                    _submit_or_skip(child, f"df-{child.name}", s["name"])

            uc_all_done = all(
                cache_lookup(uc_root / t) is not None or not (uc_root / t / "INCAR").is_file()
                for t in ("band", "dos", "dielectric")
            )
            # All defect VASP calculations are *cached* — no more should
            # be submitted to the queue (cache-basesd gate).
            df_vasp_cached = all(
                cache_lookup(child) is not None or not input_ready(child)
                for child in df_root.iterdir() if child.is_dir()
            ) if df_root.is_dir() else True
            # All defect VASP calculations have OUTCAR on disk — the
            # post-processing pipeline (pydefect CLI) reads from files,
            # so we must not run it until every OUTCAR is actually present.
            df_vasp_ondisk = all(
                check_converged(child) or not input_ready(child)
                for child in df_root.iterdir() if child.is_dir()
            ) if df_root.is_dir() else True

            if uc_all_done and df_vasp_cached and df_vasp_ondisk \
                    and (df_root / "defect_energy_summary.json").is_file():
                pass  # already done
            elif uc_all_done and df_vasp_cached and df_vasp_ondisk:
                _logger.info("%s: post-processing ...", s["name"])
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
                    _logger.error("%s post-processing failed: %s", s["name"], exc)
        except Exception as exc:
            _logger.error("%s UC_DF failed: %s", s["name"], exc)
            print(f"  ✗ {s['name']:<18} UC_DF FAILED")

def _batch_run(root: Path, *, poll_interval: int = 60, dry_run: bool = False,
               exclude: list[str] | None = None) -> None:
    """Batch pipeline — advance all systems by one cycle (single pass).

    One-shot: advances each system once by its current phase, submits
    ready VASP jobs, then exits.  No background loop, no worker pool —
    runs serially so output is real-time and no orphan processes leak.

    When *dry_run* is True, build/regenerate inputs locally but do NOT
    submit any VASP jobs.

    *exclude* — list of system directory names to skip.
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
        print("No systems found.")
        return

    print(f"Batch run: {len(sys_list)} systems\n")


    # ── Populate submission DB from crisp + filesystem ────────────────
    if not dry_run:
        from vasp_sop.core.cache import mark_submitted
        _crisp_active = _crisp_active_dirs(skip=False)
        if _crisp_active:
            logger.info("Found %d active crisp tasks, recording in submission DB.",
                        len(_crisp_active))
            for p in _crisp_active:
                mark_submitted(p, "restored")

    from vasp_sop.core.cache import cache_lookup, vasp_results_put as _cache_put

    def _cache_phase_results(wd: Path) -> None:
        try:
            _cache_put(wd)
        except Exception as exc:
            logger.warning("Failed to cache %s: %s", wd.name, exc)

    # ── Submit helper ──────────────────────────────────────────────
    def _submit_or_skip(path: Path, label: str, sys_name: str) -> object:
        if dry_run:
            print(f"  [dry-run] {sys_name:<18} would submit: {label}")
            return None
        try:
            from vasp_sop.core.cache import mark_submitted
            from vasp_sop.core.cache import lattice_too_large
            if lattice_too_large(path):
                logger.error("%s/%s: lattice too large (>MAX_LATTICE=%.1f Å), skipped",
                             sys_name, label, 25.0)
                print(f"  ✗ {sys_name:<18} {label}: lattice too large, skipped")
                return None
            job = submit_vasp(path.resolve())
            mark_submitted(str(path.resolve()), job.task_name)
            print(f"  → {sys_name:<18} {label}: {job.task_name}")
            return job
        except RuntimeError as exc:
            logger.error("%s/%s submit failed (RuntimeError): %s", sys_name, label, exc)
            print(f"  ✗ {sys_name:<18} {label}: {exc}")
            return None
        except Exception as exc:
            logger.warning("%s/%s submit failed: %s", sys_name, label, exc)
            return None

    if dry_run:
        print("Dry-run mode: will build defect structures and generate inputs, NO VASP submission.\n")

    # ── Backfill cache ──────────────────────────────────────────
    backfilled = 0
    for s in sys_list:
        cpd_root = s["root"] / _CPD
        if not cpd_root.is_dir():
            continue
        for pd in cpd_root.iterdir():
            if not pd.is_dir() or "_mp-" not in pd.name:
                continue
            if cache_lookup(pd) is not None:
                continue
            if not check_converged(pd):
                continue
            from vasp_sop.core.jobs import move_crisp_outputs
            move_crisp_outputs(pd)
            formula, mpid = pd.name.split("_mp-", 1)
            _cache_put(pd, formula=formula, task_name=f"{formula}_mp-{mpid}")
            backfilled += 1
    if backfilled:
        logger.info("Backfilled %d already-converged phase results into cache.", backfilled)

    # ── Sweep for orphan crisp outputs ──────────────────────────
    orphaned = 0
    for s in sys_list:
        for root_dir in (s["root"] / _UC, s["root"] / _DF):
            if not root_dir.is_dir():
                continue
            for child in root_dir.iterdir():
                if not child.is_dir():
                    continue
                output_dir = child / "output"
                if not output_dir.is_dir():
                    continue
                if not (output_dir / "OUTCAR").is_file():
                    continue
                move_crisp_outputs(child)
                if cache_lookup(child) is None:
                    _cache_phase_results(child)
                orphaned += 1
    if orphaned:
        logger.info("Processed %d orphaned crisp outputs.", orphaned)

    # ── Poll completed submissions (one-shot) ────────────────────────
    from vasp_sop.core.cache import _get_submitted_dirs, clear_submission
    completed = 0
    for wd_str in list(_get_submitted_dirs()):
        wd = Path(wd_str)
        if check_converged(wd):
            move_crisp_outputs(wd)
            _cache_phase_results(wd)
            clear_submission(wd_str)
            logger.info("Completed: %s", wd.name)
            completed += 1
    if completed:
        print(f"  Cached {completed} completed calculation(s).")

    # ── Advance all systems (serial, single pass) ────────────────────
    n_skipped = 0
    errors: list[tuple[str, str]] = []  # (name, reason)
    for idx, s in enumerate(sys_list, 1):
        name = s["name"]
        p = _phase(s)
        if p in ("DONE", "NO_TARGET"):
            n_skipped += 1
            continue

        print(f"  [{idx}/{len(sys_list)}] {name:<18} {p} ...", end="", flush=True)
        try:
            _advance_one_system(s, dry_run=dry_run)
            print(" done")
        except Exception as exc:
            reason = str(exc).split("(")[0].strip() or type(exc).__name__
            _logger.error("%s advance failed: %s", name, exc)
            print(f" FAILED ({reason})")
            errors.append((name, reason))

    if n_skipped:
        print(f"  [{n_skipped}/{len(sys_list)} systems already done, skipped]\n")

    # ── Final status ──────────────────────────────────────────────
    phases = [_phase(s) for s in sys_list]
    done_count = sum(1 for p in phases if p in ("DONE", "NO_TARGET"))
    counts = {p: phases.count(p) for p in sorted(set(phases))}
    parts = [f"{p}={n}" for p, n in sorted(counts.items())]
    print(f"{'  '.join(parts)}")

    if errors:
        print(f"\n  ⚠ {len(errors)} system(s) with errors:")
        for name, reason in errors:
            print(f"    {name:<18}  {reason}")

    if done_count == len(sys_list):
        print("\nAll systems complete.")
    else:
        still = len(sys_list) - done_count
        blocked = len(errors)
        running = still - blocked
        print(f"\n{running} running, {blocked} blocked, {still} remaining — re-run `vasp-sop batch run .` after VASP jobs complete.")


def _batch_generate_inputs(root: Path, *, unitcell: bool = False) -> None:
    """Generate VASP inputs for all systems in *root* that need them."""
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

    ok = 0
    fail = 0
    for t in tasks:
        try:
            sys_name, phase_name, phase_dir, plan_path = t
            config = PipelineConfig.from_yaml(plan_path, root=phase_dir.parent.parent)
            prepare_inputs(phase_dir, config)
            ok += 1
            print(f"  OK  {sys_name}/{phase_name}")
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

