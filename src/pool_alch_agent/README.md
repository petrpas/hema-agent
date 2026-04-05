# Pool Alchemy Agent

Designs fair pool assignments for HEMA tournament disciplines. The organiser
interacts with the agent through a dedicated Discord channel (`#hsq-pools-alchemy`),
working through pool layout, priorities, and iterative review until satisfied.

## Agent architecture

The agent is built on **pydantic-ai** (`Agent` with tools and typed deps). Each
Discord message triggers one agent turn via `run_pool_alch_agent()`.

### Key components

| File | Role |
|---|---|
| `pool_alch_agent.py` | Agent definition, all tools, Discord integration, history builder |
| `models.py` | Data models: `PoolFencer`, `Weights`, `PoolConfig`, `Score`, `Assignment` |
| `loader.py` | Reads fencer data from the Google Sheet discipline tab; detects dual-discipline fencers by scanning all other discipline tabs |
| `validator.py` | Pre-solve checks: missing seeds, duplicate seeds, pool size limits, club impossibility, parallel-wave capacity |
| `solver.py` | Two-phase optimiser (see algorithm below) |
| `writer.py` | Writes `{discipline}_Pools` worksheet: fencer list + 2×2 diagnostic tables (names, seeds, clubs, nationalities) |
| `renderer.py` | Renders pool tables to PNG via Typst for posting in Discord |
| `state.py` | JSON serialisation of `PoolAlchDeps` to the data volume for cross-turn persistence |

### Conversation flow

The LLM system prompt (`msgs/EN/pool_alch/system_prompt.md`) guides the agent
through a structured workflow:

1. **Load** — organiser names a discipline → `tool_load` reads the sheet and validates
2. **Pool layout** — discuss pool count, wave sizes, parallel waves → `tool_set_pool_config`
3. **Criteria** — discuss priorities (club separation, nationality, seeding) → `tool_set_weights`
4. **Solve** — `tool_run_solver` → `tool_write_to_sheet` → organiser reviews in spreadsheet
5. **Review** — swap fencers (`tool_swap_fencers`), adjust weights, re-solve, render PNG
6. **Approve** — lock pools, render final start lists

### State persistence

`PoolAlchDeps` (fencers, config, weights, assignment, score) is serialised to
`data/{tournament}/pool_alch_state.json` after every tool call. On bot restart,
`PoolsCog._get_deps()` restores the deps from disk so the conversation continues
seamlessly.

### History

The agent does not use pydantic-ai's built-in message history. Instead,
`_build_prompt()` reads the last 40 Discord messages from the channel and
formats them as `organiser:` / `bot:` lines, appended after the available
disciplines list. This gives the LLM full conversational context each turn.

## Solver algorithm

The solver (`solver.py`) assigns fencers to pools in two phases.

### Seed tiers

Seeding is protected through **hard constraints** (which swaps are allowed)
rather than a soft penalty competing with club/nationality. Fencers are
assigned to tiers based on their row in the ideal snake order:

| Tier | Rows (1-based) | Seeds (4 pools) | Rule |
|------|----------------|-----------------|------|
| 0 | Row 1 | 1–4 | **Locked** — never swapped |
| 1 | Row 2 | 5–8 | Swap only within tier 1 |
| 2 | Rows 3–4 | 9–16 | Swap only within tier 2 |
| 3 | Rows 5+ | 17+ | **Free** — swap freely, zero snake penalty |

This design reflects how organisers actually think: top seeds matter, middle
seeds somewhat, bottom seeds are flexible. It also eliminates the problem of
balancing snake weight against club/nationality — the tiers handle importance
structurally, so the soft penalties only compete with each other.

Example — 15 fencers in 3 pools, 5 rows:

| Row | Dir | Pool A | Pool B | Pool C | Tier |
|-----|-----|--------|--------|--------|------|
| 1 | → | Seed 1 | Seed 2 | Seed 3 | 0 (locked) |
| 2 | ← | Seed 6 | Seed 5 | Seed 4 | 1 |
| 3 | → | Seed 7 | Seed 8 | Seed 9 | 2 |
| 4 | ← | Seed 12 | Seed 11 | Seed 10 | 2 |
| 5 | → | Seed 13 | Seed 14 | Seed 15 | 3 (free) |

#### Snake distance (penalty metric for tiers 1–2)

When the solver displaces a tier 1 or 2 fencer, the penalty is the **snake
distance** — the absolute difference between the fencer's preferred and actual
positions along the snake path. This naturally captures both horizontal (pool)
and vertical (row) displacement.

For the example above, the snake path visits positions in order:
`0, 1, 2, 5, 4, 3, 6, 7, 8, 11, 10, 9, 12, 13, 14`. Swapping Seed 8
(snake pos 7) with Seed 11 (snake pos 9) costs `|7 − 9| = 2`. Swapping
Seed 8 with Seed 4 (snake pos 5) costs `|7 − 5| = 2`. Positions adjacent
in the snake path are cheap to swap; positions far apart are expensive.

### Objective function

The solver minimises a weighted sum of penalty terms. Snake deviation is
structural (tier constraints + snake distance); the soft penalties are:

| Term | What it measures | Default weight |
|---|---|---|
| **Snake deviation** | Snake distance for displaced tier 0–2 fencers | 1.0 |
| **Club** | Same-club pairs per pool: C(n,2) per club | 10.0 |
| **Nationality** | Uneven foreign-fencer distribution (see below) | 3.0 |
| **Wave** | Dual-discipline fencers in parallel waves (hard constraint) | 5.0 |

#### Club penalty

For each pool, count fencers from the same club. If a club has `n` members in
one pool, the penalty is `weight × n×(n−1)/2` (number of same-club pairs). This
makes three-in-a-pool far more expensive than two-in-a-pool.

#### Nationality penalty

The most frequent nationality is auto-detected as domestic. All fencers with
a different (non-null) nationality are foreign. The penalty has two layers:

1. **Total foreign distribution** — std dev of foreign-fencer counts across pools.
   Ensures foreign fencers as a group are spread evenly.
2. **Per-nationality distribution** — std dev of counts for each individual foreign
   nationality. Ensures e.g. 4 Germans and 4 Poles each get spread across pools
   rather than clustered together.

The weight is **normalised** by dividing by `(1 + num_foreign_nationalities)`,
so the total nationality contribution stays constant regardless of how many
foreign nations are present. This prevents nationality from overpowering
club separation in internationally diverse tournaments.

#### Wave constraint

Dual-discipline fencers (competing in multiple disciplines) cannot be placed in
waves where their other discipline runs simultaneously. Violated in the cost
matrix by assigning `∞` cost; in scoring by a per-violation penalty. Should
always be zero in a valid solution.

### Phase 1: Construction (Hungarian assignment)

Fencers are sorted by seed and split into windows of `num_pools` fencers each.
Each window is assigned using the **Hungarian algorithm** (`scipy.optimize.linear_sum_assignment`)
on a cost matrix that combines snake deviation, club, nationality, and wave
penalties. Tiers 0–2 (rows 1–4) include a snake penalty in the cost matrix;
tier 3 (row 5+) has zero snake cost, so the Hungarian algorithm optimises
purely for club and nationality separation.

Windows are processed top-down (best seeds first), so early placements face
fewer existing constraints and get near-optimal positions.

### Phase 2: Improvement (hill-climbing)

Starting from the construction result, the solver repeatedly evaluates all
eligible pair swaps and applies the single best-improving swap. This continues
until no swap reduces the total score (local minimum) or the iteration limit
is reached (default 500).

Swap eligibility is governed by the tier system:
- **Tier 0** fencers are never swapped.
- **Tier 1** fencers can only swap with other tier 1 fencers.
- **Tier 2** fencers can only swap with other tier 2 fencers.
- **Tier 3** fencers can swap freely with any other tier 3 fencer.
- **Wave constraint**: dual-discipline fencers are never moved into parallel waves.

The tier constraints dramatically reduce the search space. The hill-climber
typically converges in 2–5 iterations, with tier 3 swaps handling most
club/nationality improvements at zero seeding cost.