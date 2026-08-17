# Live prediction adapters

Place independently produced prediction files in this directory using the exact
sport key as the filename:

```text
predictions/
  baseball_mlb.json
  basketball_nba.json
  soccer_epl.json
```

Each file uses the schema documented in the main README. Event IDs, teams, sport,
and kickoff time must match the odds provider event exactly. The application keeps
unmatched fixtures visible as `NO_PREDICTION`; it never manufactures a model score
from bookmaker odds.

Prediction source precedence is:

1. `predictions/<sport_key>.json` — exact external/local model output;
2. `models/<sport_key>.model.json` — validated bundled rating-model inference;
3. explicit `NO_PREDICTION` rows when neither is available.

The bundled EPL artifact generates predictions automatically. NBA and MLB currently
remain unavailable until separately validated, authorised training sources are added.
The UI exposes the selected source type, model version, holdout accuracy and sample
size rather than presenting every score as equivalent.

The bundled assessment demo uses `data/demo/model_predictions.json` and is selected
from the application using **Assessment demo**. Those labelled synthetic fixtures
are not a live prediction source.
