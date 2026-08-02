"""Fetch and parse games from the Lichess public API (§6.1).

Endpoint returns NDJSON (one JSON game per line). No auth token is required for
public games.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx

LICHESS_GAMES_URL = "https://lichess.org/api/games/user/{username}"

# Games shorter than this many plies are aborts/rage-quits and pollute accuracy
# averages, so they are discarded at parse time (§6.1).
MIN_PLIES = 10

_RETRY_BACKOFF_SECONDS = 60


class LichessError(RuntimeError):
    """Raised when the Lichess API cannot be used to fetch games."""


def _ms_to_datetime(value: Any) -> datetime:
    """Lichess timestamps are epoch milliseconds."""
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _count_plies(moves: str) -> int:
    if not moves.strip():
        return 0
    return len(moves.split())


def _resolve_color(raw: dict[str, Any], username: str) -> str | None:
    """Return which color the subject played, or None if they aren't in the game."""
    players = raw.get("players", {})
    for color in ("white", "black"):
        user = players.get(color, {}).get("user", {})
        name = user.get("name")
        if name and name.lower() == username.lower():
            return color
    return None


def _resolve_result(raw: dict[str, Any], color: str) -> str:
    winner = raw.get("winner")
    if winner is None:
        return "draw"
    return "win" if winner == color else "loss"


def parse_game(line: str, username: str) -> dict[str, Any] | None:
    """Parse a single NDJSON line into a Game-shaped dict.

    Returns None if the line is blank, the subject isn't a player, or the game
    is below the minimum ply threshold.
    """
    line = line.strip()
    if not line:
        return None

    raw = json.loads(line)

    color = _resolve_color(raw, username)
    if color is None:
        return None

    moves = raw.get("moves", "") or ""
    if _count_plies(moves) < MIN_PLIES:
        return None

    opening = raw.get("opening", {}) or {}

    return {
        "lichess_id": raw["id"],
        "speed": raw.get("speed", ""),
        "played_at": _ms_to_datetime(raw.get("createdAt", 0)),
        "color": color,
        "result": _resolve_result(raw, color),
        "opening_name": opening.get("name"),
        "opening_eco": opening.get("eco"),
        "moves": moves,
    }


def parse_ndjson(text: str, username: str) -> list[dict[str, Any]]:
    """Parse an NDJSON payload into a list of Game-shaped dicts."""
    games: list[dict[str, Any]] = []
    for line in text.splitlines():
        game = parse_game(line, username)
        if game is not None:
            games.append(game)
    return games


def fetch_games(
    username: str,
    *,
    max_games: int = 30,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Fetch the subject's latest rapid/blitz games and return parsed dicts.

    Retries once on HTTP 429 after a 60s backoff; any other failure raises
    LichessError (§6.1).
    """
    url = LICHESS_GAMES_URL.format(username=username)
    params: dict[str, str | int] = {
        "max": max_games,
        "perfType": "blitz,rapid",
        "opening": "true",
        "moves": "true",
    }
    headers = {"Accept": "application/x-ndjson"}

    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        attempts = 0
        while True:
            attempts += 1
            response = client.get(url, params=params, headers=headers)
            if response.status_code == 429 and attempts == 1:
                time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            if response.status_code == 429:
                raise LichessError("Lichess rate limit exceeded; try again later.")
            if response.status_code == 404:
                raise LichessError(f"Lichess user '{username}' not found.")
            if response.status_code >= 400:
                raise LichessError(f"Lichess request failed with status {response.status_code}.")
            return parse_ndjson(response.text, username)
    finally:
        if owns_client:
            client.close()
