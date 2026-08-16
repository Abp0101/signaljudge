# Architecture and decisions

## Context

Two independent scorers rank the same sports events differently. The system must resolve each conflict, preserve state as market information changes, and explain every source choice after the fact.

The assessment context favours reproducibility, clarity and a short setup path. Expected batch size is tens of events, not millions.

## Components

| Component | Responsibility | Failure behaviour |
|---|---|---|
| Live provider | Secure request, ETag cache, retries | Last-known-good response marked degraded |
| Input validator | Bounds and validates untrusted JSON | Rejects invalid record; never executes input |
| Market normalizer | Converts odds, removes margin, rejects outliers | Event becomes a warning if no valid market remains |
| Decision engine | Applies gates, weights, winner and rationale | Deterministic for the same versioned input |
| Orchestrator | Matches, compares, ranks and persists | Idempotent content hash prevents duplicate run |
| SQLite store | Snapshots, revisions, audit chain, metrics | Transaction prevents partial run persistence |
| Replay dashboard | Time state and counterfactual comparison | Static output remains viewable without services |

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

## Data integrity

- All times must be timezone-aware and are converted to UTC.
- Probability values must be finite and strictly between zero and one.
- Input files are limited to 5 MiB and 1,000 predictions.
- Odds responses are limited to 5 MiB, 5,000 events and 250 books per event.
- Run IDs derive from canonical input content for idempotency.
- Decisions are stored transactionally and linked to prior event decisions.
- Audit entries form a hash chain starting from `GENESIS`.

## Scaling path

At larger scale, preserve the pure normalizer and decision engine while changing the edges:

1. poll providers into an object store and event queue;
2. process events idempotently with bounded workers;
3. store operational state in PostgreSQL and raw snapshots in immutable object storage;
4. publish signed audit checkpoints;
5. precompute rankings by league while retaining per-decision lineage;
6. monitor provider schema, staleness, source calibration and decision distribution.

