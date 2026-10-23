"""Stable domain errors independent of HTTP and persistence frameworks."""


class DomainValidationError(ValueError):
    """Describe invalid domain input with a machine-readable code and field."""

    def __init__(self, code: str, field: str, message: str) -> None:
        """Keep a useful human explanation alongside the code and input field."""
        self.code = code
        self.field = field
        super().__init__(message)
