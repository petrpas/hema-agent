# Pre-tournament CLI runbook — Na Duel! 2026
#
# This is NOT a script to run top-to-bottom. It sets up the environment,
# then lists every CLI command grouped by phase so you can pick one.
#
#   source src/run_nd26_cli.sh        # load env + the `cli` helper
#   cli setup-show                    # then paste any line from below
#
# Legend:  [offline] no network/LLM · [LLM $] spends Anthropic tokens
#          [remote]  touches Google/network — needs --allow-remote
#
# Global flags on every command:
#   --config PATH | --tournament NAME | --data-root DIR
#   --format text|json | --force | --allow-remote | -v / -vv
# Exit codes: 0 ok · 1 step error · 2 bad args/missing artifact ·
#             3 eval assertion failed · 4 remote blocked (no --allow-remote)

# ── environment setup ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"                                  # data/ and .env resolve here
export USER_CONFIG="$SCRIPT_DIR/nd26_cli_conf.json"
# ANTHROPIC_API_KEY is read automatically from $REPO_ROOT/.env (load_dotenv).
# Google creds for [remote] steps: src/creds.json (per agent_config.json).
cli() { .venv/bin/python -m pre_tournament.cli "$@"; }   # venv; no activate

echo "env ready · USER_CONFIG=$USER_CONFIG"
echo "tournament 'Na Duel! 2026' · data_dir=\"data/Na Duel! 2026\""
echo "verify with:  cli setup-show"

# Sourced: stop here, the rest is a copy-paste reference (all commented).
return 0 2>/dev/null || true

###############################################################################
# 0. CONFIG / SANITY
###############################################################################
# cli setup-show                                  # [offline] print resolved config
# cli setup-set language CS                        # [offline] set+persist a key
# cli setup-set output_sheet_url https://docs.google.com/spreadsheets/d/XXX/edit

###############################################################################
# 1. REGISTRATION PIPELINE  (reg_agent steps 1→7)
###############################################################################

# -- step 1: download registration sheet ----------------------------------- #
# cli download --allow-remote                      # [remote] uses registration_sheet_url from config
# cli download --allow-remote --worksheet "Form Responses 1"
# cli download --csv path/to/registrations.csv     # [offline] ingest a local CSV instead of Google

# -- step 2: parse (LLM Haiku) --------------------------------------------- #
# cli parse                                        # [LLM $] parse latest registrations_vN.csv
# cli parse --force                                # [LLM $] re-parse (bypass _csv_unchanged)
# cli parse --csv "data/Na Duel! 2026/registration_csv/registrations_v0.csv"
#   note: rejects (exit 2) a CSV that isn't a registration sheet before the LLM

# -- step 3: match to HEMA Ratings (LLM Sonnet) ---------------------------- #
# cli match                                        # [LLM $] (first run downloads+caches fighters list)
# cli match --instructions "treat 'Honza' as 'Jan'"
# cli hr-search --name "Jan Novak"                 # [offline] local fuzzy HR lookup, no LLM
# cli match-correct --name "Jan Novak" --hr-id 12345   # fix + persist a match
# cli match-correct --name "Jane Doe" --none           # mark: fencer has no HR profile

# -- step 4: dedup (LLM Sonnet, fingerprint-cached) ------------------------ #
# cli dedup                                        # [LLM $] merge duplicate registrations
# cli dedup --force                                # [LLM $] ignore the fingerprint short-circuit
# cli dedup-likely                                 # [offline] list pending likely-dup groups
# cli dedup-confirm --group 1 --group 3            # apply confirmed likely merges
# cli dedup-confirm --group 2 --hint "same person, different club spelling"
# cli dedup-confirm --approvals approvals.json     # {"1": null, "2": "hint"} (automode)

# -- step 4.5: init Fencers worksheet -------------------------------------- #
# cli sheet-create --allow-remote                  # [remote] only if you don't have a sheet yet
# cli sheet-set-url <URL> --allow-remote           # [remote] persist the URL — bot-created sheet works
# cli init-sheet --allow-remote                    # [remote] write/refresh the Fencers tab

# -- step 5: ratings (network first run/day; LLM only if parser breaks) ---- #
# cli ratings                                      # [remote] fetch HEMA ratings/ranks (cached per day)
# cli ratings --force                              # [remote] refetch today (keeps cached fighter HTML)
# cli ratings --force --force-html                 # [remote] also re-pull fighter HTML (hits hemaratings)

# -- step 6: upload + seeds + withdrawals ---------------------------------- #
# cli upload --allow-remote                        # [remote] sync enriched data to output sheet
# cli seeds-recalc --allow-remote                  # [remote] recompute Seed columns
# cli remove-fencers --name "Jan Novak"                          # dry-run (prints what would change)
# cli remove-fencers --name "Jan Novak" --confirm --allow-remote # [remote] actually withdraw

# -- whole pipeline at once ------------------------------------------------ #
# cli run-all --from parse --to ratings            # [LLM $] default offline-ish range
# cli run-all --from download --to upload --allow-remote   # full pipeline incl. remote
# cli run-all --from parse --to dedup --no-stop-on-error   # keep going past a failing step

###############################################################################
# 1.5 LISTS & DECLARATION  (Typst → PNG/PDF under data/<t>/lists/)
###############################################################################
# cli render-lists --allow-remote                  # [remote] Fencers + per-discipline PNGs from the output sheet
#   prereq: output_sheet_url set + the relevant tabs populated (init-sheet/upload)
# cli render-declaration --date "15.06.2026"       # [offline] participant declaration PDF from fencers_deduped.json

###############################################################################
# 2. POOLS  (pool_alch_agent; disciplines: SA=sabre, SB=sword & buckler)
###############################################################################
# First solve needs --num-pools and --waves (must sum to --num-pools);
# afterwards reuse the saved state with --from-state.
# cli pool-solve --discipline SA --num-pools 8 --waves 4,4
# cli pool-solve --discipline SA --num-pools 8 --waves 4,4 --parallel-waves 0
# cli pool-solve    --discipline SA --from-state    # re-solve from pool_alch_state.json
# cli pool-validate --discipline SA --from-state    # [offline] check the roster
# cli pool-render   --discipline SA --from-state    # [offline] Typst → PDF/PNG + roster CSV
# cli pool-write    --discipline SA --from-state --allow-remote   # [remote] write SA_Pools tab
#   (repeat the four lines above with --discipline SB)

###############################################################################
# 3. PAYMENTS  (payment_agent / step7)
###############################################################################
# cli pay-parse --file "data/Na Duel! 2026/payments/raw/bank_export.csv"   # [LLM $] parse one export
# cli pay-match                                    # [LLM $] match payments → fencers
# cli pay-match --hints "VS 2026007 = Jan Novak"
# cli pay-report                                   # [offline] print the payment report

###############################################################################
# 4. EVALUATION  (data/<t>/eval/ ; steps: parse match dedup ratings pay-match pool-solve)
###############################################################################
# cli eval-golden-save dedup --tag baseline        # freeze current artifact as golden
# cli eval-golden-list                             # list saved goldens
# cli eval-run dedup --golden baseline             # run + compare vs golden
# cli eval-run match --golden baseline --repeat 3  # [LLM $] sample LLM variance (mean/stdev)
# cli eval-run dedup --golden baseline --assert    # exit 3 if a metric breaches threshold
# cli eval-run parse --golden baseline --assert --threshold field_match_rate=0.95
# cli eval-diff dedup --a <RUN_TS_A> --b <RUN_TS_B>   # diff two eval runs (timestamps from eval-run)