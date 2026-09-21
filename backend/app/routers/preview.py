"""Bot-served unfurl previews (Phase 4b follow-up).

Link-unfurl bots (Discord, Slack, Twitter, Facebook, WhatsApp, Telegram,
LinkedIn) do not execute JavaScript — they read whatever <meta> tags are in the
raw HTML on first fetch. The SPA injects per-report og:* tags client-side, so
bots always saw the static generic card in ``index.html`` instead of the real
report card.

This route serves a minimal, JS-free HTML page with the report's real og:image
and headline for known bot User-Agents.

Phase 5 note: nginx must match bot User-Agents on ``^/report/`` and proxy those
requests to this backend route instead of serving the static SPA ``index.html``.
Browsers keep hitting the SPA; this route only ever sees bots in production.
"""

from __future__ import annotations

import html
import os
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.db import get_player_id, latest_done_report, session_scope

router = APIRouter(tags=["preview"])

# Case-insensitive substrings that identify link-unfurl bots (§ bot route).
BOT_UA_SUBSTRINGS: Final[tuple[str, ...]] = (
    "discordbot",
    "slackbot",
    "twitterbot",
    "facebookexternalhit",
    "whatsapp",
    "telegrambot",
    "linkedinbot",
)

# Generic site-wide card — mirrors the static og:* block in frontend/index.html.
GENERIC_TITLE = "Chess Insights — The Arbiter's Report"
GENERIC_DESCRIPTION = (
    "Find out what's actually wrong with your chess. A weakness report for any Lichess player."
)

_PAGE_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta property="og:type" content="website" />
<meta property="og:site_name" content="Chess Insights" />
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{description}" />
{url_tag}{image_tags}
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{title}" />
<meta name="twitter:description" content="{description}" />
<meta name="robots" content="noindex" />
</head>
<body></body>
</html>
"""


def _base_url() -> str:
    """The site's public origin, or "" when unconfigured.

    Read per-request rather than at import so deployments (and tests) can set it
    without reloading the module. Trailing slashes are stripped so joining never
    produces a doubled separator.
    """
    return os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _absolute(path: str) -> str:
    """Prefix a root-relative path with the public origin when one is set.

    Unfurl scrapers don't reliably resolve relative og:image URLs, so production
    must serve absolute ones. Falls back to the relative path when
    ``PUBLIC_BASE_URL`` is unset, which keeps local development working.
    """
    base = _base_url()
    return f"{base}{path}" if base else path


def _is_bot(user_agent: str) -> bool:
    """True if the User-Agent identifies a known link-unfurl bot."""
    lowered = user_agent.lower()
    return any(sub in lowered for sub in BOT_UA_SUBSTRINGS)


def _report_page(headline: str, report_id: int, username: str) -> HTMLResponse:
    title = f"{username} — Adjudication Report"
    description = headline or GENERIC_DESCRIPTION
    image = _absolute(f"/api/reports/{report_id}/og-image")
    canonical = _absolute(f"/report/{username}")
    body = _PAGE_TEMPLATE.format(
        title=html.escape(title),
        description=html.escape(description),
        url_tag=f'<meta property="og:url" content="{html.escape(canonical)}" />\n',
        image_tags=(
            f'<meta property="og:image" content="{html.escape(image)}" />\n'
            f'<meta name="twitter:image" content="{html.escape(image)}" />'
        ),
    )
    return HTMLResponse(content=body, status_code=200)


def _generic_page() -> HTMLResponse:
    body = _PAGE_TEMPLATE.format(
        title=html.escape(GENERIC_TITLE),
        description=html.escape(GENERIC_DESCRIPTION),
        url_tag="",  # no canonical URL for the generic fallback
        image_tags="",  # no per-report card for the generic fallback
    )
    return HTMLResponse(content=body, status_code=200)


@router.get("/report/{username}")
def report_preview(username: str, request: Request):
    """Serve a JS-free unfurl page for bots; stay out of the way otherwise."""
    user_agent = request.headers.get("user-agent", "")
    if not _is_bot(user_agent):
        # Browsers should never reach this route (Phase 5 nginx routes only bot
        # UAs here); a non-bot hitting it is an edge case — do not intercept the
        # SPA. 404 with a short plain body.
        return PlainTextResponse("Not found.", status_code=404)

    with session_scope() as session:
        player_id = get_player_id(username, session)
        if player_id is None:
            return _generic_page()
        done = latest_done_report(session, player_id)
        if done is None or done.payload is None or done.id is None:
            return _generic_page()
        # Read the headline defensively from the stored dict rather than
        # validating the whole ReportPayload — legacy payloads (pre-glyph) would
        # otherwise fail validation and 500 the unfurl.
        leak = done.payload.get("signature_leak") or {}
        headline = leak.get("headline", "")
        report_id = done.id

    return _report_page(headline, report_id, username)
