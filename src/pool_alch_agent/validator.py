"""Validate loaded fencer data before running the solver."""

from pool_alch_agent.models import PoolFencer, PoolConfig, ValidationIssue


def validate(fencers: list[PoolFencer], config: PoolConfig) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    # Missing names (should not happen after loader filters, but be safe)
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

    # Fencer count vs pool count
    n = len(fencers)
    p = config.num_pools
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

    # Club impossible constraint: club with more members than pools
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

    # Num waves sanity
    if config.num_waves < 1:
        issues.append(ValidationIssue(None, "num_waves", "num_waves must be at least 1"))
    elif config.num_waves > p:
        issues.append(ValidationIssue(
            None, "num_waves",
            f"num_waves ({config.num_waves}) > num_pools ({p}) — every pool would be its own wave"
        ))

    return issues