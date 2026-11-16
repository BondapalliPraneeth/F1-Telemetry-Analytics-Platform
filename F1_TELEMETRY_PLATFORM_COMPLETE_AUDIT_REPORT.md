# F1 Telemetry Analytics Platform: Audit Report

Date: 2026-11-16. Scope: backend, API, telemetry accuracy, data pipeline, database, security, performance, deployment. Every number below comes from a command run during this audit. Items that were **not** run are listed in section 11 and are not scored as passed.

## 1. Executive Summary

The platform is sound. Backend tests pass (83 passed, 1 PostgreSQL-only test skipped, 93.8% coverage, gate is 80%), lint is clean, the frontend type-checks and builds, and `npm audit` reports 0 vulnerabilities. Lap-delta output matched an independent NumPy calculation with a maximum difference of 0.0 s. The audit found and fixed three real defects (huge integer ids returned 503, no security headers, throttle values up to 104). It also found items it could not check: browser and responsive behaviour, the live Render database, OpenF1 cross-check, and replay frame rate. Fixes are in the working tree and **not yet deployed**; the user commits and pushes.

## 2. Architecture Overview

FastF1/Jolpica/OpenF1 -> idempotent ingestion CLI -> per-lap Parquet with SHA-256 manifests -> 10 m distance-grid alignment -> PostgreSQL (LIST-partitioned `telemetry_resampled`, one partition per session) -> FastAPI (REST plus `/ws/replay`) -> React 19 / Vite / Tailwind / Plotly dashboard. Frontend on Netlify, API and managed Postgres on Render (`render.yaml`, Docker).

## 3. Features Audited

Session and driver listing, laps and stints, lap comparison (delta, mini-sectors, corners), tyre degradation (race), qualifying session lap-time trend, WebSocket replay with leaderboard, ingestion and DB loading, CORS, input validation.

## 4. Issues Identified

| # | Severity | Issue | Evidence |
|---|---|---|---|
| 1 | Medium | Integer ids beyond the 32-bit range (`session=99999999999999999999`) returned `503 database unavailable` instead of a client error | Live probe against the API |
| 2 | Medium | No security headers on the API or the Netlify site (nosniff, frame options, referrer policy; Netlify already had HSTS) | `curl -I` on both |
| 3 | Low | Throttle samples up to 104 stored (valid range 0 to 100) | Range query over 1.7M local rows: max 104 |
| 4 | Low | Not-found message echoes the user-supplied driver code in JSON | Live probe; not exploitable as JSON, but unneeded |
| 5 | Low | Build tool `setuptools 65.5.0` in the venv has 3 advisories | `pip-audit`; not shipped in the runtime image path audited here |
| 6 | Info | Lap time vs telemetry duration differs by a median 0.06 s (max 0.115 s) | 927 Monza race laps |
| 7 | Info | JS bundle is 1,269 kB (432 kB gzip), single chunk | `npm run build` warning |

## 5. Fixes Implemented

- **#1**: ids and lap/stint numbers are bounded to 1..2,147,483,647 on every REST endpoint and on `/ws/replay`. Files: `src/f1tel/api.py`, `src/f1tel/replay.py`. Test: `test_huge_ids_are_validation_errors_not_db_errors`.
- **#2**: API middleware adds `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and a strict CSP (relaxed to nosniff only for `/docs`, `/redoc`, `/openapi.json`). `frontend/netlify.toml` adds nosniff, frame options, referrer policy and permissions policy. Test: `test_security_headers_present`. A frontend CSP was left out on purpose because it was not verified against Plotly in a browser.
- **#3**: throttle is clipped to 0..100 when loading into the database (`src/f1tel/dbload.py`). The added test `test_loaded_throttle_stays_within_0_100` is a range check on fixture data; the fixture contains no value above 100, so it does not prove the clip fires. Existing production rows keep their old values until sessions are reloaded.

Not fixed: #4, #5, #6, #7 (see section 11).

## 6. Telemetry Accuracy

Method: built a local SQLite copy from the raw Parquet with the project's own loader (the production database was not queried), then compared against independent calculations.

- Lap delta, 2024 Monza Q, VER vs NOR fastest laps: independent interpolation on a 10 m grid vs the live API, 576 points, **max difference 0.0 s**. Final delta -0.3717 s on both sides.
- Grid: all 92 sampled laps start at 0 m with an exact 10 m step; no non-increasing time steps.
- Sectors: for 927 Monza race laps, sector sum equals lap time (max difference 0.0 s).
- Lap time vs telemetry duration: median 0.06 s, max 0.115 s. The final delta (-0.3717 s) differs from the timing-sheet difference (-0.335 s) by 0.037 s for the same reason: telemetry duration is not identical to official timing.
- Ranges over 1,709,615 rows: speed 4.4 to 363 km/h, brake 0 to 1, gear 1 to 8, RPM 3,027 to 13,148, distance 0 to 6,160 m, zero nulls in speed, RPM and position. Throttle reached 104 (issue #3).
- Integrity: 0 orphan laps or telemetry rows, 0 duplicate laps, 0 `has_telemetry` mismatches.

Not run: the OpenF1 `crosscheck` command, tyre-degradation values against an independent fit, and a check of fuel-correction accuracy (it is an approximation by design).

## 7. End-to-End Testing

Backend API tests and replay WebSocket tests run in CI-style locally (83 passed). Live API probes: all five main endpoints returned 200 with valid payloads. **No browser end-to-end tests were run** (no Playwright), and I did not inspect the dashboard at 1920 to 360 px widths or collect console errors.

## 8. Security Findings

- Secrets scan of tracked files and git history: clean.
- `npm audit`: 0 vulnerabilities. `pip-audit`: only setuptools (issue #5).
- CORS: the Netlify origin is allowed, a foreign origin is not, GET only.
- Input handling: bad input returns 422/404/405. A SQL-injection-style string was blocked at the Cloudflare edge (403). All DB access goes through SQLAlchemy parameters.
- Headers: fixed in code (#2), not yet live.
- User-reported: the F1 account password and the Render DB password were pasted in chat earlier. Both should be rotated.

## 9. Performance Findings

Live API (warm), one request each: `/health` 0.39 s, `/sessions` 0.33 s, `/compare` 0.46 s (67.7 kB, 25.2 kB gzipped), `/degradation` 0.30 s, `/pace-trend` 0.41 s. Previously measured: 0.21 s vs 49.5 s for 2024 Monza Q loading a DB-backed session vs re-parsing from the source. The Render free tier sleeps when idle, so the first request after idle is slow (not re-measured here). Frontend bundle is 432 kB gzip in one chunk. Replay frame rate and browser load time were not measured.

## 10. Deployment Status

Frontend and API are live and healthy; five sessions are loaded. Netlify has the SPA redirect and HSTS. The fixes in this report are local only until committed and deployed.

## 11. Remaining Issues

1. Browser/responsive/console audit and Playwright tests not done.
2. Live Render database not audited directly (local replica of the same data used instead). Postgres partitions and indexes were not inspected.
3. OpenF1 crosscheck and replay frame rate not run.
4. Existing production rows still hold throttle values above 100 until a reload (a reload replaces the session copy; needs the user's approval).
5. Frontend CSP not added; bundle not code-split (Plotly dominates).
6. Not-found message still echoes the driver code; setuptools advisories remain.
7. Replay leaderboard is sparse (only stored accurate laps), the Monza GIF is a placeholder, and the README disclaimer line remains.

## 12. Health Scores

Scores are judgement based only on what was verified; unverified areas are capped.

| Area | Score /10 | Basis |
|---|---|---|
| Frontend | 6 | Builds and type-checks; browser behaviour not verified |
| Backend | 9 | 83 tests, 93.8% coverage, input hardening fixed |
| Telemetry accuracy | 8 | Independent delta check exact; OpenF1 crosscheck not run |
| Data processing | 8 | Grid, sectors, integrity checks clean; throttle clip added |
| Database | 7 | Integrity clean on a replica; production DB not inspected |
| Security | 7 | Clean scans; headers fixed but not deployed; credentials need rotation |
| Performance | 7 | Fast warm responses; cold start and frame rate unmeasured |
| Testing | 7 | Strong backend, no browser tests |
| Deployment | 8 | Live and healthy; fixes pending deploy |
| Evidence-based confidence | 7 | Gaps listed in section 11 |

## 13. Final Recommendation

Commit and deploy the fixes, then rotate both pasted credentials. Next most valuable steps: a short Playwright smoke suite with responsive checks, a read-only inspection of the Render database, and an approved reload of the five sessions so stored throttle values are clipped.
