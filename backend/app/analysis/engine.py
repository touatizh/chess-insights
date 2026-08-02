"""Stockfish wrapper (§6.2).

Isolates all engine I/O so the rest of the analysis pipeline stays pure. The
core deliverable is :func:`analyze_game`, which walks a game move-by-move and
computes ``cp_loss`` for each of the subject's moves **from the mover's own
perspective**.

Sign handling is the #1 bug risk here (§6.2). Stockfish always reports scores
from the side-to-move's perspective. We never flip signs manually: we always
project every score into the subject's POV with python-chess
``score.pov(subject_color)``. Before the subject's move it is their turn, after
their move it is the opponent's turn, and ``pov`` normalises both.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

import chess
import chess.engine
import chess.pgn


class Analyzer(Protocol):
    """Minimal engine interface used by the analysis functions.

    ``chess.engine.SimpleEngine`` satisfies this; tests supply a fake with canned
    evals so no live binary is needed for unit tests.
    """

    def analyse(self, board: chess.Board, limit: chess.engine.Limit) -> chess.engine.InfoDict: ...


# Depth 12 per §2/§6.2 (Cut List forbids > depth 14).
DEFAULT_DEPTH = 12

# Mate scores are clamped to this magnitude in centipawns (§6.2).
MATE_CLAMP_CP = 1000

DEFAULT_STOCKFISH_PATH = "/usr/games/stockfish"


def stockfish_path() -> str:
    """Resolve the Stockfish binary path from ``STOCKFISH_PATH`` (§6.2)."""
    return os.environ.get("STOCKFISH_PATH", DEFAULT_STOCKFISH_PATH)


@dataclass(frozen=True)
class MoveAnalysis:
    """Per-move analysis result for one of the subject's moves."""

    ply: int
    cp_loss: int


@contextmanager
def open_engine(path: str | None = None) -> Iterator[chess.engine.SimpleEngine]:
    """Context manager that opens Stockfish and guarantees shutdown (§6.2).

    One engine per worker process, reused across games; callers hold the engine
    open and pass it into :func:`analyze_game`.
    """
    engine = chess.engine.SimpleEngine.popen_uci(path or stockfish_path())
    try:
        yield engine
    finally:
        engine.quit()


def _clamp_cp(value: int) -> int:
    """Clamp a centipawn value to ±:data:`MATE_CLAMP_CP` (§6.2)."""
    if value > MATE_CLAMP_CP:
        return MATE_CLAMP_CP
    if value < -MATE_CLAMP_CP:
        return -MATE_CLAMP_CP
    return value


def score_cp(pov_score: chess.engine.PovScore, subject_color: chess.Color) -> int:
    """Project an engine score into the subject's POV as clamped centipawns.

    Uses ``score.pov(subject_color)`` so signs are handled by python-chess, then
    clamps mate scores to ±1000cp (§6.2).
    """
    relative = pov_score.pov(subject_color)
    mate = relative.mate()
    if mate is not None:
        # Any forced mate clamps to the full ±1000cp magnitude (§6.2).
        return MATE_CLAMP_CP if mate > 0 else -MATE_CLAMP_CP
    cp = relative.score()
    assert cp is not None  # non-mate scores always have a centipawn value
    return _clamp_cp(cp)


def evaluate_position(
    engine: Analyzer,
    board: chess.Board,
    subject_color: chess.Color,
    *,
    depth: int = DEFAULT_DEPTH,
) -> int:
    """Evaluate ``board`` at ``depth`` and return the score in the subject's POV."""
    info = engine.analyse(board, chess.engine.Limit(depth=depth))
    pov_score = info.get("score")
    if pov_score is None:  # pragma: no cover - Stockfish always returns a score
        raise RuntimeError("engine returned no score")
    return score_cp(pov_score, subject_color)


def cp_loss_for_move(
    engine: Analyzer,
    board: chess.Board,
    move: chess.Move,
    subject_color: chess.Color,
    *,
    depth: int = DEFAULT_DEPTH,
) -> int:
    """Compute ``cp_loss`` for a single subject move on ``board``.

    ``cp_loss = max(0, eval_before - eval_after)`` with both evals expressed in
    the subject's POV (§6.2). ``board`` is not mutated.
    """
    eval_before = evaluate_position(engine, board, subject_color, depth=depth)
    board.push(move)
    try:
        eval_after = evaluate_position(engine, board, subject_color, depth=depth)
    finally:
        board.pop()
    return max(0, eval_before - eval_after)


def analyze_game(
    engine: Analyzer,
    moves_san: str,
    subject_color: chess.Color,
    *,
    depth: int = DEFAULT_DEPTH,
) -> list[MoveAnalysis]:
    """Analyze every subject move in a SAN move string.

    ``moves_san`` is the space-separated SAN string stored on ``Game.moves``.
    Only the subject's own moves are evaluated (§4). Returns one
    :class:`MoveAnalysis` per subject move, keyed by 0-based ply.
    """
    board = chess.Board()
    results: list[MoveAnalysis] = []
    for ply, san in enumerate(moves_san.split()):
        move = board.parse_san(san)
        if board.turn == subject_color:
            cp_loss = cp_loss_for_move(engine, board, move, subject_color, depth=depth)
            results.append(MoveAnalysis(ply=ply, cp_loss=cp_loss))
        board.push(move)
    return results
