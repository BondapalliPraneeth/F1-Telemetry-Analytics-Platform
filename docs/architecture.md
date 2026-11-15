# Architecture

```
FastF1 (timing + telemetry) ─┐
Jolpica (results, pit stops) ─┼─► ingestion CLI ─► Parquet cache ─► clean + align (10 m grid)
OpenF1  (lap-time cross-check)┘     (idempotent,     data/raw/…        │
                                     checksummed)                      ▼
                                                              PostgreSQL (partitioned)
                                                                       │
                                                      FastAPI  ◄───────┘
                                                      REST + /ws/replay
                                                                       │
                                                              React + Plotly dashboard
```

## Modules (`src/f1tel`)

| Module | Responsibility |
| --- | --- |
| `loader.py` | FastF1 session loading, normalisation of laps/telemetry to project columns |
| `clients/` | Cached, retrying JSON clients for Jolpica and OpenF1 |
| `ingest.py` | Typer CLI: `ingest`, `batch`, `seed`, `load-db`, `verify`, `crosscheck` |
| `manifest.py`, `retry.py` | SHA-256 manifests, atomic writes, exponential backoff |
| `align.py` | Distance-grid resampling, delta time, mini-sectors |
| `dbload.py`, `db.py` | Parquet → PostgreSQL (SQLAlchemy 2.x), per-session partitions |
| `degradation.py` | Stint cleaning, fuel correction, OLS slope with confidence interval |
| `api.py`, `schemas.py`, `deps.py` | FastAPI app, Pydantic models, DB dependency |
| `replay.py` | WebSocket replay of stored telemetry in session-time order |

## Data layout

```
data/raw/{year}/{event}/{session}/
  laps.parquet  meta.json  weather.parquet  results.json  pitstops.json  manifest.json
  {driver}/lap_{n}.parquet
```

Each file is written to a temporary name and renamed into place. `manifest.json` stores a
SHA-256 per file and a `complete` flag, so an interrupted run is detected and repaired on the next
run, and a finished session is skipped.

## Distance alignment

1. Each lap is anchored at its first valid sample (distance 0, time 0).
2. Samples with NaN, repeated or decreasing distance, or time running backwards are dropped.
3. The lap is interpolated linearly onto `0, 10, 20, …` metres (`nGear` is rounded).
4. `delta[i] = time_B[i] - time_A[i]` on the common grid, i.e. the shorter lap. Positive means B is slower.

Because telemetry distance is integrated from speed, two laps can disagree on lap length by a few
metres; truncating to the common grid keeps the comparison honest instead of stretching one lap.

## Database

* `sessions`, `drivers`, `laps`, `stints`: ordinary tables.
* `telemetry_resampled`: `PARTITION BY LIST (session_id)` on PostgreSQL, one partition per session,
  created in the same transaction that loads the session. Reloading a session drops and recreates its
  partition, which is idempotent and avoids a large `DELETE`.
* Tests run the same models on SQLite; a PostgreSQL integration test runs in CI.

## Degradation model

Laps are removed when they are pit in/out laps, run under SC/VSC/red flag (track-status codes 4–7),
are the first race lap, or have no time. Remaining times are fuel-corrected,
`corrected = lap_time + fuel_effect * (lap_number - 1)` (default 0.03 s/lap, configurable), then
regressed on tyre age. The API returns the OLS slope, a t-based confidence interval and a confidence
band for the mean line. The fuel effect is an approximation and the slope also absorbs track
evolution, traffic and driver pace; the dashboard shows this caveat.

## Replay

`/ws/replay?session=&speed=` sends a `hello` (time range, drivers, track outline), then `frame`
messages with car positions every `TICK_S` seconds of wall time; session time advances `speed`×
faster. The client may send `play`, `pause`, `speed` and `seek` messages. Positions come from stored
accurate-lap telemetry placed at `lap_start + time_in_lap`, so cars are absent while on laps that
were not stored (in/out laps, neutralised laps).
