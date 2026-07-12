"""Central logging: console + a tailable file at DATA_DIR/engine.log.

Use get_logger("stage") in pipeline modules. Configured once, idempotently, and
safe across the background worker threads.
"""

from __future__ import annotations

import logging
import sys

from .config import settings

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    settings.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger("engine")
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = logging.FileHandler(settings.data_dir / "engine.log")
    file.setFormatter(fmt)
    root.handlers = [console, file]
    root.propagate = False
    # Silence uvicorn's per-request access log (the GET /books ... 200 spam from
    # the UI's polling). Real errors still log; our engine.* logs are unaffected.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"engine.{name}")
