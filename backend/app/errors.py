"""Public errors deliberately contain no upstream text or submitted values."""

from dataclasses import dataclass


@dataclass
class AppError(Exception):
    category: str
    message: str
    status_code: int = 400
    retryable: bool = False

