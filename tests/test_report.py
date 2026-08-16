import tempfile
import unittest
from pathlib import Path

from signaljudge.models import Decision, RunResult
from signaljudge.report import generate_dashboard, generate_live_dashboard


def decision(selection: str) -> Decision:
    return Decision(
        event_id="event-1",
        selection=selection,
        model_probability=0.6,
        market_probability=0.55,
        reconciled_probability=0.58,
        model_reliability=0.7,
        market_reliability=0.6,
        model_weight=0.7,
        market_weight=0.6,
        winner="MODEL",
        decision_confidence=0.54,
        material_conflict=False,
        model_rank=1,
        market_rank=1,
        rank_delta=0,
        movement=0.0,
        movement_coherence=0.0,
        reason_codes=["MODEL_HIGHER_RELIABILITY"],
        rationale="Model won.",
        sport_key="soccer_epl",
        commence_time="2026-08-16T18:00:00Z",
        home_team="Arsenal",
        away_team="Chelsea",
        final_rank=1,
    )


class DashboardSecurityTests(unittest.TestCase):
    def test_untrusted_html_is_encoded_and_escaped_before_rendering(self):
        malicious = '<img src=x onerror="alert(1)">'
        run = RunResult(
            run_id="run-1",
            mode="fixture",
            odds_fetched_at="2026-08-16T18:00:00Z",
            decisions=[decision(malicious)],
            material_conflicts=0,
            source_counts={"MODEL": 1, "MARKET": 0, "ABSTAIN": 0},
        )
        metrics = {
            "MODEL": {"brier": 0.2, "log_loss": 0.5, "accuracy": 1.0, "sample_size": 1.0}
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            generate_dashboard(output, run, run, metrics, [], True, 1)
            html = output.read_text(encoding="utf-8")
        self.assertNotIn(malicious, html)
        self.assertIn("\\u003cimg", html)
        self.assertIn("${esc(d.selection)}", html)
        self.assertIn("Content-Security-Policy", html)

    def test_live_dashboard_contains_fixture_and_kickoff_context(self):
        run = RunResult(
            run_id="run-live",
            mode="live",
            odds_fetched_at="2026-08-16T17:00:00Z",
            decisions=[decision("Arsenal")],
            material_conflicts=1,
            source_counts={"MODEL": 1, "MARKET": 0, "ABSTAIN": 0},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "live.html"
            generate_live_dashboard(output, run, True, 1)
            html = output.read_text(encoding="utf-8")
        self.assertIn("<th>Fixture</th><th>Kickoff</th><th>Prediction</th>", html)
        self.assertIn('"live": true', html)
        self.assertIn("Arsenal", html)
        self.assertIn("Chelsea", html)


if __name__ == "__main__":
    unittest.main()
