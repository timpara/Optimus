"""Regression tests for bugs fixed in the April 2026 audit.

Each test cites the specific bug it guards against so future refactors know
why the behaviour matters.
"""

from __future__ import annotations

import asyncio
import fnmatch
import math
from pathlib import Path

import pytest
from fastapi import HTTPException

# ────────────────────────────────────────────────────────────────────────────
# Bug 1 (CRITICAL): Dockerfile must copy the `optimus/` package into the
# builder stage. pyproject.toml declares packages = ["optimus"], so without
# the source tree present `pip install .` produces a venv missing the
# `optimus.config` / `optimus.constants` modules that main.py imports at
# startup — the container builds but crashes on boot.
# ────────────────────────────────────────────────────────────────────────────


def test_dockerfile_copies_optimus_package() -> None:
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    assert "COPY optimus" in text, (
        "Dockerfile does not copy the `optimus` package — `pip install .` "
        "will succeed but the resulting venv will be missing optimus.config "
        "and optimus.constants, which main.py imports at startup."
    )


# ────────────────────────────────────────────────────────────────────────────
# Bug 2 (CRITICAL): .dockerignore was silently excluding README.md and
# LICENSE, both of which the Dockerfile COPYs into the builder stage
# because pyproject.toml references them via `readme` and
# `license = { file = "LICENSE" }` metadata.  `docker build` was therefore
# broken end-to-end.
# ────────────────────────────────────────────────────────────────────────────


def _dockerignore_excludes(rules: list[tuple[str, bool]], name: str) -> bool:
    """Approximate Docker's .dockerignore semantics with ``fnmatch``.

    Docker matches both the full path and each parent prefix against each
    pattern (with trailing ``/`` treated as a directory-only match).  We
    emulate that well enough for the paths checked in these tests, but this
    helper does **not** implement Docker's ``**/`` recursive glob or the
    difference between rooted (``/foo``) and floating (``foo``) patterns.
    Don't reuse it outside this module without first extending it.
    """
    excluded = False
    parts = name.split("/")
    candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    for pattern, negated in rules:
        # Strip trailing slash — dockerignore treats `foo/` as matching the
        # directory `foo` (and therefore everything beneath it).
        pat = pattern.rstrip("/")
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, pat) or fnmatch.fnmatch(Path(candidate).name, pat):
                excluded = not negated
                break
    return excluded


def _load_dockerignore_rules() -> list[tuple[str, bool]]:
    root = Path(__file__).resolve().parent.parent
    rules: list[tuple[str, bool]] = []
    for raw in (root / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        rules.append((line[1:] if negated else line, negated))
    return rules


def test_dockerignore_does_not_exclude_build_inputs() -> None:
    root = Path(__file__).resolve().parent.parent
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    rules = _load_dockerignore_rules()

    for required in ("README.md", "LICENSE", "pyproject.toml", "main.py"):
        if required in dockerfile:
            assert not _dockerignore_excludes(rules, required), (
                f".dockerignore excludes {required!r}, but the Dockerfile "
                "COPYs it — `docker build` will fail with 'file not found'."
            )


def test_dockerignore_still_excludes_noisy_paths() -> None:
    """Sanity check that the .dockerignore relaxation didn't go too far:
    caches, venvs, and DB files must still be kept out of the build context.
    """
    rules = _load_dockerignore_rules()
    for should_exclude in (
        ".venv/foo",
        "__pycache__/bar",
        ".ruff_cache/baz",
        "battery_trader.db",
        ".git/HEAD",
    ):
        assert _dockerignore_excludes(rules, should_exclude), (
            f"{should_exclude!r} should be excluded by .dockerignore"
        )


# ────────────────────────────────────────────────────────────────────────────
# Bug 3 (HIGH): /admin/trigger_event bypassed the MAX_ACTIVE_EVENTS cap that
# the natural event spawner respects, letting an admin push the event list
# to arbitrary size — which breaks the UI ticker and num_active_events
# accounting elsewhere.
# ────────────────────────────────────────────────────────────────────────────


class _FakeRequest:
    """Minimal stand-in for ``fastapi.Request`` — only ``.json()`` is used."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


@pytest.fixture
def _isolated_game_state():
    """Snapshot-and-restore the module-level ``main.game_state`` around a test.

    Admin-endpoint tests need to mutate ``active_events`` / ``event_log`` to
    exercise the cap.  Doing that on the real global is fine for a serial
    pytest run but would race under ``pytest-xdist``.  The fixture replaces
    ``main.game_state`` with a fresh ``GameState`` instance for the duration
    of the test, then restores the original — keeping other tests
    oblivious.  Works today under the default serial runner and is
    compatible with future parallelism (each worker gets its own module).
    """
    import main

    original = main.game_state
    main.game_state = main.initialize_game_state()
    try:
        yield main
    finally:
        main.game_state = original


def test_admin_trigger_event_enforces_active_events_cap(_isolated_game_state) -> None:
    """Calling the admin handler while at the cap must raise HTTP 409."""
    main = _isolated_game_state
    main.game_state.active_events = [
        main.GameEvent(
            event_type="plant_outage",
            headline=f"existing-{i}",
            affected_zones=["DE"],
            ticks_remaining=10,
        )
        for i in range(main.MAX_ACTIVE_EVENTS)
    ]

    req = _FakeRequest({"key": "test-admin-key", "event_type": "plant_outage", "zone": "DE"})
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.admin_trigger_event(req))  # type: ignore[arg-type]
    assert exc_info.value.status_code == 409
    assert "max" in str(exc_info.value.detail).lower()


def test_admin_trigger_event_allows_when_below_cap(_isolated_game_state) -> None:
    """With no active events, the admin trigger must still succeed —
    confirming the new cap check doesn't regress the happy path."""
    main = _isolated_game_state
    main.game_state.active_events = []

    req = _FakeRequest({"key": "test-admin-key", "event_type": "plant_outage", "zone": "DE"})
    result = asyncio.run(main.admin_trigger_event(req))  # type: ignore[arg-type]
    assert result["status"] == "Event triggered"
    assert result["event_type"] == "plant_outage"
    assert len(main.game_state.active_events) == 1


def test_admin_trigger_event_rejects_wrong_key(_isolated_game_state) -> None:
    """Auth check must still fire before the new cap check — otherwise the
    409 would leak the cap's existence to unauthenticated callers."""
    main = _isolated_game_state
    req = _FakeRequest({"key": "wrong", "event_type": "plant_outage", "zone": "DE"})
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.admin_trigger_event(req))  # type: ignore[arg-type]
    assert exc_info.value.status_code == 403


# ────────────────────────────────────────────────────────────────────────────
# Bug 4 (HIGH): DA-bid websocket handler accepted arbitrary prices (incl.
# NaN/inf) and silently clamped volume.  New validate_da_bid() helper adds
# range checks and rejects non-finite numbers.
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _main_module():
    import main

    return main


def test_validate_da_bid_accepts_good_input(_main_module) -> None:
    ok, payload = _main_module.validate_da_bid("buy", 100, 42.0)
    assert ok is True
    action, mw, price = payload
    assert action == "BUY"
    assert mw == 100.0
    assert price == 42.0


def test_validate_da_bid_normalises_sell(_main_module) -> None:
    ok, payload = _main_module.validate_da_bid("sell", 50.5, -10.0)
    assert ok is True
    assert payload[0] == "SELL"


def test_validate_da_bid_clamps_mw_to_battery_max(_main_module) -> None:
    huge = _main_module.BATTERY_MAX_MW * 10
    ok, payload = _main_module.validate_da_bid("BUY", huge, 50.0)
    assert ok is True
    _, mw, _ = payload
    assert mw == float(_main_module.BATTERY_MAX_MW)


@pytest.mark.parametrize(
    "action,mw,price,hint",
    [
        ("HOLD", 100, 50.0, "BUY or SELL"),
        ("BUY", 0, 50.0, "positive"),
        ("BUY", -5, 50.0, "positive"),
        ("BUY", float("nan"), 50.0, "finite"),
        ("BUY", float("inf"), 50.0, "finite"),
        ("BUY", 100, float("nan"), "finite"),
        ("BUY", 100, float("inf"), "finite"),
        ("BUY", 100, -1000.0, "between"),  # below DA_BID_PRICE_MIN
        ("BUY", 100, 20_000.0, "between"),  # above DA_BID_PRICE_MAX
        ("BUY", "100", 50.0, "number"),
        ("BUY", 100, "cheap", "number"),
        (123, 100, 50.0, "string"),
    ],
)
def test_validate_da_bid_rejects_bad_input(_main_module, action, mw, price, hint) -> None:
    ok, err = _main_module.validate_da_bid(action, mw, price)
    assert ok is False, (
        f"Expected rejection for ({action!r}, {mw!r}, {price!r}); got ok=True payload={err!r}"
    )
    assert isinstance(err, str)
    assert hint in err, f"Expected error to mention {hint!r}; got {err!r}"


def test_validate_da_bid_rejects_booleans_as_numbers(_main_module) -> None:
    """`isinstance(True, (int, float))` is True in Python, but booleans
    should not be accepted as DA bid prices / volumes — they nearly always
    indicate a client serialisation bug."""
    assert _main_module.validate_da_bid("BUY", True, 50.0)[0] is False
    assert _main_module.validate_da_bid("BUY", 100, False)[0] is False


def test_validate_da_bid_rejects_price_just_above_max(_main_module) -> None:
    """Pin the upper bound precisely — if someone raises DA_BID_PRICE_MAX
    this test forces them to update the constant-sanity test below too."""
    over = _main_module.DA_BID_PRICE_MAX + 1.0
    ok, err = _main_module.validate_da_bid("BUY", 100, over)
    assert ok is False
    assert "between" in err


def test_da_bid_price_bounds_are_sane(_main_module) -> None:
    """Sanity-check the constants themselves so an accidental swap (min>max)
    or a wildly unrealistic bound gets caught here rather than in
    production traffic.  The upper bound must comfortably exceed the
    largest event-driven price_mod in optimus/constants.py (~25 EUR/MWh on
    top of baseline prices in the 20-80 EUR/MWh range), but still reject
    clearly-broken inputs like 10^6."""
    assert _main_module.DA_BID_PRICE_MIN < 0  # DA prices can go negative
    assert _main_module.DA_BID_PRICE_MAX >= 1000  # must exceed historical peaks
    assert _main_module.DA_BID_PRICE_MAX <= 10_000  # must still catch garbage inputs
    assert _main_module.DA_BID_PRICE_MIN < _main_module.DA_BID_PRICE_MAX
    assert math.isfinite(_main_module.DA_BID_PRICE_MIN)
    assert math.isfinite(_main_module.DA_BID_PRICE_MAX)


# ────────────────────────────────────────────────────────────────────────────
# Bug 5 (MEDIUM): game_loop and websocket exception handlers were swallowing
# stack traces — only the exception's str() was logged, making production
# incidents nearly impossible to diagnose.  They must call
# traceback.print_exc() so the full stack reaches stderr.
# ────────────────────────────────────────────────────────────────────────────


def test_main_imports_traceback() -> None:
    import main

    assert hasattr(main, "traceback"), (
        "main.py must import the stdlib `traceback` module to log full "
        "stack traces from the game loop / websocket exception handlers."
    )


def test_game_loop_logs_full_traceback() -> None:
    """The `except Exception` block around the game loop body must call
    traceback.print_exc() — otherwise production errors lose their stack."""
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    # Crude but effective: find the [GAME LOOP ERROR] line and verify the
    # very next non-blank line (or nearby) calls traceback.print_exc().
    idx = src.index("[GAME LOOP ERROR]")
    # Look at the next ~200 chars for the traceback call.
    window = src[idx : idx + 400]
    assert "traceback.print_exc()" in window, (
        "Game-loop exception handler must call traceback.print_exc() to "
        "preserve the stack for debugging."
    )


def test_websocket_handler_logs_full_traceback() -> None:
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    idx = src.index("[WEBSOCKET ERROR]")
    window = src[idx : idx + 400]
    assert "traceback.print_exc()" in window, (
        "Websocket exception handler must call traceback.print_exc()."
    )


# ────────────────────────────────────────────────────────────────────────────
# Bug 6 (FOLLOW-UP): the websocket error logger originally used ``token[:8]``
# directly, which would raise ``TypeError`` if ``token`` was ever ``None`` —
# a secondary error inside an exception handler would shadow the real one.
# The new ``_safe_token_id`` helper guarantees a string result.
# ────────────────────────────────────────────────────────────────────────────


def test_safe_token_id_handles_none() -> None:
    import main

    assert main._safe_token_id(None) == "<unknown>"


def test_safe_token_id_handles_empty_string() -> None:
    import main

    assert main._safe_token_id("") == "<unknown>"


def test_safe_token_id_handles_short_token() -> None:
    """Slicing a 3-char string with ``[:8]`` is already safe in Python, but
    we want the helper to return the whole token rather than pad or error."""
    import main

    assert main._safe_token_id("abc") == "abc"


def test_safe_token_id_truncates_long_token() -> None:
    import main

    token = "a" * 64
    assert main._safe_token_id(token) == "a" * 8


def test_safe_token_id_respects_custom_length() -> None:
    import main

    assert main._safe_token_id("abcdefghij", length=4) == "abcd"
