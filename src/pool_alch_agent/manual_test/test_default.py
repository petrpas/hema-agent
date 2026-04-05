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
    _domestic_nationality,
    _is_foreign,
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


# ── Display helpers ───────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_subheader(title: str) -> None:
    print(f"\n--- {title} ---")


def print_pool_table(assignment: Assignment, config: PoolConfig, label: str = "Names") -> None:
    """Print a single pool attribute table."""
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
        print(line)


def print_all_tables(assignment: Assignment, config: PoolConfig) -> None:
    for label in ("Names", "Seeds", "Clubs", "Nationalities"):
        print_subheader(label)
        print_pool_table(assignment, config, label)


def print_score_breakdown(assignment: Assignment, weights: Weights, config: PoolConfig) -> None:
    """Print detailed penalty breakdown."""
    s = compute_score(assignment, weights, config)
    print(f"\n  Score: {s}")

    # Club detail
    print("\n  Club collisions:")
    any_collision = False
    for pi, pool in enumerate(assignment):
        clubs = [f.club for f in pool if f.club]
        for club, count in Counter(clubs).items():
            if count > 1:
                names = [f.name for f in sorted(pool, key=lambda f: f.seed) if f.club == club]
                pairs = count * (count - 1) / 2
                print(f"    Pool {pi + 1}: {club} × {count} ({', '.join(names)}) → {pairs:.0f} pair(s) × {weights.club} = {weights.club * pairs:.1f}")
                any_collision = True
    if not any_collision:
        print("    (none)")

    # Nationality detail
    all_fencers = [f for pool in assignment for f in pool]
    domestic = _domestic_nationality(all_fencers)
    foreign_nats = {f.nationality for f in all_fencers if _is_foreign(f, domestic)}
    num_nat_terms = 1 + len(foreign_nats)
    term_weight = weights.nationality / num_nat_terms

    print(f"\n  Nationality (domestic={domestic}, {len(foreign_nats)} foreign nation(s), "
          f"weight {weights.nationality} / {num_nat_terms} terms = {term_weight:.2f} per term):")

    foreign_counts = [sum(1 for f in pool if _is_foreign(f, domestic)) for pool in assignment]
    import numpy as np
    std = float(np.std(foreign_counts))
    print(f"    Total foreign per pool: {foreign_counts} → std={std:.3f} × {term_weight:.2f} = {term_weight * std:.3f}")

    for nat in sorted(foreign_nats):
        counts = [sum(1 for f in pool if f.nationality == nat) for pool in assignment]
        std = float(np.std(counts))
        names_per_pool = []
        for pool in assignment:
            names_per_pool.append([f.name for f in pool if f.nationality == nat])
        print(f"    {nat} per pool: {counts} → std={std:.3f} × {term_weight:.2f} = {term_weight * std:.3f}")
        for pi, names in enumerate(names_per_pool):
            if names:
                print(f"      Pool {pi + 1}: {', '.join(names)}")

    # Snake detail
    all_with_pool = [(f, pi) for pi, pool in enumerate(assignment) for f in pool]
    sorted_by_seed = sorted(all_with_pool, key=lambda x: x[0].seed)
    n = config.num_pools
    num_windows = -(-len(sorted_by_seed) // n)
    print(f"\n  Snake deviation (top 10 worst):")
    deviations = []
    for snake_pos, (fencer, actual_pool) in enumerate(sorted_by_seed):
        window = snake_pos // n
        pos_in_window = snake_pos % n
        preferred = pos_in_window if window % 2 == 0 else (n - 1 - pos_in_window)
        pw = (num_windows - window) / num_windows
        dev = abs(actual_pool - preferred)
        cost = weights.snake_deviation * pw * dev
        deviations.append((fencer.name, fencer.seed, window, preferred, actual_pool, dev, pw, cost))
    deviations.sort(key=lambda x: -x[7])
    for name, seed, window, pref, actual, dev, pw, cost in deviations[:10]:
        if dev > 0:
            print(f"    Seed {seed:>2} {name:<12} window {window} preferred→Pool {pref + 1} actual→Pool {actual + 1} "
                  f"dev={dev} × weight={pw:.2f} → {cost:.2f}")


def print_construction_windows(fencers: list[PoolFencer], config: PoolConfig) -> None:
    """Show the snake window assignment order."""
    n = config.num_pools
    sorted_fencers = sorted(fencers, key=lambda f: f.seed)
    windows = [sorted_fencers[i:i + n] for i in range(0, len(sorted_fencers), n)]

    for wi, window in enumerate(windows):
        direction = "→" if wi % 2 == 0 else "←"
        pw = (len(windows) - wi) / len(windows)
        slots = list(range(n)) if wi % 2 == 0 else list(range(n - 1, -1, -1))
        parts = []
        for i, f in enumerate(window):
            pool = slots[i] if i < len(slots) else "?"
            nat_tag = f" [{f.nationality}]" if _is_foreign(f, _domestic_nationality(fencers)) else ""
            parts.append(f"Seed {f.seed:>2} {f.name}{nat_tag} → Pool {pool + 1}")
        print(f"  Window {wi} ({direction}, weight={pw:.2f}): {', '.join(parts)}")


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

    for iteration in range(max_iterations):
        best_delta = 0.0
        best_swap = None

        protected = {min(pool, key=lambda f: f.seed).seed for pool in current if pool}

        for pa, pb in combinations(range(len(current)), 2):
            for ia, fa in enumerate(current[pa]):
                for ib, fb in enumerate(current[pb]):
                    if fa.seed in protected or fb.seed in protected:
                        continue

                    current[pa][ia], current[pb][ib] = fb, fa
                    new_score = compute_score(current, weights, config)
                    delta = current_score.total - new_score.total
                    current[pa][ia], current[pb][ib] = fa, fb

                    if delta > best_delta:
                        best_delta = delta
                        best_swap = (pa, ia, pb, ib)

        if best_swap is None:
            print(f"\n  Converged after {iteration} iteration(s).")
            break

        pa, ia, pb, ib = best_swap
        fa = current[pa][ia]
        fb = current[pb][ib]
        current[pa][ia], current[pb][ib] = fb, fa
        old_score = current_score
        current_score = compute_score(current, weights, config)

        print(f"\n  Iteration {iteration}: {fa.name} (seed {fa.seed}, Pool {pa + 1}) "
              f"↔ {fb.name} (seed {fb.seed}, Pool {pb + 1})")
        print(f"    {old_score}")
        print(f"    {current_score}  (Δ = {-best_delta:+.1f})")

        # Show current state compactly
        for pi, pool in enumerate(current):
            sorted_p = sorted(pool, key=lambda f: f.seed)
            names = [f"{f.name}({f.seed})" for f in sorted_p]
            print(f"    Pool {pi + 1}: {', '.join(names)}")
    else:
        print(f"\n  Reached max iterations ({max_iterations}).")

    return current, current_score


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    domestic = _domestic_nationality(FENCERS)
    foreign = [f for f in FENCERS if _is_foreign(f, domestic)]
    print(f"Tournament: 26 fencers, 4 pools, 2 waves of 2")
    print(f"Domestic nationality: {domestic}")
    print(f"Foreign fencers ({len(foreign)}): {', '.join(f'{f.name} [{f.nationality}, {f.club}]' for f in foreign)}")
    print(f"Weights: snake={WEIGHTS.snake_deviation}, club={WEIGHTS.club}, "
          f"nat={WEIGHTS.nationality}, wave={WEIGHTS.wave}")

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