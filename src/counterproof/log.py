"""Logging setup: Rich on stderr for humans, plus a plain ``run.log`` inside each run dir."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME: Final = "counterproof"
_MARK: Final = "_counterproof_handler"


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def setup_logging(verbosity: int = 0, console: Console | None = None) -> logging.Logger:
    """Configure the package logger. ``verbosity``: 0 warnings, 1 info, 2+ debug."""
    level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    logger = get_logger()
    for handler in list(logger.handlers):
        if getattr(handler, _MARK, False) == "console":
            logger.removeHandler(handler)
    handler = RichHandler(
        console=console or Console(stderr=True),
        show_path=False,
        show_time=False,
        rich_tracebacks=False,
    )
    handler.setLevel(level)
    setattr(handler, _MARK, "console")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def attach_run_log(path: Path) -> logging.Handler:
    """Mirror all package log records at DEBUG level into ``path``."""
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    setattr(handler, _MARK, "run")
    logger = get_logger()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return handler


def detach_run_log(handler: logging.Handler) -> None:
    get_logger().removeHandler(handler)
    handler.close()
