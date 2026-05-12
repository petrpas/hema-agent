"""Server setup — applies the run_bot Discord layout idempotently.

Reads `server_layout.LAYOUT` and the role list, then on a guild:
- creates missing roles, reuses existing ones by name;
- denies @everyone the View Channel permission at the guild level;
- creates missing categories and channels, reuses existing ones by name;
- drift-corrects per-channel permission overrides on every run;
- mints (or reuses) one open invite per `auto_assign_via_invite` role and
  writes a PNG QR code for each;
- persists the `invite code → role name` map so `on_member_join` can
  look up which role to assign.

Safe to re-run; nothing is duplicated and no manual edits to message text
are touched.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import discord
import qrcode

from in_tournament.server_layout import (
    LAYOUT,
    ROLES,
    CategorySpec,
    ChannelSpec,
    Perm,
    RoleSpec,
)

log = logging.getLogger(__name__)

INVITES_FILE = "invites.json"

_SHARED_DIR  = Path(__file__).parent.parent / "shared"
_QR_TEMPLATE = _SHARED_DIR / "typst" / "templates" / "qr.typ"
_FONTS_DIR   = _SHARED_DIR / "typst" / "fonts"


# ── Public types ──────────────────────────────────────────────────────────────

@dataclass
class SetupReport:
    roles_created: list[str]
    roles_reused: list[str]
    categories_created: list[str]
    channels_created: list[str]
    invites: dict[str, str]      # role_name → invite URL
    qr_paths: dict[str, Path]    # role_name → QR PNG path

    def summary(self) -> str:
        lines = []
        if self.roles_created:
            lines.append(f"Created roles: {', '.join(self.roles_created)}")
        if self.roles_reused:
            lines.append(f"Reused roles: {', '.join(self.roles_reused)}")
        if self.categories_created:
            lines.append(f"Created categories: {', '.join(self.categories_created)}")
        if self.channels_created:
            lines.append(f"Created channels: {', '.join(self.channels_created)}")
        for role, url in self.invites.items():
            lines.append(f"Invite ({role}): {url}")
        return "\n".join(lines) if lines else "Nothing to do — server already matches layout."


# ── Path helpers ──────────────────────────────────────────────────────────────

def run_bot_data_dir(data_root: Path, guild_id: int) -> Path:
    """Per-guild data dir for run_bot (invites, QR codes).

    Keyed by guild_id rather than tournament name so it works before the
    setup-agent dialog has set `tournament_name`.
    """
    return data_root / "run_bot" / str(guild_id)


# ── Invite-code persistence ───────────────────────────────────────────────────

def load_invite_map(data_dir: Path) -> dict[str, str]:
    """Return `{invite_code: role_name}`, or empty dict if no file."""
    p = data_dir / INVITES_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, ValueError):
        log.warning("Corrupt invites.json at %s — treating as empty", p)
        return {}


def save_invite_map(data_dir: Path, mapping: dict[str, str]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / INVITES_FILE).write_text(json.dumps(mapping, indent=2))


# ── Permission translation ────────────────────────────────────────────────────

def _overwrites_for(perm: Perm) -> discord.PermissionOverwrite:
    if perm == Perm.NONE:
        return discord.PermissionOverwrite(view_channel=False)
    if perm == Perm.RO:
        return discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
        )
    # Perm.RW
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
    )


def _build_overwrites(
    spec: ChannelSpec,
    role_map: dict[str, discord.Role],
    everyone: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    out: dict[discord.Role, discord.PermissionOverwrite] = {
        everyone: discord.PermissionOverwrite(view_channel=False),
    }
    for role_name, perm in spec.overrides.items():
        role = role_map.get(role_name)
        if role is not None:
            out[role] = _overwrites_for(perm)
    return out


# ── Role / channel application ────────────────────────────────────────────────

async def _ensure_roles(
    guild: discord.Guild,
    specs: list[RoleSpec],
    report: SetupReport,
) -> dict[str, discord.Role]:
    by_name = {r.name: r for r in guild.roles}
    out: dict[str, discord.Role] = {}
    for spec in specs:
        role = by_name.get(spec.name)
        if role is None:
            role = await guild.create_role(name=spec.name, mentionable=True, reason="run_bot setup")
            report.roles_created.append(spec.name)
            log.info("Created role %s in %s", spec.name, guild.name)
        else:
            report.roles_reused.append(spec.name)
        out[spec.name] = role
    return out


async def _deny_everyone_view(guild: discord.Guild) -> None:
    """Strip View Channel from @everyone at the guild level if currently granted."""
    everyone = guild.default_role
    if everyone.permissions.view_channel:
        new_perms = everyone.permissions
        new_perms.update(view_channel=False)
        await everyone.edit(permissions=new_perms, reason="run_bot setup: visibility via roles only")
        log.info("Denied @everyone View Channel in %s", guild.name)


async def _ensure_category(
    guild: discord.Guild,
    spec: CategorySpec,
    report: SetupReport,
) -> discord.CategoryChannel:
    existing = discord.utils.get(guild.categories, name=spec.name)
    if existing is not None:
        return existing
    cat = await guild.create_category(spec.name, reason="run_bot setup")
    report.categories_created.append(spec.name)
    log.info("Created category %s in %s", spec.name, guild.name)
    return cat


async def _ensure_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    spec: ChannelSpec,
    role_map: dict[str, discord.Role],
    report: SetupReport,
) -> discord.TextChannel:
    overwrites = _build_overwrites(spec, role_map, guild.default_role)
    existing = discord.utils.get(guild.text_channels, name=spec.name)
    if existing is not None:
        return existing
    ch = await guild.create_text_channel(
        spec.name,
        category=category,
        overwrites=overwrites,
        reason="run_bot setup",
    )
    report.channels_created.append(spec.name)
    log.info("Created channel #%s under %s in %s", spec.name, category.name, guild.name)
    return ch


# ── Invites + QR codes ────────────────────────────────────────────────────────

async def _ensure_invites(
    guild: discord.Guild,
    role_specs: list[RoleSpec],
    invite_channel: discord.TextChannel,
    data_dir: Path,
    report: SetupReport,
) -> None:
    """Mint one open invite per auto-assigning role; reuse existing ones by stored code."""
    stored = load_invite_map(data_dir)  # {code: role_name}
    try:
        live_codes = {inv.code: inv for inv in await guild.invites()}
    except discord.Forbidden:
        log.warning(
            "Cannot read invites in %s (missing Manage Guild permission) — "
            "re-invite the bot with the correct OAuth URL; skipping invite setup",
            guild.name,
        )
        return

    # Reverse: which roles already have a live, stored invite?
    role_to_existing: dict[str, discord.Invite] = {}
    for code, role_name in stored.items():
        inv = live_codes.get(code)
        if inv is not None:
            role_to_existing[role_name] = inv

    for spec in role_specs:
        if not spec.auto_assign_via_invite:
            continue
        existing = role_to_existing.get(spec.name)
        if existing is not None:
            url = existing.url
        else:
            new_invite = await invite_channel.create_invite(
                max_age=0,
                max_uses=0,
                unique=True,
                reason=f"run_bot setup: {spec.name} auto-assignment",
            )
            stored[new_invite.code] = spec.name
            url = new_invite.url
            log.info("Minted %s invite %s in %s", spec.name, new_invite.code, guild.name)
        report.invites[spec.name] = url
        report.qr_paths[spec.name] = _write_qr(url, data_dir / f"qr_{spec.name.lower()}.png", spec.name)

    save_invite_map(data_dir, stored)


_QR_FILL_COLORS: dict[str, str] = {
    "organizer": "#8B0000",  # dark red — distinct from guest QR
}


def _write_qr(url: str, out_path: Path, role_name: str = "") -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fill_color = _QR_FILL_COLORS.get(role_name.lower(), "black")
    qr = qrcode.QRCode()
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill_color, back_color="white")
    img.save(out_path)
    return out_path


def render_qr_pdf(png_path: Path, tournament_name: str) -> Path:
    """Render a QR poster PDF from qr.typ alongside an existing QR PNG.

    Writes <png_path.stem>.pdf next to the PNG and returns the path.
    The temp source file is written into the same directory so that
    Typst resolves `image("<png_path.name>")` correctly.
    """
    import typst

    source = (
        _QR_TEMPLATE.read_text(encoding="utf-8")
        .replace("{{tournament_name}}", tournament_name)
        .replace("{{qr_png}}", png_path.name)
    )
    out_pdf = png_path.with_suffix(".pdf")
    with tempfile.NamedTemporaryFile(
        suffix=".typ", mode="w", encoding="utf-8",
        dir=png_path.parent, delete=False,
    ) as f:
        f.write(source)
        tmp = Path(f.name)
    try:
        pdf_bytes = typst.compile(str(tmp), format="pdf", font_paths=[str(_FONTS_DIR)])
        out_pdf.write_bytes(pdf_bytes)
    finally:
        tmp.unlink(missing_ok=True)
    log.info("Rendered QR PDF: %s", out_pdf)
    return out_pdf


# ── Top-level entry point ─────────────────────────────────────────────────────

async def setup_server(guild: discord.Guild, data_root: Path) -> SetupReport:
    """Apply the full layout to a guild idempotently. Returns a report.

    `data_root` is the bot's data root (e.g. `RegConfig.data_root_dir` or the
    Fly volume root). Per-guild files land under `<data_root>/run_bot/<guild_id>/`.
    """
    report = SetupReport(
        roles_created=[],
        roles_reused=[],
        categories_created=[],
        channels_created=[],
        invites={},
        qr_paths={},
    )
    data_dir = run_bot_data_dir(data_root, guild.id)
    data_dir.mkdir(parents=True, exist_ok=True)

    await _deny_everyone_view(guild)
    role_map = await _ensure_roles(guild, ROLES, report)

    # Assign the Bot role to the bot member NOW, before editing channels.
    # Existing channels may already have {everyone: view_channel=False} as a
    # channel-level overwrite (from a previous /setup run). That overwrite
    # takes priority over the bot's guild-level managed-role permissions, so
    # the bot loses VIEW_CHANNEL on those channels. Holding the custom Bot role
    # (which has an explicit allow) restores access before we try to edit them.
    bot_role = role_map.get("Bot")
    me = guild.me
    if bot_role is not None and me is not None and bot_role not in me.roles:
        try:
            await me.add_roles(bot_role, reason="run_bot setup: self-tag")
        except discord.Forbidden:
            log.warning("Cannot self-assign Bot role in %s (insufficient permissions)", guild.name)

    invite_channel: discord.TextChannel | None = None
    for cat_spec in LAYOUT:
        cat = await _ensure_category(guild, cat_spec, report)
        for ch_spec in cat_spec.channels:
            ch = await _ensure_channel(guild, cat, ch_spec, role_map, report)
            # Use the welcome channel as the invite source so URLs land in
            # the public landing channel.
            if ch.name == "welcome":
                invite_channel = ch

    if invite_channel is None:
        log.warning("welcome channel missing — skipping invite minting")
        return report

    await _ensure_invites(guild, ROLES, invite_channel, data_dir, report)
    return report


# ── on_member_join helper ─────────────────────────────────────────────────────

@dataclass
class InviteSnapshot:
    """In-memory `code → uses` snapshot, refreshed on relevant events."""

    by_code: dict[str, int]

    @classmethod
    async def from_guild(cls, guild: discord.Guild) -> "InviteSnapshot":
        try:
            invites = await guild.invites()
        except discord.Forbidden:
            log.warning("Cannot read invites in %s — auto-role disabled", guild.name)
            return cls({})
        return cls({inv.code: inv.uses or 0 for inv in invites})


async def detect_used_code(
    guild: discord.Guild, prev: InviteSnapshot
) -> tuple[str | None, InviteSnapshot]:
    """Diff `guild.invites()` against `prev` to find which code was just used.

    Returns `(code_or_None, fresh_snapshot)`. `code` is None if the diff is
    ambiguous (e.g. zero or multiple increments — typically a vanity URL or
    the cache was empty/stale).
    """
    fresh = await InviteSnapshot.from_guild(guild)
    candidates = [
        code for code, uses in fresh.by_code.items()
        if uses > prev.by_code.get(code, 0)
    ]
    if len(candidates) == 1:
        return candidates[0], fresh
    return None, fresh


async def assign_role_for_invite(
    member: discord.Member,
    code: str | None,
    invite_map: dict[str, str],
    role_map: dict[str, discord.Role],
) -> str | None:
    """Assign the role mapped to `code` if known; return the role name (or None)."""
    if code is None:
        return None
    role_name = invite_map.get(code)
    if role_name is None:
        return None
    role = role_map.get(role_name)
    if role is None:
        return None
    try:
        await member.add_roles(role, reason=f"auto-assign via invite {code}")
    except discord.Forbidden:
        log.warning("Cannot add role %s to %s (insufficient permissions)", role_name, member)
        return None
    return role_name
