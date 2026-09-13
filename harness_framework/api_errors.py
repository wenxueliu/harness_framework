"""Structured errors shared by new Dashboard HTTP APIs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class APIError(RuntimeError):
    code: str
    message: str
    status: int = 400
    details: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class NotFoundError(APIError):
    def __init__(self, message: str, *, code: str = "NOT_FOUND"):
        super().__init__(code, message, 404)


class ConflictError(APIError):
    def __init__(self, message: str, *, code: str = "REVISION_CONFLICT",
                 details: Mapping[str, Any] | None = None):
        super().__init__(code, message, 409, details or {})


class ValidationError(APIError):
    def __init__(self, message: str, *, code: str = "VALIDATION_FAILED",
                 details: Mapping[str, Any] | None = None):
        super().__init__(code, message, 422, details or {})
