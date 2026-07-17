"""Test that keri_serviceaid is packaged in setup.py."""
import pathlib


def test_setup_py_ships_keri_serviceaid():
    """Verify that setup.py includes keri_serviceaid in packages."""
    setup = pathlib.Path(__file__).resolve().parents[2] / "setup.py"
    text = setup.read_text()
    assert "keri_serviceaid" in text, "setup.py must package keri_serviceaid"


def test_keri_serviceaid_is_importable():
    import keri_serviceaid
    assert keri_serviceaid.__name__ == "keri_serviceaid"
