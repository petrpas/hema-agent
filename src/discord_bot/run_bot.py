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
from in_tournament.run_setup_agent.run_setup_agent import RUN_SETUP_WELCOME, run_run_setup_agent
from in_tournament.server_layout import ROLES, SETUP_CHANNEL
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
