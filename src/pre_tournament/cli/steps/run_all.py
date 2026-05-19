"""Run pipeline steps sequentially (replaces reg_agent/main.py).

Default range parse→ratings is fully offline; download/init-sheet/upload
only run when included in the range AND --allow-remote is set.
"""

import argparse

from pre_tournament.cli.errors import CliError
from pre_tournament.cli.steps._base import StepResult, timed

# Ordered pipeline. Each entry: step name → "module:function".
_ORDER: list[tuple[str, str]] = [
    ("download", "pre_tournament.cli.steps.download:cmd_download"),
    ("parse", "pre_tournament.cli.steps.parse:cmd_parse"),
    ("match", "pre_tournament.cli.steps.match:cmd_match"),
    ("dedup", "pre_tournament.cli.steps.dedup:cmd_dedup"),
    ("init-sheet", "pre_tournament.cli.steps.init_sheet:cmd_init_sheet"),
    ("ratings", "pre_tournament.cli.steps.ratings:cmd_ratings"),
    ("upload", "pre_tournament.cli.steps.upload:cmd_upload"),
]
_NAMES = [n for n, _ in _ORDER]


def _step_args(args) -> argparse.Namespace:
    """A namespace carrying globals + safe defaults for every step's options."""
    return argparse.Namespace(
        config=args.config,
        tournament=args.tournament,
        data_root=args.data_root,
        format=args.format,
        force=args.force,
        allow_remote=args.allow_remote,
        verbose=args.verbose,
        # step-specific defaults
        csv=None,
        sheet_url=None,
        worksheet=None,
        worksheet_index=0,
        instructions=None,
        force_html=False,
        group=[],
        hint=None,
        approvals=None,
        name=[],
        confirm=False,
    )


def cmd_run_all(args, config) -> StepResult:
    import importlib

    if args.from_step not in _NAMES or args.to_step not in _NAMES:
        return StepResult(
            step="run-all", ok=False,
            summary=f"--from/--to must be one of: {', '.join(_NAMES)}",
        )
    lo, hi = _NAMES.index(args.from_step), _NAMES.index(args.to_step)
    if lo > hi:
        return StepResult(step="run-all", ok=False, summary="--from is after --to")

    stop_on_error = not args.no_stop_on_error
    sub_args = _step_args(args)

    agg = StepResult(step="run-all")
    ran: list[str] = []
    with timed(agg):
        for name, target in _ORDER[lo : hi + 1]:
            mod_name, func_name = target.split(":")
            handler = getattr(importlib.import_module(mod_name), func_name)
            try:
                r = handler(sub_args, config)
            except CliError as e:
                agg.ok = False
                agg.details[name] = f"✗ {e}"
                ran.append(f"{name}✗")
                if stop_on_error:
                    break
                continue
            agg.details[name] = ("✓ " if r.ok else "✗ ") + r.summary
            ran.append(f"{name}{'✓' if r.ok else '✗'}")
            for w in r.warnings:
                agg.warnings.append(f"[{name}] {w}")
            if not r.ok:
                agg.ok = False
                if stop_on_error:
                    break

    agg.summary = f"ran {' → '.join(ran)}"
    return agg
