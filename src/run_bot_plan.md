# `run_bot` — Specification

`run_bot` is the Discord bot that runs **during** a HEMA tournament. It is independent from `pre_bot` (which handles pre-tournament prep). The two bots share only generic Discord helpers (in `src/discord_bot/`) and framework-level utilities under `src/pre_tournament/` (config loaders, message reader, tracing) — they do not share agent code.

## 0. Repository placement

The codebase is now split by tournament phase:

```
src/
  discord_bot/              — top-level: bot entry points + shared Discord helpers
    pre_bot.py              — existing entry point for the pre-tournament bot
    run_bot.py              — NEW entry point for the in-tournament bot
    discord_utils.py        — shared (send_long, make_table)
    msg_constants.py        — shared channel-name constants + APP_NAME
  pre_tournament/           — pre-tournament agents and framework utilities
    config/                 — RegConfig, AgentConfig, tracing, agent_config.json
    msgs/                   — read_msg / render_msg + EN/CS message trees for pre-bot
    reg_agent/  setup_agent/  pool_alch_agent/  payment_agent/
  in_tournament/            — NEW home for run_bot's agents and assets
    run_setup_agent/        — setup wizard for the live server (§2)
    results_agent/          — parse + verify pool-result images (§6.2)
    server_layout.py        — channel/role/permission layout (§4)
    msgs/                   — EN/CS message trees for run_setup, run_bot
    config/                 — phase-specific config (only if needed; see §4)
  post_tournament/          — placeholder for after-tournament workflows
  shared/                   — cross-phase assets (currently typst/ templates+fonts)
```

Cross-package import rules:

- `discord_bot/run_bot.py` may import from `in_tournament.*` and from framework utilities (`pre_tournament.config`, `pre_tournament.msgs`, `pre_tournament.config.tracing`).
- `in_tournament/*` may import from `pre_tournament.config`, `pre_tournament.msgs`, `shared/*`, and `discord_bot.discord_utils`. It **never** imports from `pre_tournament.reg_agent`, `pre_tournament.setup_agent`, `pre_tournament.pool_alch_agent`, or `pre_tournament.payment_agent` — those are pre-bot's agents, not framework.
- The `pre_tournament.config` and `pre_tournament.msgs` packages are de facto framework-shared today; whether to lift them into `shared/` is tracked as an open question in §5.

## 1. Server setup

### Manual prerequisites (one-off, ~5 min)

1. Create the Discord server (UI).
2. Create a Discord application + bot at <https://discord.com/developers>; copy the token.
3. Invite the bot to the server via its OAuth2 URL with the `bot` and `applications.commands` scopes and the permissions: Manage Roles, Manage Channels, Create Invite, View Channels, Send Messages, Read Message History, Embed Links, Attach Files.
4. Run the bot's `/setup` command (or one-shot CLI `python -m in_tournament.setup`) once on the server.

Everything below is automated.

### Roles

Created if missing; existing roles with the same name are reused (idempotent).

| Role | Purpose |
|---|---|
| `Organizer` | Tournament staff. Full visibility. |
| `Guest`     | Fencers and visitors. Restricted visibility. |
| `Bot`       | The bot's own role; granted only the channels it must read/write. |

`@everyone` is **denied** `View Channel` at the guild level. Visibility is granted only via per-channel role overrides — a user with no role sees nothing.

### Channel layout

Three categories. Per-channel permission matrix (rw = read+write, ro = read-only, — = invisible):

**General** (visible to everyone in the tournament)

| Channel              | Organizer | Guest | Bot |
|----------------------|-----------|-------|-----|
| `welcome`            | rw        | ro    | rw  |
| `announcements`      | rw        | ro    | rw  |
| `schedule`           | rw        | ro    | rw  |
| `results`            | ro        | ro    | rw  |
| `rules`              | rw        | ro    | —   |

**Community** (fencer-facing interaction)

| Channel              | Organizer | Guest | Bot |
|----------------------|-----------|-------|-----|
| `general-chat`       | rw        | rw    | —   |
| `looking-for-sparring` | rw      | rw    | —   |
| `questions`          | rw        | rw    | ro  |

**Organization** (organisers-only)

| Channel              | Organizer | Guest | Bot |
|----------------------|-----------|-------|-----|
| `setup`              | rw        | —     | rw  |
| `org-internal`       | rw        | —     | —   |
| `org-results-upload` | rw        | —     | rw  |
| `bot-commands`       | rw        | —     | rw  |

(Specific names/channels are the proposed default; the layout lives in one Python module — see §4 — so it can evolve per tournament without scattered edits.)

### Invite links + QR codes

Two named, vanity-style invites, both `max_age=0`, `max_uses=0`, `unique=True`:

- **Organizer invite** → on join, member is granted the `Organizer` role.
- **Guest invite**     → on join, member is granted the `Guest` role.

The bot prints both invite URLs after setup and writes PNG QR codes alongside (e.g. `data/{tournament}/qr_organizer.png`, `data/{tournament}/qr_guest.png`) using the `qrcode` library. The PNGs can be printed and posted at the venue.

### Auto-role assignment on join

Mechanism (standard discord.py pattern):
1. On bot ready and on every `on_invite_create` / `on_invite_delete`, refresh an in-memory snapshot of `guild.invites()` keyed by code → uses.
2. On `on_member_join`, fetch the current invites; whichever code's `uses` incremented is the one the new member used.
3. Look up that code in a persisted mapping (`code → role_name`, written when the bot created the invite) and assign the role.

Edge cases:
- Vanity URL / unknown invite (e.g. someone pasted a third-party link): assign no role and DM the new member with instructions to ask staff.
- Bot was offline during a join: catch up at `on_ready` by listing members without any of the two roles and DM-prompting them.

### Idempotence

The setup routine is safe to re-run:
- Roles, categories, channels: matched by name; created if missing, **never duplicated**.
- Permission overrides: re-applied to match the spec exactly (drift-correcting).
- Invites: existing `Organizer` / `Guest` invites (by stored code) are kept; otherwise new ones are minted and persisted.

## 2. Setup channel & setup agent

The first thing an organiser uses on a freshly set-up server is the `setup` channel. It hosts a dedicated **run-setup agent** (analogous to the existing `src/pre_tournament/setup_agent/` used by `pre_bot`) that gathers tournament config through a short conversation, persists it to `user_config.json`, and is the canonical place to change those values later.

### Reuse from existing setup_agent

We copy the proven pattern wholesale rather than re-design. Reference template: `src/pre_tournament/setup_agent/setup_agent.py:1-60`.

- **Module layout**: `src/in_tournament/run_setup_agent/run_setup_agent.py` (mirrors `src/pre_tournament/setup_agent/setup_agent.py`).
- **Agent runtime**: pydantic-ai `Agent` with a `SetupDeps` dataclass (`guild`, `user_config_path`, `memory_path`); same framework imports (`pre_tournament.config.tracing.observe`, `pre_tournament.config.load_agent_config`, `discord_bot.discord_utils.send_long`, `pre_tournament.msgs.read_msg / render_msg`); same `MAX_HISTORY = 40` conversation cap; same `_slugify` helper; same `Agent.instrument_all()` tracing toggle.
- **Cog wiring**: a `SetupCog` mirroring `pre_bot.SetupCog` (`src/discord_bot/pre_bot.py:175-204`) — listens on `on_message`, ignores bot's own messages and slash commands, gates with a `_running: set[int]` per-channel guard, defers to `run_run_setup_agent(channel, message_content, user_config_path=...)`. Lives inside `src/discord_bot/run_bot.py`.
- **Welcome / info / complete messages** live as Markdown under `src/in_tournament/msgs/EN/run_setup/` and `src/in_tournament/msgs/CS/run_setup/` (`welcome.md`, `info.md`, `complete.md`), matching the layout of `src/pre_tournament/msgs/EN/setup/`. The pre-loaded welcome constant lives in `discord_bot.msg_constants` next to `SETUP_WELCOME`.
- **Conversation memory** persists in `data/{tournament}/run_setup_memory.md` (parallel to `pre_bot`'s `src/pre_tournament/config/setup_memory.md`) so agent restarts don't lose the running dialog.
- **Reading run_bot's own message tree**: `read_msg` / `render_msg` currently expect a single root (`pre_tournament/msgs/`). To read from `in_tournament/msgs/`, we either (a) extend the loader to accept a package-qualified prefix (`run_setup/welcome` from in_tournament), or (b) lift the loader into `shared/msgs/` and let it know about both roots. Decision tracked in §5 open questions.

### Initial dialogue

When the organiser first writes in `setup`, the agent walks them through these questions in order. Subsequent messages can re-open any topic at will.

1. **Language** — `EN` or `CS`. Sets `user_config.language`. All subsequent prompts and bot output switch to this language immediately.
2. **Tournament name** — free-text. Sets `user_config.tournament_name` and is used for the `data/{tournament}/` directory name (slugified).
3. **Disciplines** — list of disciplines run at the tournament. Same vocabulary as `pre_bot` (e.g. SA, LS, RD, MS); reuse `src/pre_tournament/msgs/EN/setup/discipline_reference.md` and `discipline_table.md` so the organiser sees the same canonical list.
4. **Discipline sizes** — for each discipline, the expected number of fencers (or upper limit if unsure). Sets `user_config.discipline_limits`.

These are the **minimum** to start phase 1. The agent's tool surface lets it edit any field of `user_config.json`, so further keys can be added (e.g. results-channel mappings, auto-approve toggles from §6.9, tiebreaker order from §6.6) without redesigning the channel — we just extend the system prompt and tool schema.

### Tool surface

The agent has tools to:

- `set_user_config(key, value)` — write/overwrite any field in `user_config.json` (validated against `RegUserConfig`).
- `get_user_config()` — read current values to ground its replies.
- `list_disciplines()` — return the canonical discipline catalogue from the shared markdown.

Mutations are atomic (write-then-rename) and trigger a hot reload of the bot's in-memory `RegConfig`, exactly as in the existing setup_agent.

### Slash commands (server-wide)

These slash commands are **not** tied to the setup channel — they live in the `GeneralCog` reused from `pre_bot`:

- `/setup` (Organizer-only): re-run server setup idempotently (§1).
- `/clear` (Organizer-only): channel-clear utility, copied from `pre_bot.GeneralCog` (`pre_bot.py:95-128`).

Results processing (image upload → parse → sheet → publish) is described in §6.

## 3. Independence from the rest of the codebase

`run_bot` does not import from any of `pre_tournament.reg_agent`, `pre_tournament.setup_agent`, `pre_tournament.pool_alch_agent`, `pre_tournament.payment_agent` — those are pre-bot's agents, not framework. Concretely the new bot may import:

- `discord_bot.discord_utils` — `send_long`, `make_table`.
- `discord_bot.msg_constants` — generic constants (`APP_NAME`).
- `pre_tournament.msgs` — `read_msg`, `render_msg` (i18n EN/CS). Treated as framework, not pre-bot agent code.
- `pre_tournament.config` — `load_config`, `RegConfig`, `load_agent_config`, `tracing.observe`. Same: framework.
- `shared.*` — `shared/typst/` for any rendered output (e.g. future bracket image).

`pre_bot.py`-specific code (the agent Cogs that delegate to pre-tournament agents, welcome-message constants tied to those channels, the `_acquire_instance_lock` helper) stays inside `pre_bot.py` or is hoisted into a clearly-named shared module **only when** `run_bot` actually needs it.

## 4. Configuration surface

Per-tournament knobs (organiser sees the channel names, language, etc.):

- The channel/role/permission layout itself lives in `src/in_tournament/server_layout.py` as plain dataclasses/dicts. Editing it is editing one file.
- Per-tournament values (server name, language, data dir) come from the existing `user_config.json` (same file `pre_bot` writes), loaded via `pre_tournament.config.load_config` → `RegConfig`. No second config file.
   - On disk: one `user_config.json` per tournament on the Fly volume of *each* bot (pre and run) — the Google Sheet is the cross-bot source of truth, but local config is a per-app copy seeded at deploy time and updated by whichever bot last touched it.
- run-bot-specific fields (e.g. results-channel mapping overrides, auto-approve toggle from §6.9, tiebreaker order from §6.6) extend `RegUserConfig` (`src/pre_tournament/config/agent_config.py`). Adding a field is a single Pydantic edit — no schema split needed.
- Welcome / announcement / DM message strings live under `src/in_tournament/msgs/EN/run_bot/` and `src/in_tournament/msgs/CS/run_bot/`, following the project rule that user-facing text is never hardcoded.
- Setup-agent message strings live under `src/in_tournament/msgs/{EN,CS}/run_setup/` (§2).

## 5. Decisions and deferred items

Decided:

- **Discord server**: `run_bot` runs on a **different** Discord server than `pre_bot`. Each tournament therefore has two servers — a prep server (pre_bot only) and a live server (run_bot only). No role/channel/permission collision between the two bots.
- **Phasing**: phase 1 = server setup + pool-stage results loop (§6.1–§6.6). Phase 2 = elimination bracket seeding (§6.7). Phase 3 (later) = LLM-backed Q&A on `questions`. The bracket and Q&A specs stay in this document but are out of scope for the first implementation.
- **Repository layout**: phase-split packages under `src/` — `pre_tournament/`, `in_tournament/`, `post_tournament/`, with `discord_bot/` (bot entry points + shared discord helpers) and `shared/` (cross-phase assets). `run_bot`'s home is `src/in_tournament/` for agents and `src/discord_bot/run_bot.py` for the entry point. See §0.

Deferred (later phases): Q&A LLM agent on `questions`, match-timer / bracket commands — explicitly out of scope for phase 1.

Open question — framework utilities placement:

- `pre_tournament.config` and `pre_tournament.msgs` are the loaders/utils both bots need. They are reasonable to keep where they are (and import from `in_tournament` as framework), but a cleaner long-term home would be `src/shared/config/` and `src/shared/msgs/` so the package name doesn't suggest a phase ownership. **Recommendation**: defer the move; for phase 1, `in_tournament` imports from `pre_tournament.config` / `pre_tournament.msgs`. Track moving them to `shared/` as a follow-up refactor once `post_tournament/` also wants them.

### Deployment — Fly.io options

`pre_bot` today is one Fly app per tournament (`<name>` from `deploy/<tournament>.conf`), one volume mounted at `/app/data`, secrets `DISCORD_TOKEN`, `GOOGLE_CREDS_B64`, `USER_CONFIG_B64`. Three workable shapes for `run_bot`, with trade-offs:

**A. Separate Fly app per tournament: `<name>-run` alongside `<name>` (recommended).**
   - Mirrors the existing one-app-per-tournament pattern. Each `deploy/<tournament>.conf` learns a second token (`DISCORD_TOKEN_RUN`) and the same `deploy.sh` deploys both apps from the same image.
   - Total isolation: each bot has its own process, its own volume, its own logs, its own restart cadence. `run_bot` can be deployed/restarted mid-tournament without touching `pre_bot`.
   - Data sharing: the **Google Sheet is already the source of truth** for the fencer roster and pool composition (`pre_bot` writes them there). `run_bot` reads from the sheet, so it does not need access to `pre_bot`'s volume — Fly volumes are per-machine and cannot be shared cross-app, but that's fine because we don't need to share them. Anything `run_bot` produces (parsed-pool JSON cache, QR PNGs) lives on its own small volume.
   - Cost: Fly's free / shared-cpu-1x tier covers idle Discord bots; doubling to two apps per tournament is negligible. Two sets of secrets per tournament is the only real ops overhead.

**B. Single Fly app, two processes (Fly process groups).**
   - One image, one volume, one `deploy.sh` invocation; `fly.toml` declares two processes (`pre`, `run`) each running the matching entry point. Different secrets are still needed per bot (each Discord bot has its own token) but they live on one app.
   - Pros: shared volume (read each other's files directly), one deploy unit, half the secrets surface area.
   - Cons: a bad redeploy restarts both bots simultaneously; logs are interleaved; the `_acquire_instance_lock` pattern stops being meaningful since they're inside one Fly machine; more complex `fly.toml` and entrypoint script. Volume sharing is a real benefit only if `run_bot` needs files `pre_bot` produced beyond the Google Sheet — which currently it doesn't.

**C. Single binary, `BOT_MODE=pre|run` env switch, two Fly apps.**
   - Same image, same Dockerfile, single entrypoint that dispatches on `BOT_MODE`. Two Fly apps each set `BOT_MODE` and the right token.
   - Effectively option A with a thinner deploy script. No real downside vs. A; saves one entry-point file at the cost of a tiny dispatcher.

**Recommendation: A (or A-with-a-dispatcher = C).** It matches the existing per-tournament Fly pattern, keeps each bot independently restartable, and avoids the volume-sharing argument that doesn't actually buy us anything (the Google Sheet is already the cross-bot bus). Open `<name>-run` Fly apps when `run_bot` is ready to ship; until then `pre_bot`'s deployment is unchanged.

## 6. Core: tournament-results communication

The live-tournament loop is built around scanned pool sheets. End-to-end flow:

### 6.1 Pool-result intake (image upload)

- Organisers photograph or scan each completed pool sheet and upload the image to `org-results-upload`.
- The bot detects image attachments, acknowledges receipt, and queues the image for parsing. One uploaded image = one pool.

### 6.2 Parse agent (vision LLM)

- A parse agent reads the image and extracts the pool's structured result:
  - Pool ID (which discipline + which pool number).
  - For each fencer: bout-by-bout score grid, total touches scored / received, victories, indicator (V/M, TS, TR, Ind).
- The agent **verifies the parse against the tournament structure** loaded from the existing `data/{tournament}/` artefacts (fencer roster from `fencers_deduped.json`, pool composition produced by `pool_alch_agent`). Checks:
  - Every name in the parsed sheet matches a fencer assigned to that pool.
  - Bout count and score grid shape match the expected pool size (3×3, 4×4, 5×5, 6×6).
  - Row/column symmetry: A-vs-B in row A equals A-vs-B in column B.
  - Sum of touches scored == sum of touches received.
- Output: a structured `PoolResult` plus a `confidence` verdict — **`auto-ok`** (all checks pass, parse looks clean) or **`needs-review`** (any check failed or the OCR was uncertain) with a reason list.

### 6.3 Sheet upload (organiser-facing)

The Google Sheet has **two worksheets per discipline** for the pool-results loop. Naming pattern: `{DISCIPLINE}_Pools_Upload` and `{DISCIPLINE}_Pools_Verified` (e.g. `SA_Pools_Upload` / `SA_Pools_Verified`, `LS_Pools_Upload` / `LS_Pools_Verified`). Discipline codes are the existing 2-letter codes already used by `pre_bot` (one tab per discipline in the tournament sheet).

- `{DISCIPLINE}_Pools_Upload` (write target for the bot): the parse agent appends one row per bout. The bot **only** writes to this sheet; it never edits rows once written.
- `{DISCIPLINE}_Pools_Verified` (read source for the bot): the human copies rows over from `Upload` after verifying / fixing them, and removes the leading `?` flag to publish.

The discipline is determined per uploaded image from the parsed `Pool` field (e.g. `Pool=SA-3` → discipline `SA`). One image therefore lands in exactly one `{DISCIPLINE}_Pools_Upload` worksheet. The bot creates the two worksheets per discipline on first use (idempotent).

Each row is one bout (not one pool — one row per fencer-pair):

```
Pool, Fencer1, Fencer2, Score1, Score2, R1, R2, Confidence
```

| Column                | Meaning                                                                                              |
| --------------------- |------------------------------------------------------------------------------------------------------|
| `Pool`                | Pool ID, e.g. `LS-3` (discipline + pool number).                                                     |
| `Fencer1`, `Fencer2`  | The two fencers in the bout (canonical names from `fencers_deduped.json`).                           |
| `Score1`, `Score2`    | Touches scored by each.                                                                              |
| `R1`, `R2`            | Outcome flags per fencer: `Win` win, `Loss` loss, `Draw` draw, `No` no result (bout did not happen). |
| `Confidence`          | The bot's verdict — see flag system below.                                                           |

Example row written by the bot:

```
1, John Dowe, Peter Snow, 5, 3, W, L, ??
```

**Confidence flag system (the `?` count is the signal):**

| Flag  | Meaning                                                                                      |
| ----- |----------------------------------------------------------------------------------------------|
| `?`   | OK result — parse looks clean, all checks pass. Human removes the `?` to publish.            |
| `??`  | Low-confidence parse — human review needed (OCR uncertain, single check failed).             |
| `???` | Strange / faulty / missing result — bot could not parse this bout reliably; fill in by hand. |

The bot writes the row with one of `?` / `??` / `???` depending on its verification outcome (§6.2). It never writes a flag-less row.

### 6.4 Human verification

- The organiser reads `{DISCIPLINE}_Pools_Upload`, fixes wrong scores / names, and **copies the row** into `{DISCIPLINE}_Pools_Verified`.
- On the verified sheet, the organiser **removes the `?` characters** from `Confidence` to mark the bout as verified. Any remaining `?` in the verified sheet means "still needs work" and the row is treated as not-yet-verified.
- **Publication trigger: a pool is published only when *all* its bouts are present in `{DISCIPLINE}_Pools_Verified` *and* all of them have an empty `Confidence` (no `?`).** A partially-verified pool stays unpublished — the bot does not post per-bout updates. The expected bout count per pool is known from the pool composition (`pool_alch_agent` artefacts in `data/{tournament}/`): a 4-fencer pool has 6 bouts, 5-fencer 10, 6-fencer 15, etc. The bot waits until the verified-bout count for that pool equals the expected count.
- Once a pool is published, it is considered locked — re-uploading the same image just appends new rows to the corresponding `Upload` sheet, which the human can ignore or re-verify.

### 6.5 Public publication of a fully-verified pool

- When a pool's bouts are all verified (§6.4), the bot composes a tidy fencer-facing summary and posts it as a single message to `results`:
  - Pool header (discipline, pool number).
  - The bout grid as a clean ASCII / Discord-embed table (reuse `discord_utils.make_table`).
  - Per-fencer ranking inside that pool with V, TS, TR, Ind.
- Announcement (one-liner) is mirrored to `announcements` so notifications fire for fencers.

### 6.6 Pool-stage final ranking

- Once **all** pools of a discipline are fully verified and published, the bot composes the discipline's overall pool-stage ranking by sorting fencers across pools by the standard HEMA criteria (V/M ratio, then Ind, then TS — exact tiebreakers configurable per tournament in §4).
- The ranking is posted to `results` (full table) and to `announcements` (top-N + "see #results"), and written to a `{DISCIPLINE}_Pool_Ranking` worksheet.

### 6.7 Elimination bracket seeding *(phase 2 — deferred)*

Out of scope for the first implementation; captured here so the data model in phase 1 doesn't paint us into a corner.

- Using the pool-stage ranking and the tournament's elimination structure (bracket size, single/double elimination, BYEs — declared in `user_config.json` per discipline), the bot **populates the elimination bracket**:
  - Standard snake-seeding from the ranking (1 vs 16, 8 vs 9, …) unless the tournament config says otherwise.
  - Writes the populated bracket to a `Bracket` tab of the Google Sheet.
  - Posts a rendered bracket image (Typst → PNG, reusing the existing `src/shared/typst/` rendering pipeline used by `pre_bot`) to `results` plus a one-liner to `announcements`.

### 6.8 State and idempotence

- All durable state (parsed pools, confidence verdicts, approval status, rankings, bracket) lives in the Google Sheet + `data/{tournament}/run_bot/` JSON cache. Re-running any step is safe: a re-uploaded image overwrites only that pool's row; recomputing the ranking re-reads only `approved` rows.

### 6.9 Open implementation questions

- Polling interval vs. push trigger for sheet `Status` flips (default proposal: poll every 30 s + slash command `/refresh`).
- Auto-approve threshold for `auto-ok` parses — opt-in per tournament; default **off** (everything needs human eyeballs at first).
- Whether to also DM affected fencers when their pool is published — default **off**, organiser-toggleable.

