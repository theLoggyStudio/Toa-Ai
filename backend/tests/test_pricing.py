"""Tests du calcul de tarification."""

from config import (
    ECLAT_PRICE_MAX_CFA,
    ECLAT_PRICE_MIN_CFA,
    ESTIMATED_BUBBLES_PER_PAGE,
    PRICE_BASE_CFA,
    PRICE_PER_BUBBLE_CFA,
    amount_cfa_for_bubbles,
    amount_cfa_for_image_size,
    estimate_bubbles_for_pages,
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


def test_eclat_amount_clamped_min():
    # Très petite image → prix minimum
    assert amount_cfa_for_image_size(100, 100) == ECLAT_PRICE_MIN_CFA


def test_eclat_amount_clamped_max():
    # Grande image (≥ 12 MP) → prix maximum
    assert amount_cfa_for_image_size(4000, 4000) == ECLAT_PRICE_MAX_CFA


def test_eclat_amount_mid_range():
    # ~6.15 MP (entre 0.3 et 12) → entre min et max
    amount = amount_cfa_for_image_size(2500, 2500)
    assert ECLAT_PRICE_MIN_CFA < amount < ECLAT_PRICE_MAX_CFA
