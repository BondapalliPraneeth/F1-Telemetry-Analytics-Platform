import { useEffect, useState } from "react";
import { api } from "./api";
import ComparePanel from "./components/ComparePanel";
import DegradationPanel from "./components/DegradationPanel";
import ReplayPanel from "./components/ReplayPanel";
import { Empty, ErrorBox, Loading } from "./components/Status";
import { useAsync } from "./hooks";

type Tab = "compare" | "degradation" | "replay";
const TABS: { id: Tab; label: string }[] = [
  { id: "compare", label: "Lap comparison" },
  { id: "degradation", label: "Tyre degradation" },
  { id: "replay", label: "Replay" },
];

export default function App() {
  const sessions = useAsync(() => api.sessions(), []);
  const [sessionId, setSessionId] = useState<number | undefined>();
  const [tab, setTab] = useState<Tab>("compare");

  useEffect(() => {
    if (sessionId === undefined && sessions.data?.length) setSessionId(sessions.data[0].id);
  }, [sessions.data, sessionId]);

  const drivers = useAsync(() => api.drivers(sessionId!), [sessionId], sessionId !== undefined);

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">F1 Telemetry Analytics</h1>
        </div>
        <label className="block min-w-64">
          <span className="label">Session</span>
          <select
            className="input"
            value={sessionId ?? ""}
            onChange={(e) => setSessionId(Number(e.target.value))}
            disabled={!sessions.data?.length}
          >
            {(sessions.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.year} {s.event} · {s.session_type}
              </option>
            ))}
          </select>
        </label>
      </header>

      {sessions.loading && <Loading what="sessions" />}
      {sessions.error && <ErrorBox error={sessions.error} />}
      {sessions.data?.length === 0 && (
        <Empty>
          No sessions ingested yet. Run <code className="rounded bg-zinc-800 px-1">f1tel seed</code> then{" "}
          <code className="rounded bg-zinc-800 px-1">f1tel load-db</code> (see the README).
        </Empty>
      )}

      {sessionId !== undefined && (
        <>
          <nav className="mb-4 flex gap-1 border-b border-zinc-800" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`-mb-px border-b-2 px-4 py-2 text-sm ${
                  tab === t.id
                    ? "border-sky-500 text-white"
                    : "border-transparent text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
          {drivers.error && <ErrorBox error={drivers.error} />}
          {drivers.loading && <Loading what="drivers" />}
          {drivers.data && tab === "compare" && <ComparePanel session={sessionId} drivers={drivers.data} />}
          {drivers.data && tab === "degradation" && <DegradationPanel
              session={sessionId}
              sessionType={sessions.data?.find((s) => s.id === sessionId)?.session_type ?? ""}
              drivers={drivers.data}
              onOpenRace={
                sessions.data?.some((s) => s.session_type === "R")
                  ? () => setSessionId(sessions.data?.find((s) => s.session_type === "R")?.id)
                  : undefined
              }
            />}
          {tab === "replay" && <ReplayPanel
              session={sessionId}
              sessionType={sessions.data?.find((s) => s.id === sessionId)?.session_type ?? ""}
            />}
        </>
      )}
    </div>
  );
}
