from __future__ import annotations


class AuditError(Exception):
    """Base exception for expected application errors."""


class InputError(AuditError):
    """The user supplied an invalid or unusable input."""


class AnalysisError(AuditError):
    """Analysis could not complete after the input was accepted."""


class ArtifactError(AuditError):
    """An output artifact could not be validated or persisted safely."""
