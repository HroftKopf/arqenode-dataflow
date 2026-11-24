"""Расчёты и агрегация очищенных продаж."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .errors import NoValidRecordsError
from .models import (
    MonthStat,
    ProductStat,
    Summary,
    ValidationResult,
    quantize_money,
)

ZERO_MONEY = Decimal("0.00")


def build_summary(validation: ValidationResult, input_name: str) -> Summary:
    """Рассчитать сводные показатели по проверенным записям."""

    if not validation.records:
        raise NoValidRecordsError(
            "Не найдено ни одной корректной записи. "
            f"Ошибок: {validation.error_rows}, дубликатов: {validation.duplicate_rows}. "
            "Отчёт не сформирован."
        )

    total_revenue = ZERO_MONEY
    total_units = 0
    unique_orders: set[str] = set()

    product_revenue: defaultdict[str, Decimal] = defaultdict(lambda: ZERO_MONEY)
    product_units: defaultdict[str, int] = defaultdict(int)
    product_orders: defaultdict[str, set[str]] = defaultdict(set)

    month_revenue: defaultdict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO_MONEY)
    month_units: defaultdict[tuple[int, int], int] = defaultdict(int)
    month_orders: defaultdict[tuple[int, int], set[str]] = defaultdict(set)

    for record in validation.records:
        total_revenue += record.revenue
        total_units += record.quantity
        unique_orders.add(record.order_id)

        product_revenue[record.product] += record.revenue
        product_units[record.product] += record.quantity
        product_orders[record.product].add(record.order_id)

        month_key = (record.sale_date.year, record.sale_date.month)
        month_revenue[month_key] += record.revenue
        month_units[month_key] += record.quantity
        month_orders[month_key].add(record.order_id)

    products = tuple(
        sorted(
            (
                ProductStat(
                    product=product,
                    revenue=quantize_money(revenue),
                    quantity=product_units[product],
                    orders=len(product_orders[product]),
                )
                for product, revenue in product_revenue.items()
            ),
            key=lambda item: (-item.revenue, item.product.casefold(), item.product),
        )
    )

    months = tuple(
        MonthStat(
            year=year,
            month=month,
            revenue=quantize_money(month_revenue[(year, month)]),
            quantity=month_units[(year, month)],
            orders=len(month_orders[(year, month)]),
        )
        for year, month in sorted(month_revenue)
    )

    return Summary(
        input_name=input_name,
        read_rows=validation.read_rows,
        blank_rows=validation.blank_rows,
        valid_rows=validation.valid_rows,
        error_rows=validation.error_rows,
        duplicate_rows=validation.duplicate_rows,
        rejected_rows=validation.rejected_rows,
        unique_orders=len(unique_orders),
        total_units=total_units,
        total_revenue=quantize_money(total_revenue),
        products=products,
        months=months,
        top_products=products[:10],
    )
