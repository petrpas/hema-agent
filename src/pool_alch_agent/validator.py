"""Validate loaded fencer data before running the solver."""

from pool_alch_agent.models import PoolFencer, PoolConfig, ValidationIssue

_WARN_POOL_SIZE = 7    # warn if any pool exceeds this
_MAX_POOL_SIZE  = 10   # hard maximum


def validate(fencers: list[PoolFencer], config: PoolConfig | None = None) -> list[ValidationIssue]:
    """Validate fencer data and (if config is provided) pool layout constraints."""
    issues: list[ValidationIssue] = []

    # Missing names
    for f in fencers:
        if not f.name:
            issues.append(ValidationIssue(None, "name", "Fencer row has empty name"))

    # Missing or zero seeds
    for f in fencers:
        if f.seed == 0:
            issues.append(ValidationIssue(f.name, "seed", "Seed is missing or zero"))

    # Duplicate seeds
    seen: dict[int, str] = {}
    for f in fencers:
        if f.seed in seen:
            issues.append(ValidationIssue(
                f.name, "seed",
                f"Duplicate seed {f.seed} — also held by '{seen[f.seed]}'"
            ))
        else:
            seen[f.seed] = f.name

    if config is None:
        return issues

    # wave_sizes must sum to num_pools
    p = config.num_pools
    ws = config.wave_sizes
    if not ws:
        issues.append(ValidationIssue(None, "wave_sizes", "wave_sizes must not be empty"))
    else:
        if sum(ws) != p:
            issues.append(ValidationIssue(
                None, "wave_sizes",
                f"wave_sizes {ws} sum to {sum(ws)} but num_pools is {p}"
            ))
        if any(s <= 0 for s in ws):
            issues.append(ValidationIssue(None, "wave_sizes", "All wave sizes must be > 0"))

    # Fencer count vs pool count
    n = len(fencers)
    if n < p:
        issues.append(ValidationIssue(
            None, "pool_count",
            f"{n} fencers but {p} pools requested — need at least {p} fencers"
        ))
    elif n < p * 2:
        issues.append(ValidationIssue(
            None, "pool_count",
            f"Only {n} fencers for {p} pools ({n // p}–{n % p or n // p} per pool) — pools will be very small"
        ))

    # Pool size warnings (approximate — actual sizes depend on solver output)
    if p > 0:
        max_pool_size = -(-n // p)  # ceil division
        if max_pool_size > _MAX_POOL_SIZE:
            issues.append(ValidationIssue(
                None, "pool_size",
                f"Max pool size ~{max_pool_size} exceeds hard maximum of {_MAX_POOL_SIZE} — reduce pool count or accept fewer fencers"
            ))
        elif max_pool_size > _WARN_POOL_SIZE:
            issues.append(ValidationIssue(
                None, "pool_size",
                f"Max pool size ~{max_pool_size} exceeds {_WARN_POOL_SIZE} — fencers will have many bouts ({max_pool_size - 1} each)"
            ))

    # Club impossible constraint
    club_counts: dict[str, list[str]] = {}
    for f in fencers:
        if f.club:
            club_counts.setdefault(f.club, []).append(f.name)
    for club, members in club_counts.items():
        if len(members) > p:
            issues.append(ValidationIssue(
                None, "club",
                f"Club '{club}' has {len(members)} fencers but only {p} pools — "
                f"some pool will have multiple members ({', '.join(members)})"
            ))

    # Parallel wave capacity: non-parallel waves must have enough pools for all dual fencers
    if config.parallel_waves:
        dual_count = sum(1 for f in fencers if f.other_disciplines)
        non_parallel_pools = sum(
            s for i, s in enumerate(ws) if i not in config.parallel_waves
        )
        if dual_count > 0 and non_parallel_pools == 0:
            issues.append(ValidationIssue(
                None, "parallel_waves",
                f"All waves are marked parallel but {dual_count} dual-discipline fencer(s) "
                f"need at least one non-parallel wave"
            ))
        elif dual_count > 0:
            max_capacity = max(
                (s for i, s in enumerate(ws) if i not in config.parallel_waves),
                default=0,
            ) * _MAX_POOL_SIZE
            if dual_count > max_capacity:
                issues.append(ValidationIssue(
                    None, "parallel_waves",
                    f"{dual_count} dual-discipline fencers but non-parallel waves only have "
                    f"{non_parallel_pools} pool(s) — may not fit all dual fencers"
                ))

    return issues
