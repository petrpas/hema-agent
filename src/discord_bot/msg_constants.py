from msgs import read_msg, render_msg  # noqa: F401 (render_msg re-exported for callers)

APP_NAME = "HEMA Squire"

SETUP_CHANEL_NAME = "hsq-setup"
REGISTRATION_CHANEL_NAME = "hsq-registrations"
TOURNAMENT_INPUT_CHANNEL = "hsq-results-upload"
POOLS_CHANNEL_NAME = "hsq-pools-alchemy"

SETUP_WELCOME = read_msg("setup/welcome")
POOLS_WELCOME = {lang: read_msg("pool_alch/welcome", lang) for lang in ("EN", "CS")}
SETUP_INFO    = {lang: read_msg("setup/info", lang) for lang in ("EN", "CS")}
SETUP_COMPLETE = {lang: read_msg("setup/complete", lang) for lang in ("EN", "CS")}
PAYMENTS_THREAD_INTRO = {lang: read_msg("reg/payments_thread_intro", lang) for lang in ("EN", "CS")}
REGISTRATION_WELCOME  = {lang: read_msg("reg/welcome", lang) for lang in ("EN", "CS")}
SHEET_ACCESS_REQUEST  = {lang: read_msg("shared/sheet_access_request", lang) for lang in ("EN", "CS")}
SHEET_CLONE_REQUEST   = {lang: read_msg("shared/sheet_clone_request", lang) for lang in ("EN", "CS")}
