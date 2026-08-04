"""Tests for the OG share-card renderer and endpoint (Phase 4b).

The renderer is pure Pillow (no engine/network), so these run fast and offline.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, create_engine

from app.og import render_og_card
from app.schemas import ReportPayload


def _payload(**overrides) -> ReportPayload:
    base = {
        "username": "touatizh",
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
        "accuracy_trend": [
            {
                "game_index": 0,
                "played_at": "2026-08-01T10:00:00+00:00",
                "avg_cp_loss": 40.0,
                "result": "win",
                "worst_move": None,
            }
        ],
        "blunder_distribution_by_move": [{"move_bucket": "1-5", "count": 1}],
        "signature_leak": {
            "headline": "You've played the Najdorf 11 times. You've won twice.",
            "detail": "1.83 avg cp loss above your baseline in this line · sample: 11 games",
            "glyph": "?",
        },
    }
    base.update(overrides)
    return ReportPayload(**base)


def test_render_og_card_is_1200x630_png() -> None:
    png = render_og_card(_payload(), report_id=42)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert img.size == (1200, 630)


def test_render_og_card_paints_felt_and_paper() -> None:
    """Sanity: felt background at the corner, paper card at the centre."""
    img = Image.open(io.BytesIO(render_og_card(_payload(), report_id=1))).convert("RGB")
    # Corner is felt (green-ish; texture may lighten it slightly).
    r, g, b = img.getpixel((5, 5))
    assert g > r and g > b  # green dominant → felt, not paper/near-black
    # Centre is on the paper card.
    assert img.getpixel((600, 315)) == (237, 230, 211)


def test_render_og_card_handles_long_headline() -> None:
    """A very long verdict must wrap/ellipsize without crashing or overflowing."""
    long_headline = "You " + "keep hanging your queen in the middlegame " * 6
    png = render_og_card(
        _payload(signature_leak={"headline": long_headline, "detail": "x", "glyph": "?"}), 7
    )
    assert Image.open(io.BytesIO(png)).size == (1200, 630)


def test_render_og_card_handles_empty_headline() -> None:
    png = render_og_card(_payload(signature_leak={"headline": "", "detail": "", "glyph": "?"}), 0)
    assert Image.open(io.BytesIO(png)).size == (1200, 630)


def test_render_og_card_uses_signature_glyph() -> None:
    """The card renders whichever glyph the signature leak carries ("?" or "!")."""
    for glyph in ("?", "!"):
        png = render_og_card(
            _payload(signature_leak={"headline": "x", "detail": "y", "glyph": glyph}), 5
        )
        assert Image.open(io.BytesIO(png)).size == (1200, 630)


def _stamp_ink_width(png: bytes) -> int:
    """Horizontal extent of stamp-red ink (the glyph — the only stamp-red
    element on the card). Used to catch glyph doubling: a single "?"/"!" must
    stay well under the width of a doubled "??"/"!!"."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    xs = [x for x in range(img.width) for y in range(img.height) if _is_stamp(img.getpixel((x, y)))]
    return (max(xs) - min(xs)) if xs else 0


def _is_stamp(pixel: tuple[int, int, int]) -> bool:
    # Stamp red #B3402E (179,64,46). The red channel dominates strongly, unlike
    # brass-dim #8C7238 (140,114,56) whose green is closer to red — that keeps
    # the brass bottom-edge stripe out of the measurement.
    r, g, b = pixel
    return abs(r - 179) < 40 and abs(g - 64) < 30 and r > g * 2 and g > b


def test_render_og_card_glyph_is_single_char_not_doubled() -> None:
    """Chess notation is meaningful: "?" (dubious) must not render as "??"
    (blunder), and "!" must not render as "!!". The verdict glyph is drawn
    verbatim — a doubled mark would roughly double the ink width."""
    # Measured single-char widths at glyph size 92: "?" ~50px, "!" ~20px.
    # A doubled mark would be ~101px / ~44px. Assert we're in single-char range.
    for glyph, max_width in (("?", 70), ("!", 34)):
        png = render_og_card(
            _payload(signature_leak={"headline": "x", "detail": "y", "glyph": glyph}), 5
        )
        assert _stamp_ink_width(png) <= max_width


def test_render_og_card_handles_long_unbroken_token() -> None:
    """A single space-less token wider than the card must be char-broken, not
    drawn off the edge (e.g. a very long username echoed in the headline)."""
    headline = "x" * 200
    png = render_og_card(
        _payload(signature_leak={"headline": headline, "detail": "x", "glyph": "?"}), 3
    )
    assert Image.open(io.BytesIO(png)).size == (1200, 630)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.models  # noqa: F401 — register tables on SQLModel.metadata

    url = f"sqlite:///{tmp_path}/og.db"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    import app.db as app_db

    monkeypatch.setattr(app_db, "engine", engine)

    from app.main import app

    return TestClient(app)


def _seed_done_report(payload: ReportPayload) -> int:
    import app.db as app_db

    with app_db.session_scope() as s:
        pid, _ = app_db.get_or_create_player(payload.username, s)
        report = app_db.create_report(s, pid, status="done", progress=100)
        report.payload = payload.model_dump()
        s.add(report)
        s.commit()
        assert report.id is not None
        return report.id


def test_og_endpoint_returns_png(client: TestClient) -> None:
    rid = _seed_done_report(_payload())
    resp = client.get(f"/api/reports/{rid}/og-image")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert "immutable" in resp.headers.get("cache-control", "")
    assert Image.open(io.BytesIO(resp.content)).size == (1200, 630)


def test_og_endpoint_404_for_missing_report(client: TestClient) -> None:
    assert client.get("/api/reports/999999/og-image").status_code == 404


def test_og_endpoint_404_for_non_done_report(client: TestClient) -> None:
    import app.db as app_db

    with app_db.session_scope() as s:
        pid, _ = app_db.get_or_create_player("queuedguy", s)
        report = app_db.create_report(s, pid, status="queued", progress=0)
        s.commit()
        rid = report.id
    assert client.get(f"/api/reports/{rid}/og-image").status_code == 404
