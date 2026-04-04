"""Pool assignment solver.

Two phases:
1. Construction — snake seeding + Hungarian optimal assignment within each rating window.
2. Improvement  — hill-climbing pair swaps minimising the weighted score.
"""

import logging
from collections import Counter
from itertools import combinations

import numpy as np
from scipy.optimize import linear_sum_assignment

from pool_alch_agent.models import (
    Assignment,
    PoolConfig,
    PoolFencer,
    Score,
    Weights,
)

log = logging.getLogger(__name__)


# ── Scoring ────────────────────────────────────────────────────────────────────

def score(assignment: Assignment, weights: Weights, config: PoolConfig) -> Score:
    # Snake deviation: preferred pool for each fencer given snake order by seed
    all_fencers = [(f, pool_idx) for pool_idx, pool in enumerate(assignment) for f in pool]
    sorted_by_seed = sorted(all_fencers, key=lambda x: x[0].seed)
    n = config.num_pools
    snake_dev = 0.0
    for snake_pos, (fencer, actual_pool) in enumerate(sorted_by_seed):
        window = snake_pos // n
        pos_in_window = snake_pos % n
        preferred_pool = pos_in_window if window % 2 == 0 else (n - 1 - pos_in_window)
        snake_dev += abs(actual_pool - preferred_pool)
    snake_score = weights.snake_deviation * snake_dev

    # Club: count same-club pairs per pool
    club_score = 0.0
    for pool in assignment:
        clubs = [f.club for f in pool if f.club]
        for count in Counter(clubs).values():
            if count > 1:
                club_score += weights.club * (count * (count - 1) / 2)

    # Nationality: std dev of foreign fencer counts across pools
    foreign_counts = [sum(1 for f in pool if f.nationality) for pool in assignment]
    nat_score = weights.nationality * float(np.std(foreign_counts)) if foreign_counts else 0.0

    # Wave: dual-discipline fencers not in wave 1
    wave_score = 0.0
    for pool_idx, pool in enumerate(assignment):
        if config.wave_of_pool(pool_idx) != 0:
            wave_score += weights.wave * sum(1 for f in pool if f.other_disciplines)

    return Score(
        snake_deviation=snake_score,
        club=club_score,
        nationality=nat_score,
        wave=wave_score,
    )


# ── Construction ───────────────────────────────────────────────────────────────

def _build_window_cost(
    window_fencers: list[PoolFencer],
    pools: list[list[PoolFencer]],
    n: int,
    window_idx: int,
    weights: Weights,
    config: PoolConfig,
) -> np.ndarray:
    """Build cost matrix for assigning window_fencers (rows) to pools (cols)."""
    m = len(window_fencers)
    cost = np.zeros((m, n), dtype=float)

    for i, fencer in enumerate(window_fencers):
        pos_in_window = i
        preferred_pool = pos_in_window if window_idx % 2 == 0 else (n - 1 - pos_in_window)

        for j in range(n):
            # Snake deviation
            cost[i, j] += weights.snake_deviation * abs(j - preferred_pool)

            # Club penalty
            if fencer.club:
                same_club = sum(1 for f in pools[j] if f.club == fencer.club)
                cost[i, j] += weights.club * same_club

            # Nationality penalty
            if fencer.nationality:
                foreign_in_j = sum(1 for f in pools[j] if f.nationality)
                cost[i, j] += weights.nationality * foreign_in_j

            # Wave penalty: dual-discipline fencer assigned to non-wave-1 pool
            if fencer.other_disciplines and config.wave_of_pool(j) != 0:
                cost[i, j] += weights.wave

    return cost


def construct(
    fencers: list[PoolFencer],
    config: PoolConfig,
    weights: Weights,
) -> Assignment:
    """Build initial assignment using Hungarian optimal assignment within each snake window."""
    n = config.num_pools
    sorted_fencers = sorted(fencers, key=lambda f: f.seed)
    pools: list[list[PoolFencer]] = [[] for _ in range(n)]
    windows = [sorted_fencers[i:i + n] for i in range(0, len(sorted_fencers), n)]

    for window_idx, window in enumerate(windows):
        if not window:
            continue
        cost = _build_window_cost(window, pools, n, window_idx, weights, config)
        row_ind, col_ind = linear_sum_assignment(cost)
        for r, c in zip(row_ind, col_ind):
            pools[c].append(window[r])

    return pools


# ── Improvement ────────────────────────────────────────────────────────────────

def _swap(assignment: Assignment, pool_a: int, idx_a: int, pool_b: int, idx_b: int) -> None:
    assignment[pool_a][idx_a], assignment[pool_b][idx_b] = (
        assignment[pool_b][idx_b],
        assignment[pool_a][idx_a],
    )


def improve(
    assignment: Assignment,
    weights: Weights,
    config: PoolConfig,
    max_iterations: int = 10_000,
) -> tuple[Assignment, Score]:
    """Hill-climbing: repeatedly apply the best-improving pair swap until local minimum."""
    import copy
    current = copy.deepcopy(assignment)
    current_score = score(current, weights, config)

    for iteration in range(max_iterations):
        best_delta = 0.0
        best_swap: tuple[int, int, int, int] | None = None

        for pa, pb in combinations(range(len(current)), 2):
            for ia, fa in enumerate(current[pa]):
                for ib, fb in enumerate(current[pb]):
                    _swap(current, pa, ia, pb, ib)
                    new_score = score(current, weights, config)
                    delta = current_score.total - new_score.total
                    _swap(current, pa, ia, pb, ib)

                    if delta > best_delta:
                        best_delta = delta
                        best_swap = (pa, ia, pb, ib)

        if best_swap is None:
            log.info("Hill-climbing converged after %d iterations", iteration)
            break

        pa, ia, pb, ib = best_swap
        _swap(current, pa, ia, pb, ib)
        current_score = score(current, weights, config)
        log.debug("Iteration %d: swapped pool%d[%d] ↔ pool%d[%d], score=%s",
                  iteration, pa, ia, pb, ib, current_score)
    else:
        log.warning("Hill-climbing reached max_iterations=%d without converging", max_iterations)

    return current, current_score


# ── Public entry point ─────────────────────────────────────────────────────────

def solve(
    fencers: list[PoolFencer],
    config: PoolConfig,
    weights: Weights,
) -> tuple[Assignment, Score]:
    """Construct initial assignment, then improve with hill-climbing."""
    assignment = construct(fencers, config, weights)
    log.info("Construction complete, initial score=%s", score(assignment, weights, config))
    assignment, final_score = improve(assignment, weights, config)
    log.info("Solver done, final score=%s", final_score)
    return assignment, final_score
