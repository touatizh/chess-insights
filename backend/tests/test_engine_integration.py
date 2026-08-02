"""Integration test running a real Stockfish binary (§ Phase 2).

Marked ``@pytest.mark.engine`` so it is excluded from ``pytest -m "not engine"``.
Run with a Stockfish binary available (``STOCKFISH_PATH`` or /usr/games/stockfish):

    pytest -m engine
"""

from __future__ import annotations

import io
import os
import shutil

import chess
import chess.pgn
import pytest

from app.analysis import engine

pytestmark = pytest.mark.engine

# A short game (Fried Liver-ish) that ends with white blundering the queen.
# 10. Qg4?? hangs the queen to ...Bxg4 / the position collapses for white.
FIXTURE_PGN = """[Event "Fixture"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "Blunderer"]
[Black "Opponent"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Nxd5 6. Qg4 0-1
"""


def _binary_available() -> bool:
    path = engine.stockfish_path()
    return shutil.which(path) is not None or os.path.exists(path)


@pytest.mark.skipif(not _binary_available(), reason="Stockfish binary not found")
def test_stockfish_flags_queen_blunder_on_fixture_pgn() -> None:
    game = chess.pgn.read_game(io.StringIO(FIXTURE_PGN))
    assert game is not None

    # Reconstruct the SAN move string from the mainline (as stored on Game.moves).
    san_tokens: list[str] = []
    walk = game.board()
    for move in game.mainline_moves():
        san_tokens.append(walk.san(move))
        walk.push(move)
    moves_san = " ".join(san_tokens)

    subject = chess.WHITE  # the blunderer
    with engine.open_engine() as eng:
        analyses = engine.analyze_game(eng, moves_san, subject, depth=12)

    assert analyses, "expected at least one subject move analyzed"

    # The last white move (Qg4, ply 10) must be flagged as a blunder. Severity
    # and phase come straight off MoveAnalysis — no game replay needed.
    last = analyses[-1]
    assert last.ply == 10
    assert last.cp_loss >= 300
    assert last.severity == "blunder"
    assert last.phase == "opening"  # ply 10 -> full-move 6

    # And a quiet early move should not be a blunder.
    first = analyses[0]
    assert first.severity in {"ok", "inaccuracy"}
