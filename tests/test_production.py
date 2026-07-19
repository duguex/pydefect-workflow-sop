"""Gold-sample regression checks for publishable formation energies (#101).

Gold systems: GaN, AlN — completed (DONE phase) systems in the production tree
whose defect formation energies are considered publication-ready.

These tests validate the integrity of post-processing outputs (defect_energy_summary.json,
correction.json) against the production data tree.  They are skipped unless the
environment variable VASP_SOP_PROD_ROOT points to a valid production root directory.

Regenerate formation-energy figure:
    cd $VASP_SOP_PROD_ROOT && vasp-sop batch run . --dry-run && \
    python -c "from vasp_sop.defect.analysis import plot_formation_energies; plot_formation_energies('GaN')"
"""

import json
import os
import re
from pathlib import Path

import pytest

PROD_ROOT = os.environ.get("VASP_SOP_PROD_ROOT", "")

GOLD_SYSTEMS = ["GaN", "AlN"]

# Maximum allowed absolute correction energy (eV).  Override via env var.
CORRECTION_BOUND = float(os.environ.get("VASP_SOP_CORRECTION_BOUND", "1.0"))

# Maximum allowed gap between consecutive charge states.
MAX_CHARGE_GAP = 2

requires_prod = pytest.mark.skipif(
    not os.environ.get("VASP_SOP_PROD_ROOT"),
    reason="production tree not available",
)


def _defect_dir(system: str) -> Path:
    """Return the defect directory for a gold system."""
    return Path(PROD_ROOT) / system / "defect"


def _load_energy_summary(system: str) -> dict:
    """Load and return defect_energy_summary.json for a system."""
    path = _defect_dir(system) / "defect_energy_summary.json"
    assert path.exists(), f"Missing defect_energy_summary.json for {system}: {path}"
    with open(path) as f:
        data = json.load(f)
    return data


def _find_charge_state_dirs(system: str) -> list:
    """Find all charge-state directories under the defect directory.

    Charge state directories match patterns like:
      V_Ga_0, V_Ga_1, V_Ga_-1, V_N_2, antisite_Ga_on_N_-2, etc.
    The trailing integer (possibly negative) is the charge state.
    """
    defect_dir = _defect_dir(system)
    if not defect_dir.exists():
        return []
    dirs = []
    for entry in sorted(defect_dir.iterdir()):
        if entry.is_dir() and re.search(r"[-+]?\d+$", entry.name):
            dirs.append(entry)
    return dirs


def _extract_charge(dirname: str) -> int:
    """Extract the integer charge from a directory name."""
    m = re.search(r"([-+]?\d+)$", dirname)
    if m:
        return int(m.group(1))
    return 0


def _is_converged(charge_dir: Path) -> bool:
    """Check if a charge-state directory has a converged OUTCAR."""
    outcar = charge_dir / "OUTCAR"
    if not outcar.exists():
        return False
    # Simple convergence check: look for 'reached required accuracy'
    try:
        text = outcar.read_text(errors="ignore")
        return "reached required accuracy" in text
    except OSError:
        return False


@requires_prod
class TestProductionFormationEnergies:
    """Gold-sample regression checks for GaN and AlN formation energies (#101)."""

    def test_defect_energy_summary_exists_and_parses(self):
        """defect_energy_summary.json exists and is valid JSON for gold systems."""
        for system in GOLD_SYSTEMS:
            data = _load_energy_summary(system)
            assert isinstance(data, (dict, list)), (
                f"{system}: defect_energy_summary.json is not a dict or list"
            )

    def test_converged_charge_states_have_correction(self):
        """Every converged charge state directory has a correction.json."""
        for system in GOLD_SYSTEMS:
            charge_dirs = _find_charge_state_dirs(system)
            assert len(charge_dirs) > 0, (
                f"{system}: no charge-state directories found in {_defect_dir(system)}"
            )
            for cdir in charge_dirs:
                if _is_converged(cdir):
                    correction_path = cdir / "correction.json"
                    assert correction_path.exists(), (
                        f"{system}/{cdir.name}: converged but missing correction.json"
                    )

    def test_correction_within_bound(self):
        """Absolute correction energy is below the configurable bound."""
        for system in GOLD_SYSTEMS:
            charge_dirs = _find_charge_state_dirs(system)
            for cdir in charge_dirs:
                correction_path = cdir / "correction.json"
                if not correction_path.exists():
                    continue
                with open(correction_path) as f:
                    corr_data = json.load(f)
                # correction.json may store total correction under various keys
                correction_value = None
                if isinstance(corr_data, dict):
                    for key in ("total_correction", "correction", "energy_correction"):
                        if key in corr_data:
                            correction_value = float(corr_data[key])
                            break
                    if correction_value is None:
                        # Try first numeric value
                        for v in corr_data.values():
                            if isinstance(v, (int, float)):
                                correction_value = float(v)
                                break
                elif isinstance(corr_data, (int, float)):
                    correction_value = float(corr_data)

                if correction_value is not None:
                    assert abs(correction_value) < CORRECTION_BOUND, (
                        f"{system}/{cdir.name}: |correction| = {abs(correction_value):.4f} eV "
                        f">= bound {CORRECTION_BOUND} eV"
                    )

    def test_charge_states_continuous(self):
        """Charge states form a continuous sequence with no gaps > MAX_CHARGE_GAP."""
        for system in GOLD_SYSTEMS:
            charge_dirs = _find_charge_state_dirs(system)
            if not charge_dirs:
                continue
            # Group by defect type (everything before the trailing charge number)
            defect_groups = {}
            for cdir in charge_dirs:
                m = re.match(r"^(.*?)([-+]?\d+)$", cdir.name)
                if m:
                    prefix = m.group(1)
                    charge = int(m.group(2))
                    defect_groups.setdefault(prefix, []).append(charge)

            for prefix, charges in defect_groups.items():
                charges_sorted = sorted(set(charges))
                for i in range(1, len(charges_sorted)):
                    gap = charges_sorted[i] - charges_sorted[i - 1]
                    assert gap <= MAX_CHARGE_GAP, (
                        f"{system}/{prefix}: charge gap {gap} between "
                        f"{charges_sorted[i-1]} and {charges_sorted[i]} "
                        f"exceeds max allowed {MAX_CHARGE_GAP}"
                    )
