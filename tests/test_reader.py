from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from arqenode_dataflow.errors import InputError
from arqenode_dataflow.reader import normalize_header, read_input

from .conftest import DEFAULT_HEADERS, VALID_ROWS


def test_reads_utf8_csv(csv_factory) -> None:
    path = csv_factory(VALID_ROWS)
    table = read_input(path)

    assert table.headers == DEFAULT_HEADERS
    assert len(table.rows) == 3
    assert table.rows[0].row_number == 2
    assert table.rows[1].row_number == 3
    assert table.rows[0].values[0] == "ORD-1"
    assert table.rows[0].values[4] == "2"


def test_reads_utf8_bom(csv_factory) -> None:
    path = csv_factory(VALID_ROWS, encoding="utf-8-sig")
    table = read_input(path)
    assert table.headers[0] == "order_id"
    assert len(table.rows) == 3


def test_reads_windows_1251_semicolon(csv_factory) -> None:
    rows = (("ORD-42", "2024-03-01", "Клавиатура", "Аксессуары", 2, "1990,50"),)
    path = csv_factory(rows, delimiter=";", encoding="cp1251")
    table = read_input(path)

    assert table.rows[0].values[2] == "Клавиатура"
    assert table.rows[0].values[5] == "1990,50"


def test_quoted_comma_and_delimiter_detection(csv_factory) -> None:
    rows = (("ORD-1", "2024-01-01", "Widget, Pro", "Tools", 1, "10.00"),)
    path = csv_factory(rows, delimiter=";")
    table = read_input(path)
    assert table.rows[0].values[2] == "Widget, Pro"


def test_comma_delimiter_detection(csv_factory) -> None:
    rows = (("ORD-1", "2024-01-01", "Widget Pro", "Tools", 1, "10.00"),)
    path = csv_factory(rows, delimiter=",")
    table = read_input(path)
    assert len(table.headers) == 6
    assert table.rows[0].values[3] == "Tools"


def test_reads_xlsx_with_typed_cells(xlsx_factory) -> None:
    rows = (
        ("ORD-1", date(2024, 1, 2), "Widget", "Tools", 2, Decimal("10.50")),
        ("ORD-1", datetime(2024, 1, 3, 12, 0), "Gadget", None, 1, Decimal("5.00")),
    )
    path = xlsx_factory(rows)
    table = read_input(path)

    assert table.headers == DEFAULT_HEADERS
    assert len(table.rows) == 2
    assert isinstance(table.rows[0].values[1], datetime)
    assert table.rows[0].values[4] == 2
    assert table.rows[1].values[3] is None


def test_xlsx_extra_columns_are_flagged(xlsx_factory) -> None:
    path = xlsx_factory((("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00", "лишнее"),))
    table = read_input(path)
    assert table.rows[0].issues == ("в строке больше значений, чем в заголовке",)


def test_unnamed_middle_column_with_value_is_flagged(csv_factory) -> None:
    headers = ("order_id", "date", "product", "", "quantity", "unit_price", "category")
    rows = (("ORD-1", "2024-01-01", "Widget", "unexpected", 1, "10.00", "Tools"),)
    table = read_input(csv_factory(rows, headers=headers))
    assert table.rows[0].issues == ("значение в столбце без заголовка",)


def test_csv_row_start_line_for_multiline_field(csv_factory) -> None:
    text = (
        "order_id,date,product,category,quantity,unit_price\n"
        'ORD-1,2024-01-01,"Widget\nPro",Tools,1,10.00\n'
        "ORD-2,2024-01-02,Gadget,Tools,1,5.00\n"
    )
    table = read_input(csv_factory((), raw_text=text))
    assert table.rows[0].row_number == 2
    assert table.rows[0].values[2] == "Widget\nPro"
    assert table.rows[1].row_number == 4


def test_csv_trailing_empty_extra_cell_is_not_an_issue(csv_factory) -> None:
    path = csv_factory((("ORD-1", "2024-01-01", "Widget", "Tools", 1, "10.00", ""),))
    table = read_input(path)
    assert table.rows[0].issues == ()


def test_leading_blank_lines_and_blank_data_row(csv_factory) -> None:
    text = "\n\n" + (
        "order_id,date,product,category,quantity,unit_price\n"
        "ORD-1,2024-01-01,Widget,Tools,1,10.00\n"
        "\n"
        "ORD-2,2024-01-02,Gadget,Tools,2,5.00\n"
    )
    table = read_input(csv_factory((), raw_text=text))
    assert len(table.rows) == 3
    assert table.rows[0].row_number == 4
    assert all(value == "" for value in table.rows[1].values)
    assert table.rows[2].values[0] == "ORD-2"


def test_normalize_header() -> None:
    assert normalize_header("  Order_ID \ufeff") == "order_id"
    assert normalize_header(None) == ""
    assert normalize_header(" Unit   Price ") == "unit price"


def test_missing_file(tmp_path) -> None:
    with pytest.raises(InputError, match="не найден"):
        read_input(tmp_path / "missing.csv")


def test_directory_instead_of_file(tmp_path) -> None:
    with pytest.raises(InputError, match="не является файлом"):
        read_input(tmp_path)


@pytest.mark.parametrize("suffix", [".txt", ".xls", ""])
def test_unsupported_extension(tmp_path, suffix: str) -> None:
    path = tmp_path / f"input{suffix}"
    path.write_text("data", encoding="utf-8")
    with pytest.raises(InputError, match="Неподдерживаемый формат"):
        read_input(path)


def test_empty_file(csv_factory) -> None:
    path = csv_factory((), raw_bytes=b"")
    with pytest.raises(InputError, match="Файл пуст"):
        read_input(path)


def test_whitespace_only_csv(csv_factory) -> None:
    path = csv_factory((), raw_text="  \n\n")
    with pytest.raises(InputError, match="Файл пуст"):
        read_input(path)


def test_header_only_csv(csv_factory) -> None:
    path = csv_factory(())
    with pytest.raises(InputError, match="только заголовок"):
        read_input(path)


def test_header_only_xlsx(xlsx_factory) -> None:
    path = xlsx_factory(())
    with pytest.raises(InputError, match="только заголовок"):
        read_input(path)


def test_nul_bytes(csv_factory) -> None:
    path = csv_factory((), raw_bytes=b"order_id,date\x00product\n1,2,3\n")
    with pytest.raises(InputError, match="бинарные данные"):
        read_input(path)


def test_unknown_encoding(csv_factory) -> None:
    path = csv_factory((), raw_bytes=b"order_id;date\n\x98;2024-01-01\n")
    with pytest.raises(InputError, match="кодировку CSV"):
        read_input(path)


def test_malformed_csv(csv_factory) -> None:
    path = csv_factory(
        (),
        raw_text='order_id,date,product,category,quantity,unit_price\n"unclosed,2024-01-01\n',
    )
    with pytest.raises(InputError, match="Некорректный CSV"):
        read_input(path)


def test_duplicate_headers_after_normalization(csv_factory) -> None:
    headers = ("order_id", "date", "product", "quantity", "unit_price", "Order_ID")
    path = csv_factory(VALID_ROWS[:1], headers=headers)
    with pytest.raises(InputError, match="повторяются столбцы"):
        read_input(path)


def test_corrupted_xlsx(tmp_path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"this is definitely not an xlsx archive")
    with pytest.raises(InputError, match="повреждён"):
        read_input(path)


def test_empty_xlsx_file(tmp_path) -> None:
    path = tmp_path / "empty.xlsx"
    path.write_bytes(b"")
    with pytest.raises(InputError, match="Файл пуст"):
        read_input(path)
