"""Data models for the pools alchemy agent."""

from dataclasses import dataclass, field


@dataclass
class PoolFencer:
    name: str
    seed: int                        # from Seed column — authoritative
    nationality: str | None
    club: str | None
    hr_id: int | None
    other_disciplines: list[str]     # other discipline codes this fencer also registered for


@dataclass
class Weights:
    snake_deviation: float = 1.0    # penalty per pool-step deviation from snake preferred position
    club: float = 10.0              # penalty per same-club pair in a pool
    nationality: float = 3.0       # penalty for uneven foreign-fencer distribution (std dev)
    wave: float = 5.0              # penalty per dual-discipline fencer NOT in wave 1


@dataclass
class PoolConfig:
    num_pools: int
    num_waves: int                  # pools are split into waves: wave 1 = pools[0..n/waves], etc.


@dataclass
class Score:
    snake_deviation: float
    club: float
    nationality: float
    wave: float

    @property
    def total(self) -> float:
        return self.snake_deviation + self.club + self.nationality + self.wave

    def __str__(self) -> str:
        return (
            f"total={self.total:.1f} "
            f"(snake={self.snake_deviation:.1f}, club={self.club:.1f}, "
            f"nat={self.nationality:.1f}, wave={self.wave:.1f})"
        )


@dataclass
class ValidationIssue:
    fencer_name: str | None        # None = global issue
    field: str
    message: str

    def __str__(self) -> str:
        who = self.fencer_name or "(global)"
        return f"{who} [{self.field}]: {self.message}"


# Assignment: list of pools, each pool is a list of fencers (ordered by seed within pool)
Assignment = list[list[PoolFencer]]