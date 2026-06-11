"""Smoke test: verify that the package can be imported."""


def test_import_vasp_sop():
    import vasp_sop
    assert vasp_sop.__version__ == "0.1.0"


def test_import_core():
    import vasp_sop.core


def test_import_defect():
    import vasp_sop.defect


def test_import_cli():
    import vasp_sop.cli
