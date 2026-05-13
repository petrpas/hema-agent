"""Run-bot Discord server layout — roles, categories, channels, permissions.

Edit this file to change the live-tournament server's structure. The setup
module reads the layout and applies it idempotently: missing pieces are
created, existing pieces are reused, and permission overrides are
drift-corrected on every run.
"""

from dataclasses import dataclass
from enum import StrEnum


class Perm(StrEnum):
    NONE = "none"  # invisible
    RO   = "ro"    # read-only (view + read history)
    RW   = "rw"    # read-write (view + read history + send + embed + attach)


@dataclass(frozen=True)
class RoleSpec:
    name: str
    # If True, an open invite is minted at setup time and new members joining
    # via that invite are auto-assigned this role.
    auto_assign_via_invite: bool = False


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    # role name → permission. `@everyone` is implicitly NONE everywhere
    # (denied View Channel at the guild level).
    overrides: dict[str, Perm]


@dataclass(frozen=True)
class CategorySpec:
    name: str
    channels: list[ChannelSpec]


# ── Role names ────────────────────────────────────────────────────────────────

ROLE_ADMIN     = "Admin"
ROLE_ORGANIZER = "Organizer"
ROLE_GUEST     = "Guest"
ROLE_BOT       = "Bot"

# ── Channel names (used by cogs that watch specific channels) ─────────────────

WELCOME_CHANNEL        = "welcome"
ANNOUNCEMENTS_CHANNEL  = "announcements"
SCHEDULE_CHANNEL       = "schedule"
RESULTS_CHANNEL        = "results"
RULES_CHANNEL          = "rules"

GENERAL_CHAT_CHANNEL   = "general-chat"
SPARRING_CHANNEL       = "looking-for-sparring"
QUESTIONS_CHANNEL      = "questions"

SETUP_CHANNEL          = "setup"
ORG_INTERNAL_CHANNEL   = "org-internal"
RESULTS_UPLOAD_CHANNEL = "org-results-upload"
BOT_COMMANDS_CHANNEL   = "bot-commands"

# ── Layout ────────────────────────────────────────────────────────────────────

ROLES: list[RoleSpec] = [
    RoleSpec(ROLE_ADMIN,     auto_assign_via_invite=True),
    RoleSpec(ROLE_ORGANIZER, auto_assign_via_invite=True),
    RoleSpec(ROLE_GUEST,     auto_assign_via_invite=True),
    RoleSpec(ROLE_BOT),
]


def _general(name: str, admin: Perm, organizer: Perm, guest: Perm, bot: Perm) -> ChannelSpec:
    return ChannelSpec(name, {
        ROLE_ADMIN: admin,
        ROLE_ORGANIZER: organizer,
        ROLE_GUEST: guest,
        ROLE_BOT: bot,
    })


LAYOUT: list[CategorySpec] = [
    CategorySpec("General", [
        _general(WELCOME_CHANNEL,       Perm.RW, Perm.RW, Perm.RO, Perm.RW),
        _general(ANNOUNCEMENTS_CHANNEL, Perm.RW, Perm.RW, Perm.RO, Perm.RW),
        _general(SCHEDULE_CHANNEL,      Perm.RW, Perm.RW, Perm.RO, Perm.RW),
        _general(RESULTS_CHANNEL,       Perm.RO, Perm.RO, Perm.RO, Perm.RW),
        _general(RULES_CHANNEL,         Perm.RW, Perm.RW, Perm.RO, Perm.RO),
    ]),
    CategorySpec("Community", [
        _general(GENERAL_CHAT_CHANNEL,  Perm.RW, Perm.RW, Perm.RW, Perm.RO),
        _general(SPARRING_CHANNEL,      Perm.RW, Perm.RW, Perm.RW, Perm.RO),
        _general(QUESTIONS_CHANNEL,     Perm.RW, Perm.RW, Perm.RW, Perm.RO),
    ]),
    CategorySpec("Organization", [
        # #setup: Admin-only — Organizers can't configure the bot
        _general(SETUP_CHANNEL,          Perm.RW, Perm.NONE, Perm.NONE, Perm.RW),
        _general(ORG_INTERNAL_CHANNEL,   Perm.RW, Perm.RW,   Perm.NONE, Perm.RW),
        _general(RESULTS_UPLOAD_CHANNEL, Perm.RW, Perm.RW,   Perm.NONE, Perm.RW),
        _general(BOT_COMMANDS_CHANNEL,   Perm.RW, Perm.RW,   Perm.NONE, Perm.RW),
    ]),
]
