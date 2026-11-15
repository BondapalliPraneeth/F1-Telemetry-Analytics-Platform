"""Jolpica-F1 (Ergast-compatible) client: race results and pit stops."""

from __future__ import annotations

from typing import Any

from f1tel.clients._http import CachedJSONClient

PAGE = 100


class JolpicaClient(CachedJSONClient):
    """Fetch results and pit stops from a Jolpica-F1 / Ergast-compatible API."""

    def results(self, year: int, round_: int) -> list[dict[str, Any]]:
        """Final classification for a race.

        Returns:
            One dict per driver with ``driver_id``, ``code``, ``number``, ``position``,
            ``grid``, ``laps``, ``status``, ``points`` and ``constructor``.
        """
        data = self.get_json(f"{year}/{round_}/results.json", {"limit": PAGE})
        races = data["MRData"]["RaceTable"]["Races"]
        if not races:
            return []
        out = []
        for r in races[0]["Results"]:
            drv = r["Driver"]
            out.append(
                {
                    "driver_id": drv["driverId"],
                    "code": drv.get("code", ""),
                    "number": r.get("number"),
                    "position": int(r["position"]),
                    "grid": int(r.get("grid", 0)),
                    "laps": int(r.get("laps", 0)),
                    "status": r.get("status", ""),
                    "points": float(r.get("points", 0)),
                    "constructor": r.get("Constructor", {}).get("name", ""),
                }
            )
        return out

    def pit_stops(self, year: int, round_: int) -> list[dict[str, Any]]:
        """All pit stops of a race (paginated): ``driver_id``, ``stop``, ``lap``, ``duration_s``."""
        stops: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self.get_json(
                f"{year}/{round_}/pitstops.json", {"limit": PAGE, "offset": offset}
            )["MRData"]
            races = data["RaceTable"]["Races"]
            batch = races[0]["PitStops"] if races else []
            for p in batch:
                try:
                    duration = float(p["duration"])
                except (KeyError, ValueError):  # durations like "1:02.3" or missing
                    duration = None
                stops.append(
                    {
                        "driver_id": p["driverId"],
                        "stop": int(p["stop"]),
                        "lap": int(p["lap"]),
                        "duration_s": duration,
                    }
                )
            offset += PAGE
            if not batch or offset >= int(data.get("total", 0)):
                return stops
