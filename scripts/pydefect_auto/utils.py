import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("pydefect_auto")


def log_setup(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(filename)s[%(lineno)d] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_command(cmd, cwd=None, capture=False):
    logger.info("Running: %s", cmd)
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=capture, text=True
    )
    if result.returncode != 0:
        logger.error("Command failed (code=%d): %s", result.returncode, cmd)
        if capture:
            logger.error("stderr: %s", result.stderr)
    return result


def flag_write(name, cwd=None):
    path = Path(cwd) / name if cwd else Path(name)
    path.write_text("")
    logger.debug("Flag created: %s", path)


def flag_exists(name, cwd=None):
    path = Path(cwd) / name if cwd else Path(name)
    return path.exists()


def flag_remove(name, cwd=None):
    path = Path(cwd) / name if cwd else Path(name)
    if path.exists():
        path.unlink()
        logger.debug("Flag removed: %s", path)


def vasp_input_check(path):
    for f in ("INCAR", "POTCAR", "KPOINTS", "POSCAR"):
        if not os.path.isfile(os.path.join(path, f)):
            return False
    return True


def vasp_done_check(path):
    for sub in [path, os.path.join(path, "output")]:
        if os.path.isfile(os.path.join(sub, "vasprun.xml")) and \
           os.path.isfile(os.path.join(sub, "OUTCAR")):
            return True
    return False


def sync_output(path):
    output_dir = Path(path) / "output"
    if not output_dir.is_dir():
        return
    for f in ["vasprun.xml", "OUTCAR", "CONTCAR", "PROCAR", "EIGENVAL"]:
        src = output_dir / f
        dst = Path(path) / f
        if src.exists() and not dst.exists():
            os.rename(str(src), str(dst))
            logger.info("Synced %s/%s", path, f)


def encut_from_potcar(potcar_path):
    with open(potcar_path) as f:
        for line in f:
            if "ENMAX" in line:
                parts = line.strip().split(";")
                for p in parts:
                    if "ENMAX" in p:
                        val = p.split("=")[-1].strip()
                        return float(val)
    return None


def check_encut_consistency(directories):
    encut_values = {}
    for d in directories:
        incar_path = os.path.join(d, "INCAR")
        if os.path.isfile(incar_path):
            with open(incar_path) as f:
                for line in f:
                    if line.startswith("ENCUT"):
                        encut_values[d] = float(line.split("=")[-1].strip())
                        break
    if len(set(encut_values.values())) > 1:
        logger.warning("ENCUT inconsistency detected: %s", encut_values)
        return False
    return True
