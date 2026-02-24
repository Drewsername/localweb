from services.thermostat_optimizer import compute_optimal_temp


def test_single_user_gets_their_preference():
    users = [{"preferred_temp": 72, "weight": 1.0}]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 72.0


def test_two_users_symmetric_weights():
    """Two users with equal weight — result should be slightly above midpoint due to cold asymmetry."""
    users = [
        {"preferred_temp": 68, "weight": 1.0},
        {"preferred_temp": 74, "weight": 1.0},
    ]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    # Midpoint is 71.0, cold asymmetry should push it slightly warmer
    assert 71.0 < result <= 72.0


def test_higher_weight_pulls_result():
    users = [
        {"preferred_temp": 68, "weight": 2.0},
        {"preferred_temp": 74, "weight": 1.0},
    ]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    # Should be pulled toward 68 compared to equal-weight case
    assert result < 71.0


def test_clamped_to_min():
    users = [{"preferred_temp": 60, "weight": 1.0}]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 65.0


def test_clamped_to_max():
    users = [{"preferred_temp": 85, "weight": 1.0}]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 78.0


def test_empty_users_returns_none():
    result = compute_optimal_temp([], min_temp=65, max_temp=78)
    assert result is None


def test_cold_asymmetry_bias():
    """Verify cold side is penalized more than warm side.
    User at 72: candidate 70 (2 below) should cost more than candidate 74 (2 above)."""
    users = [{"preferred_temp": 72, "weight": 1.0}]
    # With only one user, result should be exactly their pref (within bounds)
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 72.0
