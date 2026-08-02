"""Unit tests for the incremental report job (§6.5).

Uses a real SQLite DB on a temp file, a mocked Lichess fetch, and a stubbed
engine — no live network or Stockfish. The dedupe test (second run analyzes 0
games) is the core §8 exit criterion.
"""

from __future__ import annotations

from datetime import UTC, datetime

import chess
import chess.engine
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import jobs
from app.db import (
    create_report,
    get_or_create_player,
    insert_new_games,
)
from app.models import Game, MoveEval, Report

# One full eligible game (reused from lichess fixtures).
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
    url = f"sqlite:///{tmp_path}/test.db"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    # Point the app's module-global engine at this temp DB so session_scope()
    # inside run_report_job uses it too.
    import app.db as app_db

    monkeypatch.setattr(app_db, "engine", engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    with Session(db_engine) as session:
        yield session


class StubEngine:
    """Fake Analyzer returning a fixed score for every position."""

    def analyse(self, board, limit):
        pov = chess.engine.PovScore(chess.engine.Cp(100), board.turn)
        return {"score": pov}


def _patch(monkeypatch: pytest.MonkeyPatch, games: list[dict]) -> None:
    """Point fetch_games at a canned list and open_engine at a stub."""
    monkeypatch.setattr(jobs, "fetch_games", lambda username, max_games=30: list(games))
    monkeypatch.setattr(jobs.engine, "open_engine", _stub_open_engine)


def _stub_open_engine():
    from contextlib import nullcontext

    return nullcontext(StubEngine())


def _rid(report: Report) -> int:
    assert report.id is not None
    return report.id


def test_job_first_run_analyzes_and_done(
    monkeypatch: pytest.MonkeyPatch, db_engine, db_session
) -> None:
    _patch(monkeypatch, [GAME_ONE, GAME_TWO])
    player_id, _ = get_or_create_player("alice", db_session)
    report = create_report(db_session, player_id)

    jobs.run_report_job(_rid(report), "alice")

    db_session.refresh(report)
    assert report.status == "done"
    assert report.progress == 100
    assert report.payload is not None
    assert report.payload["username"] == "alice"
    assert report.payload["games_analyzed"] == 2

    games = db_session.exec(select(Game)).all()
    assert len(games) == 2
    assert all(g.analyzed for g in games)
    move_evals = db_session.exec(select(MoveEval)).all()
    assert len(move_evals) > 0


def test_job_second_run_analyzes_zero_games(
    monkeypatch: pytest.MonkeyPatch, db_engine, db_session
) -> None:
    """The §8 dedupe exit criterion: a repeat run must analyze 0 games."""
    _patch(monkeypatch, [GAME_ONE, GAME_TWO])
    player_id, _ = get_or_create_player("alice", db_session)
    report = create_report(db_session, player_id)

    jobs.run_report_job(_rid(report), "alice")
    db_session.refresh(report)
    assert report.status == "done"

    # Second report for the same player — same games already stored.
    report2 = create_report(db_session, player_id)
    insert_new_games(db_session, player_id, [GAME_ONE, GAME_TWO])  # already present
    calls: list[str] = []
    monkeypatch.setattr(jobs, "_analyze_new_games", lambda s, r, p: calls.append("analyze"))
    jobs.run_report_job(_rid(report2), "alice")

    assert calls == []
    db_session.refresh(report2)
    assert report2.status == "done"
    assert report2.progress == 100


def test_job_marks_failed_on_fetch_error(
    monkeypatch: pytest.MonkeyPatch, db_engine, db_session
) -> None:
    from app.lichess import LichessError

    def boom(username, max_games=30):
        raise LichessError("Lichess down")

    monkeypatch.setattr(jobs, "fetch_games", boom)
    player_id, _ = get_or_create_player("alice", db_session)
    report = create_report(db_session, player_id)

    with pytest.raises(LichessError):
        jobs.run_report_job(_rid(report), "alice")

    db_session.refresh(report)
    assert report.status == "failed"
    assert "Lichess" in (report.error or "")


def test_job_progress_is_updating(monkeypatch: pytest.MonkeyPatch, db_engine, db_session) -> None:
    """Report.progress moves from 0→20→…→100 across the job."""
    _patch(monkeypatch, [GAME_ONE, GAME_TWO])
    player_id, _ = get_or_create_player("alice", db_session)
    report = create_report(db_session, player_id)

    seen: list[int] = []
    original = jobs.update_report

    def capture(session, report_id, **kwargs):
        seen.append(kwargs.get("progress"))
        return original(session, report_id, **kwargs)

    monkeypatch.setattr(jobs, "update_report", capture)
    jobs.run_report_job(_rid(report), "alice")

    progress_values = [p for p in seen if p is not None]
    assert 0 in progress_values
    assert 20 in progress_values
    assert 90 in progress_values  # aggregate stage
    assert all(0 <= p <= 100 for p in progress_values)


def test_job_persists_analysis_counters(
    monkeypatch: pytest.MonkeyPatch, db_engine, db_session
) -> None:
    """total_new stays fixed while analyzed_new climbs, so the frontend can show
    an exact 'Analyzing new games… N/M' label."""
    _patch(monkeypatch, [GAME_ONE, GAME_TWO])
    player_id, _ = get_or_create_player("alice", db_session)
    report = create_report(db_session, player_id)

    # Capture the (total_new, analyzed_new) persisted to the DB at each update
    # that touched the analysis counters.
    snapshots: list[tuple[int | None, int | None]] = []
    original = jobs.update_report

    def capture(session, report_id, **kwargs):
        result = original(session, report_id, **kwargs)
        # Read straight from the DB row to reflect what a poller would see.
        row = session.get(Report, report_id)
        snapshots.append((row.total_new, row.analyzed_new))
        return result

    monkeypatch.setattr(jobs, "update_report", capture)
    jobs.run_report_job(_rid(report), "alice")

    # Filter to the analysis phase (total_new set).
    during = [(t, a) for (t, a) in snapshots if t is not None]
    assert during, "expected total_new to be persisted during analysis"

    totals = {t for (t, _a) in during}
    assert totals == {2}, f"total_new must stay fixed at 2, saw {totals}"

    analyzed_seq = [a for (_t, a) in during if a is not None]
    # Climbs 0 -> 1 -> 2 (monotonic non-decreasing, reaches the total).
    assert analyzed_seq == sorted(analyzed_seq)
    assert analyzed_seq[0] == 0
    assert analyzed_seq[-1] == 2

    # Final persisted row reflects full completion of the new games.
    db_session.refresh(report)
    assert report.total_new == 2
    assert report.analyzed_new == 2


def test_job_uses_stored_evals_for_second_report(
    monkeypatch: pytest.MonkeyPatch, db_engine, db_session
) -> None:
    """A repeat job must not re-run Stockfish; MoveEvals are reused (§4)."""
    _patch(monkeypatch, [GAME_ONE, GAME_TWO])
    player_id, _ = get_or_create_player("alice", db_session)
    report = create_report(db_session, player_id)
    jobs.run_report_job(_rid(report), "alice")

    eval_count = len(db_session.exec(select(MoveEval)).all())

    report2 = create_report(db_session, player_id)
    jobs.run_report_job(_rid(report2), "alice")

    assert len(db_session.exec(select(MoveEval)).all()) == eval_count
    db_session.refresh(report2)
    assert report2.payload is not None
    assert report2.payload["games_analyzed"] == 2
