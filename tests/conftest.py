from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

DEFAULT_HEADERS: tuple[str, ...] = (
    "order_id",
    "date",
    "product",
    "category",
    "quantity",
    "unit_price",
)
VALID_ROWS: tuple[tuple[object, ...], ...] = (
    ("ORD-1", "2024-01-05", "Widget Pro", "Tools", 2, "10.00"),
    ("ORD-1", "2024-01-05", "Gadget Mini", "Tools", 1, "5.50"),
    ("ORD-2", "2024-02-10", "Widget Pro", "Tools", 3, "10.00"),
)


@pytest.fixture
def csv_factory(tmp_path: Path) -> Callable[..., Path]:
    counter = 0

    def make(
        rows: Iterable[Sequence[object]],
        *,
        headers: Sequence[object] = DEFAULT_HEADERS,
        delimiter: str = ",",
        encoding: str = "utf-8",
        raw_text: str | None = None,
        raw_bytes: bytes | None = None,
        name: str | None = None,
    ) -> Path:
        nonlocal counter
        counter += 1
        path = tmp_path / (name or f"input-{counter}.csv")
        if raw_bytes is not None:
            path.write_bytes(raw_bytes)
            return path
        if raw_text is not None:
            text = raw_text
        else:
            stream = io.StringIO(newline="")
            writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)
            text = stream.getvalue()
        path.write_bytes(text.encode(encoding))
        return path

    return make


@pytest.fixture
def xlsx_factory(tmp_path: Path) -> Callable[..., Path]:
    counter = 0

    def make(
        rows: Iterable[Sequence[object]],
        *,
        headers: Sequence[object] = DEFAULT_HEADERS,
        name: str | None = None,
    ) -> Path:
        nonlocal counter
        counter += 1
        path = tmp_path / (name or f"input-{counter}.xlsx")
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(list(headers))
        for row in rows:
            worksheet.append(list(row))
        workbook.save(path)
        workbook.close()
        return path

    return make


@pytest.fixture
def sample_dates() -> tuple[date, datetime]:
    return date(2024, 3, 1), datetime(2024, 3, 1, 12, 30)
