import json
import tempfile
import unittest
from pathlib import Path

from signaljudge.evaluation import evaluate
from signaljudge.cli import resolve_demo_dir
from signaljudge.io import load_json, load_predictions, load_results
from signaljudge.service import ReconciliationService
from signaljudge.state import StateStore


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"


class EndToEndTests(unittest.TestCase):
    def test_installed_cli_finds_fixtures_from_repository_working_directory(self):
        installed_module = Path("/opt/python/site-packages/signaljudge/cli.py")
        self.assertEqual(resolve_demo_dir(cwd=ROOT, module_file=installed_module), DEMO)

    def test_demo_meets_assessment_invariants(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = load_predictions(DEMO / "model_predictions.json")
            opening_snapshot = load_json(DEMO / "odds_opening.json")
            latest_snapshot = load_json(DEMO / "odds_latest.json")
            with StateStore(Path(directory) / "state.db") as store:
                service = ReconciliationService(store)
                opening = service.run(predictions, opening_snapshot, "opening")
                latest = service.run(predictions, latest_snapshot, "latest", opening_snapshot)
                self.assertGreaterEqual(opening.material_conflicts, 3)
                self.assertGreaterEqual(latest.material_conflicts, 3)
                self.assertGreater(latest.source_counts["MODEL"], 0)
                self.assertGreater(latest.source_counts["MARKET"], 0)
                self.assertEqual(len(latest.decisions), len(predictions))
                self.assertTrue(any(d.previous_probability is not None for d in latest.decisions))
                valid, count = store.verify_audit_chain()
                self.assertTrue(valid)
                self.assertEqual(count, 16)

    def test_reconciler_beats_blind_baselines_in_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = load_predictions(DEMO / "model_predictions.json")
            opening = load_json(DEMO / "odds_opening.json")
            latest = load_json(DEMO / "odds_latest.json")
            results = load_results(DEMO / "results.json")
            with StateStore(Path(directory) / "state.db") as store:
                decision_run = ReconciliationService(store).run(predictions, latest, "replay", opening)
            metrics, cases = evaluate(decision_run.decisions, results)
            self.assertLess(metrics["AGENT"]["brier"], metrics["MODEL"]["brier"])
            self.assertLess(metrics["AGENT"]["brier"], metrics["MARKET"]["brier"])
            self.assertTrue(any(case["corrected_model_only"] for case in cases))
            self.assertTrue(any(case["corrected_market_only"] for case in cases))

    def test_identical_input_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = load_predictions(DEMO / "model_predictions.json")
            snapshot = load_json(DEMO / "odds_opening.json")
            with StateStore(Path(directory) / "state.db") as store:
                service = ReconciliationService(store)
                first = service.run(predictions, snapshot, "fixture")
                second = service.run(predictions, snapshot, "fixture")
                self.assertEqual(first.run_id, second.run_id)
                self.assertTrue(second.reused)
                valid, count = store.verify_audit_chain()
                self.assertTrue(valid)
                self.assertEqual(count, 8)


if __name__ == "__main__":
    unittest.main()
