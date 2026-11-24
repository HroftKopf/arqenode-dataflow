#!/usr/bin/env python3
"""Детерминированный генератор демонстрационного набора продаж.

Скрипт не требует зависимостей проекта и создаёт CSV примерно на 10 000 строк.
Часть строк намеренно повреждена, а в конец добавляются точные дубликаты.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

DEFAULT_SEED = 20240101
DEFAULT_ROWS = 10_000
MONEY = Decimal("0.01")
HEADER = ("order_id", "date", "product", "category", "quantity", "unit_price")
PRODUCTS: tuple[tuple[str, str, Decimal], ...] = (
    ("Ноутбук Arqenode Pro 14", "Электроника", Decimal("89990.00")),
    ("Ноутбук Arqenode Air 13", "Электроника", Decimal("74990.00")),
    ("Монитор Arqenode View 27", "Электроника", Decimal("32990.00")),
    ("Монитор Arqenode View 32", "Электроника", Decimal("45990.00")),
    ("Клавиатура Arqenode Type", "Аксессуары", Decimal("4590.00")),
    ("Мышь Arqenode Silent", "Аксессуары", Decimal("2490.00")),
    ("Наушники Arqenode Sound", "Аксессуары", Decimal("7990.00")),
    ("Микрофон Arqenode Voice", "Аксессуары", Decimal("12990.00")),
    ("Веб-камера Arqenode Eye", "Электроника", Decimal("6990.00")),
    ("Кабель USB-C 2 м", "Аксессуары", Decimal("990.00")),
    ("Подставка для ноутбука", "Аксессуары", Decimal("2990.00")),
    ("Сетевой фильтр Arqenode Power", "Аксессуары", Decimal("3490.00")),
)
START_DATE = date(2024, 1, 1)
PERIOD_DAYS = 366


def _mutate_error(row: list[str], kind: int) -> None:
    """Испортить одно поле строки предсказуемым и понятным способом."""

    if kind == 0:
        row[1] = "2024-02-30"
    elif kind == 1:
        row[1] = "01/02/2024"
    elif kind == 2:
        row[4] = "0"
    elif kind == 3:
        row[4] = "-2"
    elif kind == 4:
        row[5] = "abc"
    elif kind == 5:
        row[5] = "-100.00"
    elif kind == 6:
        row[2] = ""
    else:
        row[0] = ""


def generate_rows(rows: int, seed: int = DEFAULT_SEED) -> tuple[list[list[str]], int, int]:
    """Сгенерировать строки, число намеренных ошибок и число дубликатов."""

    if rows < 100:
        raise ValueError("Количество строк должно быть не меньше 100.")
    rng = random.Random(seed)
    duplicate_count = max(10, round(rows * 0.01))
    primary_count = rows - duplicate_count
    error_count = max(10, round(primary_count * 0.015))
    if error_count >= primary_count:
        raise ValueError("Слишком мало строк для выбранного числа ошибок.")

    error_indices = set(rng.sample(range(primary_count), error_count))
    valid_rows: list[list[str]] = []
    order_number = 1

    while len(valid_rows) < primary_count:
        order_id = f"ORD-2024-{order_number:06d}"
        sale_date = (START_DATE + timedelta(days=rng.randrange(PERIOD_DAYS))).isoformat()
        items_in_order = min(
            rng.randint(1, 3),
            primary_count - len(valid_rows),
        )
        order_signatures: set[tuple[str, str, str]] = set()
        for _ in range(items_in_order):
            product, category, base_price = rng.choice(PRODUCTS)
            quantity = rng.randint(1, 6)
            price = base_price
            if rng.random() < 0.35:
                price += Decimal(rng.randint(-1500, 1500)) / 100
            price = max(price, Decimal("99.00")).quantize(MONEY)
            signature = (product, str(quantity), f"{price:.2f}")
            while signature in order_signatures:
                quantity += 1
                signature = (product, str(quantity), f"{price:.2f}")
            order_signatures.add(signature)
            valid_rows.append(
                [order_id, sale_date, product, category, str(quantity), f"{price:.2f}"]
            )
        order_number += 1

    for index in sorted(error_indices):
        _mutate_error(valid_rows[index], index % 8)

    clean_indices = [index for index in range(primary_count) if index not in error_indices]
    duplicate_rows = [list(valid_rows[rng.choice(clean_indices)]) for _ in range(duplicate_count)]
    return valid_rows + duplicate_rows, error_count, duplicate_count


def write_demo_csv(path: Path, rows: int, seed: int) -> tuple[int, int, int]:
    data, error_count, duplicate_count = generate_rows(rows, seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(data)
    return len(data), error_count, duplicate_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Создать детерминированный демонстрационный CSV Arqenode DataFlow."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("sales_demo.csv"),
        help="путь к итоговому CSV (по умолчанию: examples/sales_demo.csv)",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=DEFAULT_ROWS,
        help=f"общее количество строк данных (по умолчанию: {DEFAULT_ROWS})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"фиксированный seed (по умолчанию: {DEFAULT_SEED})",
    )
    args = parser.parse_args()

    try:
        total, errors, duplicates = write_demo_csv(args.output, args.rows, args.seed)
    except (OSError, ValueError) as exc:
        print(f"Ошибка: {exc}")
        return 1

    print("Arqenode DataFlow — генератор демо-данных")
    print()
    print(f"Файл: {args.output}")
    print(f"Seed: {args.seed}")
    print(f"Строк данных: {total}")
    print(f"Намеренных ошибок: {errors}")
    print(f"Точных дубликатов в конце файла: {duplicates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
