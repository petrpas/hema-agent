"""Discord bot for the live-tournament phase of a HEMA tournament.

Independent from `pre_bot.py` (pre-tournament prep). Shares only generic
helpers in `discord_bot/` and framework utilities in `shared/`.
"""

# Load .env before any other import so env-dependent module-level code
# (e.g. shared.config.tracing.enabled) sees the correct values.
from dotenv import load_dotenv
load_dotenv()

import asyncio
import io
import json
import logging
import os
import re
import sys
from pathlib import Path

# Make `shared.*`, `pre_tournament.*`, `in_tournament.*` importable when
# this module is run directly from src/discord_bot/.
_SRC_ROOT = Path(__file__).parent.parent
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import discord
from discord import app_commands
from discord.ext import commands, tasks

from discord_bot.instance_lock import acquire_instance_lock
from shared.config import load_agent_config
from in_tournament.config import load_in_config, InConfig

from discord_bot.discord_utils import send_long
from in_tournament.results_agent.calc_pools import calc_and_write_pool_results
from in_tournament.results_agent.results_agent import (
    compute_pool_stats,
    parse_pool_image,
)
from in_tournament.results_agent.sheet_io import (
    get_pool_composition,
    load_published_pools,
    read_verified_bouts,
    save_published_pools,
    write_pool_bouts,
)
from in_tournament.msgs import read_msg as _read_in_msg
from in_tournament.render_pools import (
    read_pools_for_disc,
    render_pool_results_for_disc,
    render_pools_for_disc,
    render_pools_list_for_disc,
)
from in_tournament.run_setup_agent.run_setup_agent import (
    DISCIPLINE_NAMES,
    _DEFAULT_USER_CONFIG,
    _do_configure_tournament,
    _do_create_data_sheets,
    _do_validate_discipline,
    post_invite_qrs,
    RUN_SETUP_WELCOME,
    run_run_setup_agent,
)
from in_tournament.server_layout import (
    ANNOUNCEMENTS_CHANNEL,
    BOT_COMMANDS_CHANNEL,
    RESULTS_CHANNEL,
    RESULTS_UPLOAD_CHANNEL,
    ROLE_ADMIN,
    ROLES,
    SETUP_CHANNEL,
)
from in_tournament.setup import (
    INVITES_FILE,
    InviteSnapshot,
    assign_role_for_invite,
    detect_used_code,
    load_invite_map,
    run_bot_data_dir,
    setup_server,
)

log = logging.getLogger(__name__)

_HELP_TEXT = _read_in_msg("run_bot/help")

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}


def _is_image(attachment: discord.Attachment) -> bool:
    return Path(attachment.filename).suffix.lower() in _IMAGE_EXTENSIONS


def _guess_media_type(filename: str) -> str:
    return _MEDIA_TYPES.get(Path(filename).suffix.lower(), "image/jpeg")


# ── Access-control helpers ────────────────────────────────────────────────────

def _admin_only() -> app_commands.checks.Cooldown:
    """Require the invoking user to hold the Admin role."""
    return app_commands.checks.has_role(ROLE_ADMIN)


def _in_setup() -> app_commands.check:
    """Require the command to be run in #setup."""
    async def predicate(interaction: discord.Interaction) -> bool:
        ch = interaction.channel
        if isinstance(ch, discord.TextChannel) and ch.name == SETUP_CHANNEL:
            return True
        raise app_commands.CheckFailure(f"Use this command in #{SETUP_CHANNEL}.")
    return app_commands.check(predicate)


def _in_setup_or_botcmds() -> app_commands.check:
    """Require the command to be run in #setup or #bot-commands."""
    async def predicate(interaction: discord.Interaction) -> bool:
        ch = interaction.channel
        if isinstance(ch, discord.TextChannel) and ch.name in (SETUP_CHANNEL, BOT_COMMANDS_CHANNEL):
            return True
        raise app_commands.CheckFailure(
            f"Use this command in #{SETUP_CHANNEL} or #{BOT_COMMANDS_CHANNEL}."
        )
    return app_commands.check(predicate)


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
        await self.add_cog(ResultsCog(self))

    def _data_root(self) -> Path:
        if self.config is not None:
            return Path(self.config.data_root_dir)
        return Path(load_agent_config().reg_agent.data_root_dir)

    def _setup_done(self, guild_id: int) -> bool:
        """Return True if /setup has already been run for this guild."""
        return (run_bot_data_dir(self._data_root(), guild_id) / INVITES_FILE).exists()

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
            if self._setup_done(guild.id):
                self.tree.remove_command("setup", guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash command tree synced to guild %s (%d)", guild.name, guild.id)
            await self._ensure_owner_is_admin(guild)

    async def _ensure_owner_is_admin(self, guild: discord.Guild) -> None:
        admin_role = discord.utils.get(guild.roles, name=ROLE_ADMIN)
        if admin_role is None or guild.owner is None:
            return
        if admin_role not in guild.owner.roles:
            try:
                await guild.owner.add_roles(admin_role, reason="run_bot: auto-assign Admin to server owner")
                log.info("Assigned Admin role to owner %s in %s", guild.owner, guild.name)
            except discord.Forbidden:
                log.warning("Cannot assign Admin role to owner in %s", guild.name)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash command tree synced to new guild %s (%d)", guild.name, guild.id)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingRole):
            msg = f"This command requires the **{ROLE_ADMIN}** role."
        elif isinstance(error, app_commands.CheckFailure):
            msg = str(error)
        else:
            log.exception("Unhandled app command error in %s: %s", interaction.command, error)
            msg = f"Unexpected error: {error}"
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(msg, ephemeral=True)


class GeneralCog(commands.Cog):
    """Commands available in every channel."""

    def __init__(self, bot: HemaTournamentRunBot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="List all available bot commands")
    async def help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(_HELP_TEXT, ephemeral=True)

    @app_commands.command(
        name="clear",
        description="Remove all messages except the welcome message",
    )
    @_admin_only()
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
    @commands.has_permissions(manage_guild=True)
    async def sync(self, ctx: commands.Context) -> None:
        """Sync slash commands to this guild instantly (manage_guild only, use !sync)."""
        await self.bot.tree.sync(guild=ctx.guild)
        await ctx.send("Slash commands synced to this server.", delete_after=5)
        log.info("Tree synced to guild %s by %s", ctx.guild, ctx.author)


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

        # Remove /setup from this guild's command tree — it's a one-time operation.
        self.bot.tree.remove_command("setup", guild=guild)
        await self.bot.tree.sync(guild=guild)

        # Seed the #setup channel with the welcome message on first creation,
        # so the organiser sees something there when they walk in.
        if SETUP_CHANNEL in report.channels_created:
            setup_ch = discord.utils.get(guild.text_channels, name=SETUP_CHANNEL)
            if setup_ch is not None:
                await send_long(setup_ch, RUN_SETUP_WELCOME)

        await interaction.followup.send(report.summary(), ephemeral=True)


async def _get_or_create_thread(
    channel: discord.TextChannel,
    name: str,
    auto_archive_duration: int = 10080,
) -> discord.Thread:
    """Return an active thread by name, unarchiving if necessary, or create a new one.

    Falls back to creating a new thread if the archived one cannot be unarchived
    (e.g. locked by another user or the bot lacks Manage Threads).
    """
    thread = discord.utils.get(channel.threads, name=name)
    if thread is not None:
        return thread
    async for t in channel.archived_threads(limit=100):
        if t.name == name:
            try:
                await t.edit(archived=False, locked=False)
                return t
            except discord.Forbidden:
                log.warning("Cannot unarchive thread '%s' in #%s — will create a new one", name, channel.name)
                break
    return await channel.create_thread(
        name=name,
        type=discord.ChannelType.public_thread,
        auto_archive_duration=auto_archive_duration,
    )


async def _typing_loop(channel: discord.TextChannel) -> None:
    """Send a typing indicator every 8 s, silently swallowing HTTP errors.

    The built-in channel.typing() context manager retries 429s until it raises,
    which crashes the on_message handler. This coroutine fires every 8 s (well
    within Discord's ~10 s typing-indicator window) and ignores rate-limit errors.
    Run as an asyncio.Task and cancel it when the LLM call completes.
    """
    while True:
        try:
            await channel._state.http.send_typing(channel.id)
        except Exception:
            pass
        await asyncio.sleep(8)


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
        if message.content.startswith(("/", "!")):
            return
        if isinstance(message.author, discord.Member):
            if not any(r.name == ROLE_ADMIN for r in message.author.roles):
                await message.reply(
                    f"Only members with the **{ROLE_ADMIN}** role can configure the bot.",
                    mention_author=False,
                )
                return
        if message.channel.id in self._running:
            await message.channel.send("⏳ Already processing — please wait.")
            return
        self._running.add(message.channel.id)
        typing_task = asyncio.create_task(_typing_loop(message.channel))
        try:
            user_config_path = os.environ.get("USER_CONFIG")
            kwargs = {"user_config_path": Path(user_config_path)} if user_config_path else {}
            if self.bot.config is not None:
                data_root = Path(self.bot.config.data_root_dir)
            else:
                data_root = Path(load_agent_config().reg_agent.data_root_dir)
            await run_run_setup_agent(message.channel, message.content, data_root=data_root, **kwargs)
        finally:
            typing_task.cancel()
            self._running.discard(message.channel.id)


_SUPPORTED_LANGS = ["EN", "CS", "DE", "FR", "ES", "IT", "PL", "SK", "HU", "RU"]

_DISC_COLOUR_MAP: dict[str, int] = {
    "LS":  0xED4245,  # red
    "LSM": 0xED4245,  # red
    "LSW": 0x992D22,  # dark red
    "SA":  0x1F3A7A,  # navy
    "SAM": 0x1F3A7A,  # navy
    "SAW": 0x3498DB,  # blue
    "SB":  0x57F287,  # green
    "SBM": 0x57F287,  # green
    "SBW": 0x1F8B4C,  # dark green
    "RA":  0xFEE75C,  # yellow
    "RAM": 0xFEE75C,  # yellow
    "RAW": 0xE67E22,  # orange
}
_DISC_COLOUR_DEFAULT = 0x1ABC9C  # aqua


def _disc_colour(disc_code: str) -> int:
    return _DISC_COLOUR_MAP.get(disc_code, _DISC_COLOUR_DEFAULT)


def _stats_table(stats: list[dict]) -> str:
    """Return a monospace code-block table of fencer stats for a Discord embed."""
    header = ("#", "Name", "V", "TS", "TR", "Ind")
    rows = [
        (str(i), s["name"], str(s["v"]),
         str(s["ts"]), str(s["tr"]), f"{s['ind']:+d}")
        for i, s in enumerate(stats, 1)
    ]
    all_rows = [header] + rows
    widths = [max(len(r[col]) for r in all_rows) for col in range(6)]

    def _fmt(row: tuple) -> str:
        return (
            f"{row[0]:<{widths[0]}}  "
            f"{row[1]:<{widths[1]}}  "
            f"{row[2]:>{widths[2]}}  "
            f"{row[3]:>{widths[3]}}  "
            f"{row[4]:>{widths[4]}}  "
            f"{row[5]:>{widths[5]}}"
        )

    sep = "─" * (sum(widths) + 10)
    lines = [_fmt(header), sep] + [_fmt(r) for r in rows]
    return "```\n" + "\n".join(lines) + "\n```"


def _standings_embed(disc: str, rows: list[dict]) -> discord.Embed:
    """Embed showing the ordered pool-stage standings from the Pool Results sheet."""
    header = ("#", "Name", "V/M", "Ind", "TS", "TR")
    table_rows: list[tuple] = []
    for r in rows:
        try:
            ind_str = f"{int(r['index']):+d}"
        except (ValueError, TypeError):
            ind_str = str(r.get("index", ""))
        vm = f"{r['victory']}/{r['matches']}"
        table_rows.append((r["ord"], r["name"], vm, ind_str, r["ts"], r["tr"]))
    all_rows = [header] + table_rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(6)]

    def _fmt(row: tuple) -> str:
        return (
            f"{str(row[0]):>{widths[0]}}  "
            f"{str(row[1]):<{widths[1]}}  "
            f"{str(row[2]):>{widths[2]}}  "
            f"{str(row[3]):>{widths[3]}}  "
            f"{str(row[4]):>{widths[4]}}  "
            f"{str(row[5]):>{widths[5]}}"
        )

    sep = "─" * (sum(widths) + 10)
    lines = [_fmt(header), sep] + [_fmt(r) for r in table_rows]
    return discord.Embed(
        title=f"{disc} — Pool Stage Results",
        description="```\n" + "\n".join(lines) + "\n```",
        colour=_disc_colour(disc),
    )


def _bouts_embed(pool_id: str, disc: str, bouts: list[dict]) -> discord.Embed:
    """Embed listing every bout with the winner's name in bold."""
    lines: list[str] = []
    for b in bouts:
        f1 = str(b.get("Fencer1", ""))
        f2 = str(b.get("Fencer2", ""))
        try:
            s1, s2 = int(b.get("Score1", 0)), int(b.get("Score2", 0))
        except (TypeError, ValueError):
            s1, s2 = 0, 0
        if s1 > s2:
            lines.append(f"**{f1}** vs {f2} — {s1}:{s2}")
        elif s2 > s1:
            lines.append(f"{f1} vs **{f2}** — {s1}:{s2}")
        else:
            lines.append(f"{f1} vs {f2} — {s1}:{s2}")
    return discord.Embed(
        description="\n".join(lines) or "—",
        colour=_disc_colour(disc),
    )


async def _wipe_guild_channels(guild: discord.Guild) -> None:
    """Delete all threads and all messages in every text channel."""
    log.info("wipe_guild_channels: starting for guild %s (%d text channels)", guild.name, len(guild.text_channels))
    for channel in guild.text_channels:
        log.info("wipe: processing #%s", channel.name)
        thread_count = 0
        for thread in list(channel.threads):
            try:
                await thread.delete()
                thread_count += 1
            except discord.Forbidden:
                try:
                    await thread.edit(archived=True, locked=True)
                    thread_count += 1
                    log.info("wipe: archived (no delete perm) thread %s in #%s", thread.name, channel.name)
                except discord.HTTPException as e2:
                    log.warning("wipe: failed to archive thread %s in #%s: %s", thread.name, channel.name, e2)
            except discord.HTTPException as e:
                log.warning("wipe: failed to delete thread %s in #%s: %s", thread.name, channel.name, e)
        try:
            async for thread in channel.archived_threads(limit=None):
                try:
                    await thread.delete()
                    thread_count += 1
                except discord.Forbidden:
                    log.info("wipe: skipping archived thread %s in #%s (no delete perm)", thread.name, channel.name)
                except discord.HTTPException as e:
                    log.warning("wipe: failed to delete archived thread %s in #%s: %s", thread.name, channel.name, e)
        except discord.HTTPException as e:
            log.warning("wipe: failed to list archived threads in #%s: %s", channel.name, e)
        if thread_count:
            log.info("wipe: deleted %d thread(s) from #%s", thread_count, channel.name)
        # Bulk-delete recent messages (Discord rejects messages older than 14 days
        # in bulk-delete — purge() silently skips them, so we also do a one-by-one pass).
        try:
            deleted = await channel.purge(limit=None)
            log.info("wipe: bulk-purged %d message(s) from #%s", len(deleted), channel.name)
        except discord.HTTPException as e:
            log.warning("wipe: purge failed for #%s: %s", channel.name, e)
        remaining = 0
        async for msg in channel.history(limit=None):
            try:
                await msg.delete()
                remaining += 1
            except discord.HTTPException as e:
                log.warning("wipe: failed to delete message %s in #%s: %s", msg.id, channel.name, e)
        if remaining:
            log.info("wipe: deleted %d old message(s) one-by-one from #%s", remaining, channel.name)
    log.info("wipe_guild_channels: done")


class _ConfirmConfigView(discord.ui.View):
    """Ephemeral confirmation step for /configure — shown after the modal."""

    def __init__(
        self,
        cog: "SetupCommandsCog",
        name: str,
        lang: str,
        disciplines: dict[str, str],
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._name = name
        self._lang = lang
        self._disciplines = disciplines

    def _disable_all(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._disable_all()
        await interaction.response.edit_message(content="⏳ Applying configuration…", view=self)
        self.stop()

        if interaction.guild:
            await _wipe_guild_channels(interaction.guild)

        try:
            await asyncio.to_thread(
                _do_configure_tournament,
                self._cog._user_config_path(),
                self._name,
                self._lang,
                self._disciplines,
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"Configuration failed: {e}", view=None)
            return

        if interaction.guild:
            disc_list = ", ".join(f"**{c}** ({n})" for c, n in self._disciplines.items())
            await self._cog._post_setup(
                interaction.guild,
                f"Tournament configured:\n**Name:** {self._name}\n**Language:** {self._lang}\n**Disciplines:** {disc_list}",
            )
            data_dir = run_bot_data_dir(self._cog._data_root(), interaction.guild.id)
            await post_invite_qrs(interaction.guild, data_dir, self._name, self._lang)

        await interaction.edit_original_response(content="Configuration saved — see #setup.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled — no changes made.", view=None)
        self.stop()


class _TournamentConfigModal(discord.ui.Modal, title="Tournament Configuration"):
    tournament_name = discord.ui.TextInput(
        label="Tournament name",
        placeholder="e.g. Prague Open 2026",
        required=True,
    )
    language = discord.ui.TextInput(
        label="Language (EN, CS, DE, …)",
        placeholder="EN",
        default="EN",
        min_length=2,
        max_length=5,
        required=True,
    )
    disciplines = discord.ui.TextInput(
        label="Disciplines (comma-separated codes)",
        placeholder="e.g. LS, SAW, SB",
        required=True,
    )

    def __init__(self, cog: "SetupCommandsCog") -> None:
        super().__init__()
        self._cog = cog

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("TournamentConfigModal.on_error: %s", error)
        try:
            await interaction.response.send_message(f"Unexpected error: {error}", ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_submit(self, interaction: discord.Interaction) -> None:
        log.info("configure modal submitted by %s (guild=%s)", interaction.user, interaction.guild)
        name = self.tournament_name.value.strip()
        lang = self.language.value.strip().upper()
        disc_str = self.disciplines.value

        if lang not in _SUPPORTED_LANGS:
            await interaction.response.send_message(
                f"Unsupported language **{lang}**. Supported: {', '.join(_SUPPORTED_LANGS)}",
                ephemeral=True,
            )
            return

        raw_codes = [c for c in re.split(r"[^A-Za-z0-9]+", disc_str.upper()) if c]
        if not raw_codes:
            await interaction.response.send_message("No discipline codes provided.", ephemeral=True)
            return
        unknown = [c for c in raw_codes if c not in DISCIPLINE_NAMES]
        if unknown:
            await interaction.response.send_message(
                f"Unknown code(s): **{', '.join(unknown)}**.\nValid codes: {', '.join(sorted(DISCIPLINE_NAMES))}",
                ephemeral=True,
            )
            return

        disciplines = {c: DISCIPLINE_NAMES[c] for c in raw_codes}
        disc_list = ", ".join(f"**{c}** ({n})" for c, n in disciplines.items())
        summary = (
            "This will **wipe all channel messages** and apply:\n\n"
            f"**Tournament name:** {name}\n"
            f"**Language:** {lang}\n"
            f"**Disciplines:** {disc_list}\n\n"
            "Are you sure?"
        )
        view = _ConfirmConfigView(self._cog, name, lang, disciplines)
        await interaction.response.send_message(summary, view=view, ephemeral=True)


class SetupCommandsCog(commands.Cog):
    def __init__(self, bot: HemaTournamentRunBot) -> None:
        self.bot = bot

    def _user_config_path(self) -> Path:
        env = os.environ.get("USER_CONFIG")
        return Path(env) if env else _DEFAULT_USER_CONFIG

    def _data_root(self) -> Path:
        if self.bot.config is not None:
            return Path(self.bot.config.data_root_dir)
        return Path(load_agent_config().reg_agent.data_root_dir)

    async def _post_setup(self, guild: discord.Guild, text: str) -> None:
        ch = discord.utils.get(guild.text_channels, name=SETUP_CHANNEL)
        if ch is not None:
            await send_long(ch, text)

    @app_commands.command(
        name="configure",
        description="Configure the tournament: name, language, and disciplines",
    )
    @_admin_only()
    @_in_setup()
    async def configure(self, interaction: discord.Interaction) -> None:
        log.info("/configure invoked by %s in guild %s", interaction.user, interaction.guild)
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.send_modal(_TournamentConfigModal(self))

    @app_commands.command(
        name="create_pool_sheets",
        description="Create one data entry sheet per discipline from the Drive template",
    )
    @_admin_only()
    @_in_setup()
    async def create_pool_sheets(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await asyncio.to_thread(_do_create_data_sheets, self._user_config_path())
        await self._post_setup(interaction.guild, result)
        await interaction.followup.send("Done — see #setup for the sheet links.", ephemeral=True)

    @app_commands.command(
        name="validate_pools",
        description="Check pool sheets against the tournament roster",
    )
    @app_commands.describe(
        disc="Comma-separated discipline codes to check, e.g. LS,SAW. Leave empty to check all."
    )
    @_admin_only()
    @_in_setup()
    async def validate_pools(
        self, interaction: discord.Interaction, disc: str = ""
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        user_config = self._user_config_path()

        if disc.strip():
            codes_to_check = [c.strip() for c in disc.split(",") if c.strip()]
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

        setup_ch = discord.utils.get(interaction.guild.text_channels, name=SETUP_CHANNEL)
        if setup_ch is None:
            await interaction.followup.send("❌ #setup channel not found.", ephemeral=True)
            return

        posted: list[str] = []
        for code in codes_to_check:
            report = await asyncio.to_thread(
                _do_validate_discipline, code, user_config, data_root
            )

            thread_name = f"{code} Pool Validation"
            thread = await _get_or_create_thread(setup_ch, thread_name)
            await send_long(thread, report)
            posted.append(f"**{code}** → {thread_name}")

        await interaction.followup.send(
            f"Validation complete — results posted to: {', '.join(posted)} in #{SETUP_CHANNEL}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="render_pools",
        description="Render pool table PDFs and post to #setup → <disc> Pool Tables thread",
    )
    @app_commands.describe(disc="Discipline code, e.g. LS, SAW. Leave empty to render all.")
    @_admin_only()
    @_in_setup()
    async def render_pools(self, interaction: discord.Interaction, disc: str = "") -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return

        user_config = self._user_config_path()

        if disc.strip():
            codes = [disc.strip()]
            if codes[0] not in DISCIPLINE_NAMES:
                await interaction.response.send_message(
                    f"Unknown discipline code **{codes[0]}**. "
                    f"Valid codes: {', '.join(sorted(DISCIPLINE_NAMES))}",
                    ephemeral=True,
                )
                return
        else:
            try:
                with open(user_config) as f:
                    cfg = json.load(f)
                codes = list(cfg.get("disciplines", {}).keys())
            except Exception:
                codes = []
            if not codes:
                await interaction.response.send_message(
                    "No disciplines configured. "
                    "Run `/set_disciplines` first or pass a code explicitly.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)

        setup_ch = discord.utils.get(interaction.guild.text_channels, name=SETUP_CHANNEL)
        if setup_ch is None:
            await interaction.followup.send("❌ #setup channel not found.", ephemeral=True)
            return

        rendered: list[str] = []
        for code in codes:
            try:
                filename, pdf_bytes = await asyncio.to_thread(
                    render_pools_for_disc, code, user_config
                )
                list_filename, list_pdf_bytes = await asyncio.to_thread(
                    render_pools_list_for_disc, code, user_config
                )
            except ValueError as e:
                await interaction.followup.send(f"❌ {code}: {e}", ephemeral=True)
                continue
            except Exception as e:
                log.exception("render_pools failed for %s", code)
                await interaction.followup.send(f"❌ {code}: unexpected error: {e}", ephemeral=True)
                continue

            thread_name = f"{code} Pool Tables"
            thread = await _get_or_create_thread(setup_ch, thread_name)

            disc_name = DISCIPLINE_NAMES[code]
            await thread.send(
                f"**{code}** — {disc_name}",
                files=[
                    discord.File(io.BytesIO(pdf_bytes), filename=filename),
                    discord.File(io.BytesIO(list_pdf_bytes), filename=list_filename),
                ],
            )
            rendered.append(f"**{code}** → {thread_name}")

        if rendered:
            await interaction.followup.send(
                f"Rendered pools for: {', '.join(rendered)} — see #{SETUP_CHANNEL}.",
                ephemeral=True,
            )

    @app_commands.command(
        name="publish_pools",
        description="Publish pool assignments for fencers into #announcements → <disc> Pools thread",
    )
    @app_commands.describe(disc="Discipline code, e.g. LS, SAW")
    @_admin_only()
    @_in_setup_or_botcmds()
    async def publish_pools(self, interaction: discord.Interaction, disc: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return

        disc = disc.strip()
        if disc not in DISCIPLINE_NAMES:
            await interaction.response.send_message(
                f"Unknown discipline code **{disc}**. "
                f"Valid codes: {', '.join(sorted(DISCIPLINE_NAMES))}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        user_config = self._user_config_path()
        try:
            pools = await asyncio.to_thread(read_pools_for_disc, disc, user_config)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            log.exception("publish_pools failed for %s", disc)
            await interaction.followup.send(f"❌ Unexpected error: {e}", ephemeral=True)
            return

        ann_ch = discord.utils.get(interaction.guild.text_channels, name=ANNOUNCEMENTS_CHANNEL)
        if ann_ch is None:
            await interaction.followup.send(
                f"❌ #{ANNOUNCEMENTS_CHANNEL} channel not found.", ephemeral=True
            )
            return

        thread_name = f"{disc} Pools"
        thread = await _get_or_create_thread(ann_ch, thread_name)

        colour = _disc_colour(disc)
        disc_name = DISCIPLINE_NAMES[disc]
        embeds = [
            discord.Embed(
                title=f"Pool {pool_no}",
                description="\n".join(
                    f"{i}. {name}" for i, name in enumerate(names, 1)
                ),
                colour=colour,
            )
            for pool_no, names in pools
        ]
        # Discord allows max 10 embeds per message
        for i in range(0, len(embeds), 10):
            await thread.send(embeds=embeds[i : i + 10])

        await interaction.followup.send(
            f"Published {len(pools)} pool(s) for **{disc}** ({disc_name}) — "
            f"see **{thread_name}** thread in #{ANNOUNCEMENTS_CHANNEL}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="calc_pools",
        description="Calculate pool-stage standings and write to Pool Results sheet",
    )
    @app_commands.describe(disc="Discipline code, e.g. LS")
    @_admin_only()
    @_in_setup()
    async def calc_pools(self, interaction: discord.Interaction, disc: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return

        config = self.bot.config
        if config is None:
            await interaction.response.send_message("Bot is not configured yet.", ephemeral=True)
            return

        disc = disc.strip().upper()
        sheet_url = config.data_sheet_urls.get(disc)
        if not sheet_url:
            await interaction.response.send_message(
                f"No data sheet configured for discipline **{disc}**.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            rows, warnings = await asyncio.to_thread(
                calc_and_write_pool_results, sheet_url, config.creds_path, disc
            )
        except Exception as e:
            log.exception("calc_pools failed for %s", disc)
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        if warnings:
            setup_ch = discord.utils.get(interaction.guild.text_channels, name=SETUP_CHANNEL)
            if setup_ch is not None:
                thread = await _get_or_create_thread(setup_ch, f"{disc} Pool Results")
                header = f"**{disc} Pool Results — Validation ({len(warnings)} issue(s))**"
                await send_long(thread, header + "\n" + "\n".join(warnings))

        msg = f"✅ **{disc}** — {len(rows)} fencer(s) written to Pool Results sheet."
        if warnings:
            msg += f"\n⚠ {len(warnings)} issue(s) — see **{disc} Pool Results** thread in #{SETUP_CHANNEL}."
        else:
            msg += " No validation issues."
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(
        name="pub_pool_res",
        description="Render pool-stage results as PDF+PNG and post to #setup and #results",
    )
    @app_commands.describe(disc="Discipline code, e.g. LS")
    @_admin_only()
    @_in_setup()
    async def pub_pool_res(self, interaction: discord.Interaction, disc: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return

        config = self.bot.config
        if config is None:
            await interaction.response.send_message("Bot is not configured yet.", ephemeral=True)
            return

        disc = disc.strip().upper()
        sheet_url = config.data_sheet_urls.get(disc)
        if not sheet_url:
            await interaction.response.send_message(
                f"No data sheet configured for discipline **{disc}**.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        tournament = config.tournament_display_name or config.tournament_name

        try:
            (pdf_name, pdf_bytes), (png_name, png_bytes), rows = await asyncio.to_thread(
                render_pool_results_for_disc,
                disc, sheet_url, config.creds_path, tournament, disc,
            )
        except Exception as e:
            log.exception("pub_pool_res failed for %s", disc)
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        setup_ch = discord.utils.get(interaction.guild.text_channels, name=SETUP_CHANNEL)
        if setup_ch is None:
            await interaction.followup.send("❌ #setup channel not found.", ephemeral=True)
            return

        setup_thread = await _get_or_create_thread(setup_ch, f"{disc} Pool Results")
        await setup_thread.send(
            f"**{disc}** — Pool Results",
            files=[
                discord.File(io.BytesIO(png_bytes), filename=png_name),
                discord.File(io.BytesIO(pdf_bytes), filename=pdf_name),
            ],
        )

        results_ch = discord.utils.get(interaction.guild.text_channels, name=RESULTS_CHANNEL)
        if results_ch is not None:
            results_thread = await _get_or_create_thread(results_ch, f"{disc} Pool Results")
            await results_thread.send(embed=_standings_embed(disc, rows))

        await interaction.followup.send(
            f"✅ Pool results for **{disc}** posted to **{disc} Pool Results** in #{SETUP_CHANNEL}"
            + (f" and **{disc} Pool Results** in #{RESULTS_CHANNEL}." if results_ch is not None else "."),
            ephemeral=True,
        )


class ResultsCog(commands.Cog):
    """Watches #org-results-upload for pool-sheet photos and manages the publication loop."""

    def __init__(self, bot: HemaTournamentRunBot) -> None:
        self.bot = bot
        self._poll.start()

    def cog_unload(self) -> None:
        self._poll.cancel()

    def _data_root(self) -> Path:
        if self.bot.config is not None:
            return Path(self.bot.config.data_root_dir)
        return Path(load_agent_config().reg_agent.data_root_dir)

    # ── Image intake ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.bot.user:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.channel.name != RESULTS_UPLOAD_CHANNEL:
            return
        images = [a for a in message.attachments if _is_image(a)]
        if not images:
            return
        for attachment in images:
            await self._handle_image(message, attachment)

    async def _handle_image(
        self, message: discord.Message, attachment: discord.Attachment
    ) -> None:
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        guild = message.guild
        if guild is None or self.bot.config is None:
            return

        ack = await channel.send(f"📷 Parsing `{attachment.filename}`…")
        try:
            image_bytes = await attachment.read()
            media_type = _guess_media_type(attachment.filename)
            config = self.bot.config

            # Gather pool composition across all configured disciplines
            composition: dict[str, list[str]] = {}
            for disc, sheet_url in config.data_sheet_urls.items():
                comp = await asyncio.to_thread(
                    get_pool_composition, sheet_url, config.creds_path, disc
                )
                composition.update(comp)

            if not composition:
                await ack.edit(content="❌ No pool sheets configured yet — run `/create_pool_sheets` first.")
                return

            result = await parse_pool_image(
                image_bytes, media_type, composition,
                discipline_limits=config.discipline_limits,
                disciplines=config.disciplines,
            )

            sheet_url = config.data_sheet_urls.get(result.disc)
            if sheet_url is None:
                await ack.edit(
                    content=f"❌ No data sheet configured for discipline **{result.disc}**."
                )
                return

            await asyncio.to_thread(write_pool_bouts, sheet_url, config.creds_path, result)

            flag_desc = {
                ".": "✅ clean parse",
                "?": "⚠ needs human review",
                "??": "❌ unreadable — please fill in by hand",
            }
            flag = flag_desc.get(result.confidence, result.confidence)
            report = f"**{result.pool_id}** — {len(result.bouts)} bouts written ({flag})"
            if result.issues:
                report += "\n" + "\n".join(f"  • {i}" for i in result.issues)

            await ack.edit(content=report)
            log.info("Parsed %s: %d bouts, confidence=%s", result.pool_id, len(result.bouts), result.confidence)

        except Exception as e:
            log.exception("parse_pool_image failed for %s", attachment.filename)
            await ack.edit(content=f"❌ Failed to parse `{attachment.filename}`: {e}")

    # ── Background polling ────────────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def _poll(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._poll_guild(guild)
            except Exception:
                log.exception("Results poll failed for guild %d", guild.id)

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _poll_guild(self, guild: discord.Guild) -> None:
        config = self.bot.config
        if config is None or not config.data_sheet_urls:
            return

        data_dir = run_bot_data_dir(self._data_root(), guild.id)
        published = await asyncio.to_thread(load_published_pools, data_dir)
        changed = False

        for disc, sheet_url in config.data_sheet_urls.items():
            creds = config.creds_path

            try:
                composition = await asyncio.to_thread(get_pool_composition, sheet_url, creds, disc)
                verified = await asyncio.to_thread(read_verified_bouts, sheet_url, creds)
            except Exception:
                log.exception("Sheet read failed for %s", disc)
                continue

            # Cleared = human removed the confidence symbol (cell is empty)
            cleared = [b for b in verified if str(b.get("Confidence", "")).strip() == ""]

            for pool_id, fencers in composition.items():
                if pool_id in published:
                    continue
                expected = len(fencers) * (len(fencers) - 1) // 2
                pool_no = int(pool_id.split("-")[-1])
                pool_cleared = [b for b in cleared if b.get("Pool") == pool_no]
                if len(pool_cleared) >= expected:
                    await self._publish_pool(guild, disc, pool_id, pool_cleared, fencers)
                    published.add(pool_id)
                    changed = True
                    log.info("Published pool %s in guild %d", pool_id, guild.id)

            ranking_key = f"{disc}_ranking"
            if ranking_key not in published:
                disc_pool_ids = set(composition.keys())
                if disc_pool_ids and disc_pool_ids.issubset(published):
                    all_fencers = [n for names in composition.values() for n in names]
                    disc_cleared = [b for b in cleared]
                    await self._publish_ranking(guild, disc, disc_cleared, all_fencers)
                    published.add(ranking_key)
                    changed = True
                    log.info("Published %s ranking in guild %d", disc, guild.id)

        if changed:
            await asyncio.to_thread(save_published_pools, data_dir, published)

    async def _publish_pool(
        self,
        guild: discord.Guild,
        disc: str,
        pool_id: str,
        bouts: list[dict],
        fencers: list[str],
    ) -> None:
        results_ch = discord.utils.get(guild.text_channels, name=RESULTS_CHANNEL)
        ann_ch = discord.utils.get(guild.text_channels, name=ANNOUNCEMENTS_CHANNEL)
        if results_ch is None:
            log.warning("No #%s channel in guild %d — cannot publish %s", RESULTS_CHANNEL, guild.id, pool_id)
            return
        thread = await _get_or_create_thread(results_ch, f"{disc} Pool Matches")
        stats = compute_pool_stats(fencers, bouts)
        await thread.send(embeds=[
            discord.Embed(
                title=f"{disc} Pool {pool_id.split('-', 1)[1]} Matches",
                description=_stats_table(stats),
                colour=_disc_colour(disc),
            ),
            _bouts_embed(pool_id, disc, bouts),
        ])
        if ann_ch is not None:
            await ann_ch.send(f"📊 **{pool_id}** matches posted — see <#{results_ch.id}>")

    async def _publish_ranking(
        self,
        guild: discord.Guild,
        disc: str,
        bouts: list[dict],
        all_fencers: list[str],
    ) -> None:
        results_ch = discord.utils.get(guild.text_channels, name=RESULTS_CHANNEL)
        ann_ch = discord.utils.get(guild.text_channels, name=ANNOUNCEMENTS_CHANNEL)
        if results_ch is None:
            return
        config = self.bot.config
        disc_name = (config.disciplines or {}).get(disc, disc) if config else disc
        thread = await _get_or_create_thread(results_ch, f"{disc} Pool Results")
        stats = compute_pool_stats(all_fencers, bouts)
        await thread.send(embed=discord.Embed(
            title=f"{disc} — {disc_name} · Pool Stage Results",
            description=_stats_table(stats),
            colour=_disc_colour(disc),
        ))
        if ann_ch is not None:
            await ann_ch.send(f"🏆 **{disc_name}** pool stage results posted — see <#{results_ch.id}>")

    # ── Slash commands ────────────────────────────────────────────────────────

    @app_commands.command(
        name="refresh",
        description="Check verified sheets now and publish any newly-complete pools",
    )
    @_admin_only()
    async def refresh(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self._poll_guild(guild)
            await interaction.followup.send("✅ Checked verified sheets.", ephemeral=True)
        except Exception as e:
            log.exception("refresh failed in guild %d", guild.id)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(
        name="repub_pool_matches",
        description="Manually publish matches for a specific pool from the verified sheet",
    )
    @app_commands.describe(disc="Discipline code, e.g. LS", pool_no="Pool number, e.g. 3")
    @_admin_only()
    async def repub_pool_matches(
        self, interaction: discord.Interaction, disc: str, pool_no: int
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return

        config = self.bot.config
        if config is None:
            await interaction.response.send_message("Bot is not configured yet.", ephemeral=True)
            return

        disc = disc.strip().upper()
        sheet_url = config.data_sheet_urls.get(disc)
        if sheet_url is None:
            await interaction.response.send_message(
                f"No data sheet configured for discipline **{disc}**.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        pool_id = f"{disc}-{pool_no}"
        try:
            composition = await asyncio.to_thread(
                get_pool_composition, sheet_url, config.creds_path, disc
            )
            verified = await asyncio.to_thread(
                read_verified_bouts, sheet_url, config.creds_path
            )
        except Exception as e:
            log.exception("repub_pool_matches: sheet read failed for %s", pool_id)
            await interaction.followup.send(f"❌ Failed to read sheet: {e}", ephemeral=True)
            return

        fencers = composition.get(pool_id)
        if fencers is None:
            await interaction.followup.send(
                f"❌ Pool **{pool_id}** not found in pool composition.", ephemeral=True
            )
            return

        cleared = [
            b for b in verified
            if b.get("Pool") == pool_no
            and str(b.get("Confidence", "")).strip() == ""
        ]

        await self._publish_pool(guild, disc, pool_id, cleared, fencers)

        data_dir = run_bot_data_dir(self._data_root(), guild.id)
        published = await asyncio.to_thread(load_published_pools, data_dir)
        published.add(pool_id)
        await asyncio.to_thread(save_published_pools, data_dir, published)

        await interaction.followup.send(
            f"✅ Published **{pool_id}** — {len(cleared)} cleared bout(s).", ephemeral=True
        )


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
