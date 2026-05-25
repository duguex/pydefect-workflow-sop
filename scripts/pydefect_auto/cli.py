import argparse
import asyncio
import json
import logging
import os
import sys

from . import __version__
from .config import init_info, load_info
from .utils import log_setup

logger = None


def cmd_init(args):
    info_path = os.path.join(args.dir, "info.json")
    if os.path.isfile(info_path):
        print(f"info.json already exists at {info_path}")
        return
    obj = args.obj or input("Target chemical formula (e.g. GaN): ").strip()
    init_info(info_path, obj, dopant_element=args.dopant or [],
              interstitial=args.interstitial,
              complex_defect=args.complex or 1)
    print(f"Created {info_path}")


def cmd_check(args):
    info_path = os.path.join(args.dir, "info.json")
    if not os.path.isfile(info_path):
        print(f"No info.json found in {args.dir}")
        return
    info = load_info(info_path)
    print(f"Project: {info['obj']}")
    from .utils import vasp_done_check, sync_output
    stage_checks = {
        1: [("unitcell/unitcell.yaml",), ("unitcell.yaml",)],
        2: [("cpd/target_vertices.yaml",)],
        3: [("defect/supercell_info.json",)],
        5: [("defect/defect_energy_summary.json",), ("defect_energy_summary.json",)],
    }
    for sn, checks in stage_checks.items():
        done = any(os.path.isfile(os.path.join(args.dir, *p)) for p in checks)
        print(f"  Stage {sn}: {'done' if done else 'pending'}")
    if args.verbose:
        print(f"\nFull info: {json.dumps(info, indent=2)}")


def cmd_run(args):
    from .pipeline import single_run
    info_path = os.path.join(args.dir, "info.json")
    info = load_info(info_path)
    asyncio.run(single_run(args.dir, info, auto=args.auto))


def cmd_watch(args):
    from .watcher import loop_run
    asyncio.run(loop_run())


def cmd_stage(args):
    info_path = os.path.join(args.dir, "info.json")
    info = load_info(info_path)

    stage_map = {
        "1": "stage1_unitcell",
        "2": "stage2_cpd",
        "3": "stage3_defect_gen",
        "4": "stage4_submit",
        "5": "stage5_postproc",
        "6": "stage6_doping",
        "7": "stage7_complex",
    }
    module_name = stage_map.get(args.stage)
    if not module_name:
        print(f"Unknown stage: {args.stage}")
        return
    try:
        mod = __import__(f"pydefect_auto.stages.{module_name}", fromlist=["run"])
    except ImportError:
        print(f"Stage {args.stage} not implemented yet")
        return
    result = mod.run(args.dir, info, auto=args.auto)
    print(f"Stage {args.stage}: {'done' if result else 'pending'}")


def main():
    parser = argparse.ArgumentParser(
        description=f"PyDefect Auto v{__version__} — SOP workflow automation"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Create info.json")
    p_init.add_argument("dir", nargs="?", default=".", help="Project directory")
    p_init.add_argument("--obj", help="Target chemical formula")
    p_init.add_argument("--dopant", nargs="*", default=[], help="Dopant elements")
    p_init.add_argument("--interstitial", action="store_true", help="Enable interstitial")
    p_init.add_argument("--complex", type=int, default=1, help="Max complex defect N")

    p_check = sub.add_parser("check", help="Check project status")
    p_check.add_argument("dir", nargs="?", default=".", help="Project directory")
    p_check.add_argument("--verbose", action="store_true", help="Show full info.json")

    p_run = sub.add_parser("run", help="Run full pipeline")
    p_run.add_argument("dir", nargs="?", default=".", help="Project directory")
    p_run.add_argument("--auto", action="store_true", help="Non-interactive mode")

    p_stage = sub.add_parser("stage", help="Run a single stage")
    p_stage.add_argument("stage", help="Stage number (1-7)")
    p_stage.add_argument("dir", nargs="?", default=".", help="Project directory")
    p_stage.add_argument("--auto", action="store_true", help="Non-interactive mode")

    p_watch = sub.add_parser("watch", help="Watch for new projects (loop mode)")

    args = parser.parse_args()
    log_setup(logging.INFO if args.verbose else logging.WARNING)

    if args.command == "init":
        cmd_init(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "stage":
        cmd_stage(args)
    elif args.command == "watch":
        cmd_watch(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
