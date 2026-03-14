"""Shared Discord utility helpers."""

import logging

import discord
from table2ascii import Alignment, PresetStyle, table2ascii as _table2ascii

log = logging.getLogger(__name__)

DISCORD_MAX_LEN = 2000


def _split_for_discord(text: str, max_len: int = DISCORD_MAX_LEN) -> list[str]:
    """Split text into chunks that fit within max_len, closing and reopening code fences at splits."""
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    chunk = ""
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        toggles = stripped.startswith("```") and stripped.count("```") == 1

        # Force-split a single line that is itself too long
        while len(line) > max_len:
            if chunk:
                chunks.append(chunk)
                chunk = ""
            chunks.append(line[:max_len])
            line = line[max_len:]

        if len(chunk) + len(line) > max_len:
            close = "```\n" if in_code_block else ""
            chunks.append(chunk + close)
            chunk = ("```\n" if in_code_block else "") + line
        else:
            chunk += line

        if toggles:
            in_code_block = not in_code_block

    if chunk:
        chunks.append(chunk)
    return chunks


async def send_long(channel: discord.abc.Messageable, text: str) -> discord.Message:
    """Send text to a channel, splitting into chunks if it exceeds Discord's 2000 char limit.

    Preserves code block fences across splits.
    Returns the first message sent.
    """
    chunks = _split_for_discord(text)
    if len(chunks) > 1:
        log.warning("send_long splitting into %d chunks (total %d chars)", len(chunks), len(text))
    first: discord.Message | None = None
    for chunk in chunks:
        msg = await channel.send(chunk)
        if first is None:
            first = msg
    return first  # type: ignore[return-value]


def make_table(header: list[str], rows: list[list[str]]) -> str:
    """Format data as a left-aligned ASCII table wrapped in a Discord code block."""
    alignments = [Alignment.LEFT] * len(header)
    table = _table2ascii(
        header=header,
        body=rows or [["(no data)"] + [""] * (len(header) - 1)],
        style=PresetStyle.thin_compact,
        alignments=alignments,
    )
    return f"```\n{table}\n```"
