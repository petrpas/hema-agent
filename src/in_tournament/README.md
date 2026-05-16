# Setting up a live-tournament Discord server

This guide covers the full process of spinning up the **run_bot** for a new HEMA tournament — from creating a Discord server to completing the in-channel setup dialogue.

`run_bot` is independent from `pre_bot` (pre-tournament prep). It runs on the **tournament-day** server that fencers join on site.

---

## 1. Prerequisites

- Python environment with this repo's dependencies installed (`pip install -e .`)
- An **Anthropic API key** (`ANTHROPIC_API_KEY`)
- A **Discord bot token** (see step 2)
- The bot process must be reachable and running before step 4

---

## 2. Create a Discord server

Create a plain, empty Discord server (UI: **Add a Server → Create My Own → For a club or community**). Give it a name, e.g. "HEMA NA2025". Do not add any channels or roles — `/setup` handles all of that.

---

## 3. Create a Discord bot application

1. Go to https://discord.com/developers/applications and click **New Application**.
2. Give it the same name as the tournament (e.g. "HEMA Run NA2025").
3. Go to **Bot** → click **Add Bot**.
4. Under **Privileged Gateway Intents**, enable:
   - **Server Members Intent** — required for auto-role assignment on join
   - **Message Content Intent** — required for reading messages in channels
5. Click **Reset Token** and copy the token — set it as `DISCORD_TOKEN` in your environment.
6. Invite the bot to the server with this URL (replace `YOUR_CLIENT_ID` with the **Application ID** from **General Information**):
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=268561457&scope=bot+applications.commands
   ```

   The permissions integer (`268561457`) grants exactly what the bot needs:

   | Permission | Why |
   |---|---|
   | Create Invite | Mint the Organizer and Guest invite links |
   | Manage Guild | List existing invites (required for auto-role on join) |
   | Manage Roles | Create Admin / Organizer / Guest / Bot roles and assign them on join |
   | Manage Channels | Create the three categories and twelve channels |
   | View Channels | Read any channel |
   | Send Messages | Post in text channels |
   | Embed Links | Rich embeds in results and announcements |
   | Manage Messages | `/clear` bulk-deletes messages |
   | Attach Files | Post QR code PNGs after `/setup` |
   | Read Message History | `/clear` and setup-agent channel history |

---

## 4. Run the bot

Set the required environment variables and start the bot:

```bash
export DISCORD_TOKEN=<your-bot-token>
export ANTHROPIC_API_KEY=<your-anthropic-key>

# Optional: point to a specific user_config.json (defaults to src/shared/config/user_config.json)
export USER_CONFIG=/path/to/user_config.json

python -m discord_bot.run_bot
```

Or from the `src/` directory:

```bash
cd src
python discord_bot/run_bot.py
```

Logs go to `discord-run.log` in the working directory and to stdout.

---

## 5. Run `/setup`

In any channel the bot can see (or in the server's default channel), run the slash command:

```
/setup
```

This is idempotent — safe to run multiple times. Each run:

1. **Creates roles** (if missing): `Admin`, `Organizer`, `Guest`, `Bot`
2. **Locks down visibility**: `@everyone` is denied *View Channel* at the guild level — a user with no role sees nothing
3. **Creates three categories and twelve channels**:

   **General** — visible to all members

   | Channel | Organizer | Guest |
   |---|---|---|
   | `#welcome` | read-write | read-only |
   | `#announcements` | read-write | read-only |
   | `#schedule` | read-write | read-only |
   | `#results` | read-only | read-only |
   | `#rules` | read-write | read-only |

   **Community** — fencer interaction

   | Channel | Organizer | Guest |
   |---|---|---|
   | `#general-chat` | read-write | read-write |
   | `#looking-for-sparring` | read-write | read-write |
   | `#questions` | read-write | read-write |

   **Organization** — organizers only

   | Channel | Organizer | Guest |
   |---|---|---|
   | `#setup` | read-write | invisible |
   | `#org-internal` | read-write | invisible |
   | `#org-results-upload` | read-write | invisible |
   | `#bot-commands` | read-write | invisible |

4. **Mints three permanent invite links** (no expiry, unlimited uses):
   - **Admin invite** — members joining via this link auto-receive the `Admin` role
   - **Organizer invite** — members joining via this link auto-receive the `Organizer` role
   - **Guest invite** — members joining via this link auto-receive the `Guest` role
5. **Generates QR PNG files** for each invite and posts them as the `/setup` reply

   Files are saved to: `data/run_bot/<guild_id>/qr_admin.png`, `qr_organizer.png`, `qr_guest.png`

   Print and post the QR codes at the venue — fencers scan the Guest QR to join, staff scan the Organizer QR.

---

## 6. Configure the tournament in `#setup`

After `/setup`, the bot seeds `#setup` with a welcome message. You have two ways to configure the tournament — both require the `Admin` role.

### Option A — slash command (recommended)

Run `/configure` in `#setup`. A modal form appears with three fields:

| Field | Example |
|---|---|
| Tournament name | `Prague Open 2026` |
| Language | `EN`, `CS`, `DE`, `FR`, `ES`, `IT`, `PL`, `SK`, `HU`, `RU` |
| Disciplines | `LS, SAW, SB` (comma-separated codes) |

After submitting, you are shown a confirmation step. **Confirming wipes all channel messages** before saving the configuration — treat it as a clean-slate operation.

Valid discipline codes: `LS`, `LSW`, `LSM`, `SA`, `SAW`, `SAM`, `RA`, `RAW`, `RAM`, `RD`, `RDW`, `RDM`, `SB`, `SBW`, `SBM`, `Plastic LS`, `Plastic LSW`, `Plastic SA`, `Plastic SAW`, `Plastic RA`, `Plastic SB`.

### Option B — AI dialogue

Go to `#setup` and write a free-text message describing what you want. The setup agent will ask for:

1. **Language** — e.g. "use Czech"
2. **Tournament name** — e.g. "NA Open 2025"
3. **Disciplines** — e.g. "longsword open, longsword women, sabre open"
4. **Participant counts** — expected number of fencers per discipline

The agent maps descriptions to internal codes, shows a confirmation table, and writes `user_config.json` once you confirm. You can return to `#setup` at any time to update settings — just describe what you want to change.

---

## 7. Pool management

Once pool assignments have been decided and result sheets filled in, use the following slash commands (all require `Admin` role, run in `#setup` or `#bot-commands`):

### Create data entry sheets

```
/create_pool_sheets
```

Creates one Google Sheet per discipline from the Drive template. Links are posted to `#setup`.

### Validate pool sheets

```
/validate_pools [disc]
```

Checks each discipline's pool sheet against the tournament roster. Leave `disc` empty to check all disciplines. Results are posted as threads in `#setup` (`<disc>_pools_validation`).

### Render pool tables

```
/render_pools [disc]
```

Renders pool tables as PDFs and posts them to `#setup` → `<disc>_pool_tables` thread. Leave `disc` empty to render all disciplines.

### Publish pool tables to fencers

```
/publish_pools <disc>
```

Publishes the rendered pool tables to `#announcements` → `<disc>_pools` thread, visible to Guests.

---

## 8. Share the invite links

After `/setup` you have two ways to share the links with attendees:

- **QR codes** — print `qr_guest.png` and `qr_organizer.png` and post them at the registration desk
- **Direct links** — the full URLs appear in the `/setup` reply (ephemeral, only you see them); copy them from there if needed

---

## 9. Restarting after code changes

Stop the bot process and restart it — no other steps needed. The invite links and `user_config.json` are persisted on disk and survive restarts. Re-running `/setup` after a restart is safe and will drift-correct any permissions that diverged.

---

## 10. Multiple tournaments

Each tournament needs its own Discord server, its own bot application, and its own `DISCORD_TOKEN`. Run one bot process per tournament (each gets its own lock file at `/tmp/hema-run-bot.lock`).

If running both on the same machine, use different lock file paths:

```bash
BOT_LOCK_FILE=/tmp/hema-run-na2025.lock DISCORD_TOKEN=... python -m discord_bot.run_bot
BOT_LOCK_FILE=/tmp/hema-run-eu2025.lock DISCORD_TOKEN=... python -m discord_bot.run_bot
```

---

## 11. Troubleshooting

**Slash commands not appearing after bot restart**

Commands are synced to each guild at startup. Wait a few seconds and try again. You can force a sync by running `!sync` (prefix command, owner only) in any channel.

**Bot appears online but `/setup` does nothing**

Check that **Server Members Intent** and **Message Content Intent** are both enabled in the Developer Portal (Bot → Privileged Gateway Intents). Changes there require a bot restart.

**`/setup` fails with "Missing permissions"**

Re-invite the bot using the OAuth URL from step 3 with the full permissions integer. Changing permissions on an existing invite requires a new invite URL.

**Auto-role not assigned to a new member**

The bot tracks invite uses via an in-memory snapshot that is refreshed on ready and on invite changes. If the bot was offline when the member joined, no role is assigned and the member receives a DM asking them to contact an organiser. Re-running `/setup` or assigning the role manually resolves this.

**`#setup` channel is missing**

Run `/setup` again — it is idempotent and will recreate any missing channels without touching existing ones.

**`data/run_bot/<guild_id>/` doesn't exist**

The directory is created automatically on the first `/setup` run. Check that the bot process has write access to the `data/` directory (or wherever `DATA_ROOT` points).

| Error | Fix |
|---|---|
| `DISCORD_TOKEN not set` | Export the token before starting the bot |
| `ANTHROPIC_API_KEY not set` | Export the key; required by the setup-agent LLM |
| Bot goes offline on restart | Old token still set somewhere — check your shell environment |
| `/clear` fails with 403 Forbidden | Re-invite with Manage Messages permission |
| QR PNG not generated | Install `qrcode[pil]`: `pip install "qrcode[pil]>=8.0"` |
