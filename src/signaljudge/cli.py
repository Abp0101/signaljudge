"""Command-line entry point for offline demo, fixture runs, live runs, and dashboard serving."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from signaljudge.evaluation import evaluate
from signaljudge.io import atomic_write_json, load_json, load_predictions, load_results
from signaljudge.models import RunResult, ValidationError
from signaljudge.provider import LiveOddsProvider
from signaljudge.report import generate_dashboard
from signaljudge.service import ReconciliationService
from signaljudge.state import StateStore


ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "data" / "demo"


def _print_result(result: RunResult) -> None:
    print(f"\n{result.mode.upper()} · {result.run_id} · {result.odds_fetched_at}")
    print(f"Material conflicts: {result.material_conflicts} | Winners: {result.source_counts}")
    print("-" * 112)
    print(f"{'#':>2}  {'Selection':<24} {'Model':>7} {'Market':>7} {'Final':>7} {'Winner':<7}  Rationale")
    print("-" * 112)
    for item in result.decisions:
        rationale = item.rationale if len(item.rationale) <= 52 else item.rationale[:49] + "..."
        print(
            f"{item.final_rank:>2}  {item.selection[:24]:<24} "
            f"{item.model_probability:>6.1%} {item.market_probability:>6.1%} "
            f"{item.reconciled_probability:>6.1%} {item.winner:<7}  {rationale}"
        )
    if result.warnings:
        print("\nWarnings:")
        for warning in result.warnings:
            print(f"  - {warning}")


def _result_payload(result: RunResult):
    return {
        "run_id": result.run_id,
        "mode": result.mode,
        "odds_fetched_at": result.odds_fetched_at,
        "material_conflicts": result.material_conflicts,
        "source_counts": result.source_counts,
        "warnings": result.warnings,
        "decisions": [item.as_dict() for item in result.decisions],
    }


def command_demo(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    db_path = Path(args.db).resolve()
    predictions = load_predictions(DEMO_DIR / "model_predictions.json")
    opening_snapshot = load_json(DEMO_DIR / "odds_opening.json")
    latest_snapshot = load_json(DEMO_DIR / "odds_latest.json")
    results = load_results(DEMO_DIR / "results.json")
    with StateStore(db_path) as store:
        service = ReconciliationService(store)
        opening = service.run(predictions, opening_snapshot, mode="fixture-opening", previous_snapshot=None)
        latest = service.run(predictions, latest_snapshot, mode="fixture-latest", previous_snapshot=opening_snapshot)
        metrics, cases = evaluate(latest.decisions, results)
        store.save_metrics(latest.run_id, metrics)
        audit_valid, audit_entries = store.verify_audit_chain()
        history = store.run_history()
    atomic_write_json(output_dir / "opening_ranking.json", _result_payload(opening))
    atomic_write_json(output_dir / "latest_ranking.json", _result_payload(latest))
    atomic_write_json(
        output_dir / "audit.json",
        {"audit_chain_valid": audit_valid, "entries": audit_entries, "runs": history},
    )
    atomic_write_json(output_dir / "evaluation.json", {"metrics": metrics, "cases": cases})
    generate_dashboard(output_dir / "report.html", opening, latest, metrics, cases, audit_valid, audit_entries)
    _print_result(opening)
    _print_result(latest)
    print("\nReplay metrics (lower Brier/log loss is better):")
    for source, values in metrics.items():
        print(
            f"  {source:<6} Brier={values['brier']:.3f} "
            f"LogLoss={values['log_loss']:.3f} Accuracy={values['accuracy']:.1%}"
        )
    corrected = [case for case in cases if case["corrected_model_only"] or case["corrected_market_only"]]
    print(f"Blind-source errors corrected: {len(corrected)}")
    print(f"Audit chain: {'VALID' if audit_valid else 'INVALID'} ({audit_entries} entries)")
    print(f"Dashboard: {output_dir / 'report.html'}")
    if args.serve:
        serve_report(output_dir / "report.html", args.port)
    return 0


def command_run(args: argparse.Namespace) -> int:
    predictions = load_predictions(Path(args.predictions))
    snapshot = load_json(Path(args.odds))
    previous = load_json(Path(args.previous_odds)) if args.previous_odds else None
    with StateStore(Path(args.db).resolve()) as store:
        result = ReconciliationService(store).run(predictions, snapshot, mode="fixture", previous_snapshot=previous)
        audit_valid, entries = store.verify_audit_chain()
    _print_result(result)
    atomic_write_json(Path(args.output).resolve(), _result_payload(result))
    print(f"Audit chain: {'VALID' if audit_valid else 'INVALID'} ({entries} entries)")
    return 0


def command_live(args: argparse.Namespace) -> int:
    predictions = load_predictions(Path(args.predictions))
    provider = LiveOddsProvider(Path(args.cache_dir).resolve())
    snapshot = provider.fetch(args.sport_key)
    with StateStore(Path(args.db).resolve()) as store:
        result = ReconciliationService(store).run(predictions, snapshot, mode="live")
    _print_result(result)
    atomic_write_json(Path(args.output).resolve(), _result_payload(result))
    return 0


def serve_report(report: Path, port: int) -> None:
    report = report.resolve()
    if not report.is_file():
        raise ValidationError(f"report not found: {report}")
    if not 1024 <= port <= 65535:
        raise ValidationError("port must be between 1024 and 65535")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(report.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Serving dashboard at http://127.0.0.1:{port}/{report.name} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def command_serve(args: argparse.Namespace) -> int:
    serve_report(Path(args.report), args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signaljudge",
        description="Reconcile sports-model predictions with live market odds, with a full audit trail.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="run the reproducible two-snapshot replay")
    demo.add_argument("--db", default=".signaljudge/demo.db")
    demo.add_argument("--output-dir", default="artifacts")
    demo.add_argument("--serve", action="store_true", help="serve the generated dashboard locally")
    demo.add_argument("--port", type=int, default=8765)
    demo.set_defaults(func=command_demo)

    run = subparsers.add_parser("run", help="reconcile a local prediction file and odds snapshot")
    run.add_argument("--predictions", required=True)
    run.add_argument("--odds", required=True)
    run.add_argument("--previous-odds")
    run.add_argument("--db", default=".signaljudge/state.db")
    run.add_argument("--output", default="artifacts/ranking.json")
    run.set_defaults(func=command_run)

    live = subparsers.add_parser("live", help="fetch live odds and reconcile matching predictions")
    live.add_argument("--predictions", required=True)
    live.add_argument("--sport-key", default="baseball_mlb", choices=["baseball_mlb", "basketball_nba"])
    live.add_argument("--db", default=".signaljudge/live.db")
    live.add_argument("--cache-dir", default=".signaljudge/cache")
    live.add_argument("--output", default="artifacts/live-ranking.json")
    live.set_defaults(func=command_live)

    serve = subparsers.add_parser("serve", help="serve an existing report on localhost")
    serve.add_argument("--report", default="artifacts/report.html")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=command_serve)
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        raise SystemExit(args.func(args))
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

