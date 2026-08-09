"""Tests for the defect-directory validity gate — ``vasp_sop.defect``.

Covers ``is_valid_defect_dir`` including the anion-cation antisite
exclusion (ADR 0013).
"""

from pathlib import Path

import pytest

from vasp_sop.defect import is_valid_defect_dir


def _dir(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    return d


class TestNameChargePattern:
    def test_vacancy_valid(self, tmp_path: Path):
        assert is_valid_defect_dir(_dir(tmp_path, "Va_O1_-1"))

    def test_cation_cation_antisite_valid(self, tmp_path: Path):
        # Gd on Sb site — chemically reasonable, kept (ADR 0013 keeps
        # metal↔metal substitutions).
        assert is_valid_defect_dir(_dir(tmp_path, "Gd_Sb1_6"))

    def test_dopant_on_cation_site_valid(self, tmp_path: Path):
        assert is_valid_defect_dir(_dir(tmp_path, "Bi_Gd1_0"))

    def test_no_charge_suffix_valid(self, tmp_path: Path):
        assert is_valid_defect_dir(_dir(tmp_path, "Va_O1"))

    def test_junk_dir_invalid(self, tmp_path: Path):
        assert not is_valid_defect_dir(_dir(tmp_path, "junkdir"))


class TestAnionCationAntisiteExclusion:
    """ADR 0013: exactly-one-anion-side substitutions are excluded."""

    @pytest.mark.parametrize(
        "name",
        [
            "O_Ga1_0",      # anion on cation site
            "O_Ga1_-5",
            "O_Ti2_0",
            "Bi_O1_0",      # cation on anion site
            "Ti_O1_4",
            "S_Ba1_-3",     # sulfide host
            "Ba_S1_4",
            "O_Ge2_-4",     # Ge is metalloid — still a cation role here
        ],
    )
    def test_excluded(self, tmp_path: Path, name: str):
        assert not is_valid_defect_dir(_dir(tmp_path, name))

    @pytest.mark.parametrize(
        "name",
        [
            "Va_O1_0",              # vacancy — not a substitution
            "Gd_Sb1_6",             # cation↔cation
            "Sb_Ga1_-1",
            "Y_Ti4_-1",
            "Fe_Ca1_0",
            "Gd_Ga1+Va_O1_-1",      # complex defect — untouched
            "O_i1_-2",              # interstitial naming — untouched
        ],
    )
    def test_kept(self, tmp_path: Path, name: str):
        assert is_valid_defect_dir(_dir(tmp_path, name))

    def test_defect_entry_fallback_still_applies(self, tmp_path: Path):
        # A dir without the Name_Charge pattern but with defect_entry.json
        # is still valid even if the name is not parseable.
        d = _dir(tmp_path, "weird_name")
        (d / "defect_entry.json").write_text("{}")
        assert is_valid_defect_dir(d)
