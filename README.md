# credo-fhir-migration

Migrates FHIR R4 `Patient` and `Observation` resources from the public [HAPI FHIR sandbox](https://hapi.fhir.org/baseR4) into a simplified internal model, exposes them through a read-only REST API (Django + DRF) and browses them in a small Vue UI.

Part 1 of the exercise, the plan for migrating ~50,000 patients, is in [Plan.md](Plan.md).

## Quick start

Requirements: Python 3.12 or newer (developed on 3.13; CI runs 3.12 and 3.13), Node.js 20.19+ or 22.12+, GNU Make.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
make setup                       # Python + JS dependencies, SQLite database
make import                      # full sandbox import, ~15 s (or: make import LIMIT=500)
make run                         # API on :8000 + UI on :5173
```

Then open http://localhost:5173. The import is safe to re-run at any time.

<details>
<summary>Without make</summary>

```bash
python -m pip install -e ".[dev]"
npm --prefix frontend ci
python backend/manage.py migrate
python backend/manage.py import_fhir [--limit N]
python backend/manage.py runserver          # terminal 1
npm --prefix frontend run dev               # terminal 2
```
</details>

## How it works

```
HAPI FHIR ──HTTP──▶ fhir_client ──▶ mapping ──▶ loader ──▶ SQLite ──▶ REST API ──▶ Vue UI
  (sandbox)   gzip, retries     pure functions   batches,           2 endpoints   list, detail
              pagination        FHIR → model     validation
```

| Module | Responsibility |
|---|---|
| [`fhir_client.py`](backend/migration/fhir_client.py) | Paged and grouped searches, `_summary=count`, retries, gzip. Independent from Django. |
| [`mapping.py`](backend/migration/mapping.py) | Pure functions: one FHIR resource in, one model instance out. |
| [`loader.py`](backend/migration/loader.py) | Batches, transactions, per-batch validation, run counters. |
| [`api/`](backend/migration/api/) | `GET /api/patients/` and `GET /api/patients/{id}/`. |

**Import algorithm.** For each page of 500 patients: one grouped `POST Observation/_search` with `subject=Patient/a,Patient/b,…` (all their observations, paged by 500), one `_summary=count` on the same search, then patients and observations are written in a single transaction. Upserts are keyed on the FHIR id.

**Internal model.** `Patient` (name, gender, birth date), `Observation` (status, category, LOINC-first code, value type + number / unit / text / components, effective date), and `MigrationRun` (counters of each run). The raw source resource is kept alongside each row for traceability and is never exposed by the API.

## Design choices

- **One grouped search per page of patients**, not one request per patient: 80 requests for the whole sandbox instead of ~3,300. POST keeps 500 references under URL-length limits.
- **No `_revinclude`**: on this server it silently caps included observations at 1,000 per page — data would be lost without an error.
- **No `_elements`**: measured no gain once gzipped (23 KB vs 25 KB per 500 observations), and it would truncate the stored source resource.
- **Atomic batches**: a batch is downloaded first, then written in one transaction. Patients never land without their observations, and no database lock is held during network calls.
- **Idempotent, so re-running is the resume strategy.** Server-side paging cursors expire; an 80-request re-run is cheap and leaves the same final state.
- **Retries where they can help**: timeouts, 429, 500/502/503/504, exponential backoff with jitter, 5 attempts, `Retry-After` honoured. Other 4xx fail immediately with the server's diagnostics.
- **Sequential import.** 80 requests don't need concurrency, and a shared public sandbox deserves politeness. Bounded parallelism for 50k patients is described in Plan.md.
- **Validation built in**: every batch compares the source count with what was fetched. A mismatch is reported, not fatal (the sandbox changes while we read it).
- **Defensive mapping**: a malformed resource is counted and skipped, never fails its batch. Values are exact `Decimal`s. Partial dates are not invented into full ones; day-only observation dates are kept and flagged so the UI never shows a made-up time.
- **No automatic deduplication.** 45% of sandbox patients share name and birth date with another record (test scripts). They are distinct source resources; merging on demographics risks mixing two people's records.
- **API**: list in 1 SQL query, detail in 2 (asserted in tests). No pagination (out of scope): the list is gzipped instead.

## Measured results

Full import into an empty database, 2026-10-05:

| | |
|---|---|
| Patients / observations imported | 3,317 / 30,302 |
| Batches matching the source count | 7 / 7 |
| Rejected resources (invalid / orphan) | 0 / 0 |
| HTTP requests (retries) | 80 (0) |
| Data received (gzip) | 2.0 MiB |
| Duration | 14 s, of which 81% network |
| Same import, one request per patient (computed) | 3,336 requests, ~8 min |
| `GET /api/patients/` (3,317 patients) | 498 KiB → 62 KiB gzipped, ~60 ms, 1 SQL query |
| `GET /api/patients/{id}/` (largest patient, 5,910 observations) | 72 KiB gzipped, ~150 ms, 2 SQL queries |

Profiling showed the database side (2.7 s, including mapping) is not worth optimising further: the network dominates and is already at the minimum number of requests for this approach.

## Tests and quality

```bash
make test    # 72 tests, no network access
make lint    # ruff + mypy (strict)
```

- **Client** (respx): pagination, grouped POST search, retries per status, `Retry-After`, give-up after 5 attempts, fail-fast on 4xx, off-server links refused, gzip. Retry tests use an injected sleep: no real waiting.
- **Mapping**: every value shape found on the sandbox, name and date rules, reference forms, invalid values.
- **Loader**: idempotence, a failure mid-batch leaves nothing behind, count mismatches, invalid and orphan resources, `--limit`.
- **API**: query counts, ordering, display values, `raw` never exposed, 404, 405, gzip.

CI runs lint and tests on Python 3.12 and 3.13, and type-checks and builds the frontend.

## Data and PHI

- Synthetic data only, from the public sandbox. The local database is git-ignored; no data, export or screenshot of records is committed.
- Logs and run errors carry the URL path, counts and FHIR ids only: never names, dates or values. Unexpected exceptions are reduced to their type, since their message could quote a field value.
- The API never serialises the stored source resource (minimum necessary).
- Even on synthetic data the sandbox contains user-submitted identifiers that look like real e-mail addresses — a reminder to treat any clinical dataset as if it were real. Production safeguards (encryption, access control, audit, retention) are in [Plan.md](Plan.md#4-safety-phi).

## Use of AI

I used **Claude (Claude Code)** as a pair programmer throughout:

- **Generated with AI, then reviewed by me**: the project scaffold, first drafts of the modules and their tests, the frontend components, and drafts of Plan.md and this README.
- **Exploration**: AI-run probes against the sandbox (page size cap, gzip ratio, `_revinclude` truncation, `_elements` gain, value-shape distribution). The figures in this README come from those real runs, not from estimates.
- **Mine**: the final say on scope and on every trade-off listed above, the review of each pull request before merging, and running the setup, tests and manual UI checks on my machine. Several changes came from that review and testing, such as the list filters and the duplicate analysis.

I can walk through and justify every line of the submission.

## What I'd do next

In priority order:

1. **Server-side filtering and pagination** on the patient list — client-side filtering is fine for 3,000 patients, not for 50,000.
2. **Production orchestration** as described in Plan.md: Bulk Data `$export` when available, Temporal workflow with bounded parallelism, resume from the last completed batch.
3. **PostgreSQL**, with staging tables per run and an atomic cutover; encrypted, time-limited storage of the raw resources.
4. **Duplicate-candidate report** for human review.
5. **Frontend tests** (component tests and one end-to-end path).

### Optimisations, sized from the measurements above

| Optimisation | Expected gain | Why not now |
|---|---|---|
| **Delta runs**: fetch only `_lastUpdated=gt<last successful run>`, skip resources whose `meta.lastUpdated` did not change, handle deletions | A re-run costs in proportion to what changed, instead of the full 80 requests | Needs deletion handling and a reliable "last successful run" watermark |
| **Overlap network and database**: fetch batch N+1 while batch N is written | Up to ~19% of import time (the database + mapping share) | Adds concurrency for a small gain at this size |
| **PostgreSQL bulk inserts** (or `COPY`): a few statements per batch instead of 84 (Django caps SQLite at 999 parameters, ~58 rows per insert; PostgreSQL allows 65,535) | Database share of the import, mostly at 50k scale | SQLite is what the exercise allows and is not the bottleneck here |
| **Store raw resources apart, one compressed NDJSON file per batch** (object storage) | `raw` is 30 MiB of the 44 MiB database; measured: 27.3 MiB of observations → 1.0 MiB gzipped as one file (row-by-row compression only halves it) | Simpler to keep it next to the row for this slice |
| **Denormalised `observation_count` / `last_observation_at`** on `Patient`, written by the loader | List query avoids aggregating every observation (15 ms today, grows with volume) | Premature at 30k observations |
| **Conditional GET on the list** (`ETag` derived from the last migration run) | Unchanged list → `304`, no payload | Data only changes on import; low value for a demo |
| **Paginate observations in the detail view** (latest N first) | Largest patient: 5,910 observations, 72 KiB gzipped in one response | Fine at this size |
| **Network vs database timings in `MigrationRun`** | Makes the profiling above part of every run summary | Measured once with a throwaway script for this README |
