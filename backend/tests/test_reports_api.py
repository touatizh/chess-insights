"""API tests for the report endpoints (§5).

Uses a temp SQLite DB, a fakeredis-backed synchronous RQ queue (is_async=False so
the job runs inline, no worker), and respx-mocked Lichess. The engine is stubbed
so no Stockfish is needed.
"""

from __future__ import annotations

import json

import chess
import chess.engine
import fakeredis
import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from rq import Queue
from sqlmodel import SQLModel, create_engine

FRESHNESS_GAME = {
    "id": "game0001",
    "speed": "blitz",
    "createdAt": 1_700_000_000_000,
    "winner": "white",
    "players": {
        "white": {"user": {"name": "alice"}},
        "black": {"user": {"name": "rival"}},
    },
    "opening": {"name": "Sicilian Defense", "eco": "B20"},
    "moves": "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be2 e5",
}

FRESHNESS_GAME_2 = {
    "id": "game0002",
    "speed": "rapid",
    "createdAt": 1_700_000_100_000,
    "winner": None,
    "players": {
        "white": {"user": {"name": "alice"}},
        "black": {"user": {"name": "rival"}},
    },
    "opening": {"name": "Italian Game", "eco": "C50"},
    "moves": "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O",
}


def _ndjson(*games: dict) -> str:
    return "\n".join(json.dumps(g) for g in games) + "\n"


class StubEngine:
    def analyse(self, board, limit):
        return {"score": chess.engine.PovScore(chess.engine.Cp(30), board.turn)}


def _stub_open_engine():
    from contextlib import nullcontext

    return nullcontext(StubEngine())


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Temp DB wired into the app's module-global engine.
    url = f"sqlite:///{tmp_path}/api.db"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    import app.db as app_db

    monkeypatch.setattr(app_db, "engine", engine)

    # Synchronous fakeredis queue so enqueued jobs run inline (no worker).
    conn = fakeredis.FakeStrictRedis()
    sync_queue = Queue("reports", connection=conn, is_async=False)

    import app.queue as app_queue
    import app.routers.reports as reports_mod

    monkeypatch.setattr(app_queue, "get_queue", lambda connection=None: sync_queue)
    monkeypatch.setattr(reports_mod, "get_queue", lambda connection=None: sync_queue)

    # Stub the engine so no Stockfish is needed when the inline job runs.
    import app.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod.engine, "open_engine", _stub_open_engine)

    # Reset per-IP rate limits between tests.
    import app.ratelimit as rl

    rl.reset_limits()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    rl.reset_limits()


def _mock_freshness(latest: dict | None) -> None:
    """Mock the max=1 freshness call."""
    body = _ndjson(latest) if latest else ""
    respx.get(url__regex=r"https://lichess\.org/api/games/user/.*").mock(
        return_value=httpx.Response(200, text=body)
    )


# --------------------------------------------------------------------------- #
# POST /api/reports
# --------------------------------------------------------------------------- #


@respx.mock
def test_post_queues_job_then_completes_inline(client: TestClient) -> None:
    _mock_freshness(FRESHNESS_GAME)
    resp = client.post("/api/reports", json={"username": "alice"})
    # Inline (is_async=False) job runs during enqueue; API still returns 202.
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    report_id = body["report_id"]

    # The inline job already finished it → status is done.
    status = client.get(f"/api/reports/{report_id}").json()
    assert status["status"] == "done"
    assert status["username"] == "alice"
    assert status["payload"]["games_analyzed"] == 1
    # Analysis counters surfaced for the "Analyzing N/M" label.
    assert status["total_new"] == 1
    assert status["analyzed_new"] == 1


@respx.mock
def test_post_unknown_user_404(client: TestClient) -> None:
    respx.get(url__regex=r"https://lichess\.org/api/games/user/.*").mock(
        return_value=httpx.Response(404, text="")
    )
    resp = client.post("/api/reports", json={"username": "ghost"})
    assert resp.status_code == 404


@respx.mock
def test_repeat_request_returns_cached_200(client: TestClient) -> None:
    _mock_freshness(FRESHNESS_GAME)
    first = client.post("/api/reports", json={"username": "alice"})
    assert first.status_code == 202

    # Same newest game already stored + done report exists → cached 200.
    second = client.post("/api/reports", json={"username": "alice"})
    assert second.status_code == 200
    assert second.json()["status"] == "done"
    # Cached path reuses the same report id.
    assert second.json()["report_id"] == first.json()["report_id"]


@respx.mock
def test_rate_limit_new_jobs_429(client: TestClient) -> None:
    # Distinct newest game each time so it never hits the cache path; each POST
    # tries to queue a NEW job -> 3rd within the hour is 429.
    games = [dict(FRESHNESS_GAME, id=f"g{i}") for i in range(3)]
    for i, g in enumerate(games):
        respx.get(url__regex=r"https://lichess\.org/api/games/user/.*").mock(
            return_value=httpx.Response(200, text=_ndjson(g))
        )
        resp = client.post("/api/reports", json={"username": f"user{i}"})
        # user0, user1 succeed (202); the limiter is per-IP so the 3rd is 429.
        if i < 2:
            assert resp.status_code == 202, (i, resp.status_code)
        else:
            assert resp.status_code == 429


# --------------------------------------------------------------------------- #
# GET /api/reports/{id} and by-username
# --------------------------------------------------------------------------- #


def test_get_missing_report_404(client: TestClient) -> None:
    assert client.get("/api/reports/99999").status_code == 404


def test_by_username_404_when_none(client: TestClient) -> None:
    assert client.get("/api/reports/by-username/nobody").status_code == 404


@respx.mock
def test_by_username_returns_payload(client: TestClient) -> None:
    _mock_freshness(FRESHNESS_GAME)
    client.post("/api/reports", json={"username": "alice"})
    resp = client.get("/api/reports/by-username/alice")
    assert resp.status_code == 200
    assert resp.json()["payload"]["username"] == "alice"


def test_by_username_does_not_create_player(client: TestClient) -> None:
    """A GET must not write a Player row for an unknown username (read-only)."""
    from sqlmodel import Session, func, select

    import app.db as app_db
    from app.models import Player

    resp = client.get("/api/reports/by-username/ghostuser")
    assert resp.status_code == 404
    with Session(app_db.engine) as s:
        count = s.exec(select(func.count()).select_from(Player)).one()
    assert count == 0  # no player created by the read endpoint


def test_stale_active_report_does_not_block_new_post(client: TestClient, monkeypatch) -> None:
    """A report stuck 'analyzing' longer than the job timeout must not 409 forever."""
    from datetime import UTC, datetime, timedelta

    from sqlmodel import Session

    import app.db as app_db
    from app.models import Player, Report
    from app.queue import JOB_TIMEOUT

    # Seed a player with a stale in-progress report (older than the job timeout).
    with Session(app_db.engine) as s:
        player = Player(username="stuck")
        s.add(player)
        s.commit()
        s.refresh(player)
        stale = Report(
            player_id=player.id,
            status="analyzing",
            progress=50,
            created_at=datetime.now(UTC) - timedelta(seconds=JOB_TIMEOUT + 60),
        )
        s.add(stale)
        s.commit()

    with respx.mock:
        respx.get(url__regex=r"https://lichess\.org/api/games/user/.*").mock(
            return_value=httpx.Response(200, text=_ndjson(dict(FRESHNESS_GAME, id="fresh1")))
        )
        resp = client.post("/api/reports", json={"username": "stuck"})

    # Not 409 — the stale report is treated as abandoned, so a new job is queued.
    assert resp.status_code in (200, 202)


def test_fresh_active_report_resumes_existing(client: TestClient) -> None:
    """A recent in-progress report is handed back so the client can resume
    polling it (202 + that report's id), instead of a dead-end 409."""
    from datetime import UTC, datetime

    from sqlmodel import Session

    import app.db as app_db
    from app.models import Player, Report

    with Session(app_db.engine) as s:
        player = Player(username="busy")
        s.add(player)
        s.commit()
        s.refresh(player)
        active = Report(
            player_id=player.id,
            status="analyzing",
            progress=50,
            created_at=datetime.now(UTC),
        )
        s.add(active)
        s.commit()
        s.refresh(active)
        active_id = active.id

    with respx.mock:
        respx.get(url__regex=r"https://lichess\.org/api/games/user/.*").mock(
            return_value=httpx.Response(200, text=_ndjson(dict(FRESHNESS_GAME, id="fresh2")))
        )
        resp = client.post("/api/reports", json={"username": "busy"})

    assert resp.status_code == 202
    body = resp.json()
    assert body["report_id"] == str(active_id)
    assert body["status"] == "analyzing"


# --------------------------------------------------------------------------- #
# GET /api/featured
# --------------------------------------------------------------------------- #


def test_featured_empty_by_default(client: TestClient) -> None:
    resp = client.get("/api/featured")
    assert resp.status_code == 200
    assert resp.json() == {"featured": []}
