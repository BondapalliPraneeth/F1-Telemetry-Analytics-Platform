import type { Compare, Degradation, Driver, Lap, Session, Stint } from "./types";

const BASE: string = import.meta.env.VITE_API_URL ?? "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Params = Record<string, string | number | undefined>;

async function get<T>(path: string, params: Params = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}?${qs}`);
  } catch {
    throw new ApiError(0, "Cannot reach the API. Is the backend running?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  sessions: () => get<Session[]>("/sessions"),
  drivers: (session: number) => get<Driver[]>("/drivers", { session }),
  laps: (session: number, driver: string) => get<Lap[]>("/laps", { session, driver }),
  stints: (session: number, driver: string) => get<Stint[]>("/stints", { session, driver }),
  compare: (session: number, a: string, b: string, lapA?: number, lapB?: number) =>
    get<Compare>("/compare", { session, a, b, lap_a: lapA, lap_b: lapB }),
  degradation: (session: number, driver: string, stint: number, fuel?: number) =>
    get<Degradation>("/degradation", { session, driver, stint, fuel_effect_s_per_lap: fuel }),
};

/** WebSocket URL for replay (works behind the /api proxy and with an absolute VITE_API_URL). */
export function replayUrl(session: number, speed: number): string {
  const base = BASE.startsWith("http")
    ? BASE.replace(/^http/, "ws")
    : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${BASE}`;
  return `${base}/ws/replay?session=${session}&speed=${speed}`;
}
