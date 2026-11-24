"""Точка входа ``python -m arqenode_dataflow``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover - поведение проверяется через CLI-тесты
    raise SystemExit(main())
