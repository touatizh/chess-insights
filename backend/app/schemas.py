"""Pydantic schemas for the HTTP API (§5) and the analysis payload (§6.4).

The payload schema mirrors the §6.4 JSON structure exactly — typed via Pydantic
so the FastAPI router returns consistent, validated responses.
"""

from __future__ import annotations

from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Payload shapes (§6.4) — Pydantic versions of the TypedDicts
# --------------------------------------------------------------------------- #


class WinRateColor(BaseModel):
    win: int
    loss: int
    draw: int


class PhaseErrors(BaseModel):
    blunders: int
    mistakes: int
    inaccuracies: int


class OpeningSummary(BaseModel):
    name: str
    eco: str
    games: int
    score_pct: float


class WorstMove(BaseModel):
    ply: int
    san: str
    cp_loss: int
    severity: str  # "mistake" | "blunder"


class TrendPoint(BaseModel):
    game_index: int
    played_at: str
    avg_cp_loss: float
    result: str
    worst_move: WorstMove | None = None


class BlunderBucket(BaseModel):
    move_bucket: str
    count: int


class SignatureLeak(BaseModel):
    headline: str
    detail: str
    glyph: str  # "?" or "!" only


class ReportPayload(BaseModel):
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
# API response shapes (§5)
# --------------------------------------------------------------------------- #


class ReportRequest(BaseModel):
    username: str


class ReportCreateResponse(BaseModel):
    report_id: str
    status: str  # "done" (cached) or "queued"


class ReportStatusResponse(BaseModel):
    status: str  # "queued", "fetching", "analyzing", "done", "failed"
    progress: int  # 0–100
    queue_position: int | None
    payload: ReportPayload | None
    error: str | None
    username: str
    # Exact analysis counters for the "Analyzing new games… N/M" label.
    total_new: int | None = None
    analyzed_new: int | None = None


class ReportByUsernameResponse(BaseModel):
    payload: ReportPayload | None
    games_analyzed: int | None


class FeaturedReportItem(BaseModel):
    username: str
    report_id: str
    # Verdict preview for the home-page case tabs (design guide "Layout — Home
    # page"). Pulled from the cached payload's signature_leak.
    verdict_headline: str
    verdict_glyph: str  # "?" or "!"


class FeaturedReportsResponse(BaseModel):
    featured: list[FeaturedReportItem]
