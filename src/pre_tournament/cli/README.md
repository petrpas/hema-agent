# Pre-tournament CLI

A local, no-Discord harness to run **each pre-tournament step in isolation**,
reusing the exact step functions the bot uses. The `data/{tournament}/`
directory is the inter-step contract (every step reads the previous step's
JSON) and the home for evaluation artifacts (`data/{tournament}/eval/`).

See [`plan.md`](plan.md) for the full design rationale.

## Usage

```bash
python -m pre_tournament.cli <command> [options]
# installed entry point:
hema-cli <command> [options]
```

### Global options (every command)

| Option | Meaning |
|---|---|
| `--config PATH` | user_config.json (else `$USER_CONFIG`, else package default) |
| `--tournament NAME` | override `tournament_name` (selects `data_dir`) |
| `--data-root DIR` | override `data_root_dir` (default `./data`) |
| `--format text\|json` | output mode (json → stdout is one clean JSON doc, logs → stderr) |
| `--force` | bypass this step's cache (deletes the specific cache artifact) |
| `--allow-remote` | permit Google/network side-effects |
| `-v`, `-vv` | log level |

### Exit codes

`0` ok · `1` step error · `2` bad args / missing artifact · `3` eval
assertion failed · `4` remote action blocked (no `--allow-remote`).

## Commands

**Registration pipeline** — `download` (remote), `parse`, `match`
(+ `match-correct`, `hr-search`), `dedup` (+ `dedup-likely`,
`dedup-confirm`), `init-sheet` (remote), `ratings`, `upload` (remote),
`seeds-recalc` (remote), `remove-fencers`, `sheet-create`/`sheet-set-url`
(remote), `run-all`.

**Pools** — `pool-solve`, `pool-validate`, `pool-write` (remote),
`pool-render`. Use `--from-state` to work offline from
`pool_alch_state.json`; otherwise `--num-pools` + `--waves a,b` and a sheet
read.

**Payments** — `pay-parse --file P`, `pay-match`, `pay-report`.

**Setup** — `setup-show`, `setup-set KEY VALUE`.

**Eval** — `eval-golden-save STEP [--tag T]`, `eval-golden-list`,
`eval-run STEP [--golden T] [--repeat N] [--assert] [--threshold m=v]`,
`eval-diff STEP --a RUN --b RUN`. Evaluable steps: `parse`, `match`,
`dedup`, `ratings`, `pay-match`, `pool-solve`. Metrics are pure JSON
comparisons (offline); `--repeat N` samples LLM variance (mean + stdev).

## Examples

```bash
# Offline: re-parse, match, dedup against a tournament's data dir
hema-cli parse  --config cfg.json
hema-cli match  --config cfg.json
hema-cli dedup  --config cfg.json

# Fix a wrong match and have it persist across reruns
hema-cli match-correct --name "Jane Doe" --hr-id 12345 --config cfg.json

# Deterministic pool solve from a saved sheet snapshot, then render
hema-cli pool-solve  --discipline SA --from-state --config cfg.json
hema-cli pool-render --discipline SA --config cfg.json

# Freeze a golden, then regression-check a step against it
hema-cli eval-golden-save parse --tag baseline --config cfg.json
hema-cli eval-run parse --golden baseline --repeat 3 --assert --config cfg.json

# Remote steps require --allow-remote
hema-cli upload --allow-remote --config cfg.json
```

## Notes

- The CLI never duplicates step logic. Shared pieces previously trapped in
  the Discord agent (`apply_correction`, `search_profiles`,
  `apply_confirmed_merges`, `build_fencer_summaries`) were extracted into the
  step modules so the bot and CLI run one implementation.
- `eval-run` is non-destructive: it snapshots the working artifact, runs the
  step, captures the output under `eval/runs/`, then restores the original.
- Goldens and runs live under `data/{tournament}/eval/` which is gitignored.
