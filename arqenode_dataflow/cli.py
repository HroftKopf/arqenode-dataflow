"""CLI Arqenode DataFlow."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import DataFlowError
from .models import OutputPaths, Summary
from .processor import build_summary
from .reader import read_input
from .reporter import write_outputs
from .validator import validate_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arqenode-dataflow",
        description=(
            "Обработка таблицы продаж CSV/XLSX: проверка данных, расчёты "
            "и формирование Excel-отчёта."
        ),
        epilog=(
            "Коды завершения: 0 — успех; 1 — ошибка чтения, проверки или записи; "
            "2 — ошибка аргументов командной строки."
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        metavar="FILE",
        help="путь к исходному файлу .csv или .xlsx",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output"),
        metavar="DIR",
        help="каталог для report.xlsx и cleaned.csv (по умолчанию: output)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="перезаписать существующие report.xlsx и cleaned.csv",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="показать подробный traceback для диагностики",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def run_job(
    input_path: str | Path, output_dir: str | Path, *, force: bool = False
) -> tuple[Summary, OutputPaths]:
    """Прочитать, проверить, рассчитать и записать результаты."""

    source = Path(input_path)
    table = read_input(source)
    validation = validate_table(table)
    summary = build_summary(validation, source.name)
    outputs = write_outputs(
        validation,
        summary,
        output_dir,
        input_path=source,
        force=force,
    )
    return summary, outputs


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _print_success(summary: Summary, outputs: OutputPaths) -> None:
    lines = [
        "Arqenode DataFlow",
        "",
        "Файл обработан успешно.",
        "",
        f"Прочитано строк: {summary.read_rows}",
        f"Корректных: {summary.valid_rows}",
        f"Ошибок: {summary.error_rows}",
        f"Дубликатов: {summary.duplicate_rows}",
    ]
    if summary.blank_rows:
        lines.append(f"Пустых строк: {summary.blank_rows}")
    lines.extend(
        (
            "",
            f"Отчёт: {_display_path(outputs.report)}",
            f"Данные: {_display_path(outputs.cleaned_csv)}",
        )
    )
    print("\n".join(lines))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary, outputs = run_job(args.input, args.output, force=args.force)
    except DataFlowError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary must not leak tracebacks by default
        print(
            "Внутренняя ошибка Arqenode DataFlow. "
            "Повторите запуск с --debug для подробной диагностики.",
            file=sys.stderr,
        )
        if args.debug:
            traceback.print_exc()
        else:
            print(f"Техническое сообщение: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    _print_success(summary, outputs)
    return 0
