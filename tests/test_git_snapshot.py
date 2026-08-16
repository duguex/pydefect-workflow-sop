"""Per-system git snapshot repos (ADR 0019).

Real-git integration tests: inputs + CONTCAR + *.log are tracked,
POTCAR and large outputs are ignored, commits happen only on change,
and the batch loop snapshots on its cycle cadence.
"""

import subprocess
from pathlib import Path


from vasp_sop.core.git_snapshot import GITIGNORE, commit_snapshot, init_system_repo
from vasp_sop.core.paths import override_cache_root


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _make_system_dir(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "plan.yaml").write_text("project:\n  formula: NaCl\n")
    cpd = root / "cpd" / "NaCl_mp-1"
    cpd.mkdir(parents=True)
    for f in ("INCAR", "POSCAR", "KPOINTS"):
        (cpd / f).write_text("x\n")


def _tracked_files(root: Path) -> set[str]:
    return set(_git(root, "ls-files").split())


class TestInitSystemRepo:
    def test_baseline_commit_created(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        assert init_system_repo(root) is True
        assert (root / ".git").is_dir()
        assert (root / ".gitignore").is_file()
        assert "plan.yaml" in _tracked_files(root)
        assert "cpd/NaCl_mp-1/INCAR" in _tracked_files(root)
        assert len(_git(root, "log", "--oneline").splitlines()) == 1

    def test_idempotent(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        init_system_repo(root)
        assert init_system_repo(root) is False

    def test_ignores_potcar_and_outputs(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        cpd = root / "cpd" / "NaCl_mp-1"
        (cpd / "POTCAR").write_text("POTCAR\n")
        (cpd / "OUTCAR").write_text("out\n")
        (cpd / "vasprun.xml").write_text("<v/>\n")
        (cpd / "CHGCAR").write_text("charge\n")
        init_system_repo(root)
        tracked = _tracked_files(root)
        assert "cpd/NaCl_mp-1/POTCAR" not in tracked
        assert "cpd/NaCl_mp-1/OUTCAR" not in tracked
        assert "cpd/NaCl_mp-1/vasprun.xml" not in tracked
        assert "cpd/NaCl_mp-1/CHGCAR" not in tracked

    def test_tracks_contcar_and_logs(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        cpd = root / "cpd" / "NaCl_mp-1"
        (cpd / "CONTCAR").write_text("geometry\n")
        (cpd / "206588.log").write_text("slurm out\n")
        init_system_repo(root)
        tracked = _tracked_files(root)
        assert "cpd/NaCl_mp-1/CONTCAR" in tracked
        assert "cpd/NaCl_mp-1/206588.log" in tracked


class TestCommitSnapshot:
    def test_commit_on_change(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        init_system_repo(root)
        (root / "cpd" / "NaCl_mp-1" / "INCAR").write_text("NSW = 100\n")
        assert commit_snapshot(root, "cycle snapshot") is True
        assert len(_git(root, "log", "--oneline").splitlines()) == 2

    def test_no_commit_without_change(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        init_system_repo(root)
        assert commit_snapshot(root, "cycle snapshot") is False
        assert len(_git(root, "log", "--oneline").splitlines()) == 1

    def test_ignored_changes_do_not_commit(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        init_system_repo(root)
        (root / "cpd" / "NaCl_mp-1" / "POTCAR").write_text("new pot\n")
        (root / "cpd" / "NaCl_mp-1" / "OUTCAR").write_text("more out\n")
        assert commit_snapshot(root, "cycle snapshot") is False
        assert len(_git(root, "log", "--oneline").splitlines()) == 1

    def test_contcar_change_commits(self, tmp_path: Path):
        root = tmp_path / "sys"
        _make_system_dir(root)
        init_system_repo(root)
        (root / "cpd" / "NaCl_mp-1" / "CONTCAR").write_text("new geometry\n")
        assert commit_snapshot(root, "cycle snapshot") is True


class TestOrchestratorSnapshots:
    def test_loop_snapshots_system_repo(self, tmp_path: Path, monkeypatch):
        override_cache_root(tmp_path / ".vasp_sop")
        root = tmp_path / "p"
        _make_system_dir(root / "NaCl")
        (root / "NaCl" / "plan.yaml").write_text(
            "project:\n  formula: NaCl\n  poscar_src: MP mp-1\nparameters:\n  functional: pbesol\n"
        )
        # A second system without a repo — lazy init path
        _make_system_dir(root / "GaN")
        (root / "GaN" / "plan.yaml").write_text(
            "project:\n  formula: GaN\n  poscar_src: MP mp-2\nparameters:\n  functional: pbesol\n"
        )

        from vasp_sop.core.orchestrator import BatchOrchestrator

        orch = BatchOrchestrator(tmp_path / "p", dry_run=False)
        try:
            n = orch._git_snapshots()
            assert n == 2, n  # both systems baseline-committed
            n2 = orch._git_snapshots()
            assert n2 == 0, n2  # nothing changed -> no new commits
            (root / "NaCl" / "cpd" / "NaCl_mp-1" / "INCAR").write_text(
                "NSW = 50\n"
            )
            assert orch._git_snapshots() == 1
        finally:
            orch.js.close()


def test_gitignore_mentions_all_large_outputs():
    for name in ("OUTCAR", "vasprun.xml", "OSZICAR", "CHG", "CHGCAR",
                 "WAVECAR", "DOSCAR", "EIGENVAL", "PROCAR", "LOGCAR"):
        assert name in GITIGNORE, name
