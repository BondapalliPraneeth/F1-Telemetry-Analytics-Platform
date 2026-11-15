"""FastAPI application: sessions, laps, lap comparison, degradation and replay."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from f1tel import __version__
from f1tel.align import (
    AlignmentError,
    ResampledLap,
    delta_time,
    mini_sectors,
)
from f1tel.config import get_settings
from f1tel.db import (
    DriverRow,
    LapRow,
    SessionRow,
    StintRow,
    TelemetryResampled,
)
from f1tel.degradation import InsufficientDataError, analyze_stint
from f1tel.deps import get_db
from f1tel.replay import router as replay_router
from f1tel.schemas import (
    BandPoint,
    CleaningOut,
    CompareOut,
    CornerOut,
    DegradationOut,
    DegradationPoint,
    DriverOut,
    FitOut,
    HealthOut,
    LapOut,
    MiniSectorOut,
    SectorOut,
    SessionOut,
    StintOut,
    TraceOut,
)

DEGRADATION_NOTE = (
    "Fuel correction is an approximation: corrected = lap_time + fuel_effect_s_per_lap * "
    "(lap_number - 1). The slope also absorbs track evolution, traffic and driver pace."
)


DB = Annotated[Session, Depends(get_db)]


def _num(values: object) -> list[float | None]:
    """Array to JSON-safe list (NaN/inf become ``None``)."""
    arr = np.asarray(values, dtype=float)
    return [None if not np.isfinite(v) else float(v) for v in arr]


def _opt(value: object) -> float | None:
    return None if value is None or not np.isfinite(float(value)) else float(value)


def _get_session(db: Session, session_id: int) -> SessionRow:
    row = db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(404, f"session {session_id} not found")
    return row


def _get_lap(db: Session, session_id: int, driver: str, lap: int) -> LapRow:
    row = db.scalars(
        select(LapRow).where(
            LapRow.session_id == session_id, LapRow.driver == driver, LapRow.lap_number == lap
        )
    ).first()
    if row is None:
        raise HTTPException(404, f"lap {lap} of {driver} not found in session {session_id}")
    return row


def _fastest_lap(db: Session, session_id: int, driver: str) -> int:
    row = db.scalars(
        select(LapRow)
        .where(
            LapRow.session_id == session_id,
            LapRow.driver == driver,
            LapRow.has_telemetry.is_(True),
            LapRow.lap_time_s.is_not(None),
        )
        .order_by(LapRow.lap_time_s)
    ).first()
    if row is None:
        raise HTTPException(404, f"{driver} has no laps with telemetry in session {session_id}")
    return row.lap_number


def _resampled(
    db: Session, session_id: int, driver: str, lap: int
) -> tuple[ResampledLap, pd.DataFrame]:
    q = (
        select(
            TelemetryResampled.distance_m,
            TelemetryResampled.time_s,
            TelemetryResampled.speed,
            TelemetryResampled.throttle,
            TelemetryResampled.brake,
            TelemetryResampled.gear,
        )
        .where(
            TelemetryResampled.session_id == session_id,
            TelemetryResampled.driver == driver,
            TelemetryResampled.lap_number == lap,
        )
        .order_by(TelemetryResampled.distance_m)
    )
    df = pd.DataFrame(
        db.execute(q).all(), columns=["distance_m", "time_s", "speed", "throttle", "brake", "gear"]
    )
    if len(df) < 2:
        raise HTTPException(404, f"no telemetry for {driver} lap {lap} in session {session_id}")
    d = df["distance_m"].to_numpy(dtype=float)
    step = float(np.median(np.diff(d)))
    lap_obj = ResampledLap(distance=d, time=df["time_s"].to_numpy(dtype=float), step=step)
    return lap_obj, df


def _trace(driver: str, lap_row: LapRow, df: pd.DataFrame, n: int) -> TraceOut:
    cut = df.iloc[:n]
    return TraceOut(
        driver=driver,
        lap_number=lap_row.lap_number,
        lap_time_s=lap_row.lap_time_s,
        time_s=_num(cut["time_s"]),
        speed=_num(cut["speed"]),
        throttle=_num(cut["throttle"]),
        brake=_num(cut["brake"]),
        gear=_num(cut["gear"]),
    )


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(
        title="F1 Telemetry Analytics API",
        version=__version__,
        description=(
            "Unofficial project built on public data. F1 and related marks belong to their owners. "
            "`delta = time_B - time_A`: positive means B is slower at that distance."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(replay_router)

    @app.exception_handler(SQLAlchemyError)
    async def db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})

    @app.exception_handler(AlignmentError)
    async def align_error(_: Request, exc: AlignmentError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(InsufficientDataError)
    async def insufficient(_: Request, exc: InsufficientDataError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthOut, tags=["meta"])
    def health(db: DB) -> HealthOut:
        """Report API and database status (503 if the database is unreachable)."""
        db.execute(text("SELECT 1"))
        return HealthOut(status="ok", database="ok", version=__version__)

    @app.get("/sessions", response_model=list[SessionOut], tags=["sessions"])
    def sessions(db: DB) -> list[SessionOut]:
        """List ingested sessions, newest season first."""
        rows = db.scalars(
            select(SessionRow).order_by(
                SessionRow.year.desc(), SessionRow.round, SessionRow.session_type
            )
        ).all()
        return [
            SessionOut(
                id=r.id,
                year=r.year,
                round=r.round,
                event=r.event,
                session_type=r.session_type,
                location=r.location,
                n_laps=r.n_laps,
            )
            for r in rows
        ]

    @app.get("/drivers", response_model=list[DriverOut], tags=["sessions"])
    def drivers(
        db: DB, session: Annotated[int, Query(description="session id")]
    ) -> list[DriverOut]:
        """Drivers in a session."""
        _get_session(db, session)
        rows = db.scalars(
            select(DriverRow).where(DriverRow.session_id == session).order_by(DriverRow.code)
        )
        return [DriverOut(code=r.code, number=r.number, team=r.team) for r in rows]

    @app.get("/laps", response_model=list[LapOut], tags=["laps"])
    def laps(
        db: DB,
        session: Annotated[int, Query(description="session id")],
        driver: Annotated[
            str, Query(min_length=2, max_length=8, description="driver code, e.g. VER")
        ],
        only_with_telemetry: bool = False,
    ) -> list[LapOut]:
        """Laps of one driver in a session."""
        _get_session(db, session)
        q = select(LapRow).where(LapRow.session_id == session, LapRow.driver == driver.upper())
        if only_with_telemetry:
            q = q.where(LapRow.has_telemetry.is_(True))
        rows = db.scalars(q.order_by(LapRow.lap_number)).all()
        return [
            LapOut(
                driver=r.driver,
                lap_number=r.lap_number,
                lap_time_s=r.lap_time_s,
                s1_s=r.s1_s,
                s2_s=r.s2_s,
                s3_s=r.s3_s,
                stint=r.stint,
                compound=r.compound,
                tyre_life=r.tyre_life,
                track_status=r.track_status,
                is_accurate=r.is_accurate,
                pit_in=r.pit_in,
                pit_out=r.pit_out,
                has_telemetry=r.has_telemetry,
            )
            for r in rows
        ]

    @app.get("/stints", response_model=list[StintOut], tags=["laps"])
    def stints(
        db: DB,
        session: Annotated[int, Query()],
        driver: Annotated[str, Query(min_length=2, max_length=8)],
    ) -> list[StintOut]:
        """Tyre stints of one driver."""
        _get_session(db, session)
        rows = db.scalars(
            select(StintRow)
            .where(StintRow.session_id == session, StintRow.driver == driver.upper())
            .order_by(StintRow.stint_number)
        )
        return [
            StintOut(
                driver=r.driver,
                stint_number=r.stint_number,
                compound=r.compound,
                lap_start=r.lap_start,
                lap_end=r.lap_end,
                n_laps=r.n_laps,
            )
            for r in rows
        ]

    @app.get("/compare", response_model=CompareOut, tags=["analysis"])
    def compare(
        db: DB,
        session: Annotated[int, Query(description="session id")],
        a: Annotated[str, Query(min_length=2, max_length=8, description="driver A code")],
        b: Annotated[str, Query(min_length=2, max_length=8, description="driver B code")],
        lap_a: Annotated[int | None, Query(ge=1, description="default: A's fastest lap")] = None,
        lap_b: Annotated[int | None, Query(ge=1, description="default: B's fastest lap")] = None,
        mini: Annotated[int, Query(ge=1, le=60, description="number of mini-sectors")] = 20,
    ) -> CompareOut:
        """Compare two laps by distance: speed/throttle/brake traces, delta and sector tables."""
        sess = _get_session(db, session)
        a, b = a.upper(), b.upper()
        lap_a = lap_a or _fastest_lap(db, session, a)
        lap_b = lap_b or _fastest_lap(db, session, b)
        row_a, row_b = _get_lap(db, session, a, lap_a), _get_lap(db, session, b, lap_b)
        res_a, df_a = _resampled(db, session, a, lap_a)
        res_b, df_b = _resampled(db, session, b, lap_b)
        res = delta_time(res_a, res_b)
        n = len(res.distance)

        trimmed_a = ResampledLap(res_a.distance[:n], res_a.time[:n], res_a.step)
        trimmed_b = ResampledLap(res_b.distance[:n], res_b.time[:n], res_b.step)
        minis = [
            MiniSectorOut(
                index=i,
                start_m=s0,
                end_m=s1,
                a_s=ta,
                b_s=tb,
                delta_s=tb - ta,
            )
            for i, ((s0, s1, ta), (_, _, tb)) in enumerate(
                zip(mini_sectors(trimmed_a, mini), mini_sectors(trimmed_b, mini), strict=True)
            )
        ]
        sectors = []
        for name, col in (("S1", "s1_s"), ("S2", "s2_s"), ("S3", "s3_s"), ("Lap", "lap_time_s")):
            ta, tb = _opt(getattr(row_a, col)), _opt(getattr(row_b, col))
            sectors.append(
                SectorOut(
                    name=name, a_s=ta, b_s=tb, delta_s=None if ta is None or tb is None else tb - ta
                )
            )
        corners = [CornerOut(**c) for c in (sess.corners or [])]
        return CompareOut(
            session_id=session,
            step_m=res_a.step,
            distance=_num(res.distance),  # type: ignore[arg-type]
            delta=_num(res.delta),
            a=_trace(a, row_a, df_a, n),
            b=_trace(b, row_b, df_b, n),
            corners=corners,
            sectors=sectors,
            mini_sectors=minis,
            final_delta_s=float(res.delta[-1]),
        )

    @app.get("/degradation", response_model=DegradationOut, tags=["analysis"])
    def degradation(
        db: DB,
        session: Annotated[int, Query(description="session id")],
        driver: Annotated[str, Query(min_length=2, max_length=8)],
        stint: Annotated[int, Query(ge=1, description="stint number")],
        fuel_effect_s_per_lap: Annotated[
            float | None, Query(ge=0, le=0.2, description="default from server config")
        ] = None,
        confidence: Annotated[float, Query(gt=0.5, lt=0.999)] = 0.95,
    ) -> DegradationOut:
        """Fuel-corrected tyre degradation for a stint, with a confidence interval on the slope."""
        _get_session(db, session)
        driver = driver.upper()
        fuel = (
            get_settings().fuel_effect_s_per_lap
            if fuel_effect_s_per_lap is None
            else fuel_effect_s_per_lap
        )
        rows = db.scalars(
            select(LapRow)
            .where(LapRow.session_id == session, LapRow.driver == driver, LapRow.stint == stint)
            .order_by(LapRow.lap_number)
        ).all()
        if not rows:
            raise HTTPException(404, f"no laps for {driver} stint {stint} in session {session}")
        frame = pd.DataFrame(
            [
                {
                    "lap_number": r.lap_number,
                    "lap_time_s": r.lap_time_s,
                    "pit_in": r.pit_in,
                    "pit_out": r.pit_out,
                    "track_status": r.track_status,
                    "tyre_life": r.tyre_life,
                }
                for r in rows
            ]
        )
        result = analyze_stint(frame, fuel, confidence)
        pts = result.points
        xs = np.linspace(pts["tyre_age"].min(), pts["tyre_age"].max(), 25)
        fit_y, lo, hi = result.fit.band(xs)
        f = result.fit
        return DegradationOut(
            session_id=session,
            driver=driver,
            stint=stint,
            compound=rows[0].compound,
            fuel_effect_s_per_lap=fuel,
            note=DEGRADATION_NOTE,
            points=[
                DegradationPoint(
                    lap_number=int(r.lap_number),
                    tyre_age=float(r.tyre_age),
                    lap_time_s=float(r.lap_time_s),
                    corrected_s=float(r.corrected_s),
                )
                for r in pts.itertuples()
            ],
            fit=FitOut(
                slope=f.slope,
                intercept=f.intercept,
                stderr=f.stderr,
                ci_low=f.ci_low,
                ci_high=f.ci_high,
                r2=f.r2,
                n=f.n,
                confidence=f.confidence,
            ),
            band=[
                BandPoint(tyre_age=float(x), fit=float(y), low=float(lw), high=float(h))
                for x, y, lw, h in zip(xs, fit_y, lo, hi, strict=True)
            ],
            cleaning=CleaningOut(**asdict(result.cleaning)),
        )

    return app


app = create_app()
