"""Aggregate analyzed games into the report payload (§6.4).

Pure functions only: :func:`build_report` takes plain analyzed-game objects and
returns the exact JSON-shaped payload the frontend consumes. No DB or engine
access lives here — the caller (jobs.py) loads games + stored MoveEvals and
passes them in.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypedDict

# --------------------------------------------------------------------------- #
# Payload shapes (§6.4) — TypedDicts mirror the exact JSON schema
# --------------------------------------------------------------------------- #


class WinRateColor(TypedDict):
    win: int
    loss: int
    draw: int


class PhaseErrors(TypedDict):
    blunders: int
    mistakes: int
    inaccuracies: int


class OpeningSummary(TypedDict):
    name: str
    eco: str
    games: int
    score_pct: float


class TrendPoint(TypedDict):
    game_index: int
    played_at: str
    avg_cp_loss: float
    result: str


class BlunderBucket(TypedDict):
    move_bucket: str
    count: int


class SignatureLeak(TypedDict):
    headline: str
    detail: str


class ReportPayload(TypedDict):
    username: str
    generated_at: str
    games_analyzed: int
    win_rate: dict[str, WinRateColor]
    errors_by_phase: dict[str, PhaseErrors]
    top_openings: list[OpeningSummary]
    accuracy_trend: list[TrendPoint]
    blunder_distribution_by_move: list[BlunderBucket]
    signature_leak: SignatureLeak


# --------------------------------------------------------------------------- #
# Input data shapes (plain, DB-agnostic)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MoveEvalInput:
    """One analyzed subject move (mirrors a MoveEval row, §4)."""

    ply: int
    cp_loss: int
    phase: str  # "opening" | "middlegame" | "endgame"
    severity: str  # "ok" | "inaccuracy" | "mistake" | "blunder"


@dataclass(frozen=True)
class GameInput:
    """One analyzed game plus the subject's move evals (§4)."""

    color: str  # "white" | "black"
    result: str  # "win" | "loss" | "draw"
    played_at: datetime
    opening_name: str | None
    opening_eco: str | None
    move_evals: Sequence[MoveEvalInput] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_BUCKET_SIZE = 5  # full-move buckets for blunder distribution (§6.4)
_PHASES = ("opening", "middlegame", "endgame")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _fullmove(ply: int) -> int:
    """0-based ply -> 1-based full-move number."""
    return ply // 2 + 1


def _bucket_label_for_index(index: int) -> str:
    """0-based bucket index -> 5-move bucket label, e.g. 1 -> "6-10"."""
    start = index * _BUCKET_SIZE + 1
    end = start + _BUCKET_SIZE - 1
    return f"{start}-{end}"


def _bucket_label(fullmove: int) -> str:
    """Full-move number -> 5-move bucket label, e.g. 7 -> "6-10"."""
    return _bucket_label_for_index((fullmove - 1) // _BUCKET_SIZE)


def _round1(value: float) -> float:
    return round(value, 1)


def _avg_cp_loss(evals: Sequence[MoveEvalInput]) -> float:
    if not evals:
        return 0.0
    return sum(e.cp_loss for e in evals) / len(evals)


def _score_pct(wins: int, draws: int, games: int) -> float:
    if games == 0:
        return 0.0
    return (wins + 0.5 * draws) / games * 100


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #


def _win_rate(games: Sequence[GameInput]) -> dict[str, WinRateColor]:
    rate: dict[str, WinRateColor] = {
        "white": {"win": 0, "loss": 0, "draw": 0},
        "black": {"win": 0, "loss": 0, "draw": 0},
    }
    for game in games:
        if game.color in rate and game.result in ("win", "loss", "draw"):
            rate[game.color][game.result] += 1  # type: ignore[literal-required]
    return rate


def _errors_by_phase(games: Sequence[GameInput]) -> dict[str, PhaseErrors]:
    errors: dict[str, PhaseErrors] = {
        phase: {"blunders": 0, "mistakes": 0, "inaccuracies": 0} for phase in _PHASES
    }
    key = {"blunder": "blunders", "mistake": "mistakes", "inaccuracy": "inaccuracies"}
    for game in games:
        for ev in game.move_evals:
            if ev.phase in errors and ev.severity in key:
                errors[ev.phase][key[ev.severity]] += 1  # type: ignore[literal-required]
    return errors


@dataclass
class _OpeningStat:
    name: str
    eco: str
    games: int = 0
    wins: int = 0
    draws: int = 0
    cp_loss_sum: float = 0.0
    move_count: int = 0

    @property
    def score_pct(self) -> float:
        return _score_pct(self.wins, self.draws, self.games)

    @property
    def avg_cp_loss(self) -> float:
        if self.move_count == 0:
            return 0.0
        return self.cp_loss_sum / self.move_count


def _opening_stats(games: Sequence[GameInput]) -> list[_OpeningStat]:
    stats: dict[str, _OpeningStat] = {}
    for game in games:
        name = game.opening_name or "Unknown"
        eco = game.opening_eco or ""
        stat = stats.get(name)
        if stat is None:
            stat = _OpeningStat(name=name, eco=eco)
            stats[name] = stat
        stat.games += 1
        if game.result == "win":
            stat.wins += 1
        elif game.result == "draw":
            stat.draws += 1
        stat.cp_loss_sum += sum(e.cp_loss for e in game.move_evals)
        stat.move_count += len(game.move_evals)
    return list(stats.values())


def _top_openings(stats: Sequence[_OpeningStat]) -> list[OpeningSummary]:
    ranked = sorted(stats, key=lambda s: (-s.games, s.name))[:3]
    return [
        OpeningSummary(
            name=s.name,
            eco=s.eco,
            games=s.games,
            score_pct=_round1(s.score_pct),
        )
        for s in ranked
    ]


def _accuracy_trend(games: Sequence[GameInput]) -> list[TrendPoint]:
    ordered = sorted(games, key=lambda g: g.played_at)
    return [
        TrendPoint(
            game_index=index,
            played_at=game.played_at.isoformat(),
            avg_cp_loss=_round1(_avg_cp_loss(game.move_evals)),
            result=game.result,
        )
        for index, game in enumerate(ordered)
    ]


def _blunder_distribution(games: Sequence[GameInput]) -> list[BlunderBucket]:
    counts: dict[str, int] = {}
    max_fullmove = 0
    for game in games:
        for ev in game.move_evals:
            if ev.severity == "blunder":
                fullmove = _fullmove(ev.ply)
                label = _bucket_label(fullmove)
                counts[label] = counts.get(label, 0) + 1
                max_fullmove = max(max_fullmove, fullmove)

    num_buckets = max((max_fullmove - 1) // _BUCKET_SIZE + 1, 1) if max_fullmove else 0
    distribution: list[BlunderBucket] = []
    for index in range(num_buckets):
        label = _bucket_label_for_index(index)
        distribution.append(BlunderBucket(move_bucket=label, count=counts.get(label, 0)))
    return distribution


# --------------------------------------------------------------------------- #
# Signature leak (§6.4)
# --------------------------------------------------------------------------- #

_TEMPLATES: dict[int, list[str]] = {
    1: [
        "You've played the {opening} {games} times. You've won {wins}.",
        "The {opening}: {games} games, {wins} wins. Consider other hobbies.",
    ],
    2: [
        "The {opening} costs you {extra} centipawns per move above your baseline. "
        "It is not your friend.",
        "Every {opening} move bleeds {extra} extra centipawns. The line is a leak.",
    ],
    3: [
        "Moves {bucket} are where your games go to die: {pct}% of your blunders live there.",
        "{pct}% of your blunders happen on moves {bucket}. Slow down there.",
    ],
    4: [
        "As {color}, you win {pct}%. As {other_color}, {other_pct}%. The pieces are the same ones.",
        "You win {pct}% as {color} and {other_pct}% as {other_color}. One of these is a problem.",
    ],
    5: [
        "Over half your blunders — {pct}% — happen in the {phase}. That is a pattern.",
        "The {phase} holds {pct}% of your blunders. It is not a coincidence.",
    ],
    6: [
        "Your worst opening is the {opening}, averaging {avg} centipawns lost per move.",
        "The {opening} is your leakiest line at {avg} centipawns lost per move.",
    ],
}

_DEFAULT_LEAK: SignatureLeak = {
    "headline": "Not enough games to find a signature leak yet.",
    "detail": "Play a few more rapid or blitz games and check back.",
}


def _pick(rule: int, rng: random.Random) -> str:
    return rng.choice(_TEMPLATES[rule])


def _leak_opening_low_score(
    stats: Sequence[_OpeningStat], rng: random.Random
) -> SignatureLeak | None:
    for s in sorted(stats, key=lambda x: (x.score_pct, -x.games)):
        if s.games >= 5 and s.score_pct <= 35:
            headline = _pick(1, rng).format(opening=s.name, games=s.games, wins=s.wins)
            detail = (
                f"{s.name}: {s.games} games, {s.wins} wins, "
                f"{s.draws} draws — {_round1(s.score_pct)}% score."
            )
            return {"headline": headline, "detail": detail}
    return None


def _leak_opening_high_cploss(
    stats: Sequence[_OpeningStat], overall_avg: float, rng: random.Random
) -> SignatureLeak | None:
    if overall_avg <= 0:
        return None
    for s in sorted(stats, key=lambda x: -x.avg_cp_loss):
        if s.games >= 4 and s.avg_cp_loss >= 1.5 * overall_avg:
            extra = _round1(s.avg_cp_loss - overall_avg)
            headline = _pick(2, rng).format(opening=s.name, extra=extra)
            detail = (
                f"The {s.name} averages {_round1(s.avg_cp_loss)} centipawns lost per move "
                f"vs your {_round1(overall_avg)} overall."
            )
            return {"headline": headline, "detail": detail}
    return None


def _leak_blunder_bucket(
    distribution: Sequence[BlunderBucket], rng: random.Random
) -> SignatureLeak | None:
    total = sum(b["count"] for b in distribution)
    if total == 0:
        return None
    for b in sorted(distribution, key=lambda x: -x["count"]):
        count = b["count"]
        pct = count / total * 100
        if pct > 35:
            bucket = b["move_bucket"]
            headline = _pick(3, rng).format(bucket=bucket, pct=_round1(pct))
            detail = f"{count} of {total} blunders ({_round1(pct)}%) fall in moves {bucket}."
            return {"headline": headline, "detail": detail}
    return None


def _leak_color_gap(win_rate: dict[str, WinRateColor], rng: random.Random) -> SignatureLeak | None:
    def games_of(color: str) -> int:
        c = win_rate[color]
        return c["win"] + c["loss"] + c["draw"]

    def win_pct(color: str) -> float:
        total = games_of(color)
        return win_rate[color]["win"] / total * 100 if total else 0.0

    white_games, black_games = games_of("white"), games_of("black")
    if white_games < 8 or black_games < 8:
        return None
    white_pct, black_pct = win_pct("white"), win_pct("black")
    if abs(white_pct - black_pct) < 15:
        return None
    if white_pct >= black_pct:
        color, other, pct, other_pct = "white", "black", white_pct, black_pct
    else:
        color, other, pct, other_pct = "black", "white", black_pct, white_pct
    headline = _pick(4, rng).format(
        color=color, other_color=other, pct=_round1(pct), other_pct=_round1(other_pct)
    )
    detail = (
        f"White: {_round1(white_pct)}% win rate over {white_games} games. "
        f"Black: {_round1(black_pct)}% over {black_games}."
    )
    return {"headline": headline, "detail": detail}


def _leak_phase_blunders(
    errors_by_phase: dict[str, PhaseErrors], rng: random.Random
) -> SignatureLeak | None:
    per_phase = {phase: errors_by_phase[phase]["blunders"] for phase in _PHASES}
    total = sum(per_phase.values())
    if total == 0:
        return None
    worst_phase = max(per_phase, key=lambda p: per_phase[p])
    pct = per_phase[worst_phase] / total * 100
    if pct <= 50:
        return None
    headline = _pick(5, rng).format(phase=worst_phase, pct=_round1(pct))
    count = per_phase[worst_phase]
    detail = f"{count} of {total} blunders ({_round1(pct)}%) are in the {worst_phase}."
    return {"headline": headline, "detail": detail}


def _leak_fallback(stats: Sequence[_OpeningStat], rng: random.Random) -> SignatureLeak | None:
    eligible = [s for s in stats if s.games >= 3 and s.move_count > 0]
    if not eligible:
        return None
    s = max(eligible, key=lambda x: x.avg_cp_loss)
    headline = _pick(6, rng).format(opening=s.name, avg=_round1(s.avg_cp_loss))
    detail = (
        f"The {s.name} averages {_round1(s.avg_cp_loss)} centipawns lost per move "
        f"across {s.games} games."
    )
    return {"headline": headline, "detail": detail}


def _signature_leak(
    stats: Sequence[_OpeningStat],
    overall_avg: float,
    distribution: Sequence[BlunderBucket],
    win_rate: dict[str, WinRateColor],
    errors_by_phase: dict[str, PhaseErrors],
    rng: random.Random,
) -> SignatureLeak:
    """Pick the single strongest finding by the §6.4 priority order."""
    for finding in (
        _leak_opening_low_score(stats, rng),
        _leak_opening_high_cploss(stats, overall_avg, rng),
        _leak_blunder_bucket(distribution, rng),
        _leak_color_gap(win_rate, rng),
        _leak_phase_blunders(errors_by_phase, rng),
        _leak_fallback(stats, rng),
    ):
        if finding is not None:
            return finding
    return SignatureLeak(headline=_DEFAULT_LEAK["headline"], detail=_DEFAULT_LEAK["detail"])


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_report(
    username: str,
    games: Sequence[GameInput],
    *,
    generated_at: datetime | None = None,
    rng: random.Random | None = None,
) -> ReportPayload:
    """Aggregate analyzed games into the §6.4 payload.

    Returns exactly the keys in §6.4 — no more, no fewer. Works with fewer than
    30 games and with zero games (empty sections, default signature leak).
    """
    rng = rng or random.Random()
    generated_at = generated_at or datetime.now(UTC)

    win_rate = _win_rate(games)
    errors_by_phase = _errors_by_phase(games)
    stats = _opening_stats(games)
    top_openings = _top_openings(stats)
    accuracy_trend = _accuracy_trend(games)
    blunder_distribution = _blunder_distribution(games)

    all_evals = [ev for g in games for ev in g.move_evals]
    overall_avg = _avg_cp_loss(all_evals)

    signature_leak = _signature_leak(
        stats, overall_avg, blunder_distribution, win_rate, errors_by_phase, rng
    )

    return ReportPayload(
        username=username,
        generated_at=generated_at.isoformat(),
        games_analyzed=len(games),
        win_rate=win_rate,
        errors_by_phase=errors_by_phase,
        top_openings=top_openings,
        accuracy_trend=accuracy_trend,
        blunder_distribution_by_move=blunder_distribution,
        signature_leak=signature_leak,
    )
