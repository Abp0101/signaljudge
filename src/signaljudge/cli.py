"""Command-line entry point for offline demo, fixture runs, live runs, and dashboard serving."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from signaljudge.application import ApplicationConfig, ApplicationService, serve_application
from signaljudge.demo import run_demo_replay
from signaljudge.evaluation import evaluate
from signaljudge.io import atomic_write_json, load_json, load_predictions
from signaljudge.models import RunResult, ValidationError
from signaljudge.provider import ALLOWED_REGIONS, ALLOWED_SPORTS, LiveOddsProvider
from signaljudge.report import generate_dashboard, generate_live_dashboard
from signaljudge.service import ReconciliationService
from signaljudge.state import StateStore


DEMO_FILES = (
    "model_predictions.json",
    "odds_opening.json",
    "odds_latest.json",
    "results.json",
)


def load_api_key_env_file(path: Path) -> bool:
    """Load only THE_ODDS_API_KEY from a small dotenv file without executing it."""
    if os.getenv("THE_ODDS_API_KEY", "").strip():
        return False
    path = path.resolve()
    if not path.is_file():
        return False
    if path.stat().st_size > 16 * 1024:
        raise ValidationError("environment file is unexpectedly large")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValidationError(f"could not read environment file: {path.name}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() != "THE_ODDS_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or len(value) > 512 or any(ord(character) < 33 for character in value):
            raise ValidationError("THE_ODDS_API_KEY in the environment file is invalid")
        os.environ["THE_ODDS_API_KEY"] = value
        return True
    return False


def resolve_demo_dir(
    cwd: Optional[Path] = None, module_file: Optional[Path] = None
) -> Path:
    """Locate fixtures in a clone, source checkout, container, or explicit override."""
    working_directory = (cwd or Path.cwd()).resolve()
    source_file = (module_file or Path(__file__)).resolve()
    candidates = []
    override = os.getenv("SIGNALJUDGE_DEMO_DIR")
    if override:
        candidates.append(Path(override).expanduser().resolve())
    candidates.extend(
        [
            working_directory / "data" / "demo",
            source_file.parents[2] / "data" / "demo",
            source_file.parent / "demo_data",
        ]
    )
    for candidate in candidates:
        if all((candidate / filename).is_file() for filename in DEMO_FILES):
            return candidate
    checked = ", ".join(str(candidate) for candidate in candidates)
    raise ValidationError(
        "demo fixtures were not found; run from the repository root or set "
        f"SIGNALJUDGE_DEMO_DIR (checked: {checked})"
    )


def _print_result(result: RunResult) -> None:
    print(f"\n{result.mode.upper()} · {result.run_id} · {result.odds_fetched_at}")
    print(f"Material conflicts: {result.material_conflicts} | Winners: {result.source_counts}")
    print("-" * 112)
    print(f"{'#':>2}  {'Selection':<24} {'Model':>7} {'Market':>7} {'Final':>7} {'Winner':<7}  Rationale")
    print("-" * 112)
    def probability(value: Optional[float]) -> str:
        return "   —  " if value is None else f"{value:>6.1%}"

    for item in result.decisions:
        rationale = item.rationale if len(item.rationale) <= 52 else item.rationale[:49] + "..."
        rank = "—" if item.final_rank is None else str(item.final_rank)
        print(
            f"{rank:>2}  {item.selection[:24]:<24} "
            f"{probability(item.model_probability)} {probability(item.market_probability)} "
            f"{probability(item.reconciled_probability)} {item.winner:<7}  {rationale}"
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


def _result_exit_code(result: RunResult, audit_valid: bool = True) -> int:
    if not audit_valid:
        return 4
    if not any(item.status == "RECONCILED" for item in result.decisions):
        return 3
    return 0


def command_demo(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    db_path = Path(args.db).resolve()
    demo_dir = resolve_demo_dir()
    with StateStore(db_path) as store:
        replay = run_demo_replay(store, demo_dir, "fixture")
        metrics, cases = evaluate(replay.latest.decisions, replay.results)
        audit_valid, audit_entries = store.verify_audit_chain()
        history = store.run_history()
    atomic_write_json(output_dir / "opening_ranking.json", _result_payload(replay.opening))
    atomic_write_json(output_dir / "latest_ranking.json", _result_payload(replay.latest))
    atomic_write_json(
        output_dir / "audit.json",
        {"audit_chain_valid": audit_valid, "entries": audit_entries, "runs": history},
    )
    atomic_write_json(output_dir / "evaluation.json", {"metrics": metrics, "cases": cases})
    generate_dashboard(
        output_dir / "report.html",
        replay.opening,
        replay.latest,
        metrics,
        cases,
        audit_valid,
        audit_entries,
    )
    _print_result(replay.opening)
    _print_result(replay.latest)
    print("\nReplay metrics (lower Brier/log loss is better):")
    for source, values in metrics.items():
        print(
            f"  {source:<6} Brier={values['brier']:.3f} "
            f"LogLoss={values['log_loss']:.3f} "
            f"SelectionAccuracy={values['accuracy']:.1%}"
        )
    corrected = [case for case in cases if case["corrected_model_only"] or case["corrected_market_only"]]
    print(f"Blind-source errors corrected: {len(corrected)}")
    print(f"Audit chain: {'VALID' if audit_valid else 'INVALID'} ({audit_entries} entries)")
    print(f"Dashboard: {output_dir / 'report.html'}")
    if args.serve:
        serve_report(output_dir / "report.html", args.port)
    return _result_exit_code(replay.latest, audit_valid)


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
    return _result_exit_code(result, audit_valid)


def command_live(args: argparse.Namespace) -> int:
    predictions = load_predictions(Path(args.predictions))
    provider = LiveOddsProvider(Path(args.cache_dir).resolve())
    snapshot = provider.fetch(args.sport_key, region=args.region)
    with StateStore(Path(args.db).resolve()) as store:
        result = ReconciliationService(store).run(predictions, snapshot, mode="live")
        audit_valid, entries = store.verify_audit_chain()
    _print_result(result)
    atomic_write_json(Path(args.output).resolve(), _result_payload(result))
    report = Path(args.report).resolve()
    generate_live_dashboard(report, result, audit_valid, entries)
    print(f"Audit chain: {'VALID' if audit_valid else 'INVALID'} ({entries} entries)")
    print(f"Dashboard: {report}")
    exit_code = _result_exit_code(result, audit_valid)
    if args.serve:
        serve_report(report, args.port)
    return exit_code


def serve_report(report: Path, port: int) -> None:
    report = report.resolve()
    if not report.is_file():
        raise ValidationError(f"report not found: {report}")
    if not 1024 <= port <= 65535:
        raise ValidationError("port must be between 1024 and 65535")
    with tempfile.TemporaryDirectory(prefix="signaljudge-report-") as directory:
        isolated_report = Path(directory) / report.name
        shutil.copy2(report, isolated_report)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
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


def command_app(args: argparse.Namespace) -> int:
    """Run the interactive localhost operator console."""
    load_api_key_env_file(Path(args.env_file))
    config = ApplicationConfig(
        prediction_dir=Path(args.predictions_dir).resolve(),
        model_dir=Path(args.model_dir).resolve(),
        db_path=Path(args.db).resolve(),
        cache_dir=Path(args.cache_dir).resolve(),
        demo_dir=resolve_demo_dir(),
    )
    service = ApplicationService(config)
    serve_application(service, port=args.port, open_browser=args.open)
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
    live.add_argument("--sport-key", default="baseball_mlb", choices=sorted(ALLOWED_SPORTS))
    live.add_argument(
        "--region",
        choices=sorted(ALLOWED_REGIONS),
        help="bookmaker region; defaults to UK for EPL and US otherwise",
    )
    live.add_argument("--db", default=".signaljudge/live.db")
    live.add_argument("--cache-dir", default=".signaljudge/cache")
    live.add_argument("--output", default="artifacts/live-ranking.json")
    live.add_argument("--report", default="artifacts/live-report.html")
    live.add_argument("--serve", action="store_true", help="serve the generated live dashboard")
    live.add_argument("--port", type=int, default=8765)
    live.set_defaults(func=command_live)

    app = subparsers.add_parser(
        "app", help="open the interactive live-fixture and prediction console"
    )
    app.add_argument(
        "--predictions-dir",
        default="predictions",
        help="directory containing one <sport_key>.json prediction file per sport",
    )
    app.add_argument(
        "--model-dir",
        default="models",
        help="directory containing validated <sport_key>.model.json artifacts",
    )
    app.add_argument("--db", default=".signaljudge/application.db")
    app.add_argument("--cache-dir", default=".signaljudge/cache")
    app.add_argument(
        "--env-file",
        default=".env",
        help="dotenv file from which only THE_ODDS_API_KEY is safely parsed",
    )
    app.add_argument("--port", type=int, default=8765)
    app.add_argument("--open", action="store_true", help="open the application in a browser")
    app.set_defaults(func=command_app)

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
