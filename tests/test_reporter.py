from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest
from openpyxl import load_workbook

from arqenode_dataflow import reporter
from arqenode_dataflow.errors import OutputError
from arqenode_dataflow.processor import build_summary
from arqenode_dataflow.reader import read_input
from arqenode_dataflow.reporter import (
    CSV_HEADERS,
    DATA_HEADERS,
    ERROR_HEADERS,
    MONTH_HEADERS,
    PRODUCT_HEADERS,
    protect_csv_text,
    write_outputs,
)
from arqenode_dataflow.validator import validate_table


def _generate(input_path: Path, output_dir: Path, *, force: bool = False):
    validation = validate_table(read_input(input_path))
    summary = build_summary(validation, input_path.name)
    outputs = write_outputs(
        validation,
        summary,
        output_dir,
        input_path=input_path,
        force=force,
    )
    return validation, summary, outputs


def _summary_value(worksheet, label: str):
    for row in worksheet.iter_rows(min_col=2, max_col=3, values_only=True):
        if row[0] == label:
            return row[1]
    raise AssertionError(f"Показатель не найден: {label}")


@pytest.fixture
def report_input(csv_factory):
    rows = (
        ("ORD-1", "2024-01-05", "Widget Pro", "Tools", 2, "10.00"),
        ("ORD-1", "2024-01-05", "Gadget Mini", "Tools", 1, "5.50"),
        ("ORD-2", "2024-02-10", "Widget Pro", "Tools", 3, "10.00"),
        ("ORD-3", "bad-date", "Broken", "Tools", 1, "1.00"),
        ("ORD-2", "2024-02-10", "Widget Pro", "Tools", 3, "10.00"),
    )
    return csv_factory(rows, name="sales.csv")


def test_report_structure_and_formatting(report_input, tmp_path) -> None:
    validation, summary, outputs = _generate(report_input, tmp_path / "out")
    assert outputs.report.exists()
    assert outputs.cleaned_csv.exists()

    workbook = load_workbook(outputs.report)
    try:
        assert workbook.sheetnames == ["Данные", "Ошибки", "Сводка", "Товары", "Месяцы"]
        assert workbook.active.title == "Сводка"
        assert workbook.properties.creator == "Arqenode DataFlow"

        data = workbook["Данные"]
        assert tuple(cell.value for cell in data[1]) == DATA_HEADERS
        assert data.max_row == validation.valid_rows + 1
        assert data.freeze_panes == "A2"
        assert data.auto_filter.ref == f"A1:H{data.max_row}"
        assert data["C2"].number_format == "DD.MM.YYYY"
        assert data["G2"].number_format == "#,##0.00"
        assert data["H2"].number_format == "#,##0.00"
        assert data.column_dimensions["D"].width == 38
        assert data.sheet_view.showGridLines is False

        errors = workbook["Ошибки"]
        assert tuple(cell.value for cell in errors[1]) == ERROR_HEADERS
        assert errors.freeze_panes == "A2"
        assert errors.auto_filter.ref.startswith("A1:H")
        assert errors.max_row == 3

        products = workbook["Товары"]
        assert tuple(cell.value for cell in products[1]) == PRODUCT_HEADERS
        assert products.freeze_panes == "A2"

        months = workbook["Месяцы"]
        assert tuple(cell.value for cell in months[1]) == MONTH_HEADERS
        assert len(months._charts) == 1
        assert months._charts[0].anchor._from.col == 0
        assert months._charts[0].anchor._from.row == 14
    finally:
        workbook.close()


def test_summary_values(report_input, tmp_path) -> None:
    validation, summary, outputs = _generate(report_input, tmp_path / "out")
    workbook = load_workbook(outputs.report)
    try:
        sheet = workbook["Сводка"]
        assert _summary_value(sheet, "Прочитано строк") == 5
        assert _summary_value(sheet, "Корректных строк") == 3
        assert _summary_value(sheet, "Ошибок") == 1
        assert _summary_value(sheet, "Дубликатов") == 1
        assert _summary_value(sheet, "Уникальных заказов") == 2
        assert _summary_value(sheet, "Продано единиц товаров") == 6
        assert _summary_value(sheet, "Общая выручка, ₽") == 55.5
        top_header_row = next(
            row[0].row for row in sheet.iter_rows(min_col=1, max_col=1) if row[0].value == "№"
        )
        top_rows = [
            sheet.cell(row=top_header_row + offset, column=2).value
            for offset in range(1, len(summary.top_products) + 1)
        ]
        assert top_rows == [product.product for product in summary.top_products]
    finally:
        workbook.close()


def test_errors_sheet_contains_raw_values_and_reasons(report_input, tmp_path) -> None:
    _, _, outputs = _generate(report_input, tmp_path / "out")
    workbook = load_workbook(outputs.report)
    try:
        sheet = workbook["Ошибки"]
        assert sheet["A2"].value == 5
        assert sheet["B2"].value == "ORD-3"
        assert sheet["C2"].value == "bad-date"
        assert "не распознана дата" in sheet["H2"].value
        assert sheet["A3"].value == 6
        assert "точный дубликат" in sheet["H3"].value
        assert sheet["B3"].value == "ORD-2"
    finally:
        workbook.close()


def test_cleaned_csv_contents(report_input, tmp_path) -> None:
    _, _, outputs = _generate(report_input, tmp_path / "out")
    raw = outputs.cleaned_csv.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    with outputs.cleaned_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == CSV_HEADERS
    assert len(rows) == 4
    assert rows[1] == ["ORD-1", "2024-01-05", "Widget Pro", "Tools", "2", "10.00", "20.00"]
    assert rows[3][6] == "30.00"


def test_empty_errors_sheet_when_all_rows_valid(csv_factory, tmp_path) -> None:
    path = csv_factory(
        (("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00"),),
        name="valid.csv",
    )
    _, _, outputs = _generate(path, tmp_path / "out")
    workbook = load_workbook(outputs.report)
    try:
        errors = workbook["Ошибки"]
        assert errors.max_row == 1
        assert tuple(cell.value for cell in errors[1]) == ERROR_HEADERS
    finally:
        workbook.close()


def test_formula_injection_is_neutralized(csv_factory, tmp_path) -> None:
    rows = (
        (
            "=1+1",
            "2024-01-01",
            "+SUM(A1:A2)",
            "@cmd",
            1,
            "10.00",
        ),
        (
            '=HYPERLINK("https://example.invalid")',
            "2024-01-02",
            "",
            "X",
            1,
            "1.00",
        ),
    )
    path = csv_factory(rows, name="formulas.csv")
    _, _, outputs = _generate(path, tmp_path / "out")

    workbook = load_workbook(outputs.report)
    try:
        data = workbook["Данные"]
        assert data["B2"].value == "=1+1"
        assert data["B2"].data_type == "s"
        assert data["D2"].value == "+SUM(A1:A2)"
        assert data["D2"].data_type == "s"
        assert data["E2"].value == "@cmd"
        assert data["E2"].data_type == "s"

        errors = workbook["Ошибки"]
        assert errors["B2"].value == '=HYPERLINK("https://example.invalid")'
        assert errors["B2"].data_type == "s"

        for worksheet in workbook.worksheets:
            for row in worksheet.iter_rows():
                for cell in row:
                    assert cell.data_type != "f"
    finally:
        workbook.close()

    with outputs.cleaned_csv.open(encoding="utf-8-sig", newline="") as handle:
        cleaned = list(csv.reader(handle))
    assert cleaned[1][0] == "'=1+1"
    assert cleaned[1][2] == "'+SUM(A1:A2)"
    assert cleaned[1][3] == "'@cmd"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+SUM(A1)", "'+SUM(A1)"),
        ("-2", "'-2"),
        ("@cmd", "'@cmd"),
        ("\t=1+1", "'\t=1+1"),
        ("\tabc", "'\tabc"),
        ("\n=1+1", "'\n=1+1"),
        (" normal", " normal"),
        ("", ""),
    ],
)
def test_protect_csv_text(value: str, expected: str) -> None:
    assert protect_csv_text(value) == expected


def test_refuses_overwrite_without_force(report_input, tmp_path) -> None:
    output_dir = tmp_path / "out"
    _generate(report_input, output_dir)
    with pytest.raises(OutputError, match="--force"):
        _generate(report_input, output_dir)


def test_force_overwrites(report_input, tmp_path) -> None:
    output_dir = tmp_path / "out"
    first = _generate(report_input, output_dir)[2]
    first_csv = first.cleaned_csv.read_bytes()
    second = _generate(report_input, output_dir, force=True)[2]
    assert second.report.exists()
    assert second.cleaned_csv.read_bytes() == first_csv

    workbook = load_workbook(second.report)
    try:
        assert workbook.sheetnames == ["Данные", "Ошибки", "Сводка", "Товары", "Месяцы"]
        assert workbook["Данные"].max_row == 4
    finally:
        workbook.close()


def test_input_file_is_not_modified(report_input, tmp_path) -> None:
    before = hashlib.sha256(report_input.read_bytes()).hexdigest()
    _generate(report_input, tmp_path / "out")
    after = hashlib.sha256(report_input.read_bytes()).hexdigest()
    assert before == after


def test_refuses_to_overwrite_source_named_report(csv_factory, tmp_path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    source = csv_factory(
        (("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00"),),
        name="sales.csv",
    )
    target = output_dir / "report.xlsx"
    target.write_bytes(b"placeholder")
    validation = validate_table(read_input(source))
    summary = build_summary(validation, source.name)
    with pytest.raises(OutputError, match="перезаписать исходный файл"):
        write_outputs(validation, summary, output_dir, input_path=target, force=True)
    assert target.read_bytes() == b"placeholder"


def test_output_path_is_a_file(report_input, tmp_path) -> None:
    output_file = tmp_path / "out"
    output_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OutputError, match="не является каталогом"):
        _generate(report_input, output_file)


def test_report_target_is_a_directory(report_input, tmp_path) -> None:
    output_dir = tmp_path / "out"
    (output_dir / "report.xlsx").mkdir(parents=True)
    with pytest.raises(OutputError, match="найден каталог"):
        _generate(report_input, output_dir)


def test_generation_failure_cleans_temp_files(report_input, tmp_path, monkeypatch) -> None:
    validation = validate_table(read_input(report_input))
    summary = build_summary(validation, report_input.name)
    output_dir = tmp_path / "out"

    def fail(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(reporter, "_build_workbook", fail)
    with pytest.raises(OutputError, match="Не удалось сформировать"):
        write_outputs(validation, summary, output_dir, input_path=report_input)
    assert list(output_dir.iterdir()) == []


def test_temp_creation_failure_is_wrapped(report_input, tmp_path, monkeypatch) -> None:
    validation = validate_table(read_input(report_input))
    summary = build_summary(validation, report_input.name)

    def fail(*args, **kwargs):
        raise OSError("no temp")

    monkeypatch.setattr(reporter.tempfile, "mkstemp", fail)
    with pytest.raises(OutputError, match="Не удалось сформировать"):
        write_outputs(validation, summary, tmp_path / "out", input_path=report_input)


def test_replace_failure_is_wrapped_and_cleaned(report_input, tmp_path, monkeypatch) -> None:
    validation = validate_table(read_input(report_input))
    summary = build_summary(validation, report_input.name)
    output_dir = tmp_path / "out"

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(reporter.os, "replace", fail)
    with pytest.raises(OutputError, match="Не удалось сформировать"):
        write_outputs(validation, summary, output_dir, input_path=report_input)
    assert list(output_dir.iterdir()) == []


def test_permission_error_has_friendly_message(report_input, tmp_path, monkeypatch) -> None:
    validation = validate_table(read_input(report_input))
    summary = build_summary(validation, report_input.name)
    output_dir = tmp_path / "out"

    def fail(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(reporter.os, "replace", fail)
    with pytest.raises(OutputError, match="Нет прав на запись"):
        write_outputs(validation, summary, output_dir, input_path=report_input)
    assert list(output_dir.iterdir()) == []


def test_report_number_formats_and_products_order(report_input, tmp_path) -> None:
    _, summary, outputs = _generate(report_input, tmp_path / "out")
    workbook = load_workbook(outputs.report)
    try:
        products = workbook["Товары"]
        assert products["A2"].value == summary.products[0].product
        assert products["B2"].number_format == "#,##0.00"
        assert products["C2"].number_format == "#,##0"
        months = workbook["Месяцы"]
        assert months["A2"].value == "2024-01"
        assert months["B2"].number_format == "#,##0.00"
    finally:
        workbook.close()
