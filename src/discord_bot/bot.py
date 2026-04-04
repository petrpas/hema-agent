"""Discord bot for HEMA tournament management."""

# Load .env before any other import so that env-dependent module-level code
# (e.g. config.tracing.enabled) sees the correct values.
from dotenv import load_dotenv
load_dotenv()

import asyncio
import fcntl
import logging
import os
import sys
from pathlib import Path

from discord_bot.msg_constants import REGISTRATION_CHANEL_NAME, SETUP_CHANEL_NAME, SETUP_WELCOME, POOLS_CHANNEL_NAME

# Make reg_agent importable when bot.py is run directly from src/discord/
_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import discord
from discord import app_commands
from discord.ext import commands


from reg_agent.reg_agent import run_agent, _PAYMENTS_THREAD_PREFIX
from reg_agent.step1_download import save_registration_csv
from reg_agent.step7_payments import parse_and_store, load_all_parsed
from setup_agent.setup_agent import run_setup_agent
from pool_alch_agent.pool_alch_agent import run_pool_alch_agent, PoolAlchDeps
from pool_alch_agent.state import load_state, deps_from_state
from config import load_config, RegConfig

log = logging.getLogger(__name__)



# channel name → welcome message posted on creation
CHANNEL_WELCOMES: dict[str, str] = {
    SETUP_CHANEL_NAME: SETUP_WELCOME,
}


class HemaTournamentBot(commands.Bot):
    config: RegConfig | None = None

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        user_config_path = os.environ.get("USER_CONFIG")
        try:
            # agent_config.json is auto-discovered at src/config/agent_config.json
            self.config = load_config(user_config_path)
            log.info("Loaded config: tournament=%s", self.config.tournament_name)
        except Exception as e:
            log.warning("Could not load config (USER_CONFIG=%s): %s", user_config_path, e)

        await self.add_cog(GeneralCog(self))
        await self.add_cog(SetupCog(self))
        await self.add_cog(RegistrationCog(self))
        await self.add_cog(PoolsCog(self))

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

    def __init__(self, bot: HemaTournamentBot) -> None:
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

        # The oldest message is the welcome/manual message — keep it
        first_msg: discord.Message | None = None
        async for msg in channel.history(limit=1, oldest_first=True):
            first_msg = msg

        to_delete: list[discord.Message] = []
        async for msg in channel.history(limit=None):
            if first_msg is None or msg.id != first_msg.id:
                to_delete.append(msg)

        # Bulk-delete in chunks of 100 (Discord API limit per request)
        for i in range(0, len(to_delete), 100):
            await channel.delete_messages(to_delete[i : i + 100])

        count = len(to_delete)
        await interaction.followup.send(f"Cleared {count} message(s).", ephemeral=True)
        log.info(
            "Cleared %d messages in #%s (%s)", count, channel.name, interaction.guild
        )

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync(self, ctx: commands.Context) -> None:
        """Sync slash commands to this guild instantly (owner only, use !sync)."""
        await self.bot.tree.sync(guild=ctx.guild)
        await ctx.send("Slash commands synced to this server.", delete_after=5)
        log.info("Tree synced to guild %s by owner", ctx.guild)

    @app_commands.command(
        name="setup",
        description="Create tournament channels and post welcome messages",
    )
    @app_commands.default_permissions(manage_channels=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Must be used inside a server.", ephemeral=True)
            return

        created: list[str] = []
        for ch_name, welcome_msg in CHANNEL_WELCOMES.items():
            existing = discord.utils.get(guild.text_channels, name=ch_name)
            if existing is None:
                ch = await guild.create_text_channel(ch_name)
                await ch.send(welcome_msg)
                created.append(ch_name)
                log.info("Created #%s in %s", ch_name, guild)
            else:
                log.info("#%s already exists in %s, skipping", ch_name, guild)

        if created:
            names = ", ".join(f"#{c}" for c in created)
            await interaction.followup.send(
                f"Created channel(s): {names}", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "All tournament channels already exist.", ephemeral=True
            )


class SetupCog(commands.Cog):
    """Handles the #setup channel — delegates to the setup_agent."""

    _running: set[int] = set()

    def __init__(self, bot: HemaTournamentBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.bot.user:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.channel.name != SETUP_CHANEL_NAME:
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
                await run_setup_agent(message.channel, message.content, **kwargs)
        finally:
            self._running.discard(message.channel.id)


class RegistrationCog(commands.Cog):
    """Handles the #registration channel — delegates to the reg_agent."""

    # Class-level so it's shared across all instances (guards against accidental double-cog).
    _running: set[int] = set()
    _processed_ids: set[int] = set()  # message IDs already dispatched this session

    def __init__(self, bot: HemaTournamentBot) -> None:
        self.bot = bot

    def _check_registration_channel(self, channel: discord.abc.Messageable) -> bool:
        return isinstance(channel, discord.TextChannel) and channel.name == REGISTRATION_CHANEL_NAME

    async def _invoke_agent(
        self,
        channel: discord.TextChannel,
        message_content: str,
        response_channel: discord.abc.Messageable | None = None,
    ) -> None:
        reply_to: discord.abc.Messageable = response_channel or channel
        if self.bot.config is None:
            user_config_path = os.environ.get("USER_CONFIG")
            try:
                self.bot.config = load_config(user_config_path)
                log.info("Config loaded on demand: tournament=%s", self.bot.config.tournament_name)
            except Exception as e:
                log.warning("Config reload failed (USER_CONFIG=%s): %s", user_config_path, e)
                await reply_to.send(
                    "⚠ No tournament config loaded. Set `USER_CONFIG` env var and restart the bot."
                )
                return

        if channel.id in self._running:
            log.warning("Concurrent run blocked for channel %s", channel.id)
            await reply_to.send("⏳ Already processing — please wait.")
            return

        self._running.add(channel.id)
        try:
            try:
                async with reply_to.typing():
                    await run_agent(channel, message_content, self.bot.config, response_channel=reply_to)
            except discord.HTTPException as e:
                if e.status == 429:
                    log.warning("Typing indicator rate-limited, running without it")
                    await run_agent(channel, message_content, self.bot.config, response_channel=reply_to)
                else:
                    raise
        finally:
            self._running.discard(channel.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.bot.user:
            return

        # Thread messages: handle payments thread, ignore all other threads
        if isinstance(message.channel, discord.Thread):
            log.info(
                "Thread message from %s in '%s' (attachments=%d)",
                message.author, message.channel.name, len(message.attachments),
            )
            if message.channel.name.startswith(_PAYMENTS_THREAD_PREFIX):
                try:
                    await self._handle_payments_thread_message(message)
                except Exception:
                    log.exception("Unhandled error in _handle_payments_thread_message")
                    try:
                        await message.channel.send("⚠ Unexpected error — check bot logs.")
                    except Exception:
                        pass
            return

        if not self._check_registration_channel(message.channel):
            return
        if message.content.startswith("/"):
            return

        if message.id in self._processed_ids:
            log.warning("Duplicate on_message for id=%s — skipping", message.id)
            return
        self._processed_ids.add(message.id)

        csv_attachment = next(
            (a for a in message.attachments if a.filename.lower().endswith(".csv")), None
        )
        if csv_attachment is not None:
            await self._handle_csv_upload(message, csv_attachment)
            return

        log.info("Registration message from %s: %s", message.author, message.content[:80])
        await self._invoke_agent(message.channel, message.content)  # type: ignore[arg-type]

    async def _handle_csv_upload(
        self, message: discord.Message, attachment: discord.Attachment
    ) -> None:
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        if self.bot.config is None:
            await channel.send("⚠ No tournament config loaded. Set `USER_CONFIG` env var and restart the bot.")
            return
        try:
            data = await attachment.read()
            path = save_registration_csv(self.bot.config, data)
            log.info("Saved uploaded CSV from %s → %s", message.author, path.name)
        except Exception as e:
            log.exception("Failed to save uploaded CSV")
            await channel.send(f"⚠ Could not save the uploaded file: {e}")
            return
        synthetic = f"[system: organiser uploaded a CSV file — {attachment.filename} saved as {path.name}. Decide what to do based on current pipeline state.]"
        await self._invoke_agent(channel, synthetic)

    async def _handle_payments_thread_message(self, message: discord.Message) -> None:
        thread = message.channel
        if not isinstance(thread, discord.Thread):
            return
        if self.bot.config is None:
            user_config_path = os.environ.get("USER_CONFIG")
            try:
                self.bot.config = load_config(user_config_path)
            except Exception as e:
                await thread.send(f"⚠ No tournament config loaded: {e}")
                return

        if message.attachments:
            async with thread.typing():
                for attachment in message.attachments:
                    await self._parse_and_store_payment_file(attachment, thread, self.bot.config)
            # Post total count across all parsed files
            all_txns = load_all_parsed(self.bot.config.data_dir)
            parsed_dir = self.bot.config.data_dir / "payments" / "parsed"
            file_count = len(list(parsed_dir.glob("*.json"))) if parsed_dir.exists() else 0
            await thread.send(
                f"📊 {len(all_txns)} transaction(s) total across {file_count} file(s)."
            )
        else:
            # Text message in payments thread → forward to agent via synthetic message
            parent = thread.parent
            if not isinstance(parent, discord.TextChannel):
                return
            synthetic = f"[system: organiser said in 💰 Payments thread: {message.content}]"
            await self._invoke_agent(parent, synthetic, response_channel=thread)

    async def _parse_and_store_payment_file(
        self,
        attachment: discord.Attachment,
        thread: discord.Thread,
        config: RegConfig,
    ) -> None:
        try:
            data = await attachment.read()
            content = data.decode("utf-8", errors="replace")
            log.info("Parsing payment file from thread: %s (%d bytes)", attachment.filename, len(data))
            txns = await asyncio.to_thread(
                parse_and_store, content, attachment.filename, config.data_dir, config
            )
            await thread.send(f"✅ Parsed {len(txns)} transaction(s) from **{attachment.filename}**")
        except Exception as e:
            log.exception("Failed to parse payment file %s", attachment.filename)
            await thread.send(f"⚠ Could not parse **{attachment.filename}**: {e}")

    @app_commands.command(name="run", description="Start or continue the registration pipeline")
    @app_commands.default_permissions(manage_messages=True)
    async def run_pipeline(self, interaction: discord.Interaction) -> None:
        if not self._check_registration_channel(interaction.channel):
            await interaction.response.send_message(
                "This command can only be used in #registration.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("Starting…", ephemeral=True)
        await self._invoke_agent(
            interaction.channel,  # type: ignore[arg-type]
            f"{interaction.user.display_name} used /run",
        )

    @app_commands.command(name="status", description="Show current registration pipeline status")
    async def status(self, interaction: discord.Interaction) -> None:
        if not self._check_registration_channel(interaction.channel):
            await interaction.response.send_message(
                "This command can only be used in #registration.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("Checking status…", ephemeral=True)
        await self._invoke_agent(
            interaction.channel,  # type: ignore[arg-type]
            f"{interaction.user.display_name} used /status — summarise the current pipeline state without running any steps.",
        )


class PoolsCog(commands.Cog):
    """Handles the #hsq-pools-alchemy channel — delegates to the pool_alch_agent."""

    _running: set[int] = set()
    _deps: dict[int, PoolAlchDeps] = {}  # channel_id → persistent deps across turns

    def __init__(self, bot: HemaTournamentBot) -> None:
        self.bot = bot

    def _get_deps(self, channel: discord.TextChannel, config) -> PoolAlchDeps:
        """Return existing in-memory deps, or restore from disk, or create fresh."""
        if channel.id in self._deps:
            return self._deps[channel.id]
        state = load_state(config)
        if state is not None:
            deps = deps_from_state(state, channel, config)
        else:
            deps = PoolAlchDeps(channel=channel, config=config)
        self._deps[channel.id] = deps
        return deps

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.bot.user:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.channel.name != POOLS_CHANNEL_NAME:
            return
        if message.content.startswith("/"):
            return

        if self.bot.config is None:
            user_config_path = os.environ.get("USER_CONFIG")
            try:
                self.bot.config = load_config(user_config_path)
            except Exception:
                await message.channel.send(
                    "⚠ No tournament config loaded. Set `USER_CONFIG` env var and restart the bot."
                )
                return

        config = self.bot.config
        assert config is not None
        if message.channel.id in self._running:
            await message.channel.send("⏳ Already processing — please wait.")
            return

        self._running.add(message.channel.id)
        try:
            async with message.channel.typing():
                deps = self._get_deps(message.channel, config)
                await run_pool_alch_agent(message.channel, message.content, config, deps)
        finally:
            self._running.discard(message.channel.id)


_lock_fh = None  # module-level so it stays alive (GC would release it)


def _acquire_instance_lock() -> None:
    """Prevent two bot processes from running simultaneously using an exclusive file lock.

    Uses fcntl.flock so the OS releases the lock automatically if the process dies.
    Exits with a clear error if the lock is already held.
    """
    global _lock_fh
    lock_path = Path(os.environ.get("BOT_LOCK_FILE", "/tmp/hema-bot.lock"))
    _lock_fh = lock_path.open("w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
    except BlockingIOError:
        sys.exit(f"ERROR: another bot instance is already running (lock held: {lock_path}). Exiting.")


def run() -> None:
    logging.basicConfig(
        handlers=[
            logging.FileHandler("discord.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)-22s %(message)s",
    )
    _acquire_instance_lock()
    token = os.environ["DISCORD_TOKEN"]
    bot = HemaTournamentBot()
    bot.run(token, log_handler=None)  # log_handler=None preserves our basicConfig


if __name__ == "__main__":
    run()