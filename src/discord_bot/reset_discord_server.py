"""Reset a Discord server to a blank slate — deletes all channels, categories,
and non-default roles.

Reads DISCORD_TOKEN from .env (repo root) or the environment.
GUILD_ID can be passed as a CLI argument or set in the environment; if omitted
the script lists the guild IDs it finds under data/run_bot/ to help you choose.

Usage:
    python reset_discord_server.py [GUILD_ID]
"""

import asyncio
import os
import sys
from pathlib import Path

# Load .env from repo root before anything else.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_env_file = _REPO_ROOT / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

import discord  # noqa: E402


def _known_guild_ids() -> list[str]:
    data_dir = _HERE / "data" / "run_bot"
    if not data_dir.exists():
        return []
    return [p.name for p in data_dir.iterdir() if p.is_dir() and p.name.isdigit()]


async def reset(token: str, guild_id: int) -> None:
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    async with client:
        await client.login(token)
        guild = await client.fetch_guild(guild_id)

        print(f"\nAbout to wipe ALL channels, categories, and non-default roles from:")
        print(f"  {guild.name}  (id={guild.id})\n")
        answer = input("Type 'yes' to continue: ").strip()
        if answer != "yes":
            print("Aborted.")
            return

        channels = await guild.fetch_channels()
        for ch in channels:
            try:
                await ch.delete(reason="dev reset")
                print(f"  deleted channel  #{ch.name}")
            except discord.HTTPException as e:
                print(f"  WARN: could not delete #{ch.name}: {e}")

        roles = await guild.fetch_roles()
        for role in roles:
            if role.is_default() or role.managed:
                continue
            try:
                await role.delete(reason="dev reset")
                print(f"  deleted role     @{role.name}")
            except discord.HTTPException as e:
                print(f"  WARN: could not delete @{role.name}: {e}")

        print("\nDone.")


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not set (checked environment and .env).")
        sys.exit(1)

    guild_id_str = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GUILD_ID", "")
    if not guild_id_str:
        known = _known_guild_ids()
        if known:
            print(f"Usage: python reset_discord_server.py GUILD_ID")
            print(f"\nKnown guild IDs from data/run_bot/:")
            for gid in known:
                print(f"  {gid}")
        else:
            print("Usage: python reset_discord_server.py GUILD_ID")
        sys.exit(1)

    try:
        guild_id = int(guild_id_str)
    except ValueError:
        print(f"Error: GUILD_ID must be an integer, got {guild_id_str!r}")
        sys.exit(1)

    asyncio.run(reset(token, guild_id))


if __name__ == "__main__":
    main()
