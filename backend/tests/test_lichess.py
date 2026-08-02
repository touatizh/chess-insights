"""Unit tests for lichess.py NDJSON fetch/parse (§6.1)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.lichess import LichessError, fetch_games, parse_ndjson

USERNAME = "someuser"

# A game with well over 10 plies where the subject played white and won.
FULL_GAME_WHITE_WIN = {
    "id": "abc12345",
    "speed": "blitz",
    "createdAt": 1_700_000_000_000,
    "winner": "white",
    "players": {
        "white": {"user": {"name": "SomeUser"}},
        "black": {"user": {"name": "Rival"}},
    },
    "opening": {"name": "Sicilian Defense", "eco": "B20"},
    "moves": "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be2 e5",
}

# Subject played black and lost (winner == white).
FULL_GAME_BLACK_LOSS = {
    "id": "def67890",
    "speed": "rapid",
    "createdAt": 1_700_000_100_000,
    "winner": "white",
    "players": {
        "white": {"user": {"name": "Rival"}},
        "black": {"user": {"name": "someuser"}},
    },
    "opening": {"name": "Italian Game", "eco": "C50"},
    "moves": "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O",
}

# A draw (no winner key).
FULL_GAME_DRAW = {
    "id": "ghi11111",
    "speed": "blitz",
    "createdAt": 1_700_000_200_000,
    "players": {
        "white": {"user": {"name": "someuser"}},
        "black": {"user": {"name": "Rival"}},
    },
    "opening": {"name": "French Defense", "eco": "C00"},
    "moves": "e4 e6 d4 d5 Nc3 Nf6 e5 Nfd7 f4 c5 Nf3 Nc6",
}

# An abandoned game: fewer than 10 plies -> must be skipped.
SHORT_GAME = {
    "id": "short001",
    "speed": "blitz",
    "createdAt": 1_700_000_300_000,
    "winner": "black",
    "players": {
        "white": {"user": {"name": "someuser"}},
        "black": {"user": {"name": "Rival"}},
    },
    "opening": {"name": "Aborted", "eco": "A00"},
    "moves": "e4 e5 Nf3",
}


def _ndjson(*games: dict) -> str:
    return "\n".join(json.dumps(g) for g in games) + "\n"


def _mock_route(body: str, status_code: int = 200) -> respx.Route:
    route = respx.get(url__regex=r"https://lichess\.org/api/games/user/.*")
    route.mock(return_value=httpx.Response(status_code, text=body))
    return route


@respx.mock
def test_fetch_games_returns_parsed_dicts() -> None:
    _mock_route(_ndjson(FULL_GAME_WHITE_WIN))
    games = fetch_games(USERNAME)
    assert len(games) >= 1
    game = games[0]
    for key in (
        "lichess_id",
        "speed",
        "played_at",
        "color",
        "result",
        "opening_name",
        "opening_eco",
        "moves",
    ):
        assert key in game
    assert game["lichess_id"] == "abc12345"
    assert game["color"] == "white"
    assert game["result"] == "win"
    assert game["opening_eco"] == "B20"


@respx.mock
def test_fetch_games_parses_multiple() -> None:
    _mock_route(_ndjson(FULL_GAME_WHITE_WIN, FULL_GAME_BLACK_LOSS, FULL_GAME_DRAW))
    games = fetch_games(USERNAME)
    assert len(games) == 3
    by_id = {g["lichess_id"]: g for g in games}
    assert by_id["def67890"]["color"] == "black"
    assert by_id["def67890"]["result"] == "loss"
    assert by_id["ghi11111"]["result"] == "draw"


@respx.mock
def test_short_games_are_skipped() -> None:
    _mock_route(_ndjson(FULL_GAME_WHITE_WIN, SHORT_GAME))
    games = fetch_games(USERNAME)
    ids = {g["lichess_id"] for g in games}
    assert "abc12345" in ids
    assert "short001" not in ids


def test_parse_ndjson_skips_blank_lines() -> None:
    body = _ndjson(FULL_GAME_WHITE_WIN) + "\n\n"
    games = parse_ndjson(body, USERNAME)
    assert len(games) == 1


def test_parse_ndjson_skips_games_without_subject() -> None:
    other = dict(FULL_GAME_WHITE_WIN)
    other["players"] = {
        "white": {"user": {"name": "notyou"}},
        "black": {"user": {"name": "alsonotyou"}},
    }
    games = parse_ndjson(_ndjson(other), USERNAME)
    assert games == []


@respx.mock
def test_fetch_games_404_raises() -> None:
    _mock_route("", status_code=404)
    with pytest.raises(LichessError):
        fetch_games(USERNAME)


@respx.mock
def test_fetch_games_500_raises() -> None:
    _mock_route("", status_code=500)
    with pytest.raises(LichessError):
        fetch_games(USERNAME)


@respx.mock
def test_fetch_games_retries_once_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.lichess.time.sleep", lambda s: sleeps.append(s))

    responses = [
        httpx.Response(429, text=""),
        httpx.Response(200, text=_ndjson(FULL_GAME_WHITE_WIN)),
    ]
    route = respx.get(url__regex=r"https://lichess\.org/api/games/user/.*")
    route.side_effect = responses

    games = fetch_games(USERNAME)
    assert len(games) == 1
    assert sleeps == [60]
