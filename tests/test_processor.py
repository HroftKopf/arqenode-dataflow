from __future__ import annotations

from decimal import Decimal

import pytest

from arqenode_dataflow.errors import NoValidRecordsError
from arqenode_dataflow.models import RejectedRow, ValidationResult
from arqenode_dataflow.processor import build_summary
from arqenode_dataflow.reader import read_input
from arqenode_dataflow.validator import validate_table


def _summary(path):
    validation = validate_table(read_input(path))
    return build_summary(validation, path.name)


def test_summary_metrics(csv_factory) -> None:
    rows = (
        ("A", "2024-01-05", "Widget", "Tools", 2, "10.00"),
        ("A", "2024-01-20", "Gadget", "Tools", 1, "5.50"),
        ("B", "2024-02-01", "Widget", "Tools", 3, "10.00"),
    )
    summary = _summary(csv_factory(rows))

    assert summary.read_rows == 3
    assert summary.valid_rows == 3
    assert summary.error_rows == 0
    assert summary.duplicate_rows == 0
    assert summary.rejected_rows == 0
    assert summary.unique_orders == 2
    assert summary.total_units == 6
    assert summary.total_revenue == Decimal("55.50")

    widget = next(product for product in summary.products if product.product == "Widget")
    assert widget.revenue == Decimal("50.00")
    assert widget.quantity == 5
    assert widget.orders == 2

    assert [month.key for month in summary.months] == ["2024-01", "2024-02"]
    assert summary.months[0].revenue == Decimal("25.50")
    assert summary.months[1].revenue == Decimal("30.00")


def test_monthly_aggregation_is_chronological(csv_factory) -> None:
    rows = (
        ("A", "2024-12-31", "Widget", "Tools", 1, "10.00"),
        ("B", "2023-05-15", "Widget", "Tools", 1, "20.00"),
        ("C", "2024-01-01", "Widget", "Tools", 1, "30.00"),
        ("D", "2023-05-20", "Widget", "Tools", 1, "40.00"),
    )
    summary = _summary(csv_factory(rows))

    assert [month.key for month in summary.months] == ["2023-05", "2024-01", "2024-12"]
    assert summary.months[0].revenue == Decimal("60.00")
    assert summary.months[2].orders == 1


def test_top_products_limit_and_page_sorted_products(csv_factory) -> None:
    rows = tuple(
        (
            f"ORD-{index}",
            "2024-03-01",
            f"Товар {index:02d}",
            "Категория",
            1,
            f"{100 - index}.00",
        )
        for index in range(12)
    )
    summary = _summary(csv_factory(rows))

    assert len(summary.products) == 12
    assert len(summary.top_products) == 10
    assert summary.products[0].product == "Товар 00"
    assert summary.products[-1].product == "Товар 11"
    assert summary.top_products[-1].product == "Товар 09"
    revenues = [product.revenue for product in summary.products]
    assert revenues == sorted(revenues, reverse=True)


def test_equal_revenue_products_are_sorted_deterministically(csv_factory) -> None:
    rows = (
        ("A", "2024-01-01", "beta", "X", 1, "10.00"),
        ("B", "2024-01-01", "Alpha", "X", 1, "10.00"),
        ("C", "2024-01-01", "gamma", "X", 1, "10.00"),
    )
    summary = _summary(csv_factory(rows))
    assert [product.product for product in summary.products] == ["Alpha", "beta", "gamma"]


def test_no_valid_records_raises() -> None:
    validation = ValidationResult(
        read_rows=1,
        blank_rows=0,
        records=(),
        rejected=(
            RejectedRow(
                row_number=2,
                values=("A", "bad", "Widget", "1", "10.00", ""),
                reasons=("некорректная дата",),
            ),
        ),
    )
    with pytest.raises(NoValidRecordsError, match="Не найдено ни одной корректной записи"):
        build_summary(validation, "bad.csv")


def test_reproducible_summary(csv_factory) -> None:
    path = csv_factory(
        (
            ("A", "2024-01-05", "Widget", "Tools", 2, "10.00"),
            ("B", "2024-02-01", "Gadget", "Tools", 3, "5.50"),
        )
    )
    assert _summary(path) == _summary(path)


def test_summary_preserves_input_name(csv_factory) -> None:
    path = csv_factory(
        (("A", "2024-01-05", "Widget", "Tools", 2, "10.00"),),
        name="custom-name.csv",
    )
    summary = _summary(path)
    assert summary.input_name == "custom-name.csv"
