import argparse
import asyncio
import json
import logging
import os
import sys

from . import __version__
from .plan import generate_plan, write_plan, read_plan, print_summary, validate as validate_plan
from .config import load_plan
from .utils import log_setup

logger = None


def cmd_plan(args):
    if getattr(args, "validate", False):
        cmd_plan_validate(args)
        return

    root = os.path.abspath(args.dir)
    os.makedirs(root, exist_ok=True)
    plan_path = os.path.join(root, "plan.yaml")
    if os.path.isfile(plan_path) and not args.force:
        print(f"plan.yaml already exists at {plan_path}")
        print("Use --force to overwrite")
        return
    if not args.obj:
        print("Error: --obj is required to generate plan")
        return

    dopant = args.dopant or []
    poscar = args.poscar or None

    plan = generate_plan(root, args.obj, dopant_elements=dopant,
                          poscar_src=poscar, functional=args.functional,
                          encut=args.encut)

    # Print summary
    print(print_summary(plan))

    # Write plan.yaml
    write_plan(root, plan)
    print(f"plan.yaml written to {plan_path}")
    print("Review and edit plan.yaml, then run: pydefect-run run")


def cmd_plan_validate(args):
    path = os.path.join(args.dir, "plan.yaml")
    if not os.path.isfile(path):
        print(f"plan.yaml not found in {args.dir}")
        return
    plan = read_plan(args.dir)
    errors = validate_plan(plan)
    if errors:
        print("plan.yaml 校验失败:")
        for e in errors:
            print(f"  ✗ {e}")
    else:
        print("plan.yaml 校验通过")


def cmd_check(args):
    plan_path = os.path.join(args.dir, "plan.yaml")
    if not os.path.isfile(plan_path):
        print(f"No plan.yaml found in {args.dir}")
        return
    try:
        info, raw = load_plan(args.dir)
    except Exception as e:
        print(f"Error reading plan.yaml: {e}")
        return
    print(f"Project: {info['obj']}")
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
    try:
        info, raw = load_plan(args.dir)
    except FileNotFoundError:
        print("plan.yaml not found. Run: pydefect-run plan --obj ...")
        return
    stages_cfg = raw.get("stages", {}) if raw else {}
    single_run(args.dir, info, auto=args.auto, stage_config=stages_cfg)


def cmd_watch(args):
    from .watcher import loop_run
    asyncio.run(loop_run())


def cmd_stage(args):
    try:
        info, raw = load_plan(args.dir)
    except FileNotFoundError:
        print("plan.yaml not found. Run: pydefect-run plan --obj ...")
        return

    stage_map = {
        "1": ("stage1_unitcell", "unitcell"),
        "2": ("stage2_cpd", "cpd"),
        "3": ("stage3_defect_gen", "defect_gen"),
        "4": ("stage4_submit", "submit"),
        "5": ("stage5_postproc", "postproc"),
        "6": ("stage6_doping", "doping"),
        "7": ("stage7_complex", "complex"),
    }
    module_name, stage_key = stage_map.get(args.stage, (None, None))
    if not module_name:
        print(f"Unknown stage: {args.stage}")
        return

    stages_cfg = raw.get("stages", {}) if raw else {}
    if not stages_cfg.get(stage_key, True):
        print(f"Stage {args.stage} is disabled in plan.yaml")
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

    p_plan = sub.add_parser("plan", help="Generate or validate plan.yaml")
    p_plan.add_argument("dir", nargs="?", default=".", help="Project directory")
    p_plan.add_argument("--obj", help="Target chemical formula (not needed for --validate)")
    p_plan.add_argument("--dopant", nargs="*", default=[], help="Dopant elements")
    p_plan.add_argument("--poscar", help="Local POSCAR path (omit for MP download)")
    p_plan.add_argument("--functional", default="pbesol", help="XC functional")
    p_plan.add_argument("--encut", type=int, help="ENCUT override")
    p_plan.add_argument("--force", action="store_true", help="Overwrite existing plan.yaml")
    p_plan.add_argument("--validate", action="store_true", help="Validate only (no generate)")

    p_check = sub.add_parser("check", help="Check project status")
    p_check.add_argument("dir", nargs="?", default=".", help="Project directory")
    p_check.add_argument("--verbose", action="store_true", help="Show full config")

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

    if args.command == "plan":
        cmd_plan(args)
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
