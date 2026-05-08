"""Discord bot for the live-tournament phase of a HEMA tournament.

Independent from `pre_bot.py` (pre-tournament prep). Shares only generic
helpers in `discord_bot/` and framework utilities in `shared/`.

Slice 1 (this file): bot wiring + GeneralCog (/clear, /sync). The setup
module, setup-agent cog, and results-loop cog land in later slices.
"""

# Load .env before any other import so env-dependent module-level code
# (e.g. shared.config.tracing.enabled) sees the correct values.
from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Make `shared.*`, `pre_tournament.*`, `in_tournament.*` importable when
# this module is run directly from src/discord_bot/.
_SRC_ROOT = Path(__file__).parent.parent
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.instance_lock import acquire_instance_lock
from shared.config import load_agent_config
from in_tournament.config import load_in_config, InConfig

from discord_bot.discord_utils import send_long
from in_tournament.run_setup_agent.run_setup_agent import (
    DISCIPLINE_NAMES,
    SHARED_MEMORY_PATH,
    _DEFAULT_USER_CONFIG,
    _append_memory,
    _do_init_data_dir,
    _do_save_disciplines,
    _do_save_language,
    _do_validate_disciplines,
    RUN_SETUP_WELCOME,
    run_run_setup_agent,
)
from in_tournament.server_layout import ROLES, SETUP_CHANNEL
from table2ascii import Alignment, PresetStyle, table2ascii
from in_tournament.setup import (
    InviteSnapshot,
    assign_role_for_invite,
    detect_used_code,
    load_invite_map,
    run_bot_data_dir,
    setup_server,
)

log = logging.getLogger(__name__)


class HemaTournamentRunBot(commands.Bot):
    """Live-tournament bot. Provisions the server, processes pool results,
    publishes them to fencer-facing channels.
    """

    config: InConfig | None = None

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.members = True  # required for on_member_join + role assignment
        intents.invites = True  # required for invite-uses tracking
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        user_config_path = os.environ.get("USER_CONFIG")
        try:
            self.config = load_in_config(user_config_path)
            log.info("Loaded config: tournament=%s", self.config.tournament_name)
        except Exception as e:
            log.warning("Could not load config (USER_CONFIG=%s): %s", user_config_path, e)

        await self.add_cog(GeneralCog(self))
        await self.add_cog(ServerSetupCog(self))
        await self.add_cog(SetupAgentCog(self))
        await self.add_cog(SetupCommandsCog(self))
        # ResultsCog will be added in the next slice.

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Logged in as %s (ID: %d)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="HEMA tournaments"
            )
        )
        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash command tree synced to guild %s (%d)", guild.name, guild.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash command tree synced to new guild %s (%d)", guild.name, guild.id)


class GeneralCog(commands.Cog):
    """Commands available in every channel."""

    def __init__(self, bot: HemaTournamentRunBot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="List all available bot commands")
    async def help(self, interaction: discord.Interaction) -> None:
        msg = (
            "**HEMA Tournament Bot — commands**\n"
            "\n"
            "**Server**\n"
            "`/setup` — Provision roles, channels, permissions and invite links *(manage_guild)*\n"
            "\n"
            "**Setup wizard shortcuts** *(manage_guild — use in #setup)*\n"
            "`/set_language <code>` — Set organiser language (EN, CS, DE, …)\n"
            "`/set_name <name>` — Set tournament display name\n"
            "`/set_disciplines <codes>` — Set disciplines as comma-separated codes, e.g. `LS,SAW,SB`\n"
            "\n"
            "**Validation** *(manage_guild)*\n"
            "`/validate_disc [disciplines]` — Check pool sheets against the tournament roster. "
            "Omit `disciplines` to validate all configured disciplines.\n"
            "\n"
            "**Moderation**\n"
            "`/clear` — Delete all messages in this channel except the first *(manage_messages)*\n"
            "\n"
            "You can also type freely in **#setup** to configure the tournament step by step "
            "with the AI assistant."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="clear",
        description="Remove all messages except the welcome message",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command only works in text channels.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        first_msg: discord.Message | None = None
        async for msg in channel.history(limit=1, oldest_first=True):
            first_msg = msg

        to_delete: list[discord.Message] = []
        async for msg in channel.history(limit=None):
            if first_msg is None or msg.id != first_msg.id:
                to_delete.append(msg)

        for i in range(0, len(to_delete), 100):
            await channel.delete_messages(to_delete[i : i + 100])

        count = len(to_delete)
        await interaction.followup.send(f"Cleared {count} message(s).", ephemeral=True)
        log.info("Cleared %d messages in #%s (%s)", count, channel.name, interaction.guild)

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync(self, ctx: commands.Context) -> None:
        """Sync slash commands to this guild instantly (owner only, use !sync)."""
        await self.bot.tree.sync(guild=ctx.guild)
        await ctx.send("Slash commands synced to this server.", delete_after=5)
        log.info("Tree synced to guild %s by owner", ctx.guild)


class ServerSetupCog(commands.Cog):
    """Owns `/setup` and the auto-role-on-join machinery."""

    def __init__(self, bot: HemaTournamentRunBot) -> None:
        self.bot = bot
        # Per-guild caches keyed by guild_id.
        self._invite_snapshots: dict[int, InviteSnapshot] = {}
        self._invite_maps: dict[int, dict[str, str]] = {}  # code → role name

    def _data_root(self) -> Path:
        if self.bot.config is not None:
            return Path(self.bot.config.data_root_dir)
        return Path(load_agent_config().reg_agent.data_root_dir)

    async def _refresh_invites(self, guild: discord.Guild) -> None:
        self._invite_snapshots[guild.id] = await InviteSnapshot.from_guild(guild)
        data_dir = run_bot_data_dir(self._data_root(), guild.id)
        self._invite_maps[guild.id] = load_invite_map(data_dir)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._refresh_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            await self._refresh_invites(invite.guild)  # type: ignore[arg-type]

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            await self._refresh_invites(invite.guild)  # type: ignore[arg-type]

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        prev = self._invite_snapshots.get(guild.id)
        if prev is None:
            await self._refresh_invites(guild)
            await self._dm_unknown(member)
            return

        code, fresh = await detect_used_code(guild, prev)
        self._invite_snapshots[guild.id] = fresh

        invite_map = self._invite_maps.get(guild.id, {})
        role_map = {r.name: r for r in guild.roles}
        assigned = await assign_role_for_invite(member, code, invite_map, role_map)
        if assigned is None:
            await self._dm_unknown(member)
            log.info("New member %s in %s: no auto-role assignment", member, guild.name)
        else:
            log.info("Auto-assigned role %s to %s in %s", assigned, member, guild.name)

    async def _dm_unknown(self, member: discord.Member) -> None:
        try:
            await member.send(
                "Welcome! I couldn't determine which role to assign you. "
                "Please ask a tournament organiser to give you the right role."
            )
        except discord.Forbidden:
            pass

    @app_commands.command(
        name="setup",
        description="Set up the tournament server (roles, channels, permissions, invites)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            report = await setup_server(guild, self._data_root())
        except discord.Forbidden as e:
            log.exception("Setup forbidden in %s", guild.name)
            await interaction.followup.send(
                f"⚠ Missing permissions: {e}. Re-invite the bot with the required scopes.",
                ephemeral=True,
            )
            return
        except Exception as e:
            log.exception("Setup failed in %s", guild.name)
            await interaction.followup.send(f"⚠ Setup failed: {e}", ephemeral=True)
            return

        await self._refresh_invites(guild)

        # Seed the #setup channel with the welcome message on first creation,
        # so the organiser sees something there when they walk in.
        if SETUP_CHANNEL in report.channels_created:
            setup_ch = discord.utils.get(guild.text_channels, name=SETUP_CHANNEL)
            if setup_ch is not None:
                await send_long(setup_ch, RUN_SETUP_WELCOME)

        msg = report.summary()
        qr_files = [discord.File(p, filename=p.name) for p in report.qr_paths.values()]
        await interaction.followup.send(msg, files=qr_files, ephemeral=True)


class SetupAgentCog(commands.Cog):
    """Handles the #setup channel — delegates to the run-bot setup agent."""

    _running: set[int] = set()

    def __init__(self, bot: HemaTournamentRunBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.bot.user:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.channel.name != SETUP_CHANNEL:
            return
        if message.content.startswith("/"):
            return
        if message.channel.id in self._running:
            await message.channel.send("⏳ Already processing — please wait.")
            return
        self._running.add(message.channel.id)
        try:
            async with message.channel.typing():
                user_config_path = os.environ.get("USER_CONFIG")
                kwargs = {"user_config_path": Path(user_config_path)} if user_config_path else {}
                if self.bot.config is not None:
                    data_root = Path(self.bot.config.data_root_dir)
                else:
                    data_root = Path(load_agent_config().reg_agent.data_root_dir)
                await run_run_setup_agent(message.channel, message.content, data_root=data_root, **kwargs)
        finally:
            self._running.discard(message.channel.id)


_LANG_CHOICES = [
    app_commands.Choice(name=code, value=code)
    for code in ["EN", "CS", "DE", "FR", "ES", "IT", "PL", "SK", "HU", "RU"]
]


class SetupCommandsCog(commands.Cog):
    """Slash-command shortcuts that bypass the LLM for well-known setup steps.

    Each command directly calls the same `_do_*` helpers used by the agent
    tools, then posts a confirmation to the #setup channel.
    """

    def __init__(self, bot: HemaTournamentRunBot) -> None:
        self.bot = bot

    def _user_config_path(self) -> Path:
        env = os.environ.get("USER_CONFIG")
        return Path(env) if env else _DEFAULT_USER_CONFIG

    async def _post_setup(self, guild: discord.Guild, text: str) -> None:
        ch = discord.utils.get(guild.text_channels, name=SETUP_CHANNEL)
        if ch is not None:
            await send_long(ch, text)

    @app_commands.command(
        name="set_language",
        description="Set the organiser language for this tournament",
    )
    @app_commands.describe(code="ISO 639-1 language code, e.g. EN, CS, DE")
    @app_commands.choices(code=_LANG_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    async def set_language(self, interaction: discord.Interaction, code: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = _do_save_language(self._user_config_path(), SHARED_MEMORY_PATH, code)
        await self._post_setup(interaction.guild, msg)
        await interaction.followup.send(f"Language set to **{code.upper()}** — see #setup.", ephemeral=True)

    @app_commands.command(
        name="set_name",
        description="Set the tournament display name",
    )
    @app_commands.describe(name="Full tournament name, e.g. Prague Open 2026")
    @app_commands.default_permissions(manage_guild=True)
    async def set_name(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        if not name.strip():
            await interaction.response.send_message("Tournament name cannot be empty.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        _append_memory(SHARED_MEMORY_PATH, f"Tournament name: {name}")
        result = _do_init_data_dir(self._user_config_path(), name.strip())
        await self._post_setup(interaction.guild, f"Tournament name set to **{name}**.\n_{result}_")
        await interaction.followup.send(f"Tournament name saved — see #setup.", ephemeral=True)

    @app_commands.command(
        name="set_disciplines",
        description="Set tournament disciplines using comma-separated codes, e.g. LS,SAW,SB",
    )
    @app_commands.describe(codes="Comma-separated discipline codes, e.g. LS,SAW,SB or LS, SAW, SB")
    @app_commands.default_permissions(manage_guild=True)
    async def set_disciplines(self, interaction: discord.Interaction, codes: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        raw_codes = [c.strip() for c in codes.split(",") if c.strip()]
        if not raw_codes:
            await interaction.followup.send("No codes provided.", ephemeral=True)
            return

        unknown = [c for c in raw_codes if c not in DISCIPLINE_NAMES]
        if unknown:
            valid = ", ".join(sorted(DISCIPLINE_NAMES))
            await interaction.followup.send(
                f"Unknown code(s): **{', '.join(unknown)}**.\nValid codes: {valid}",
                ephemeral=True,
            )
            return

        disciplines = {c: DISCIPLINE_NAMES[c] for c in raw_codes}
        _do_save_disciplines(self._user_config_path(), disciplines)

        rows = [[code, name] for code, name in disciplines.items()]
        table = table2ascii(
            header=["Code", "Discipline"],
            body=rows,
            style=PresetStyle.thin_compact,
            alignments=[Alignment.LEFT, Alignment.LEFT],
        )
        await self._post_setup(
            interaction.guild,
            f"Disciplines saved:\n```\n{table}\n```",
        )
        await interaction.followup.send("Disciplines saved — see #setup.", ephemeral=True)

    @app_commands.command(
        name="validate_disc",
        description="Check pool sheets against the tournament roster",
    )
    @app_commands.describe(
        disciplines="Comma-separated discipline codes to check, e.g. LS,SAW. Leave empty to check all."
    )
    @app_commands.default_permissions(manage_guild=True)
    async def validate_disc(
        self, interaction: discord.Interaction, disciplines: str = ""
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        user_config = self._user_config_path()

        # Resolve which disciplines to validate
        if disciplines.strip():
            codes_to_check = [c.strip() for c in disciplines.split(",") if c.strip()]
            unknown = [c for c in codes_to_check if c not in DISCIPLINE_NAMES]
            if unknown:
                await interaction.followup.send(
                    f"Unknown code(s): **{', '.join(unknown)}**. "
                    f"Valid codes: {', '.join(sorted(DISCIPLINE_NAMES))}",
                    ephemeral=True,
                )
                return
        else:
            try:
                with open(user_config) as f:
                    cfg = json.load(f)
                codes_to_check = list(cfg.get("disciplines", {}).keys())
            except Exception:
                codes_to_check = []
            if not codes_to_check:
                await interaction.followup.send(
                    "No disciplines configured. "
                    "Run `/set_disciplines` first or pass codes explicitly.",
                    ephemeral=True,
                )
                return

        if self.bot.config is not None:
            data_root = Path(self.bot.config.data_root_dir)
        else:
            data_root = Path(load_agent_config().reg_agent.data_root_dir)

        report = await asyncio.to_thread(
            _do_validate_disciplines, codes_to_check, user_config, data_root
        )

        ch = interaction.channel
        if isinstance(ch, discord.TextChannel):
            await send_long(ch, report)
            await interaction.followup.send(
                f"Validation complete for {len(codes_to_check)} discipline(s) — see above.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(report, ephemeral=True)


def run() -> None:
    logging.basicConfig(
        handlers=[
            logging.FileHandler("discord-run.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)-22s %(message)s",
    )
    acquire_instance_lock("/tmp/hema-run-bot.lock")
    token = os.environ["DISCORD_TOKEN"]
    bot = HemaTournamentRunBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    run()
