"""Unit tests for report.py aggregation + signature-leak priority (§6.4).

All canned data — no engine.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.analysis.report import (
    GameInput,
    MoveEvalInput,
    build_report,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)

EXPECTED_KEYS = {
    "username",
    "generated_at",
    "games_analyzed",
    "win_rate",
    "errors_by_phase",
    "top_openings",
    "accuracy_trend",
    "blunder_distribution_by_move",
    "signature_leak",
}


def _rng() -> random.Random:
    return random.Random(1234)


def _game(
    *,
    color: str = "white",
    result: str = "win",
    opening: str = "Italian Game",
    eco: str = "C50",
    offset_minutes: int = 0,
    evals: list[MoveEvalInput] | None = None,
) -> GameInput:
    return GameInput(
        color=color,
        result=result,
        played_at=BASE + timedelta(minutes=offset_minutes),
        opening_name=opening,
        opening_eco=eco,
        move_evals=tuple(evals or ()),
    )


def _ev(ply: int, cp_loss: int, phase: str = "middlegame") -> MoveEvalInput:
    sev = (
        "blunder"
        if cp_loss >= 300
        else "mistake"
        if cp_loss >= 150
        else "inaccuracy"
        if cp_loss >= 50
        else "ok"
    )
    return MoveEvalInput(ply=ply, cp_loss=cp_loss, phase=phase, severity=sev)


# --------------------------------------------------------------------------- #
# Schema shape (§6.4) — EXACTLY these keys
# --------------------------------------------------------------------------- #


def test_payload_has_exact_keys() -> None:
    report = build_report("Bob", [_game()], rng=_rng())
    assert set(report.keys()) == EXPECTED_KEYS


def test_empty_games_still_valid_shape() -> None:
    report = build_report("Nobody", [], rng=_rng())
    assert set(report.keys()) == EXPECTED_KEYS
    assert report["games_analyzed"] == 0
    assert report["top_openings"] == []
    assert report["accuracy_trend"] == []
    assert report["blunder_distribution_by_move"] == []
    assert set(report["signature_leak"].keys()) == {"headline", "detail"}
    # win_rate + errors_by_phase keep full structure even when empty.
    assert report["win_rate"] == {
        "white": {"win": 0, "loss": 0, "draw": 0},
        "black": {"win": 0, "loss": 0, "draw": 0},
    }
    for phase in ("opening", "middlegame", "endgame"):
        assert report["errors_by_phase"][phase] == {
            "blunders": 0,
            "mistakes": 0,
            "inaccuracies": 0,
        }


def test_win_rate_counts_by_color_and_result() -> None:
    games = [
        _game(color="white", result="win"),
        _game(color="white", result="loss"),
        _game(color="black", result="draw"),
        _game(color="black", result="win"),
    ]
    report = build_report("Bob", games, rng=_rng())
    assert report["win_rate"]["white"] == {"win": 1, "loss": 1, "draw": 0}
    assert report["win_rate"]["black"] == {"win": 1, "loss": 0, "draw": 1}


def test_errors_by_phase_counts() -> None:
    evals = [
        _ev(20, 350, "middlegame"),  # blunder
        _ev(22, 200, "middlegame"),  # mistake
        _ev(24, 60, "endgame"),  # inaccuracy
        _ev(26, 10, "opening"),  # ok -> not counted
    ]
    report = build_report("Bob", [_game(evals=evals)], rng=_rng())
    ebp = report["errors_by_phase"]
    assert ebp["middlegame"] == {"blunders": 1, "mistakes": 1, "inaccuracies": 0}
    assert ebp["endgame"] == {"blunders": 0, "mistakes": 0, "inaccuracies": 1}
    assert ebp["opening"] == {"blunders": 0, "mistakes": 0, "inaccuracies": 0}


def test_top_openings_top_three_by_games_and_score_pct() -> None:
    games = (
        [_game(opening="A", eco="A00", result="win") for _ in range(5)]
        + [_game(opening="B", eco="B00", result="draw") for _ in range(4)]
        + [_game(opening="C", eco="C00", result="loss") for _ in range(3)]
        + [_game(opening="D", eco="D00", result="win") for _ in range(2)]
    )
    report = build_report("Bob", games, rng=_rng())
    top = report["top_openings"]
    assert [o["name"] for o in top] == ["A", "B", "C"]
    assert top[0] == {"name": "A", "eco": "A00", "games": 5, "score_pct": 100.0}
    assert top[1]["score_pct"] == 50.0  # all draws -> 0.5
    assert top[2]["score_pct"] == 0.0


def test_accuracy_trend_is_chronological_with_avg() -> None:
    games = [
        _game(offset_minutes=30, result="loss", evals=[_ev(0, 100), _ev(2, 300)]),
        _game(offset_minutes=10, result="win", evals=[_ev(0, 0), _ev(2, 20)]),
    ]
    report = build_report("Bob", games, rng=_rng())
    trend = report["accuracy_trend"]
    assert [t["game_index"] for t in trend] == [0, 1]
    # Earlier game (offset 10) first.
    assert trend[0]["result"] == "win"
    assert trend[0]["avg_cp_loss"] == 10.0
    assert trend[1]["avg_cp_loss"] == 200.0


def test_blunder_distribution_buckets() -> None:
    evals = [
        _ev(0, 400),  # full-move 1 -> bucket 1-5
        _ev(8, 400),  # full-move 5 -> bucket 1-5
        _ev(10, 400),  # full-move 6 -> bucket 6-10
        _ev(30, 100),  # not a blunder -> excluded
    ]
    report = build_report("Bob", [_game(evals=evals)], rng=_rng())
    dist = report["blunder_distribution_by_move"]
    by_bucket = {d["move_bucket"]: d["count"] for d in dist}
    assert by_bucket["1-5"] == 2
    assert by_bucket["6-10"] == 1


# --------------------------------------------------------------------------- #
# Signature leak priority rules (§6.4)
# --------------------------------------------------------------------------- #


def test_leak_rule1_low_score_opening_wins() -> None:
    # Opening with >=5 games and score <=35%.
    games = [_game(opening="London System", eco="D02", result="loss") for _ in range(6)]
    report = build_report("Bob", games, rng=_rng())
    leak = report["signature_leak"]
    assert "London System" in leak["headline"]
    assert set(leak.keys()) == {"headline", "detail"}


def test_leak_rule2_high_cploss_opening() -> None:
    # No opening triggers rule 1 (make scores healthy), but one opening bleeds
    # >= 1.5x overall avg cp loss with >= 4 games.
    baseline = [
        _game(opening="Baseline", eco="B00", result="win", evals=[_ev(0, 10), _ev(2, 10)])
        for _ in range(6)
    ]
    leaky = [
        _game(opening="Leaky Line", eco="L00", result="win", evals=[_ev(0, 90), _ev(2, 90)])
        for _ in range(4)
    ]
    report = build_report("Bob", baseline + leaky, rng=_rng())
    assert "Leaky Line" in report["signature_leak"]["headline"]


def test_leak_rule3_blunder_bucket() -> None:
    # Avoid rules 1/2: healthy openings, but blunders concentrated in one bucket.
    games = []
    for i in range(4):
        games.append(
            _game(
                opening=f"Op{i}",
                eco="X00",
                result="win",
                evals=[_ev(0, 5), _ev(10, 400), _ev(12, 400)],
            )
        )
    report = build_report("Bob", games, rng=_rng())
    headline = report["signature_leak"]["headline"]
    assert "6-10" in headline


def test_leak_rule4_color_gap() -> None:
    # No opening triggers rule 1 (same opening, ~50% overall score so it never
    # dips to <=35%); no blunders; big win-rate gap by color, >=8 games each.
    games = []
    for _ in range(8):
        games.append(_game(color="white", result="win", opening="Var", evals=[_ev(0, 5)]))
    for _ in range(8):
        games.append(_game(color="black", result="loss", opening="Var", evals=[_ev(0, 5)]))
    report = build_report("Bob", games, rng=_rng())
    headline = report["signature_leak"]["headline"]
    assert "white" in headline.lower() and "black" in headline.lower()


def test_leak_rule5_phase_blunders() -> None:
    # >50% of blunders in one phase (endgame), but blunders spread across many
    # move-buckets so no single bucket exceeds 35% (rule 3 skips). Openings have
    # <5 games (rule 1 skips), healthy cp loss (rule 2 skips), <8 games per color
    # (rule 4 skips). Endgame holds all 4 blunders -> rule 5 fires.
    # Blunder plies chosen so each lands in a distinct 5-full-move bucket.
    blunder_plies = [0, 12, 22, 32]  # full-moves 1, 7, 12, 17 -> buckets 1-5,6-10,11-15,16-20
    games = [
        _game(
            color="white" if i % 2 == 0 else "black",
            result="win",
            opening=f"Op{i}",
            eco="X00",
            evals=[_ev(ply, 400, "endgame")],
        )
        for i, ply in enumerate(blunder_plies)
    ]
    report = build_report("Bob", games, rng=_rng())
    headline = report["signature_leak"]["headline"]
    assert "endgame" in headline


def test_leak_fallback_rule6() -> None:
    # >=3 games one opening, healthy score (rule1 no), cp loss not 1.5x (rule2 no),
    # no blunders (rules 3/5 no), <8 games (rule4 no) -> fallback.
    games = [
        _game(opening="Meh Opening", eco="M00", result="win", evals=[_ev(0, 40), _ev(2, 40)])
        for _ in range(3)
    ]
    report = build_report("Bob", games, rng=_rng())
    headline = report["signature_leak"]["headline"]
    assert "Meh Opening" in headline


def test_leak_default_when_no_data() -> None:
    report = build_report("Nobody", [], rng=_rng())
    assert "enough games" in report["signature_leak"]["headline"].lower()


def test_specificity_rule1_beats_rule5() -> None:
    # Construct data that satisfies BOTH rule 1 (low-score opening) and rule 5
    # (phase blunders). Rule 1 must win (specificity).
    games = [
        _game(
            opening="Bad Opening",
            eco="B00",
            result="loss",
            evals=[_ev(30, 400, "endgame")],
        )
        for _ in range(6)
    ]
    report = build_report("Bob", games, rng=_rng())
    assert "Bad Opening" in report["signature_leak"]["headline"]


def test_templates_vary_with_rng() -> None:
    games = [_game(opening="London System", eco="D02", result="loss") for _ in range(6)]
    headlines = {
        build_report("Bob", games, rng=random.Random(seed))["signature_leak"]["headline"]
        for seed in range(20)
    }
    # At least 2 templates per top rule -> more than one distinct headline.
    assert len(headlines) >= 2
