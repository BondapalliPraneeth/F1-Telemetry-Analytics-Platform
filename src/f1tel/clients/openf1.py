"""OpenF1 client used to cross-check FastF1 lap timing."""

from __future__ import annotations

from typing import Any

from f1tel.clients._http import CachedJSONClient

OPENF1_SESSION_NAMES = {"R": "Race", "Q": "Qualifying", "S": "Sprint", "SQ": "Sprint Qualifying"}


class OpenF1Client(CachedJSONClient):
    """Query the OpenF1 REST API (sessions and laps)."""

    def find_session_key(self, year: int, location: str, session_code: str) -> int | None:
        """Return the OpenF1 ``session_key`` for a weekend/session, or ``None``."""
        rows = self.get_json(
            "sessions",
            {
                "year": year,
                "location": location,
                "session_name": OPENF1_SESSION_NAMES[session_code],
            },
        )
        return int(rows[0]["session_key"]) if rows else None

    def laps(self, session_key: int) -> list[dict[str, Any]]:
        """All laps in a session: ``driver_number``, ``lap_number``, ``lap_duration``."""
        rows = self.get_json("laps", {"session_key": session_key})
        return [
            {
                "driver_number": str(r["driver_number"]),
                "lap_number": int(r["lap_number"]),
                "lap_duration": r.get("lap_duration"),
            }
            for r in rows
        ]
