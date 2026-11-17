# F1 Telemetry Analytics Platform: Project Walkthrough

A plain-language guide to what the project is, the technology behind it and how data flows from the source to the screen. Written so it can be explained in an interview.

## 1. The one-minute pitch

I built a full-stack data platform for Formula 1 timing and telemetry. It downloads real race and qualifying data, cleans and aligns it so two laps can be compared fairly, stores it in a database, serves it through an API and shows it in an interactive web dashboard. It also fits statistical models, for example how fast tyres wear in a race, and replays a session on a track map. It is live on the web.

- Dashboard: https://f1telemetryanalyticsplatform.netlify.app
- API docs: https://f1tel-api.onrender.com/docs
- Source: https://github.com/BondapalliPraneeth/F1-Telemetry-Analytics-Platform

## 2. Tech stack

| Layer | Technology | Why it was used |
|---|---|---|
| Data sources | FastF1 (timing and telemetry), Jolpica (results, pit stops), OpenF1 (cross-check) | Public F1 data; three sources let me verify one against another |
| Language | Python 3.11 | Strong data ecosystem |
| Data processing | pandas, NumPy, SciPy, PyArrow | Cleaning, interpolation, regression with confidence intervals, Parquet files |
| Ingestion CLI | Typer (`f1tel` command), httpx and requests with retries | Repeatable command-line tool, safe to re-run |
| Storage (files) | Parquet with SHA-256 manifests | Fast columnar cache; checksums detect corrupt or partial files |
| Database | PostgreSQL 16, SQLAlchemy 2, Alembic migrations, psycopg 3 | Reliable relational store; the telemetry table is partitioned per session |
| API | FastAPI, Uvicorn, Pydantic | Typed, validated, self-documenting REST API plus a WebSocket |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, Plotly | Interactive charts and a typed UI |
| Testing | pytest, pytest-cov, Hypothesis, ruff | Unit and property tests, 80% coverage gate, linting |
| Packaging | Docker, Docker Compose | One command runs database, API and dashboard locally |
| CI | GitHub Actions (tests on every push, nightly real-data smoke test) | Catches regressions automatically |
| Hosting | Netlify (frontend), Render (API container and managed PostgreSQL) | Free-tier cloud deployment |

## 3. End-to-end workflow

1. **Ingest.** The `f1tel ingest` command pulls a session from FastF1 (needs an F1 account) and from Jolpica and OpenF1. It saves one Parquet file per lap plus a lap table, and a manifest of SHA-256 checksums. Re-running skips intact sessions and repairs broken ones. Network calls retry with exponential backoff.
2. **Align.** Each lap is a different length in time and slightly different in distance. I resample every lap onto a fixed 10 m distance grid, so lap A and lap B can be compared at the same point on the track. The comparison gives a delta-time trace (positive means driver B is slower there) and 20 mini-sectors showing where time is won or lost.
3. **Load.** `f1tel load-db` writes the aligned data into PostgreSQL. Telemetry goes in short, retried chunks, and a failed load removes the half-loaded session, so the database never holds partial data. Running it again replaces the old copy.
4. **Analyse.**
   - *Race:* tyre degradation. Remove pit, safety-car and first laps, apply an approximate fuel correction, fit a linear regression and report the slope (seconds lost per lap) with a confidence interval.
   - *Qualifying:* runs are too short for tyre wear, so the dashboard shows a clearly labelled session lap-time trend over push laps (it reflects track evolution, not tyre wear).
5. **Serve.** FastAPI exposes endpoints for sessions, drivers, laps, stints, comparison, degradation and the qualifying trend, plus a WebSocket that streams replay frames. Inputs are validated, errors map to proper status codes (422, 404, 503) and responses carry security headers.
6. **Show.** The React dashboard has tabs for lap comparison (speed, throttle, brake overlay, delta trace with corner markers, sector tables), tyre degradation or qualifying trend, and a session replay with a live leaderboard that shows driver and team names.
7. **Deploy and monitor.** Netlify builds the frontend, Render runs the API container and the database. GitHub Actions runs tests on each push and a nightly smoke test against real data.

```
FastF1 / Jolpica / OpenF1
        |  (f1tel ingest)
   Parquet files + checksums
        |  (10 m distance alignment)
   PostgreSQL (partitioned telemetry)
        |  (FastAPI: REST + WebSocket)
   React dashboard (Plotly charts, replay)
```

## 4. Engineering decisions worth mentioning

- **Idempotent, verifiable ingestion:** safe to re-run; checksums detect corruption.
- **Fair comparison by distance, not time:** the core of the telemetry maths. I checked it against an independent NumPy calculation on real Monza data and the results matched exactly.
- **Honest statistics:** confidence intervals, an approximate fuel correction that is documented as approximate, and no fabricated numbers. When qualifying data could not support a tyre-wear fit, I labelled a different metric instead of faking one.
- **Resilient loading:** found that Render's free database cuts long transactions, so I changed loading to short retried transactions with cleanup on failure, and added tests for it.
- **Testing against known truth:** a synthetic session with a planted tyre slope, fuel effect, safety car and pit stop lets tests confirm the maths recovers the answer. 83 tests pass, 93.8% coverage.
- **Security and audit:** secrets are never stored in the repo (scanned history is clean), CORS is limited to the dashboard origin, `npm audit` shows 0 vulnerabilities, integer inputs are bounded, and a full audit report documents what was and was not verified.

## 5. Real numbers (measured)

- 5 sessions loaded: 2024 Monza Q and R, Bahrain R, Monaco Q, Silverstone R.
- 2,181,236 raw telemetry samples across 3,133 laps; about 1.7 million resampled rows in the database copy.
- Loading a session from Parquet takes 0.21 s versus 49.5 s from a cold FastF1 load (about 235 times faster).
- Lap comparison API answers in about 0.5 s (68 kB, 25 kB compressed).

## 6. Honest limitations (good to say proactively)

- Fuel correction is a fixed approximation, and the degradation slope also absorbs track evolution and traffic.
- Replay uses stored accurate laps only, so the leaderboard is approximate, not official.
- The free Render tier sleeps when idle, so the first request can be slow.
- Browser end-to-end tests are not yet written.

## 7. Likely interview questions

- *Why align by distance?* Two laps take different times, so comparing at equal timestamps puts cars at different places on the track.
- *Why Parquet and a database?* Parquet is the cheap, verifiable cache of raw data; PostgreSQL gives fast, queryable, partitioned access for the API.
- *What went wrong and how did you fix it?* The managed database dropped long transactions; I switched to chunked, retried loading with cleanup.
- *How do you know the numbers are right?* Independent recalculation, sector sums that equal lap time, range and integrity checks, and tests with planted ground truth.
- *What would you do next?* Browser end-to-end tests, code-splitting the frontend bundle, and an approved reload of production data.
