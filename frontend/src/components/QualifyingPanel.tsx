import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { lapTime, useAsync } from "../hooks";
import type { Driver } from "../types";
import Plot, { axis } from "./Plot";
import { Empty, ErrorBox, Loading } from "./Status";

const COMPOUND_COLORS: Record<string, string> = {
  SOFT: "#ef4444",
  MEDIUM: "#facc15",
  HARD: "#e4e4e7",
  INTERMEDIATE: "#22c55e",
  WET: "#3b82f6",
};
const PUSH_LIMIT = 1.07; // laps slower than 107% of the driver's best are treated as non-push laps

/** Lap times of one driver in a qualifying session, push laps only, coloured by tyre compound. */
export default function QualifyingPanel({ session, drivers }: { session: number; drivers: Driver[] }) {
  const [driver, setDriver] = useState("");
  const [stint, setStint] = useState<number | "all">("all");
  useEffect(() => {
    setDriver(drivers[0]?.code ?? "");
  }, [drivers]);
  useEffect(() => setStint("all"), [driver, session]);

  const laps = useAsync(() => api.laps(session, driver), [session, driver], !!driver);
  const stints = useAsync(() => api.stints(session, driver), [session, driver], !!driver);

  const view = useMemo(() => {
    const timed = (laps.data ?? []).filter((l) => l.lap_time_s !== null && l.is_accurate && !l.pit_in && !l.pit_out);
    if (timed.length === 0) return null;
    const best = timed.reduce((a, b) => ((a.lap_time_s as number) <= (b.lap_time_s as number) ? a : b));
    const push = timed.filter((l) => (l.lap_time_s as number) <= (best.lap_time_s as number) * PUSH_LIMIT);
    return { best, push };
  }, [laps.data]);

  const runs = useMemo(
    () =>
      (stints.data ?? []).map((s) => {
        const mine = (view?.push ?? []).filter((l) => l.stint === s.stint_number);
        const fastest = mine.length
          ? mine.reduce((a, b) => ((a.lap_time_s as number) <= (b.lap_time_s as number) ? a : b))
          : null;
        return { ...s, pushLaps: mine.length, fastest };
      }),
    [stints.data, view],
  );
  const shown = view ? view.push.filter((l) => stint === "all" || l.stint === stint) : [];

  const compounds = view ? [...new Set(shown.map((l) => l.compound ?? "UNKNOWN"))] : [];
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
            value={stint}
            onChange={(e) => setStint(e.target.value === "all" ? "all" : Number(e.target.value))}
            disabled={!runs.length}
          >
            <option value="all">All stints</option>
            {runs.map((r) => (
              <option key={r.stint_number} value={r.stint_number}>
                {r.stint_number} · {r.compound ?? "?"} · laps {r.lap_start}–{r.lap_end}
              </option>
            ))}
          </select>
        </label>
        {view && (
          <div>
            <span className="label">Best lap</span>
            <p className="text-xl font-semibold tabular-nums">{lapTime(view.best.lap_time_s)}</p>
            <p className="text-xs text-zinc-400">
              lap {view.best.lap_number} · {view.best.compound ?? "?"}
            </p>
          </div>
        )}
      </div>
      {laps.loading && <Loading what="laps" />}
      {laps.error && <ErrorBox error={laps.error} />}
      {laps.data && !view && <Empty>No timed push laps recorded for {driver}.</Empty>}
      {view && (
        <section className="card" aria-label="Qualifying lap times">
          <div className="h-80">
            <Plot
              data={compounds.map((c) => {
                const pts = shown.filter((l) => (l.compound ?? "UNKNOWN") === c);
                return {
                  x: pts.map((l) => l.lap_number),
                  y: pts.map((l) => l.lap_time_s),
                  text: pts.map((l) => `Lap ${l.lap_number} · ${lapTime(l.lap_time_s)}`),
                  hoverinfo: "text",
                  type: "scatter",
                  mode: "markers",
                  marker: {
                    color: COMPOUND_COLORS[c] ?? "#a1a1aa",
                    size: pts.map((l) => (l.lap_number === view.best.lap_number ? 13 : 8)),
                    line: { color: "#18181b", width: 1 },
                  },
                  name: c,
                };
              })}
              layout={{ xaxis: axis("Lap number"), yaxis: axis("Lap time (s)") }}
            />
          </div>
        </section>
      )}
      {runs.length > 0 && (
        <section className="card overflow-x-auto" aria-label="Stints">
          <table className="w-full text-left text-sm tabular-nums">
            <thead className="text-xs text-zinc-400">
              <tr>
                <th className="pb-2 pr-4 font-medium">Stint</th>
                <th className="pb-2 pr-4 font-medium">Tyre</th>
                <th className="pb-2 pr-4 font-medium">Laps</th>
                <th className="pb-2 pr-4 font-medium">Push laps</th>
                <th className="pb-2 font-medium">Fastest push lap</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr
                  key={r.stint_number}
                  className={`cursor-pointer border-t border-zinc-800 ${stint === r.stint_number ? "bg-zinc-800/60" : ""}`}
                  onClick={() => setStint(stint === r.stint_number ? "all" : r.stint_number)}
                >
                  <td className="py-1 pr-4">{r.stint_number}</td>
                  <td className="py-1 pr-4">
                    <span
                      className="mr-2 inline-block h-2 w-2 rounded-full"
                      style={{ background: COMPOUND_COLORS[r.compound ?? ""] ?? "#a1a1aa" }}
                    />
                    {r.compound ?? "?"}
                  </td>
                  <td className="py-1 pr-4">
                    {r.lap_start}–{r.lap_end} ({r.n_laps})
                  </td>
                  <td className="py-1 pr-4">{r.pushLaps}</td>
                  <td className="py-1">
                    {r.fastest ? `${lapTime(r.fastest.lap_time_s)} (lap ${r.fastest.lap_number})` : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <p className="text-xs text-zinc-400">
        Shows timed laps within 107% of the driver&apos;s best, excluding in and out laps. The biggest dot is
        the best lap.
      </p>
    </div>
  );
}
