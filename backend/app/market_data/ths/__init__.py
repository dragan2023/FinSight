"""Domain-specific Financial-API clients sharing one authenticated transport."""

from .transport import ThsTransport

__all__ = ["ThsTransport"]
