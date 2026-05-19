# Pre-tournament CLI — Implementation Plan

A local, no-Discord command-line harness to run each pre-tournament agent step
in isolation, reusing the existing step functions and the `data/{tournament}/`
directory as the inter-step contract and as the place where evaluation
artifacts land.

Status: **plan only** — no code written yet.

---

## 1. Goal & scope

- Run any single step of the pre-tournament pipeline locally, from a shell,
  without Discord, without the bot, without the LLM orchestrator in
  `reg_agent.py`.
- Reuse the existing step functions verbatim — the CLI is a thin argument
  parser + artifact loader + reporter. **No business logic in the CLI.**
- Use `data/{tournament_name}/` both as the hand-off between steps (every step
  already reads/writes JSON there) and as the home for evaluation runs
  (`data/{tournament_name}/eval/...`).
- Be runnable autonomously (automode): every command has a non-interactive
  form, deterministic exit codes, and machine-readable output.
- Decompose **all** pre-tournament agents (reg, setup, pool-alch, payment),
  with `reg_agent`'s 7-step pipeline as the primary target.

### Non-goals

- Not replacing the Discord bot or the conversational agents.
- Not re-implementing any step logic.
- Not building a test framework — `eval` mode produces reports; assertions are
  a thin layer on top.

---

## 2. Findings — current state of the codebase

### 2.1 The pipeline is already decomposed; the seams are the JSON artifacts

Every reg_agent step is a plain function `(data, config) -> data` that also
**persists its output to a fixed filename in `config.data_dir`** via
`utils.load_fencers_list` / `save_fencers_list` / `save_ratings`. The next step
reloads that file. The Discord layer (`reg_agent.py` tools) only does:
load-artifact → `asyncio.to_thread(step_fn, ...)` → format-for-Discord.

This means the inter-step contract is **already the filesystem**, not return
values. The CLI can run any step standalone by pointing at a `data_dir` that
contains the prior step's artifact. This is the central design lever.

Artifact map (all under `data/{tournament_name}/`):

| Step | Reads | Writes |
|---|---|---|
| 1 download | (Google Sheet) | `registration_csv/registrations_vN.csv` |
| 2 parse | latest `registrations_vN.csv` | `fencers_parsed.json` |
| 3 match | `fencers_parsed.json` | `fencers_matched.json`, `fencers_cache.json`, `match_corrections.json`, `hemaratings_fighters.{html,csv}` |
| 4 dedup | `fencers_matched.json` | `fencers_deduped.json` + `.fingerprint`, `fencers_likely_groups_pending.json` |
| 4.5 init sheet | `fencers_deduped.json` | (Google Sheet "Fencers" tab) |
| 5 ratings | `fencers_deduped.json` | `ratings_YYYY_MM_DD.json`, `rating_html_YYYY_MM_DD/fighter_*.html` |
| 6 upload | `fencers_deduped.json` + latest `ratings_*.json` | (Google Sheet discipline tabs) |
| 7 payments | `payments/raw/*`, fencer list | `payments/parsed/*.json`, `payments/matched.json` |

### 2.2 `reg_agent/main.py` is stale and cannot be the basis

`main.py:64` calls `download_registrations(config)` but the current signature
is `download_registrations(config, sheet_url, worksheet_index=0,
worksheet_name=None)` (`step1_download.py:47`). The linear orchestrator is
out of date and broken. The new CLI **supersedes** `main.py`; we should plan to
delete `main.py` once the CLI's `run-all` subcommand reaches parity.

### 2.3 Caching / short-circuits that the CLI must be able to bypass

- step2: `_csv_unchanged()` skips the LLM and reloads `fencers_parsed.json` if
  the latest CSV is byte-identical to the previous version.
- step4: fingerprint file — skips the LLM merge if input list is unchanged.
- step5: ratings cached per **calendar day** (`ratings_YYYY_MM_DD.json`); also
  per-fighter HTML cached under `rating_html_YYYY_MM_DD/`.
- step3: `hemaratings_fighters.csv` cached once, never re-downloaded.

Each needs a `--force` to delete/ignore the relevant cache so a step can be
re-evaluated. (Implementation: CLI deletes the specific artifact before calling
the step — never adds a flag to the step functions themselves.)

### 2.4 External dependencies per step

| Step | LLM | Network | Google API | Offline re-runnable? |
|---|---|---|---|---|
| 1 download | no | yes | yes (Sheets read) | no |
| 2 parse | Haiku | no | no | yes (needs CSV) |
| 3 match | Sonnet | yes (first run only — HTML cached) | no | yes after first fetch |
| 4 dedup | Sonnet | no | no | yes |
| 4.5 init | no | yes | yes (Sheets write) | no |
| 5 ratings | Sonnet (only on parser break) | yes (first run/day) | no | yes after HTML cached |
| 6 upload | Sonnet | yes | yes (Sheets write) | no |
| 7 payments | Haiku+Sonnet | no | no | yes |

Steps 2, 3, 4, 5, 7 are fully runnable offline from cached artifacts (given an
ANTHROPIC key for the LLM ones; step 5 needs no LLM unless the parser breaks).
Steps 1, 4.5, 6 touch Google and cannot be evaluated without credentials and a
live sheet — the CLI should treat them as "side-effecting" and gate them behind
an explicit `--allow-remote` flag.

### 2.5 Discord-coupled logic that needs a CLI equivalent

Three pieces of behaviour live only in `reg_agent.py` tools, not in step files:

1. **Match corrections** — `tool_correct_match` patches
   `fencers_matched.json` + `fencers_deduped.json` + cache + `match_corrections.json`.
   Logic is mostly self-contained; CLI needs a `match-correct` command.
2. **Likely-duplicate confirmation** — `tool_find_likely_duplicates` /
   `tool_merge_confirmed_duplicates` use Discord ✅ reactions as the approval
   signal. CLI replacement: print likely groups, accept approvals via a flag or
   a small JSON/stdin answer file, then call `merge_group` directly.
3. **`tool_set_output_sheet` / `setup_output_sheet`** — creating and wiring the
   output sheet. CLI: a `sheet create` / `sheet set-url` command.

### 2.6 The other three pre-tournament agents

- `setup_agent` — conversational wizard that mutates the user config JSON.
  Its testable core is config read/write; the conversation is the agent.
- `pool_alch_agent` — has a deterministic `solver.py` (`construct`, `score`,
  hill-climb), `loader.py` (Sheets read), `validator.py`, `writer.py`,
  `renderer.py` (Typst), `state.py` (JSON persistence). Already has
  `manual_test/test_default.py` driving the solver with hardcoded fencers.
- `payment_agent` — conversational; testable core is `step7_payments.py`
  (`parse_transactions`, `match_payments`, `format_payments_report`).

Plan covers all, but reg_agent steps are phase 1.

---

## 3. Design principles

1. **Filesystem is the contract.** Every command's default behaviour: load the
   upstream artifact from `data_dir`, run the step, let the step persist its
   own output, then print a summary + write an eval record. Commands compose by
   sharing `data_dir`, exactly as the bot already does.
2. **Thin wrappers only.** A command = parse args → build `PreConfig` → resolve
   input artifact → optionally clear a cache for `--force` → call the existing
   function → render summary / eval. If a command needs logic not in a step
   file, that logic gets *extracted into the step file* (so the bot benefits
   too), not duplicated in the CLI.
3. **Two output modes.** Human (`--format text`, default) and machine
   (`--format json`, for automode). JSON mode prints one JSON object to stdout
   and nothing else; logs go to stderr.
4. **Deterministic exit codes.** `0` success, `1` step error, `2` bad
   args/missing artifact, `3` eval assertion failed, `4` remote action blocked
   (no `--allow-remote`).
5. **Remote side-effects are opt-in.** Steps 1, 4.5, 6 (Google writes) and any
   network refetch require `--allow-remote`; otherwise they error with exit 4
   and a message telling the user what would have happened.
6. **Evaluation is a separate concern layered on top.** Running a step and
   evaluating it are different subcommands; eval never changes step behaviour.

---

## 4. CLI architecture

### 4.1 Directory layout (new)

```
src/pre_tournament/cli/
  plan.md            — this file
  __init__.py
  __main__.py        — `python -m pre_tournament.cli ...` entry point
  app.py             — argparse/subcommand wiring, shared options, exit codes
  context.py         — build PreConfig, resolve data_dir, creds, .env, logging
  artifacts.py       — locate/validate/clear the per-step JSON & CSV artifacts
  report.py          — text + json renderers; eval record writer
  steps/
    __init__.py
    download.py       — step 1 wrapper
    parse.py          — step 2 wrapper
    match.py          — step 3 wrapper (+ match-correct, hr-search)
    dedup.py          — step 4 wrapper (+ likely/confirm)
    init_sheet.py     — step 4.5 wrapper
    ratings.py        — step 5 wrapper
    upload.py         — step 6 wrapper (+ seeds, remove-fencers)
    payments.py       — step 7 wrappers
    run_all.py        — sequential 1→6 (replaces reg_agent/main.py)
  agents/
    pool_alch.py      — solver/validator/writer/render subcommands
    payment.py        — parse/match/report subcommands
    setup.py          — config show/set subcommands
  eval/
    __init__.py
    runner.py         — run step → capture output → compare vs golden
    metrics.py        — per-step comparison metrics (see §6)
    golden.py         — manage data/{t}/eval/golden/ snapshots
```

`cli/` reuses the same `sys.path` shim the step files use (insert `src/` and
`src/pre_tournament/reg_agent/` so bare `step*`, `models`, `utils` imports
resolve). Centralise that in `context.py`.

### 4.2 Command surface

```
python -m pre_tournament.cli <command> [options]

Global options (all commands):
  --config PATH         user_config.json (else $USER_CONFIG, else pre_config default)
  --tournament NAME     override tournament_name (selects data_dir)
  --data-root DIR       override data_root_dir (default: ./data)
  --format text|json    output mode (default text)
  --force               bypass this step's cache (delete the cache artifact first)
  --allow-remote        permit Google/network side-effects
  -v/-vv                log level

Pipeline commands (reg_agent):
  download   [--sheet-url U] [--worksheet NAME|--worksheet-index N]
  parse      [--csv PATH]                  # default: latest registrations_vN.csv
  match      [--instructions TEXT]
  match-correct  --name N (--hr-id ID | --none)
  hr-search  --name N                      # local fuzzy lookup, no LLM
  dedup
  dedup-likely [--list]                    # print pending likely groups
  dedup-confirm  --group N [--group M ...] [--hint TEXT]   # apply confirmed merges
  init-sheet                               # requires --allow-remote
  ratings
  upload                                   # requires --allow-remote
  seeds-recalc                             # requires --allow-remote
  remove-fencers --name N [--name M ...] [--confirm]
  sheet create|set-url URL                 # requires --allow-remote
  run-all    [--from STEP] [--to STEP] [--stop-on-error]

Other agents:
  pool solve --discipline SA [--from-state] [--num-pools P --waves a,b]
  pool validate --discipline SA
  pool write   --discipline SA             # requires --allow-remote
  pool render  --discipline SA             # Typst → PNG
  pay parse  --file PATH
  pay match  [--hints TEXT]
  pay report
  setup show
  setup set KEY VALUE

Evaluation (any pipeline step or pool/pay core):
  eval run   STEP [--golden TAG] [--repeat N] [--assert]
  eval golden save STEP [--tag TAG]
  eval golden list
  eval diff  STEP --a RUN --b RUN
```

### 4.3 Config & creds resolution (`context.py`)

- `load_pre_config(user_config_path)` already merges user JSON +
  `agent_config.json`. CLI resolves `user_config_path` from `--config` →
  `$USER_CONFIG` → the package default. `--tournament` / `--data-root`
  overlay onto the loaded `PreUserConfig` before constructing `PreConfig`
  (build a modified dict and re-validate; do not mutate the frozen
  `computed_field`).
- `creds_path` comes from `agent_config.json` (`src/creds.json`). CLI checks it
  exists; for offline steps a missing creds file is fine and only matters when
  `--allow-remote` is used.
- Load `.env` (same as `main.py:47`) for `ANTHROPIC_API_KEY` / tracing.
- Logging: reuse the `_DeltaFormatter` from `reg_agent/main.py` (extract it to
  `cli/report.py` so both can share; then `main.py` can be deleted).
- In `--format json`, attach the stream handler to **stderr** so stdout stays
  a single clean JSON document.

---

## 5. Per-step wrapper specifications

Each wrapper follows the same skeleton:

```
resolve config (context)
resolve & validate input artifact (artifacts) -> error exit 2 if missing
if --force: clear this step's cache artifact
if step is remote and not --allow-remote: exit 4 with explanation
call existing step function (in a thread is unnecessary off-Discord — call directly)
collect a StepResult: counts, output artifact path, timing, warnings
render summary (report) ; if eval requested, hand StepResult to eval.runner
exit 0
```

### Step 1 — `download`
- Fn: `step1_download.download_registrations(config, sheet_url, worksheet_index, worksheet_name)`.
- `--sheet-url` default = `config.registration_sheet_url`.
- Remote (Sheets read) → needs `--allow-remote`.
- Alternative offline path: `--csv PATH` copies a local CSV in via
  `save_registration_csv(config, data)` — useful to seed the pipeline for eval
  without Google.
- Output: prints new `registrations_vN.csv` name + row count.

### Step 2 — `parse`
- Fn: `step2_parse.parse_registrations(csv_path, config)`.
- Input: latest `registrations_vN.csv` (or `--csv`).
- LLM (Haiku). `--force` deletes `fencers_parsed.json` AND must defeat
  `_csv_unchanged` — simplest: copy the chosen CSV to a fresh version number so
  it's treated as new, OR (cleaner) add a private `force` path. **Decision:**
  CLI deletes `fencers_parsed.json`; `_csv_unchanged` only short-circuits when
  the parsed file exists, so deleting it forces a re-parse without touching
  step code. Document this coupling.
- Output: fencer count, discipline histogram, #without hr_id.

### Step 3 — `match`
- Fn: `step3_match.match_fencers(fencers, config, instructions)`.
- Input: `fencers_parsed.json`.
- First run downloads + caches `hemaratings_fighters.html`; subsequent runs
  offline. `--force` here means re-run LLM matching: delete
  `fencers_matched.json` (the bot has no skip cache for step 3, so just calling
  it re-runs; `--force` mainly clears stale output before eval).
- Sub-commands reuse existing helpers:
  - `match-correct` → port `tool_correct_match` body into
    `step3_match.apply_correction(data_dir, config, name, hr_id)` (extract from
    `reg_agent.py:602-715` so bot + CLI share it), CLI just calls it.
  - `hr-search` → `tool_search_hr_profile` logic; extract to
    `step3_match.search_profiles(data_dir, name)`.
- Output: matched/found/unmatched/rejected category counts + unmatched names.

### Step 4 — `dedup`
- Fn: `step4_dedup.deduplicate_fencers(fencers, config)` → `(list, report,
  likely_groups)`.
- Input: `fencers_matched.json`. `--force` deletes
  `fencers_deduped.fingerprint` (defeats the fingerprint short-circuit).
- `dedup-likely --list`: read `fencers_likely_groups_pending.json`, print each
  group as a table (reuse `_dedup_likely_table_text`).
- `dedup-confirm --group N`: replicate `tool_merge_confirmed_duplicates`
  without Discord — read the pending file, take approved indices from
  `--group`, call `merge_group(group, config, hint)`, rewrite
  `fencers_deduped.json`, delete the pending file. Extract the merge-apply loop
  from `reg_agent.py:880-909` into `step4_dedup.apply_confirmed_merges(...)`.
- Output: before→after counts, merged groups, #likely pending.

### Step 4.5 — `init-sheet`  (remote)
- Fn: `step4_5_init.init_fencers_sheet(fencers, config)`.
- If no `output_sheet_url`: `sheet create` → `step6_upload.setup_output_sheet`,
  print URL, then user runs `sheet set-url`.
- Requires `--allow-remote`; without it, exit 4 + message
  "would write Fencers tab for N fencers to <url|NONE>".

### Step 5 — `ratings`
- Fn: `step5_ratings.fetch_ratings(fencers, config)`.
- Input: `fencers_deduped.json`. Cached per day; `--force` deletes today's
  `ratings_YYYY_MM_DD.json` (keep cached HTML so we don't hammer hemaratings).
  Add `--force-html` to also clear `rating_html_YYYY_MM_DD/` (use sparingly).
- Network only for uncached fighter HTML. LLM only if the regex parser raises.
- Output: rated/total, list of 404 hr_ids (with names) → these usually mean a
  wrong hr_id; suggest `match-correct`.

### Step 6 — `upload`  (remote)
- Fn: `step6_upload.upload_results(fencers, ratings, config)` (loads latest
  `ratings_*.json` like the bot does).
- Plus `seeds-recalc` (`recalculate_seeds` per discipline) and
  `remove-fencers` (`remove_fencers_from_sheets` + `save_withdrawn`; mirror
  `tool_remove_fencers` two-phase confirm with `--confirm`).
- Requires `--allow-remote`.

### Step 7 — `pay parse|match|report`
- `pay parse --file P`: `step7_payments.parse_and_store(raw, P.name,
  data_dir, config)` → writes `payments/parsed/*.json`.
- `pay match`: `load_all_parsed(data_dir)` + build fencer summaries from
  `fencers_deduped.json` + `match_payments(...)` → write `payments/matched.json`.
- `pay report`: load `payments/matched.json`, `format_payments_report(...)`,
  print.
- Fencer-summary construction currently lives in the payment_agent; extract a
  `step7_payments.build_fencer_summaries(fencers)` so CLI and agent share it.

### `run-all`
- Sequential 2→6 (1 and 4.5/6 gated by `--allow-remote`; default `--from
  parse --to ratings` for a safe offline run). Reuses each wrapper's
  `StepResult`. `--stop-on-error` (default on). This replaces `main.py`.

---

## 6. Evaluation framework (`cli/eval/`)

The user's emphasis: the data directory is **mainly for evaluation results**.
So eval is first-class.

### 6.1 Layout

```
data/{tournament_name}/eval/
  golden/<step>/<tag>/        # frozen expected artifacts + metadata.json
  runs/<step>/<timestamp>/    # actual artifact copy + metrics.json + stdout.txt
  latest.json                 # pointer to most recent run per step
```

### 6.2 `eval run STEP`

1. Snapshot the current output artifact path (so we can restore it).
2. Run the step wrapper (optionally `--repeat N` for LLM-stability sampling —
   each repeat after the first uses `--force`).
3. Copy the produced artifact into `runs/<step>/<ts>/`.
4. If `--golden TAG` (or a default golden exists): compute step-specific
   metrics vs golden, write `metrics.json`.
5. `--assert`: exit 3 if any metric breaches its threshold (thresholds in
   `eval/metrics.py`, overridable via `--threshold name=val`).

### 6.3 Per-step metrics (`metrics.py`)

These compare the JSON artifacts, keyed by a stable identity (hr_id when
present else normalized name):

| Step | Golden | Metrics |
|---|---|---|
| parse | `fencers_parsed.json` | record count delta; per-field exact-match rate (name, club, nationality, disciplines, hr_id); list of mismatched records |
| match | `fencers_matched.json` | hr_id accuracy vs golden; precision/recall on "has match"; count of changed assignments; rejected-id list |
| dedup | `fencers_deduped.json` | final count delta; set diff of surviving identities; #groups merged vs expected |
| ratings | `ratings_*.json` | coverage (rated/total); per-fighter rating/rank exact match; #404 |
| payments | `payments/matched.json` | matched/possible/unmatched counts vs golden; per-line fencer-set agreement |
| pool (solver) | score components | snake/club/nationality/wave penalties vs golden score; assignment stability |

Metrics are pure functions over two JSON files → no Google/LLM needed to
evaluate, so eval runs fully offline against cached artifacts. LLM
non-determinism is handled by `--repeat N` reporting mean/variance per metric.

### 6.4 `eval golden save STEP --tag TAG`

Copy the current artifact into `golden/<step>/<tag>/` + write `metadata.json`
(source CSV version, model id from `config.model(step)`, date, git SHA). Goldens
are per-tournament and live in `data/` (gitignored) — exactly "data dir for
evaluation results".

---

## 7. Refactors required (small, shared with the bot)

To keep the CLI a thin wrapper, extract these from `reg_agent.py` into the step
modules so **both** the bot and CLI call one implementation:

1. `step3_match.apply_correction(data_dir, config, name, hr_id|None) -> str`
   ← body of `tool_correct_match` (`reg_agent.py:602-715`).
2. `step3_match.search_profiles(data_dir, name) -> list[tuple]`
   ← body of `tool_search_hr_profile` (`reg_agent.py:547-599`).
3. `step4_dedup.apply_confirmed_merges(data_dir, config, approvals) -> str`
   ← merge-apply loop of `tool_merge_confirmed_duplicates`
   (`reg_agent.py:880-909`), Discord-reaction reading stripped out and replaced
   by an explicit `approvals: dict[group_idx, hint|None]` argument.
4. `step7_payments.build_fencer_summaries(fencers) -> list[dict]`
   ← currently assembled in `payment_agent.py`.
5. Move `_DeltaFormatter` → `cli/report.py`; delete `reg_agent/main.py` after
   `run-all` reaches parity.

Each refactor is behaviour-preserving: the bot tool becomes
`return step_module.fn(...)` plus its Discord formatting. Do these as the first
implementation step so nothing is duplicated.

---

## 8. Implementation phases

1. **Scaffolding** — `cli/` package, `app.py` argparse, `context.py`
   (config/creds/.env/logging/path shim), `report.py` (text+json+exit codes),
   `artifacts.py` (artifact locate/validate/clear). Smoke: `cli parse` end to
   end on an existing `data/na_duel_2026`.
2. **Offline steps** — `parse`, `match` (+ `hr-search`, `match-correct`),
   `dedup` (+ `dedup-likely`, `dedup-confirm`), `ratings`, `pay *`. Do the §7
   extractions here.
3. **Remote steps** — `download`, `init-sheet`, `upload`, `seeds-recalc`,
   `remove-fencers`, `sheet create/set-url`, all behind `--allow-remote`.
   `run-all`. Delete `main.py`.
4. **Other agents' cores** — `pool solve/validate/write/render`,
   `setup show/set`.
5. **Eval framework** — `eval run/golden/diff`, `metrics.py`, `--repeat`,
   `--assert`. Backfill goldens for `na_duel_2026` from current artifacts.
6. **Docs** — short `cli/README.md`; update root `README.md`; add a
   `[project.scripts]` `hema-cli = "pre_tournament.cli.__main__:main"` entry.

Phases 1–2 deliver the core ask (every offline step individually runnable +
testable). 3–5 complete coverage and the eval emphasis.

---

## 9. Open decisions (resolve before/while coding)

1. **argparse vs Typer/Click.** `pyproject.toml` has no CLI lib; argparse keeps
   deps unchanged. **Lean argparse** unless we want Typer's ergonomics enough to
   add a dependency.
2. **`--force` for step 2.** Confirmed approach: CLI deletes
   `fencers_parsed.json` to defeat `_csv_unchanged` (no step-code change).
   Document the coupling in `parse.py`.
3. **Likely-merge approvals UX.** `--group N` flags (repeatable) vs a
   `--approvals FILE` JSON vs interactive stdin prompt. Default: `--group`
   flags + optional `--hint`; add `--approvals` JSON for automode.
4. **Repeat sampling for LLM eval.** Re-run = `--force` + re-call. Confirm
   acceptable API spend before defaulting `--repeat` > 1.
5. **Golden location.** Under `data/{t}/eval/` (gitignored, per the user's
   "data dir for evaluation"). Confirm goldens should NOT be committed.
6. **Does `run-all` need step 1?** Default offline run starts at `parse` from
   an existing CSV; step 1 only with `--allow-remote --from download`.

---

## 10. Quick reference — exact functions the CLI will call

```
step1_download.download_registrations(config, sheet_url, worksheet_index=0, worksheet_name=None) -> Path
step1_download.save_registration_csv(config, data: bytes) -> Path
step2_parse.parse_registrations(csv_path: Path, config) -> list[FencerRecord]
step3_match.match_fencers(fencers, config, instructions=None) -> list[FencerRecord]
step3_match.load_corrections / save_corrections / _get_fighters_compact / _build_hr_index
step4_dedup.deduplicate_fencers(fencers, config) -> (list, report, likely_groups)
step4_dedup.merge_group(records, config, hint=None) -> FencerMergeResult
step4_5_init.init_fencers_sheet(fencers, config) -> None              # remote
step5_ratings.fetch_ratings(fencers, config) -> (ratings, not_found)
step6_upload.upload_results(fencers, ratings, config) -> None         # remote
step6_upload.setup_output_sheet(config) -> str                        # remote
step6_upload.create_discipline_worksheets / recalculate_seeds / remove_fencers_from_sheets
step7_payments.parse_and_store / load_all_parsed / match_payments / format_payments_report
utils.load_fencers_list / save_fencers_list / load_ratings / load_withdrawn / save_withdrawn
config.load_pre_config(user_config_path) -> PreConfig   # .data_dir, .model(Step), .disciplines
pool_alch_agent.solver.construct / score / (hill-climb); loader.load_discipline;
  validator.*; writer.*; renderer.*; state.load_state/save_state
```

Artifact filename constants are in `reg_agent/utils.py`
(`FENCERS_PARSED_FILE`, `FENCERS_MATCHED_FILE`, `FENCERS_DEDUPED_FILE`,
`FENCERS_DEDUPED_FP_FILE`, `FENCERS_CACHE_FILE`, `REG_VER_*`) and
`step7_payments.py` (`PAYMENTS_*`). The CLI's `artifacts.py` must import these,
never hardcode names.
```
