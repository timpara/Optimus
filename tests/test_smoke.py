"""Smoke tests that import the main module and verify its public surface."""

from __future__ import annotations


def test_main_module_imports() -> None:
    """The server module should import cleanly with required env vars set."""
    import main

    assert hasattr(main, "app")
    # FastAPI instance exposes `.routes`
    assert hasattr(main.app, "routes")


def test_zones_configured() -> None:
    import main

    # Five European market zones are expected.
    assert set(main.ZONES) == {"DE", "FR", "NL", "DK", "PL"}
    for cfg in main.ZONES.values():
        assert cfg["base_price"] > 0
        assert cfg["peak_demand_gw"] > 0


def test_interconnectors_configured() -> None:
    import main

    assert "DE-FR" in main.INTERCONNECTORS
    assert "DE-DK" in main.INTERCONNECTORS
    for ic in main.INTERCONNECTORS.values():
        assert ic["max_capacity_mw"] > 0


def test_duck_curve_length() -> None:
    import main

    assert len(main.DUCK_CURVE) == 24
    assert all(0 < x <= 1.0 for x in main.DUCK_CURVE)
