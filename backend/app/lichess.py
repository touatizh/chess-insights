"""Fetch and parse games from the Lichess public API (§6.1).

Endpoint returns NDJSON (one JSON game per line). No auth token is required for
public games, but an optional ``LICHESS_TOKEN`` (env var only) is sent as a
Bearer token when present to raise rate limits.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

LICHESS_GAMES_URL = "https://lichess.org/api/games/user/{username}"
LICHESS_USER_URL = "https://lichess.org/api/user/{username}"

# Lichess asks every API client to send a descriptive User-Agent. Without one
# the bulk games-export endpoint may reject requests — and, misleadingly, return
# a 404 {"error":"Not found"} instead of an honest 429 for throttled IPs, which
# breaks naive retry-on-429 logic.
USER_AGENT = "chess-insights/0.1 (+https://github.com/touatizh/chess-insights)"


def _auth_headers() -> dict[str, str]:
    """Base headers, adding a Bearer token from LICHESS_TOKEN when present.

    An authenticated request gets substantially higher Lichess rate limits. The
    token is read from the environment at call time and never logged or persisted
    — config via env var only (§10). Absent/blank token -> anonymous request.
    """
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("LICHESS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# Games shorter than this many plies are aborts/rage-quits and pollute accuracy
# averages, so they are discarded at parse time (§6.1).
MIN_PLIES = 10

_RETRY_BACKOFF_SECONDS = 60

# The exact body Lichess returns when it declines the games-export request. This
# is ambiguous: it appears both for a genuinely unknown user AND for a throttled
# IP (a masked 429). We disambiguate via a cheap /api/user existence check.
_NOT_FOUND_BODY = '{"error":"Not found"}'


class LichessError(RuntimeError):
    """Raised when the Lichess API cannot be used to fetch games."""


class LichessRateLimitError(LichessError):
    """Raised when Lichess throttles the request (HTTP 429, or a masked 404)."""


class LichessUserNotFoundError(LichessError):
    """Raised when the requested user genuinely does not exist on Lichess."""


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


def _user_exists(username: str, client: httpx.Client) -> bool:
    """Cheap existence check via /api/user (unaffected by games-export throttling).

    Any non-2xx response is treated as "cannot confirm existence" -> False, so the
    caller falls back to the safe user-not-found error rather than masking a real
    404 as a rate-limit.
    """
    url = LICHESS_USER_URL.format(username=username)
    try:
        response = client.get(url, headers=_auth_headers())
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def fetch_games(
    username: str,
    *,
    max_games: int = 30,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Fetch the subject's latest rapid/blitz games and return parsed dicts.

    Retries once on HTTP 429 after a 60s backoff. A genuine unknown user raises
    LichessUserNotFoundError; throttling raises LichessRateLimitError. Lichess
    masks some throttled requests as a 404 with body ``{"error":"Not found"}`` on
    this endpoint, so an ambiguous 404 is disambiguated with a cheap /api/user
    existence check (§6.1).
    """
    url = LICHESS_GAMES_URL.format(username=username)
    params: dict[str, str | int] = {
        "max": max_games,
        "perfType": "blitz,rapid",
        "opening": "true",
        "moves": "true",
    }
    headers = {"Accept": "application/x-ndjson", **_auth_headers()}

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
                raise LichessRateLimitError("Lichess rate limit exceeded; try again later.")
            if response.status_code == 404:
                # Ambiguous: genuine unknown user, or a throttled request masked
                # as a 404. If the user demonstrably exists, it's throttling.
                if response.text.strip() == _NOT_FOUND_BODY and _user_exists(username, client):
                    raise LichessRateLimitError(
                        "Lichess declined the games export (rate limited); try again later."
                    )
                raise LichessUserNotFoundError(f"Lichess user '{username}' not found.")
            if response.status_code >= 400:
                raise LichessError(f"Lichess request failed with status {response.status_code}.")
            return parse_ndjson(response.text, username)
    finally:
        if owns_client:
            client.close()


def fetch_latest_game_id(username: str, *, client: httpx.Client | None = None) -> str:
    """Fetch the subject's most recent game id for the freshness check (§4).

    Makes a single ``max=1`` games-export call. Returns the ``lichess_id`` when
    one exists; returns ``""`` when the user has no eligible games; raises
    :class:`LichessUserNotFoundError` for a genuinely unknown user and
    :class:`LichessError` on throttling/other failures.
    """
    url = LICHESS_GAMES_URL.format(username=username)
    params: dict[str, str | int] = {"max": 1, "perfType": "blitz,rapid"}
    headers = {"Accept": "application/x-ndjson", **_auth_headers()}

    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        response = client.get(url, params=params, headers=headers)
        if response.status_code == 429:
            raise LichessRateLimitError("Lichess rate limit exceeded; try again later.")
        if response.status_code == 404:
            # Ambiguous (as in fetch_games): user exists but has no eligible
            # games, or a masked throttle. Disambiguate cheaply.
            if response.text.strip() == _NOT_FOUND_BODY and _user_exists(username, client):
                raise LichessRateLimitError(
                    "Lichess declined the games export (rate limited); try again later."
                )
            raise LichessUserNotFoundError(f"Lichess user '{username}' not found.")
        if response.status_code >= 400:
            raise LichessError(f"Lichess request failed with status {response.status_code}.")

        games = parse_ndjson(response.text, username)
        if not games:
            return ""
        return str(games[0]["lichess_id"])
    finally:
        if owns_client:
            client.close()
