import type { ReactNode } from "react";

export function Loading({ what }: { what: string }) {
  return (
    <p className="animate-pulse text-sm text-zinc-400" role="status">
      Loading {what}…
    </p>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return (
    <p className="rounded border border-red-900 bg-red-950/50 p-3 text-sm text-red-300" role="alert">
      {error}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-zinc-400">{children}</p>;
}
