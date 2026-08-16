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

The bundled assessment demo uses `data/demo/model_predictions.json` and is selected
from the application using **Assessment demo**. Those labelled synthetic fixtures
are not a live prediction source.
