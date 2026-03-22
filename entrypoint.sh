#!/bin/bash
set -euo pipefail

# Decode Google service-account key
if [ -n "${GOOGLE_CREDS_B64:-}" ]; then
    echo "$GOOGLE_CREDS_B64" | base64 -d > /app/src/creds.json
fi

# Seed user_config.json onto the volume on first boot (if not yet present).
# After first boot the volume copy is authoritative — setup_agent can mutate it at runtime.
if [ -n "${USER_CONFIG_B64:-}" ] && [ ! -f "/app/data/user_config.json" ]; then
    echo "$USER_CONFIG_B64" | base64 -d > /app/data/user_config.json
fi

exec python src/discord_bot/bot.py
