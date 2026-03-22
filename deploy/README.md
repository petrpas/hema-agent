# Deploying a tournament bot to fly.io

Each tournament runs as its own fly.io app with its own Discord bot token and persistent volume. This directory contains everything needed to spin one up.

---

## 1. Prerequisites

- **flyctl** installed and logged in (see step 2)
- A **Discord bot application** with a token (see step 3)
- Your **Google service-account JSON** (`src/creds.json` by default — shared across all tournaments)
- An **Anthropic API key**

---

## 2. One-time flyctl setup

```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Log in
flyctl auth login
```

---

## 3. Create a Discord bot

1. Go to https://discord.com/developers/applications and click **New Application**.
2. Give it a name (e.g. "HEMA Agent NA2025").
3. Go to **Bot** → click **Add Bot**.
4. Under **Privileged Gateway Intents**, enable **Message Content Intent**.
   - Záměr obsahu zprávy
5. Click **Reset Token** and copy the token — you'll paste it into your `.conf` file.
6. To invite the bot, construct the invite URL manually (the portal generator is unreliable):
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=68624&scope=bot+applications.commands
   ```
   Replace `YOUR_CLIENT_ID` with the **Application ID** from **General Information**. Open the URL in a browser to add the bot to your server.

---

## 4. Prepare your conf file

```bash
cp deploy/deployment.conf.template deploy/na2025.conf
```

Open `deploy/na2025.conf` and fill in:

| Variable | Where to find it |
|---|---|
| `APP_NAME` | Pick a unique name, e.g. `hema-agent-na2025`. Must be globally unique on fly.io. |
| `DISCORD_TOKEN` | Copied from Discord Developer Portal → Bot → Token |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |
| `FLY_REGION` | Optional. `iad` (Virginia), `ams` (Amsterdam), `lhr` (London). Defaults to `iad`. |
| `CREDS_PATH` | Path to your Google service-account JSON, relative to repo root. Defaults to `src/creds.json`. |
| `VOLUME_SIZE` | Persistent disk in GB. `1` is plenty. |

**Keep conf files out of git** — they contain secrets. Add them to `.gitignore`:

```
deploy/*.conf
```

---

## 5. Run deploy.sh

From the **repo root**:

```bash
./deploy/deploy.sh deploy/na2025.conf
```

Expected output:
```
==> Deploying app: hema-agent-na2025 (region: iad)
==> Creating app 'hema-agent-na2025'...
==> Creating volume 'hema_data' (1GB, region: iad)...
==> Setting secrets...
==> Deploying (remote build)...
...
==> Done! Bot 'hema-agent-na2025' is deployed.
    View logs: flyctl logs --app hema-agent-na2025
    Next step: invite the bot to your Discord server, then run /setup in a channel.
```

---

## 6. First boot

1. **Invite the bot** to your Discord server using the OAuth URL from step 3.
2. In any channel the bot can see, run the slash command: `/setup`
   - This creates the `#setup` and `#reg-agent` channels.
3. Go to `#setup` and follow the prompts to configure the tournament (name, disciplines, sheet URLs, etc.).
   - The bot will create `user_config.json` on its persistent volume at the end of setup.
4. Once setup is complete, use `#reg-agent` to run the registration pipeline.

---

## 7. Redeploying after code changes

Just re-run the same command. `deploy.sh` is idempotent — it skips app and volume creation if they already exist, and updates secrets and deploys the new image:

```bash
./deploy/deploy.sh deploy/na2025.conf
```

---

## 8. Multiple tournaments

One conf file per tournament:

```
deploy/na2025.conf       # APP_NAME=hema-agent-na2025
deploy/eu2025.conf       # APP_NAME=hema-agent-eu2025
```

Each gets its own fly.io app, its own Discord bot, its own volume. They all share the same Docker image and the same Google service account.

---

## 9. Troubleshooting

**View live logs:**
```bash
flyctl logs --app hema-agent-na2025
```

**Common errors:**

| Error | Fix |
|---|---|
| `App name is already taken` | Choose a different `APP_NAME` — fly.io app names are globally unique. |
| `Error: conf file not found` | Run from the repo root, not from inside `deploy/`. |
| `GOOGLE_CREDS_B64 decode fails at runtime` | Check `CREDS_PATH` points to valid JSON. |
| Bot appears online but doesn't respond | Check that **Message Content Intent** is enabled in the Discord Developer Portal. |
| `flyctl: command not found` | Re-run the install from step 2 and make sure `~/.fly/bin` is on your `PATH`. |
| Setup agent not creating channels | The bot needs **Manage Channels** permission — re-invite with the correct OAuth URL. |
