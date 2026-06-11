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

    # ── defect ──────────────────────────────────────────────────────
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
    init_parser.add_argument("-f", "--formula", type=str, default="GaN",
                             help="Compound formula (e.g. GaN, SiC)")
    init_parser.add_argument("-d", "--dopant", type=str, nargs="*", default=[],
                             help="Dopant elements (e.g. Mg Si)")

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
    if args.command == "defect":
        _handle_defect(args)


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

    # Try to load config from the project root
    config_path = root / "config.yaml"
    if config_path.is_file():
        config = PipelineConfig.from_yaml(config_path, root=root)
    else:
        # Minimal config from state — formula may be needed for unitcell
        logger.error(
            "No config.yaml found in %s. Cannot resume without configuration.",
            root,
        )
        sys.exit(1)

    if state.is_terminal():
        logger.info("Pipeline already complete. Nothing to resume.")
        return

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


if __name__ == "__main__":
    main()
