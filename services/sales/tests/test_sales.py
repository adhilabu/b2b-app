"""
Sales Service Tests — Rule Engine, Tax Calculator, Orders, Promotions.
The rule engine tests are pure unit tests (no DB/HTTP).
"""
import pytest
import uuid
from unittest.mock import patch, AsyncMock

from app.engine.rule_engine import (
    OrderContext, OrderItem, evaluate_promotion, evaluate_promotions,
    evaluate_condition, apply_action, PromotionResult
)
from app.engine.tax_calculator import calculate_taxes


# ─────────────────────────────────────────────
# RULE ENGINE — UNIT TESTS
# ─────────────────────────────────────────────

def make_context(items: list[tuple]) -> OrderContext:
    """Helper: items = [(sku, qty, price), ...]"""
    return OrderContext(
        items=[OrderItem(sku=s, quantity=q, unit_price=p) for s, q, p in items],
        tenant_id="tenant-1",
        customer_id="cust-1",
    )


class TestConditionEvaluator:
    def test_min_order_amount_passes(self):
        ctx = make_context([("A", 10, 50.0)])  # subtotal = 500
        assert evaluate_condition("min_order_amount", {"min_amount": 400}, ctx) is True

    def test_min_order_amount_fails(self):
        ctx = make_context([("A", 2, 50.0)])  # subtotal = 100
        assert evaluate_condition("min_order_amount", {"min_amount": 200}, ctx) is False

    def test_has_sku_passes(self):
        ctx = make_context([("JUICE-001", 5, 20.0)])
        assert evaluate_condition("has_sku", {"sku": "JUICE-001"}, ctx) is True

    def test_has_sku_fails(self):
        ctx = make_context([("BISCUIT-001", 3, 30.0)])
        assert evaluate_condition("has_sku", {"sku": "JUICE-001"}, ctx) is False

    def test_has_any_sku_passes(self):
        ctx = make_context([("JUICE-001", 5, 20.0), ("WATER-002", 2, 10.0)])
        assert evaluate_condition("has_any_sku", {"skus": ["JUICE-001", "COLA-003"]}, ctx) is True

    def test_has_any_sku_fails(self):
        ctx = make_context([("BISCUIT-001", 5, 20.0)])
        assert evaluate_condition("has_any_sku", {"skus": ["JUICE-001", "COLA-003"]}, ctx) is False

    def test_unknown_condition_raises(self):
        ctx = make_context([("A", 1, 10.0)])
        with pytest.raises(ValueError, match="Unknown condition_type"):
            evaluate_condition("unknown_condition", {}, ctx)


class TestActionApplicator:
    def test_percentage_off_order(self):
        ctx = make_context([("A", 10, 100.0)])  # subtotal = 1000
        result = PromotionResult(promotion_id="p1", promotion_code="SAVE10")
        apply_action("percentage_off_order", {"percentage": 10}, ctx, result)
        assert result.discount_amount == pytest.approx(100.0)

    def test_free_item_injection(self):
        ctx = make_context([("JUICE-001", 6, 20.0)])
        result = PromotionResult(promotion_id="p2", promotion_code="FREE1")
        apply_action("free_item", {"sku": "GIFT-001", "quantity": 2}, ctx, result)
        assert len(result.free_items) == 1
        assert result.free_items[0]["sku"] == "GIFT-001"
        assert result.free_items[0]["quantity"] == 2
        assert result.free_items[0]["unit_price"] == 0.0

    def test_bogo_buy_4_get_2(self):
        ctx = make_context([("JUICE-001", 4, 20.0)])
        result = PromotionResult(promotion_id="p3", promotion_code="BOGO")
        apply_action("bogo", {"skus": ["JUICE-001"]}, ctx, result)
        assert result.free_items[0]["sku"] == "JUICE-001"
        assert result.free_items[0]["quantity"] == 2  # 4 // 2 = 2

    def test_bogo_buy_3_get_1(self):
        ctx = make_context([("JUICE-001", 3, 20.0)])
        result = PromotionResult(promotion_id="p4", promotion_code="BOGO")
        apply_action("bogo", {"skus": ["JUICE-001"]}, ctx, result)
        assert result.free_items[0]["quantity"] == 1  # 3 // 2 = 1


class TestPromotionEvaluator:
    def _make_promo(self, pid, code, is_stackable, conditions, actions):
        return {
            "promotion": {"id": pid, "code": code, "is_stackable": is_stackable},
            "conditions": conditions,
            "actions": actions,
        }

    def test_full_promotion_applies(self):
        ctx = make_context([("A", 10, 100.0)])  # subtotal = 1000
        promo = self._make_promo(
            "p1", "BULK10", False,
            conditions=[{"condition_type": "min_order_amount", "parameters": {"min_amount": 500}}],
            actions=[{"action_type": "percentage_off_order", "parameters": {"percentage": 10}}],
        )
        results = evaluate_promotions([promo], ctx)
        assert len(results) == 1
        assert results[0].discount_amount == pytest.approx(100.0)

    def test_promotion_not_applicable(self):
        ctx = make_context([("A", 1, 10.0)])  # subtotal = 10
        promo = self._make_promo(
            "p1", "BIG_ORDER", False,
            conditions=[{"condition_type": "min_order_amount", "parameters": {"min_amount": 500}}],
            actions=[{"action_type": "percentage_off_order", "parameters": {"percentage": 10}}],
        )
        results = evaluate_promotions([promo], ctx)
        assert len(results) == 0

    def test_stackable_promotions_both_apply(self):
        ctx = make_context([("JUICE-001", 10, 50.0)])  # subtotal = 500
        promos = [
            self._make_promo("p1", "SAVE5", True,
                conditions=[{"condition_type": "min_order_amount", "parameters": {"min_amount": 100}}],
                actions=[{"action_type": "percentage_off_order", "parameters": {"percentage": 5}}]),
            self._make_promo("p2", "SAVE3", True,
                conditions=[{"condition_type": "has_sku", "parameters": {"sku": "JUICE-001"}}],
                actions=[{"action_type": "percentage_off_order", "parameters": {"percentage": 3}}]),
        ]
        results = evaluate_promotions(promos, ctx)
        assert len(results) == 2
        total_discount = sum(r.discount_amount for r in results)
        assert total_discount == pytest.approx(500 * 0.05 + 500 * 0.03)

    def test_non_stackable_best_discount_wins(self):
        ctx = make_context([("A", 10, 100.0)])  # subtotal = 1000
        promos = [
            self._make_promo("p1", "SAVE10", False,
                conditions=[{"condition_type": "min_order_amount", "parameters": {"min_amount": 500}}],
                actions=[{"action_type": "percentage_off_order", "parameters": {"percentage": 10}}]),
            self._make_promo("p2", "SAVE20", False,
                conditions=[{"condition_type": "min_order_amount", "parameters": {"min_amount": 500}}],
                actions=[{"action_type": "percentage_off_order", "parameters": {"percentage": 20}}]),
        ]
        results = evaluate_promotions(promos, ctx)
        # Only best non-stackable discount applies
        assert len(results) == 1
        assert results[0].promotion_code == "SAVE20"
        assert results[0].discount_amount == pytest.approx(200.0)


# ─────────────────────────────────────────────
# TAX CALCULATOR — UNIT TESTS
# ─────────────────────────────────────────────

class TestTaxCalculator:
    def test_intra_state_gst_12_percent(self):
        items = [{"sku": "A", "quantity": 10, "unit_price": 100.0, "tax_rate_percent": 12.0}]
        result = calculate_taxes(items, is_intra_state=True)
        assert result.subtotal == pytest.approx(1000.0)
        assert result.total_cgst == pytest.approx(60.0)  # 6%
        assert result.total_sgst == pytest.approx(60.0)  # 6%
        assert result.total_igst == pytest.approx(0.0)
        assert result.total_tax == pytest.approx(120.0)
        assert result.grand_total == pytest.approx(1120.0)

    def test_inter_state_igst(self):
        items = [{"sku": "A", "quantity": 5, "unit_price": 200.0, "tax_rate_percent": 18.0}]
        result = calculate_taxes(items, is_intra_state=False)
        assert result.total_cgst == pytest.approx(0.0)
        assert result.total_igst == pytest.approx(180.0)  # 18% of 1000
        assert result.grand_total == pytest.approx(1180.0)

    def test_tax_with_discount(self):
        items = [{"sku": "A", "quantity": 10, "unit_price": 100.0, "tax_rate_percent": 10.0}]
        result = calculate_taxes(items, discount_amount=100.0, is_intra_state=True)
        # Subtotal 1000, discount 100, taxable 900, tax 10% = 90
        assert result.taxable_amount == pytest.approx(900.0)
        assert result.total_tax == pytest.approx(90.0)
        assert result.grand_total == pytest.approx(990.0)

    def test_zero_tax_rate(self):
        items = [{"sku": "B", "quantity": 5, "unit_price": 100.0, "tax_rate_percent": 0.0}]
        result = calculate_taxes(items)
        assert result.total_tax == pytest.approx(0.0)
        assert result.grand_total == pytest.approx(500.0)

    def test_multiple_items_mixed_tax(self):
        items = [
            {"sku": "A", "quantity": 10, "unit_price": 50.0, "tax_rate_percent": 5.0},
            {"sku": "B", "quantity": 5, "unit_price": 100.0, "tax_rate_percent": 12.0},
        ]
        result = calculate_taxes(items)
        assert result.subtotal == pytest.approx(1000.0)
        # A: 500 * 5% = 25, B: 500 * 12% = 60
        assert result.total_tax == pytest.approx(85.0)
