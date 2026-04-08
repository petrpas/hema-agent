"""Pool assignment solver.

Two phases:
1. Construction — snake seeding + Hungarian optimal assignment within each window.
2. Improvement  — hill-climbing pair swaps within tier-based constraints.

Seed tiers (1-based rows):
  Row 1  (tier 0): pool heads — locked, never swapped.
  Row 2  (tier 1): swap only within tier 1, penalty = snake distance.
  Row 3–4 (tier 2): swap within tier 2, penalty = snake distance.
  Row 5+  (tier 3): swap freely, zero snake penalty.
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

# Number of rows (0-based) that form the protected tiers.
# Row 0 = locked, row 1 = tier 1, rows 2-3 = tier 2, rows 4+ = free.
_TIER_BOUNDARIES = (1, 2, 4)  # tier 0: [0,1), tier 1: [1,2), tier 2: [2,4), tier 3: [4,∞)


def _domestic_nationality(fencers: list[PoolFencer]) -> str | None:
    """Return the most frequent nationality (treated as domestic), or None if no nationalities."""
    counts: Counter[str] = Counter(f.nationality for f in fencers if f.nationality)
    return counts.most_common(1)[0][0] if counts else None


def _is_foreign(fencer: PoolFencer, domestic: str | None) -> bool:
    """A fencer is foreign if they have a nationality that differs from the domestic one."""
    return bool(fencer.nationality and fencer.nationality != domestic)


def _seed_tier(seed: int, num_pools: int) -> int:
    """Return tier (0–3) for a fencer based on seed and pool count.

    Tier is determined by which row the fencer occupies in ideal snake order.
    """
    row = (seed - 1) // num_pools  # 0-based row
    if row < _TIER_BOUNDARIES[0]:
        return 0
    if row < _TIER_BOUNDARIES[1]:
        return 1
    if row < _TIER_BOUNDARIES[2]:
        return 2
    return 3


def _snake_pos(pool: int, row: int, num_pools: int) -> int:
    """Return the snake-order position (0-based) for a (pool, row) slot."""
    if row % 2 == 0:
        return row * num_pools + pool
    return row * num_pools + (num_pools - 1 - pool)


# ── Scoring ────────────────────────────────────────────────────────────────────

def score(assignment: Assignment, weights: Weights, config: PoolConfig) -> Score:
    n = config.num_pools

    # Snake deviation: measured as snake distance from preferred position.
    # Only tiers 0–2 contribute; tier 3 (row 5+) has zero snake penalty.
    snake_dev = 0.0
    for pool_idx, pool in enumerate(assignment):
        sorted_pool = sorted(pool, key=lambda f: f.seed)
        for row, fencer in enumerate(sorted_pool):
            tier = _seed_tier(fencer.seed, n)
            if tier == 3:
                continue
            preferred = fencer.seed - 1  # ideal snake position
            actual = _snake_pos(pool_idx, row, n)
            snake_dev += abs(actual - preferred)
    snake_score = weights.snake_deviation * snake_dev

    # Club: count same-club pairs per pool
    club_score = 0.0
    for pool in assignment:
        clubs = [f.club for f in pool if f.club]
        for count in Counter(clubs).values():
            if count > 1:
                club_score += weights.club * (count * (count - 1) / 2)

    # Nationality: penalise uneven distribution of foreign fencers as a whole
    # and each foreign nationality individually, normalised so the total
    # contribution is constant regardless of how many nationalities are present.
    all_fencers = [f for pool in assignment for f in pool]
    domestic = _domestic_nationality(all_fencers)
    foreign_nats = {f.nationality for f in all_fencers if _is_foreign(f, domestic)}
    num_nat_terms = 1 + len(foreign_nats)
    term_weight = weights.nationality / num_nat_terms
    nat_score = 0.0
    # Total foreign distribution
    foreign_counts = [sum(1 for f in pool if _is_foreign(f, domestic)) for pool in assignment]
    nat_score += term_weight * float(np.std(foreign_counts))
    # Per-nationality distribution
    for nat in foreign_nats:
        counts = [sum(1 for f in pool if f.nationality == nat) for pool in assignment]
        nat_score += term_weight * float(np.std(counts))

    # Wave: count dual-discipline fencers in parallel waves (hard constraint — should be 0).
    wave_violations = 0
    for pool_idx, pool in enumerate(assignment):
        if config.is_parallel(config.wave_of_pool(pool_idx)):
            wave_violations += sum(1 for f in pool if f.other_disciplines)
    wave_score = weights.wave * wave_violations

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
    domestic: str | None,
    num_nat_terms: int,
) -> np.ndarray:
    """Build cost matrix for assigning window_fencers (rows) to pools (cols)."""
    m = len(window_fencers)
    cost = np.zeros((m, n), dtype=float)
    # Tiers 0–2 (rows 0–3) get snake penalty; tier 3 (row 4+) gets none.
    use_snake = window_idx < _TIER_BOUNDARIES[2]
    nat_weight = weights.nationality / num_nat_terms if num_nat_terms else 0.0

    for i, fencer in enumerate(window_fencers):
        pos_in_window = i
        preferred_pool = pos_in_window if window_idx % 2 == 0 else (n - 1 - pos_in_window)

        for j in range(n):
            # Snake deviation (flat weight for tiers 0–2, zero for tier 3)
            if use_snake:
                cost[i, j] += weights.snake_deviation * abs(j - preferred_pool)

            # Club penalty
            if fencer.club:
                same_club = sum(1 for f in pools[j] if f.club == fencer.club)
                cost[i, j] += weights.club * same_club

            # Nationality penalty: total foreign + same-nationality clustering
            if _is_foreign(fencer, domestic):
                foreign_in_j = sum(1 for f in pools[j] if _is_foreign(f, domestic))
                cost[i, j] += nat_weight * foreign_in_j
                same_nat_in_j = sum(1 for f in pools[j] if f.nationality == fencer.nationality)
                cost[i, j] += nat_weight * same_nat_in_j

            # Wave: dual-discipline fencer should not be in a parallel wave.
            # Use a large finite penalty (not inf) so the cost matrix stays feasible
            # even when a window has more dual fencers than non-parallel pool slots.
            # Any remaining violations are fixed by _repair_wave_violations().
            if fencer.other_disciplines and config.is_parallel(config.wave_of_pool(j)):
                cost[i, j] += 1e6

    return cost


def _repair_wave_violations(pools: list[list[PoolFencer]], config: PoolConfig) -> None:
    """Swap dual fencers out of parallel-wave pools with non-dual fencers from non-parallel pools.

    This runs after Hungarian construction, which may place dual fencers in parallel pools
    when a window has more dual fencers than non-parallel pool slots. The repair ignores
    tier constraints — it only ensures the hard wave constraint is satisfied.
    """
    n = config.num_pools
    parallel_pools = [j for j in range(n) if config.is_parallel(config.wave_of_pool(j))]
    if not parallel_pools:
        return

    repairs = 0
    for pp in parallel_pools:
        for i, fencer in enumerate(pools[pp]):
            if not fencer.other_disciplines:
                continue
            # Find a non-dual fencer in a non-parallel pool to swap with
            swapped = False
            for np_pool in range(n):
                if config.is_parallel(config.wave_of_pool(np_pool)):
                    continue
                for k, candidate in enumerate(pools[np_pool]):
                    if not candidate.other_disciplines:
                        pools[pp][i], pools[np_pool][k] = pools[np_pool][k], pools[pp][i]
                        swapped = True
                        repairs += 1
                        break
                if swapped:
                    break
            if not swapped:
                log.warning("Cannot repair wave violation for %s (seed %d) — not enough non-dual fencers",
                            fencer.name, fencer.seed)
    if repairs:
        log.info("Wave repair: swapped %d dual fencer(s) out of parallel pool(s)", repairs)


def construct(
    fencers: list[PoolFencer],
    config: PoolConfig,
    weights: Weights,
) -> Assignment:
    """Build initial assignment using Hungarian optimal assignment within each snake window."""
    n = config.num_pools
    sorted_fencers = sorted(fencers, key=lambda f: f.seed)
    domestic = _domestic_nationality(fencers)
    foreign_nats = {f.nationality for f in fencers if _is_foreign(f, domestic)}
    num_nat_terms = 1 + len(foreign_nats)
    pools: list[list[PoolFencer]] = [[] for _ in range(n)]
    windows = [sorted_fencers[i:i + n] for i in range(0, len(sorted_fencers), n)]

    for window_idx, window in enumerate(windows):
        if not window:
            continue
        cost = _build_window_cost(window, pools, n, window_idx, weights, config, domestic, num_nat_terms)
        row_ind, col_ind = linear_sum_assignment(cost)
        for r, c in zip(row_ind, col_ind):
            pools[c].append(window[r])

    _repair_wave_violations(pools, config)
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
    max_iterations: int = 500,
) -> tuple[Assignment, Score]:
    """Hill-climbing: repeatedly apply the best-improving pair swap within tier constraints."""
    import copy
    current = copy.deepcopy(assignment)
    current_score = score(current, weights, config)
    n = config.num_pools

    for iteration in range(max_iterations):
        best_delta = 0.0
        best_swap: tuple[int, int, int, int] | None = None

        for pa, pb in combinations(range(len(current)), 2):
            for ia, fa in enumerate(current[pa]):
                tier_a = _seed_tier(fa.seed, n)
                if tier_a == 0:
                    continue  # pool heads are locked

                for ib, fb in enumerate(current[pb]):
                    tier_b = _seed_tier(fb.seed, n)
                    if tier_b == 0:
                        continue

                    # Tier constraint: fencers can only swap within the same tier
                    if tier_a != tier_b:
                        continue

                    # Never move a dual fencer into a parallel wave
                    if fa.other_disciplines and config.is_parallel(config.wave_of_pool(pb)):
                        continue
                    if fb.other_disciplines and config.is_parallel(config.wave_of_pool(pa)):
                        continue

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
    if config.parallel_waves:
        dual_count = sum(1 for f in fencers if f.other_disciplines)
        non_parallel_pools = sum(
            s for i, s in enumerate(config.wave_sizes) if i not in config.parallel_waves
        )
        log.info("Parallel waves=%s: %d dual fencers must fit in %d non-parallel pool(s)",
                 config.parallel_waves, dual_count, non_parallel_pools)

    assignment = construct(fencers, config, weights)
    log.info("Construction complete, initial score=%s", score(assignment, weights, config))
    assignment, final_score = improve(assignment, weights, config)
    log.info("Solver done, final score=%s", final_score)
    return assignment, final_score