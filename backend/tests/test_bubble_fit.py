"""Tests du module bubble_fit (localisation + bulles auto-dimensionnées)."""

import numpy as np

from models import BoundingBox
from services.bubble_fit import (
    BUBBLE_FIT_CSS,
    BUBBLE_FIT_SCRIPT,
    TRANSLATED_TEXT_COLOR,
    build_bubble_wrap,
    estimate_font_size,
    locate_bubble_interior,
)


class TestEstimateFontSize:
    def test_short_text_large_box_gets_big_font(self):
        small = estimate_font_size(
            "Une très longue phrase qui remplit la bulle entière", 200, 100
        )
        big = estimate_font_size("Oui.", 200, 100)
        assert big > small

    def test_bounds_respected(self):
        assert estimate_font_size("x", 2000, 2000) <= 26
        assert estimate_font_size("mot " * 200, 40, 30) >= 9

    def test_sfx_scales_with_height(self):
        assert estimate_font_size("BOUM", 300, 80, is_sfx=True) == 32
        assert estimate_font_size("BOUM", 300, 200, is_sfx=True) == 40


class TestBuildBubbleWrap:
    def test_anchored_at_bbox_center(self):
        html = build_bubble_wrap(
            "<p>Salut</p>",
            box_x_min=100,
            box_y_min=50,
            box_w=80,
            box_h=40,
            page_width=800,
            font_size=16,
        )
        assert "left:140px" in html  # 100 + 80/2
        assert "top:70px" in html  # 50 + 40/2
        assert 'data-w="80"' in html
        assert 'data-h="40"' in html
        assert "font-size:16px" in html

    def test_max_width_clamped_to_page(self):
        html = build_bubble_wrap(
            "<p>x</p>",
            box_x_min=0,
            box_y_min=0,
            box_w=900,
            box_h=100,
            page_width=400,
            font_size=14,
        )
        assert "--toa-max-w:384px" in html


class TestCssAndScript:
    def test_bubble_constrained_to_envelope(self):
        assert "width: 100%" in BUBBLE_FIT_CSS
        assert "translate(-50%, -50%)" in BUBBLE_FIT_CSS
        assert "overflow: hidden" in BUBBLE_FIT_CSS
        assert "word-break: break-word" in BUBBLE_FIT_CSS

    def test_translated_color_forced(self):
        assert TRANSLATED_TEXT_COLOR == "#4A3F35"
        assert TRANSLATED_TEXT_COLOR in BUBBLE_FIT_CSS

    def test_script_signals_completion(self):
        assert "window.__toaFitDone = true" in BUBBLE_FIT_SCRIPT
        assert "overflows" in BUBBLE_FIT_SCRIPT


class TestLocateBubbleInterior:
    def test_flood_fill_finds_white_ellipse(self):
        import cv2

        img = np.full((400, 400, 3), 40, dtype=np.uint8)
        cv2.ellipse(img, (200, 200), (90, 60), 0, 0, 360, (255, 255, 255), -1)
        # Bbox Cursor volontairement trop petite, au centre.
        seed = BoundingBox(x_min=180, y_min=185, x_max=220, y_max=215)
        located = locate_bubble_interior(img, seed)
        # Doit couvrir une bonne partie de l'ellipse (pas juste le seed).
        assert located.x_max - located.x_min >= 100
        assert located.y_max - located.y_min >= 70
        # Centre proche du centre de l'ellipse.
        cx = (located.x_min + located.x_max) // 2
        cy = (located.y_min + located.y_max) // 2
        assert abs(cx - 200) < 20
        assert abs(cy - 200) < 20

    def test_fallback_when_no_white(self):
        img = np.full((200, 200, 3), 30, dtype=np.uint8)
        seed = BoundingBox(x_min=50, y_min=50, x_max=120, y_max=100)
        located = locate_bubble_interior(img, seed)
        # Repli inset : légèrement plus petit que la seed.
        assert located.x_min >= seed.x_min
        assert located.y_min >= seed.y_min
        assert located.x_max <= seed.x_max
        assert located.y_max <= seed.y_max
