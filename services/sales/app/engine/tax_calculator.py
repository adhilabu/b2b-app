"""
Tax Calculator — Indian GST and trade tax computation.
Handles CGST/SGST (intra-state), IGST (inter-state), and HSN-based rates.
"""
from dataclasses import dataclass
from app.engine.rule_engine import OrderItem


@dataclass
class TaxLineItem:
    sku: str
    taxable_amount: float
    tax_rate_percent: float
    cgst_percent: float
    sgst_percent: float
    igst_percent: float
    cgst_amount: float
    sgst_amount: float
    igst_amount: float
    total_tax: float


@dataclass
class TaxSummary:
    subtotal: float
    total_discount: float
    taxable_amount: float
    total_cgst: float
    total_sgst: float
    total_igst: float
    total_tax: float
    grand_total: float
    line_items: list[TaxLineItem]


def calculate_taxes(
    items: list[dict],  # [{"sku": ..., "quantity": ..., "unit_price": ..., "tax_rate_percent": ...}]
    discount_amount: float = 0.0,
    is_intra_state: bool = True,  # True=CGST+SGST, False=IGST
) -> TaxSummary:
    """
    Calculate GST taxes for an order.

    For intra-state: tax_rate is split equally between CGST and SGST.
    For inter-state: full tax_rate applied as IGST.
    """
    subtotal = sum(i["quantity"] * i["unit_price"] for i in items)
    taxable_amount = max(subtotal - discount_amount, 0)

    # Distribute discount proportionally across items
    discount_ratio = discount_amount / subtotal if subtotal > 0 else 0

    line_items = []
    total_cgst = 0.0
    total_sgst = 0.0
    total_igst = 0.0

    for item in items:
        item_subtotal = item["quantity"] * item["unit_price"]
        item_discount = item_subtotal * discount_ratio
        item_taxable = item_subtotal - item_discount
        tax_rate = item.get("tax_rate_percent", 0.0)

        if is_intra_state:
            half_rate = tax_rate / 2
            cgst = round(item_taxable * half_rate / 100, 2)
            sgst = round(item_taxable * half_rate / 100, 2)
            igst = 0.0
        else:
            cgst = 0.0
            sgst = 0.0
            igst = round(item_taxable * tax_rate / 100, 2)

        total_tax_item = cgst + sgst + igst
        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst

        line_items.append(TaxLineItem(
            sku=item["sku"],
            taxable_amount=round(item_taxable, 2),
            tax_rate_percent=tax_rate,
            cgst_percent=tax_rate / 2 if is_intra_state else 0,
            sgst_percent=tax_rate / 2 if is_intra_state else 0,
            igst_percent=tax_rate if not is_intra_state else 0,
            cgst_amount=cgst,
            sgst_amount=sgst,
            igst_amount=igst,
            total_tax=total_tax_item,
        ))

    total_tax = total_cgst + total_sgst + total_igst
    grand_total = round(taxable_amount + total_tax, 2)

    return TaxSummary(
        subtotal=round(subtotal, 2),
        total_discount=round(discount_amount, 2),
        taxable_amount=round(taxable_amount, 2),
        total_cgst=round(total_cgst, 2),
        total_sgst=round(total_sgst, 2),
        total_igst=round(total_igst, 2),
        total_tax=round(total_tax, 2),
        grand_total=grand_total,
        line_items=line_items,
    )
