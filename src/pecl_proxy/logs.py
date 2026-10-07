"""JSON logging.

Every record is written as one JSON object per line. Records are split logically by the
``type`` field:

* ``access`` -- HTTP access log (logger ``pecl_proxy.access``);
* ``admin``  -- audit of admin API actions (logger ``pecl_proxy.admin``);
* ``app``    -- everything else (service events, uvicorn messages, errors).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACCESS_LOGGER = "pecl_proxy.access"
ADMIN_LOGGER = "pecl_proxy.admin"

access_log = logging.getLogger(ACCESS_LOGGER)
admin_log = logging.getLogger(ADMIN_LOGGER)
app_log = logging.getLogger("pecl_proxy")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "type": _record_type(record),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                entry.setdefault(key, value)
        if record.exc_info:
            entry["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return json.dumps(entry, ensure_ascii=False, default=str)


def _record_type(record: logging.LogRecord) -> str:
    if record.name == ACCESS_LOGGER:
        return "access"
    if record.name == ADMIN_LOGGER:
        return "admin"
    return "app"


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Route all logging (including uvicorn's) through a single JSON handler."""
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # WatchedFileHandler reopens the file after logrotate moves it.
        handler: logging.Handler = logging.handlers.WatchedFileHandler(log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers unless told otherwise; make it propagate to ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # the access log is written by our middleware with richer fields
    logging.getLogger("uvicorn.access").disabled = True
    # keep httpx/httpcore request chatter out of the INFO stream
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger, level: int, msg: str, event: str | None = None, **fields: Any
) -> None:
    if event is not None:
        fields = {"event": event, **fields}
    logger.log(level, msg, extra={"fields": fields})
