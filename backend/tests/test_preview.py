"""Tests for the bot-served unfurl preview route (Phase 4b follow-up).

Link-unfurl bots don't run JS, so the backend serves a minimal HTML page with the
real og:image + headline for known bot User-Agents. Browsers must never be
intercepted by this route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

BOT_UAS = [
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Twitterbot/1.0",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "WhatsApp/2.23.20.0 A",
    "TelegramBot (like TwitterBot)",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0; Jakarta Commons-HttpClient/4.3 +https://www.linkedin.com)",
]

NON_BOT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def _payload_dump(username: str = "touatizh") -> dict:
    return {
        "username": username,
        "generated_at": "2026-08-02T05:39:40+00:00",
        "games_analyzed": 30,
        "win_rate": {
            "white": {"win": 11, "loss": 4, "draw": 2},
            "black": {"win": 6, "loss": 5, "draw": 2},
        },
        "errors_by_phase": {
            "opening": {"blunders": 1, "mistakes": 2, "inaccuracies": 3},
            "middlegame": {"blunders": 4, "mistakes": 5, "inaccuracies": 6},
            "endgame": {"blunders": 0, "mistakes": 1, "inaccuracies": 2},
        },
        "top_openings": [
            {"name": "Sicilian, Najdorf", "eco": "B90", "games": 11, "score_pct": 18.0}
        ],
        "accuracy_trend": [],
        "blunder_distribution_by_move": [],
        "signature_leak": {
            "headline": "You've played the Najdorf 11 times. You've won twice.",
            "detail": "1.83 avg cp loss above your baseline in this line · sample: 11 games",
            "glyph": "?",
        },
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.models  # noqa: F401 — register tables on SQLModel.metadata

    url = f"sqlite:///{tmp_path}/preview.db"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    import app.db as app_db

    monkeypatch.setattr(app_db, "engine", engine)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _seed_done_report(username: str = "touatizh") -> int:
    from sqlmodel import Session

    import app.db as app_db
    from app.models import Player, Report

    report_id: int | None = None
    with Session(app_db.engine) as s:
        player = Player(username=username)
        s.add(player)
        s.commit()
        s.refresh(player)
        report = Report(
            player_id=player.id,
            status="done",
            progress=100,
            payload=_payload_dump(username),
        )
        s.add(report)
        s.commit()
        s.refresh(report)
        report_id = report.id
    assert report_id is not None
    return report_id


@pytest.mark.parametrize("ua", BOT_UAS)
def test_bot_gets_report_preview(client: TestClient, ua: str) -> None:
    rid = _seed_done_report()
    resp = client.get("/report/touatizh", headers={"User-Agent": ua})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert f"/api/reports/{rid}/og-image" in body
    assert "Najdorf 11 times" in body  # headline (apostrophes are HTML-escaped)
    assert "touatizh — Adjudication Report" in body


def test_bot_unknown_username_gets_generic_card_not_404(client: TestClient) -> None:
    resp = client.get("/report/ghostuser", headers={"User-Agent": BOT_UAS[0]})
    assert resp.status_code == 200
    assert "Chess Insights" in resp.text  # generic site card (apostrophe escaped)
    assert "/api/reports/" not in resp.text  # no per-report image in fallback


def test_bot_preview_tolerates_legacy_payload_without_glyph(client: TestClient) -> None:
    """Reports stored before the glyph feature have no signature_leak.glyph.
    The unfurl must still render (read the headline defensively), not 500."""
    from sqlmodel import Session

    import app.db as app_db
    from app.models import Player, Report

    legacy = _payload_dump("legacyuser")
    del legacy["signature_leak"]["glyph"]  # simulate a pre-glyph payload

    report_id: int | None = None
    with Session(app_db.engine) as s:
        player = Player(username="legacyuser")
        s.add(player)
        s.commit()
        s.refresh(player)
        report = Report(player_id=player.id, status="done", progress=100, payload=legacy)
        s.add(report)
        s.commit()
        s.refresh(report)
        report_id = report.id

    resp = client.get("/report/legacyuser", headers={"User-Agent": BOT_UAS[0]})
    assert resp.status_code == 200
    assert f"/api/reports/{report_id}/og-image" in resp.text
    assert "Najdorf 11 times" in resp.text


def test_non_bot_is_not_intercepted(client: TestClient) -> None:
    rid = _seed_done_report()
    resp = client.get("/report/touatizh", headers={"User-Agent": NON_BOT_UA})
    # A browser must never get the bot HTML page — 404 keeps the route out of
    # the way (Phase 5 nginx routes only bot UAs here anyway).
    assert resp.status_code == 404
    assert f"/api/reports/{rid}/og-image" not in resp.text
