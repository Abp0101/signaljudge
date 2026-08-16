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
- remote odds responses;
- command-line paths and options;
- content rendered into the local HTML report.

## Threats and controls

| Threat | Control |
|---|---|
| Secret committed or logged | `.env` ignored; secret-bearing V4 request URLs are never logged or exception-chained; placeholder only in `.env.example` |
| SSRF through configurable URL | Fixed HTTPS base URL and sport-key allowlist |
| Malformed or oversized input | Strict types/ranges, identifier constraints, record and byte limits |
| Unsafe deserialization | JSON only; no pickle, YAML object construction or dynamic imports |
| Incorrect event reconciliation | Provider ID, exact team verification and bounded time check |
| API quota exhaustion | One batch fetch per sport, usage-header tracking, bounded retries and `Retry-After` support |
| Provider outage | Timeouts and last-known-good cache explicitly marked degraded |
| SQL injection | Parameterized SQLite statements; no user-provided SQL |
| Partial state | Transactional run and decision persistence |
| Silent audit modification | SHA-256 chain verification across canonical decision payloads |
| Browser script injection | Strict control-character/length validation and escaped embedded JSON |
| Container privilege | Non-root container user and read-only source by convention |

## Deployment note

The included HTTP server binds only to `127.0.0.1` and is a demonstration server. For shared deployment, use an authenticated reverse proxy with TLS, request limits, security headers and authorization around reports and raw model inputs.

The audit chain is tamper-evident only while a trusted checkpoint is retained. An attacker with full database write access could rebuild the chain. Production deployment should sign and export periodic checkpoints to immutable storage.
