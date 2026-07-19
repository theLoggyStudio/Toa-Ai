"""Tests du calcul de tarification."""

from config import (
    ESTIMATED_BUBBLES_PER_PAGE,
    PRICE_BASE_CFA,
    PRICE_PER_BUBBLE_CFA,
    amount_cfa_for_bubbles,
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
