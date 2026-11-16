# F1 Telemetry Analytics Platform

> Unofficial project. F1 and related marks belong to their owners.

An end-to-end pipeline for Formula 1 timing and telemetry data: an idempotent ingestion CLI,
a Parquet cache, distance-aligned lap comparison, tyre-degradation analysis with confidence
intervals, a partitioned PostgreSQL store, a FastAPI service and a React dashboard with a
WebSocket replay mode.

![Monza 2024 Q delta trace](docs/monza-2024-q.gif)
<!-- Placeholder: record this GIF after seeding (see "Quick start"). -->

## What it does

* **Ingest** sessions from FastF1 (timing + telemetry), Jolpica (results, pit stops) and OpenF1
  (lap-time cross-check) into `data/raw/…` as per-lap Parquet files with SHA-256 manifests. Re-running
  skips intact sessions and repairs partial ones; network calls retry with exponential backoff.
* **Align** every lap onto a 10 m distance grid so two laps can be compared point by point, with a
  delta-time trace and 20 mini-sectors.
* **Analyse tyre degradation**: remove pit, safety-car and first laps, apply a fuel correction,
  fit a regression and report the slope with a confidence interval.
* **Serve** it all through a FastAPI app (`/sessions`, `/laps`, `/compare`, `/degradation`, `/stints`,
  `/drivers`, `/health`, `/ws/replay`).
* **Show** it in a dashboard: session browser, lap picker, speed/throttle/brake overlay, delta trace
  with corner markers, sector tables, degradation chart with a confidence band and a replay panel.

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
| Test coverage (`pytest --cov`, gate at 80%) | 93.6% (77 tests passed, 1 PostgreSQL-only test skipped locally; synthetic fixture) |
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

Not deployed by this repo; the pieces map onto common hosts:

* **API**: any container host (Fly.io, Render) using `Dockerfile.api`; set `F1TEL_DATABASE_URL`
  and `F1TEL_CORS_ORIGINS`. The container runs `alembic upgrade head` on start.
* **Database**: managed PostgreSQL 16.
* **Frontend**: static build (`npm run build`) on Vercel or similar; set `VITE_API_URL` to the API's
  public URL (WebSocket replay uses the same base).

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
