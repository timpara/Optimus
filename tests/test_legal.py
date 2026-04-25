"""Tests for the public legal pages (Impressum + Datenschutzerklärung).

Both pages must be reachable without authentication so that visitors can read
them before deciding to log in.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client() -> TestClient:
    import main

    return TestClient(main.app)


def test_impressum_is_public() -> None:
    res = _client().get("/impressum")
    assert res.status_code == 200
    assert "Impressum" in res.text
    # Must include the § 5 DDG marker so we don't accidentally serve a stub.
    assert "§ 5 DDG" in res.text


def test_datenschutz_is_public() -> None:
    res = _client().get("/datenschutz")
    assert res.status_code == 200
    assert "Datenschutz" in res.text
    # Aufsichtsbehörde must be named (LDI NRW).
    assert "Nordrhein-Westfalen" in res.text


def test_legal_pages_link_back_to_login() -> None:
    res = _client().get("/impressum")
    assert "Zur Anmeldung" in res.text


def test_disclaimer_present() -> None:
    """The login HTML must carry the educational-purpose disclaimer."""
    res = _client().get("/")
    assert res.status_code == 200
    assert "educational simulation" in res.text.lower()
    assert "/impressum" in res.text
    assert "/datenschutz" in res.text
