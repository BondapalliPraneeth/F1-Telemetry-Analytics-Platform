import { useEffect, useState } from "react";
import { api } from "../api";
import { fmt, useAsync } from "../hooks";
import type { Driver } from "../types";
import Plot, { COLORS, axis } from "./Plot";
import QualifyingPanel from "./QualifyingPanel";
import { Empty, ErrorBox, Loading } from "./Status";

export default function DegradationPanel({
  session,
  sessionType,
  drivers,
  onOpenRace,
}: {
  session: number;
  sessionType: string;
  drivers: Driver[];
  onOpenRace?: () => void;
}) {
  const isRace = sessionType === "R" || sessionType === "S";
  const [driver, setDriver] = useState("");
  const [stint, setStint] = useState<number | undefined>();
  const [fuel, setFuel] = useState(0.03);

  useEffect(() => {
    setDriver(drivers[0]?.code ?? "");
  }, [drivers]);

  const stints = useAsync(() => api.stints(session, driver), [session, driver], !!driver);
  useEffect(() => {
    const longest = [...(stints.data ?? [])].sort((x, y) => y.n_laps - x.n_laps)[0];
    setStint(longest?.stint_number);
  }, [stints.data]);

  const deg = useAsync(
    () => api.degradation(session, driver, stint!, fuel),
    [session, driver, stint, fuel],
    isRace && !!driver && stint !== undefined,
  );

  const d = deg.data;
  const band = d?.band ?? [];
  const f = d?.fit;
  if (!isRace)
    return (
      <div className="grid gap-4">
        <Empty>
          Qualifying runs are too short to fit a tyre-degradation trend, so this session shows each stint&apos;s
          push laps instead.
          {onOpenRace && (
            <>
              {" "}
              <button type="button" className="text-sky-400 underline" onClick={onOpenRace}>
                See degradation in a race
              </button>
            </>
          )}
        </Empty>
        <QualifyingPanel session={session} drivers={drivers} />
      </div>
    );
  return (
    <div className="grid gap-4">
      <div className="card grid grid-cols-2 gap-3 md:grid-cols-4">
        <label className="block">
          <span className="label">Driver</span>
          <select className="input" value={driver} onChange={(e) => setDriver(e.target.value)}>
            {drivers.map((x) => (
              <option key={x.code}>{x.code}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Stint</span>
          <select
            className="input"
            value={stint ?? ""}
            onChange={(e) => setStint(Number(e.target.value))}
            disabled={!stints.data?.length}
          >
            {(stints.data ?? []).map((s) => (
              <option key={s.stint_number} value={s.stint_number}>
                {s.stint_number} · {s.compound ?? "?"} · laps {s.lap_start}–{s.lap_end}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Fuel effect (s/lap): {fuel.toFixed(3)}</span>
          <input
            className="w-full"
            type="range"
            min={0}
            max={0.1}
            step={0.005}
            value={fuel}
            onChange={(e) => setFuel(Number(e.target.value))}
          />
        </label>
      </div>
      {stints.error && <ErrorBox error={stints.error} />}
      {stints.data && stints.data.length === 0 && <Empty>No stints recorded for {driver}.</Empty>}
      {deg.loading && <Loading what="degradation" />}
      {deg.error && <ErrorBox error={deg.error} />}
      {d && f && !deg.loading && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="card">
              <span className="label">Slope</span>
              <p className="text-xl font-semibold tabular-nums">{fmt(f.slope, 3)} s/lap</p>
            </div>
            <div className="card">
              <span className="label">{Math.round(f.confidence * 100)}% CI</span>
              <p className="text-xl font-semibold tabular-nums">
                [{fmt(f.ci_low, 3)}, {fmt(f.ci_high, 3)}]
              </p>
            </div>
            <div className="card">
              <span className="label">R² / laps used</span>
              <p className="text-xl font-semibold tabular-nums">
                {fmt(f.r2, 2)} / {f.n}
              </p>
            </div>
            <div className="card">
              <span className="label">Removed laps</span>
              <p className="text-sm tabular-nums">
                pit {d.cleaning.pit_laps} · SC/VSC {d.cleaning.safety_car_laps} · lap 1 {d.cleaning.first_lap} ·
                no time {d.cleaning.missing_time}
              </p>
            </div>
          </div>
          <section className="card" aria-label="Degradation chart">
            <div className="h-80">
              <Plot
                data={[
                  {
                    x: [...band.map((p) => p.tyre_age), ...band.map((p) => p.tyre_age).reverse()],
                    y: [...band.map((p) => p.high), ...band.map((p) => p.low).reverse()],
                    type: "scatter",
                    fill: "toself",
                    fillcolor: "rgba(56,189,248,0.18)",
                    line: { width: 0 },
                    name: `${Math.round(f.confidence * 100)}% band`,
                    hoverinfo: "skip",
                  },
                  {
                    x: band.map((p) => p.tyre_age),
                    y: band.map((p) => p.fit),
                    type: "scatter",
                    mode: "lines",
                    line: { color: COLORS.a, width: 2 },
                    name: "Fit",
                  },
                  {
                    x: d.points.map((p) => p.tyre_age),
                    y: d.points.map((p) => p.corrected_s),
                    text: d.points.map((p) => `Lap ${p.lap_number}`),
                    type: "scatter",
                    mode: "markers",
                    marker: { color: COLORS.b, size: 7 },
                    name: "Fuel-corrected laps",
                  },
                ]}
                layout={{ xaxis: axis("Tyre age (laps)"), yaxis: axis("Corrected lap time (s)") }}
              />
            </div>
          </section>
          <p className="text-xs text-zinc-400">{d.note}</p>
        </>
      )}
    </div>
  );
}
