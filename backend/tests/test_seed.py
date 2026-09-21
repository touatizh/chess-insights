"""Tests for the featured-report seeding CLI (§8 Phase 5).

Mirrors test_jobs.py: a real SQLite DB on a temp file, a canned game list, and a
stubbed engine -- no live Lichess calls and no Stockfish.
"""

from __future__ import annotations

from datetime import UTC, datetime

import chess
import chess.engine
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import jobs, seed
from app.models import Report

GAME_ONE = {
    "lichess_id": "abc12345",
    "speed": "blitz",
    "played_at": datetime(2026, 1, 1, tzinfo=UTC),
    "color": "white",
    "result": "win",
    "opening_name": "Sicilian Defense",
    "opening_eco": "B20",
    "moves": "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be2 e5",
}

GAME_TWO = {
    "lichess_id": "def67890",
    "speed": "rapid",
    "played_at": datetime(2026, 1, 2, tzinfo=UTC),
    "color": "black",
    "result": "loss",
    "opening_name": "Italian Game",
    "opening_eco": "C50",
    "moves": "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O",
}


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/seed.db"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    import app.db as app_db

    monkeypatch.setattr(app_db, "engine", engine)
    # init_db() would rebuild the schema on the app's own engine, not this one.
    monkeypatch.setattr(seed, "init_db", lambda: None)
    yield engine
    engine.dispose()


class StubEngine:
    def analyse(self, board, limit):
        return {"score": chess.engine.PovScore(chess.engine.Cp(100), board.turn)}


def _stub_open_engine():
    from contextlib import nullcontext

    return nullcontext(StubEngine())


@pytest.fixture
def stub_pipeline(monkeypatch):
    monkeypatch.setattr(jobs, "fetch_games", lambda username, max_games=30: [GAME_ONE, GAME_TWO])
    monkeypatch.setattr(jobs.engine, "open_engine", _stub_open_engine)


def _featured(db_engine) -> list[Report]:
    with Session(db_engine) as s:
        return list(s.exec(select(Report).where(Report.featured == True)).all())  # noqa: E712


def test_seed_creates_done_featured_report(db_engine, stub_pipeline) -> None:
    report_id = seed.seed_username("alice")

    with Session(db_engine) as s:
        report = s.get(Report, report_id)
        assert report is not None
        assert report.status == "done"
        assert report.featured is True
        assert report.payload is not None

    assert len(_featured(db_engine)) == 1


def test_seed_is_idempotent(db_engine, stub_pipeline) -> None:
    """Re-running must flag the existing report, not analyze a second one."""
    first = seed.seed_username("alice")
    second = seed.seed_username("alice")

    assert first == second
    with Session(db_engine) as s:
        assert len(s.exec(select(Report)).all()) == 1
    assert len(_featured(db_engine)) == 1


def test_main_seeds_every_username(db_engine, stub_pipeline, capsys) -> None:
    exit_code = seed.main(["alice", "bob"])

    assert exit_code == 0
    assert len(_featured(db_engine)) == 2
    assert "seeded 2/2" in capsys.readouterr().out


def test_main_isolates_failures(db_engine, stub_pipeline, monkeypatch, capsys) -> None:
    """One bad username must not discard the others' work."""
    real = seed.seed_username

    def flaky(username: str) -> int:
        if username == "ghost":
            raise RuntimeError("no such user")
        return real(username)

    monkeypatch.setattr(seed, "seed_username", flaky)

    exit_code = seed.main(["alice", "ghost", "bob"])

    assert exit_code == 1
    assert len(_featured(db_engine)) == 2
    assert "seeded 2/3" in capsys.readouterr().out


def test_main_without_usernames_is_a_usage_error(db_engine, capsys) -> None:
    assert seed.main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_seed_raises_when_report_fails(db_engine, monkeypatch) -> None:
    """A failed analysis must not be flagged featured."""

    def boom(username: str, max_games: int = 30):
        raise jobs.LichessError("lichess is down")

    monkeypatch.setattr(jobs, "fetch_games", boom)

    with pytest.raises(jobs.LichessError):
        seed.seed_username("alice")

    assert _featured(db_engine) == []


def test_seed_refuses_a_report_that_never_reached_done(db_engine, monkeypatch) -> None:
    """If the job returns without raising but the report isn't done, the row must
    not be flagged featured -- a half-finished report on the homepage is worse
    than an empty one."""
    monkeypatch.setattr(seed, "run_report_job", lambda report_id, username: None)

    with pytest.raises(RuntimeError, match="not done"):
        seed.seed_username("alice")

    assert _featured(db_engine) == []
