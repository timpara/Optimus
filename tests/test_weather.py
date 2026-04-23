"""Property tests for the weather advancement routine."""

from __future__ import annotations


def test_advance_weather_keeps_wind_in_bounds() -> None:
    import main

    state = main.initialize_game_state()

    # Run many ticks and verify wind stays in the physical range.
    for _ in range(500):
        main.advance_weather(state)
        for w in state.weather.values():
            assert 0.5 <= w.wind_speed <= 25.0
            assert 0.0 <= w.solar_irradiance <= 1.0


def test_solar_is_zero_at_night() -> None:
    import main

    state = main.initialize_game_state()

    for hour in [0, 1, 2, 3, 4, 5, 21, 22, 23]:
        state.tick = hour
        main.advance_weather(state)
        for w in state.weather.values():
            assert w.solar_irradiance == 0.0


def test_solar_positive_at_midday() -> None:
    import main

    state = main.initialize_game_state()
    state.tick = 12
    main.advance_weather(state)

    for w in state.weather.values():
        assert w.solar_irradiance > 0.1
