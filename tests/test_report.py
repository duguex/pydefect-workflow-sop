from pathlib import Path

import yaml


def _write_poscar(path: Path, formula: str = "CsEuCl3") -> None:
    path.write_text(
        f"{formula}\n1.0\n10 0 0\n0 10 0\n0 0 10\nCs Eu Cl\n1 1 3\nDirect\n"
        "0 0 0\n0.5 0.5 0.5\n0.25 0.25 0.25\n0.25 0.25 0.5\n0.25 0.5 0.25\n"
    )


def _write_converged_outcar(path: Path) -> None:
    path.write_text(
        " NSW = 50\n IBRION = 2\n EDIFFG = -0.005\n"
        " reached required accuracy - stopping structural energy minimisation\n"
    )


def _make_report_system(tmp_path: Path) -> Path:
    root = tmp_path / "CsEuCl3"
    target = root / "cpd" / "CsEuCl3_mp-1213256"
    competitor = root / "cpd" / "CsCl_mp-22865"
    target.mkdir(parents=True)
    competitor.mkdir()
    (root / "unitcell" / "structure_opt").mkdir(parents=True)
    (root / "plan.yaml").write_text(
        yaml.safe_dump(
            {
                "project": {
                    "formula": "CsEuCl3",
                    "poscar_src": "MP mp-1213256",
                },
                "parameters": {
                    "functional": "pbesol",
                    "hubbard_u": True,
                    "pp": ["Cl", "Cs_sv", "Eu"],
                },
                "corrections": {"Cl2": 1.228},
                "correction_policy": "custom_molecular_reference",
            }
        )
    )
    _write_poscar(target / "POSCAR")
    _write_poscar(root / "unitcell" / "structure_opt" / "CONTCAR")
    _write_converged_outcar(root / "unitcell" / "structure_opt" / "OUTCAR")
    (root / "unitcell" / "structure_opt" / "INCAR").write_text(
        "ISPIN = 2\nLDAU = True\nLDAUL = -1 3 -1\nLDAUU = 0 5 0\n"
    )
    (root / "unitcell" / "structure_opt" / "POTCAR").write_text(
        "TITEL = PAW_PBE Cs_sv\nTITEL = PAW_PBE Eu\nTITEL = PAW_PBE Cl\n"
    )
    _write_converged_outcar(target / "OUTCAR")
    (target / "vasprun.xml").write_text("<vasprun />\n")
    (target / "INCAR").write_text(
        "ISPIN = 2\nLDAU = True\nLDAUL = -1 3 -1\nLDAUU = 0 5 0\n"
    )
    (target / "POTCAR").write_text(
        "TITEL = PAW_PBE Cs_sv\nTITEL = PAW_PBE Eu\nTITEL = PAW_PBE Cl\n"
    )
    _write_poscar(competitor / "POSCAR", "CsCl")
    (competitor / "OUTCAR").write_text("General timing and accounting\n")
    (root / "cpd" / "composition_energies.yaml").write_text(
        yaml.safe_dump(
            {
                "Cs4Eu4Cl12": {
                    "energy": -117.0,
                    "source": "CsEuCl3_mp-1213256",
                },
                "Cs1Cl1": {"energy": -6.0, "source": "CsCl_mp-22865"},
            }
        )
    )
    (root / "cpd" / "target_vertices.yaml").write_text(
        yaml.safe_dump(
            {
                "target": "CsEuCl3",
                "A": {"chem_pot": {"Cs": -1.0, "Eu": -2.0, "Cl": -3.0}},
            }
        )
    )
    return root


def test_generate_report_reads_current_files(tmp_path: Path):
    root = _make_report_system(tmp_path)
    (root / "calculation_report.md").write_text("STALE MANUAL REPORT\n")

    from vasp_sop.core.report import generate_report

    output = generate_report(root)
    text = output.read_text()

    assert output == root / "calculation_report.md"
    assert "STALE MANUAL REPORT" not in text
    assert "CsEuCl3" in text
    assert "Cs4Eu4Cl12" in text
    assert "CsEuCl3_mp-1213256" in text
    assert "completed/converged" in text
    assert "MAGMOM" in text
    assert "Cs_sv, Eu, Cl" in text
    assert "8 个" not in text
    assert "CPD 竞争相目录：1 个" in text
    assert "能量推导链" in text
    assert "OUTCAR TOTEN 重解析" in text
    assert "composition_energies.yaml" in text
    assert "证据状态" in text
    assert "KPOINTS" in text
    assert "Cl2" in text
    assert "未找到（未应用）" in text
    assert "Cs4Eu4Cl12 = -117.0 eV; source=CsEuCl3_mp-1213256" in text
    assert "目标能量：`Cs4Eu4Cl12` = `-117.0` eV, source=`CsEuCl3_mp-1213256`" in text
    assert "不等同于 Materials Project 2020 阴离子校正" in text
    assert "校正策略：`custom_molecular_reference`" in text


def test_report_cli_writes_requested_output(tmp_path: Path, monkeypatch, capsys):
    root = _make_report_system(tmp_path)
    output = tmp_path / "generated.md"
    monkeypatch.setattr(
        "sys.argv",
        ["vasp-sop", "report", str(root), "--output", str(output)],
    )

    from vasp_sop.cli.main import main

    main()

    assert output.is_file()
    assert "Report written to" in capsys.readouterr().out


def test_report_marks_missing_cpd_artifacts(tmp_path: Path):
    root = _make_report_system(tmp_path)
    (root / "cpd" / "composition_energies.yaml").unlink()
    (root / "cpd" / "target_vertices.yaml").unlink()

    from vasp_sop.core.report import generate_report

    text = generate_report(root).read_text()

    assert "composition_energies.yaml 缺失" in text
    assert "target_vertices.yaml 缺失" in text
    assert (
        f"| composition_energies.yaml | — | {root / 'cpd' / 'composition_energies.yaml'} | 未找到 |"
        in text
    )
    assert (
        f"| target_vertices.yaml | — | {root / 'cpd' / 'target_vertices.yaml'} | 未找到 |"
        in text
    )
    assert (
        f"| KPOINTS | — | {root / 'unitcell' / 'structure_opt' / 'KPOINTS'} | 未找到 |"
        in text
    )


def test_report_marks_failed_marker_before_old_converged_outcar(tmp_path: Path):
    root = _make_report_system(tmp_path)
    phase = root / "cpd" / "Cs2EuCl5_mp-1214346"
    phase.mkdir()
    _write_poscar(phase / "POSCAR", "CsEuCl3")
    _write_converged_outcar(phase / "OUTCAR")
    (phase / ".failed").write_text("CRISP_FAILED\nEXIT_CODE: 1\n")

    from vasp_sop.core.report import _phase_status

    assert _phase_status(phase) == "failed/latest attempt"




