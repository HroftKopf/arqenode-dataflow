"""Формирование Excel-отчёта и очищенного CSV."""

from __future__ import annotations

import contextlib
import csv
import os
import re
import tempfile
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .errors import OutputError
from .models import (
    OutputPaths,
    RejectedRow,
    SaleRecord,
    Summary,
    ValidationResult,
    quantize_money,
)

REPORT_FILENAME = "report.xlsx"
CLEANED_FILENAME = "cleaned.csv"

BRAND_DARK = "0B3B36"
BRAND = "0F766E"
BRAND_LIGHT = "E6F4F1"
ACCENT = "D97706"
TEXT = "1F2937"
MUTED = "64748B"
BORDER = "CBD5E1"
WHITE = "FFFFFF"
ROW_ALT = "F8FAFC"

MONEY_FORMAT = "#,##0.00"
INTEGER_FORMAT = "#,##0"
DATE_FORMAT = "DD.MM.YYYY"

DATA_HEADERS = (
    "Строка",
    "Заказ",
    "Дата",
    "Товар",
    "Категория",
    "Количество",
    "Цена, ₽",
    "Выручка, ₽",
)
ERROR_HEADERS = (
    "Строка",
    "Заказ",
    "Дата",
    "Товар",
    "Количество",
    "Цена",
    "Категория",
    "Причина",
)
PRODUCT_HEADERS = ("Товар", "Выручка, ₽", "Количество, шт.", "Заказов")
MONTH_HEADERS = ("Месяц", "Выручка, ₽", "Количество, шт.", "Заказов")
CSV_HEADERS = (
    "order_id",
    "date",
    "product",
    "category",
    "quantity",
    "unit_price",
    "revenue",
)

HEADER_FONT = Font(name="Calibri", size=11, bold=True, color=WHITE)
TITLE_FONT = Font(name="Calibri", size=16, bold=True, color=WHITE)
SECTION_FONT = Font(name="Calibri", size=11, bold=True, color=WHITE)
BODY_FONT = Font(name="Calibri", size=11, color=TEXT)
MUTED_FONT = Font(name="Calibri", size=9, italic=True, color=MUTED)
ERROR_REASON_FONT = Font(name="Calibri", size=10, color="92400E")

BRAND_FILL = PatternFill("solid", fgColor=BRAND)
DARK_FILL = PatternFill("solid", fgColor=BRAND_DARK)
LIGHT_FILL = PatternFill("solid", fgColor=BRAND_LIGHT)
ALT_FILL = PatternFill("solid", fgColor=ROW_ALT)
ERROR_HEADER_FILL = PatternFill("solid", fgColor="92400E")

THIN_BOTTOM = Border(bottom=Side(style="thin", color=BORDER))
THIN_ALL = Border(
    left=Side(style="thin", color=BORDER),
    right=Side(style="thin", color=BORDER),
    top=Side(style="thin", color=BORDER),
    bottom=Side(style="thin", color=BORDER),
)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")
LEFT_INDENT = Alignment(horizontal="left", vertical="center", indent=1)
RIGHT = Alignment(horizontal="right", vertical="center")
WRAP_LEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)

_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def protect_csv_text(value: str) -> str:
    """Нейтрализовать формулу в CSV-значении, сохранив читаемый текст."""

    if not value:
        return value
    stripped = value.lstrip(" \t\r\n\u00a0")
    if value.startswith(("\t", "\r", "\n")) or stripped.startswith(
        ("=", "+", "-", "@", "\t", "\r")
    ):
        return "'" + value
    return value


def _safe_excel_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = _ILLEGAL_XML_RE.sub("", text)
    if len(text) > 32767:
        text = text[:32740] + "… [значение обрезано Excel]"
    return text


def _set_text(cell, value: object) -> None:
    """Записать текст как строку, а не как формулу Excel."""

    cell.value = _safe_excel_text(value)
    if cell.value != "":
        cell.data_type = "s"


def _style_header_row(
    worksheet: Worksheet,
    row: int,
    *,
    columns: int,
    fill: PatternFill = BRAND_FILL,
) -> None:
    for column in range(1, columns + 1):
        cell = worksheet.cell(row=row, column=column)
        cell.fill = fill
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = THIN_BOTTOM
    worksheet.row_dimensions[row].height = 26


def _configure_sheet(worksheet: Worksheet, *, tab_color: str) -> None:
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_view.zoomScale = 100
    worksheet.sheet_properties.tabColor = tab_color
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(worksheet.max_column)}{worksheet.max_row}"


def _write_data_sheet(worksheet: Worksheet, records: tuple[SaleRecord, ...]) -> None:
    worksheet.title = "Данные"
    worksheet.append(DATA_HEADERS)
    _style_header_row(worksheet, 1, columns=len(DATA_HEADERS))

    for row_index, record in enumerate(records, start=2):
        worksheet.cell(row=row_index, column=1, value=record.row_number)
        worksheet.cell(row=row_index, column=1).number_format = INTEGER_FORMAT
        _set_text(worksheet.cell(row=row_index, column=2), record.order_id)
        date_cell = worksheet.cell(row=row_index, column=3, value=record.sale_date)
        date_cell.number_format = DATE_FORMAT
        _set_text(worksheet.cell(row=row_index, column=4), record.product)
        _set_text(worksheet.cell(row=row_index, column=5), record.category or "")
        worksheet.cell(row=row_index, column=6, value=record.quantity)
        worksheet.cell(row=row_index, column=6).number_format = INTEGER_FORMAT
        worksheet.cell(row=row_index, column=7, value=record.unit_price)
        worksheet.cell(row=row_index, column=7).number_format = MONEY_FORMAT
        worksheet.cell(row=row_index, column=8, value=record.revenue)
        worksheet.cell(row=row_index, column=8).number_format = MONEY_FORMAT
        for column in range(1, 9):
            cell = worksheet.cell(row=row_index, column=column)
            cell.font = BODY_FONT
            if column in (1, 6, 7, 8):
                cell.alignment = RIGHT
            elif column == 3:
                cell.alignment = CENTER
            else:
                cell.alignment = LEFT

    widths = (9, 18, 12, 38, 20, 13, 14, 15)
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    _configure_sheet(worksheet, tab_color=BRAND)


def _write_errors_sheet(
    worksheet: Worksheet,
    rejected: tuple[RejectedRow, ...],
) -> None:
    worksheet.title = "Ошибки"
    worksheet.append(ERROR_HEADERS)
    _style_header_row(worksheet, 1, columns=len(ERROR_HEADERS), fill=ERROR_HEADER_FILL)

    for row_index, item in enumerate(rejected, start=2):
        worksheet.cell(row=row_index, column=1, value=item.row_number)
        worksheet.cell(row=row_index, column=1).number_format = INTEGER_FORMAT
        for offset, value in enumerate(item.values, start=2):
            _set_text(worksheet.cell(row=row_index, column=offset), value)
        _set_text(worksheet.cell(row=row_index, column=8), item.reason_text)
        if item.is_duplicate:
            for column in range(1, 9):
                worksheet.cell(row=row_index, column=column).fill = LIGHT_FILL

        for column in range(1, 9):
            cell = worksheet.cell(row=row_index, column=column)
            cell.font = BODY_FONT
            cell.border = THIN_BOTTOM
            cell.alignment = WRAP_LEFT if column == 8 else LEFT
        worksheet.cell(row=row_index, column=8).font = ERROR_REASON_FONT

    widths = (9, 18, 14, 30, 14, 14, 20, 72)
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    _configure_sheet(worksheet, tab_color=ACCENT)


def _write_section(worksheet: Worksheet, row: int, title: str) -> None:
    worksheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    cell = worksheet.cell(row=row, column=1, value=title)
    cell.fill = DARK_FILL
    cell.font = SECTION_FONT
    cell.alignment = LEFT
    worksheet.row_dimensions[row].height = 23


def _write_summary_sheet(worksheet: Worksheet, summary: Summary) -> None:
    worksheet.title = "Сводка"
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_properties.tabColor = BRAND_DARK
    widths = (8, 44, 22, 20)
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width

    worksheet.merge_cells("A1:D1")
    title_cell = worksheet["A1"]
    title_cell.value = "Arqenode DataFlow — сводка по продажам"
    title_cell.fill = DARK_FILL
    title_cell.font = TITLE_FONT
    title_cell.alignment = LEFT
    worksheet.row_dimensions[1].height = 36

    worksheet.merge_cells("A2:D2")
    subtitle = worksheet["A2"]
    subtitle.value = f"Исходный файл: {summary.input_name}"
    subtitle.fill = LIGHT_FILL
    subtitle.font = Font(name="Calibri", size=10, color=MUTED)
    subtitle.alignment = LEFT
    worksheet.row_dimensions[2].height = 22

    def metrics_header(row: int) -> None:
        label = worksheet.cell(row=row, column=2, value="Показатель")
        value = worksheet.cell(row=row, column=3, value="Значение")
        worksheet.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
        for cell in (label, value):
            cell.fill = BRAND_FILL
            cell.font = HEADER_FONT
            cell.alignment = CENTER
            cell.border = THIN_ALL
        worksheet.row_dimensions[row].height = 23

    def metric(row: int, label: str, value: object, number_format: str) -> None:
        label_cell = worksheet.cell(row=row, column=2, value=label)
        value_cell = worksheet.cell(row=row, column=3, value=value)
        worksheet.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
        label_cell.font = BODY_FONT
        label_cell.alignment = LEFT
        label_cell.border = THIN_ALL
        value_cell.font = BODY_FONT
        value_cell.alignment = RIGHT
        value_cell.number_format = number_format
        value_cell.border = THIN_ALL
        worksheet.cell(row=row, column=4).border = THIN_ALL
        if row % 2 == 0:
            for column in range(2, 5):
                worksheet.cell(row=row, column=column).fill = ALT_FILL

    row = 4
    _write_section(worksheet, row, "Обработка данных")
    row += 1
    metrics_header(row)
    processing = (
        ("Прочитано строк", summary.read_rows),
        ("Пустых строк", summary.blank_rows),
        ("Корректных строк", summary.valid_rows),
        ("Ошибок", summary.error_rows),
        ("Дубликатов", summary.duplicate_rows),
        ("Отклонено всего", summary.rejected_rows),
    )
    for label, value in processing:
        row += 1
        metric(row, label, value, INTEGER_FORMAT)

    row += 2
    _write_section(worksheet, row, "Продажи")
    row += 1
    metrics_header(row)
    average_order = (
        quantize_money(summary.total_revenue / summary.unique_orders)
        if summary.unique_orders
        else Decimal("0.00")
    )
    sales = (
        ("Уникальных заказов", summary.unique_orders, INTEGER_FORMAT),
        ("Продано единиц товаров", summary.total_units, INTEGER_FORMAT),
        ("Общая выручка, ₽", summary.total_revenue, MONEY_FORMAT),
        ("Средняя выручка на заказ, ₽", average_order, MONEY_FORMAT),
    )
    for label, value, number_format in sales:
        row += 1
        metric(row, label, value, number_format)

    row += 2
    _write_section(worksheet, row, "Топ-10 товаров по выручке")
    row += 1
    top_headers = ("№", "Товар", "Выручка, ₽", "Количество, шт.")
    for column, header in enumerate(top_headers, start=1):
        cell = worksheet.cell(row=row, column=column, value=header)
        cell.fill = BRAND_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = THIN_ALL
    worksheet.row_dimensions[row].height = 23

    for place, product in enumerate(summary.top_products, start=1):
        row += 1
        cells = (
            (1, place, INTEGER_FORMAT),
            (2, product.product, None),
            (3, product.revenue, MONEY_FORMAT),
            (4, product.quantity, INTEGER_FORMAT),
        )
        for column, value, number_format in cells:
            cell = worksheet.cell(row=row, column=column)
            if column == 2:
                _set_text(cell, value)
            else:
                cell.value = value
                if number_format:
                    cell.number_format = number_format
            cell.font = BODY_FONT
            cell.alignment = LEFT_INDENT if column == 2 else CENTER if column == 1 else RIGHT
            cell.border = THIN_ALL
            if place % 2 == 0:
                cell.fill = ALT_FILL

    row += 2
    worksheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    note = worksheet.cell(row=row, column=1)
    note.value = (
        "Денежные значения рассчитаны через Decimal и округлены до двух знаков "
        "после запятой по правилу ROUND_HALF_UP."
    )
    note.font = MUTED_FONT
    note.alignment = LEFT


def _write_products_sheet(worksheet: Worksheet, summary: Summary) -> None:
    worksheet.title = "Товары"
    worksheet.append(PRODUCT_HEADERS)
    _style_header_row(worksheet, 1, columns=len(PRODUCT_HEADERS))
    for row_index, product in enumerate(summary.products, start=2):
        _set_text(worksheet.cell(row=row_index, column=1), product.product)
        worksheet.cell(row=row_index, column=2, value=product.revenue)
        worksheet.cell(row=row_index, column=2).number_format = MONEY_FORMAT
        worksheet.cell(row=row_index, column=3, value=product.quantity)
        worksheet.cell(row=row_index, column=3).number_format = INTEGER_FORMAT
        worksheet.cell(row=row_index, column=4, value=product.orders)
        worksheet.cell(row=row_index, column=4).number_format = INTEGER_FORMAT
        for column in range(1, 5):
            cell = worksheet.cell(row=row_index, column=column)
            cell.font = BODY_FONT
            cell.border = THIN_BOTTOM
            cell.alignment = LEFT if column == 1 else RIGHT
        if row_index % 2 == 0:
            for column in range(1, 5):
                worksheet.cell(row=row_index, column=column).fill = ALT_FILL

    for index, width in enumerate((44, 19, 17, 12), start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    _configure_sheet(worksheet, tab_color=BRAND)


def _write_months_sheet(worksheet: Worksheet, summary: Summary) -> None:
    worksheet.title = "Месяцы"
    worksheet.append(MONTH_HEADERS)
    _style_header_row(worksheet, 1, columns=len(MONTH_HEADERS))
    for row_index, month in enumerate(summary.months, start=2):
        cell = worksheet.cell(row=row_index, column=1, value=month.key)
        cell.alignment = CENTER
        worksheet.cell(row=row_index, column=2, value=month.revenue)
        worksheet.cell(row=row_index, column=2).number_format = MONEY_FORMAT
        worksheet.cell(row=row_index, column=3, value=month.quantity)
        worksheet.cell(row=row_index, column=3).number_format = INTEGER_FORMAT
        worksheet.cell(row=row_index, column=4, value=month.orders)
        worksheet.cell(row=row_index, column=4).number_format = INTEGER_FORMAT
        for column in range(1, 5):
            row_cell = worksheet.cell(row=row_index, column=column)
            row_cell.font = BODY_FONT
            row_cell.border = THIN_BOTTOM
            if column > 1:
                row_cell.alignment = RIGHT
        if row_index % 2 == 0:
            for column in range(1, 5):
                worksheet.cell(row=row_index, column=column).fill = ALT_FILL

    for index, width in enumerate((14, 19, 17, 12), start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width

    chart = BarChart()
    chart.type = "col"
    chart.style = 10
    chart.title = "Выручка по месяцам"
    chart.y_axis.title = "Выручка, ₽"
    chart.x_axis.title = "Месяц"
    chart.height = 9.5
    chart.width = 22
    chart.legend = None
    chart.gapWidth = 55
    data = Reference(
        worksheet,
        min_col=2,
        min_row=1,
        max_row=1 + len(summary.months),
    )
    categories = Reference(
        worksheet,
        min_col=1,
        min_row=2,
        max_row=1 + len(summary.months),
    )
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    chart.y_axis.numFmt = "#,##0"
    if chart.series:
        chart.series[0].graphicalProperties.solidFill = BRAND
        chart.series[0].graphicalProperties.line.solidFill = BRAND_DARK
    worksheet.add_chart(chart, "A15")

    _configure_sheet(worksheet, tab_color=ACCENT)
    worksheet.auto_filter.ref = f"A1:D{1 + len(summary.months)}"


def _build_workbook(
    path: Path,
    validation: ValidationResult,
    summary: Summary,
) -> None:
    workbook = Workbook()
    data_sheet = workbook.active
    assert data_sheet is not None
    _write_data_sheet(data_sheet, validation.records)
    _write_errors_sheet(workbook.create_sheet(), validation.rejected)
    _write_summary_sheet(workbook.create_sheet(), summary)
    _write_products_sheet(workbook.create_sheet(), summary)
    _write_months_sheet(workbook.create_sheet(), summary)

    workbook.properties.creator = "Arqenode DataFlow"
    workbook.properties.title = "Отчёт по продажам"
    workbook.properties.subject = "Автоматическая обработка табличных данных"
    workbook.active = workbook.sheetnames.index("Сводка")
    workbook.save(path)


def _write_cleaned_csv(path: Path, records: Iterable[SaleRecord]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CSV_HEADERS)
        for record in records:
            writer.writerow(
                (
                    protect_csv_text(record.order_id),
                    record.sale_date.isoformat(),
                    protect_csv_text(record.product),
                    protect_csv_text(record.category or ""),
                    record.quantity,
                    f"{record.unit_price:.2f}",
                    f"{record.revenue:.2f}",
                )
            )


def _temp_path(directory: Path, prefix: str, suffix: str) -> Path:
    handle, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)
    os.close(handle)
    return Path(name)


def _prepare_output_directory(output_dir: Path) -> None:
    try:
        if output_dir.exists() and not output_dir.is_dir():
            raise OutputError(f"Путь вывода существует, но не является каталогом: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
    except OutputError:
        raise
    except OSError as exc:
        raise OutputError(f"Не удалось создать каталог результатов {output_dir}: {exc}") from exc


def write_outputs(
    validation: ValidationResult,
    summary: Summary,
    output_dir: str | Path,
    *,
    input_path: str | Path,
    force: bool = False,
) -> OutputPaths:
    """Атомарно записать ``report.xlsx`` и ``cleaned.csv``.

    По умолчанию существующие файлы не перезаписываются: пользователь должен
    явно передать ``--force``. Исходный файл не может быть целью записи.
    """

    directory = Path(output_dir)
    _prepare_output_directory(directory)
    report_path = directory / REPORT_FILENAME
    cleaned_path = directory / CLEANED_FILENAME

    source = Path(input_path).resolve()
    for target in (report_path, cleaned_path):
        if target.resolve() == source:
            raise OutputError(
                f"Результат не может перезаписать исходный файл: {target}. "
                "Выберите другой каталог --output."
            )

    targets = (report_path, cleaned_path)
    for target in targets:
        if target.exists() and target.is_dir():
            raise OutputError(f"Ожидался файл, но найден каталог: {target}")
    existing = [str(target) for target in targets if target.exists()]
    if existing and not force:
        raise OutputError(
            "Файлы результата уже существуют:\n  "
            + "\n  ".join(existing)
            + "\nЧтобы осознанно перезаписать их, используйте --force. "
            "Исходный файл при этом не изменяется."
        )

    report_temp: Path | None = None
    cleaned_temp: Path | None = None
    try:
        report_temp = _temp_path(directory, ".report-", ".xlsx")
        cleaned_temp = _temp_path(directory, ".cleaned-", ".csv")
        _build_workbook(report_temp, validation, summary)
        _write_cleaned_csv(cleaned_temp, validation.records)

        if not force:
            appeared = [str(target) for target in targets if target.exists()]
            if appeared:
                raise OutputError(
                    "Файлы результата появились во время обработки:\n  "
                    + "\n  ".join(appeared)
                    + "\nПовторите запуск с --force для осознанной перезаписи."
                )
        os.replace(report_temp, report_path)
        report_temp = None
        os.replace(cleaned_temp, cleaned_path)
        cleaned_temp = None
    except OutputError:
        raise
    except PermissionError as exc:
        raise OutputError(f"Нет прав на запись в каталог результатов {directory}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - ошибка генерации должна быть понятной пользователю
        raise OutputError(f"Не удалось сформировать файлы отчёта: {exc}") from exc
    finally:
        for temp in (report_temp, cleaned_temp):
            if temp is not None:
                with contextlib.suppress(OSError):
                    temp.unlink(missing_ok=True)

    return OutputPaths(report=report_path, cleaned_csv=cleaned_path)
