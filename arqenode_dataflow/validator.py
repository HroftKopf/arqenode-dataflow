"""Проверка, нормализация и очистка строк продаж."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import InputError
from .models import (
    REQUIRED_COLUMNS,
    SCHEMA_COLUMNS,
    InputTable,
    RejectedRow,
    SaleRecord,
    ValidationResult,
    quantize_money,
)

DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d.%m.%Y")
DATE_FORMAT_HINT = "YYYY-MM-DD или DD.MM.YYYY"
_QUANTITY_RE = re.compile(r"[+]?\d+")
_WHITESPACE_RE = re.compile(r"[\s\u00a0\u202f]+")


def normalize_text(value: object) -> str:
    """Схлопнуть любые пробельные последовательности и обрезать края."""

    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _short_repr(value: object) -> str:
    text = normalize_text(value)
    return repr(text if len(text) <= 60 else f"{text[:57]}...")


def parse_sale_date(value: object) -> date:
    """Разобрать дату только в однозначно документированных форматах."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if _is_blank(value):
        raise ValueError("не заполнено обязательное поле «date»")
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        raise ValueError(
            "дата должна быть записана текстом в формате "
            f"{DATE_FORMAT_HINT}; числовое значение не угадывается"
        )

    text = normalize_text(value)
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise ValueError(
        f"не распознана дата {_short_repr(value)}. "
        f"Используйте однозначный формат {DATE_FORMAT_HINT}"
    )


def parse_quantity(value: object) -> int:
    """Проверить положительное целое количество."""

    if _is_blank(value):
        raise ValueError("не заполнено обязательное поле «quantity»")
    if isinstance(value, bool):
        raise ValueError("количество должно быть положительным целым числом")

    if isinstance(value, int):
        quantity = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError("количество должно быть положительным целым числом")
        quantity = int(value)
    elif isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("количество должно быть положительным целым числом")
        quantity = int(value)
    else:
        text = normalize_text(value)
        if not _QUANTITY_RE.fullmatch(text):
            raise ValueError("количество должно быть положительным целым числом")
        quantity = int(text)

    if quantity <= 0:
        raise ValueError("количество должно быть положительным целым числом")
    return quantity


def parse_unit_price(value: object) -> Decimal:
    """Проверить неотрицательную цену и округлить её до копеек."""

    if _is_blank(value):
        raise ValueError("не заполнено обязательное поле «unit_price»")
    if isinstance(value, bool):
        raise ValueError("цена должна быть неотрицательным числом")

    if isinstance(value, Decimal):
        price = value
    elif isinstance(value, (int, float)):
        price = Decimal(str(value))
    else:
        text = normalize_text(value)
        compact = _WHITESPACE_RE.sub("", text)
        if compact.count(",") == 1 and "." not in compact:
            compact = compact.replace(",", ".")
        try:
            price = Decimal(compact)
        except InvalidOperation as exc:
            raise ValueError(
                f"цена должна быть неотрицательным числом (получено: {_short_repr(value)})"
            ) from exc

    if not price.is_finite():
        raise ValueError("цена должна быть конечным неотрицательным числом")
    if price < 0:
        raise ValueError("цена не может быть отрицательной")
    try:
        return quantize_money(price)
    except InvalidOperation as exc:
        raise ValueError("цена слишком велика для денежного расчёта") from exc


def _raw_values(row_values: tuple[object, ...], positions: dict[str, int]) -> tuple[str, ...]:
    result: list[str] = []
    for column in SCHEMA_COLUMNS:
        position = positions.get(column)
        value = row_values[position] if position is not None and position < len(row_values) else ""
        result.append(_display_value(value))
    return tuple(result)


def _field_value(
    row_values: tuple[object, ...],
    positions: dict[str, int],
    column: str,
) -> object:
    position = positions.get(column)
    if position is None or position >= len(row_values):
        return ""
    return row_values[position]


def _required_text(value: object, field_name: str, issues: list[str]) -> str:
    if _is_blank(value):
        issues.append(f"не заполнено обязательное поле «{field_name}»")
        return ""
    return normalize_text(value)


def validate_table(table: InputTable) -> ValidationResult:
    """Проверить таблицу, собрать ошибки и удалить точные дубликаты.

    Строка с одинаковым ``order_id`` не считается дубликатом: один заказ может
    содержать несколько товарных позиций. Дубликатом считается совпадение всех
    полей записи после нормализации.
    """

    missing = [column for column in REQUIRED_COLUMNS if column not in table.headers]
    if missing:
        found = ", ".join(table.headers) if table.headers else "ни одного"
        raise InputError(
            "В файле отсутствуют обязательные столбцы: "
            f"{', '.join(missing)}. Найденные столбцы: {found}. "
            "Проверьте заголовок таблицы."
        )
    if not table.rows:
        raise InputError("Файл не содержит строк данных.")

    positions = {
        column: table.headers.index(column) for column in SCHEMA_COLUMNS if column in table.headers
    }
    records: list[SaleRecord] = []
    rejected: list[RejectedRow] = []
    seen: dict[tuple[Any, ...], SaleRecord] = {}
    blank_rows = 0

    for row in table.rows:
        if not row.issues and all(_is_blank(value) for value in row.values):
            blank_rows += 1
            continue

        raw_values = _raw_values(row.values, positions)
        issues = list(row.issues)
        row_values = row.values

        order_id = _required_text(
            _field_value(row_values, positions, "order_id"),
            "order_id",
            issues,
        )
        product = _required_text(
            _field_value(row_values, positions, "product"),
            "product",
            issues,
        )

        sale_date: date | None = None
        try:
            sale_date = parse_sale_date(_field_value(row_values, positions, "date"))
        except ValueError as exc:
            issues.append(str(exc))

        quantity: int | None = None
        try:
            quantity = parse_quantity(_field_value(row_values, positions, "quantity"))
        except ValueError as exc:
            issues.append(str(exc))

        unit_price: Decimal | None = None
        try:
            unit_price = parse_unit_price(_field_value(row_values, positions, "unit_price"))
        except ValueError as exc:
            issues.append(str(exc))

        category_value = _field_value(row_values, positions, "category")
        category = None if _is_blank(category_value) else normalize_text(category_value)

        if issues or sale_date is None or quantity is None or unit_price is None:
            rejected.append(
                RejectedRow(
                    row_number=row.row_number,
                    values=raw_values,
                    reasons=tuple(issues),
                )
            )
            continue

        revenue = quantize_money(unit_price * quantity)
        signature = (order_id, sale_date, product, quantity, unit_price, category)
        first = seen.get(signature)
        if first is not None:
            rejected.append(
                RejectedRow(
                    row_number=row.row_number,
                    values=raw_values,
                    reasons=(
                        "точный дубликат строки "
                        f"{first.row_number} после нормализации; оставлено первое вхождение",
                    ),
                    is_duplicate=True,
                )
            )
            continue

        record = SaleRecord(
            row_number=row.row_number,
            order_id=order_id,
            sale_date=sale_date,
            product=product,
            quantity=quantity,
            unit_price=unit_price,
            category=category,
            revenue=revenue,
        )
        seen[signature] = record
        records.append(record)

    return ValidationResult(
        read_rows=len(table.rows),
        blank_rows=blank_rows,
        records=tuple(records),
        rejected=tuple(rejected),
    )
