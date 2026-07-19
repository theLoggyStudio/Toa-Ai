"""Tests du parsing des réponses Cursor et de la validation des bulles."""

import base64

import pytest

from models import BoundingBox, TextBlock
from services.translation import (
    _bbox_iou,
    _parse_full_page_response,
    validate_full_page_blocks,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _make_block(x1: int, y1: int, x2: int, y2: int, order: int = 0) -> TextBlock:
    return TextBlock(
        id=order,
        boundingBox=BoundingBox(x_min=x1, y_min=y1, x_max=x2, y_max=y2),
        originalText="こんにちは",
        translatedText="Bonjour",
    )


class TestParseFullPageResponse:
    def test_parse_nominal(self):
        raw = "\n".join(
            [
                "SOURCE_LANG|ja",
                "BUBBLE|1|10|20|110|80|こんにちは|Bonjour",
                f"HTML_B64|1|{_b64('<p>Bonjour</p>')}",
                "BUBBLE|2|200|300|320|380|ずんっ|Boum !",
            ]
        )
        blocks, lang, css = _parse_full_page_response(
            raw, width=800, height=1200, page_index=0
        )
        assert lang == "ja"
        assert css == ""
        assert len(blocks) == 2
        assert blocks[0].originalText == "こんにちは"
        assert blocks[0].translatedText == "Bonjour"
        assert blocks[0].bubbleHtml == "<p>Bonjour</p>"
        assert blocks[0].boundingBox.x_min == 10
        assert blocks[1].translatedText == "Boum !"

    def test_coordinates_clamped_to_image(self):
        raw = "BUBBLE|1|-50|-10|900|1500|texte|traduction"
        blocks, _, _ = _parse_full_page_response(
            raw, width=800, height=1200, page_index=0
        )
        bb = blocks[0].boundingBox
        assert bb.x_min == 0
        assert bb.y_min == 0
        assert bb.x_max == 800
        assert bb.y_max == 1200

    def test_invalid_bbox_dropped(self):
        # x_max <= x_min : la bulle doit être ignorée.
        raw = "BUBBLE|1|100|100|50|200|texte|traduction"
        blocks, _, _ = _parse_full_page_response(
            raw, width=800, height=1200, page_index=0
        )
        assert blocks == []

    def test_malformed_lines_ignored(self):
        raw = "\n".join(
            [
                "du bruit",
                "BUBBLE|pas_un_nombre|1|2|3|4|src|trg",
                "BUBBLE|1|10|20|110|80|こんにちは|Bonjour",
            ]
        )
        blocks, _, _ = _parse_full_page_response(
            raw, width=800, height=1200, page_index=0
        )
        assert len(blocks) == 1

    def test_translation_from_html_when_missing(self):
        raw = "\n".join(
            [
                "BUBBLE|1|10|20|110|80|こんにちは|",
                f"HTML_B64|1|{_b64('<p>Salut !</p>')}",
            ]
        )
        blocks, _, _ = _parse_full_page_response(
            raw, width=800, height=1200, page_index=0
        )
        assert blocks[0].translatedText == "Salut !"

    def test_page_index_offsets_ids(self):
        raw = "BUBBLE|1|10|20|110|80|src|trg"
        blocks, _, _ = _parse_full_page_response(
            raw, width=800, height=1200, page_index=3
        )
        assert blocks[0].id == 3000


class TestValidation:
    def test_empty_blocks_rejected(self):
        with pytest.raises(RuntimeError, match="Aucune bulle"):
            validate_full_page_blocks([], "ja", "fr")

    def test_missing_translation_rejected(self):
        block = _make_block(0, 0, 100, 50)
        block = block.model_copy(update={"translatedText": ""})
        with pytest.raises(RuntimeError, match="traduction manquante"):
            validate_full_page_blocks([block], "ja", "fr")

    def test_overlapping_blocks_rejected(self):
        a = _make_block(0, 0, 100, 100)
        b = _make_block(10, 10, 110, 110, order=1)
        with pytest.raises(RuntimeError, match="enchevetrent"):
            validate_full_page_blocks([a, b], "ja", "fr")

    def test_valid_blocks_pass(self):
        a = _make_block(0, 0, 100, 100)
        b = _make_block(200, 200, 300, 300, order=1)
        validate_full_page_blocks([a, b], "ja", "fr")


class TestBboxIou:
    def test_disjoint_is_zero(self):
        a = BoundingBox(x_min=0, y_min=0, x_max=10, y_max=10)
        b = BoundingBox(x_min=20, y_min=20, x_max=30, y_max=30)
        assert _bbox_iou(a, b) == 0.0

    def test_identical_is_one(self):
        a = BoundingBox(x_min=0, y_min=0, x_max=10, y_max=10)
        assert _bbox_iou(a, a) == pytest.approx(1.0)
