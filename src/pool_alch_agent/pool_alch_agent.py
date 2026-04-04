"""Pools alchemy agent — designs tournament pool assignments interactively.

Flow:
  1. load(discipline)  — read discipline tab from Google Sheet, detect dual-discipline
  2. validate()        — report issues; user fixes sheet; reload if needed
  3. set_pool_config() — num_pools, num_waves
  4. set_weights()     — tune scoring penalties (LLM translates user priorities)
  5. run_solver()      — Hungarian construction + hill-climbing
  6. review loop       — swap pairs, adjust weights, re-run solver
  7. approve           — write result back to sheet (future)
"""

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import discord
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelHTTPError

from config import RegConfig
from discord_bot.discord_utils import send_long
from msgs import read_msg
from pool_alch_agent.loader import load_discipline
from pool_alch_agent.models import Assignment, PoolConfig, PoolFencer, Score, Weights
from pool_alch_agent.validator import validate
from pool_alch_agent.solver import solve, score as compute_score
from pool_alch_agent.state import save_state
from pool_alch_agent.writer import write_pools_sheet

log = logging.getLogger(__name__)

MAX_HISTORY = 40


@dataclass
class PoolAlchDeps:
    channel: discord.TextChannel
    config: RegConfig
    fencers: list[PoolFencer] = field(default_factory=list)
    validated: bool = False
    pool_config: PoolConfig | None = None
    weights: Weights = field(default_factory=Weights)
    assignment: Assignment | None = None
    last_score: Score | None = None
    current_discipline: str | None = None


pool_alch_agent = Agent(
    "anthropic:claude-sonnet-4-6",
    deps_type=PoolAlchDeps,
    system_prompt=read_msg("pool_alch/system_prompt"),
)


# ── Tools ──────────────────────────────────────────────────────────────────────

@pool_alch_agent.tool
async def tool_load(ctx: RunContext[PoolAlchDeps], discipline_code: str) -> str:
    """Load fencers from the discipline tab of the output Google Sheet and validate.

    Always call this first, and again after the organiser fixes sheet issues.
    """
    deps = ctx.deps
    try:
        fencers, warnings = await asyncio.to_thread(
            load_discipline, deps.config, discipline_code
        )
    except ValueError as e:
        return f"error: {e}"

    deps.fencers = fencers
    deps.current_discipline = discipline_code
    deps.validated = False
    deps.assignment = None
    deps.last_score = None

    # Validate — requires pool_config for some checks; use a placeholder if not set yet
    pc = deps.pool_config or PoolConfig(num_pools=1, num_waves=1)
    issues = validate(fencers, pc)

    lines = [f"Loaded {len(fencers)} fencers for discipline '{discipline_code}'."]

    dual = [f for f in fencers if f.other_disciplines]
    if dual:
        lines.append(f"Dual-discipline: {len(dual)} fencer(s) also in {set(d for f in dual for d in f.other_disciplines)}.")

    if warnings:
        lines.append(f"\nLoad warnings ({len(warnings)}):")
        lines.extend(f"  - {w}" for w in warnings)

    if issues:
        lines.append(f"\nValidation issues ({len(issues)}) — ask organiser to fix before solving:")
        lines.extend(f"  - {i}" for i in issues)
    else:
        deps.validated = True
        lines.append("\nValidation passed.")

    save_state(deps)
    return "\n".join(lines)


@pool_alch_agent.tool
def tool_set_pool_config(ctx: RunContext[PoolAlchDeps], num_pools: int, wave_sizes: list[int]) -> str:
    """Set number of pools and wave layout.

    wave_sizes is a list of pool counts per wave, e.g. [3, 3, 2, 2] for 10 pools in 4 waves.
    Must sum to num_pools.
    """
    deps = ctx.deps
    deps.pool_config = PoolConfig(num_pools=num_pools, wave_sizes=wave_sizes)
    deps.assignment = None
    deps.last_score = None

    if not deps.fencers:
        return f"Pool config set: {num_pools} pools, waves={wave_sizes}. Load fencers first."

    issues = validate(deps.fencers, deps.pool_config)
    deps.validated = not bool(issues)

    result = f"Pool config set: {num_pools} pools, waves={wave_sizes}."
    if issues:
        result += f"\n{len(issues)} validation issue(s) with this config:\n"
        result += "\n".join(f"  - {i}" for i in issues)
    else:
        result += " Validation passed."
    save_state(deps)
    return result


@pool_alch_agent.tool
def tool_set_weights(
    ctx: RunContext[PoolAlchDeps],
    snake_deviation: float | None = None,
    club: float | None = None,
    nationality: float | None = None,
    wave: float | None = None,
) -> str:
    """Update scoring weights. Only pass the weights you want to change."""
    w = ctx.deps.weights
    if snake_deviation is not None:
        w.snake_deviation = snake_deviation
    if club is not None:
        w.club = club
    if nationality is not None:
        w.nationality = nationality
    if wave is not None:
        w.wave = wave
    ctx.deps.assignment = None  # invalidate current assignment
    ctx.deps.last_score = None
    save_state(ctx.deps)
    return (
        f"Weights updated: snake={w.snake_deviation}, club={w.club}, "
        f"nationality={w.nationality}, wave={w.wave}"
    )


@pool_alch_agent.tool
async def tool_run_solver(ctx: RunContext[PoolAlchDeps]) -> str:
    """Run the solver (Hungarian construction + hill-climbing) and store the assignment."""
    deps = ctx.deps
    if not deps.fencers:
        return "error: no fencers loaded — call tool_load first."
    if not deps.validated:
        return "error: data has validation issues — fix them and reload before solving."
    if deps.pool_config is None:
        return "error: pool config not set — call tool_set_pool_config first."

    assignment, s = await asyncio.to_thread(
        solve, deps.fencers, deps.pool_config, deps.weights
    )
    deps.assignment = assignment
    deps.last_score = s
    save_state(deps)
    return f"Solver done. Score: {s}"


@pool_alch_agent.tool
def tool_get_assignment(ctx: RunContext[PoolAlchDeps]) -> str:
    """Return the current pool assignment as a formatted text table."""
    deps = ctx.deps
    if deps.assignment is None:
        return "No assignment yet — call tool_run_solver first."

    config = deps.pool_config
    lines = []

    for pool_idx, pool in enumerate(deps.assignment):
        wave = (config.wave_of_pool(pool_idx) + 1) if config else 1
        lines.append(f"\n**Pool {pool_idx + 1}** (wave {wave}) — {len(pool)} fencers")
        sorted_pool = sorted(pool, key=lambda f: f.seed)
        for f in sorted_pool:
            dual = f" +{','.join(f.other_disciplines)}" if f.other_disciplines else ""
            nat = f.nationality or "?"
            club = f.club or "?"
            lines.append(f"  [{f.seed:>3}] {f.name} | {nat} | {club}{dual}")

    if deps.last_score:
        lines.append(f"\nScore: {deps.last_score}")

    return "\n".join(lines)


@pool_alch_agent.tool
def tool_swap_fencers(ctx: RunContext[PoolAlchDeps], name_a: str, name_b: str) -> str:
    """Manually swap two fencers between pools by name (partial name match supported)."""
    deps = ctx.deps
    if deps.assignment is None:
        return "No assignment yet — run solver first."

    def _find(name: str) -> tuple[int, int] | None:
        name_l = name.lower()
        for pi, pool in enumerate(deps.assignment):
            for fi, f in enumerate(pool):
                if name_l in f.name.lower():
                    return (pi, fi)
        return None

    loc_a = _find(name_a)
    loc_b = _find(name_b)

    if loc_a is None:
        return f"error: fencer '{name_a}' not found in assignment."
    if loc_b is None:
        return f"error: fencer '{name_b}' not found in assignment."
    if loc_a[0] == loc_b[0]:
        return "error: both fencers are in the same pool — swap would have no effect."

    pa, ia = loc_a
    pb, ib = loc_b
    fa = deps.assignment[pa][ia]
    fb = deps.assignment[pb][ib]
    deps.assignment[pa][ia], deps.assignment[pb][ib] = fb, fa

    # Recompute score
    if deps.pool_config:
        deps.last_score = compute_score(deps.assignment, deps.weights, deps.pool_config)

    save_state(deps)
    return (
        f"Swapped {fa.name} (pool {pa + 1}) ↔ {fb.name} (pool {pb + 1}). "
        f"New score: {deps.last_score}"
    )


@pool_alch_agent.tool
async def tool_write_to_sheet(ctx: RunContext[PoolAlchDeps]) -> str:
    """Write the approved pool assignment to a new '{discipline}_Pools' worksheet.

    Section 1 (cols A-G): fencer list sorted by seed.
    Section 2 (col J+): pool assignment table, one wave block per wave.
    Call only when the organiser has approved the assignment.
    """
    deps = ctx.deps
    if deps.assignment is None:
        return "error: no assignment — run solver first."
    if not deps.current_discipline:
        return "error: no discipline loaded."
    if deps.pool_config is None:
        return "error: pool config not set."

    try:
        url, warnings = await asyncio.to_thread(
            write_pools_sheet,
            deps.config,
            deps.current_discipline,
            deps.fencers,
            deps.assignment,
            deps.pool_config,
        )
    except Exception as e:
        return f"error writing sheet: {e}"

    lines = [f"Written to sheet: {url}"]
    if warnings:
        lines.append(f"\nWarnings ({len(warnings)}):")
        lines.extend(f"  - {w}" for w in warnings)
    return "\n".join(lines)


# ── History + run ──────────────────────────────────────────────────────────────

async def _build_prompt(channel: discord.TextChannel, new_message_content: str) -> str:
    msgs: list[discord.Message] = []
    async for msg in channel.history(limit=MAX_HISTORY + 1):
        msgs.append(msg)
    msgs = msgs[1:]
    msgs.reverse()
    lines = [
        f"{'bot' if msg.author.bot else 'organiser'}: {msg.content}"
        for msg in msgs
    ]
    history = "\n".join(lines) if lines else "(no prior messages)"
    return (
        f"[Channel history — oldest first]\n{history}\n\n"
        f"[New message from organiser]\n{new_message_content}"
    )


async def run_pool_alch_agent(
    channel: discord.TextChannel,
    new_message_content: str,
    config: RegConfig,
    deps: PoolAlchDeps | None = None,
) -> None:
    """Run one agent turn in the pools alchemy channel."""
    if deps is None:
        deps = PoolAlchDeps(channel=channel, config=config)
    else:
        deps.channel = channel  # refresh in case of reconnect
    prompt = await _build_prompt(channel, new_message_content)

    try:
        result = await pool_alch_agent.run(prompt, deps=deps)
        output_str = result.output or ""
        log.info("PoolAlch agent result: %d chars, preview=%r", len(output_str), output_str[:200])
        if output_str.strip():
            await send_long(channel, output_str)
    except ModelHTTPError as e:
        log.exception("Pool alch agent run failed")
        if e.status_code == 529:
            await channel.send("⚠ Anthropic API is overloaded — please try again in a moment.")
        else:
            await channel.send(f"⚠ Anthropic API error ({e.status_code}) — check logs.")
    except Exception:
        log.exception("Pool alch agent run failed")
        await channel.send("⚠ Internal error — check logs.")