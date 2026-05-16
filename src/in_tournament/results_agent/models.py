"""Data models for the pool-results parsing pipeline."""

from enum import Enum
from pydantic import BaseModel


class BoutOutcome(str, Enum):
    WIN = "Win"
    LOSS = "Loss"
    DRAW = "Draw"
    NO = "No"


class RawBout(BaseModel):
    """Single bout as returned by the vision LLM (pre-verification)."""
    fencer1: str
    fencer2: str
    score1: int
    score2: int
    r1: str   # "Win" / "Loss" / "Draw" / "No"
    r2: str
    uncertain: bool = False   # LLM was unsure about any value in this bout
    note: str | None = None   # any annotation from the match list (e.g. "walkover", "medical")


class ParsedPool(BaseModel):
    """Full pool as returned by the vision LLM (pre-verification)."""
    disc: str            # discipline code chosen from the provided list, e.g. "LS"
    pool_no: int | None  # pool number (None if unreadable from the sheet)
    bouts: list[RawBout]
    low_confidence: bool = False  # LLM signals image quality / legibility issues


class BoutResult(BaseModel):
    """Single verified bout, ready to write to the Upload sheet."""
    pool_id: str
    fencer1: str
    fencer2: str
    score1: int
    score2: int
    r1: BoutOutcome
    r2: BoutOutcome
    note: str | None = None


class PoolResult(BaseModel):
    """Fully verified pool result returned to the bot."""
    pool_id: str            # "LS-3" (or "LS-?" if pool number unreadable)
    disc: str               # "LS"
    pool_no: int | None     # None if unreadable from the sheet
    bouts: list[BoutResult]
    confidence: str         # "." clean / "?" needs review / "??" unreadable
    issues: list[str]       # human-readable verification problems
