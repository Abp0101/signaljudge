# Architecture and decisions

## Context

Two independent scorers rank the same sports events differently. The system must resolve each conflict, preserve state as market information changes, and explain every source choice after the fact.

The assessment context favours reproducibility, clarity and a short setup path. Expected batch size is tens of events, not millions.

## Components

| Component | Responsibility | Failure behaviour |
|---|---|---|
| Live provider | Secure V4 request, sport-aware region, schema adapter, quota tracking, retries | Recent last-known-good response marked degraded; expired cache rejected |
| Input validator | Bounds and validates untrusted JSON | Rejects invalid record; never executes input |
| Local model adapter | Converts fixture identity into an independent probability from a validated artifact | Missing/invalid artifact remains explicit; started games are never backfilled |
| Market normalizer | Converts odds, removes margin, rejects outliers | Event becomes a warning if no valid market remains |
| Decision engine | Applies gates, weights, winner, abstention and rationale | Deterministic for the same versioned input |
| Orchestrator | Matches, compares, ranks and persists | Idempotent content hash prevents duplicate run |
| SQLite store | Scoped snapshots, revisions, decision and run audit chains, metrics | Immediate transaction prevents partial or interleaved persistence |
| Replay/live dashboard | Fixture identity, kickoff, probabilities, time state and counterfactual comparison | Static output remains viewable without services |
| Local application API | Sport selection, fixture/prediction join, bounded refresh and browser view model | Missing predictions remain visible; invalid input fails closed |
| Operator console | Sort/filter current fixtures and inspect complete rationale | Demo mode remains usable when live service is unavailable |

## Key decisions

### Modular monolith over microservices

**Chosen:** one Python package with explicit component boundaries.

**Why:** atomic local transactions, no deployment topology, fast reviewer setup and sufficient scale.

**Trade-off:** independent component scaling is unavailable. If API polling, evaluation and web serving developed materially different load profiles, they could be separated behind a queue later.

### Deterministic rule over LLM decision-making

**Chosen:** versioned mathematical rule and structured reason codes.

**Why:** probabilities must be reproducible, testable and auditable. An LLM could render prose later but must not own source selection.

**Trade-off:** human-designed features may miss contexts not represented by the reliability model. Historical evaluation should drive subsequent rule or meta-model development.

### Winner-dominant blend over hard replacement

**Chosen:** the larger reliability weight defines an explicit winner while both weighted probabilities contribute to the final score.

**Why:** it satisfies source accountability and avoids unstable discontinuities near a threshold.

**Trade-off:** the final number is not identical to the winner's probability. The audit therefore exposes both weights and separates decision confidence from outcome probability.

### Exact identity over unrestricted fuzzy matching

**Chosen:** provider event ID plus exact teams and a 15-minute start-time guard.

**Why:** silently reconciling the wrong event is a severe integrity failure.

**Trade-off:** upstream naming changes can reject a legitimate event. A future identity service could maintain reviewed aliases without permitting silent ambiguity.

### SQLite over PostgreSQL

**Chosen:** WAL-mode SQLite and transactional append-only decisions.

**Why:** zero setup and correct state for the single-process assessment.

**Trade-off:** limited concurrent writers and no remote durability. PostgreSQL is the production migration point for multi-user deployment.

### Standard library runtime

**Chosen:** no mandatory runtime package download.

**Why:** review environments and video demos should work even when package registries or network access do not.

**Trade-off:** validation and the dashboard require more local code than Pydantic and a UI framework. Domain boundaries keep a future replacement straightforward.

### Local application over a hosted service

**Chosen:** a same-origin browser console backed by a standard-library server bound
to `127.0.0.1`.

**Why:** it adds a product-quality workflow without accounts, deployment credentials,
or a new framework. The API key remains in the Python process and the existing core
service stays the only reconciliation path.

**Trade-off:** this is deliberately single-operator software. A shared deployment
would require authenticated HTTPS, authorization, durable rate limiting, production
observability, and a multi-writer database.

### Versioned rating artifact over runtime training

**Chosen:** train offline from allowlisted result fields, commit a small JSON artifact,
and perform dependency-free inference inside the application.

**Why:** reviewer runs are fast and deterministic; training provenance, source hashes,
parameters and untouched holdout metrics travel with the model. The inference API
accepts fixture identity only, making accidental odds leakage structurally difficult.

**Trade-off:** the bundled EPL model uses team-strength and home-advantage features,
not injuries, line-ups or player form. Manual prediction files intentionally override
the artifact when a stronger independent model is available. NBA and MLB fail visibly
as unavailable until their own authorised artifacts exist.

## Application data flow

```text
Browser sport selection
        -> allowlisted localhost API
        -> quota-aware live provider
        -> fixture-only model adapter or exact prediction file
        -> existing ReconciliationService
        -> SQLite state and audit verification
        -> fixture view model
        -> sort/filter in the browser
```

The server returns all provider fixtures, not only matched predictions. An unmatched
fixture is an explicit `NO_PREDICTION` view state and never enters the ranked batch.
Responses are held in memory for five minutes; a manual refresh has a 30-second
cooldown. Provider last-known-good caching remains a separate, explicitly degraded
reliability mechanism.

## Model lifecycle and leakage control

```text
Fixed historical-result URLs
        -> allowlist six non-odds fields
        -> chronological train / tune / untouched test
        -> JSON artifact + source SHA-256 hashes + metrics
        -> strict artifact validation
        -> fixture-only inference
        -> normal Prediction domain object
```

Prediction files have precedence over artifacts. Generated predictions use the live
provider event ID but the model never receives the event's bookmaker list. Fixtures
that have already started are skipped instead of being assigned a falsely pregame
prediction. Teams missing from the training artifact are explicitly marked out of
distribution, which activates the existing decision safety gate.

## Data integrity

- All times must be timezone-aware and are converted to UTC.
- Probability values must be finite and strictly between zero and one.
- Rating artifacts bound team count, aliases, ratings, appearances, parameters and validation metrics.
- Every trained artifact declares source URLs, hashes, row counts and result fields used.
- Input files are limited to 5 MiB and 1,000 predictions.
- Odds responses are limited to 5 MiB, 5,000 events and 250 books per event.
- Sport and bookmaker region are selected from fixed allowlists; EPL defaults to UK books.
- Soccer head-to-head normalization removes margin across home, away and draw outcomes.
- Run IDs derive from canonical input content for idempotency.
- Canonical input includes all prediction fields, current and previous snapshots, context and policy configuration.
- Previous state is scoped by sport, model version and market type; a new event does not require historical odds.
- Unavailable markets and dual-source safety failures remain visible as unranked abstentions.
- Decisions are stored transactionally and linked to prior event decisions.
- Decision and run-envelope audit entries form hash chains starting from `GENESIS`.

## Scaling path

At larger scale, preserve the pure normalizer and decision engine while changing the edges:

1. poll providers into an object store and event queue;
2. process events idempotently with bounded workers;
3. store operational state in PostgreSQL and raw snapshots in immutable object storage;
4. publish signed audit checkpoints;
5. precompute rankings by league while retaining per-decision lineage;
6. monitor provider schema, staleness, source calibration and decision distribution.
