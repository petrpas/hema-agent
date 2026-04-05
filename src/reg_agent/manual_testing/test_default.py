"""Manual test: 26 fencers, 4 pools, 2 waves — typical small Czech tournament.

Run from repo root:
    python -m reg_agent.manual_testing.test_default
"""

import copy
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

_SRC = Path(__file__).parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pool_alch_agent.models import Assignment, PoolConfig, PoolFencer, Score, Weights
from pool_alch_agent.solver import (
    _TIER_BOUNDARIES,
    _domestic_nationality,
    _is_foreign,
    _seed_tier,
    _snake_pos,
    _swap,
    construct,
    score as compute_score,
)

# ── Test data ─────────────────────────────────────────────────────────────────

FENCERS = [
    PoolFencer("Adam",      seed=1,  nationality="CZ", club="Bohemia Praha",   hr_id=None, other_disciplines=[]),
    PoolFencer("Boris",     seed=2,  nationality="CZ", club="Bohemia Praha",   hr_id=None, other_disciplines=[]),
    PoolFencer("Cyril",     seed=3,  nationality="CZ", club="Moravia Brno",    hr_id=None, other_disciplines=[]),
    PoolFencer("Daniel",    seed=4,  nationality="CZ", club="Moravia Brno",    hr_id=None, other_disciplines=[]),
    PoolFencer("Emil",      seed=5,  nationality="CZ", club="Bohemia Praha",   hr_id=None, other_disciplines=[]),
    PoolFencer("Filip",     seed=6,  nationality="CZ", club="Silesia Ostrava", hr_id=None, other_disciplines=[]),
    PoolFencer("Grzegorz",  seed=7,  nationality="PL", club="KS Kraków",      hr_id=None, other_disciplines=[]),
    PoolFencer("Hans",      seed=8,  nationality="DE", club="TV Berlin",       hr_id=None, other_disciplines=[]),
    PoolFencer("Igor",      seed=9,  nationality="CZ", club="Silesia Ostrava", hr_id=None, other_disciplines=[]),
    PoolFencer("Jakub",     seed=10, nationality="CZ", club="Moravia Brno",    hr_id=None, other_disciplines=[]),
    PoolFencer("Karel",     seed=11, nationality="CZ", club="Bohemia Praha",   hr_id=None, other_disciplines=[]),
    PoolFencer("Lukáš",     seed=12, nationality="CZ", club="Silesia Ostrava", hr_id=None, other_disciplines=[]),
    PoolFencer("Marek",     seed=13, nationality="CZ", club="Pilsen",          hr_id=None, other_disciplines=[]),
    PoolFencer("Nikolas",   seed=14, nationality="CZ", club="Pilsen",          hr_id=None, other_disciplines=[]),
    PoolFencer("Ondřej",    seed=15, nationality="CZ", club="Moravia Brno",    hr_id=None, other_disciplines=[]),
    PoolFencer("Paweł",     seed=16, nationality="PL", club="KS Kraków",      hr_id=None, other_disciplines=[]),
    PoolFencer("Radek",     seed=17, nationality="CZ", club="Silesia Ostrava", hr_id=None, other_disciplines=[]),
    PoolFencer("Stefan",    seed=18, nationality="DE", club="TV Berlin",       hr_id=None, other_disciplines=[]),
    PoolFencer("Tomáš",     seed=19, nationality="CZ", club="Bohemia Praha",   hr_id=None, other_disciplines=[]),
    PoolFencer("Václav",    seed=20, nationality="CZ", club="Pilsen",          hr_id=None, other_disciplines=[]),
    PoolFencer("Wojciech",  seed=21, nationality="PL", club="KS Kraków",      hr_id=None, other_disciplines=[]),
    PoolFencer("Klaus",     seed=22, nationality="DE", club="TSV München",     hr_id=None, other_disciplines=[]),
    PoolFencer("Zdeněk",    seed=23, nationality="CZ", club="Pilsen",          hr_id=None, other_disciplines=[]),
    PoolFencer("Aleš",      seed=24, nationality="CZ", club="Bohemia Praha",   hr_id=None, other_disciplines=[]),
    PoolFencer("Martin",    seed=25, nationality="CZ", club="Moravia Brno",    hr_id=None, other_disciplines=[]),
    PoolFencer("Petr",      seed=26, nationality="CZ", club="Silesia Ostrava", hr_id=None, other_disciplines=[]),
]

CONFIG = PoolConfig(num_pools=4, wave_sizes=[2, 2])
WEIGHTS = Weights()

TIER_LABELS = {0: "LOCKED", 1: "tier 1", 2: "tier 2", 3: "free"}


# ── Display helpers ───────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_subheader(title: str) -> None:
    print(f"\n--- {title} ---")


def print_pool_table(assignment: Assignment, config: PoolConfig, label: str = "Names") -> None:
    """Print a single pool attribute table."""
    n = config.num_pools
    max_size = max(len(p) for p in assignment)
    col_width = 22

    # Header
    header = "     "
    for pi in range(len(assignment)):
        wave = config.wave_of_pool(pi) + 1
        header += f"{'Pool ' + str(pi + 1) + ' (W' + str(wave) + ')':<{col_width}}"
    print(header)
    print("     " + "-" * (col_width * len(assignment)))

    for row in range(max_size):
        tier = _seed_tier(row * n + 1, n)  # approximate tier for this row
        tier_tag = f" [{TIER_LABELS[tier]}]" if label == "Names" else ""
        line = f"  {row + 1:>2} "
        for pool in assignment:
            sorted_pool = sorted(pool, key=lambda f: f.seed)
            if row < len(sorted_pool):
                f = sorted_pool[row]
                if label == "Names":
                    line += f"{f.name:<{col_width}}"
                elif label == "Seeds":
                    line += f"{f.seed:<{col_width}}"
                elif label == "Clubs":
                    line += f"{(f.club or ''):<{col_width}}"
                elif label == "Nationalities":
                    line += f"{(f.nationality or ''):<{col_width}}"
            else:
                line += " " * col_width
        print(f"{line}{tier_tag}")


def print_all_tables(assignment: Assignment, config: PoolConfig) -> None:
    for label in ("Names", "Seeds", "Clubs", "Nationalities"):
        print_subheader(label)
        print_pool_table(assignment, config, label)


def print_score_breakdown(assignment: Assignment, weights: Weights, config: PoolConfig) -> None:
    """Print detailed penalty breakdown."""
    n = config.num_pools
    s = compute_score(assignment, weights, config)
    print(f"\n  Score: {s}")

    # Snake detail
    print("\n  Snake deviation by tier:")
    tier_totals = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    deviations = []
    for pool_idx, pool in enumerate(assignment):
        sorted_pool = sorted(pool, key=lambda f: f.seed)
        for row, fencer in enumerate(sorted_pool):
            tier = _seed_tier(fencer.seed, n)
            preferred = fencer.seed - 1
            actual = _snake_pos(pool_idx, row, n)
            dist = abs(actual - preferred)
            tier_totals[tier] += dist
            if dist > 0 and tier < 3:
                deviations.append((fencer.name, fencer.seed, tier, preferred, actual, dist))

    for tier in range(4):
        label = TIER_LABELS[tier]
        penalty = "no penalty" if tier == 3 else f"× {weights.snake_deviation} = {weights.snake_deviation * tier_totals[tier]:.1f}"
        print(f"    {label}: total snake distance = {tier_totals[tier]:.0f} ({penalty})")

    if deviations:
        deviations.sort(key=lambda x: -x[5])
        print("    Displaced fencers (tiers 0–2):")
        for name, seed, tier, pref, actual, dist in deviations:
            print(f"      Seed {seed:>2} {name:<12} {TIER_LABELS[tier]:>6}  "
                  f"preferred snake pos {pref:>2} → actual {actual:>2}  distance={dist}")

    # Club detail
    print("\n  Club collisions:")
    any_collision = False
    for pi, pool in enumerate(assignment):
        clubs = [f.club for f in pool if f.club]
        for club, count in Counter(clubs).items():
            if count > 1:
                names = [f.name for f in sorted(pool, key=lambda f: f.seed) if f.club == club]
                pairs = count * (count - 1) / 2
                print(f"    Pool {pi + 1}: {club} × {count} ({', '.join(names)}) "
                      f"→ {pairs:.0f} pair(s) × {weights.club} = {weights.club * pairs:.1f}")
                any_collision = True
    if not any_collision:
        print("    (none)")

    # Nationality detail
    all_fencers = [f for pool in assignment for f in pool]
    domestic = _domestic_nationality(all_fencers)
    foreign_nats = {f.nationality for f in all_fencers if _is_foreign(f, domestic)}
    num_nat_terms = 1 + len(foreign_nats)
    term_weight = weights.nationality / num_nat_terms

    import numpy as np_
    print(f"\n  Nationality (domestic={domestic}, {len(foreign_nats)} foreign nation(s), "
          f"weight {weights.nationality} / {num_nat_terms} terms = {term_weight:.2f} per term):")

    foreign_counts = [sum(1 for f in pool if _is_foreign(f, domestic)) for pool in assignment]
    std = float(np_.std(foreign_counts))
    print(f"    Total foreign per pool: {foreign_counts} → std={std:.3f} × {term_weight:.2f} = {term_weight * std:.3f}")

    for nat in sorted(foreign_nats):
        counts = [sum(1 for f in pool if f.nationality == nat) for pool in assignment]
        std = float(np_.std(counts))
        names_per_pool = []
        for pool in assignment:
            names_per_pool.append([f.name for f in pool if f.nationality == nat])
        print(f"    {nat} per pool: {counts} → std={std:.3f} × {term_weight:.2f} = {term_weight * std:.3f}")
        for pi, names in enumerate(names_per_pool):
            if names:
                print(f"      Pool {pi + 1}: {', '.join(names)}")


def print_construction_windows(fencers: list[PoolFencer], config: PoolConfig) -> None:
    """Show the snake window assignment order with tiers."""
    n = config.num_pools
    sorted_fencers = sorted(fencers, key=lambda f: f.seed)
    windows = [sorted_fencers[i:i + n] for i in range(0, len(sorted_fencers), n)]
    domestic = _domestic_nationality(fencers)

    for wi, window in enumerate(windows):
        direction = "→" if wi % 2 == 0 else "←"
        tier = _seed_tier(window[0].seed, n)
        slots = list(range(n)) if wi % 2 == 0 else list(range(n - 1, -1, -1))
        parts = []
        for i, f in enumerate(window):
            pool = slots[i] if i < len(slots) else "?"
            nat_tag = f" [{f.nationality}]" if _is_foreign(f, domestic) else ""
            parts.append(f"Seed {f.seed:>2} {f.name}{nat_tag} → Pool {pool + 1}")
        print(f"  Window {wi} ({direction}, {TIER_LABELS[tier]}): {', '.join(parts)}")


# ── Hill-climbing with verbose output ─────────────────────────────────────────

def improve_verbose(
    assignment: Assignment,
    weights: Weights,
    config: PoolConfig,
    max_iterations: int = 500,
) -> tuple[Assignment, Score]:
    """Hill-climbing with per-iteration output."""
    current = copy.deepcopy(assignment)
    current_score = compute_score(current, weights, config)
    n = config.num_pools

    for iteration in range(max_iterations):
        best_delta = 0.0
        best_swap = None

        for pa, pb in combinations(range(len(current)), 2):
            for ia, fa in enumerate(current[pa]):
                tier_a = _seed_tier(fa.seed, n)
                if tier_a == 0:
                    continue

                for ib, fb in enumerate(current[pb]):
                    tier_b = _seed_tier(fb.seed, n)
                    if tier_b == 0:
                        continue
                    if tier_a != tier_b:
                        continue

                    _swap(current, pa, ia, pb, ib)
                    new_score = compute_score(current, weights, config)
                    delta = current_score.total - new_score.total
                    _swap(current, pa, ia, pb, ib)

                    if delta > best_delta:
                        best_delta = delta
                        best_swap = (pa, ia, pb, ib)

        if best_swap is None:
            print(f"\n  Converged after {iteration} iteration(s).")
            break

        pa, ia, pb, ib = best_swap
        fa = current[pa][ia]
        fb = current[pb][ib]
        tier = _seed_tier(fa.seed, n)
        current[pa][ia], current[pb][ib] = current[pb][ib], current[pa][ia]
        old_score = current_score
        current_score = compute_score(current, weights, config)

        print(f"\n  Iteration {iteration} [{TIER_LABELS[tier]}]: "
              f"{fa.name} (seed {fa.seed}, Pool {pa + 1}) "
              f"↔ {fb.name} (seed {fb.seed}, Pool {pb + 1})")
        print(f"    {old_score}")
        print(f"    {current_score}  (Δ = {-best_delta:+.1f})")

        for pi, pool in enumerate(current):
            sorted_p = sorted(pool, key=lambda f: f.seed)
            names = [f"{f.name}({f.seed})" for f in sorted_p]
            print(f"    Pool {pi + 1}: {', '.join(names)}")
    else:
        print(f"\n  Reached max iterations ({max_iterations}).")

    return current, current_score


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    n = CONFIG.num_pools
    domestic = _domestic_nationality(FENCERS)
    foreign = [f for f in FENCERS if _is_foreign(f, domestic)]

    print(f"Tournament: {len(FENCERS)} fencers, {n} pools, {CONFIG.num_waves} waves")
    print(f"Domestic nationality: {domestic}")
    print(f"Foreign fencers ({len(foreign)}): "
          f"{', '.join(f'{f.name} [{f.nationality}, {f.club}]' for f in foreign)}")
    print(f"Weights: snake={WEIGHTS.snake_deviation}, club={WEIGHTS.club}, "
          f"nat={WEIGHTS.nationality}, wave={WEIGHTS.wave}")

    print(f"\nTier boundaries (row 0-based): "
          f"tier 0 [0,{_TIER_BOUNDARIES[0]}) = seeds 1–{_TIER_BOUNDARIES[0]*n} LOCKED | "
          f"tier 1 [{_TIER_BOUNDARIES[0]},{_TIER_BOUNDARIES[1]}) = seeds {_TIER_BOUNDARIES[0]*n+1}–{_TIER_BOUNDARIES[1]*n} | "
          f"tier 2 [{_TIER_BOUNDARIES[1]},{_TIER_BOUNDARIES[2]}) = seeds {_TIER_BOUNDARIES[1]*n+1}–{_TIER_BOUNDARIES[2]*n} | "
          f"tier 3 [{_TIER_BOUNDARIES[2]},∞) = seeds {_TIER_BOUNDARIES[2]*n+1}+ FREE")

    # ── Phase 1: Construction ─────────────────────────────────────────────
    print_header("PHASE 1: Construction (Hungarian assignment per snake window)")

    print_subheader("Snake window preferred assignments")
    print_construction_windows(FENCERS, CONFIG)

    assignment = construct(FENCERS, CONFIG, WEIGHTS)

    print_subheader("Construction result")
    print_all_tables(assignment, CONFIG)
    print_score_breakdown(assignment, WEIGHTS, CONFIG)

    # ── Phase 2: Hill-climbing ────────────────────────────────────────────
    print_header("PHASE 2: Hill-climbing improvement")

    final_assignment, final_score = improve_verbose(assignment, WEIGHTS, CONFIG)

    # ── Final result ──────────────────────────────────────────────────────
    print_header("FINAL RESULT")
    print_all_tables(final_assignment, CONFIG)
    print_score_breakdown(final_assignment, WEIGHTS, CONFIG)


if __name__ == "__main__":
    main()