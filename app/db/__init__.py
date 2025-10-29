"""Database models and utilities."""

from __future__ import annotations

from typing import Any, Dict

from pydantic.fields import FieldInfo

from app.db import models


def _patch_fieldinfo_default_factory() -> None:
    """Ensure SQLModel can call default factories under Pydantic >=2.8."""

    if getattr(FieldInfo, "_pricebot_default_factory_patched", False):
        return

    original_get_default = FieldInfo.get_default

    def _patched_get_default(
        self: FieldInfo,
        *,
        call_default_factory: bool = False,
        validated_data: Dict[str, Any] | None = None,
    ) -> Any:
        if call_default_factory and validated_data is None:
            validated_data = {}
        return original_get_default(
            self,
            call_default_factory=call_default_factory,
            validated_data=validated_data,
        )

    FieldInfo.get_default = _patched_get_default  # type: ignore[assignment]
    setattr(FieldInfo, "_pricebot_default_factory_patched", True)


_patch_fieldinfo_default_factory()

__all__ = ["models"]
