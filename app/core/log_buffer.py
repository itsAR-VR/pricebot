from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from threading import Lock
from typing import Any


def _truncate(message: str, limit: int = 2000) -> str:
    """Trim long log messages to keep payloads lightweight."""
    if len(message) <= limit:
        return message
    return f"{message[: limit - 1]}…"


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    level: str
    logger: str
    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolCallEntry:
    timestamp: datetime
    method: str
    path: str
    status: int
    duration_ms: float
    conversation_id: str | None = None


class _RingBuffer:
    """Simple ring buffer with thread-safe snapshots."""

    def __init__(self, capacity: int):
        self._capacity = max(1, capacity)
        self._items: deque[Any] = deque(maxlen=self._capacity)
        self._lock = Lock()

    def append(self, item: Any) -> None:
        with self._lock:
            self._items.append(item)

    def snapshot(self, limit: int | None = None) -> list[Any]:
        with self._lock:
            data = list(self._items)
        if limit is None or limit >= len(data):
            return data
        return data[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    @property
    def capacity(self) -> int:
        return self._capacity


class _InMemoryLogBuffer(logging.Handler):
    """Logging handler that stores sanitized log records."""

    def __init__(self, buffer: _RingBuffer):
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc)
            message = _truncate(record.getMessage())
            details: dict[str, Any] | None = None

            if record.exc_info:
                details = {
                    "exception": self.formatException(record.exc_info),
                }

            entry = LogEntry(
                timestamp=timestamp,
                level=record.levelname,
                logger=record.name,
                message=message,
                details=details,
            )
            self._buffer.append(entry)
        except Exception:  # pragma: no cover - defensive logging
            self.handleError(record)


class LogBufferManager:
    """Coordinates in-memory buffering for logs and chat tool events."""

    def __init__(self) -> None:
        self._log_buffer = _RingBuffer(500)
        self._tool_buffer = _RingBuffer(200)
        self._log_handler: _InMemoryLogBuffer | None = None
        self._file_handler: logging.Handler | None = None
        self._installed = False
        self._lock = Lock()

    def install(
        self,
        *,
        max_logs: int = 500,
        max_tool_events: int = 200,
        file_path: Path | None = None,
        level: int = logging.INFO,
    ) -> None:
        with self._lock:
            if self._installed:
                return

            self._log_buffer = _RingBuffer(max_logs)
            self._tool_buffer = _RingBuffer(max_tool_events)

            root_logger = logging.getLogger()
            handler = _InMemoryLogBuffer(self._log_buffer)
            handler.setLevel(logging.NOTSET)
            root_logger.addHandler(handler)
            if root_logger.level == logging.NOTSET or root_logger.level > level:
                root_logger.setLevel(level)
            self._log_handler = handler

            if file_path:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(file_path)
                file_handler.setLevel(level)
                formatter = logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"
                )
                file_handler.setFormatter(formatter)
                logging.getLogger().addHandler(file_handler)
                self._file_handler = file_handler

            self._installed = True

    def add_tool_call(
        self,
        *,
        method: str,
        path: str,
        status: int,
        duration_ms: float,
        conversation_id: str | None = None,
    ) -> None:
        entry = ToolCallEntry(
            timestamp=datetime.now(timezone.utc),
            method=method,
            path=path,
            status=status,
            duration_ms=round(duration_ms, 2),
            conversation_id=conversation_id,
        )
        self._tool_buffer.append(entry)

    def log_entries(self, limit: int | None = None) -> list[LogEntry]:
        return self._log_buffer.snapshot(limit)

    def tool_entries(self, limit: int | None = None) -> list[ToolCallEntry]:
        return self._tool_buffer.snapshot(limit)

    @property
    def max_logs(self) -> int:
        return self._log_buffer.capacity

    @property
    def max_tool_events(self) -> int:
        return self._tool_buffer.capacity

    def clear(self) -> None:
        self._log_buffer.clear()
        self._tool_buffer.clear()


_MANAGER = LogBufferManager()


def install_log_buffer(
    *,
    max_logs: int = 500,
    max_tool_events: int = 200,
    file_path: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Attach the log buffer handler to the root logger."""

    _MANAGER.install(
        max_logs=max_logs,
        max_tool_events=max_tool_events,
        file_path=file_path,
        level=level,
    )


def record_tool_call(
    *,
    method: str,
    path: str,
    status: int,
    duration_ms: float,
    conversation_id: str | None = None,
) -> None:
    """Record metadata about chat tool HTTP calls."""

    _MANAGER.add_tool_call(
        method=method,
        path=path,
        status=status,
        duration_ms=duration_ms,
        conversation_id=conversation_id,
    )


def get_log_entries(limit: int | None = None) -> list[LogEntry]:
    return _MANAGER.log_entries(limit)


def get_tool_entries(limit: int | None = None) -> list[ToolCallEntry]:
    return _MANAGER.tool_entries(limit)


def buffer_limits() -> dict[str, int]:
    return {
        "logs": _MANAGER.max_logs,
        "tool_calls": _MANAGER.max_tool_events,
    }


def reset_buffers() -> None:
    """TEST-ONLY: clear in-memory buffers."""

    _MANAGER.clear()


__all__ = [
    "buffer_limits",
    "get_log_entries",
    "get_tool_entries",
    "install_log_buffer",
    "record_tool_call",
    "reset_buffers",
    "LogEntry",
    "ToolCallEntry",
]
