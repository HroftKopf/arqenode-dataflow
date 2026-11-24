from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from arqenode_dataflow.processor import build_summary
from arqenode_dataflow.reader import read_input
from arqenode_dataflow.validator import validate_table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT_ROOT / "examples"


def _load_generator():
    path = EXAMPLES / "generate_demo.py"
    spec = importlib.util.spec_from_file_location("arqenode_generate_demo", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sales_csv_summary() -> None:
    validation = validate_table(read_input(EXAMPLES / "sales.csv"))
    summary = build_summary(validation, "sales.csv")

    assert summary.read_rows == 16
    assert summary.valid_rows == 16
    assert summary.error_rows == 0
    assert summary.duplicate_rows == 0
    assert summary.unique_orders == 9
    assert summary.total_units == 25
    assert summary.total_revenue == Decimal("506250.00")
    assert len(summary.months) == 6
    assert summary.products[0].product == "Ноутбук Arqenode Pro 14"


def test_sales_xlsx_matches_csv() -> None:
    csv_validation = validate_table(read_input(EXAMPLES / "sales.csv"))
    xlsx_validation = validate_table(read_input(EXAMPLES / "sales.xlsx"))

    assert xlsx_validation.records == csv_validation.records
    assert xlsx_validation.read_rows == csv_validation.read_rows
    assert xlsx_validation.rejected == csv_validation.rejected


def test_sales_with_errors_summary() -> None:
    validation = validate_table(read_input(EXAMPLES / "sales_with_errors.csv"))
    summary = build_summary(validation, "sales_with_errors.csv")

    assert summary.read_rows == 18
    assert summary.blank_rows == 1
    assert summary.valid_rows == 6
    assert summary.error_rows == 9
    assert summary.duplicate_rows == 2
    assert summary.rejected_rows == 11
    assert summary.total_revenue == Decimal("300120.00")


def test_generator_is_deterministic(tmp_path) -> None:
    generator = _load_generator()
    first_rows, first_errors, first_duplicates = generator.generate_rows(500, seed=42)
    second_rows, second_errors, second_duplicates = generator.generate_rows(500, seed=42)

    assert first_rows == second_rows
    assert first_errors == second_errors
    assert first_duplicates == second_duplicates
    assert len(first_rows) == 500
    assert first_duplicates == 10

    path = tmp_path / "generated.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        import csv

        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(generator.HEADER)
        writer.writerows(first_rows)

    validation = validate_table(read_input(path))
    assert validation.error_rows == first_errors
    assert validation.duplicate_rows == first_duplicates
    assert validation.valid_rows == 500 - first_errors - first_duplicates


def test_generator_rejects_too_few_rows() -> None:
    generator = _load_generator()
    with pytest.raises(ValueError, match="не меньше 100"):
        generator.generate_rows(50)


def test_demo_file_exists_and_has_expected_size() -> None:
    path = EXAMPLES / "sales_demo.csv"
    assert path.exists()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        assert sum(1 for _ in handle) == 10_001
