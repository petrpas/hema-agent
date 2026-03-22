#!/usr/bin/env bash
set -euo pipefail

# Usage: ./deploy/deploy.sh path/to/my.conf
if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <path/to/deployment.conf>" >&2
    echo "  Copy deploy/deployment.conf.template, fill in values, then pass it here." >&2
    exit 1
fi

CONF_FILE="$1"
if [[ ! -f "$CONF_FILE" ]]; then
    echo "Error: conf file not found: $CONF_FILE" >&2
    exit 1
fi

# Source conf file relative to repo root (where this script should be run from)
# shellcheck source=/dev/null
source "$CONF_FILE"

# Fall back to .env for ANTHROPIC_API_KEY if not set in conf
if [[ -z "${ANTHROPIC_API_KEY:-}" && -f ".env" ]]; then
    ANTHROPIC_API_KEY="$(grep -m1 '^ANTHROPIC_API_KEY=' .env | cut -d= -f2-)"
fi

# Validate required vars
for var in APP_NAME DISCORD_TOKEN ANTHROPIC_API_KEY; do
    if [[ -z "${!var:-}" ]]; then
        echo "Error: $var is not set in $CONF_FILE or .env" >&2
        exit 1
    fi
done

# Apply defaults for optional vars
FLY_REGION="${FLY_REGION:-iad}"
CREDS_PATH="${CREDS_PATH:-src/creds.json}"
VOLUME_SIZE="${VOLUME_SIZE:-1}"

# Validate creds file exists
if [[ ! -f "$CREDS_PATH" ]]; then
    echo "Error: Google credentials file not found: $CREDS_PATH" >&2
    echo "  Set CREDS_PATH in your conf file to the path of your service-account JSON." >&2
    exit 1
fi

echo "==> Deploying app: $APP_NAME (region: $FLY_REGION)"

# 1. Create app (skip if already exists)
if flyctl apps list --json | grep -q "\"$APP_NAME\""; then
    echo "==> App '$APP_NAME' already exists, skipping create"
else
    echo "==> Creating app '$APP_NAME'..."
    flyctl apps create "$APP_NAME"
fi

# 2. Create persistent volume (skip if already exists)
if flyctl volumes list --app "$APP_NAME" --json | grep -q '"hema_data"'; then
    echo "==> Volume 'hema_data' already exists on '$APP_NAME', skipping create"
else
    echo "==> Creating volume 'hema_data' (${VOLUME_SIZE}GB, region: $FLY_REGION)..."
    flyctl volumes create hema_data \
        --app "$APP_NAME" \
        --region "$FLY_REGION" \
        --size "$VOLUME_SIZE"
fi

# 3. Set secrets
echo "==> Setting secrets..."
GOOGLE_CREDS_B64="$(base64 -w 0 "$CREDS_PATH")"
flyctl secrets set \
    --app "$APP_NAME" \
    DISCORD_TOKEN="$DISCORD_TOKEN" \
    ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    GOOGLE_CREDS_B64="$GOOGLE_CREDS_B64"

# 4. Deploy
echo "==> Deploying (remote build)..."
flyctl deploy --app "$APP_NAME" --primary-region "$FLY_REGION" --remote-only

echo ""
echo "==> Done! Bot '$APP_NAME' is deployed."
echo "    View logs: flyctl logs --app $APP_NAME"
echo "    Next step: invite the bot to your Discord server, then run /setup in a channel."
