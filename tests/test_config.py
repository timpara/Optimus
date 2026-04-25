"""Configuration / fail-fast tests."""

from __future__ import annotations

import importlib
import sys

import pytest


def _reload_main() -> None:
    """Force a fresh import of main.py so env vars are re-evaluated."""
    if "main" in sys.modules:
        del sys.modules["main"]
    import main  # noqa: F401


def test_main_refuses_to_boot_without_class_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRIDPLAY_CLASS_PASSWORD", raising=False)
    monkeypatch.setenv("GRIDPLAY_ADMIN_KEY", "x")
    with pytest.raises(RuntimeError, match="GRIDPLAY_CLASS_PASSWORD"):
        _reload_main()


def test_main_refuses_to_boot_without_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRIDPLAY_CLASS_PASSWORD", "x")
    monkeypatch.delenv("GRIDPLAY_ADMIN_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GRIDPLAY_ADMIN_KEY"):
        _reload_main()


def test_main_refuses_empty_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIDPLAY_CLASS_PASSWORD", "   ")
    monkeypatch.setenv("GRIDPLAY_ADMIN_KEY", "ok")
    with pytest.raises(RuntimeError):
        _reload_main()


def test_main_boots_with_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIDPLAY_CLASS_PASSWORD", "class-pw")
    monkeypatch.setenv("GRIDPLAY_ADMIN_KEY", "admin-key")
    _reload_main()
    import main

    assert main.CLASS_PASSWORD == "class-pw"
    assert main.ADMIN_KEY == "admin-key"


@pytest.fixture(autouse=True)
def _restore_main_after_each_test(monkeypatch: pytest.MonkeyPatch):
    """Ensure subsequent tests get a clean main import with the default fixture env."""
    yield
    # Restore the fixture-provided env and reload cleanly for downstream tests.
    monkeypatch.setenv("GRIDPLAY_CLASS_PASSWORD", "test-class-pw")
    monkeypatch.setenv("GRIDPLAY_ADMIN_KEY", "test-admin-key")
    if "main" in sys.modules:
        del sys.modules["main"]
    importlib.invalidate_caches()
