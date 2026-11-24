"""Чтение CSV и XLSX без изменения исходного файла."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .errors import InputError
from .models import REQUIRED_COLUMNS, InputTable, SourceRow

SUPPORTED_SUFFIXES = {".csv", ".xlsx"}
CSV_ENCODINGS = ("utf-8-sig", "cp1251")
_CSV_SAMPLE_LIMIT = 64 * 1024


def normalize_header(value: object) -> str:
    """Нормализовать имя столбца: BOM, регистр и лишние пробелы."""

    return " ".join(str(value or "").replace("\ufeff", "").split()).lower()


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _row_has_content(values: Iterable[object]) -> bool:
    return any(not _is_blank(value) for value in values)


def read_input(path: str | Path) -> InputTable:
    """Прочитать CSV/XLSX и вернуть сырые строки.

    Поддерживаются UTF-8, UTF-8 с BOM и Windows-1251 для CSV, а также
    автоматическое определение разделителя ``,`` или ``;``.
    """

    source = Path(path)
    try:
        if not source.exists():
            raise InputError(f"Файл не найден: {source}")
        if not source.is_file():
            raise InputError(f"Указанный путь не является файлом: {source}")
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
            raise InputError(
                f"Неподдерживаемый формат файла «{source.suffix or 'без расширения'}». "
                f"Поддерживаются: {supported}."
            )
        if source.stat().st_size == 0:
            raise InputError(f"Файл пуст: {source}")
    except OSError as exc:
        raise InputError(f"Не удалось получить доступ к файлу {source}: {exc}") from exc

    if suffix == ".csv":
        return _read_csv(source)
    return _read_xlsx(source)


def _decode_csv(data: bytes) -> str:
    if b"\x00" in data:
        raise InputError("CSV-файл содержит бинарные данные (NUL-байты) и не может быть прочитан.")

    errors: list[str] = []
    for encoding in CSV_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc.reason}")
    details = "; ".join(errors)
    raise InputError(
        "Не удалось определить кодировку CSV. Поддерживаются UTF-8, UTF-8 с BOM "
        f"и Windows-1251. Детали: {details}"
    )


def _detect_delimiter(text: str) -> str:
    """Выбрать ``;`` или ``,`` по заголовку и устойчивости ширины строк."""

    sample = text[:_CSV_SAMPLE_LIMIT]
    best: tuple[tuple[int, float, int], str] | None = None
    for delimiter in (",", ";"):
        try:
            parsed = list(csv.reader(io.StringIO(sample, newline=""), delimiter=delimiter))
        except csv.Error:
            continue
        non_blank = [row for row in parsed if _row_has_content(row)]
        if not non_blank:
            continue

        header = {normalize_header(cell) for cell in non_blank[0]}
        required_found = len(header.intersection(REQUIRED_COLUMNS))
        width = len(non_blank[0])
        same_width = sum(1 for row in non_blank[1:] if len(row) == width)
        consistency = same_width / max(1, len(non_blank) - 1)
        score = (required_found, consistency, min(width, 100))
        if best is None or score > best[0]:
            best = (score, delimiter)

    return best[1] if best is not None else ","


def _rows_from_matrix(
    matrix: list[tuple[int, list[object]]],
    *,
    source: Path,
) -> InputTable:
    """Преобразовать пронумерованные строки в :class:`InputTable`."""

    header_position = next(
        (index for index, (_, row) in enumerate(matrix) if _row_has_content(row)),
        None,
    )
    if header_position is None:
        raise InputError(f"Файл не содержит данных: {source}")

    _, raw_header = matrix[header_position]
    headers = tuple(normalize_header(value) for value in raw_header)
    while headers and not headers[-1]:
        headers = headers[:-1]
    if not headers:
        raise InputError(f"Не удалось прочитать заголовок таблицы: {source}")
    seen: set[str] = set()
    duplicates: set[str] = set()
    for header in headers:
        if not header:
            continue
        if header in seen:
            duplicates.add(header)
        seen.add(header)
    if duplicates:
        names = ", ".join(sorted(duplicates))
        raise InputError(f"В заголовке повторяются столбцы: {names}.")

    width = len(headers)
    unnamed_columns = {index for index, header in enumerate(headers) if not header}
    rows: list[SourceRow] = []
    for row_number, raw_values in matrix[header_position + 1 :]:
        extra = raw_values[width:]
        issues: list[str] = []
        if _row_has_content(extra):
            issues.append("в строке больше значений, чем в заголовке")
        if any(
            index < len(raw_values) and not _is_blank(raw_values[index])
            for index in unnamed_columns
        ):
            issues.append("значение в столбце без заголовка")

        padded = list(raw_values[:width])
        padded.extend([""] * (width - len(padded)))
        rows.append(SourceRow(row_number=row_number, values=tuple(padded), issues=tuple(issues)))

    if not rows:
        raise InputError(f"Файл содержит только заголовок и не содержит строк данных: {source}")
    return InputTable(headers=headers, rows=tuple(rows))


def _read_csv(path: Path) -> InputTable:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError(f"Не удалось прочитать CSV-файл {path}: {exc}") from exc

    text = _decode_csv(data)
    if not text.strip():
        raise InputError(f"Файл пуст: {path}")

    delimiter = _detect_delimiter(text)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        matrix: list[tuple[int, list[object]]] = []
        while True:
            start_line = reader.line_num + 1
            try:
                row = next(reader)
            except StopIteration:
                break
            matrix.append((start_line, list(row)))
    except csv.Error as exc:
        raise InputError(f"Некорректный CSV-файл {path}: {exc}") from exc

    return _rows_from_matrix(matrix, source=path)


def _read_xlsx(path: Path) -> InputTable:
    workbook = None
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        worksheet = workbook.active
        if worksheet is None:
            raise InputError(f"Книга Excel не содержит листов: {path}")
        matrix = [
            (row_number, list(values))
            for row_number, values in enumerate(
                worksheet.iter_rows(values_only=True),
                start=1,
            )
        ]
    except InputError:
        raise
    except (
        InvalidFileException,
        zipfile.BadZipFile,
        ParseError,
        KeyError,
        ValueError,
        TypeError,
        EOFError,
        OSError,
    ) as exc:
        raise InputError(
            f"Не удалось прочитать Excel-файл {path}. "
            "Файл повреждён или не является корректной книгой .xlsx. "
            f"Детали: {exc}"
        ) from exc
    finally:
        if workbook is not None:
            workbook.close()

    return _rows_from_matrix(matrix, source=path)
