from msgs import read_msg, render_msg  # noqa: F401 (render_msg re-exported for callers)

APP_NAME = "HEMA Squire"

SETUP_CHANEL_NAME = "hsq-setup"
REGISTRATION_CHANEL_NAME = "hsq-registrations"
TOURNAMENT_INPUT_CHANNEL = "hsq-results-upload"

SETUP_WELCOME = read_msg("setup_welcome")
SETUP_INFO    = {lang: read_msg("setup_info", lang) for lang in ("EN", "CS")}
SETUP_COMPLETE = {lang: read_msg("setup_complete", lang) for lang in ("EN", "CS")}
PAYMENTS_THREAD_INTRO = {lang: read_msg("payments_thread_intro", lang) for lang in ("EN", "CS")}
REGISTRATION_WELCOME  = {lang: read_msg("registration_welcome", lang) for lang in ("EN", "CS")}
SHEET_ACCESS_REQUEST  = {lang: read_msg("sheet_access_request", lang) for lang in ("EN", "CS")}
SHEET_CLONE_REQUEST   = {lang: read_msg("sheet_clone_request", lang) for lang in ("EN", "CS")}
