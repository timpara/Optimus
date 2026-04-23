"""Tests for the optimus.config env-backed settings."""

from __future__ import annotations

import importlib
import sys

import pytest


def _reload_config():
    sys.modules.pop("optimus.config", None)
    return importlib.import_module("optimus.config")


def test_require_env_accepts_nonempty(monkeypatch: pytest.MonkeyPatch) -> None:
    from optimus.config import require_env

    monkeypatch.setenv("OPTIMUS_TEST_VAR_X", "hello")
    assert require_env("OPTIMUS_TEST_VAR_X") == "hello"


def test_require_env_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    from optimus.config import require_env

    monkeypatch.setenv("OPTIMUS_TEST_VAR_Y", "   value   ")
    assert require_env("OPTIMUS_TEST_VAR_Y") == "value"


def test_require_env_rejects_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from optimus.config import require_env

    monkeypatch.delenv("OPTIMUS_TEST_VAR_MISSING", raising=False)
    with pytest.raises(RuntimeError, match="OPTIMUS_TEST_VAR_MISSING"):
        require_env("OPTIMUS_TEST_VAR_MISSING")


def test_require_env_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from optimus.config import require_env

    monkeypatch.setenv("OPTIMUS_TEST_VAR_EMPTY", "   ")
    with pytest.raises(RuntimeError):
        require_env("OPTIMUS_TEST_VAR_EMPTY")


def test_battery_physics_constants_consistent() -> None:
    from optimus import config

    # Round-trip efficiency must equal charge * discharge efficiency within
    # floating-point tolerance — they're derived as sqrt(0.9) per leg.
    product = config.BATTERY_CHARGE_EFF * config.BATTERY_DISCHARGE_EFF
    assert abs(product - config.BATTERY_ROUND_TRIP_EFF) < 1e-9


def test_battery_capacity_numbers_are_positive() -> None:
    from optimus import config

    assert config.BATTERY_MAX_MWH > 0
    assert config.BATTERY_MAX_MW > 0
    assert 0 < config.BATTERY_START_MWH <= config.BATTERY_MAX_MWH
    assert 0 < config.BATTERY_EOL_SOH < config.BATTERY_START_SOH
