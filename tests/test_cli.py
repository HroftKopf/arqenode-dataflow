from __future__ import annotations

import hashlib

import pytest

from arqenode_dataflow.cli import main

from .conftest import DEFAULT_HEADERS, VALID_ROWS


def test_cli_success(csv_factory, tmp_path, capsys) -> None:
    path = csv_factory(VALID_ROWS)
    output = tmp_path / "result"
    code = main(["--input", str(path), "--output", str(output)])

    captured = capsys.readouterr()
    assert code == 0
    assert "Arqenode DataFlow" in captured.out
    assert "Файл обработан успешно" in captured.out
    assert "Прочитано строк: 3" in captured.out
    assert "Корректных: 3" in captured.out
    assert "Ошибок: 0" in captured.out
    assert "Дубликатов: 0" in captured.out
    assert "report.xlsx" in captured.out
    assert "cleaned.csv" in captured.out
    assert (output / "report.xlsx").exists()
    assert (output / "cleaned.csv").exists()


def test_cli_with_errors_and_duplicates(csv_factory, tmp_path, capsys) -> None:
    rows = (
        VALID_ROWS[0],
        ("ORD-9", "broken", "Bad", "Tools", 1, "1.00"),
        VALID_ROWS[0],
        ("", "", "", "", "", ""),
    )
    path = csv_factory(rows)
    code = main(["--input", str(path), "--output", str(tmp_path / "result")])

    captured = capsys.readouterr()
    assert code == 0
    assert "Корректных: 1" in captured.out
    assert "Ошибок: 1" in captured.out
    assert "Дубликатов: 1" in captured.out
    assert "Пустых строк: 1" in captured.out


def test_cli_missing_file(tmp_path, capsys) -> None:
    code = main(["--input", str(tmp_path / "missing.csv"), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1
    assert "Ошибка:" in captured.err
    assert "не найден" in captured.err
    assert "Traceback" not in captured.err


def test_cli_missing_required_columns(csv_factory, tmp_path, capsys) -> None:
    path = csv_factory(VALID_ROWS[:1], headers=("order_id", "date", "product"))
    code = main(["--input", str(path), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1
    assert "отсутствуют обязательные столбцы" in captured.err


def test_cli_unsupported_extension(tmp_path, capsys) -> None:
    path = tmp_path / "input.txt"
    path.write_text("data", encoding="utf-8")
    code = main(["--input", str(path), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1
    assert "Неподдерживаемый формат" in captured.err


def test_cli_empty_file(tmp_path, capsys) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    code = main(["--input", str(path), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1
    assert "Файл пуст" in captured.err


def test_cli_no_valid_records(csv_factory, tmp_path, capsys) -> None:
    rows = (("ORD-1", "bad", "Widget", "Tools", 1, "10.00"),)
    path = csv_factory(rows)
    code = main(["--input", str(path), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1
    assert "Не найдено ни одной корректной записи" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "out" / "report.xlsx").exists()


def test_cli_overwrite_policy(csv_factory, tmp_path, capsys) -> None:
    path = csv_factory(VALID_ROWS)
    output = tmp_path / "out"
    assert main(["--input", str(path), "--output", str(output)]) == 0
    capsys.readouterr()

    assert main(["--input", str(path), "--output", str(output)]) == 1
    captured = capsys.readouterr()
    assert "--force" in captured.err

    assert main(["--input", str(path), "--output", str(output), "--force"]) == 0
    captured = capsys.readouterr()
    assert "Файл обработан успешно" in captured.out


def test_cli_help(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "Обработка таблицы продаж" in captured.out
    assert "--input" in captured.out


def test_cli_no_arguments() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_cli_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "1.0.0" in capsys.readouterr().out


def test_cli_debug_shows_traceback(tmp_path, capsys) -> None:
    code = main(
        [
            "--input",
            str(tmp_path / "missing.csv"),
            "--output",
            str(tmp_path / "out"),
            "--debug",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "Traceback" in captured.err


def test_cli_unexpected_error_is_hidden_without_debug(
    csv_factory,
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    path = csv_factory(VALID_ROWS)

    def crash(*args, **kwargs):
        raise RuntimeError("internal secret")

    monkeypatch.setattr("arqenode_dataflow.cli.run_job", crash)
    code = main(["--input", str(path), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1
    assert "Внутренняя ошибка" in captured.err
    assert "Traceback" not in captured.err
    assert "RuntimeError" in captured.err


def test_cli_unexpected_error_with_debug(
    csv_factory,
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    path = csv_factory(VALID_ROWS)

    def crash(*args, **kwargs):
        raise RuntimeError("internal secret")

    monkeypatch.setattr("arqenode_dataflow.cli.run_job", crash)
    code = main(["--input", str(path), "--output", str(tmp_path / "out"), "--debug"])
    captured = capsys.readouterr()
    assert code == 1
    assert "Traceback" in captured.err
    assert "RuntimeError" in captured.err


def test_cli_does_not_modify_source(csv_factory, tmp_path) -> None:
    path = csv_factory(VALID_ROWS)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert main(["--input", str(path), "--output", str(tmp_path / "out")]) == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_cli_xlsx_input(xlsx_factory, tmp_path, capsys) -> None:
    path = xlsx_factory(VALID_ROWS)
    code = main(["--input", str(path), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 0
    assert "Файл обработан успешно" in captured.out


def test_cli_output_conflict(csv_factory, tmp_path, capsys) -> None:
    path = csv_factory(VALID_ROWS)
    output = tmp_path / "out"
    output.write_text("file blocks directory", encoding="utf-8")
    code = main(["--input", str(path), "--output", str(output)])
    captured = capsys.readouterr()
    assert code == 1
    assert "не является каталогом" in captured.err


def test_default_headers_fixture_is_complete() -> None:
    assert {"order_id", "date", "product", "quantity", "unit_price"} <= set(DEFAULT_HEADERS)
