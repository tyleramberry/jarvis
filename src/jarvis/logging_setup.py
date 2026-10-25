"""Log to stderr and a rotating file."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jarvis.config import AppConfig


def default_log_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "jarvis" / "jarvis.log"
    return Path.home() / ".local" / "state" / "jarvis" / "jarvis.log"


def setup_logging(config: AppConfig) -> Path:
    level_name = config.logging.level
    level = getattr(logging, level_name, logging.INFO)
    log_path = Path(config.logging.file).expanduser() if config.logging.file else default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(stream)
    logging.getLogger("websockets").setLevel(max(level, logging.INFO))
    return log_path
