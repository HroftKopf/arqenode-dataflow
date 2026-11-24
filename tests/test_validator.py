from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from arqenode_dataflow.errors import InputError
from arqenode_dataflow.models import InputTable, ValidationResult
from arqenode_dataflow.reader import read_input
from arqenode_dataflow.validator import (
    parse_quantity,
    parse_sale_date,
    parse_unit_price,
    validate_table,
)

from .conftest import DEFAULT_HEADERS, VALID_ROWS


def _validate(path):
    return validate_table(read_input(path))


def test_validates_correct_rows_and_blank_rows(csv_factory) -> None:
    rows = (
        VALID_ROWS[0],
        ("", "", "", "", "", ""),
        ("   ", "  ", " ", " ", " ", " "),
    )
    result = _validate(csv_factory(rows))

    assert result.read_rows == 3
    assert result.blank_rows == 2
    assert result.valid_rows == 1
    assert result.error_rows == 0
    assert result.rejected_rows == 0


def test_missing_required_columns(csv_factory) -> None:
    headers = ("order_id", "date", "quantity", "unit_price")
    path = csv_factory(VALID_ROWS[:1], headers=headers)
    with pytest.raises(InputError, match="отсутствуют обязательные столбцы"):
        validate_table(read_input(path))


def test_empty_table_directly() -> None:
    with pytest.raises(InputError, match="не содержит строк"):
        validate_table(InputTable(headers=DEFAULT_HEADERS, rows=()))


def test_category_is_optional(csv_factory) -> None:
    headers = ("order_id", "date", "product", "quantity", "unit_price")
    rows = (("ORD-1", "2024-01-01", "Widget", 1, "10.00"),)
    result = _validate(csv_factory(rows, headers=headers))
    assert result.valid_rows == 1
    assert result.records[0].category is None


def test_normalizes_spaces_in_text_fields(csv_factory) -> None:
    rows = (("  ORD  42  ", "2024-01-01", "  Widget\t Pro  ", " Tools  ", " 2 ", " 10.00 "),)
    result = _validate(csv_factory(rows))
    record = result.records[0]
    assert record.order_id == "ORD 42"
    assert record.product == "Widget Pro"
    assert record.category == "Tools"
    assert record.quantity == 2
    assert record.unit_price == Decimal("10.00")


def test_invalid_dates_and_original_values(csv_factory) -> None:
    rows = (
        ("ORD-1", "2024-02-30", "Widget", "Tools", 1, "10.00"),
        ("ORD-2", "01/02/2024", "Widget", "Tools", 1, "10.00"),
        ("ORD-3", "next tuesday", "Widget", "Tools", 1, "10.00"),
        ("ORD-4", "", "Widget", "Tools", 1, "10.00"),
    )
    result = _validate(csv_factory(rows))

    assert result.valid_rows == 0
    assert result.error_rows == 4
    assert "2024-02-30" in result.rejected[0].reasons[0]
    assert "01/02/2024" in result.rejected[1].reasons[0]
    assert "не распознана дата" in result.rejected[2].reasons[0]
    assert "не заполнено" in result.rejected[3].reasons[0]
    assert result.rejected[1].values[1] == "01/02/2024"


@pytest.mark.parametrize(
    ("quantity", "price"),
    [
        ("0", "10.00"),
        ("-1", "10.00"),
        ("1.5", "10.00"),
        ("abc", "10.00"),
        ("1", "-0.01"),
        ("1", "abc"),
    ],
)
def test_invalid_quantity_and_price(csv_factory, quantity: str, price: str) -> None:
    rows = (("ORD-1", "2024-01-01", "Widget", "Tools", quantity, price),)
    result = _validate(csv_factory(rows))
    assert result.valid_rows == 0
    assert result.error_rows == 1


@pytest.mark.parametrize(
    "row",
    [
        ("", "2024-01-01", "Widget", "Tools", 1, "10.00"),
        ("ORD-1", "", "Widget", "Tools", 1, "10.00"),
        ("ORD-1", "2024-01-01", "", "Tools", 1, "10.00"),
        ("ORD-1", "2024-01-01", "Widget", "Tools", "", "10.00"),
        ("ORD-1", "2024-01-01", "Widget", "Tools", 1, ""),
    ],
)
def test_missing_required_values(csv_factory, row) -> None:
    result = _validate(csv_factory((row,)))
    assert result.error_rows == 1
    assert "не заполнено обязательное поле" in result.rejected[0].reasons[0]


def test_multiple_issues_in_one_row(csv_factory) -> None:
    rows = (("", "not-a-date", "", "Tools", -2, "abc"),)
    result = _validate(csv_factory(rows))
    assert result.error_rows == 1
    reasons = result.rejected[0].reasons
    assert len(reasons) == 5
    assert any("order_id" in reason for reason in reasons)
    assert any("product" in reason for reason in reasons)


def test_multiple_items_with_same_order_are_not_duplicates(csv_factory) -> None:
    rows = (
        ("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00"),
        ("ORD-1", "2024-01-01", "Gadget", "Tools", 1, "10.00"),
        ("ORD-1", "2024-01-01", "Widget", "Tools", 2, "10.00"),
    )
    result = _validate(csv_factory(rows))
    assert result.valid_rows == 3
    assert result.duplicate_rows == 0
    assert result.error_rows == 0


def test_exact_duplicates_after_normalization(csv_factory) -> None:
    rows = (
        ("ORD-1", "2024-01-01", "  Widget   Pro ", " Tools ", "2", "10.0"),
        ("ORD-1", "2024-01-01", "Widget Pro", "Tools", "2", "10.00"),
    )
    result = _validate(csv_factory(rows))

    assert result.valid_rows == 1
    assert result.duplicate_rows == 1
    assert result.error_rows == 0
    duplicate = result.rejected[0]
    assert duplicate.is_duplicate is True
    assert duplicate.row_number == 3
    assert duplicate.values[2] == "Widget Pro"
    assert "точный дубликат строки 2" in duplicate.reason_text


def test_same_data_with_different_category_is_not_duplicate(csv_factory) -> None:
    rows = (
        ("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00"),
        ("ORD-1", "2024-01-01", "Widget", "Other", 1, "10.00"),
    )
    result = _validate(csv_factory(rows))
    assert result.valid_rows == 2
    assert result.duplicate_rows == 0


def test_error_row_does_not_stop_processing(csv_factory) -> None:
    rows = (
        ("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00"),
        ("ORD-2", "broken", "Gadget", "Tools", 1, "5.00"),
        ("ORD-3", "2024-01-03", "Gizmo", "Tools", 1, "7.00"),
    )
    result = _validate(csv_factory(rows))
    assert result.valid_rows == 2
    assert result.error_rows == 1
    assert [record.order_id for record in result.records] == ["ORD-1", "ORD-3"]


def test_extra_values_reject_row(csv_factory) -> None:
    rows = (("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00", "extra"),)
    result = _validate(csv_factory(rows))
    assert result.error_rows == 1
    assert "больше значений" in result.rejected[0].reason_text


def test_rounds_unit_price_and_revenue_half_up(csv_factory) -> None:
    rows = (("ORD-1", "2024-01-01", "Widget", "Tools", 3, "10.005"),)
    result = _validate(csv_factory(rows))
    record = result.records[0]
    assert record.unit_price == Decimal("10.01")
    assert record.revenue == Decimal("30.03")


def test_accepts_decimal_comma_and_thousands_spaces(csv_factory) -> None:
    rows = (("ORD-1", "2024-01-01", "Widget", "Tools", 2, "1 234,565"),)
    result = _validate(csv_factory(rows, delimiter=";"))
    record = result.records[0]
    assert record.unit_price == Decimal("1234.57")
    assert record.revenue == Decimal("2469.14")


def test_parse_sale_date(sample_dates) -> None:
    sample_date, sample_datetime = sample_dates
    assert parse_sale_date("2024-02-29") == date(2024, 2, 29)
    assert parse_sale_date("29.02.2024") == date(2024, 2, 29)
    assert parse_sale_date(sample_date) == sample_date
    assert parse_sale_date(sample_datetime) == sample_date


@pytest.mark.parametrize(
    "value",
    ["2024-02-30", "29/02/2024", "01/02/2024", "tomorrow", "", None, 45292],
)
def test_parse_sale_date_rejects_unclear_values(value) -> None:
    with pytest.raises(ValueError):
        parse_sale_date(value)


def test_parse_quantity_variants() -> None:
    assert parse_quantity(3) == 3
    assert parse_quantity(3.0) == 3
    assert parse_quantity(Decimal("3")) == 3
    assert parse_quantity("+3") == 3
    assert parse_quantity(" 3 ") == 3


@pytest.mark.parametrize(
    "value",
    [0, -1, 1.5, Decimal("1.5"), "3.0", "abc", True, Decimal("NaN")],
)
def test_parse_quantity_rejects_invalid(value) -> None:
    with pytest.raises(ValueError, match="положительным целым"):
        parse_quantity(value)


@pytest.mark.parametrize("value", ["", None, "   "])
def test_parse_quantity_rejects_missing(value) -> None:
    with pytest.raises(ValueError, match="не заполнено"):
        parse_quantity(value)


def test_parse_unit_price_variants() -> None:
    assert parse_unit_price(Decimal("10.005")) == Decimal("10.01")
    assert parse_unit_price("10,505") == Decimal("10.51")
    assert parse_unit_price(" 1 234,56 ") == Decimal("1234.56")
    assert parse_unit_price(0) == Decimal("0.00")


@pytest.mark.parametrize(
    "value",
    [-1, "-0.01", "abc", "", None, True, "NaN", "Infinity"],
)
def test_parse_unit_price_rejects_invalid(value) -> None:
    with pytest.raises(ValueError):
        parse_unit_price(value)


def test_parse_unit_price_too_large() -> None:
    with pytest.raises(ValueError, match="слишком велика"):
        parse_unit_price("1e100000")


def test_validation_result_properties() -> None:
    empty = ValidationResult(read_rows=0, blank_rows=0, records=(), rejected=())
    assert empty.error_rows == 0
    assert empty.duplicate_rows == 0
