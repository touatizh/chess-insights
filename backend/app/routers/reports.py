"""Report endpoints (§5).

POST /api/reports          create (freshness cache-hit 200 / queued or in-progress 202 / 404 / 429)
GET  /api/reports/{id}     status + payload + honest queue_position
GET  /api/reports/by-username/{username}   latest done report payload
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import Response as FastAPIResponse
from sqlmodel import Session

from app.db import (
    create_report,
    get_or_create_player,
    get_player_id,
    get_report,
    has_game,
    latest_active_report,
    latest_done_report,
    session_scope,
)
from app.jobs import run_report_job
from app.lichess import (
    LichessError,
    LichessRateLimitError,
    LichessUserNotFoundError,
    fetch_latest_game_id,
)
from app.models import Player
from app.og import render_og_card
from app.queue import JOB_TIMEOUT, get_queue, queue_position
from app.ratelimit import allow_freshness, allow_new_job
from app.schemas import (
    ReportByUsernameResponse,
    ReportCreateResponse,
    ReportPayload,
    ReportRequest,
    ReportStatusResponse,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("", response_model=ReportCreateResponse)
def create_report_endpoint(
    body: ReportRequest, request: Request, response: Response
) -> ReportCreateResponse:
    """Create (or return a cached) report for a username (§4 freshness, §5)."""
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="username is required")

    ip = _client_ip(request)

    # Freshness check first (§4): the single most recent game. This costs one
    # Lichess call, hence its own looser per-IP cap.
    if not allow_freshness(ip):
        raise HTTPException(status_code=429, detail="Too many requests; slow down.")

    try:
        latest_lichess_id = fetch_latest_game_id(username)
    except LichessUserNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Lichess user '{username}' not found."
        ) from exc
    except LichessRateLimitError as exc:
        raise HTTPException(status_code=429, detail="Lichess is busy; try again shortly.") from exc
    except LichessError as exc:
        raise HTTPException(
            status_code=502, detail="Lichess request failed; try again later."
        ) from exc

    with session_scope() as session:
        player_id, _ = get_or_create_player(username, session)

        # Cache hit: newest game already stored + a done report exists → return it.
        if latest_lichess_id and has_game(session, player_id, latest_lichess_id):
            done = latest_done_report(session, player_id)
            if done is not None:
                response.status_code = status.HTTP_200_OK
                return ReportCreateResponse(report_id=str(done.id), status="done")

        # Already in progress: instead of a bare 409 (which stranded the client
        # with no report id to resume — the re-file loop bug), hand back the
        # existing report so the frontend can resume live polling. Mirrors the
        # cached-`done` return above. Reports older than the job timeout are
        # treated as abandoned (crashed worker) so a stuck report never locks the
        # username out permanently.
        active = latest_active_report(session, player_id, stale_after_seconds=JOB_TIMEOUT)
        if active is not None and active.id is not None:
            response.status_code = status.HTTP_202_ACCEPTED
            return ReportCreateResponse(report_id=str(active.id), status=active.status)

        # New job: enforce the stricter per-IP job cap.
        if not allow_new_job(ip):
            raise HTTPException(
                status_code=429, detail="Report limit reached; try again in an hour."
            )

        report = create_report(session, player_id, status="queued", progress=0)
        report_id = report.id
        assert report_id is not None

        queue = get_queue()
        job = queue.enqueue(run_report_job, report_id, username)
        report.rq_job_id = job.id
        session.add(report)
        session.commit()

        response.status_code = status.HTTP_202_ACCEPTED
        return ReportCreateResponse(report_id=str(report_id), status="queued")


@router.get("/by-username/{username}", response_model=ReportByUsernameResponse)
def report_by_username(username: str) -> ReportByUsernameResponse:
    """Latest done report payload for a username, 404 if none (§5)."""
    with session_scope() as session:
        # Read-only: never create a player row on a GET.
        player_id = get_player_id(username, session)
        if player_id is None:
            raise HTTPException(status_code=404, detail="No report found for this user.")
        done = latest_done_report(session, player_id)
        if done is None or done.payload is None:
            raise HTTPException(status_code=404, detail="No report found for this user.")
        games_analyzed = done.payload.get("games_analyzed")
        return ReportByUsernameResponse(
            payload=done.payload,  # type: ignore[arg-type]
            games_analyzed=games_analyzed,
        )


@router.get("/{report_id}/og-image")
def report_og_image(report_id: int) -> FastAPIResponse:
    """1200×630 PNG share card for a done report (Phase 4b, growth loop).

    404 if the report doesn't exist or isn't done. Cached aggressively — a
    report's payload never changes once done, so the card is immutable.
    """
    with session_scope() as session:
        report = get_report(session, report_id)
        if report is None or report.status != "done" or report.payload is None:
            raise HTTPException(status_code=404, detail="No card available for this report.")
        payload = ReportPayload(**report.payload)

    png = render_og_card(payload, report_id)
    return FastAPIResponse(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/{report_id}", response_model=ReportStatusResponse)
def report_status(report_id: int) -> ReportStatusResponse:
    """Report status + progress + honest queue_position (§5)."""
    with session_scope() as session:
        report = get_report(session, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found.")

        position: int | None = None
        if report.status == "queued" and report.rq_job_id:
            position = queue_position(get_queue(), report.rq_job_id)

        username = _username_for_report(session, report.player_id)

        return ReportStatusResponse(
            status=report.status,
            progress=report.progress,
            queue_position=position,
            payload=report.payload,  # type: ignore[arg-type]
            error=report.error,
            username=username,
            total_new=report.total_new,
            analyzed_new=report.analyzed_new,
        )


def _username_for_report(session: Session, player_id: int) -> str:
    player = session.get(Player, player_id)
    return player.username if player else ""
