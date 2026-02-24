"""Weighted asymmetric quadratic discomfort minimizer for multi-occupant thermostat."""

COLD_SENSITIVITY = 1.5  # Being too cold hurts 1.5x more than being too warm
WARM_SENSITIVITY = 1.0
STEP = 0.5  # Sweep in 0.5F increments


def compute_optimal_temp(users, min_temp=65, max_temp=78):
    """Compute the optimal temperature for all present users.

    Args:
        users: list of dicts with 'preferred_temp' (float) and 'weight' (float)
        min_temp: absolute minimum allowed temperature
        max_temp: absolute maximum allowed temperature

    Returns:
        Optimal temperature as float, or None if no users provided.
    """
    if not users:
        return None

    best_temp = None
    best_cost = float("inf")

    # Sweep candidate temps from min to max in STEP increments
    candidate = min_temp
    while candidate <= max_temp + 0.01:  # small epsilon for float comparison
        total_cost = 0.0
        for u in users:
            pref = u["preferred_temp"]
            w = u["weight"]
            diff = candidate - pref
            if diff < 0:
                # Too cold
                total_cost += w * COLD_SENSITIVITY * diff * diff
            else:
                # Too warm (or exact)
                total_cost += w * WARM_SENSITIVITY * diff * diff
        if total_cost < best_cost:
            best_cost = total_cost
            best_temp = candidate
        candidate += STEP

    return round(best_temp, 1) if best_temp is not None else None
