from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol

from app.ingestion.types import IngestionResult


class SupportsMime(Protocol):
    mime_type: str | None


class BaseIngestionProcessor(ABC):
    """Abstract base class for ingestion processors handling different file types."""

    name: str = "base"
    supported_suffixes: tuple[str, ...] = ()

    def can_process(self, file_path: Path, mime_type: str | None = None) -> bool:
        suffix_ok = not self.supported_suffixes or file_path.suffix.lower() in self.supported_suffixes
        return suffix_ok

    @abstractmethod
    def process(self, file_path: Path, *, context: dict[str, Any] | None = None) -> IngestionResult:
        """Return structured offers parsed from the given file."""


class IngestionRegistry:
    """Runtime registry for available processors."""

    def __init__(self) -> None:
        self._processors: dict[str, BaseIngestionProcessor] = {}

    def register(self, processor: BaseIngestionProcessor) -> None:
        self._processors[processor.name] = processor

    def get(self, name: str) -> BaseIngestionProcessor:
        return self._processors[name]

    def match(self, file_path: Path, mime_type: str | None = None) -> BaseIngestionProcessor | None:
        for processor in self._processors.values():
            if processor.can_process(file_path, mime_type):
                return processor
        return None

    @property
    def processors(self) -> dict[str, BaseIngestionProcessor]:
        return self._processors.copy()


registry = IngestionRegistry()
