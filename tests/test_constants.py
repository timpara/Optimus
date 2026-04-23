"""Tests for the optimus.constants data tables."""

from __future__ import annotations

from optimus import constants


def test_zones_complete() -> None:
    required_keys = {
        "name",
        "base_price",
        "wind_sensitivity",
        "solar_sensitivity",
        "demand_elasticity",
        "wind_volatility",
        "wind_mean",
        "peak_demand_gw",
        "latitude",
    }
    assert set(constants.ZONES) == {"DE", "FR", "NL", "DK", "PL"}
    for cfg in constants.ZONES.values():
        assert required_keys <= set(cfg)
        assert cfg["base_price"] > 0


def test_interconnectors_reference_valid_zones() -> None:
    zones = set(constants.ZONES)
    for ic in constants.INTERCONNECTORS.values():
        a, b = ic["zones"]
        assert a in zones and b in zones
        assert ic["max_capacity_mw"] > 0
        assert ic["flow_sensitivity"] > 0


def test_duck_curve_24_hours() -> None:
    assert len(constants.DUCK_CURVE) == 24
    for v in constants.DUCK_CURVE:
        assert 0.0 < v <= 1.0
    # Evening peak (hour 18) should be the global maximum.
    assert constants.DUCK_CURVE[18] == max(constants.DUCK_CURVE)


def test_event_templates_wellformed() -> None:
    required_keys = {"type", "headlines", "price_mod", "duration_range", "probability"}
    for tmpl in constants.EVENT_TEMPLATES:
        assert required_keys <= set(tmpl)
        assert len(tmpl["headlines"]) >= 1
        lo, hi = tmpl["duration_range"]
        assert 0 < lo <= hi
        assert 0 < tmpl["probability"] < 1


def test_carbon_intensity_covers_all_zones() -> None:
    assert set(constants.CARBON_INTENSITY) == set(constants.ZONES)
    for v in constants.CARBON_INTENSITY.values():
        assert 0 <= v <= 1


def test_forecast_scale_covers_all_zones() -> None:
    assert set(constants.FORECAST_NOISE_SCALE) == set(constants.ZONES)
    assert constants.FORECAST_HORIZON >= 1
    assert constants.SURPRISE_AMPLIFIER > 1.0  # must amplify, not dampen
