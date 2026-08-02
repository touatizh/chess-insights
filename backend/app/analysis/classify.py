"""Pure classification helpers: severity + game phase (§6.3).

No engine I/O here — everything is a pure function so it can be unit-tested with
canned inputs.
"""

from __future__ import annotations

import chess

# Severity thresholds in centipawns lost (§6.3).
BLUNDER_CP = 300
MISTAKE_CP = 150
INACCURACY_CP = 50

# Phase heuristic constants (§6.3).
OPENING_MAX_FULLMOVE = 10
ENDGAME_MATERIAL_MAX = 13

# Non-king, non-pawn piece values for the endgame material heuristic (§6.3).
_PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.QUEEN: 9,
    chess.ROOK: 5,
    chess.BISHOP: 3,
    chess.KNIGHT: 3,
}


def classify_severity(cp_loss: int) -> str:
    """Map a centipawn loss to a severity label (§6.3).

    blunder ≥ 300, mistake 150–299, inaccuracy 50–149, else ok.
    """
    if cp_loss >= BLUNDER_CP:
        return "blunder"
    if cp_loss >= MISTAKE_CP:
        return "mistake"
    if cp_loss >= INACCURACY_CP:
        return "inaccuracy"
    return "ok"


def non_king_non_pawn_material(board: chess.Board) -> int:
    """Total Q/R/B/N material for both sides in points (§6.3)."""
    total = 0
    for piece_type, value in _PIECE_VALUES.items():
        count = len(board.pieces(piece_type, chess.WHITE))
        count += len(board.pieces(piece_type, chess.BLACK))
        total += count * value
    return total


def ply_to_fullmove(ply: int) -> int:
    """Convert a 0-based ply index to a 1-based full-move number.

    Ply 0 and 1 are full-move 1, ply 2 and 3 are full-move 2, and so on.
    """
    return ply // 2 + 1


def classify_phase(board: chess.Board, ply: int) -> str:
    """Classify the game phase at ``ply`` given the board *before* that move (§6.3).

    opening = full-move ≤ 10; endgame = non-king/non-pawn material ≤ 13 for both
    sides; else middlegame. The opening check takes precedence.
    """
    if ply_to_fullmove(ply) <= OPENING_MAX_FULLMOVE:
        return "opening"
    if non_king_non_pawn_material(board) <= ENDGAME_MATERIAL_MAX:
        return "endgame"
    return "middlegame"
