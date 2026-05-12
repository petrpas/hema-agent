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
import io
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
from in_tournament.msgs import read_msg as _read_in_msg
from in_tournament.render_pools import render_pools_for_disc
from in_tournament.run_setup_agent.run_setup_agent import (
    DISCIPLINE_NAMES,
    _DEFAULT_USER_CONFIG,
    _do_configure_tournament,
    _do_create_data_sheets,
    _do_validate_discipline,
    RUN_SETUP_WELCOME,
    run_run_setup_agent,
)
from in_tournament.server_layout import ANNOUNCEMENTS_CHANNEL, ROLES, SETUP_CHANNEL
from in_tournament.setup import (
    InviteSnapshot,
    assign_role_for_invite,
    detect_used_code,
    load_invite_map,
    run_bot_data_dir,
    setup_server,
)

log = logging.getLogger(__name__)

_HELP_TEXT = _read_in_msg("run_bot/help")


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
        await interaction.response.send_message(_HELP_TEXT, ephemeral=True)

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
        if message.content.startswith("/"):
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
            except discord.HTTPException as e:
                log.warning("wipe: failed to delete thread %s in #%s: %s", thread.name, channel.name, e)
        try:
            async for thread in channel.archived_threads(limit=None):
                try:
                    await thread.delete()
                    thread_count += 1
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

        raw_codes = [c.strip().upper() for c in disc_str.split(",") if c.strip()]
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

        await interaction.response.defer(ephemeral=True)

        if interaction.guild:
            await _wipe_guild_channels(interaction.guild)

        try:
            await asyncio.to_thread(
                _do_configure_tournament,
                self._cog._user_config_path(),
                name,
                lang,
                disciplines,
            )
        except Exception as e:
            await interaction.followup.send(f"Configuration failed: {e}", ephemeral=True)
            return

        if interaction.guild:
            disc_list = ", ".join(f"**{c}** ({n})" for c, n in disciplines.items())
            await self._cog._post_setup(
                interaction.guild,
                f"Tournament configured:\n**Name:** {name}\n**Language:** {lang}\n**Disciplines:** {disc_list}",
            )
        await interaction.followup.send("Configuration saved — see #setup.", ephemeral=True)


class SetupCommandsCog(commands.Cog):
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
        name="configure",
        description="Configure the tournament: name, language, and disciplines",
    )
    @app_commands.default_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_guild=True)
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

            thread_name = f"{code}_pools_validation"
            thread = discord.utils.get(setup_ch.threads, name=thread_name)
            if thread is None:
                async for t in setup_ch.archived_threads(limit=50):
                    if t.name == thread_name:
                        await t.unarchive()
                        thread = t
                        break
            if thread is None:
                thread = await setup_ch.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=10080,
                )

            await send_long(thread, report)
            posted.append(f"**{code}** → {thread_name}")

        await interaction.followup.send(
            f"Validation complete — results posted to: {', '.join(posted)} in #{SETUP_CHANNEL}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="render_pools",
        description="Render pool table PDFs and post to #setup → <disc>_pool_tables thread",
    )
    @app_commands.describe(disc="Discipline code, e.g. LS, SAW. Leave empty to render all.")
    @app_commands.default_permissions(manage_guild=True)
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
                pdfs: list[tuple[str, bytes]] = await asyncio.to_thread(
                    render_pools_for_disc, code, user_config
                )
            except ValueError as e:
                await interaction.followup.send(f"❌ {code}: {e}", ephemeral=True)
                continue
            except Exception as e:
                log.exception("render_pools failed for %s", code)
                await interaction.followup.send(f"❌ {code}: unexpected error: {e}", ephemeral=True)
                continue

            thread_name = f"{code}_pool_tables"
            thread = discord.utils.get(setup_ch.threads, name=thread_name)
            if thread is None:
                async for t in setup_ch.archived_threads(limit=50):
                    if t.name == thread_name:
                        await t.unarchive()
                        thread = t
                        break
            if thread is None:
                thread = await setup_ch.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=10080,  # 1 week
                )

            files = [
                discord.File(io.BytesIO(pdf_bytes), filename=filename)
                for filename, pdf_bytes in pdfs
            ]
            disc_name = DISCIPLINE_NAMES[code]
            await thread.send(f"**{code}** — {disc_name} ({len(pdfs)} pool(s))", files=files)
            rendered.append(f"**{code}** → {thread_name}")

        if rendered:
            await interaction.followup.send(
                f"Rendered pools for: {', '.join(rendered)} — see #{SETUP_CHANNEL}.",
                ephemeral=True,
            )

    @app_commands.command(
        name="publish_pools",
        description="Publish pool tables for fencers into #announcements → <disc>_pools thread",
    )
    @app_commands.describe(disc="Discipline code, e.g. LS, SAW")
    @app_commands.default_permissions(manage_guild=True)
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
            pdfs: list[tuple[str, bytes]] = await asyncio.to_thread(
                render_pools_for_disc, disc, user_config
            )
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

        thread_name = f"{disc}_pools"
        thread = discord.utils.get(ann_ch.threads, name=thread_name)
        if thread is None:
            async for t in ann_ch.archived_threads(limit=50):
                if t.name == thread_name:
                    await t.unarchive()
                    thread = t
                    break
        if thread is None:
            thread = await ann_ch.create_thread(
                name=thread_name,
                type=discord.ChannelType.public_thread,
                auto_archive_duration=10080,  # 1 week
            )

        files = [
            discord.File(io.BytesIO(pdf_bytes), filename=filename)
            for filename, pdf_bytes in pdfs
        ]
        disc_name = DISCIPLINE_NAMES[disc]
        await thread.send(f"**{disc}** — {disc_name} ({len(pdfs)} pool(s))", files=files)

        await interaction.followup.send(
            f"Published {len(pdfs)} pool table(s) for **{disc}** — "
            f"see **{thread_name}** thread in #{ANNOUNCEMENTS_CHANNEL}.",
            ephemeral=True,
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
