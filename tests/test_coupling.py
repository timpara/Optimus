"""Tests for the market-coupling relaxation algorithm."""

from __future__ import annotations


def test_equal_raw_prices_no_congestion() -> None:
    """If every zone starts at the same price, nothing should be congested."""
    import main

    state = main.initialize_game_state()
    for zone_id in main.ZONES:
        state.raw_prices[zone_id] = 50.0

    main.apply_market_coupling(state)

    for ic in state.interconnectors.values():
        assert ic.is_congested is False
        assert abs(ic.flow_mw) < 1.0  # essentially zero


def test_large_spread_triggers_congestion() -> None:
    """
    A big price gap between DE and DK should saturate the DE-DK line.
    DK cheap (10), DE expensive (100) → power flows DE←DK at max capacity.
    """
    import main

    state = main.initialize_game_state()
    # Start every zone neutral, then shock DE up and DK down.
    for zone_id in main.ZONES:
        state.raw_prices[zone_id] = 50.0
    state.raw_prices["DE"] = 100.0
    state.raw_prices["DK"] = 10.0

    main.apply_market_coupling(state)

    de_dk = state.interconnectors["DE-DK"]
    assert de_dk.is_congested is True
    assert abs(de_dk.flow_mw) >= de_dk.max_capacity_mw * 0.95


def test_prices_floor_at_minus_fifty() -> None:
    """The coupling stage must floor prices at -50 EUR/MWh."""
    import main

    state = main.initialize_game_state()
    for zone_id in main.ZONES:
        state.raw_prices[zone_id] = -500.0

    main.apply_market_coupling(state)

    for price in state.prices.values():
        assert price >= -50.0
