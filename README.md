# F1 Telemetry Analytics Platform

> Unofficial project. F1 and related marks belong to their owners.

An end-to-end pipeline for Formula 1 timing and telemetry data: an idempotent ingestion CLI,
a Parquet cache, distance-aligned lap comparison, tyre-degradation analysis with confidence
intervals, a partitioned PostgreSQL store, a FastAPI service and a React dashboard with a
WebSocket replay mode.

![Monza 2024 Q delta trace](docs/monza-2024-q.gif)
<!-- Placeholder: record this GIF after seeding (see "Quick start"). -->

**Live:** dashboard <https://f1telemetryanalyticsplatform.netlify.app> · API <https://f1tel-api.onrender.com/docs>
(the free Render tier sleeps when idle, so the first request can take about a minute).

## Handover: read this first if you are a new developer or an AI assistant

* **Status:** all 9 build phases are done and deployed (frontend on Netlify, API and managed PostgreSQL
  on Render via `render.yaml`). A full audit is in
  [F1_TELEMETRY_PLATFORM_COMPLETE_AUDIT_REPORT.md](F1_TELEMETRY_PLATFORM_COMPLETE_AUDIT_REPORT.md)
  (a Word copy sits next to it); its section 11 lists what is still open.
* **Data flow:** FastF1/Jolpica/OpenF1 -> `src/f1tel/ingest.py` (CLI `f1tel`) -> `data/raw/` Parquet ->
  `src/f1tel/align.py` (10 m grid) -> `src/f1tel/dbload.py` -> PostgreSQL -> `src/f1tel/api.py` ->
  `frontend/` (React + Vite + Tailwind + Plotly).
* **Where things live:** `src/f1tel/` backend (`api.py` routes, `degradation.py` regression,
  `replay.py` WebSocket, `db.py` models, `schemas.py`), `tests/` (synthetic fixture with known truths),
  `frontend/src/components/` one panel per tab, `docs/architecture.md` design notes.
* **Conventions:** `delta = time_B - time_A` (positive means B is slower). Tyre degradation is race-only;
  qualifying shows a separately labelled session lap-time trend (`/pace-trend`), which is track
  evolution, not tyre wear. Never invent or mock telemetry numbers.
* **Rules for contributors:** no secrets in the repo (F1 credentials come from `F1_USERNAME` /
  `F1_PASSWORD` in your shell; the database URL from `F1TEL_DATABASE_URL`); raw data is git-ignored; no
  F1 logos; the repository owner makes the commits and pushes; commit messages carry no tool attribution.
* **Verify a change:** `pytest --cov` (gate 80%), `ruff check .`, then in `frontend/`:
  `npx tsc --noEmit` and `npm run build`.
* **Gotchas:** Render's free Postgres drops long transactions, so `dbload.py` loads in short retried
  chunks and removes a half-loaded session on failure (a reload replaces the old copy of that session).
  Integer ids are bounded to the PostgreSQL integer range and the API sends security headers.

## What it does

* **Ingest** sessions from FastF1 (timing + telemetry), Jolpica (results, pit stops) and OpenF1
  (lap-time cross-check) into `data/raw/…` as per-lap Parquet files with SHA-256 manifests. Re-running
  skips intact sessions and repairs partial ones; network calls retry with exponential backoff.
* **Align** every lap onto a 10 m distance grid so two laps can be compared point by point, with a
  delta-time trace and 20 mini-sectors.
* **Analyse tyre degradation**: remove pit, safety-car and first laps, apply a fuel correction,
  fit a regression and report the slope with a confidence interval.
* **Qualifying trend:** for qualifying sessions, fit a labelled session lap-time trend over push laps
  (`/pace-trend`) instead of a tyre-degradation line, because qualifying runs are too short.
* **Serve** it all through a FastAPI app (`/sessions`, `/laps`, `/compare`, `/degradation`, `/pace-trend`,
  `/stints`, `/drivers`, `/health`, `/ws/replay`).
* **Show** it in a dashboard: session browser, lap picker, speed/throttle/brake overlay, delta trace
  with corner markers, sector tables, degradation chart with a confidence band (race), a qualifying
  lap-time trend, and a replay panel with a leaderboard showing driver and team names.

Design details are in [docs/architecture.md](docs/architecture.md).

## Data access (read this first)

FastF1 3.9 downloads timing data through a Formula 1 account. Create one (free account works for the
data used here) and export the credentials in **your own shell** before ingesting. They are never
stored in the repo:

```powershell
$env:F1_USERNAME = "you@example.com"
$env:F1_PASSWORD = "your-password"
```

Without them `f1tel ingest` stops with a clear message. The unit tests and the dashboard demo on
the synthetic fixture do not need credentials.

## Quick start (Windows 11, PowerShell)

Prerequisites: Python 3.11, Node 20+ (for local frontend work), Docker Desktop.

```powershell
# 1. everything in containers
docker compose up --build            # db, api (runs migrations), dashboard on http://localhost:8080

# 2. load the 5 demo sessions (needs F1_USERNAME / F1_PASSWORD, see above)
docker compose --profile seed run --rm seed
```

Open <http://localhost:8080>, choose **2024 Italian Grand Prix · Q** and compare two drivers on the
*Lap comparison* tab. The API docs are at <http://localhost:8000/docs>.

### Local development

```powershell
.\tasks.ps1 setup                 # venv at .venv, editable install with dev tools
.\.venv\Scripts\Activate.ps1
copy .env.example .env            # adjust if needed
docker compose up -d db           # just PostgreSQL
alembic upgrade head
f1tel seed                        # ingest + load the 5 sessions
.\tasks.ps1 api                   # API on :8000

cd frontend; npm install; npm run dev   # dashboard on :5173, proxies /api to :8000
```

Other commands:

```powershell
f1tel ingest --year 2024 --event Monza --session Q
f1tel batch                       # rounds 1-8 of 2023-2025 races (24 sessions)
f1tel load-db                     # load everything already in data/raw into PostgreSQL
f1tel verify                      # re-check every checksum
f1tel crosscheck --year 2024 --event Monza --session R
.\tasks.ps1 test | lint | fmt | measure
```

## Measured numbers

`python scripts/measure.py` prints these from your own machine; nothing here is estimated.
Entries say **not yet measured** until the script has been run against real ingested data.

| Metric | Value |
| --- | --- |
| Test coverage (`pytest --cov`, gate at 80%) | 93.8% (83 tests passed, 1 PostgreSQL-only test skipped locally; synthetic fixture) |
| Telemetry samples ingested | 2,181,236 raw samples across the 5 seed sessions (3,133 laps) |
| Parquet reload vs cold FastF1 load | 0.21 s vs 49.5 s for 2024 Monza Q (about 235x faster; median of 5 reloads) |
| Sessions in the seed set | 5 (2024 Monza Q, Monza R, Bahrain R, Monaco Q, Silverstone R) |

## Testing

```powershell
pytest --cov
ruff check .
```

Tests use a deterministic synthetic session (`tests/fixtures`) with known truths (a planted tyre
slope, fuel effect, safety-car lap and pit stop), so degradation and alignment results are checked
against ground truth, plus property tests for the alignment. A PostgreSQL integration test runs when
`F1TEL_TEST_DATABASE_URL` is set (CI sets it). The real-data path (FastF1 loading) is covered by
a nightly CI smoke job rather than unit tests.

## Deployment notes

Currently deployed as follows (the pieces also map onto other hosts):

* **API**: Render web service from `Dockerfile.api` (see `render.yaml`); set `F1TEL_DATABASE_URL` and
  `F1TEL_CORS_ORIGINS` (the Netlify origin). The container runs `alembic upgrade head` on start.
* **Database**: managed PostgreSQL on Render. Load sessions with `f1tel load-db` pointed at it via
  `F1TEL_DATABASE_URL` in your own shell.
* **Frontend**: Netlify static build (`frontend/netlify.toml`, SPA redirect and security headers); set
  `VITE_API_URL` to the API's public URL at build time (WebSocket replay uses the same base).

## Data sources and licences

* [FastF1](https://github.com/theOehrly/Fastf1) (MIT) for timing and telemetry; the underlying data
  comes from Formula 1's live timing service.
* [Jolpica F1](https://github.com/jolpica/jolpica-f1) (Ergast-compatible) for results and pit stops.
* [OpenF1](https://openf1.org) for lap-time cross-checks.

Raw data is never committed (`data/` is git-ignored). Check each provider's terms before
redistributing data. No F1 logos or trademarks are used.

## Limitations

* The fuel correction is a fixed per-lap approximation (default 0.03 s/lap, configurable). The
  degradation slope also absorbs track evolution, traffic and driver behaviour.
* Replay shows stored accurate laps only; cars vanish on laps that were not ingested, and timestamps
  are `lap_start + time in lap`.
* Telemetry distance is integrated from speed, so lap lengths differ by a few metres between laps; the
  delta uses the common (shorter) grid.
* FastF1 loading and the PostgreSQL partitioning are exercised in CI and the nightly job, not in the
  default local unit-test run.

## Licence

MIT, see [LICENSE](LICENSE).
