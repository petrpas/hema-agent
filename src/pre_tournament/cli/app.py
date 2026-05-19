"""argparse wiring, command dispatch, and exit-code mapping.

Handlers are `(args, config) -> StepResult`, imported lazily so a single
command does not pay the import cost of every step module.
"""

import argparse
import importlib
import sys

from pre_tournament.cli.context import build_config
from pre_tournament.cli.errors import CliError
from pre_tournament.cli.report import StepResult, emit, setup_logging

# command → "module:function"
_DISPATCH: dict[str, str] = {
    "download": "pre_tournament.cli.steps.download:cmd_download",
    "parse": "pre_tournament.cli.steps.parse:cmd_parse",
    "match": "pre_tournament.cli.steps.match:cmd_match",
    "match-correct": "pre_tournament.cli.steps.match:cmd_match_correct",
    "hr-search": "pre_tournament.cli.steps.match:cmd_hr_search",
    "dedup": "pre_tournament.cli.steps.dedup:cmd_dedup",
    "dedup-likely": "pre_tournament.cli.steps.dedup:cmd_dedup_likely",
    "dedup-confirm": "pre_tournament.cli.steps.dedup:cmd_dedup_confirm",
    "init-sheet": "pre_tournament.cli.steps.init_sheet:cmd_init_sheet",
    "ratings": "pre_tournament.cli.steps.ratings:cmd_ratings",
    "upload": "pre_tournament.cli.steps.upload:cmd_upload",
    "seeds-recalc": "pre_tournament.cli.steps.upload:cmd_seeds_recalc",
    "remove-fencers": "pre_tournament.cli.steps.upload:cmd_remove_fencers",
    "sheet-create": "pre_tournament.cli.steps.sheet:cmd_sheet_create",
    "sheet-set-url": "pre_tournament.cli.steps.sheet:cmd_sheet_set_url",
    "run-all": "pre_tournament.cli.steps.run_all:cmd_run_all",
    "render-lists": "pre_tournament.cli.steps.render:cmd_render_lists",
    "render-declaration": "pre_tournament.cli.steps.render:cmd_render_declaration",
    "pool-solve": "pre_tournament.cli.agents.pool_alch:cmd_pool_solve",
    "pool-validate": "pre_tournament.cli.agents.pool_alch:cmd_pool_validate",
    "pool-write": "pre_tournament.cli.agents.pool_alch:cmd_pool_write",
    "pool-render": "pre_tournament.cli.agents.pool_alch:cmd_pool_render",
    "pay-parse": "pre_tournament.cli.agents.payment:cmd_pay_parse",
    "pay-match": "pre_tournament.cli.agents.payment:cmd_pay_match",
    "pay-report": "pre_tournament.cli.agents.payment:cmd_pay_report",
    "setup-show": "pre_tournament.cli.agents.setup:cmd_setup_show",
    "setup-set": "pre_tournament.cli.agents.setup:cmd_setup_set",
    "eval-run": "pre_tournament.cli.eval.runner:cmd_eval_run",
    "eval-diff": "pre_tournament.cli.eval.runner:cmd_eval_diff",
    "eval-golden-save": "pre_tournament.cli.eval.golden:cmd_eval_golden_save",
    "eval-golden-list": "pre_tournament.cli.eval.golden:cmd_eval_golden_list",
}


def _add_global(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", help="path to user_config.json (else $USER_CONFIG)")
    p.add_argument("--tournament", help="override tournament_name (selects data_dir)")
    p.add_argument("--data-root", help="override data_root_dir (default: ./data)")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--force", action="store_true", help="bypass this step's cache")
    p.add_argument("--allow-remote", action="store_true",
                   help="permit Google/network side-effects")
    p.add_argument("-v", "--verbose", action="count", default=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hema-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    def cmd(name: str, **kw) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kw)
        _add_global(sp)
        return sp

    p = cmd("download", help="step 1: download registration sheet (remote)")
    p.add_argument("--sheet-url")
    p.add_argument("--worksheet")
    p.add_argument("--worksheet-index", type=int, default=0)
    p.add_argument("--csv", help="ingest a local CSV instead of Google (offline)")

    p = cmd("parse", help="step 2: LLM-parse the latest registration CSV")
    p.add_argument("--csv", help="parse a specific CSV instead of the latest")

    p = cmd("match", help="step 3: fuzzy-match fencers to HEMA Ratings")
    p.add_argument("--instructions")

    p = cmd("match-correct", help="fix a step-3 match and persist it")
    p.add_argument("--name", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--hr-id", type=int)
    g.add_argument("--none", action="store_true", help="fencer has no HR profile")

    p = cmd("hr-search", help="local fuzzy lookup of HR profiles (no LLM)")
    p.add_argument("--name", required=True)

    cmd("dedup", help="step 4: merge duplicate registrations")

    p = cmd("dedup-likely", help="list pending likely no-hr_id duplicate groups")

    p = cmd("dedup-confirm", help="apply confirmed likely-duplicate merges")
    p.add_argument("--group", type=int, action="append", default=[],
                   help="1-based group number to merge (repeatable)")
    p.add_argument("--hint", help="merge hint applied to all confirmed groups")
    p.add_argument("--approvals", help="JSON file: {group_num: hint|null}")

    cmd("init-sheet", help="step 4.5: init Fencers worksheet (remote)")

    p = cmd("ratings", help="step 5: fetch HEMA ratings/ranks")
    p.add_argument("--force-html", action="store_true",
                   help="also clear cached fighter HTML (hits hemaratings)")

    cmd("upload", help="step 6: sync enriched data to output sheet (remote)")
    cmd("seeds-recalc", help="recalculate Seed columns (remote)")

    p = cmd("remove-fencers", help="withdraw fencers (remote sheet edit)")
    p.add_argument("--name", action="append", default=[], required=True)
    p.add_argument("--confirm", action="store_true")

    cmd("sheet-create", help="create a blank output sheet (remote)")
    p = cmd("sheet-set-url", help="set + persist the output sheet URL (remote)")
    p.add_argument("url")

    p = cmd("run-all", help="run pipeline steps sequentially")
    p.add_argument("--from", dest="from_step", default="parse")
    p.add_argument("--to", dest="to_step", default="ratings")
    p.add_argument("--no-stop-on-error", action="store_true")

    cmd("render-lists", help="render Fencers + per-discipline PNGs from the output sheet (remote)")
    p = cmd("render-declaration", help="render the participant declaration PDF from fencers_deduped.json")
    p.add_argument("--date", required=True, help="tournament date string substituted into the template")

    for action in ("solve", "validate", "write", "render"):
        p = cmd(f"pool-{action}", help=f"pool_alch: {action}")
        p.add_argument("--discipline", required=True)
        p.add_argument("--from-state", action="store_true",
                       help="reuse persisted pool_alch_state.json")
        p.add_argument("--num-pools", type=int)
        p.add_argument("--waves", help="comma list of wave sizes, e.g. 3,3,2")
        p.add_argument("--parallel-waves",
                       help="comma list of 0-based parallel wave indices")

    p = cmd("pay-parse", help="step 7: parse a bank export file")
    p.add_argument("--file", required=True)
    p = cmd("pay-match", help="step 7: match payments to fencers")
    p.add_argument("--hints")
    cmd("pay-report", help="step 7: print the payment report")

    cmd("setup-show", help="print the resolved tournament config")
    p = cmd("setup-set", help="set a user-config key and persist it")
    p.add_argument("key")
    p.add_argument("value")

    p = cmd("eval-run", help="run a step and compare against golden")
    p.add_argument("step")
    p.add_argument("--golden", default="default")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--assert", dest="do_assert", action="store_true")
    p.add_argument("--threshold", action="append", default=[],
                   help="metric=value override (repeatable)")
    p = cmd("eval-diff", help="diff two eval runs of a step")
    p.add_argument("step")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p = cmd("eval-golden-save", help="freeze the current artifact as golden")
    p.add_argument("step")
    p.add_argument("--tag", default="default")
    p = cmd("eval-golden-list", help="list saved goldens")

    return parser


def _resolve(command: str):
    mod_name, func_name = _DISPATCH[command].split(":")
    return getattr(importlib.import_module(mod_name), func_name)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose, args.format == "json")

    try:
        config = build_config(args)
        handler = _resolve(args.command)
        result: StepResult = handler(args, config)
    except CliError as e:
        err = StepResult(step=args.command, ok=False, summary=str(e))
        emit(err, args.format)
        return e.exit_code
    except Exception as e:  # unexpected — treat as step error
        import logging

        logging.getLogger("hema-cli").exception("unhandled error")
        err = StepResult(step=args.command, ok=False, summary=f"{type(e).__name__}: {e}")
        emit(err, args.format)
        return 1

    emit(result, args.format)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
