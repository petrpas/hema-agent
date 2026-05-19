"""Step 7 — payment parse / match / report (no Discord)."""

from pathlib import Path

from pre_tournament.cli.steps._base import StepResult, artifacts, timed
from step7_payments import (
    PaymentsResult,
    build_fencer_summaries,
    format_payments_report,
    load_all_parsed,
    match_payments,
    parse_and_store,
)
from utils import load_fencers_list, FENCERS_DEDUPED_FILE


def cmd_pay_parse(args, config) -> StepResult:
    data_dir = config.data_dir
    src = artifacts.require(Path(args.file), "bank export file", args.file)
    raw = src.read_text(encoding="utf-8", errors="replace")

    res = StepResult(step="pay-parse")
    with timed(res):
        txns = parse_and_store(raw, src.name, data_dir, config)

    res.summary = f"parsed {len(txns)} transaction(s) from {src.name}"
    res.details = {"transactions": len(txns)}
    res.artifact = artifacts.payments_parsed_dir(data_dir) / f"{src.stem}.json"
    return res


def cmd_pay_match(args, config) -> StepResult:
    data_dir = config.data_dir
    transactions = load_all_parsed(data_dir)
    if not transactions:
        return StepResult(step="pay-match", ok=False,
                          summary="no parsed payment files — run `pay-parse` first")
    artifacts.require(artifacts.deduped(data_dir), "fencers_deduped.json", "run `dedup` first")
    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)
    summaries = build_fencer_summaries(fencers)

    res = StepResult(step="pay-match")
    with timed(res):
        result = match_payments(transactions, summaries, args.hints, config)

    artifacts.payments_matched(data_dir).write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    res.summary = (
        f"{len(result.matched)} matched, {len(result.possible)} possible, "
        f"{len(result.unmatched_payments)} unmatched payments, "
        f"{len(result.unmatched_fencers)} fencers without payment"
    )
    res.details = {
        "matched": len(result.matched),
        "possible": len(result.possible),
        "unmatched_payments": len(result.unmatched_payments),
        "unmatched_fencers": len(result.unmatched_fencers),
    }
    res.artifact = artifacts.payments_matched(data_dir)
    return res


def cmd_pay_report(args, config) -> StepResult:
    data_dir = config.data_dir
    mp = artifacts.require(
        artifacts.payments_matched(data_dir), "payments/matched.json",
        "run `pay-match` first",
    )
    result = PaymentsResult.model_validate_json(mp.read_text())

    fencer_disciplines: dict[str, str] = {}
    if artifacts.deduped(data_dir).exists():
        fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)
        fencer_disciplines = {
            f.name: ", ".join(d.str() for d in f.disciplines) for f in fencers
        }

    report = format_payments_report(result, fencer_disciplines)
    res = StepResult(step="pay-report")
    res.summary = "payment report"
    res.details = {"report": "\n" + report}
    return res
