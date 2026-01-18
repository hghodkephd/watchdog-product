#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Central logging configuration for Watchdog.

This module provides a single place to configure Python logging for both
development (console) and production (rotating file logs).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Optional

from storage import DATA_DIR


_CONFIG_LOCK = Lock()
_CONFIGURED = False


def _configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging exactly once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    with _CONFIG_LOCK:
        if _CONFIGURED:
            return

        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "watchdog.log"

        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

        root = logging.getLogger()
        root.setLevel(level)

        # Prevent duplicate handlers if something configured logging before us.
        # We only add handlers if there isn't already a RotatingFileHandler
        # pointing at our watchdog log.
        existing_watchdog_file_handler = False
        for h in root.handlers:
            if isinstance(h, RotatingFileHandler):
                try:
                    if Path(getattr(h, "baseFilename", "")) == log_path:
                        existing_watchdog_file_handler = True
                except Exception:
                    continue

        if not existing_watchdog_file_handler:
            file_handler = RotatingFileHandler(
                filename=str(log_path),
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

        # Console handler for development runs.
        if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
            console = logging.StreamHandler()
            console.setLevel(level)
            console.setFormatter(formatter)
            root.addHandler(console)

        _CONFIGURED = True


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """Return a configured logger.

    Args:
        name: Logger name.
        level: Optional level override for this logger.
    """
    _configure_logging()
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger
