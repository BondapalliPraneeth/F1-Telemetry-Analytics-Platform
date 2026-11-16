import { useCallback, useEffect, useRef, useState } from "react";
import { replayUrl } from "../api";
import type { ReplayCar, ReplayMsg } from "../types";
import Plot from "./Plot";
import { ErrorBox } from "./Status";

const SPEEDS = [1, 2, 5, 10, 20];
const PALETTE = ["#38bdf8", "#f59e0b", "#34d399", "#f472b6", "#a78bfa", "#facc15", "#fb7185", "#22d3ee"];

type Conn = "idle" | "connecting" | "open" | "ended";

export default function ReplayPanel({ session, sessionType }: { session: number; sessionType: string }) {
  const ws = useRef<WebSocket | null>(null);
  const [conn, setConn] = useState<Conn>("idle");
  const [error, setError] = useState<string | null>(null);
  const [track, setTrack] = useState<{ x: number[]; y: number[] }>({ x: [], y: [] });
  const [drivers, setDrivers] = useState<string[]>([]);
  const [teams, setTeams] = useState<Record<string, string>>({});
  const [cars, setCars] = useState<ReplayCar[]>([]);
  const [range, setRange] = useState<[number, number]>([0, 1]);
  const [t, setT] = useState(0);
  const [speed, setSpeed] = useState(5);
  const [playing, setPlaying] = useState(true);

  const close = useCallback(() => {
    ws.current?.close();
    ws.current = null;
  }, []);

  const start = useCallback(() => {
    close();
    setError(null);
    setConn("connecting");
    setPlaying(true);
    const sock = new WebSocket(replayUrl(session, speed));
    ws.current = sock;
    sock.onmessage = (ev) => {
      const m = JSON.parse(ev.data) as ReplayMsg;
      if (m.type === "hello") {
        setConn("open");
        setTrack(m.track);
        setDrivers(m.drivers);
        setTeams(m.teams ?? {});
        setRange([m.t0, m.t1]);
        setT(m.t0);
      } else if (m.type === "frame") {
        setT(m.t);
        setCars(m.cars);
        setPlaying(m.playing);
      } else if (m.type === "end") {
        setConn("ended");
      } else if (m.type === "error") {
        setError(m.detail);
        setConn("idle");
      }
    };
    sock.onerror = () => {
      setError("Replay connection failed. Is the API running?");
      setConn("idle");
    };
    sock.onclose = () => setConn((c) => (c === "open" || c === "connecting" ? "ended" : c));
  }, [close, session, speed]);

  useEffect(() => {
    close();
    setConn("idle");
    setCars([]);
    return close;
  }, [session, close]);

  const send = (msg: object) => ws.current?.readyState === WebSocket.OPEN && ws.current.send(JSON.stringify(msg));
  const colorOf = (d: string) => PALETTE[Math.max(0, drivers.indexOf(d)) % PALETTE.length];
  const isRace = sessionType === "R" || sessionType === "S";
  // Race order: most laps first, then furthest into the current lap. Cars whose lap is not stored are listed last.
  const onTrack = [...cars].sort((a, b) =>
    isRace ? b.lap - a.lap || (b.distance_m ?? 0) - (a.distance_m ?? 0) : a.driver.localeCompare(b.driver),
  );
  const offTrack = drivers.filter((d) => !cars.some((c) => c.driver === d));
  const live = conn === "open";
  const elapsed = Math.max(0, t - range[0]);
  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  return (
    <div className="grid gap-4">
      <div className="card flex flex-wrap items-end gap-3">
        <button className="btn" onClick={start} disabled={conn === "connecting"}>
          {conn === "idle" || conn === "ended" ? "Start replay" : "Restart"}
        </button>
        <button
          className="btn-ghost"
          disabled={!live}
          onClick={() => send({ action: playing ? "pause" : "play" })}
        >
          {playing ? "Pause" : "Play"}
        </button>
        <label className="block">
          <span className="label">Speed</span>
          <select
            className="input"
            value={speed}
            onChange={(e) => {
              const v = Number(e.target.value);
              setSpeed(v);
              send({ action: "speed", value: v });
            }}
          >
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}×
              </option>
            ))}
          </select>
        </label>
        <label className="min-w-48 flex-1">
          <span className="label">
            Session time {mmss(elapsed)} / {mmss(range[1] - range[0])}
          </span>
          <input
            className="w-full"
            type="range"
            min={range[0]}
            max={range[1]}
            step={1}
            value={t}
            disabled={!live}
            onChange={(e) => send({ action: "seek", t: Number(e.target.value) })}
          />
        </label>
      </div>
      {error && <ErrorBox error={error} />}
      {conn === "connecting" && <p className="animate-pulse text-sm text-zinc-400">Connecting…</p>}
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <section className="card" aria-label="Track map">
        <div className="h-[28rem]">
          <Plot
            data={[
              {
                x: track.x,
                y: track.y,
                type: "scatter",
                mode: "lines",
                line: { color: "#3f3f46", width: 6 },
                hoverinfo: "skip",
                showlegend: false,
              },
              ...cars.map((c) => ({
                x: [c.x],
                y: [c.y],
                type: "scatter",
                mode: "markers+text",
                text: [c.driver],
                textposition: "top center",
                marker: { color: colorOf(c.driver), size: 11 },
                name: c.driver,
                hovertemplate: `${c.driver}${teams[c.driver] ? ` (${teams[c.driver]})` : ""} · lap ${c.lap} · ${c.speed === null ? "–" : c.speed.toFixed(0)} km/h<extra></extra>`,
              })),
            ]}
            layout={{
              xaxis: { visible: false, scaleanchor: "y" },
              yaxis: { visible: false },
              showlegend: false,
              margin: { l: 8, r: 8, t: 8, b: 8 },
              uirevision: "track",
            }}
          />
        </div>
        {conn === "idle" && !error && (
          <p className="mt-2 text-sm text-zinc-400">Press “Start replay” to stream car positions from the stored telemetry.</p>
        )}
        {conn === "ended" && <p className="mt-2 text-sm text-zinc-400">Replay finished. Start again to rewatch.</p>}
        <p className="mt-2 text-xs text-zinc-500">
          Replay uses stored accurate laps only, so a car disappears while it is on a lap that was not ingested.
        </p>
      </section>
      <section className="card" aria-label="Leaderboard">
        <h2 className="label mb-2">{isRace ? "Race order" : "Cars on track"}</h2>
        {drivers.length === 0 && <p className="text-sm text-zinc-400">Start the replay to see the order.</p>}
        <ol className="grid gap-1 text-sm">
          {onTrack.map((c, i) => (
            <li key={c.driver} className="flex items-center gap-2 tabular-nums">
              <span className="w-5 text-right text-zinc-500">{isRace ? i + 1 : ""}</span>
              <span className="h-3 w-1 rounded" style={{ background: colorOf(c.driver) }} />
              <span className="w-10 font-semibold">{c.driver}</span>
              <span className="min-w-0 flex-1 truncate text-zinc-400">{teams[c.driver] ?? ""}</span>
              <span className="text-xs text-zinc-400">
                L{c.lap} · {c.speed === null ? "–" : c.speed.toFixed(0)}
              </span>
            </li>
          ))}
          {offTrack.map((d) => (
            <li key={d} className="flex items-center gap-2 text-zinc-600">
              <span className="w-5" />
              <span className="h-3 w-1 rounded bg-zinc-700" />
              <span className="w-10">{d}</span>
              <span className="min-w-0 flex-1 truncate">{teams[d] ?? ""}</span>
              <span className="text-xs">no stored lap</span>
            </li>
          ))}
        </ol>
        {isRace && (
          <p className="mt-3 text-xs text-zinc-500">
            Order is by laps completed, then distance into the current lap, from the stored laps. A car on a
            lap that was not ingested drops to the bottom until its next stored lap.
          </p>
        )}
      </section>
      </div>
    </div>
  );
}
