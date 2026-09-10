"""Application error hierarchy.

Every error carries an HTTP status and a stable machine-readable code, and is
rendered to the client as RFC 7807 ``application/problem+json``. Clients switch
on ``code``; humans read ``detail``.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every deliberate failure in the application."""

    status_code: int = 500
    code: str = "internal_error"
    detail: str = "An unexpected error occurred."

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        **extra: Any,
    ) -> None:
        self.detail = detail or self.detail
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.extra = extra
        super().__init__(self.detail)

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        """Render as an RFC 7807 problem document."""
        problem: dict[str, Any] = {
            "type": f"https://workbench.local/errors/{self.code}",
            "title": self.__class__.__name__,
            "status": self.status_code,
            "detail": self.detail,
            "code": self.code,
        }
        if instance:
            problem["instance"] = instance
        problem.update(self.extra)
        return problem


# --------------------------------------------------------------- client errors
class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    detail = "The request payload is invalid."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    detail = "The requested resource does not exist."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    detail = "The request conflicts with the current state."


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"
    detail = "The uploaded file exceeds the configured limit."


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"
    detail = "This file type cannot be ingested."


# ------------------------------------------------------------ security errors
class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"
    detail = "Authentication is required."


class AuthorizationError(AppError):
    """Raised when a principal lacks a permission.

    The message deliberately does not reveal whether the resource exists — for a
    classified document, the difference between "forbidden" and "not found" is
    itself information.
    """

    status_code = 403
    code = "forbidden"
    detail = "You do not have permission to perform this action."


class AccountLockedError(AppError):
    status_code = 423
    code = "account_locked"
    detail = "This account is temporarily locked after repeated failed logins."


# ------------------------------------------------------------- domain errors
class ProviderError(AppError):
    """A model backend failed. Usually retryable against a fallback model."""

    status_code = 502
    code = "provider_error"
    detail = "The model provider failed to respond."


class ModelUnavailableError(AppError):
    status_code = 503
    code = "model_unavailable"
    detail = "No model satisfying this request is currently available."


class IngestionError(AppError):
    status_code = 422
    code = "ingestion_failed"
    detail = "The document could not be processed."


class RetrievalError(AppError):
    status_code = 500
    code = "retrieval_failed"
    detail = "The knowledge search failed."


class ToolError(AppError):
    status_code = 500
    code = "tool_failed"
    detail = "A tool invocation failed."


class ToolNotFoundError(AppError):
    status_code = 404
    code = "tool_not_found"
    detail = "No such tool is registered."


class SandboxError(AppError):
    status_code = 500
    code = "sandbox_error"
    detail = "Sandboxed execution failed."


class SandboxPolicyError(AppError):
    """The submitted code was rejected before it ever reached a container."""

    status_code = 400
    code = "sandbox_policy_violation"
    detail = "The generated code violates the execution policy."


class ApprovalRequiredError(AppError):
    status_code = 202
    code = "approval_required"
    detail = "This action is waiting for human approval."


class BudgetExceededError(AppError):
    status_code = 429
    code = "budget_exceeded"
    detail = "The run exhausted its step, token or time budget."


class ConfigurationError(AppError):
    """A misconfiguration that should stop the process at startup."""

    status_code = 500
    code = "configuration_error"
    detail = "The service is misconfigured."


class AuditIntegrityError(AppError):
    """The audit hash chain does not verify — treat as a security incident."""

    status_code = 500
    code = "audit_integrity_failure"
    detail = "The audit log failed integrity verification."
