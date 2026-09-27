"""Centralised exception hierarchy for the platform."""
from __future__ import annotations


class WebArchiverError(Exception):
    """Base class for all application errors."""


class ConfigError(WebArchiverError):
    """Raised when configuration is missing or invalid."""


class ValidationError(WebArchiverError):
    """Raised when input validation fails."""


class FetchError(WebArchiverError):
    """Raised when a page or asset cannot be fetched."""


class RenderEngineError(WebArchiverError):
    """Raised when a browser render engine fails to initialise or operate."""


class StorageError(WebArchiverError):
    """Raised when writing to disk fails."""
