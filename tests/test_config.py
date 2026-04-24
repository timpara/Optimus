"""Configuration / fail-fast tests."""

from __future__ import annotations

import importlib
import sys

import pytest


def _reload_main() -> None:
    """Force a fresh import of main.py so env vars are re-evaluated.

    We must also purge ``optimus.config`` (and any child modules) from
    ``sys.modules`` because ``main`` imports it at module load time and the
    fail-fast secret validation lives in ``optimus.config``.  Without this
    eviction, once any earlier test has imported ``main``, re-importing it
    here just re-uses the cached ``optimus.config`` module and silently
    skips the validation we're trying to assert on.
    """
    for mod in ("main", "optimus.config", "optimus"):
        sys.modules.pop(mod, None)
    import main  # noqa: F401


def test_main_refuses_to_boot_without_class_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPTIMUS_CLASS_PASSWORD", raising=False)
    monkeypatch.setenv("OPTIMUS_ADMIN_KEY", "x")
    with pytest.raises(RuntimeError, match="OPTIMUS_CLASS_PASSWORD"):
        _reload_main()


def test_main_refuses_to_boot_without_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPTIMUS_CLASS_PASSWORD", "x")
    monkeypatch.delenv("OPTIMUS_ADMIN_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPTIMUS_ADMIN_KEY"):
        _reload_main()


def test_main_refuses_empty_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIMUS_CLASS_PASSWORD", "   ")
    monkeypatch.setenv("OPTIMUS_ADMIN_KEY", "ok")
    with pytest.raises(RuntimeError):
        _reload_main()


def test_main_boots_with_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIMUS_CLASS_PASSWORD", "class-pw")
    monkeypatch.setenv("OPTIMUS_ADMIN_KEY", "admin-key")
    _reload_main()
    import main

    assert main.CLASS_PASSWORD == "class-pw"
    assert main.ADMIN_KEY == "admin-key"


@pytest.fixture(autouse=True)
def _restore_main_after_each_test(monkeypatch: pytest.MonkeyPatch):
    """Ensure subsequent tests get a clean main import with the default fixture env."""
    yield
    # Restore the fixture-provided env and reload cleanly for downstream tests.
    monkeypatch.setenv("OPTIMUS_CLASS_PASSWORD", "test-class-pw")
    monkeypatch.setenv("OPTIMUS_ADMIN_KEY", "test-admin-key")
    # Purge both `main` and `optimus.config` — see _reload_main for rationale.
    for mod in ("main", "optimus.config", "optimus"):
        sys.modules.pop(mod, None)
    importlib.invalidate_caches()
