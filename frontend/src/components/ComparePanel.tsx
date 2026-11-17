import { useEffect, useState } from "react";
import { api } from "../api";
import { fmt, lapTime, useAsync } from "../hooks";
import type { Compare, Driver, Lap } from "../types";
import Plot, { COLORS, axis } from "./Plot";
import { Empty, ErrorBox, Loading } from "./Status";

function LapSelect({
  label,
  laps,
  value,
  onChange,
}: {
  label: string;
  laps: Lap[];
  value: number | undefined;
  onChange: (n: number | undefined) => void;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <select
        className="input"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
      >
        <option value="">Fastest lap</option>
        {laps.map((l) => (
          <option key={l.lap_number} value={l.lap_number} disabled={!l.has_telemetry}>
            Lap {l.lap_number} · {lapTime(l.lap_time_s)}
            {l.has_telemetry ? "" : ` · ${l.pit_in ? "in lap" : l.pit_out ? "out lap" : "no telemetry"} (not comparable)`}
          </option>
        ))}
      </select>
    </label>
  );
}

function DriverSelect({
  label,
  drivers,
  value,
  onChange,
}: {
  label: string;
  drivers: Driver[];
  value: string;
  onChange: (c: string) => void;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {drivers.map((d) => (
          <option key={d.code} value={d.code}>
            {d.code} {d.team ? `· ${d.team}` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

const deltaClass = (v: number | null) =>
  v === null ? "" : v > 0 ? "text-amber-400" : v < 0 ? "text-sky-400" : "";

function Charts({ c }: { c: Compare }) {
  const x = c.distance;
  const la = `${c.a.driver} L${c.a.lap_number}`;
  const lb = `${c.b.driver} L${c.b.lap_number}`;
  const cornerShapes = c.corners.map((k) => ({
    type: "line",
    xref: "x",
    yref: "paper",
    x0: k.distance_m,
    x1: k.distance_m,
    y0: 0,
    y1: 1,
    line: { color: "#52525b", width: 1, dash: "dot" },
  }));
  const cornerLabels = c.corners.map((k) => ({
    x: k.distance_m,
    y: 1,
    xref: "x",
    yref: "paper",
    text: `T${k.number}${k.letter}`,
    showarrow: false,
    font: { size: 10, color: "#a1a1aa" },
    yanchor: "bottom",
  }));
  const line = (name: string, y: (number | null)[], color: string, extra = {}) => ({
    x,
    y,
    name,
    type: "scatter",
    mode: "lines",
    line: { color, width: 1.6 },
    ...extra,
  });
  return (
    <div className="grid grid-cols-1 gap-4">
      <section className="card" aria-label="Delta trace">
        <h3 className="mb-1 text-sm font-semibold">
          Delta ({c.b.driver} − {c.a.driver}) · positive = {c.b.driver} slower
        </h3>
        <div className="h-72">
          <Plot
            data={[
              line("Delta (s)", c.delta, COLORS.delta, { fill: "tozeroy", fillcolor: "rgba(167,139,250,0.12)" }),
            ]}
            layout={{
              xaxis: axis("Distance (m)"),
              yaxis: axis("Delta (s)"),
              shapes: cornerShapes,
              annotations: cornerLabels,
              showlegend: false,
            }}
          />
        </div>
      </section>
      <section className="card" aria-label="Speed trace">
        <h3 className="mb-1 text-sm font-semibold">Speed</h3>
        <div className="h-64">
          <Plot
            data={[line(la, c.a.speed, COLORS.a), line(lb, c.b.speed, COLORS.b)]}
            layout={{ xaxis: axis("Distance (m)"), yaxis: axis("km/h"), shapes: cornerShapes }}
          />
        </div>
      </section>
      <section className="card" aria-label="Throttle and brake">
        <h3 className="mb-1 text-sm font-semibold">Throttle (solid) &amp; brake (dashed)</h3>
        <div className="h-64">
          <Plot
            data={[
              line(`${la} throttle`, c.a.throttle, COLORS.a),
              line(`${lb} throttle`, c.b.throttle, COLORS.b),
              line(`${la} brake`, c.a.brake.map((v) => (v === null ? null : v * 100)), COLORS.a, {
                line: { color: COLORS.a, width: 1, dash: "dash" },
              }),
              line(`${lb} brake`, c.b.brake.map((v) => (v === null ? null : v * 100)), COLORS.b, {
                line: { color: COLORS.b, width: 1, dash: "dash" },
              }),
            ]}
            layout={{ xaxis: axis("Distance (m)"), yaxis: axis("%"), shapes: cornerShapes }}
          />
        </div>
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        <section className="card overflow-x-auto" aria-label="Sector times">
          <h3 className="mb-2 text-sm font-semibold">Sectors</h3>
          <table className="w-full text-sm tabular-nums">
            <thead className="text-left text-xs text-zinc-400">
              <tr>
                <th className="py-1">Sector</th>
                <th>{c.a.driver}</th>
                <th>{c.b.driver}</th>
                <th>Δ (s)</th>
              </tr>
            </thead>
            <tbody>
              {c.sectors.map((s) => (
                <tr key={s.name} className="border-t border-zinc-800">
                  <td className="py-1">{s.name}</td>
                  <td>{fmt(s.a_s)}</td>
                  <td>{fmt(s.b_s)}</td>
                  <td className={deltaClass(s.delta_s)}>{fmt(s.delta_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card overflow-x-auto" aria-label="Mini-sector times">
          <h3 className="mb-2 text-sm font-semibold">Mini-sectors ({c.mini_sectors.length})</h3>
          <div className="max-h-64 overflow-y-auto">
            <table className="w-full text-sm tabular-nums">
              <thead className="sticky top-0 bg-zinc-900 text-left text-xs text-zinc-400">
                <tr>
                  <th className="py-1">#</th>
                  <th>From–to (m)</th>
                  <th>Δ (s)</th>
                </tr>
              </thead>
              <tbody>
                {c.mini_sectors.map((m) => (
                  <tr key={m.index} className="border-t border-zinc-800">
                    <td className="py-1">{m.index + 1}</td>
                    <td>
                      {Math.round(m.start_m)}–{Math.round(m.end_m)}
                    </td>
                    <td className={deltaClass(m.delta_s)}>{fmt(m.delta_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

export default function ComparePanel({ session, drivers }: { session: number; drivers: Driver[] }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [lapA, setLapA] = useState<number | undefined>();
  const [lapB, setLapB] = useState<number | undefined>();

  useEffect(() => {
    setA(drivers[0]?.code ?? "");
    setB(drivers[1]?.code ?? drivers[0]?.code ?? "");
    setLapA(undefined);
    setLapB(undefined);
  }, [drivers]);

  const lapsA = useAsync(() => api.laps(session, a), [session, a], !!a);
  const lapsB = useAsync(() => api.laps(session, b), [session, b], !!b);
  const cmp = useAsync(() => api.compare(session, a, b, lapA, lapB), [session, a, b, lapA, lapB], !!a && !!b);

  if (drivers.length === 0) return <Empty>No drivers in this session.</Empty>;
  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="card grid grid-cols-2 gap-3 md:grid-cols-4">
        <DriverSelect label="Driver A" drivers={drivers} value={a} onChange={(v) => { setA(v); setLapA(undefined); }} />
        <LapSelect label="Lap A" laps={lapsA.data ?? []} value={lapA} onChange={setLapA} />
        <DriverSelect label="Driver B" drivers={drivers} value={b} onChange={(v) => { setB(v); setLapB(undefined); }} />
        <LapSelect label="Lap B" laps={lapsB.data ?? []} value={lapB} onChange={setLapB} />
      </div>
      {cmp.loading && <Loading what="comparison" />}
      {cmp.error && <ErrorBox error={cmp.error} />}
      {cmp.data && !cmp.loading && <Charts c={cmp.data} />}
    </div>
  );
}
