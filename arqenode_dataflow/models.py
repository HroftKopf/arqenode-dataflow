"""Модели данных Arqenode DataFlow.

Модели намеренно небольшие: они отделяют «сырые» строки файла от проверенных
записей и агрегатов, но не вводят лишних слоёв абстракции.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

MONEY_QUANT = Decimal("0.01")

#: Фиксированная схема входного файла. Порядок используется в листе «Ошибки».
SCHEMA_COLUMNS: tuple[str, ...] = (
    "order_id",
    "date",
    "product",
    "quantity",
    "unit_price",
    "category",
)
REQUIRED_COLUMNS: tuple[str, ...] = (
    "order_id",
    "date",
    "product",
    "quantity",
    "unit_price",
)
OPTIONAL_COLUMNS: tuple[str, ...] = ("category",)


def quantize_money(value: Decimal) -> Decimal:
    """Округлить денежное значение до копеек по правилу ROUND_HALF_UP."""

    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class SourceRow:
    """Строка, прочитанная из CSV/XLSX, ещё без проверки."""

    row_number: int
    values: tuple[object, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InputTable:
    """Таблица с нормализованными именами столбцов."""

    headers: tuple[str, ...]
    rows: tuple[SourceRow, ...]


@dataclass(frozen=True, slots=True)
class SaleRecord:
    """Проверенная товарная позиция заказа."""

    row_number: int
    order_id: str
    sale_date: date
    product: str
    quantity: int
    unit_price: Decimal
    category: str | None
    revenue: Decimal


@dataclass(frozen=True, slots=True)
class RejectedRow:
    """Отклонённая строка с исходными значениями и причиной."""

    row_number: int
    values: tuple[str, ...]
    reasons: tuple[str, ...]
    is_duplicate: bool = False

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Результат проверки и удаления точных дубликатов."""

    read_rows: int
    blank_rows: int
    records: tuple[SaleRecord, ...]
    rejected: tuple[RejectedRow, ...]

    @property
    def valid_rows(self) -> int:
        return len(self.records)

    @property
    def error_rows(self) -> int:
        return sum(1 for item in self.rejected if not item.is_duplicate)

    @property
    def duplicate_rows(self) -> int:
        return sum(1 for item in self.rejected if item.is_duplicate)

    @property
    def rejected_rows(self) -> int:
        return len(self.rejected)


@dataclass(frozen=True, slots=True)
class ProductStat:
    """Агрегат по одному товару."""

    product: str
    revenue: Decimal
    quantity: int
    orders: int


@dataclass(frozen=True, slots=True)
class MonthStat:
    """Агрегат по календарному месяцу."""

    year: int
    month: int
    revenue: Decimal
    quantity: int
    orders: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True, slots=True)
class Summary:
    """Все показатели, необходимые для листа «Сводка» и CLI."""

    input_name: str
    read_rows: int
    blank_rows: int
    valid_rows: int
    error_rows: int
    duplicate_rows: int
    rejected_rows: int
    unique_orders: int
    total_units: int
    total_revenue: Decimal
    products: tuple[ProductStat, ...]
    months: tuple[MonthStat, ...]
    top_products: tuple[ProductStat, ...]


@dataclass(frozen=True, slots=True)
class OutputPaths:
    """Пути успешно сформированных файлов."""

    report: Path
    cleaned_csv: Path
