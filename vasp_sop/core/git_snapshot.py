"""Per-system git repositories tracking calculation *inputs* and small
result artifacts (ADR 0019).

Each system directory (e.g. ``BaAl2B2O7/``) owns a plain ``.git``; the
batch loop snapshots it every ``_GIT_SNAPSHOT_EVERY`` cycles so input
changes (INCAR/POSCAR/KPOINTS/plan.yaml/...) and result snapshots
(CONTCAR geometry, slurm ``*.log``) are auditable over time.  Large and
binary outputs (OUTCAR/vasprun.xml/CHG/...) stay out of git — their
state is already tracked by verdicts, the JobStore and crisp.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Files tracked by every system repo.  Everything else (POTCAR — ADR 0007
# regenerable; binary/large outputs) is ignored.
GITIGNORE = """\
# VASP inputs (regenerable from PSP store, ADR 0007)
POTCAR

# VASP binary outputs
CHG
CHGCAR
WAVECAR
LOCPOT
ELFCAR

# VASP large/process-log outputs (state tracked by verdict/JobStore/crisp)
OUTCAR
vasprun.xml
OSZICAR
LOGCAR
DOSCAR
EIGENVAL
PROCAR
REPORT
IBZKPT
PCDAT
XDATCAR

# Machine-learned force-field artifacts
ML_ABN
ML_FF

# Build/scratch/report artifacts
big_sc_bak/
defect_generate_flag
output/
*.pdf
*.html
__pycache__/

# crisp runtime markers (transient; can vanish mid-snapshot)
.timeout
.crisp-submission.json
"""

_GIT_IDENTITY = ("vasp-sop", "vasp-sop@localhost")


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _ensure_identity(root: Path) -> None:
    """Set repo-local git identity so commits never depend on global config."""
    for key, value in zip(("user.name", "user.email"), _GIT_IDENTITY):
        _git(root, "config", key, value)


def init_system_repo(root: Path) -> bool:
    """Initialise (or re-initialise) the system repo and take a baseline.

    Idempotent: a repo that already exists is left alone (returns False).
    Returns True when a baseline commit was created.
    """
    if not (root / "plan.yaml").is_file():
        raise ValueError(f"{root} is not a system directory (no plan.yaml)")
    if (root / ".git").is_dir():
        return False
    logger.info("git_snapshot: initialising repo for %s", root.name)
    _git(root, "init", "-q")
    (root / ".gitignore").write_text(GITIGNORE)
    _ensure_identity(root)
    return commit_snapshot(root, "baseline: input + result snapshot")


def commit_snapshot(root: Path, message: str) -> bool:
    """Stage everything and commit if anything changed. Returns True if a
    commit was created. Never raises on git failure — logs and returns
    False so the batch loop is never crashed by a git problem."""
    try:
        _git(root, "add", "-A")
        changed = _git(root, "diff", "--cached", "--quiet", check=False)
        if changed.returncode != 1:
            # 0 = nothing staged; anything else = git error, not a commit.
            if changed.returncode != 0:
                logger.error(
                    "git_snapshot: diff failed for %s (rc=%d): %s",
                    root.name, changed.returncode, changed.stderr.strip(),
                )
            return False
        _git(root, "commit", "-q", "-m", message)
        logger.info("git_snapshot: committed %s: %s", root.name, message)
        return True
    except Exception as exc:  # noqa: BLE001 — loop must survive git issues
        logger.error(
            "git_snapshot: commit failed for %s (%s): %s",
            root.name, message, exc,
        )
        return False
