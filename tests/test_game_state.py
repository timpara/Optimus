"""Tests for game-state initialisation and deterministic structure."""

from __future__ import annotations


def test_initial_state_has_all_zones() -> None:
    import main

    state = main.initialize_game_state()

    assert set(state.prices) == set(main.ZONES)
    assert set(state.weather) == set(main.ZONES)
    assert set(state.price_history) == set(main.ZONES)

    # Every zone's starting price equals its configured base price.
    for zone_id, cfg in main.ZONES.items():
        assert state.prices[zone_id] == cfg["base_price"]

    # Every interconnector exists and starts uncongested with zero flow.
    for ic_id in main.INTERCONNECTORS:
        ic_state = state.interconnectors[ic_id]
        assert ic_state.flow_mw == 0.0
        assert ic_state.is_congested is False


def test_initial_game_clock() -> None:
    import main

    state = main.initialize_game_state()
    assert state.tick == 0
    assert state.day == 1
    assert state.running is True
    assert state.paused is False
