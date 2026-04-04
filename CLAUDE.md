# HEMA Agent — Project Guide for Claude

## Code style

- **Use f-strings** for all regular string formatting. Never use `%`-operator formatting (`"hello %s" % name`) or `.format()`.
- **Exception — logging calls:** `log.info("msg %s", val)` must keep `%`-style lazy args. Converting these to f-strings causes unnecessary interpolation when the log level is inactive.
- **Exception — strftime / logging.basicConfig format strings:** these use `%` as part of their own mini-language, not Python string formatting. Leave them as-is.

---

## What this project is

A Discord bot that assists HEMA tournament organisers. It runs as a long-lived process on Fly.io —
one Fly app per tournament. Each app has its own Discord bot token and persistent volume.

---

## Repository layout

```
src/
  config/               — Config models (RegConfig, AgentConfig) and loaders
  discord_bot/
    bot.py              — Bot entry point; one Cog per agent channel
    msg_constants.py    — Channel names, and pre-loaded message constants
    discord_utils.py    — send_long() helper
  msgs/                 — All user-facing text and LLM prompts (see section below)
  reg_agent/            — Registration enrichment pipeline (steps 1–7 + payments)
  setup_agent/          — Tournament setup wizard
  pool_alch_agent/      — Pool assignment designer (solver + LLM agent)

deploy/                 — Fly.io deployment scripts and templates
typst/                  — Typst templates and fonts for PNG rendering
data/                   — Runtime data dir (gitignored); persisted on Fly volume at /app/data
```

---

## Adding a new agent

1. Create `src/<name>_agent/` with at minimum:
   - `models.py` — Pydantic/dataclass models
   - `<name>_agent.py` — pydantic-ai `Agent`, `Deps` dataclass, tools, `run_<name>_agent()` async fn
2. Add a channel name constant to `discord_bot/msg_constants.py`.
3. Add a `<Name>Cog` to `discord_bot/bot.py` and register it in `setup_hook()`.
4. Put all prompts and messages in `src/msgs/` (see below).

---

## src/msgs/ — message and prompt organisation

All user-facing text and LLM system prompts live here as `.md` files, never hardcoded in Python.

**Structure:** `src/msgs/{LANG}/{agent}/filename.md`

| Folder | Contents |
|---|---|
| `EN/reg/` | reg_agent prompts, step prompts, match tables, welcome messages |
| `EN/setup/` | setup_agent system prompt, info, discipline reference |
| `EN/pool_alch/` | pool_alch_agent system prompt |
| `EN/shared/` | Sheet-related messages used by multiple agents |
| `CS/{agent}/` | Czech translations — same filenames, fall back to EN if missing |

**Usage:**
```python
from msgs import read_msg, render_msg

read_msg("reg/system_prompt")               # plain text
read_msg("setup/info", lang)                # with language fallback
render_msg("shared/sheet_access_request", {"bot_email": email}, lang)  # Jinja2 template
```

**Rules:**
- Every new agent gets its own subfolder: `EN/<agent_name>/`.
- Strip redundant prefixes from filenames — `setup/info.md` not `setup/setup_info.md`.
- LLM system prompts go in `<agent>/system_prompt.md`.
- Only EN is required; add CS translations where the organiser will see the text directly.
- Jinja2 templating is available (`{{ variable }}`).

---

## Config

Two JSON files merged at runtime into `RegConfig`:

- `src/config/agent_config.json` — system settings (model names, paths, thinking tokens); committed
- `user_config.json` — tournament-specific (name, disciplines, sheet URL, language); gitignored, lives on Fly volume

Load with:
```python
from config import load_config
config = load_config(user_config_path)   # RegConfig
```

---

## Discord channels → Cogs

| Channel | Cog | Agent |
|---|---|---|
| `hsq-setup` | `SetupCog` | `setup_agent` |
| `hsq-registrations` | `RegistrationCog` | `reg_agent` |
| `hsq-pools-alchemy` | `PoolsCog` | `pool_alch_agent` |

Each Cog listens for text messages on its channel and delegates to `run_<agent>()`.
Cogs use a `_running: set[int]` guard to prevent concurrent turns per channel.

---

## Data persistence

The Fly volume is mounted at `/app/data`. Each tournament gets a subdirectory:
`/app/data/{tournament_name}/`

Key files written by reg_agent:
- `fencers_parsed.json`, `fencers_matched.json`, `fencers_deduped.json`
- `ratings_YYYY-MM-DD.json`
- `withdrawn.json`
- `user_config.json` (written by setup_agent)

Load/save helpers live in `reg_agent/utils.py` (`load_fencers_list`, `save_fencers_list`, `load_ratings`, etc.).

---

## Google Sheets

The output sheet is a Google Spreadsheet with one tab per discipline (e.g. `SA`, `LS`) plus a `Fencers` tab.
Discipline tab columns: `No. | Name | Nat. | Club | HR_ID | HRating | HRank | Seed`

Access via gspread service account (`config.creds_path`), opened by URL (`config.output_sheet_url`).

---

## Deployment

Each tournament is a separate Fly app:
```bash
./deploy/deploy.sh deploy/<tournament>.conf
```

See `deploy/README.md` for full instructions including Discord bot setup and OAuth invite URL.
