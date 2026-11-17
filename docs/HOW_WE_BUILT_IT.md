# How the F1 Telemetry Analytics Platform Was Built, Step by Step

This document tells the story of the implementation in the order the work happened: what was built at each step, which file holds it, the problem it solved and how it was checked. Use it to explain the project from first idea to live deployment.

## Step 1. Define the goal and the shape of the system

Goal: turn raw Formula 1 timing and telemetry into something a person can explore: compare two laps fairly, measure tyre wear, and replay a session. I decided on a pipeline where each stage has one job and can be tested alone:

source data -> ingestion -> Parquet cache -> alignment -> PostgreSQL -> API -> dashboard

Ground rules set at the start: never use fake or invented telemetry, never store credentials in the repo, keep raw data out of git, and keep every stage re-runnable. The design is summarised in `docs/architecture.md`.

## Step 2. Project scaffold and tooling

- A Python 3.11 package under `src/f1tel/` with a `pyproject.toml` that pins the dependency ranges.
- Quality tools from day one: pytest with a coverage gate of 80%, Hypothesis for property tests, ruff for linting and formatting.
- Configuration in `config.py` using pydantic-settings, so every setting comes from environment variables with the `F1TEL_` prefix (database URL, data folder, allowed web origins, grid step, fuel effect).
- Windows helper script `tasks.ps1` for setup, test, lint and run.

## Step 3. Ingestion: getting real data safely

Files: `ingest.py`, `loader.py`, `clients/`, `retry.py`, `manifest.py`.

- A command-line tool `f1tel` (built with Typer) with commands `ingest`, `seed`, `batch`, `load-db`, `verify` and `crosscheck`.
- Timing and telemetry come from FastF1, results and pit stops from Jolpica, and lap times for cross-checking from OpenF1. FastF1 3.9 needs a Formula 1 account, so credentials are read from `F1_USERNAME` and `F1_PASSWORD` in the user's own shell and never written to disk or git.
- Every network call goes through a retry helper with exponential backoff.
- Output is one Parquet file per lap plus a laps table, and a manifest of SHA-256 checksums. Files are written atomically (write to a temp name, then rename), so a crash never leaves a half-written file.
- Idempotent by design: re-running skips sessions whose checksums match and repairs partial ones. `f1tel verify` re-checks every checksum.

## Step 4. Alignment: making two laps comparable

File: `align.py`.

Problem: two laps have different durations, so comparing at the same timestamp puts the cars at different places on track. Solution: resample every lap onto a fixed 10 m distance grid using interpolation, then compute the delta as `time_B - time_A` at each grid point (positive means B is slower). The same module builds 20 mini-sectors and raises a clear `AlignmentError` for laps that cannot be aligned.

Checks: property-based tests (monotonic distance, exact grid step) and, later, an independent NumPy recalculation on real Monza data that matched the API output exactly.

## Step 5. Tyre degradation maths

File: `degradation.py`.

- Clean the data: remove pit in/out laps, safety-car laps and the first lap.
- Apply a fuel correction: cars get lighter and faster as fuel burns, so a fixed 0.03 s per lap (configurable) is added back. This is documented as an approximation.
- Fit an ordinary least squares line and report the slope (seconds lost per lap of tyre age) with a t-distribution confidence interval and a confidence band around the line.
- Too few laps raises `InsufficientDataError`, which the API turns into a 422 response instead of a made-up number.

Checks: a synthetic test session with a planted tyre slope, fuel effect, safety-car lap and pit stop. The tests confirm the code recovers the planted slope.

## Step 6. Database

Files: `db.py`, `dbload.py`, `alembic/`.

- SQLAlchemy 2 models for sessions, drivers, laps, stints and the resampled telemetry. Migration `0001` creates the schema with Alembic.
- The telemetry table is LIST-partitioned in PostgreSQL, one partition per session, so loading or deleting a session touches only its own partition.
- `dbload.py` reads the Parquet files, resamples laps to the grid and inserts them. Loading a session is idempotent: the old copy is replaced.
- Tests run on SQLite for speed, and a PostgreSQL-only test runs in CI.

## Step 7. The API

Files: `api.py`, `schemas.py`, `deps.py`, `replay.py`.

- FastAPI endpoints: `/sessions`, `/drivers`, `/laps`, `/stints`, `/compare`, `/degradation`, `/health`, plus the WebSocket `/ws/replay`.
- Pydantic schemas validate every response. Inputs are bounded (driver code length, lap numbers, confidence level). CORS only allows the dashboard's own origin and only GET.
- Error mapping: bad input is 422, unknown session or lap is 404, database problems are 503, alignment and data problems are 422 with a readable message.
- The replay WebSocket sends a `hello` message (time range, drivers, teams), then `frame` messages with car positions, and an `end` message.

## Step 8. The dashboard

Folder: `frontend/` (React 19, TypeScript, Vite, Tailwind 4, Plotly).

- `ComparePanel`: session and driver pickers, speed, throttle and brake overlay, delta trace with corner markers, sector tables.
- `DegradationPanel`: stint picker and a chart with the fitted line and confidence band.
- `ReplayPanel`: track map with moving cars streamed over the WebSocket.
- A small typed API layer (`api.ts`, `types.ts`) so a change in the API shows up as a compile error.
- The API address is set at build time through `VITE_API_URL`.

## Step 9. Docker, CI and deployment

- `Dockerfile.api` and `docker-compose.yml`: one command starts PostgreSQL, the API (which runs migrations on start) and the dashboard.
- GitHub Actions (`.github/workflows/ci.yml`): lint and tests on every push, with a PostgreSQL service, plus a nightly smoke test against real data.
- Live deployment: `render.yaml` defines the API container and a managed PostgreSQL on Render; `frontend/netlify.toml` builds the dashboard on Netlify with a single-page-app redirect.

## Step 10. Real data and the problems it exposed

Loading the five real sessions (2024 Monza Q and R, Bahrain R, Monaco Q, Silverstone R) turned up issues that the synthetic fixture could not:

- **Render's free database cut long transactions**, so a session load failed with "server closed the connection". Fix: load telemetry in short per-chunk transactions, retry a chunk if the connection drops, and delete a half-loaded session if the load fails. Tests were added for both behaviours.
- **Qualifying has no tyre degradation.** Qualifying stints are about three laps, so the fit failed with "need at least 3 laps with distinct tyre ages". Rather than fake a line, the dashboard now explains this, offers a button that jumps to a race, and shows each stint's push laps instead.
- **Qualifying lap-time trend.** A new `/pace-trend` endpoint fits a clearly labelled session trend over push laps (within 107% of the driver's best, excluding in and out laps). It reflects track evolution, not tyre wear, and the page says so.
- **In and out laps** are listed but disabled in the lap pickers, with a reason.
- **Replay leaderboard** with driver and team names, ordered by lap and distance. It only uses stored accurate laps, so it is approximate and labelled as such.

## Step 11. Measuring and documenting

`scripts/measure.py` produced the real numbers in the README: 2,181,236 raw samples across 3,133 laps, and 0.21 s to reload a session from Parquet versus 49.5 s from a cold FastF1 load. Tests were added until the coverage claim in the README was true.

## Step 12. Audit and hardening

A full audit checked the backend, data, security and performance, and wrote down what was and was not verified (see `F1_TELEMETRY_PLATFORM_COMPLETE_AUDIT_REPORT.md`). Confirmed issues were fixed:

- Huge integer ids returned a database error (503); they are now rejected with 422.
- Security headers were added to the API and to the Netlify site.
- Throttle readings above 100 (up to 104 in the raw feed) are clipped to 0 to 100 when loading.
- Telemetry accuracy was checked: the delta matched an independent calculation exactly, sector times summed to lap times, and database integrity checks found no orphans or duplicates.

## Step 13. Handover documentation

The README gained a handover section (status, where things live, conventions, how to verify a change), and `docs/PROJECT_WALKTHROUGH.md` explains the stack and workflow for non-experts.

## Timeline from the git history

- 15 Nov 2026: scaffold, then the first full platform commit.
- 16 Nov 2026: Docker, Render and Netlify configuration; the loader robustness fix; the qualifying views; the replay leaderboard; the qualifying trend; the audit fixes.
- 17 Nov 2026: handover documentation.

## How to explain it in one breath

"I built an end-to-end F1 analytics platform: a re-runnable ingestion tool that caches real data with checksums, a distance-based alignment step so laps compare fairly, a statistics module with confidence intervals, a partitioned PostgreSQL store, a validated FastAPI service with a WebSocket replay, and a React dashboard, all containerised, tested at 93.8% coverage, and deployed on Netlify and Render. When real data broke my assumptions, for example the free database cutting long transactions and qualifying having no tyre wear, I fixed the cause and labelled the limits honestly instead of faking results."
