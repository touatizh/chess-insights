"""Unit tests for engine.py using a FAKE engine with canned evals — no live
Stockfish (§ Phase 2).

The non-negotiable hanging-queen test (§6.2) lives here: it feeds canned
side-to-move scores into the *real* ``cp_loss_for_move`` logic and asserts the
blunder yields cp_loss >= 300 while a quiet good move stays near zero. This
exercises the sign handling (``score.pov``) — the #1 bug risk — without a binary.
"""

from __future__ import annotations

import chess
import chess.engine
import pytest

from app.analysis import engine
from app.analysis.engine import (
    MATE_CLAMP_CP,
    MoveAnalysis,
    analyze_game,
    cp_loss_for_move,
    score_cp,
)


class FakeEngine:
    """Stand-in for SimpleEngine.

    Returns a canned side-to-move PovScore keyed by board FEN. Scores are always
    from the perspective of the side to move (exactly like Stockfish), so the
    ``score.pov`` logic under test does the real work.
    """

    def __init__(self, scores_by_fen: dict[str, chess.engine.PovScore]) -> None:
        self._scores = scores_by_fen
        self.analyse_calls = 0

    def analyse(self, board: chess.Board, limit: chess.engine.Limit) -> chess.engine.InfoDict:
        self.analyse_calls += 1
        return {"score": self._scores[board.fen()]}


def _cp(centipawns: int, turn: chess.Color) -> chess.engine.PovScore:
    """A side-to-move PovScore of ``centipawns`` for the given side to move."""
    return chess.engine.PovScore(chess.engine.Cp(centipawns), turn)


# --------------------------------------------------------------------------- #
# score_cp / clamping
# --------------------------------------------------------------------------- #


def test_score_cp_projects_into_subject_pov() -> None:
    # Side to move is white, +80 for white. Subject white sees +80.
    s = _cp(80, chess.WHITE)
    assert score_cp(s, chess.WHITE) == 80
    # Same score, but subject is black -> -80 in black's POV.
    assert score_cp(s, chess.BLACK) == -80


def test_mate_scores_clamped_to_1000() -> None:
    mate_white = chess.engine.PovScore(chess.engine.Mate(1), chess.WHITE)
    assert score_cp(mate_white, chess.WHITE) == MATE_CLAMP_CP
    assert score_cp(mate_white, chess.BLACK) == -MATE_CLAMP_CP
    mate_against = chess.engine.PovScore(chess.engine.Mate(-2), chess.WHITE)
    assert score_cp(mate_against, chess.WHITE) == -MATE_CLAMP_CP


def test_huge_non_mate_cp_scores_clamped() -> None:
    # A non-mate but overwhelming eval is still clamped to ±1000cp (§6.2).
    big = chess.engine.PovScore(chess.engine.Cp(4000), chess.WHITE)
    assert score_cp(big, chess.WHITE) == MATE_CLAMP_CP
    assert score_cp(big, chess.BLACK) == -MATE_CLAMP_CP


# --------------------------------------------------------------------------- #
# THE hanging-queen test (§6.2) — non-negotiable
# --------------------------------------------------------------------------- #


def test_hanging_queen_blunder_yields_high_cp_loss() -> None:
    """A queen-hang must score cp_loss >= 300; a quiet good move near zero.

    Fried Liver position (white to move). White is roughly +1 (≈ +90cp) here.
    - Quiet good move (Nxf7): position stays ≈ +90cp for white -> tiny cp_loss.
    - Blunder (Qg4??): black wins the queen; after the move it is black to move
      and black is ≈ +800cp (their POV) -> white's POV is ≈ -800cp -> a huge
      swing from +90 to -800.
    """
    board = chess.Board()
    for san in "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Nxd5".split():
        board.push_san(san)
    assert board.turn == chess.WHITE

    good = board.parse_san("Nxf7")
    blunder = board.parse_san("Qg4")

    before_fen = board.fen()

    def after_fen(move: chess.Move) -> str:
        b = board.copy()
        b.push(move)
        return b.fen()

    # Before: white to move, +90cp for white (side to move).
    # After the good move: black to move, black ≈ -90cp (side to move) -> white +90.
    # After the blunder: black to move, black ≈ +800cp (side to move) -> white -800.
    scores = {
        before_fen: _cp(90, chess.WHITE),
        after_fen(good): _cp(-90, chess.BLACK),
        after_fen(blunder): _cp(800, chess.BLACK),
    }
    fake = FakeEngine(scores)

    good_loss = cp_loss_for_move(fake, board, good, chess.WHITE)
    blunder_loss = cp_loss_for_move(fake, board, blunder, chess.WHITE)

    assert blunder_loss >= 300
    assert good_loss < 50
    # Sanity on the exact arithmetic: 90 - (-800) = 890; 90 - 90 = 0.
    assert blunder_loss == 890
    assert good_loss == 0


def test_cp_loss_never_negative_for_improving_move() -> None:
    """If the eval improves after the move, cp_loss floors at 0 (§6.2)."""
    board = chess.Board()
    move = board.parse_san("e4")
    scores = {
        board.fen(): _cp(20, chess.WHITE),
        _after(board, move): _cp(-60, chess.BLACK),  # white +60 after
    }
    fake = FakeEngine(scores)
    assert cp_loss_for_move(fake, board, move, chess.WHITE) == 0


def _after(board: chess.Board, move: chess.Move) -> str:
    b = board.copy()
    b.push(move)
    return b.fen()


# --------------------------------------------------------------------------- #
# analyze_game — only subject moves are evaluated
# --------------------------------------------------------------------------- #


def test_analyze_game_only_scores_subject_moves() -> None:
    """Subject = white: only white's moves (even plies) get analyzed."""
    moves_san = "e4 e5 Nf3 Nc6"  # plies 0,1,2,3
    subject = chess.WHITE

    # Build canned scores for every position white must evaluate.
    scores: dict[str, chess.engine.PovScore] = {}
    walk = chess.Board()
    for san in moves_san.split():
        mv = walk.parse_san(san)
        if walk.turn == subject:
            scores[walk.fen()] = _cp(30, subject)
            after = walk.copy()
            after.push(mv)
            scores[after.fen()] = _cp(-30, not subject)  # subject +30 unchanged
        walk.push(mv)

    fake = FakeEngine(scores)
    results = analyze_game(fake, moves_san, subject)

    assert [r.ply for r in results] == [0, 2]  # white plies only
    assert all(isinstance(r, MoveAnalysis) for r in results)
    assert all(r.cp_loss == 0 for r in results)


def test_analyze_game_black_subject() -> None:
    """Subject = black: only black's moves (odd plies) get analyzed."""
    moves_san = "e4 e5 Nf3 Nc6"
    subject = chess.BLACK

    scores: dict[str, chess.engine.PovScore] = {}
    walk = chess.Board()
    for san in moves_san.split():
        mv = walk.parse_san(san)
        if walk.turn == subject:
            scores[walk.fen()] = _cp(10, subject)
            after = walk.copy()
            after.push(mv)
            scores[after.fen()] = _cp(-10, not subject)
        walk.push(mv)

    fake = FakeEngine(scores)
    results = analyze_game(fake, moves_san, subject)
    assert [r.ply for r in results] == [1, 3]


def test_stockfish_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    assert engine.stockfish_path() == "/usr/games/stockfish"
    monkeypatch.setenv("STOCKFISH_PATH", "/custom/sf")
    assert engine.stockfish_path() == "/custom/sf"
