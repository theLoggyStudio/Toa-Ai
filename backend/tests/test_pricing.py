"""Tests du calcul de tarification."""

from config import (
    ESTIMATED_BUBBLES_PER_PAGE,
    FRESCO_OPTION_PRICE_CFA,
    PRICE_BASE_CFA,
    PRICE_PER_BUBBLE_CFA,
    amount_cfa_for_bubbles,
    amount_cfa_for_image_size,
    amount_cfa_for_restore_options,
    estimate_bubbles_for_pages,
    normalize_restore_options,
)


def test_amount_is_base_plus_per_bubble():
    assert amount_cfa_for_bubbles(0) == PRICE_BASE_CFA
    assert (
        amount_cfa_for_bubbles(10)
        == PRICE_BASE_CFA + 10 * PRICE_PER_BUBBLE_CFA
    )


def test_amount_negative_bubbles_clamped():
    assert amount_cfa_for_bubbles(-5) == PRICE_BASE_CFA


def test_estimate_bubbles_for_pages():
    assert estimate_bubbles_for_pages(3) == 3 * ESTIMATED_BUBBLES_PER_PAGE
    # Toujours au moins 1 bulle estimée.
    assert estimate_bubbles_for_pages(0) == 1


def test_fresco_default_option_is_tears():
    assert normalize_restore_options([]) == ["tears"]
    assert normalize_restore_options(None) == ["tears"]


def test_fresco_amount_per_option():
    assert amount_cfa_for_restore_options(["tears"]) == FRESCO_OPTION_PRICE_CFA
    assert (
        amount_cfa_for_restore_options(["tears", "color"])
        == 2 * FRESCO_OPTION_PRICE_CFA
    )
    assert (
        amount_cfa_for_restore_options(["tears", "color", "hd"])
        == 3 * FRESCO_OPTION_PRICE_CFA
    )


def test_fresco_ignores_unknown_and_dedupes():
    assert normalize_restore_options(["hd", "hd", "nope", "color"]) == [
        "color",
        "hd",
    ]


def test_eclat_legacy_amount_is_one_option():
    assert amount_cfa_for_image_size(100, 100) == FRESCO_OPTION_PRICE_CFA
    assert amount_cfa_for_image_size(4000, 4000) == FRESCO_OPTION_PRICE_CFA
