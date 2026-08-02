"""SQLModel tables. Schema kept Postgres-compatible for later migration (§2, §4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Player(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)  # stored lowercase
    created_at: datetime = Field(default_factory=_utcnow)


class Game(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    player_id: int = Field(foreign_key="player.id", index=True)
    lichess_id: str = Field(index=True, unique=True)
    speed: str
    played_at: datetime
    color: str  # "white" | "black"
    result: str  # "win" | "loss" | "draw"
    opening_name: str | None = None
    opening_eco: str | None = None
    moves: str  # SAN string
    analyzed: bool = Field(default=False)


class MoveEval(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    game_id: int = Field(foreign_key="game.id", index=True)
    ply: int
    cp_loss: int
    phase: str  # "opening" | "middlegame" | "endgame"
    severity: str  # "ok" | "inaccuracy" | "mistake" | "blunder"


class Report(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    player_id: int = Field(foreign_key="player.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    status: str = Field(default="queued")  # queued|fetching|analyzing|done|failed
    progress: int = Field(default=0)  # 0-100
    total_new: int | None = None
    analyzed_new: int | None = None
    # JSON column (§4): native JSON on Postgres, TEXT-backed on SQLite. Null until done.
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    rq_job_id: str | None = None
    error: str | None = None
