import copy
import json
import tempfile
import unittest
from dataclasses import replace
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

    def test_new_event_is_reconciled_without_previous_market(self):
        with tempfile.TemporaryDirectory() as directory:
            prediction = load_predictions(DEMO / "model_predictions.json")[0]
            snapshot = load_json(DEMO / "odds_opening.json")
            event = next(item for item in snapshot["data"] if item["event_id"] == prediction.event_id)
            first_snapshot = {**snapshot, "data": [event]}
            new_prediction = replace(prediction, event_id="brand-new-event")
            new_event = copy.deepcopy(event)
            new_event["event_id"] = new_prediction.event_id
            second_snapshot = {
                **snapshot,
                "fetched_at": "2026-08-15T12:01:00Z",
                "data": [new_event],
            }
            with StateStore(Path(directory) / "state.db") as store:
                service = ReconciliationService(store)
                service.run([prediction], first_snapshot, "live")
                result = service.run([new_prediction], second_snapshot, "live")
            self.assertEqual(len(result.decisions), 1)
            self.assertEqual(result.decisions[0].event_id, "brand-new-event")
            self.assertEqual(result.decisions[0].status, "RECONCILED")

    def test_missing_market_is_audited_as_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            prediction = load_predictions(DEMO / "model_predictions.json")[0]
            snapshot = load_json(DEMO / "odds_opening.json")
            snapshot["data"] = []
            with StateStore(Path(directory) / "state.db") as store:
                service = ReconciliationService(store)
                result = service.run([prediction], snapshot, "live")
                replay = service.run([prediction], snapshot, "live")
                valid, count = store.verify_audit_chain()
            self.assertEqual(len(result.decisions), 1)
            self.assertEqual(result.decisions[0].winner, "ABSTAIN")
            self.assertEqual(result.decisions[0].status, "UNRESOLVED")
            self.assertIsNone(result.decisions[0].reconciled_probability)
            self.assertTrue(replay.reused)
            self.assertEqual(replay.warnings, result.warnings)
            self.assertTrue(valid)
            self.assertEqual(count, 1)

    def test_decision_changing_prediction_fields_invalidate_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = load_predictions(DEMO / "model_predictions.json")
            snapshot = load_json(DEMO / "odds_opening.json")
            with StateStore(Path(directory) / "state.db") as store:
                service = ReconciliationService(store)
                first = service.run(predictions, snapshot, "fixture")
                changed = list(predictions)
                changed[0] = replace(
                    changed[0], historical_accuracy=0.51, calibration_error=0.30
                )
                second = service.run(changed, snapshot, "fixture")
            self.assertNotEqual(first.run_id, second.run_id)
            self.assertFalse(second.reused)

    def test_raw_snapshot_tampering_breaks_run_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = load_predictions(DEMO / "model_predictions.json")
            snapshot = load_json(DEMO / "odds_opening.json")
            with StateStore(Path(directory) / "state.db") as store:
                result = ReconciliationService(store).run(predictions, snapshot, "fixture")
                store.connection.execute(
                    "UPDATE runs SET raw_snapshot_json = ? WHERE run_id = ?",
                    ('{"tampered":true}', result.run_id),
                )
                store.connection.commit()
                valid, _ = store.verify_audit_chain()
            self.assertFalse(valid)
            with StateStore(Path(directory) / "state.db") as reopened:
                still_invalid, _ = reopened.verify_audit_chain()
            self.assertFalse(still_invalid)

    def test_denormalized_ranking_tampering_breaks_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = load_predictions(DEMO / "model_predictions.json")
            snapshot = load_json(DEMO / "odds_opening.json")
            with StateStore(Path(directory) / "state.db") as store:
                ReconciliationService(store).run(predictions, snapshot, "fixture")
                store.connection.execute(
                    "UPDATE decisions SET final_rank = 99 WHERE decision_id = 1"
                )
                store.connection.commit()
                valid, _ = store.verify_audit_chain()
            self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()
