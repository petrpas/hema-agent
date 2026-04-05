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
    h_rating: float | None = None    # from HRating column
    h_rank: int | None = None        # from HRank column


@dataclass
class Weights:
    snake_deviation: float = 1.0    # penalty per pool-step deviation from snake preferred position
    club: float = 10.0              # penalty per same-club pair in a pool
    nationality: float = 3.0       # penalty for uneven foreign-fencer distribution (std dev)
    wave: float = 5.0              # penalty per wave violation (hard constraint — should always be 0)


@dataclass
class PoolConfig:
    num_pools: int
    wave_sizes: list[int]           # e.g. [3, 3, 2, 2] — number of pools per wave, must sum to num_pools
    parallel_waves: list[int] = field(default_factory=list)
    # 0-based wave indices where other disciplines run simultaneously.
    # Dual-discipline fencers must be kept OUT of these waves (they'd be
    # fencing two disciplines at once). Empty = sequential / single-discipline.

    @property
    def num_waves(self) -> int:
        return len(self.wave_sizes)

    def wave_of_pool(self, pool_idx: int) -> int:
        """Return 0-based wave index for a given pool index."""
        cumulative = 0
        for wave_idx, size in enumerate(self.wave_sizes):
            cumulative += size
            if pool_idx < cumulative:
                return wave_idx
        return len(self.wave_sizes) - 1  # fallback for out-of-range

    def is_parallel(self, wave_idx: int) -> bool:
        """True if this wave has other disciplines running alongside it."""
        return wave_idx in self.parallel_waves

    def wave_start(self, wave_idx: int) -> int:
        """Return the first pool index belonging to the given wave."""
        return sum(self.wave_sizes[:wave_idx])


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