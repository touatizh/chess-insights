"""Unit tests for classify.py severity + phase heuristics (§6.3). Pure functions."""

from __future__ import annotations

import chess
import pytest

from app.analysis.classify import (
    classify_phase,
    classify_severity,
    non_king_non_pawn_material,
    ply_to_fullmove,
)


@pytest.mark.parametrize(
    ("cp_loss", "expected"),
    [
        (0, "ok"),
        (49, "ok"),
        (50, "inaccuracy"),
        (149, "inaccuracy"),
        (150, "mistake"),
        (299, "mistake"),
        (300, "blunder"),
        (1000, "blunder"),
    ],
)
def test_classify_severity_thresholds(cp_loss: int, expected: str) -> None:
    assert classify_severity(cp_loss) == expected


@pytest.mark.parametrize(
    ("ply", "fullmove"),
    [(0, 1), (1, 1), (2, 2), (3, 2), (18, 10), (19, 10), (20, 11)],
)
def test_ply_to_fullmove(ply: int, fullmove: int) -> None:
    assert ply_to_fullmove(ply) == fullmove


def test_starting_material_is_62() -> None:
    # Both sides: 2Q?? no — 1Q(9)+2R(10)+2B(6)+2N(6) = 31 per side, 62 total.
    board = chess.Board()
    assert non_king_non_pawn_material(board) == 62


def test_bare_kings_and_pawns_material_is_zero() -> None:
    board = chess.Board("4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w - - 0 1")
    assert non_king_non_pawn_material(board) == 0


def test_phase_opening_by_fullmove() -> None:
    # Full board, early ply -> opening regardless of material.
    board = chess.Board()
    assert classify_phase(board, ply=0) == "opening"
    assert classify_phase(board, ply=19) == "opening"  # full-move 10


def test_phase_middlegame_when_material_high_after_opening() -> None:
    board = chess.Board()
    # ply 20 -> full-move 11, full material -> not opening, not endgame.
    assert classify_phase(board, ply=20) == "middlegame"


def test_phase_endgame_low_material_after_opening() -> None:
    # King + rook each = 5 + 5 = 10 <= 13, and past the opening.
    board = chess.Board("4k3/8/8/8/8/8/4R3/r3K3 w - - 0 30")
    assert classify_phase(board, ply=40) == "endgame"


def test_phase_boundary_material_13_is_endgame() -> None:
    # White queen (9) + black bishop+knight (6) = 15 -> middlegame.
    mid = chess.Board("4k3/8/8/5b2/5n2/8/8/3QK3 w - - 0 30")
    assert non_king_non_pawn_material(mid) == 15
    assert classify_phase(mid, ply=40) == "middlegame"
    # White rook (5) + black rook+knight (8) = 13 -> endgame (<= 13).
    end = chess.Board("4k3/8/8/5n2/8/8/8/r3K2R w - - 0 30")
    assert non_king_non_pawn_material(end) == 13
    assert classify_phase(end, ply=40) == "endgame"
