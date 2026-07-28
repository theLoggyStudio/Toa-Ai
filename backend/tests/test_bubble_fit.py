"""Tests superposition stricte (polygone + clip-path + police seule)."""

import numpy as np

from models import BoundingBox
from services.bubble_fit import (
    BUBBLE_FIT_CSS,
    BUBBLE_FIT_SCRIPT,
    TRANSLATED_TEXT_COLOR,
    boxes_overlap,
    build_bubble_wrap,
    content_inner_pad,
    estimate_font_size,
    estimate_original_font_size,
    polygon_bbox,
    polygon_centroid,
    polygon_to_clip_path,
    resolve_placement_boxes,
    separate_overlapping_boxes,
    shrink_polygon,
)


class TestEstimateFontSize:
    def test_short_vs_long(self):
        small = estimate_font_size("phrase tres longue " * 5, 200, 100)
        big = estimate_font_size("Oui.", 200, 100)
        assert big > small

    def test_bounds(self):
        assert estimate_font_size("x", 2000, 2000) <= 36
        assert estimate_font_size("mot " * 200, 40, 30) >= 9

    def test_capped_by_original_font(self):
        # Petite bbox originale → plafond bas, même si la zone placée est grande.
        size = estimate_font_size(
            "Ok",
            400,
            300,
            original_text="あ",
            original_box_w=40,
            original_box_h=36,
        )
        assert size <= estimate_original_font_size("あ", 40, 36)


class TestContentPad:
    def test_short_tighter_than_long(self):
        short = content_inner_pad("Oui", 120, 80)
        long = content_inner_pad("phrase " * 40, 120, 80)
        assert short <= long
        assert short <= 3


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
    def test_polygon_uses_clip_path_and_css_bg(self):
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
        assert 'class="toa-bubble-bg"' in html
        assert "border: 1px" not in html

    def test_ellipse_fallback_css_bg(self):
        html = build_bubble_wrap(
            "<p>x</p>",
            box_x_min=40,
            box_y_min=60,
            box_w=80,
            box_h=40,
            page_width=800,
            font_size=12,
        )
        assert "toa-bubble-wrap--ellipse" in html
        assert 'class="toa-bubble-bg"' in html
        assert "data-max-font=" in html
        assert "left:42px" in html  # 40 + pad court (2)
        assert "top:62px" in html
        assert "translate(-50%" not in html

    def test_sfx_has_no_white_bg(self):
        html = build_bubble_wrap(
            "<p>Schlop!</p>",
            box_x_min=10,
            box_y_min=10,
            box_w=60,
            box_h=30,
            page_width=800,
            font_size=16,
            is_sfx=True,
        )
        assert "toa-bubble-wrap--sfx" in html
        assert "toa-bubble-bg" not in html


class TestSeparateOverlaps:
    def test_side_by_side_no_overlap_after(self):
        a = BoundingBox(x_min=10, y_min=10, x_max=80, y_max=70)
        b = BoundingBox(x_min=60, y_min=20, x_max=130, y_max=80)
        out = separate_overlapping_boxes([a, b], page_w=400, page_h=400, gap=10)
        assert not boxes_overlap(out[0], out[1], gap=9)

    def test_resolve_drops_polygon_on_conflict(self):
        a = BoundingBox(x_min=0, y_min=0, x_max=100, y_max=80)
        b = BoundingBox(x_min=40, y_min=10, x_max=140, y_max=90)
        placed, keep = resolve_placement_boxes(
            [a, b], page_w=500, page_h=500, use_polygon_flags=[True, True]
        )
        assert keep == [False, False]
        assert not boxes_overlap(placed[0], placed[1], gap=9)

    def test_css_white_layer_under_text(self):
        assert ".toa-bubble-bg" in BUBBLE_FIT_CSS
        assert "border-radius: 50%" in BUBBLE_FIT_CSS
        assert "background: #ffffff" in BUBBLE_FIT_CSS
        assert "z-index: 1000" in BUBBLE_FIT_CSS
        assert TRANSLATED_TEXT_COLOR in BUBBLE_FIT_CSS
        assert "font-weight: 800" in BUBBLE_FIT_CSS
        assert "opacity: 1" in BUBBLE_FIT_CSS
        assert ".toa-bubble-wrap .toa-bubble" in BUBBLE_FIT_CSS
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
