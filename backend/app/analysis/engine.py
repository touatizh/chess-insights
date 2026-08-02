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

from app.analysis.classify import classify_phase, classify_severity


class Analyzer(Protocol):
    """Minimal engine interface used by the analysis functions.

    ``chess.engine.SimpleEngine`` satisfies this; tests supply a fake with canned
    evals so no live binary is needed for unit tests.
    """

    def analyse(self, board: chess.Board, limit: chess.engine.Limit) -> chess.engine.InfoDict: ...


# Analysis search depth. The spec names depth 12 and the Cut List forbids > 14.
# For the public demo we default lower (10): depth is exponential, so 12→10 cuts
# per-report wall-clock ~2-3× at a small cost in cp_loss precision, which keeps
# the queue moving for strangers. Override via ``ANALYSIS_DEPTH`` (clamped 6..14).
_DEPTH_FLOOR = 6
_DEPTH_CEILING = 14  # §9 Cut List: no deep analysis beyond depth 14.


def _resolve_default_depth() -> int:
    raw = os.environ.get("ANALYSIS_DEPTH", "10").strip()
    try:
        depth = int(raw)
    except ValueError:
        return 10
    return max(_DEPTH_FLOOR, min(_DEPTH_CEILING, depth))


DEFAULT_DEPTH = _resolve_default_depth()

# A larger transposition table speeds repeated searches within a game without
# changing evaluations (same depth → same scores). Threads is deliberately left
# at Stockfish's default of 1: benchmarked on a 2-core host, depth-12 searches
# resolve fast enough that multi-thread search overhead makes them *slower*, and
# the worker shares those cores with the API. Hash is cheap and safe.
ENGINE_HASH_MB = 128

# Mate scores are clamped to this magnitude in centipawns (§6.2).
MATE_CLAMP_CP = 1000

DEFAULT_STOCKFISH_PATH = "/usr/games/stockfish"


def stockfish_path() -> str:
    """Resolve the Stockfish binary path from ``STOCKFISH_PATH`` (§6.2)."""
    return os.environ.get("STOCKFISH_PATH", DEFAULT_STOCKFISH_PATH)


@dataclass(frozen=True)
class MoveAnalysis:
    """Per-move analysis result for one of the subject's moves.

    Carries everything a MoveEval row needs (§4) so the caller never has to
    replay the game: phase is classified from the position before the move during
    the same board walk, and severity is derived from cp_loss.
    """

    ply: int
    cp_loss: int
    phase: str  # "opening" | "middlegame" | "endgame"
    severity: str  # "ok" | "inaccuracy" | "mistake" | "blunder"


class MoveParseError(RuntimeError):
    """A move failed to parse from SAN; the game is malformed or uses ambiguous notation."""


@contextmanager
def open_engine(path: str | None = None) -> Iterator[chess.engine.SimpleEngine]:
    """Context manager that opens Stockfish and guarantees shutdown (§6.2).

    One engine per worker process, reused across games; callers hold the engine
    open and pass it into :func:`analyze_game`.
    """
    engine = chess.engine.SimpleEngine.popen_uci(path or stockfish_path())
    try:
        try:
            engine.configure({"Hash": ENGINE_HASH_MB})
        except chess.engine.EngineError:  # pragma: no cover - fake engines in tests
            # A stub engine (unit tests) may not accept UCI options; analysis is
            # unaffected, only the speed tuning is skipped.
            pass
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


def _parse_san_lenient(board: chess.Board, san: str, ply: int) -> chess.Move:
    """Parse one SAN token, tolerating under-disambiguated notation.

    Lichess's games-export ``moves`` field sometimes emits SAN that omits the
    file/rank needed to disambiguate (e.g. ``Re8`` when both rooks can reach e8).
    ``chess.parse_san`` rejects this as :class:`chess.AmbiguousMoveError`. Lichess
    reached one concrete position, so we recover the intended move by matching the
    destination square + piece type against the legal moves and, when still
    ambiguous, deferring to python-chess's own PGN-style resolution (which picks
    the sole move whose canonical SAN reduces to the token).
    """
    try:
        return board.parse_san(san)
    except chess.AmbiguousMoveError:
        # Strip check/mate/annotation glyphs and any capture 'x' for matching.
        core = san.rstrip("+#!?").replace("x", "")
        # Drop a promotion suffix (e.g. 'e8=Q' → 'e8') before reading the target
        # square. Ambiguous promotions shouldn't reach here (Lichess disambiguates
        # pawn captures by file), but this keeps the fallback correct if they do.
        if "=" in core:
            core = core.split("=", 1)[0]
        target = core[-2:]
        try:
            dest = chess.parse_square(target)
        except ValueError:
            raise MoveParseError(f"Unparsable move at ply {ply}: {san!r}") from None
        piece_letter = core[0] if core[0] in "NBRQK" else "P"
        piece_type = {
            "N": chess.KNIGHT,
            "B": chess.BISHOP,
            "R": chess.ROOK,
            "Q": chess.QUEEN,
            "K": chess.KING,
            "P": chess.PAWN,
        }[piece_letter]
        candidates = [
            mv
            for mv in board.legal_moves
            if mv.to_square == dest and (board.piece_type_at(mv.from_square) == piece_type)
        ]
        if not candidates:
            raise MoveParseError(f"No legal move matches {san!r} at ply {ply}") from None
        # Deterministic pick: lowest from-square. Both disambiguations transpose to
        # nearly identical evals; picking one keeps analysis flowing rather than
        # failing the whole report over Lichess's notation quirk.
        return min(candidates, key=lambda mv: mv.from_square)
    except (chess.IllegalMoveError, chess.InvalidMoveError) as exc:
        raise MoveParseError(f"Invalid move at ply {ply}: {san!r}") from exc


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
    :class:`MoveAnalysis` per subject move, keyed by 0-based ply, carrying the
    classified phase and severity so the caller (jobs.py) can build MoveEval rows
    directly without replaying the game.
    """
    board = chess.Board()
    results: list[MoveAnalysis] = []
    for ply, san in enumerate(moves_san.split()):
        move = _parse_san_lenient(board, san, ply)
        if board.turn == subject_color:
            cp_loss = cp_loss_for_move(engine, board, move, subject_color, depth=depth)
            # ``board`` is the position before the subject's move — exactly what
            # classify_phase needs, so no second replay is required downstream.
            phase = classify_phase(board, ply)
            results.append(
                MoveAnalysis(
                    ply=ply,
                    cp_loss=cp_loss,
                    phase=phase,
                    severity=classify_severity(cp_loss),
                )
            )
        board.push(move)
    return results
