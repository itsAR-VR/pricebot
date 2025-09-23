"""Ingestion subsystem entrypoint."""

from app.ingestion.base import BaseIngestionProcessor, registry

# Register built-in processors by importing their modules
from app.ingestion import document as _document  # noqa: F401
from app.ingestion import spreadsheet as _spreadsheet  # noqa: F401
from app.ingestion import whatsapp as _whatsapp  # noqa: F401

__all__ = ["BaseIngestionProcessor", "registry"]
