"""Shared pytest fixtures for the gridplay test suite."""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure required environment variables are set before importing main."""
    monkeypatch.setenv("GRIDPLAY_CLASS_PASSWORD", "test-class-pw")
    monkeypatch.setenv("GRIDPLAY_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("GRIDPLAY_DB_PATH", ":memory:")


@pytest.fixture(autouse=True)
def _deterministic_random() -> Iterator[None]:
    """Seed the global random module for deterministic tests."""
    state = random.getstate()
    random.seed(42)
    yield
    random.setstate(state)


@pytest.fixture
def env_overrides(monkeypatch: pytest.MonkeyPatch):
    """Helper to override environment variables in a test."""

    def _apply(**overrides: str) -> None:
        for key, value in overrides.items():
            monkeypatch.setenv(key, value)

    return _apply
