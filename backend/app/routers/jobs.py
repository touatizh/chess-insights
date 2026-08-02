"""Featured reports endpoint (§5).

Per the §3 file layout, ``/api/featured`` lives here. It returns pre-generated
demo reports flagged ``featured`` (Phase 5 seeds these). In dev this is empty
until seeds exist — no fake data.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.db import featured_reports, session_scope
from app.models import Player
from app.schemas import FeaturedReportItem, FeaturedReportsResponse

router = APIRouter(prefix="/api", tags=["featured"])


@router.get("/featured", response_model=FeaturedReportsResponse)
def get_featured() -> FeaturedReportsResponse:
    """List pre-generated demo reports for the homepage (§5)."""
    items: list[FeaturedReportItem] = []
    with session_scope() as session:
        for report in featured_reports(session):
            player = session.get(Player, report.player_id)
            if player is None or report.id is None:
                continue
            items.append(FeaturedReportItem(username=player.username, report_id=str(report.id)))
    return FeaturedReportsResponse(featured=items)
