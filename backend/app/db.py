"""Database engine/session.

SQLite runs under two writers (API + worker), so WAL mode and busy_timeout are
mandatory per §5 to avoid SQLITE_BUSY on concurrent writes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import event
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.analysis.engine import MoveAnalysis
from app.analysis.report import ReportPayload
from app.models import Game, MoveEval, Player, Report

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./chess_insights.db")

# check_same_thread=False so the engine can be shared across threads/processes.
# busy_timeout via connect_args guards against SQLITE_BUSY while a writer holds
# the lock.
_connect_args: dict[str, object] = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 5}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def _set_sqlite_pragma(dbapi_connection: object, connection_record: object) -> None:
    """Enable WAL and a 5s busy timeout on every SQLite connection (§5)."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


# Attach the pragma to THIS engine only (not the global Engine class), so test
# suites or workers that spin up their own engines aren't affected. Only wired
# for SQLite; Postgres needs neither WAL nor busy_timeout.
if DATABASE_URL.startswith("sqlite"):
    event.listens_for(engine, "connect")(_set_sqlite_pragma)


def init_db() -> None:
    """Create tables if they don't exist."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit-or-rollback session context manager for jobs and routers."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #


def get_player_id(username: str, session: Session) -> int | None:
    """Look up a player id by (case-insensitive) username without creating one.

    Returns ``None`` when the player doesn't exist — used by read endpoints so a
    GET never writes to the DB.
    """
    normalized = username.lower()
    player = session.exec(select(Player).where(Player.username == normalized)).first()
    return player.id if player is not None else None


def get_or_create_player(username: str, session: Session) -> tuple[int, bool]:
    """Get an existing player by (case-insensitive) username or create one.

    Returns ``(player_id, created)``. Usernames are stored lowercase (§4).
    """
    normalized = username.lower()
    player = session.exec(select(Player).where(Player.username == normalized)).first()
    if player is not None:
        return player.id, False  # type: ignore[return-value]
    player = Player(username=normalized)
    session.add(player)
    session.commit()
    session.refresh(player)
    return player.id, True  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Games / dedupe (§6.5, per-player)
# --------------------------------------------------------------------------- #


def stored_lichess_ids(session: Session, player_id: int) -> set[str]:
    """All ``lichess_id``s already stored for one player (for dedupe)."""
    rows = session.exec(select(Game.lichess_id).where(Game.player_id == player_id)).all()
    return set(rows)


def insert_new_games(session: Session, player_id: int, games: list[dict[str, object]]) -> int:
    """Insert games for a player, skipping any whose lichess_id is already stored.

    Dedupe is scoped per player (§4, (player_id, lichess_id) unique constraint).
    Returns the number of games actually inserted.
    """
    existing = stored_lichess_ids(session, player_id)
    inserted = 0
    for game in games:
        lichess_id = str(game["lichess_id"])
        if lichess_id in existing:
            continue
        session.add(
            Game(
                player_id=player_id,
                lichess_id=lichess_id,
                speed=str(game["speed"]),
                played_at=game["played_at"],  # type: ignore[arg-type]
                color=str(game["color"]),
                result=str(game["result"]),
                opening_name=game.get("opening_name"),
                opening_eco=game.get("opening_eco"),
                moves=str(game["moves"]),
                analyzed=False,
            )
        )
        existing.add(lichess_id)
        inserted += 1
    session.commit()
    return inserted


def latest_games(session: Session, player_id: int, limit: int = 30) -> list[Game]:
    """Most recent ``limit`` games for a player, chronological ascending."""
    stmt = (
        select(Game)
        .where(Game.player_id == player_id)
        .order_by(col(Game.played_at).desc())
        .limit(limit)
    )
    return list(reversed(session.exec(stmt).all()))


def has_game(session: Session, player_id: int, lichess_id: str) -> bool:
    """True if this player already stores this lichess_id (freshness check §4)."""
    stmt = select(Game.id).where(Game.player_id == player_id, Game.lichess_id == lichess_id)
    return session.exec(stmt).first() is not None


# --------------------------------------------------------------------------- #
# MoveEvals
# --------------------------------------------------------------------------- #


def insert_move_evals(
    session: Session,
    game: Game,
    analyses: list[MoveAnalysis],
) -> None:
    """Persist MoveEval rows for one game (subject's own moves only, §4)."""
    for analysis in analyses:
        session.add(
            MoveEval(
                game_id=game.id,
                ply=analysis.ply,
                cp_loss=analysis.cp_loss,
                phase=analysis.phase,
                severity=analysis.severity,
            )
        )
    session.commit()


def move_evals_for_games(session: Session, game_ids: list[int]) -> dict[int, list[MoveEval]]:
    """MoveEvals grouped by game_id (only for the given game_ids)."""
    stmt = select(MoveEval).where(col(MoveEval.game_id).in_(game_ids))
    grouped: dict[int, list[MoveEval]] = {}
    for move_eval in session.exec(stmt).all():
        grouped.setdefault(move_eval.game_id, []).append(move_eval)
    return grouped


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


def create_report(
    session: Session,
    player_id: int,
    *,
    status: str = "queued",
    progress: int = 0,
) -> Report:
    """Create a new Report row and return it."""
    report = Report(
        player_id=player_id,
        status=status,
        progress=progress,
        created_at=_utcnow(),
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def update_report(
    session: Session, report_id: int, *, status: str | None = None, progress: int | None = None
) -> None:
    """Update a report's status/progress in place."""
    report = session.exec(select(Report).where(Report.id == report_id)).first()
    if report is None:
        raise LookupError(f"report {report_id} not found")
    if status is not None:
        report.status = status
    if progress is not None:
        report.progress = progress
    session.commit()


def set_report_payload(session: Session, report_id: int, payload: ReportPayload) -> None:
    """Store the finished payload and mark the report done."""
    report = session.exec(select(Report).where(Report.id == report_id)).first()
    if report is None:
        raise LookupError(f"report {report_id} not found")
    report.payload = dict(payload)
    report.status = "done"
    report.progress = 100
    session.commit()


def fail_report(session: Session, report_id: int, error: str) -> None:
    """Mark a report failed with a user-safe error message."""
    report = session.exec(select(Report).where(Report.id == report_id)).first()
    if report is None:
        raise LookupError(f"report {report_id} not found")
    report.status = "failed"
    report.error = error
    session.commit()


def get_report(session: Session, report_id: int) -> Report | None:
    return session.exec(select(Report).where(Report.id == report_id)).first()


def latest_done_report(session: Session, player_id: int) -> Report | None:
    """Most recent done report for a player (freshness check / by-username)."""
    stmt = (
        select(Report)
        .where(Report.player_id == player_id, Report.status == "done")
        .order_by(col(Report.created_at).desc())
        .limit(1)
    )
    return session.exec(stmt).first()


def latest_active_report(
    session: Session, player_id: int, *, stale_after_seconds: int | None = None
) -> Report | None:
    """Most recent genuinely-in-progress report for a player (409 guard).

    A report whose ``created_at`` is older than ``stale_after_seconds`` is treated
    as abandoned (e.g. the worker was hard-killed — OOM/SIGKILL — before it could
    mark the report ``failed``) and is ignored, so a stuck report never locks a
    username out of new reports forever.
    """
    stmt = (
        select(Report)
        .where(
            Report.player_id == player_id,
            col(Report.status).in_(["queued", "fetching", "analyzing"]),
        )
        .order_by(col(Report.created_at).desc())
        .limit(1)
    )
    report = session.exec(stmt).first()
    if report is None:
        return None
    if stale_after_seconds is not None:
        age = (_utcnow() - _as_utc(report.created_at)).total_seconds()
        if age > stale_after_seconds:
            return None
    return report


def _as_utc(value: datetime) -> datetime:
    """Coerce a possibly-naive datetime (SQLite loses tz) to UTC-aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def featured_reports(session: Session) -> list[Report]:
    """Done reports flagged for the homepage (Phase 5 seeds these)."""
    stmt = (
        select(Report)
        .where(Report.status == "done", Report.featured == True)  # noqa: E712
        .order_by(col(Report.created_at).desc())
    )
    return list(session.exec(stmt).all())
