"""Shared validation and result helpers for deterministic services."""
from collections.abc import Mapping
from typing import Any

SERVICE_VERSION = "1.0.0"


class InputValidationError(ValueError):
    """Raised when an input has the wrong shape or an invalid value."""


def require_mapping(value: Any, name: str = "input") -> Mapping:
    if not isinstance(value, Mapping):
        raise InputValidationError(f"{name} must be a Mapping")
    return value


def non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"{name} must be a non-empty string")
    return value.strip()


def finite_number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputValidationError(f"{name} must be a number")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise InputValidationError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise InputValidationError(f"{name} must be at least {minimum}")
    return result


def result(data: Mapping, *, source: str, version: str = SERVICE_VERSION,
           status: str = "ok", unknown: list[str] | None = None) -> dict:
    return {
        "status": status,
        "data": dict(data),
        "unknown": list(unknown or []),
        "metadata": {"service_version": version, "source": source},
    }
