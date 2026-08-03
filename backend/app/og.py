"""Open Graph share-card renderer (Phase 4b).

Renders a 1200×630 PNG "adjudication report" card from a report payload, matching
``design-mockup-og-card.html`` in the felt/paper/brass document language. A link
to ``/report/{username}`` pasted into Discord/Slack must show this styled preview
rather than a blank default — it is the app's growth loop (§7 / design guide).

Pure Pillow, no headless browser: fonts are bundled under ``assets/fonts`` so the
render is deterministic and offline. Falls back to DejaVu if a bundled face is
missing, so the endpoint never crashes over a font path.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.schemas import ReportPayload

# --------------------------------------------------------------------------- #
# Canvas + tokens (exact values from design-mockup-og-card.html)
# --------------------------------------------------------------------------- #

WIDTH, HEIGHT = 1200, 630

FELT = (30, 58, 46)  # #1E3A2E
PAPER = (237, 230, 211)  # #EDE6D3
INK = (30, 27, 22)  # #1E1B16
INK_SOFT = (88, 82, 74)  # #58524A
STAMP = (179, 64, 46)  # #B3402E
BRASS = (201, 162, 75)  # #C9A24B
BRASS_DIM = (140, 114, 56)  # #8C7238

CARD_W = 1020
CARD_PAD_X = 64
CARD_PAD_Y = 56

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_DEJAVU = "/usr/share/fonts/truetype/dejavu"


def _font(name: str, size: int, *, fallback: str) -> ImageFont.FreeTypeFont:
    """Load a bundled TTF at ``size``, falling back to a DejaVu face if missing."""
    path = _FONT_DIR / name
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.truetype(f"{_DEJAVU}/{fallback}", size)


def _mono(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    face = "JetBrainsMono-Bold.ttf" if bold else "JetBrainsMono-Regular.ttf"
    fb = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    return _font(face, size, fallback=fb)


def _serif_italic(size: int) -> ImageFont.FreeTypeFont:
    return _font("LibreCaslonText-Italic.ttf", size, fallback="DejaVuSerif.ttf")


def _marker(size: int) -> ImageFont.FreeTypeFont:
    # Permanent Marker has no bold; DejaVu Serif Bold is the closest fallback ink.
    return _font("PermanentMarker-Regular.ttf", size, fallback="DejaVuSerif-Bold.ttf")


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def _tracked_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    *,
    tracking: float = 0.0,
) -> None:
    """Draw text with letter-spacing (Pillow has no native tracking)."""
    x_pos = float(xy[0])
    y = xy[1]
    for ch in text:
        draw.text((x_pos, y), ch, font=font, fill=fill)
        x_pos += draw.textlength(ch, font=font) + tracking


def _break_long_words(
    draw: ImageDraw.ImageDraw,
    words: list[str],
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Split any single word wider than ``max_width`` into character-level chunks.

    The greedy wrapper only breaks on spaces, so a lone unbreakable token (e.g. a
    very long username in the headline) would otherwise draw past the card edge.
    """
    out: list[str] = []
    for word in words:
        if draw.textlength(word, font=font) <= max_width:
            out.append(word)
            continue
        chunk = ""
        for ch in word:
            if chunk and draw.textlength(chunk + ch, font=font) > max_width:
                out.append(chunk)
                chunk = ch
            else:
                chunk += ch
        if chunk:
            out.append(chunk)
    return out


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = 3,
) -> list[str]:
    """Greedy word-wrap to fit ``max_width``; ellipsize the last line if over."""
    words = _break_long_words(draw, text.split(), font, max_width)
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    # Ellipsize if the text overflowed the allotted lines.
    if len(lines) == max_lines and (len(" ".join(lines).split()) < len(words)):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last + "…"
    return lines


# --------------------------------------------------------------------------- #
# Card render
# --------------------------------------------------------------------------- #


def render_og_card(
    payload: ReportPayload, report_id: int, *, site: str = "chess-insights.app"
) -> bytes:
    """Render the 1200×630 share card for one report and return PNG bytes."""
    img = Image.new("RGB", (WIDTH, HEIGHT), FELT)
    _paint_felt_texture(img)
    draw = ImageDraw.Draw(img)

    # Card geometry (centred horizontally; vertically balanced like the mockup).
    card_x = (WIDTH - CARD_W) // 2
    card_h = _card_height(draw, payload)
    card_y = (HEIGHT - card_h) // 2

    _draw_card_surface(img, draw, card_x, card_y, card_h)

    inner_x = card_x + CARD_PAD_X
    inner_w = CARD_W - 2 * CARD_PAD_X
    y = card_y + CARD_PAD_Y

    # Kicker: "ADJUDICATION REPORT" ↔ "No. NNNN"
    kicker_font = _mono(15, bold=False)
    case = f"No. {report_id:04d}"
    _tracked_text(draw, (inner_x, y), "ADJUDICATION REPORT", kicker_font, INK_SOFT, tracking=2.1)
    case_w = draw.textlength(case, font=kicker_font)
    draw.text((card_x + CARD_W - CARD_PAD_X - case_w, y), case, font=kicker_font, fill=INK_SOFT)
    y += 15 + 10

    # Username line: "user · N games analyzed"
    user_font = _mono(26, bold=True)
    subject = f"{payload.username} · {payload.games_analyzed} games analyzed"
    draw.text((inner_x, y), subject, font=user_font, fill=INK)
    y += 26 + 28

    # Verdict row: rotated marker glyph + italic headline (wrapped).
    glyph_font = _marker(92)
    verdict_font = _serif_italic(40)
    verdict_max_w = inner_w - 120  # leave room for the glyph column (~92 + gap 28)
    lines = _wrap(draw, payload.signature_leak.headline, verdict_font, verdict_max_w, max_lines=3)

    _draw_glyph(img, (inner_x, y - 6), glyph_font)
    text_x = inner_x + 120
    line_h = int(40 * 1.32)
    ty = y
    for line in lines:
        draw.text((text_x, ty), line, font=verdict_font, fill=INK)
        ty += line_h

    # Footer: brand mark ↔ url.
    footer_font = _mono(15, bold=False)
    footer_bold = _mono(15, bold=True)
    footer_y = card_y + card_h - CARD_PAD_Y - 20
    _draw_brand(draw, (inner_x, footer_y), footer_bold)
    url = f"{site}/report/{payload.username}"
    url_w = draw.textlength(url, font=footer_font)
    url_x = card_x + CARD_W - CARD_PAD_X - url_w
    draw.text((url_x, footer_y + 3), url, font=footer_font, fill=INK_SOFT)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _card_height(draw: ImageDraw.ImageDraw, payload: ReportPayload) -> int:
    """Compute card height from the wrapped verdict (mirrors the fixed mockup rhythm)."""
    verdict_font = _serif_italic(40)
    inner_w = CARD_W - 2 * CARD_PAD_X
    lines = _wrap(draw, payload.signature_leak.headline, verdict_font, inner_w - 120, max_lines=3)
    line_h = int(40 * 1.32)
    verdict_block = max(92, len(lines) * line_h)  # glyph is 92px tall
    # pad_top + kicker(15+10) + user(26+28) + verdict + gap(40) + footer(20) + pad_bottom
    return CARD_PAD_Y + 25 + 54 + verdict_block + 40 + 20 + CARD_PAD_Y


def _paint_felt_texture(img: Image.Image) -> None:
    """Two soft radial gradients over the felt, matching the mockup's lighting."""
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    # Top-left light bloom.
    _radial(odraw, (int(WIDTH * 0.12), int(HEIGHT * 0.08)), int(WIDTH * 0.42), (255, 255, 255, 10))
    # Bottom-right shadow.
    _radial(odraw, (int(WIDTH * 0.92), int(HEIGHT * 0.95)), int(WIDTH * 0.55), (0, 0, 0, 51))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _radial(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
) -> None:
    """Approximate a radial gradient with concentric fading ellipses."""
    cx, cy = center
    steps = 24
    r, g, b, a = color
    for i in range(steps, 0, -1):
        rr = int(radius * i / steps)
        alpha = int(a * (1 - i / steps))
        if alpha <= 0:
            continue
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(r, g, b, alpha))


def _draw_card_surface(img: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, h: int) -> None:
    """Paper card: soft drop shadow, brass bottom edge, brass deckle top stripe."""
    # Drop shadow.
    shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle([x, y + 20, x + CARD_W, y + h + 24], radius=3, fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    img.paste(Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB"), (0, 0))

    # Brass bottom edge (3px offset) + paper surface.
    draw.rounded_rectangle([x, y + 3, x + CARD_W, y + h + 3], radius=3, fill=BRASS_DIM)
    draw.rounded_rectangle([x, y, x + CARD_W, y + h], radius=3, fill=PAPER)

    # Deckle top stripe: repeating brass dashes, 22px on / 8px gap, 8px tall, ~55% alpha.
    stripe = Image.new("RGBA", (CARD_W, 8), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stripe)
    dash = BRASS + (140,)  # ~0.55 alpha
    dx = 0
    while dx < CARD_W:
        sd.rectangle([dx, 0, dx + 22, 8], fill=dash)
        dx += 30
    img.paste(stripe, (x, y), stripe)


def _draw_glyph(img: Image.Image, xy: tuple[int, int], font: ImageFont.FreeTypeFont) -> None:
    """The ?? blunder glyph in stamp red, rotated -6° (design signature)."""
    layer = Image.new("RGBA", (160, 130), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((0, 0), "??", font=font, fill=STAMP + (255,))
    rotated = layer.rotate(6, expand=True, resample=Image.Resampling.BICUBIC)
    img.paste(rotated, xy, rotated)


def _draw_brand(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], font: ImageFont.FreeTypeFont
) -> None:
    """ "♞ Chess Insights" — brass knight + bold ink wordmark."""
    x, y = xy
    knight_font = _mono(20, bold=True)
    draw.text((x, y - 2), "\u265e", font=knight_font, fill=BRASS)
    kx = x + draw.textlength("\u265e", font=knight_font) + 8
    draw.text((kx, y + 3), "Chess Insights", font=font, fill=INK)
