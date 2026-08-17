# Trained model artifacts

`soccer_epl.model.json` is a dependency-free three-way Elo artifact trained by
`scripts/train_rating_models.py`. The artifact contains ratings, team aliases,
hyperparameters, chronological holdout metrics, training cutoff, source URLs,
source SHA-256 hashes and exact fields used.

Runtime precedence is documented in `predictions/README.md`. Artifacts are treated
as untrusted JSON and fully validated before inference. The model receives fixture
identity only and cannot inspect bookmaker odds.
