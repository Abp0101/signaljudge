# Security policy and threat model

## Supported version

Security fixes are applied to the latest `1.x` release on the default branch.

## Reporting

Do not open a public issue containing credentials or exploitable details. Contact the repository owner privately with reproduction steps and impact. Revoke any exposed API key immediately.

## Assets and trust boundaries

Protected assets:

- odds-provider API key and quota;
- integrity of event matching, probabilities, rankings and audit records;
- availability of the local process and provider allowance;
- confidentiality of any future proprietary model inputs.

Untrusted inputs:

- prediction and result JSON files;
- trained model JSON artifacts and historical training responses;
- remote odds responses;
- command-line paths and options;
- content rendered into the local HTML report.

## Threats and controls

| Threat | Control |
|---|---|
| Secret committed or logged | `.env` ignored; app safely parses only the literal API-key assignment; secret-bearing V4 request URLs are never logged or exception-chained; placeholder only in `.env.example` |
| SSRF through configurable URL | Fixed HTTPS base URL plus sport-key and bookmaker-region allowlists |
| Malformed or oversized input | Strict types/ranges, identifier constraints, record and byte limits |
| Unsafe deserialization | JSON only; no pickle, YAML object construction or dynamic imports |
| Model artifact tampering | Strict schema/range/alias/provenance validation; invalid artifacts fail closed |
| Training data leakage | Fixed source URLs; trainer parses only six result fields and discards every odds column |
| Market leakage into model | Fixture-only inference object excludes books, prices, movement and market probabilities |
| Post-start backfill | Local model skips fixtures whose kickoff is not strictly after generation time |
| Incorrect event reconciliation | Provider ID, exact team verification and bounded time check |
| API quota exhaustion | One batch fetch per sport, usage-header tracking, bounded retries and `Retry-After` support |
| Browser-triggered quota exhaustion | Same-origin custom request header, five-minute response cache and manual-refresh cooldown |
| Provider outage | Timeouts; recent cache marked degraded and aged against evaluation time; expired cache rejected |
| SQL injection | Parameterized SQLite statements; no user-provided SQL |
| Partial state | Transactional run and decision persistence |
| Silent audit modification | SHA-256 chains across canonical decisions and run envelopes containing source evidence |
| Browser script injection | Escaped embedded JSON, output encoding at DOM sinks and restrictive Content Security Policy |
| Localhost DNS rebinding / hostile embedding | Loopback bind, Host allowlist, no CORS, frame denial and same-origin assets/API |
| Accidental filesystem exposure | Application serves only fixed in-memory assets and named API routes, never a directory |
| Container privilege | Non-root container user and read-only source by convention |
| Local data disclosure | State and generated artifacts are created with owner-only file permissions |

## Deployment note

Both included HTTP surfaces bind only to `127.0.0.1`. The static-report server serves
an isolated copy of the selected report rather than its artifact directory. The
interactive application serves only fixed in-memory assets and allowlisted JSON
routes; it rejects untrusted Host headers and does not enable CORS. These remain
single-operator demonstration servers. For shared deployment, use an authenticated
reverse proxy with TLS, durable request limits, security headers and authorization
around reports and raw model inputs.

The audit chain is tamper-evident only while a trusted checkpoint is retained. An attacker with full database write access could rebuild the chain. Production deployment should sign and export periodic checkpoints to immutable storage.
