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
    """List pre-generated demo reports for the homepage (§5).

    Each item carries the verdict headline + glyph from the cached payload so the
    home-page case tabs can show a real preview (design guide "Layout — Home").
    """
    items: list[FeaturedReportItem] = []
    with session_scope() as session:
        for report in featured_reports(session):
            player = session.get(Player, report.player_id)
            if player is None or report.id is None or not report.payload:
                continue
            leak = report.payload.get("signature_leak") or {}
            items.append(
                FeaturedReportItem(
                    username=player.username,
                    report_id=str(report.id),
                    verdict_headline=leak.get("headline", ""),
                    verdict_glyph=leak.get("glyph", "?"),
                )
            )
    return FeaturedReportsResponse(featured=items)
