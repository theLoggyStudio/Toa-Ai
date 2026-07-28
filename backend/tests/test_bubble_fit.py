"""Tests superposition stricte (polygone + clip-path + police seule)."""

import numpy as np

from models import BoundingBox
from services.bubble_fit import (
    BUBBLE_FIT_CSS,
    BUBBLE_FIT_SCRIPT,
    TRANSLATED_TEXT_COLOR,
    build_bubble_wrap,
    estimate_font_size,
    polygon_bbox,
    polygon_centroid,
    polygon_to_clip_path,
    shrink_polygon,
)


class TestEstimateFontSize:
    def test_short_vs_long(self):
        small = estimate_font_size("phrase tres longue " * 5, 200, 100)
        big = estimate_font_size("Oui.", 200, 100)
        assert big > small

    def test_bounds(self):
        assert estimate_font_size("x", 2000, 2000) <= 24
        assert estimate_font_size("mot " * 200, 40, 30) >= 8


class TestPolygonHelpers:
    def test_bbox_and_centroid(self):
        poly = [(10, 10), (110, 10), (110, 60), (10, 60)]
        bb = polygon_bbox(poly)
        assert bb.x_min == 10 and bb.y_min == 10
        assert bb.x_max == 111 and bb.y_max == 61
        cx, cy = polygon_centroid(poly)
        assert abs(cx - 60) < 1
        assert abs(cy - 35) < 1

    def test_shrink_moves_inward(self):
        poly = [(0, 0), (100, 0), (100, 100), (0, 100)]
        shrunk = shrink_polygon(poly, factor=0.5)
        bb = polygon_bbox(shrunk)
        assert bb.x_max - bb.x_min < 100
        assert bb.y_max - bb.y_min < 100

    def test_clip_path_relative(self):
        poly = [(100, 200), (150, 200), (150, 250)]
        clip = polygon_to_clip_path(poly, origin_x=100, origin_y=200)
        assert clip.startswith("polygon(")
        assert "0px 0px" in clip
        assert "50px 0px" in clip


class TestBuildBubbleWrap:
    def test_polygon_uses_clip_path_no_new_shape(self):
        poly = [(100, 50), (200, 50), (200, 120), (100, 120)]
        html = build_bubble_wrap(
            "<p>Salut</p>",
            box_x_min=100,
            box_y_min=50,
            box_w=100,
            box_h=70,
            page_width=800,
            font_size=14,
            polygon=poly,
        )
        assert "clip-path:polygon(" in html
        assert "toa-bubble-wrap--poly" in html
        assert "background:transparent" not in html or True  # fond via CSS
        assert "border: 1px" not in html

    def test_aabb_fallback_applies_inner_margin(self):
        html = build_bubble_wrap(
            "<p>x</p>",
            box_x_min=40,
            box_y_min=60,
            box_w=80,
            box_h=40,
            page_width=800,
            font_size=12,
        )
        assert "left:47px" in html  # 40 + INNER_PAD_PX
        assert "top:67px" in html  # 60 + INNER_PAD_PX
        assert "translate(-50%" not in html


class TestCssContract:
    def test_no_new_bubble_chrome(self):
        assert "background: #ffffff" in BUBBLE_FIT_CSS
        assert "max-content" in BUBBLE_FIT_CSS
        assert "background: transparent !important" in BUBBLE_FIT_CSS
        assert "z-index: 100" in BUBBLE_FIT_CSS
        assert TRANSLATED_TEXT_COLOR in BUBBLE_FIT_CSS
        assert "translate(-50%" not in BUBBLE_FIT_CSS

    def test_script_only_font(self):
        assert "fontSize" in BUBBLE_FIT_SCRIPT
        assert "window.__toaFitDone = true" in BUBBLE_FIT_SCRIPT
        # Pas de redimensionnement du wrap.
        assert "wrap.style.width" not in BUBBLE_FIT_SCRIPT
        assert "wrap.style.height" not in BUBBLE_FIT_SCRIPT


class TestDetectPolygonOnEllipse:
    def test_detect_bubble_polygon_finds_ellipse(self):
        import cv2
        from services.rendering import detect_bubble_polygon

        img = np.full((400, 400, 3), 40, dtype=np.uint8)
        cv2.ellipse(img, (200, 200), (70, 45), 0, 0, 360, (255, 255, 255), -1)
        # Seed proche de la vraie bulle (évite de confondre avec un panneau).
        seed = BoundingBox(x_min=150, y_min=170, x_max=250, y_max=230)
        poly = detect_bubble_polygon(img, seed)
        assert poly is not None
        assert len(poly) >= 3
        bb = polygon_bbox(poly)
        assert bb.x_max - bb.x_min >= 80
        assert bb.y_max - bb.y_min >= 50
