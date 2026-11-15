import { useEffect, useState } from "react";

export interface Async<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/** Run an async loader whenever `deps` change; ignores stale responses. Idle when `enabled` is false. */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[], enabled = true): Async<T> {
  const [state, setState] = useState<Async<T>>({ data: null, loading: enabled, error: null });
  useEffect(() => {
    if (!enabled) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let live = true;
    setState((s) => ({ data: s.data, loading: true, error: null }));
    loader().then(
      (data) => live && setState({ data, loading: false, error: null }),
      (e: unknown) =>
        live && setState({ data: null, loading: false, error: e instanceof Error ? e.message : String(e) }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled]);
  return state;
}

export const fmt = (v: number | null | undefined, digits = 3): string =>
  v === null || v === undefined ? "–" : v.toFixed(digits);

export function lapTime(s: number | null): string {
  if (s === null) return "–";
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(3).padStart(6, "0")}`;
}
