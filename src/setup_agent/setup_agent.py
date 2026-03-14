"""Setup agent — guides the tournament organiser through initial configuration.

Runs in the Discord #setup channel. Collects language preference, tournament name,
and disciplines, then persists them to user_config.json.
"""

import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

# Ensure src/ is on sys.path.
_SRC = Path(__file__).parent.parent  # src/
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import discord
from config.tracing import observe, enabled as _tracing_enabled
from pydantic_ai import Agent, RunContext

from config import load_agent_config
from table2ascii import Alignment, PresetStyle, table2ascii

from discord_bot.discord_utils import send_long
from discord_bot.msg_constants import REGISTRATION_CHANEL_NAME, REGISTRATION_WELCOME, SETUP_COMPLETE, SETUP_INFO

log = logging.getLogger(__name__)

_DEFAULT_USER_CONFIG = _SRC / "config" / "user_config.json"
SHARED_MEMORY_PATH = _SRC / "config" / "setup_memory.md"
_DEFAULT_MEMORY = SHARED_MEMORY_PATH
MAX_HISTORY = 40

_DISCIPLINE_REFERENCE = """\
Discipline codes are composed of three parts:

Weapon (required):
  LS  — Longsword
  SA  — Sabre
  RA  — Rapier
  RD  — Rapier & Dagger
  SB  — Sword & Buckler

Gender suffix (optional, appended to weapon code):
  M   — Men only
  W   — Women only
  (none) — Open (default)

Material prefix (optional, prepended with a space):
  "Plastic " — plastic weapons
  (none)     — steel (default)

Examples:
  LS         → Steel Longsword Open
  LSW        → Steel Longsword Women
  LSM        → Steel Longsword Men
  SA         → Steel Sabre Open
  SAW        → Steel Sabre Women
  Plastic SA → Plastic Sabre Open
  RD         → Steel Rapier & Dagger Open

The config value for each code is a human-readable description, e.g.:
  {{"LS": "Longsword Open", "LSW": "Longsword Women", "Plastic SA": "Plastic Sabre Open"}}"""

_SYSTEM_PROMPT = """\
You are the HEMA Tournament Setup Agent running inside a Discord #setup channel.
Your goal is to guide the tournament organiser through initial configuration,
one step at a time. Never skip steps or combine multiple steps in a single turn.

## Steps (always in this order)

1. **Welcome** — Post a warm welcome message explaining what you will configure together.
   Then ask the organiser for their **preferred language**.

2. **Language** — Once the organiser provides their language:
   - Detect the ISO 639-1 language code (e.g. "EN", "CS", "DE"). Supported languages with pre-built
     messages: {supported_languages}. Any other code is also valid.
   - Call save_language with the detected code.
   - From this point on, use only that language in messages to the organiser.
   - save_language returns a pre-built message. If the organiser's language has a dedicated
     constant it will already be in the correct language — return it verbatim as your output.
     Otherwise the returned text is English — translate it to the organiser's language first,
     then return it as your output.

3. **Tournament name** — Once provided:
   - Call store_memory to record the tournament name.
   - Call init_data_dir with the tournament name.
   - Ask what **disciplines** will be held at the tournament (do not
     mention codes or the internal system).

4. **Disciplines** — The organiser describes disciplines. You internally
   map them to discipline codes using the reference below, then call format_table with a
   pipe-separated CSV to produce a confirmation table (use user language):

     Code | Discipline
     LS   | Longsword Open
     SAW  | Sabre Women

   Paste the exact return value of format_table verbatim into your output (it is a Discord
   code block — do not paraphrase, summarise, or omit it), then ask the organiser to confirm
   or correct it.
   Once confirmed:
   - Call save_disciplines with the collected dict (code → human-readable description).
   - Call finish_setup to create remaining channels and finalise configuration.
   - Return the result of finish_setup verbatim as your output — do not paraphrase or add to it, unless anything is factually wrong.

## Discipline code reference (internal — never expose this to the organiser)

{discipline_reference}

## Maintenance requests (outside the normal setup flow)
If the organiser reports that the #{registration_channel} channel is missing or was deleted,
call recreate_registration_channel immediately — no confirmation needed.

## Rules
- Run exactly ONE step per turn, then stop and wait for the organiser.
- Always communicate in the organiser's preferred language (stored in memory).
- Your text output is posted directly to the Discord channel — do not use any tool to send messages.
- After each tool call, briefly confirm what was saved and what comes next in your output.

## Organiser memory
{memory}
"""


@dataclass
class SetupDeps:
    guild: discord.Guild
    user_config_path: Path
    memory_path: Path


if _tracing_enabled:
    Agent.instrument_all()

setup_agent = Agent(
    "anthropic:claude-sonnet-4-6",
    deps_type=SetupDeps,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _read_memory(path: Path) -> str:
    return path.read_text().strip() if path.exists() else "(empty)"


def _append_memory(path: Path, fact: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    with path.open("a") as f:
        f.write(f"- [{ts}] {fact}\n")


def _update_user_config(path: Path, updates: dict) -> None:
    existing: dict = {}
    if path.exists():
        try:
            with open(path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass  # treat corrupted/empty file as empty config
    existing.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


# ── System prompt ──────────────────────────────────────────────────────────────

@setup_agent.system_prompt
def _system_prompt(ctx: RunContext[SetupDeps]) -> str:
    return _SYSTEM_PROMPT.format(
        discipline_reference=_DISCIPLINE_REFERENCE,
        memory=_read_memory(ctx.deps.memory_path),
        supported_languages=", ".join(SETUP_INFO.keys()),
        registration_channel=REGISTRATION_CHANEL_NAME,
    )


# ── Tools ──────────────────────────────────────────────────────────────────────

@setup_agent.tool
def store_memory(ctx: RunContext[SetupDeps], fact: str) -> str:
    """Persist a fact to the setup memory file."""
    _append_memory(ctx.deps.memory_path, fact)
    return "stored"


@setup_agent.tool
def read_memory(ctx: RunContext[SetupDeps]) -> str:
    """Return the full contents of the setup memory file."""
    return _read_memory(ctx.deps.memory_path)


_ALIGNMENT_MAP = {"l": Alignment.LEFT, "c": Alignment.CENTER, "r": Alignment.RIGHT}


@setup_agent.tool
def format_table(ctx: RunContext[SetupDeps], csv: str, alignments: list[str] | None = None) -> str:
    """Format a pipe-separated CSV string into a pretty ASCII table for Discord.

    The first row is treated as the header. Columns are separated by '|'.
    Returns the table wrapped in a Discord code block.

    alignments: optional list of per-column alignment codes — 'l' (left), 'c' (center),
        'r' (right). Defaults to left-aligned for all columns.

    Example input:
        Code | Discipline
        LS   | Longsword Open
        SAW  | Sabre Women
    """
    rows = [
        [cell.strip() for cell in line.split("|")]
        for line in csv.strip().splitlines()
        if line.strip()
    ]
    if not rows:
        return "(empty table)"
    header, body = rows[0], rows[1:]
    col_alignments = [_ALIGNMENT_MAP.get(a, Alignment.LEFT) for a in (alignments or [])]
    if len(col_alignments) < len(header):
        col_alignments += [Alignment.LEFT] * (len(header) - len(col_alignments))
    try:
        table = table2ascii(header=header, body=body, style=PresetStyle.thin_compact, alignments=col_alignments)
    except Exception as e:
        return f"error formatting table: {e}"
    return f"```\n{table}\n```"


@setup_agent.tool
def save_language(ctx: RunContext[SetupDeps], language_code: str) -> str:
    """Save the organiser's preferred language to memory and user config.

    language_code: uppercase ISO 639-1 code, e.g. "EN", "CS", "DE".

    Returns the pre-built welcome/info message for that language if one exists,
    or the English version as fallback.
    """
    code = language_code.upper()
    _append_memory(
        ctx.deps.memory_path,
        f"Preferred language: {code}. ALWAYS use {code} when writing messages to the organiser. "
        "Internal reasoning, tool call arguments, and all other agent outputs remain in English.",
    )
    _update_user_config(ctx.deps.user_config_path, {"language": code})
    log.info("Language set to: %s", code)
    return SETUP_INFO.get(code, SETUP_INFO["EN"])


@setup_agent.tool
def init_data_dir(ctx: RunContext[SetupDeps], tournament_name: str) -> str:
    """Create the tournament data directory and save tournament_name to user config.

    The directory is created as <data_root>/<slug> where slug is the tournament name
    lowercased and with non-alphanumeric characters replaced by underscores.
    Returns the created path or an error message.
    """
    try:
        agent_config = load_agent_config()
        data_root = agent_config.reg_agent.data_root_dir
        slug = _slugify(tournament_name)
        data_dir = Path(data_root) / slug
        data_dir.mkdir(parents=True, exist_ok=True)
        _update_user_config(ctx.deps.user_config_path, {"tournament_name": slug})
        log.info("Created data dir: %s", data_dir)
        return f"Created data directory: {data_dir}"
    except Exception as e:
        return f"error: {e}"


@setup_agent.tool
def save_disciplines(ctx: RunContext[SetupDeps], disciplines: dict[str, str]) -> str:
    """Save tournament disciplines to user config.

    disciplines: dict mapping discipline code to human-readable description,
    e.g. {"LS": "Longsword Open", "LSW": "Longsword Women"}.
    """
    try:
        _update_user_config(ctx.deps.user_config_path, {"disciplines": disciplines})
        log.info("Saved disciplines: %s", list(disciplines.keys()))
        return f"Saved {len(disciplines)} discipline(s): {', '.join(disciplines.keys())}"
    except Exception as e:
        return f"error: {e}"


@setup_agent.tool
async def finish_setup(ctx: RunContext[SetupDeps]) -> str:
    """Finalise setup: create additional channels and perform any post-setup actions.

    Call this once after save_disciplines is confirmed by the organiser.
    Returns a summary of what was created, or an error message.
    """
    try:
        existing = discord.utils.get(ctx.deps.guild.text_channels, name=REGISTRATION_CHANEL_NAME)
        if existing is not None:
            return f"#{REGISTRATION_CHANEL_NAME} already exists."
        config: dict = {}
        if ctx.deps.user_config_path.exists():
            try:
                with open(ctx.deps.user_config_path) as f:
                    config = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass
        lang = config.get("language", "EN")

        ch = await ctx.deps.guild.create_text_channel(REGISTRATION_CHANEL_NAME)
        welcome = REGISTRATION_WELCOME.get(lang, REGISTRATION_WELCOME["EN"])
        await send_long(ch, welcome)
        log.info("Created #%s in %s", REGISTRATION_CHANEL_NAME, ctx.deps.guild)
        return SETUP_COMPLETE.get(lang, SETUP_COMPLETE["EN"])
    except Exception as e:
        return f"error: {e}"


@setup_agent.tool
async def recreate_registration_channel(ctx: RunContext[SetupDeps]) -> str:
    """Recreate the registration channel if it was deleted.

    Safe to call at any time — skips creation if the channel already exists.
    Returns a status message.
    """
    try:
        existing = discord.utils.get(ctx.deps.guild.text_channels, name=REGISTRATION_CHANEL_NAME)
        if existing is not None:
            return f"#{REGISTRATION_CHANEL_NAME} already exists, nothing to do."
        config: dict = {}
        if ctx.deps.user_config_path.exists():
            try:
                with open(ctx.deps.user_config_path) as f:
                    config = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass
        lang = config.get("language", "EN")
        ch = await ctx.deps.guild.create_text_channel(REGISTRATION_CHANEL_NAME)
        welcome = REGISTRATION_WELCOME.get(lang, REGISTRATION_WELCOME["EN"])
        await send_long(ch, welcome)
        log.info("Recreated #%s in %s", REGISTRATION_CHANEL_NAME, ctx.deps.guild)
        return SETUP_COMPLETE.get(lang, SETUP_COMPLETE["EN"])
    except Exception as e:
        return f"error: {e}"


# ── Entry point ────────────────────────────────────────────────────────────────

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


@observe(capture_input=False, capture_output=False)
async def run_setup_agent(
    channel: discord.TextChannel,
    new_message_content: str,
    user_config_path: Path = _DEFAULT_USER_CONFIG,
    memory_path: Path = _DEFAULT_MEMORY,
) -> None:
    """Run one setup agent turn: read channel history, decide next action, post response."""
    deps = SetupDeps(
        guild=channel.guild,
        user_config_path=user_config_path,
        memory_path=memory_path,
    )
    prompt = await _build_prompt(channel, new_message_content)
    try:
        result = await setup_agent.run(prompt, deps=deps)
        if result.output and result.output.strip():
            await send_long(channel, result.output)
    except Exception:
        log.exception("Setup agent run failed")
        await channel.send("⚠ Internal error — check logs.")
