"""Run-bot setup agent — guides the organiser through live-tournament configuration.

Runs in the Discord #setup channel of the live-tournament server. Collects
language, tournament name, disciplines, and expected discipline sizes, and
persists them to user_config.json.

Mirror of pre_tournament.setup_agent.setup_agent, adapted for the
live-tournament workflow:

- The Discord server (roles, channels, permissions, invites) is provisioned
  by `/setup` (see in_tournament.setup) before this agent runs — this agent
  does NOT create channels.
- finish_setup is a no-op confirmation; nothing else needs to be created.
"""

import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

# Push src/ onto sys.path so `shared.*` and sibling phase packages resolve
# when this module is run as a script.
_SRC_ROOT = Path(__file__).parent.parent.parent  # src/
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import discord
from pydantic_ai import Agent, RunContext
from table2ascii import Alignment, PresetStyle, table2ascii

from shared.config import load_agent_config
from shared.config.tracing import enabled as _tracing_enabled, observe

from discord_bot.discord_utils import send_long
from in_tournament.msgs import read_msg as _read_msg, render_msg as _render_msg

log = logging.getLogger(__name__)

_DEFAULT_USER_CONFIG = _SRC_ROOT / "in_tournament" / "config" / "in_user_config.json"
SHARED_MEMORY_PATH = Path(__file__).parent / "run_setup_memory.md"
_DEFAULT_MEMORY = SHARED_MEMORY_PATH
MAX_HISTORY = 40

# Pre-loaded language-keyed message constants, mirroring pre_tournament.
# Add a new language by dropping `<lang>/run_setup/{info,complete}.md` files.
_SUPPORTED_LANGS = ("EN", "CS")
RUN_SETUP_INFO     = {lang: _read_msg("run_setup/info", lang)     for lang in _SUPPORTED_LANGS}
RUN_SETUP_COMPLETE = {lang: _read_msg("run_setup/complete", lang) for lang in _SUPPORTED_LANGS}
RUN_SETUP_WELCOME  = _read_msg("run_setup/welcome")


@dataclass
class SetupDeps:
    guild: discord.Guild
    user_config_path: Path
    memory_path: Path
    data_root: Path


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
            pass  # corrupted/empty file → start fresh
    existing.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


# ── System prompt ──────────────────────────────────────────────────────────────

@setup_agent.system_prompt
def _system_prompt(ctx: RunContext[SetupDeps]) -> str:
    return _render_msg("run_setup/system_prompt", {
        "discipline_reference": _read_msg("run_setup/discipline_reference"),
        "discipline_table": _read_msg("run_setup/discipline_table", _memory_language(ctx.deps.memory_path)),
        "memory": _read_memory(ctx.deps.memory_path),
        "supported_languages": ", ".join(RUN_SETUP_INFO.keys()),
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

    Returns the pre-built info message for that language if one exists,
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
    return RUN_SETUP_INFO.get(code, RUN_SETUP_INFO["EN"])


@setup_agent.tool
def init_data_dir(ctx: RunContext[SetupDeps], tournament_name: str) -> str:
    """Create the tournament data directory and save tournament_name to user config.

    The directory is created as <data_root>/<slug> where slug is the tournament name
    lowercased and with non-alphanumeric characters replaced by underscores.
    """
    try:
        agent_config = load_agent_config()
        data_root = agent_config.reg_agent.data_root_dir
        slug = _slugify(tournament_name)
        data_dir = Path(data_root) / slug
        data_dir.mkdir(parents=True, exist_ok=True)
        _update_user_config(ctx.deps.user_config_path, {
            "tournament_name": slug,
            "tournament_display_name": tournament_name,
        })
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
    """Save per-discipline expected/maximum participant counts to user config.

    limits: dict mapping discipline code to expected fencer count,
    e.g. {"LS": 32, "LSW": 16}.
    """
    try:
        _update_user_config(ctx.deps.user_config_path, {"discipline_limits": limits})
        log.info("Saved discipline limits: %s", limits)
        return f"Saved counts: {', '.join(f'{k}={v}' for k, v in limits.items())}"
    except Exception as e:
        return f"error: {e}"


@setup_agent.tool
async def publish_invite_links(ctx: RunContext[SetupDeps]) -> str:
    """Post QR codes and invite links to the right channels.

    Posts the Organizer invite to #org-internal and the Guest invite to #announcements,
    labelled with the tournament name. Call this before finish_setup.
    """
    from in_tournament.setup import load_invite_map, run_bot_data_dir
    from in_tournament.server_layout import (
        ANNOUNCEMENTS_CHANNEL, ORG_INTERNAL_CHANNEL, ROLE_GUEST, ROLE_ORGANIZER,
    )

    config: dict = {}
    if ctx.deps.user_config_path.exists():
        try:
            with open(ctx.deps.user_config_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass

    lang = config.get("language", "EN")
    display_name = config.get("tournament_display_name") or config.get("tournament_name", "")

    data_dir = run_bot_data_dir(ctx.deps.data_root, ctx.deps.guild.id)
    invite_map = load_invite_map(data_dir)          # {code: role_name}
    role_to_code = {v: k for k, v in invite_map.items()}

    posted: list[str] = []
    for role_name, channel_name, msg_key in [
        (ROLE_ORGANIZER, ORG_INTERNAL_CHANNEL, "setup/org_invite"),
        (ROLE_GUEST,     ANNOUNCEMENTS_CHANNEL, "setup/guest_invite"),
    ]:
        code = role_to_code.get(role_name)
        if code is None:
            continue
        ch = discord.utils.get(ctx.deps.guild.text_channels, name=channel_name)
        if ch is None:
            continue
        url = f"https://discord.gg/{code}"
        qr_path = data_dir / f"qr_{role_name.lower()}.png"
        text = _render_msg(msg_key, {"url": url, "tournament_name": display_name}, lang)
        try:
            if qr_path.exists():
                await ch.send(text, file=discord.File(qr_path, filename=qr_path.name))
            else:
                await ch.send(text)
        except discord.Forbidden:
            log.warning("Missing send permission on #%s — posting text only", channel_name)
            try:
                await ch.send(text)
            except discord.Forbidden:
                log.error("No send permission at all on #%s — skipping", channel_name)
                continue
        posted.append(f"#{channel_name}")

    if posted:
        return f"Posted invite links to: {', '.join(posted)}"
    return "No invite links found — run /setup first to generate invites."


@setup_agent.tool
def finish_setup(ctx: RunContext[SetupDeps]) -> str:
    """Finalise setup. The Discord server itself is already provisioned by `/setup` —
    this just returns the completion message in the organiser's language.
    """
    config: dict = {}
    if ctx.deps.user_config_path.exists():
        try:
            with open(ctx.deps.user_config_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    lang = config.get("language", "EN")
    return RUN_SETUP_COMPLETE.get(lang, RUN_SETUP_COMPLETE["EN"])


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
async def run_run_setup_agent(
    channel: discord.TextChannel,
    new_message_content: str,
    user_config_path: Path = _DEFAULT_USER_CONFIG,
    memory_path: Path = _DEFAULT_MEMORY,
    data_root: Path | None = None,
) -> None:
    """Run one setup-agent turn: read history, decide next action, post response."""
    if data_root is None:
        data_root = Path(load_agent_config().reg_agent.data_root_dir)
    deps = SetupDeps(
        guild=channel.guild,
        user_config_path=user_config_path,
        memory_path=memory_path,
        data_root=data_root,
    )
    prompt = await _build_prompt(channel, new_message_content)
    try:
        result = await setup_agent.run(prompt, deps=deps)
        if result.output and result.output.strip():
            await send_long(channel, result.output)
    except Exception:
        log.exception("Run-setup agent run failed")
        await channel.send("⚠ Internal error — check logs.")
