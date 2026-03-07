"""Custom exceptions for the magicfilter library."""


class MagicFilterError(ValueError):
    """Base exception for magicfilter failures."""


class ConfigurationError(MagicFilterError):
    """Raised when the builder or spec is misconfigured."""


class AttributeResolutionError(MagicFilterError):
    """Raised when a filter or sort field cannot be resolved."""


class OperatorError(MagicFilterError):
    """Raised when a filter operator is invalid for a field."""


class SpecificationError(MagicFilterError):
    """Raised when an explicit query specification is invalid."""
