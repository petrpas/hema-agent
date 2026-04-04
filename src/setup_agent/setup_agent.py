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
from msgs import render_msg as _render_msg, read_msg as _read_msg

log = logging.getLogger(__name__)

_DEFAULT_USER_CONFIG = _SRC / "config" / "user_config.json"
SHARED_MEMORY_PATH = _SRC / "config" / "setup_memory.md"
_DEFAULT_MEMORY = SHARED_MEMORY_PATH
MAX_HISTORY = 40



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


def _memory_language(path: Path) -> str:
    """Extract the saved language code from memory, defaulting to EN."""
    import re
    memory = _read_memory(path)
    m = re.search(r"Preferred language:\s*([A-Z]{2,5})", memory)
    return m.group(1) if m else "EN"


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
    return _render_msg("setup/system_prompt", {
        "discipline_reference": _read_msg("setup/discipline_reference"),
        "discipline_table": _read_msg("setup/discipline_table", _memory_language(ctx.deps.memory_path)),
        "memory": _read_memory(ctx.deps.memory_path),
        "supported_languages": ", ".join(SETUP_INFO.keys()),
        "registration_channel": REGISTRATION_CHANEL_NAME,
    })


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
def save_discipline_limits(ctx: RunContext[SetupDeps], limits: dict[str, int]) -> str:
    """Save per-discipline participant capacity limits to user config.

    limits: dict mapping discipline code to maximum number of accepted fencers,
    e.g. {"LS": 32, "LSW": 16}.
    """
    try:
        _update_user_config(ctx.deps.user_config_path, {"discipline_limits": limits})
        log.info("Saved discipline limits: %s", limits)
        return f"Saved limits: {', '.join(f'{k}={v}' for k, v in limits.items())}"
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
