"""
AST JSON Rule Engine for Trade Promotions.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrderItem:
    sku: str
    quantity: float
    unit_price: float

    @property
    def subtotal(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class OrderContext:
    items: list[OrderItem]
    tenant_id: str
    customer_id: str
    price_list: str = "standard"

    @property
    def subtotal(self) -> float:
        return sum(i.subtotal for i in self.items)

    @property
    def skus(self) -> set[str]:
        return {i.sku for i in self.items}

    def quantity_of(self, sku: str) -> float:
        return sum(i.quantity for i in self.items if i.sku == sku)


@dataclass
class PromotionResult:
    promotion_id: str
    promotion_code: str
    discount_amount: float = 0.0
    free_items: list[dict] = field(default_factory=list)
    # e.g., [{"sku": "JUICE-001", "quantity": 2, "reason": "BOGO"}]


# ─────────────────────────────────────────────
# CONDITION EVALUATORS
# ─────────────────────────────────────────────

def evaluate_condition(condition_type: str, parameters: dict, context: OrderContext) -> bool:
    """Evaluate a single condition node against the order context."""

    if condition_type == "min_order_amount":
        min_amount = parameters.get("min_amount", 0)
        return context.subtotal >= min_amount

    elif condition_type == "has_sku":
        required_sku = parameters.get("sku")
        return required_sku in context.skus

    elif condition_type == "has_any_sku":
        required_skus = set(parameters.get("skus", []))
        return bool(context.skus & required_skus)

    else:
        raise ValueError(f"Unknown condition_type: '{condition_type}'")


# ─────────────────────────────────────────────
# ACTION APPLICATORS
# ─────────────────────────────────────────────

def apply_action(
    action_type: str,
    parameters: dict,
    context: OrderContext,
    result: PromotionResult,
) -> None:
    """Apply a promotion action and mutate the result in-place."""

    if action_type == "percentage_off_order":
        pct = parameters.get("percentage", 0)
        result.discount_amount += context.subtotal * (pct / 100.0)

    elif action_type == "free_item":
        sku = parameters.get("sku")
        quantity = parameters.get("quantity", 1)
        if sku:
            result.free_items.append({
                "sku": sku,
                "quantity": quantity,
                "unit_price": 0.0,
                "reason": f"Free item from promotion {result.promotion_code}",
            })

    elif action_type == "bogo":
        # Buy One Get One — add one free unit of each qualifying SKU
        qualifying_skus = parameters.get("skus", list(context.skus))
        for sku in qualifying_skus:
            qty = context.quantity_of(sku)
            free_qty = int(qty // 2)  # Floor division: buy 2 get 1, buy 4 get 2, etc.
            if free_qty > 0:
                result.free_items.append({
                    "sku": sku,
                    "quantity": free_qty,
                    "unit_price": 0.0,
                    "reason": f"BOGO from promotion {result.promotion_code}",
                })

    else:
        raise ValueError(f"Unknown action_type: '{action_type}'")


# ─────────────────────────────────────────────
# PROMOTION EVALUATOR
# ─────────────────────────────────────────────

def evaluate_promotion(
    promotion: dict,
    conditions: list[dict],
    actions: list[dict],
    context: OrderContext,
) -> PromotionResult | None:
    """
    Evaluate a full promotion (conditions + actions) against an order.

    Args:
        promotion: Promotion record dict (id, code, is_stackable, valid_from, valid_to)
        conditions: List of condition dicts (condition_type, parameters)
        actions: List of action dicts (action_type, parameters)
        context: OrderContext

    Returns:
        PromotionResult if all conditions pass, else None.
    """
    # All conditions must pass (AND logic)
    for condition in conditions:
        if not evaluate_condition(
            condition["condition_type"],
            condition.get("parameters", {}),
            context,
        ):
            return None  # Short-circuit: promotion not applicable

    result = PromotionResult(
        promotion_id=str(promotion["id"]),
        promotion_code=promotion.get("code", ""),
    )

    for action in actions:
        apply_action(
            action["action_type"],
            action.get("parameters", {}),
            context,
            result,
        )

    return result


def evaluate_promotions(
    promotions_with_rules: list[dict],
    context: OrderContext,
) -> list[PromotionResult]:
    """
    Evaluate a list of promotions against an order.
    Handles stacking: non-stackable promotions — only the best discount wins.

    Args:
        promotions_with_rules: List of dicts with keys:
            {"promotion": {...}, "conditions": [...], "actions": [...]}
        context: OrderContext

    Returns:
        List of applicable PromotionResult objects.
    """
    results: list[PromotionResult] = []
    stackable_results: list[PromotionResult] = []
    non_stackable_results: list[PromotionResult] = []

    for promo_data in promotions_with_rules:
        result = evaluate_promotion(
            promo_data["promotion"],
            promo_data["conditions"],
            promo_data["actions"],
            context,
        )
        if result:
            if promo_data["promotion"].get("is_stackable", False):
                stackable_results.append(result)
            else:
                non_stackable_results.append(result)

    # Apply all stackable promotions
    results.extend(stackable_results)

    # For non-stackable: keep only the best (highest discount)
    if non_stackable_results:
        best = max(non_stackable_results, key=lambda r: r.discount_amount)
        results.append(best)

    return results
